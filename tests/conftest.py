"""Fixtures for GTFS-Realtime tests."""

from __future__ import annotations

from collections.abc import Generator
import os
import time

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant

from custom_components.gtfs_rt.const import (
    CONF_HEADERS,
    CONF_ROUTE,
    CONF_STOP_ID,
    CONF_TRIP_UPDATE_URL,
    CONF_VEHICLE_POSITION_URL,
    DOMAIN,
    SUBENTRY_TYPE_DEPARTURE,
)

from .common import NOW, TRIP_URL, VEHICLE_URL, standard_feeds


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components/."""


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Freeze time at NOW."""
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
async def new_york(hass: HomeAssistant) -> None:
    """Configure Home Assistant for New York."""
    await hass.config.async_set_time_zone("America/New_York")


@pytest.fixture
def os_timezone_tokyo() -> Generator[None]:
    """Give the OS a different timezone from Home Assistant, as in a container."""
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    yield
    if original is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = original
    time.tzset()


@pytest.fixture
def mock_feeds(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Serve the standard trip and vehicle feeds."""
    trips, vehicles = standard_feeds()
    aioclient_mock.get(TRIP_URL, content=trips)
    aioclient_mock.get(VEHICLE_URL, content=vehicles)
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a feed entry with two departures."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Example Transit",
        data={
            CONF_TRIP_UPDATE_URL: TRIP_URL,
            CONF_VEHICLE_POSITION_URL: VEHICLE_URL,
            CONF_HEADERS: {"x-api-key": "SECRET"},
        },
        subentries_data=[
            ConfigSubentryData(
                data={CONF_STOP_ID: "514", CONF_ROUTE: "100"},
                subentry_type=SUBENTRY_TYPE_DEPARTURE,
                title="Downtown to airport",
                unique_id="100|514",
            ),
            ConfigSubentryData(
                data={CONF_STOP_ID: "514"},
                subentry_type=SUBENTRY_TYPE_DEPARTURE,
                title="Any bus at 514",
                unique_id="|514",
            ),
        ],
    )
