"""Tests for GTFS-realtime parsing and departure building."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiohttp
from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2
from multidict import CIMultiDict, CIMultiDictProxy
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.gtfs_rt.feed import (
    ACCEPT,
    FeedAuthError,
    FeedFetchError,
    InvalidFeedError,
    _name,
    async_fetch,
    build_departures,
    parse_feed,
)

from .common import NOW, TRIP_URL, add_trip, add_vehicle, at, make_feed

TripDescriptor = gtfs_realtime_pb2.TripDescriptor
StopTimeUpdate = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate
REQUEST_INFO = aiohttp.RequestInfo(
    URL(TRIP_URL), "GET", CIMultiDictProxy(CIMultiDict()), URL(TRIP_URL)
)


def minutes(departure_time: datetime) -> float:
    return (departure_time - NOW).total_seconds() / 60


def test_departures_are_indexed_by_stop_and_sorted() -> None:
    """Departures are grouped per stop, soonest first, with UTC-aware times."""
    feed = make_feed()
    add_trip(feed, "late", "100", stops={"A": 10, "B": 15})
    add_trip(feed, "early", "200", stops={"A": 5})

    departures = build_departures(feed, None, NOW)

    assert [d.trip_id for d in departures["A"]] == ["early", "late"]
    assert [d.route_id for d in departures["A"]] == ["200", "100"]
    assert [d.trip_id for d in departures["B"]] == ["late"]
    first = departures["A"][0]
    assert first.time == NOW + timedelta(minutes=5)
    assert first.time.tzinfo is UTC


def test_past_departures_are_dropped() -> None:
    """Only future departures are kept."""
    feed = make_feed()
    add_trip(feed, "gone", "100", stops={"A": -1})
    add_trip(feed, "now", "100", stops={"A": 0})
    add_trip(feed, "soon", "100", stops={"A": 1})

    assert [d.trip_id for d in build_departures(feed, None, NOW)["A"]] == ["soon"]


def test_departure_time_used_when_arrival_missing() -> None:
    """The first stop of a trip often only has a departure time."""
    feed = make_feed()
    add_trip(feed, "t", "100").stop_time_update.add(stop_id="A").departure.time = at(4)

    assert minutes(build_departures(feed, None, NOW)["A"][0].time) == 4


def test_arrival_preferred_over_departure() -> None:
    """Arrival time stays the primary value, as in 1.x."""
    feed = make_feed()
    update = add_trip(feed, "t", "100").stop_time_update.add(stop_id="A")
    update.arrival.time = at(4)
    update.departure.time = at(6)

    assert minutes(build_departures(feed, None, NOW)["A"][0].time) == 4


def test_departure_time_used_while_vehicle_is_at_stop() -> None:
    """A vehicle that has arrived but not left can still be caught."""
    feed = make_feed()
    update = add_trip(feed, "t", "100").stop_time_update.add(stop_id="A")
    update.arrival.time = at(-1)
    update.departure.time = at(2)

    assert minutes(build_departures(feed, None, NOW)["A"][0].time) == 2


def test_delay_only_updates_are_skipped() -> None:
    """Without a static schedule a delay alone can't be turned into a time."""
    feed = make_feed()
    add_trip(feed, "t", "100").stop_time_update.add(stop_id="A").arrival.delay = 60

    assert build_departures(feed, None, NOW) == {}


@pytest.mark.parametrize("relationship", ["CANCELED", "DELETED"])
def test_canceled_trips_are_skipped(relationship: str) -> None:
    """Canceled and deleted trips never show up."""
    feed = make_feed()
    trip = add_trip(feed, "t", "100", stops={"A": 5})
    trip.trip.schedule_relationship = TripDescriptor.ScheduleRelationship.Value(
        relationship
    )

    assert build_departures(feed, None, NOW) == {}


@pytest.mark.parametrize("relationship", ["SKIPPED", "NO_DATA"])
def test_skipped_stops_are_skipped(relationship: str) -> None:
    """Stops the vehicle won't serve, or has no data for, are dropped."""
    feed = make_feed()
    trip = add_trip(feed, "t", "100", stops={"A": 5, "B": 8})
    trip.stop_time_update[
        0
    ].schedule_relationship = StopTimeUpdate.ScheduleRelationship.Value(relationship)

    assert list(build_departures(feed, None, NOW)) == ["B"]


def test_deleted_entities_and_other_messages_are_ignored() -> None:
    """Deleted entities and non-trip-update entities are skipped."""
    feed = make_feed()
    add_trip(feed, "t", "100", stops={"A": 5})
    feed.entity[0].is_deleted = True
    add_vehicle(feed, "bus", trip_id="t")

    assert build_departures(feed, None, NOW) == {}


def test_stop_time_updates_without_stop_id_are_skipped() -> None:
    """Updates identified only by stop_sequence can't be matched to a stop."""
    feed = make_feed()
    update = add_trip(feed, "t", "100").stop_time_update.add(stop_sequence=3)
    update.arrival.time = at(5)

    assert build_departures(feed, None, NOW) == {}


def test_vehicle_joined_by_vehicle_id() -> None:
    """Position and occupancy come from the vehicle named in the trip update."""
    trips = make_feed()
    add_trip(trips, "t", "100", stops={"A": 5}, vehicle_id="bus")
    vehicles = make_feed()
    add_vehicle(vehicles, "bus", position=(1.5, 2.5), occupancy="FULL")

    departure = build_departures(trips, vehicles, NOW)["A"][0]

    assert departure.vehicle_id == "bus"
    assert (departure.latitude, departure.longitude) == (1.5, 2.5)
    assert departure.occupancy == "FULL"


def test_vehicle_joined_by_trip_id() -> None:
    """Trip updates without a vehicle are matched through the trip ID."""
    trips = make_feed()
    add_trip(trips, "t", "100", stops={"A": 5})
    vehicles = make_feed()
    add_vehicle(
        vehicles,
        "bus",
        trip_id="t",
        position=(1.5, 2.5),
        occupancy="STANDING_ROOM_ONLY",
    )

    departure = build_departures(trips, vehicles, NOW)["A"][0]

    assert departure.vehicle_id == "bus"
    assert departure.latitude == 1.5
    assert departure.occupancy == "STANDING_ROOM_ONLY"


def test_route_id_falls_back_to_vehicle_position() -> None:
    """route_id is optional in trip updates when vehicle positions carry it."""
    trips = make_feed()
    add_trip(trips, "t", stops={"A": 5})
    vehicles = make_feed()
    add_vehicle(vehicles, trip_id="t", route_id="100")

    departure = build_departures(trips, vehicles, NOW)["A"][0]

    assert departure.route_id == "100"
    assert departure.vehicle_id is None


def test_out_of_range_times_are_skipped() -> None:
    """A timestamp in milliseconds is dropped instead of failing the whole feed."""
    feed = make_feed()
    add_trip(feed, "bad", "100").stop_time_update.add(stop_id="A").arrival.time = (
        at(5) * 1000
    )
    add_trip(feed, "good", "100", stops={"A": 6})

    assert [d.trip_id for d in build_departures(feed, None, NOW)["A"]] == ["good"]


def test_route_id_not_taken_from_vehicle_on_another_trip() -> None:
    """A vehicle still finishing its previous trip doesn't lend that trip's route."""
    trips = make_feed()
    add_trip(trips, "next", stops={"A": 20}, vehicle_id="bus")
    vehicles = make_feed()
    add_vehicle(vehicles, "bus", trip_id="current", route_id="R1", position=(1.5, 2.5))

    departure = build_departures(trips, vehicles, NOW)["A"][0]

    assert departure.route_id == ""
    assert departure.vehicle_id == "bus"
    assert departure.latitude == 1.5


def test_frequency_based_trips_are_told_apart_by_start_time() -> None:
    """Vehicles sharing a trip_id are matched by start time, never guessed."""
    trips = make_feed()
    add_trip(trips, "f", "100", stops={"A": 5}).trip.start_time = "08:10:00"
    add_trip(trips, "f", "100", stops={"B": 5})
    vehicles = make_feed()
    for vehicle_id, start_time in (("early", "08:00:00"), ("late", "08:10:00")):
        add_vehicle(vehicles, vehicle_id, trip_id="f").trip.start_time = start_time

    departures = build_departures(trips, vehicles, NOW)

    assert departures["A"][0].vehicle_id == "late"
    assert departures["B"][0].vehicle_id is None


def test_trip_updates_without_trip_id() -> None:
    """A trip identified only by its route still yields departures."""
    trips = make_feed()
    trips.entity.add(id="r").trip_update.trip.route_id = "100"
    trips.entity[0].trip_update.stop_time_update.add(stop_id="A").arrival.time = at(5)
    vehicles = make_feed()
    add_vehicle(vehicles, "bus", route_id="100", position=(1.5, 2.5))

    departure = build_departures(trips, vehicles, NOW)["A"][0]

    assert departure.route_id == "100"
    assert departure.vehicle_id is None


def test_unreported_occupancy_and_position_are_none() -> None:
    """Missing fields are None, not the protobuf defaults (EMPTY, 0°/0°)."""
    trips = make_feed()
    add_trip(trips, "t", "100", stops={"A": 5}, vehicle_id="bus")
    vehicles = make_feed()
    add_vehicle(vehicles, "bus", trip_id="t", route_id="100")

    departure = build_departures(trips, vehicles, NOW)["A"][0]

    assert departure.occupancy is None
    assert departure.latitude is None
    assert departure.longitude is None


def test_deleted_vehicles_are_ignored() -> None:
    """Deleted vehicle entities are not joined."""
    trips = make_feed()
    add_trip(trips, "t", "100", stops={"A": 5}, vehicle_id="bus")
    vehicles = make_feed()
    add_vehicle(vehicles, "bus", position=(1.5, 2.5))
    vehicles.entity[0].is_deleted = True

    assert build_departures(trips, vehicles, NOW)["A"][0].latitude is None


def test_unknown_enum_values_have_no_name() -> None:
    """Enum values newer than the bindings never match a skip rule."""
    assert _name(TripDescriptor.ScheduleRelationship, 999) == ""
    assert _name(TripDescriptor.ScheduleRelationship, 3) == "CANCELED"


def test_parse_protobuf() -> None:
    """Binary protobuf round-trips."""
    feed = make_feed()
    add_trip(feed, "t", "100", stops={"A": 5})

    assert parse_feed(feed.SerializeToString()) == feed


def test_parse_json() -> None:
    """JSON feeds parse with camelCase or snake_case field names."""
    feed = make_feed()
    add_trip(feed, "t", "100", stops={"A": 5})
    camel = json_format.MessageToJson(feed).encode()
    snake = json_format.MessageToJson(feed, preserving_proto_field_name=True).encode()

    assert parse_feed(camel) == feed
    assert parse_feed(b"  \n" + snake) == feed


def test_parse_binary_feed_that_looks_like_json() -> None:
    """A 123-byte header serializes as b"\\n{", which is still binary protobuf."""
    feed = make_feed()
    while feed.header.ByteSize() < 123:
        feed.header.feed_version += "x"
    add_trip(feed, "t", "100", stops={"A": 5})
    content = feed.SerializeToString()
    assert content.startswith(b"\n{")

    assert parse_feed(content) == feed


def test_parse_tolerates_missing_header() -> None:
    """Feeds that skip required fields still parse if they contain entities."""
    feed = gtfs_realtime_pb2.FeedMessage()
    add_trip(feed, "t", "100", stops={"A": 5})

    assert len(parse_feed(feed.SerializePartialToString()).entity) == 1


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "empty response"),
        (b"PK\x03\x04\x14\x00\x00\x00\x08\x00", "static GTFS"),
        (b"<!DOCTYPE html><html><body>Forbidden</body></html>", "HTML"),
        (b'{"status": "OK", "response": {}}', "JSON that is not"),
        (b"{not json", "JSON that is not"),
        (b'header {\n  gtfs_realtime_version: "2.0"\n}\n', "text instead"),
        (b"\xff\xff\xff\xff\xff", "could not be parsed"),
    ],
)
def test_parse_rejects_non_feeds(content: bytes, message: str) -> None:
    """Common wrong URLs get an actionable explanation."""
    with pytest.raises(InvalidFeedError, match=message):
        parse_feed(content)


async def test_fetch_sends_headers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """User headers are sent, with a protobuf Accept header by default."""
    aioclient_mock.get(TRIP_URL, content=b"feed")

    content = await async_fetch(
        async_get_clientsession(hass), TRIP_URL, {"x-api-key": "k"}
    )

    assert content == b"feed"
    assert aioclient_mock.mock_calls[0][3] == {"x-api-key": "k", "Accept": ACCEPT}


async def test_fetch_keeps_user_accept_header(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A user-supplied Accept header (any case) wins."""
    aioclient_mock.get(TRIP_URL, content=b"feed")

    await async_fetch(async_get_clientsession(hass), TRIP_URL, {"accept": "x/y"})

    assert aioclient_mock.mock_calls[0][3] == {"accept": "x/y"}


@pytest.mark.parametrize(
    ("mock_kwargs", "error", "message"),
    [
        ({"status": 401}, FeedAuthError, "HTTP 401"),
        ({"status": 403}, FeedAuthError, "HTTP 403"),
        ({"status": 404}, FeedFetchError, "HTTP 404"),
        ({"status": 503}, FeedFetchError, "HTTP 503"),
        ({"exc": TimeoutError()}, FeedFetchError, "timed out"),
        ({"exc": aiohttp.ClientConnectionError("boom")}, FeedFetchError, "boom"),
        # These embed the full URL, including any key, in their message.
        (
            {"exc": aiohttp.TooManyRedirects(REQUEST_INFO, ())},
            FeedFetchError,
            "TooManyRedirects",
        ),
        (
            {"exc": aiohttp.InvalidUrlClientError(URL(TRIP_URL))},
            FeedFetchError,
            "InvalidUrlClientError",
        ),
        (
            {"exc": aiohttp.NonHttpUrlClientError(URL(TRIP_URL))},
            FeedFetchError,
            "NonHttpUrlClientError",
        ),
    ],
)
async def test_fetch_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict,
    error: type[Exception],
    message: str,
) -> None:
    """HTTP and network errors map to feed errors that never include the URL."""
    aioclient_mock.get(TRIP_URL, **mock_kwargs)

    with pytest.raises(error, match=message) as excinfo:
        await async_fetch(async_get_clientsession(hass), TRIP_URL, {})

    assert "SECRET" not in str(excinfo.value)
