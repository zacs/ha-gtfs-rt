"""Backwards-compatibility tests for YAML-configured sensors.

These pin down the 1.x contract: configuration keys, entity IDs, state and
attribute names/formats.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
import voluptuous as vol
import yaml

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.gtfs_rt.feed import ACCEPT
from custom_components.gtfs_rt.sensor import PLATFORM_SCHEMA

from .common import (
    TRIP_URL,
    VEHICLE_URL,
    add_trip,
    advance_time,
    make_feed,
    standard_feeds,
)

YAML_CONFIG: dict[str, Any] = {
    "platform": "gtfs_rt",
    "trip_update_url": TRIP_URL,
    "vehicle_position_url": VEHICLE_URL,
    "departures": [{"name": "Downtown to airport", "route": "100", "stopid": "514"}],
}
ENTITY_ID = "sensor.downtown_to_airport"


async def setup_yaml(hass: HomeAssistant, config: dict[str, Any]) -> None:
    assert await async_setup_component(hass, "sensor", {"sensor": [config]})
    await hass.async_block_till_done()


class _SecretLoader(yaml.SafeLoader):
    """YAML loader that accepts Home Assistant's !secret tag."""


_SecretLoader.add_constructor("!secret", lambda loader, node: "secret")


def readme_platform_configs() -> list[dict[str, Any]]:
    """Return every `platform: gtfs_rt` block from the README examples."""
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    configs = []
    for block in re.findall(r"```yaml\n(.*?)```", readme, re.DOTALL):
        loaded = yaml.load(block, Loader=_SecretLoader)
        items = loaded.get("sensor", []) if isinstance(loaded, dict) else loaded
        configs.extend(
            item
            for item in items or []
            if isinstance(item, dict) and item.get("platform") == "gtfs_rt"
        )
    return configs


def test_readme_examples_are_valid() -> None:
    """Every YAML example in the README passes the schema."""
    configs = readme_platform_configs()
    assert len(configs) >= 5
    for config in configs:
        PLATFORM_SCHEMA(config)


@pytest.mark.parametrize(
    "config",
    [
        # Examples from the 1.0.1 README, including unquoted numeric IDs.
        {
            "platform": "gtfs_rt",
            "trip_update_url": "https://data.texas.gov/download/rmk2-acnw/application%2foctet-stream",
            "vehicle_position_url": "https://data.texas.gov/download/eiei-9rpf/application%2Foctet-stream",
            "departures": [
                {"name": "Downtown to airport", "route": 100, "stopid": 514}
            ],
        },
        {
            "platform": "gtfs_rt",
            "trip_update_url": "https://api.stm.info/pub/od/gtfs-rt/ic/v2/tripUpdates",
            "apikey": "key",
            "departures": [{"name": "Bus 178", "route": 168, "stopid": 56698}],
        },
        {
            "platform": "gtfs_rt",
            "trip_update_url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm",
            "x_api_key": "key",
            "scan_interval": 60,
            "entity_namespace": "gtfs_mta_subway",
            "departures": [{"name": "Brooklyn F", "route": "F", "stopid": "F16S"}],
        },
        {
            "platform": "gtfs_rt",
            "trip_update_url": "https://api.at.govt.nz/realtime/legacy/tripupdates",
            "headers": {"Ocp-Apim-Subscription-Key": "key"},
            "departures": [{"route": "HOBS-209", "stopid": "21779-d98fae38"}],
        },
        {"platform": "gtfs_rt", "trip_update_url": TRIP_URL, "api_key": "key"},
    ],
)
def test_legacy_configs_are_valid(config: dict[str, Any]) -> None:
    """Configurations that worked in 1.x still validate."""
    validated = PLATFORM_SCHEMA(config)
    for departure in validated["departures"]:
        assert isinstance(departure["stopid"], str)
        assert departure["name"]


def test_auth_shortcuts_are_mutually_exclusive() -> None:
    """As in 1.x, only one way of passing credentials is allowed."""
    with pytest.raises(vol.Invalid):
        PLATFORM_SCHEMA({**YAML_CONFIG, "api_key": "a", "headers": {"b": "c"}})


@pytest.mark.usefixtures("new_york", "mock_feeds")
async def test_state_and_attributes(hass: HomeAssistant) -> None:
    """State and attributes keep their 1.x names and formats."""
    await setup_yaml(hass, YAML_CONFIG)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "5"
    assert state.attributes == {
        "Due in": 5,
        "Due at": "12:05",
        "Stop ID": "514",
        "Route": "100",
        "Occupancy": "MANY_SEATS_AVAILABLE",
        "latitude": 40.75,
        "longitude": -73.5,
        "Next bus": "12:12",
        "Next bus due in": 12,
        "Next bus occupancy": None,
        "departures": [
            {
                "route_id": "100",
                "trip_id": "t1",
                "vehicle_id": "bus-1",
                "time": "2026-01-15T12:05:00-05:00",
                "due_in": 5,
                "occupancy": "MANY_SEATS_AVAILABLE",
            },
            {
                "route_id": "100",
                "trip_id": "t2",
                "vehicle_id": None,
                "time": "2026-01-15T12:12:00-05:00",
                "due_in": 12,
                "occupancy": None,
            },
        ],
        "unit_of_measurement": "min",
        "device_class": "duration",
        "friendly_name": "Downtown to airport",
        "icon": "mdi:bus",
    }


@pytest.mark.usefixtures("mock_feeds")
async def test_no_upcoming_departures(hass: HomeAssistant) -> None:
    """With nothing scheduled the state is unknown; attributes keep 1.x values."""
    await setup_yaml(
        hass,
        {**YAML_CONFIG, "departures": [{"name": "Night owl", "stopid": "999"}]},
    )

    state = hass.states.get("sensor.night_owl")
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes["Due in"] == "-"
    assert state.attributes["Stop ID"] == "999"
    assert state.attributes["Route"] is None
    assert state.attributes["departures"] == []
    assert "Due at" not in state.attributes
    assert "Next bus" not in state.attributes


@pytest.mark.usefixtures("mock_feeds")
async def test_route_is_optional(hass: HomeAssistant) -> None:
    """Without a route the sensor shows the next departure of any route."""
    await setup_yaml(
        hass, {**YAML_CONFIG, "departures": [{"name": "Any bus", "stopid": "514"}]}
    )

    state = hass.states.get("sensor.any_bus")
    assert state is not None
    assert state.state == "3"
    assert [d["route_id"] for d in state.attributes["departures"]] == [
        "200",
        "100",
        "100",
    ]


@pytest.mark.usefixtures("mock_feeds")
async def test_default_name_and_entity_namespace(hass: HomeAssistant) -> None:
    """The default name and entity_namespace produce the same IDs as 1.x."""
    await setup_yaml(
        hass,
        {
            **YAML_CONFIG,
            "entity_namespace": "transit",
            "departures": [{"route": "100", "stopid": "514"}],
        },
    )

    assert hass.states.get("sensor.transit_next_bus") is not None


@pytest.mark.usefixtures("mock_feeds")
async def test_unique_id_registers_entity(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """An optional unique_id makes the entity editable from the UI."""
    await setup_yaml(
        hass,
        {
            **YAML_CONFIG,
            "departures": [
                {"name": "Downtown to airport", "route": "100", "stopid": "514"},
                {"name": "Registered", "stopid": "514", "unique_id": "my-stop"},
            ],
        },
    )

    assert entity_registry.async_get(ENTITY_ID) is None
    entry = entity_registry.async_get("sensor.registered")
    assert entry is not None
    assert entry.unique_id == "my-stop"


@pytest.mark.parametrize(
    ("auth", "expected"),
    [
        ({"api_key": "k"}, {"Authorization": "k"}),
        ({"apikey": "k"}, {"apikey": "k"}),
        ({"x_api_key": "k"}, {"x-api-key": "k"}),
        (
            {"headers": {"Ocp-Apim-Subscription-Key": "k"}},
            {"Ocp-Apim-Subscription-Key": "k"},
        ),
        ({}, {}),
    ],
)
async def test_auth_headers(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    auth: dict[str, Any],
    expected: dict[str, str],
) -> None:
    """Each auth option sends the same header as in 1.x, to both feeds."""
    await setup_yaml(hass, {**YAML_CONFIG, **auth})

    assert mock_feeds.call_count == 2
    for call in mock_feeds.mock_calls:
        assert call[3] == {**expected, "Accept": ACCEPT}


async def test_vehicle_positions_are_optional(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without a vehicle feed only trip updates are fetched."""
    trips, _ = standard_feeds()
    aioclient_mock.get(TRIP_URL, content=trips)
    config = {k: v for k, v in YAML_CONFIG.items() if k != "vehicle_position_url"}

    await setup_yaml(hass, config)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "5"
    assert state.attributes["Occupancy"] is None
    assert "latitude" not in state.attributes
    assert aioclient_mock.call_count == 1


async def test_feed_error_then_recovery(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """A feed that is down at startup leaves the sensor unavailable, not missing."""
    aioclient_mock.get(TRIP_URL, status=500)
    aioclient_mock.get(VEHICLE_URL, status=500)
    await setup_yaml(hass, YAML_CONFIG)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    trips, vehicles = standard_feeds()
    aioclient_mock.clear_requests()
    aioclient_mock.get(TRIP_URL, content=trips)
    aioclient_mock.get(VEHICLE_URL, content=vehicles)
    await advance_time(hass, frozen_time, 60)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "4"


async def test_vehicle_feed_failure_keeps_trip_updates(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken vehicle feed is logged once and doesn't affect departures."""
    trips, vehicles = standard_feeds()
    aioclient_mock.get(TRIP_URL, content=trips)
    aioclient_mock.get(VEHICLE_URL, status=500)
    await setup_yaml(hass, YAML_CONFIG)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "5"
    assert state.attributes["Occupancy"] is None
    assert "latitude" not in state.attributes
    assert caplog.text.count("Vehicle positions feed unavailable") == 1

    # Still failing: no repeated warning.
    await advance_time(hass, frozen_time, 60)
    assert caplog.text.count("Vehicle positions feed unavailable") == 1

    aioclient_mock.clear_requests()
    aioclient_mock.get(TRIP_URL, content=trips)
    aioclient_mock.get(VEHICLE_URL, content=vehicles)
    await advance_time(hass, frozen_time, 60)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["Occupancy"] == "MANY_SEATS_AVAILABLE"
    assert "Vehicle positions feed is available again" in caplog.text


async def test_invalid_feed_is_explained(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pointing at a web page logs why it isn't a feed, without the URL."""
    aioclient_mock.get(TRIP_URL, content=b"<html>Sign in</html>")
    aioclient_mock.get(VEHICLE_URL, content=b"<html>Sign in</html>")
    await setup_yaml(hass, YAML_CONFIG)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    assert "HTML or XML page" in caplog.text
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize(
    ("scan_interval", "expected_seconds"), [(10, 60), (60, 60), (300, 300)]
)
async def test_scan_interval_has_one_minute_floor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
    scan_interval: int,
    expected_seconds: int,
) -> None:
    """Shorter intervals were throttled to a minute in 1.x; longer ones are honored."""
    trips, _ = standard_feeds()
    aioclient_mock.get(TRIP_URL, content=trips)
    config = {k: v for k, v in YAML_CONFIG.items() if k != "vehicle_position_url"}
    await setup_yaml(hass, {**config, "scan_interval": scan_interval})
    assert aioclient_mock.call_count == 1

    await advance_time(hass, frozen_time, expected_seconds - 1)
    assert aioclient_mock.call_count == 1

    await advance_time(hass, frozen_time, 1)
    assert aioclient_mock.call_count == 2


async def test_departures_attribute_is_capped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The departures list is bounded; it is also excluded from the recorder."""
    feed = make_feed()
    for minute in range(1, 16):
        add_trip(feed, f"t{minute}", "100", stops={"514": minute})
    aioclient_mock.get(TRIP_URL, content=feed.SerializeToString())
    config = {k: v for k, v in YAML_CONFIG.items() if k != "vehicle_position_url"}

    await setup_yaml(hass, config)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert len(state.attributes["departures"]) == 10
