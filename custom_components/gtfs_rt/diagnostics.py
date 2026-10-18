"""Diagnostics support for the GTFS-Realtime integration."""

from __future__ import annotations

from typing import Any

from yarl import URL

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HEADERS,
    CONF_ROUTE,
    CONF_STOP_ID,
    CONF_TRIP_UPDATE_URL,
    CONF_VEHICLE_POSITION_URL,
)
from .coordinator import GtfsRtConfigEntry, GtfsRtData


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GtfsRtConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    vehicle_url = entry.data.get(CONF_VEHICLE_POSITION_URL)
    last_exception = coordinator.last_exception
    return {
        "feed": {
            CONF_TRIP_UPDATE_URL: _redact_url(entry.data[CONF_TRIP_UPDATE_URL]),
            CONF_VEHICLE_POSITION_URL: vehicle_url and _redact_url(vehicle_url),
            CONF_HEADERS: dict.fromkeys(entry.data.get(CONF_HEADERS, {}), REDACTED),
            "last_update_success": coordinator.last_update_success,
            "last_exception": str(last_exception) if last_exception else None,
            "vehicle_feed_error": coordinator.vehicle_feed_error,
            "feed_timestamp": data.feed_timestamp and data.feed_timestamp.isoformat(),
            "stops_with_departures": len(data.departures),
        },
        "departures": [
            _departure_diagnostics(subentry, data)
            for subentry in entry.subentries.values()
        ],
    }


def _departure_diagnostics(
    subentry: ConfigSubentry, data: GtfsRtData
) -> dict[str, Any]:
    """Describe a departure, so users can check its IDs against the feed."""
    at_stop = data.departures.get(subentry.data[CONF_STOP_ID], [])
    return {
        "title": subentry.title,
        "stop_id": subentry.data[CONF_STOP_ID],
        "route_id": subentry.data.get(CONF_ROUTE),
        "departures_at_stop": len(at_stop),
        "routes_at_stop": sorted({departure.route_id for departure in at_stop}),
    }


def _redact_url(url: str) -> str:
    """Keep only the scheme and host; paths and query strings can hold API keys."""
    parsed = URL(url)
    return f"{parsed.scheme}://{parsed.host}/{REDACTED}"
