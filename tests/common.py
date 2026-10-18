"""Helpers for building GTFS-realtime feeds in tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from google.transit import gtfs_realtime_pb2
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant

# 12:00 in New York, 17:00 UTC.
NOW = datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

TRIP_URL = "https://transit.example.com/trip-updates.pb?key=SECRET"
VEHICLE_URL = "https://transit.example.com/vehicle-positions.pb?key=SECRET"


def at(minutes: float) -> int:
    """Return the POSIX timestamp `minutes` after NOW."""
    return int((NOW + timedelta(minutes=minutes)).timestamp())


def make_feed() -> gtfs_realtime_pb2.FeedMessage:
    """Return an empty feed with a valid header."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = at(0)
    return feed


def add_trip(
    feed: gtfs_realtime_pb2.FeedMessage,
    trip_id: str,
    route_id: str = "",
    *,
    stops: dict[str, float] | None = None,
    vehicle_id: str = "",
) -> gtfs_realtime_pb2.TripUpdate:
    """Add a trip update; `stops` maps stop IDs to arrival minutes after NOW."""
    trip_update = feed.entity.add(id=f"trip-{trip_id}").trip_update
    trip_update.trip.trip_id = trip_id
    if route_id:
        trip_update.trip.route_id = route_id
    if vehicle_id:
        trip_update.vehicle.id = vehicle_id
    for stop_id, minutes in (stops or {}).items():
        trip_update.stop_time_update.add(stop_id=stop_id).arrival.time = at(minutes)
    return trip_update


def add_vehicle(
    feed: gtfs_realtime_pb2.FeedMessage,
    vehicle_id: str = "",
    *,
    trip_id: str = "",
    route_id: str = "",
    position: tuple[float, float] | None = None,
    occupancy: str | None = None,
) -> gtfs_realtime_pb2.VehiclePosition:
    """Add a vehicle position."""
    vehicle = feed.entity.add(id=f"vehicle-{vehicle_id or trip_id}").vehicle
    if vehicle_id:
        vehicle.vehicle.id = vehicle_id
    if trip_id:
        vehicle.trip.trip_id = trip_id
    if route_id:
        vehicle.trip.route_id = route_id
    if position:
        vehicle.position.latitude, vehicle.position.longitude = position
    if occupancy:
        vehicle.occupancy_status = (
            gtfs_realtime_pb2.VehiclePosition.OccupancyStatus.Value(occupancy)
        )
    return vehicle


def standard_feeds() -> tuple[bytes, bytes]:
    """Return serialized trip and vehicle feeds used by most entity tests.

    Route 100 leaves stop 514 in 5 and 12 minutes; route 200 leaves in 3.
    Only the 5-minute bus reports a position and occupancy.
    """
    trips = make_feed()
    add_trip(trips, "t1", "100", stops={"514": 5}, vehicle_id="bus-1")
    add_trip(trips, "t2", "100", stops={"514": 12})
    add_trip(trips, "t3", "200", stops={"514": 3})
    vehicles = make_feed()
    add_vehicle(
        vehicles,
        "bus-1",
        trip_id="t1",
        route_id="100",
        position=(40.75, -73.5),
        occupancy="MANY_SEATS_AVAILABLE",
    )
    return trips.SerializeToString(), vehicles.SerializeToString()


async def advance_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Move time forward and wait for any coordinator refresh it triggers.

    Scheduled refreshes run as background tasks, so they must be awaited
    explicitly.
    """
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
