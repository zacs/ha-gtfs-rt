"""Fetch and interpret GTFS-realtime feeds.

Nothing in this module imports Home Assistant. It is the part that decides
which transit systems work, so it is kept small, spec-driven and free of
per-agency special cases.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp
from google.protobuf import json_format
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

# Some servers content-negotiate and return JSON or HTML unless asked for protobuf.
ACCEPT = "application/x-protobuf, application/octet-stream;q=0.9, */*;q=0.8"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Matched by name so older and newer bindings (which add enum values) both work.
_SKIPPED_TRIPS = frozenset({"CANCELED", "DELETED"})
_SKIPPED_STOPS = frozenset({"SKIPPED", "NO_DATA"})

_TripRelationship = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship
_StopRelationship = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship
_OccupancyStatus = gtfs_realtime_pb2.VehiclePosition.OccupancyStatus


class FeedError(Exception):
    """Base class for feed errors."""


class FeedFetchError(FeedError):
    """The feed could not be downloaded."""


class FeedAuthError(FeedError):
    """The server rejected the request's credentials."""


class InvalidFeedError(FeedError):
    """The response is not a GTFS-realtime feed."""


@dataclass(frozen=True, slots=True)
class Departure:
    """A predicted departure of one trip from one stop."""

    route_id: str
    trip_id: str
    time: datetime
    vehicle_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    occupancy: str | None = None


@dataclass(frozen=True, slots=True)
class _Vehicle:
    vehicle_id: str | None
    route_id: str
    latitude: float | None
    longitude: float | None
    occupancy: str | None


async def async_fetch(
    session: aiohttp.ClientSession, url: str, headers: Mapping[str, str]
) -> bytes:
    """Download a feed and return the raw response body."""
    request_headers = dict(headers)
    if not any(key.lower() == "accept" for key in request_headers):
        request_headers["Accept"] = ACCEPT
    try:
        async with session.get(
            url, headers=request_headers, timeout=REQUEST_TIMEOUT
        ) as response:
            # Messages deliberately leave out the URL: it often contains an API key.
            if response.status in (401, 403):
                raise FeedAuthError(f"HTTP {response.status}")
            if response.status >= 400:
                raise FeedFetchError(f"HTTP {response.status}")
            return await response.read()
    except TimeoutError as err:
        raise FeedFetchError("request timed out") from err
    except aiohttp.ClientConnectionError as err:
        # Connection errors only name the host; other client errors (redirects,
        # invalid URLs, HTTP errors) embed the full URL in their message.
        raise FeedFetchError(f"{type(err).__name__}: {err}") from err
    except aiohttp.ClientResponseError as err:
        raise FeedFetchError(f"{type(err).__name__} (HTTP {err.status})") from err
    except aiohttp.ClientError as err:
        raise FeedFetchError(type(err).__name__) from err


def parse_feed(content: bytes) -> gtfs_realtime_pb2.FeedMessage:
    """Parse a protobuf (or JSON) GTFS-realtime feed."""
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(content)
    except DecodeError as err:
        # Only fall back to JSON after binary fails: a binary feed starts with
        # 0x0A ("\n") and can be followed by "{", so sniffing would misfire.
        if content.lstrip()[:1] != b"{":
            raise InvalidFeedError(_describe(content)) from err
        feed = gtfs_realtime_pb2.FeedMessage()
        try:
            json_format.Parse(content, feed, ignore_unknown_fields=True)
        except (json_format.ParseError, UnicodeDecodeError) as json_err:
            raise InvalidFeedError(_describe(content)) from json_err
    if not feed.HasField("header") and not feed.entity:
        raise InvalidFeedError(_describe(content))
    return feed


def build_departures(
    trip_feed: gtfs_realtime_pb2.FeedMessage,
    vehicle_feed: gtfs_realtime_pb2.FeedMessage | None,
    now: datetime,
) -> dict[str, list[Departure]]:
    """Index upcoming departures by stop ID, soonest first."""
    vehicles = _VehicleIndex(vehicle_feed)
    now_ts = now.timestamp()
    departures: defaultdict[str, list[Departure]] = defaultdict(list)

    for entity in trip_feed.entity:
        if entity.is_deleted or not entity.HasField("trip_update"):
            continue
        trip_update = entity.trip_update
        trip = trip_update.trip
        if _name(_TripRelationship, trip.schedule_relationship) in _SKIPPED_TRIPS:
            continue

        trip_vehicle = vehicles.for_trip(trip)
        vehicle = vehicles.for_vehicle_id(trip_update.vehicle.id) or trip_vehicle
        # route_id is optional when trip_id is set. Only a position reported for
        # this same trip says which route it is; the vehicle may still be
        # finishing a different trip.
        route_id = trip.route_id or (trip_vehicle.route_id if trip_vehicle else "")
        vehicle_id = trip_update.vehicle.id or (vehicle.vehicle_id if vehicle else None)

        for update in trip_update.stop_time_update:
            if (
                not update.stop_id
                or _name(_StopRelationship, update.schedule_relationship)
                in _SKIPPED_STOPS
            ):
                continue
            timestamp = _next_event_time(update, now_ts)
            if timestamp is None or (when := to_datetime(timestamp)) is None:
                continue
            departures[update.stop_id].append(
                Departure(
                    route_id=route_id,
                    trip_id=trip.trip_id,
                    time=when,
                    vehicle_id=vehicle_id,
                    latitude=vehicle.latitude if vehicle else None,
                    longitude=vehicle.longitude if vehicle else None,
                    occupancy=vehicle.occupancy if vehicle else None,
                )
            )

    for stop_departures in departures.values():
        stop_departures.sort(key=lambda departure: departure.time)
    return dict(departures)


def _next_event_time(
    update: gtfs_realtime_pb2.TripUpdate.StopTimeUpdate, now_ts: float
) -> int | None:
    """Return the arrival time, or the departure time if the vehicle already arrived.

    Either event is optional in the spec; arrival is preferred so existing
    sensors keep counting down to the same moment.
    """
    for event in (update.arrival, update.departure):
        if event.HasField("time") and event.time > now_ts:
            return int(event.time)
    return None


class _VehicleIndex:
    """Vehicle positions, looked up by vehicle ID or by trip."""

    def __init__(self, feed: gtfs_realtime_pb2.FeedMessage | None) -> None:
        self._by_vehicle_id: dict[str, _Vehicle] = {}
        self._by_trip: dict[tuple[str, str, str], _Vehicle] = {}
        # None marks a trip_id that several vehicles run at once, which the
        # spec allows for frequency-based trips (told apart by start time).
        self._by_trip_id: dict[str, _Vehicle | None] = {}
        for entity in feed.entity if feed else ():
            if entity.is_deleted or not entity.HasField("vehicle"):
                continue
            self._add(entity.vehicle)

    def _add(self, position: gtfs_realtime_pb2.VehiclePosition) -> None:
        has_fix = position.HasField("position")
        occupancy = None
        if position.HasField("occupancy_status"):
            occupancy = _name(_OccupancyStatus, position.occupancy_status) or None
        vehicle = _Vehicle(
            vehicle_id=position.vehicle.id or None,
            route_id=position.trip.route_id,
            latitude=position.position.latitude if has_fix else None,
            longitude=position.position.longitude if has_fix else None,
            occupancy=occupancy,
        )
        if vehicle.vehicle_id:
            self._by_vehicle_id[vehicle.vehicle_id] = vehicle
        if trip_id := position.trip.trip_id:
            self._by_trip[_trip_key(position.trip)] = vehicle
            existing = self._by_trip_id.get(trip_id, vehicle)
            self._by_trip_id[trip_id] = vehicle if existing == vehicle else None

    def for_vehicle_id(self, vehicle_id: str) -> _Vehicle | None:
        return self._by_vehicle_id.get(vehicle_id) if vehicle_id else None

    def for_trip(self, trip: gtfs_realtime_pb2.TripDescriptor) -> _Vehicle | None:
        if not trip.trip_id:
            return None
        return self._by_trip.get(_trip_key(trip)) or self._by_trip_id.get(trip.trip_id)


def _trip_key(trip: gtfs_realtime_pb2.TripDescriptor) -> tuple[str, str, str]:
    return (trip.trip_id, trip.start_date, trip.start_time)


def to_datetime(timestamp: int) -> datetime | None:
    """Convert a POSIX timestamp; None if out of range (e.g. in milliseconds)."""
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _name(enum: Any, value: int) -> str:
    """Return the name of an enum value, or an empty string if it is unknown."""
    try:
        return str(enum.Name(value))
    except ValueError:
        return ""


def _describe(content: bytes) -> str:
    """Explain why a response is not a GTFS-realtime feed."""
    head = content.lstrip()[:64]
    if not head:
        return "the server returned an empty response"
    if head.startswith(b"PK"):
        return (
            "the server returned a ZIP file; this looks like a static GTFS "
            "schedule rather than a GTFS-realtime feed"
        )
    if head.startswith(b"<"):
        return (
            "the server returned an HTML or XML page instead of a feed; "
            "check the URL and credentials"
        )
    if head.startswith(b"{"):
        return "the server returned JSON that is not a GTFS-realtime feed"
    if all(32 <= byte < 127 or byte in b"\t\r\n" for byte in head):
        return (
            "the server returned text instead of binary protobuf; remove any "
            "debug or text-format option from the URL"
        )
    return "the response could not be parsed as a GTFS-realtime feed"
