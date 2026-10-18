"""Tests for diagnostics."""

from __future__ import annotations

import json

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .common import TRIP_URL, VEHICLE_URL, add_trip, at, make_feed, standard_feeds


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_feeds: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Diagnostics help match IDs and never leak credentials."""
    assert await async_setup_component(hass, "diagnostics", {})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )

    assert "SECRET" not in json.dumps(diagnostics)
    assert diagnostics["feed"] == {
        "trip_update_url": "https://transit.example.com/**REDACTED**",
        "vehicle_position_url": "https://transit.example.com/**REDACTED**",
        "headers": {"x-api-key": "**REDACTED**"},
        "last_update_success": True,
        "last_exception": None,
        "vehicle_feed_error": None,
        "feed_timestamp": "2026-01-15T17:00:00+00:00",
        "stops_with_departures": 1,
    }
    assert sorted(diagnostics["departures"], key=lambda d: d["title"]) == [
        {
            "title": "Any bus at 514",
            "stop_id": "514",
            "route_id": None,
            "departures_at_stop": 3,
            "routes_at_stop": ["100", "200"],
        },
        {
            "title": "Downtown to airport",
            "stop_id": "514",
            "route_id": "100",
            "departures_at_stop": 3,
            "routes_at_stop": ["100", "200"],
        },
    ]


async def test_diagnostics_vehicle_feed_error(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A failing vehicle feed shows up in diagnostics."""
    trips, _ = standard_feeds()
    aioclient_mock.get(config_entry.data["trip_update_url"], content=trips)
    aioclient_mock.get(VEHICLE_URL, status=500)
    assert await async_setup_component(hass, "diagnostics", {})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )

    assert diagnostics["feed"]["vehicle_feed_error"] == "HTTP 500"


@pytest.mark.parametrize("timestamp", [None, at(0) * 1000])
async def test_diagnostics_without_usable_feed_timestamp(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    timestamp: int | None,
) -> None:
    """A missing or out-of-range (milliseconds) header timestamp is ignored."""
    feed = make_feed()
    feed.header.ClearField("timestamp")
    if timestamp is not None:
        feed.header.timestamp = timestamp
    add_trip(feed, "t", "100", stops={"514": 5})
    aioclient_mock.get(TRIP_URL, content=feed.SerializeToString())
    aioclient_mock.get(VEHICLE_URL, content=make_feed().SerializeToString())
    assert await async_setup_component(hass, "diagnostics", {})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )

    assert diagnostics["feed"]["feed_timestamp"] is None
    assert diagnostics["feed"]["last_update_success"] is True
