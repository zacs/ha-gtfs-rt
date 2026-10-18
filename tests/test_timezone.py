"""Regression test for issue #23: OS and Home Assistant timezones differ.

0.1.0 built arrival times in the OS timezone and compared them with Home
Assistant's local time, so "Due in" was off by hours in containers. freezegun
ignores the OS timezone for naive local times, so this test uses the real clock.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .common import TRIP_URL, make_feed


@pytest.fixture
def frozen_time() -> None:
    """Use the real clock instead of the frozen one from conftest."""


@pytest.mark.usefixtures("new_york", "os_timezone_tokyo")
async def test_os_timezone_does_not_shift_times(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Times are correct when the OS is in Tokyo and Home Assistant in New York."""
    arrival = dt_util.utcnow().replace(microsecond=0) + timedelta(minutes=5, seconds=30)
    feed = make_feed()
    trip = feed.entity.add(id="t").trip_update
    trip.trip.trip_id = "t"
    trip.trip.route_id = "100"
    trip.stop_time_update.add(stop_id="514").arrival.time = int(arrival.timestamp())
    aioclient_mock.get(TRIP_URL, content=feed.SerializeToString())

    assert await async_setup_component(
        hass,
        "sensor",
        {
            "sensor": {
                "platform": "gtfs_rt",
                "trip_update_url": TRIP_URL,
                "departures": [{"name": "Next", "route": "100", "stopid": "514"}],
            }
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("sensor.next")
    assert state is not None
    assert state.state == "5"
    assert state.attributes["Due at"] == arrival.astimezone(
        ZoneInfo("America/New_York")
    ).strftime("%H:%M")
