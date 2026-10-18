"""Coordinator that polls one GTFS-realtime feed."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from google.transit import gtfs_realtime_pb2

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HEADERS,
    CONF_TRIP_UPDATE_URL,
    CONF_VEHICLE_POSITION_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .feed import (
    Departure,
    FeedAuthError,
    FeedError,
    async_fetch,
    build_departures,
    parse_feed,
    to_datetime,
)

_LOGGER = logging.getLogger(__name__)

type GtfsRtConfigEntry = ConfigEntry[GtfsRtCoordinator]


@dataclass(frozen=True, slots=True)
class GtfsRtData:
    """Upcoming departures from the latest successful poll."""

    departures: dict[str, list[Departure]]
    feed_timestamp: datetime | None


class GtfsRtCoordinator(DataUpdateCoordinator[GtfsRtData]):
    """Fetch a trip updates feed (and optional vehicle positions feed)."""

    config_entry: GtfsRtConfigEntry | None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: GtfsRtConfigEntry | None,
        *,
        trip_update_url: str,
        vehicle_position_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=config_entry.title if config_entry else DOMAIN,
            update_interval=update_interval,
        )
        self.trip_update_url = trip_update_url
        self.vehicle_position_url = vehicle_position_url
        self._headers = dict(headers or {})
        self._session = async_get_clientsession(hass)
        self.vehicle_feed_error: str | None = None
        # YAML setups keep running when the first poll fails; start empty, not None.
        self.data = GtfsRtData(departures={}, feed_timestamp=None)

    @classmethod
    def from_config_entry(
        cls, hass: HomeAssistant, entry: GtfsRtConfigEntry
    ) -> GtfsRtCoordinator:
        """Create a coordinator for a config entry."""
        return cls(
            hass,
            entry,
            trip_update_url=entry.data[CONF_TRIP_UPDATE_URL],
            vehicle_position_url=entry.data.get(CONF_VEHICLE_POSITION_URL),
            headers=entry.data.get(CONF_HEADERS),
        )

    async def _async_update_data(self) -> GtfsRtData:
        """Fetch the feeds and index upcoming departures."""
        try:
            trip_feed = await self._async_load(self.trip_update_url)
        except FeedAuthError as err:
            raise UpdateFailed(
                f"Trip updates feed rejected the credentials ({err})"
            ) from err
        except FeedError as err:
            raise UpdateFailed(f"Error loading trip updates feed: {err}") from err

        vehicle_feed = None
        if self.vehicle_position_url:
            # Vehicle positions only enrich departures; never fail the update over them.
            try:
                vehicle_feed = await self._async_load(self.vehicle_position_url)
            except FeedError as err:
                if self.vehicle_feed_error is None:
                    _LOGGER.warning(
                        "Vehicle positions feed unavailable, continuing without it: %s",
                        err,
                    )
                self.vehicle_feed_error = str(err)
            else:
                if self.vehicle_feed_error is not None:
                    _LOGGER.info("Vehicle positions feed is available again")
                self.vehicle_feed_error = None

        departures = await self.hass.async_add_executor_job(
            build_departures, trip_feed, vehicle_feed, dt_util.utcnow()
        )
        feed_timestamp = None
        if trip_feed.header.HasField("timestamp"):
            feed_timestamp = to_datetime(trip_feed.header.timestamp)
        return GtfsRtData(departures=departures, feed_timestamp=feed_timestamp)

    async def _async_load(self, url: str) -> gtfs_realtime_pb2.FeedMessage:
        content = await async_fetch(self._session, url, self._headers)
        # Large feeds take a while to decode; keep that off the event loop.
        return await self.hass.async_add_executor_job(parse_feed, content)
