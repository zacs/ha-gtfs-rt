"""Departure sensors for GTFS-realtime feeds."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_API_KEY,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_UNIQUE_ID,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    AddEntitiesCallback,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DEPARTURES,
    ATTR_DUE_AT,
    ATTR_DUE_IN,
    ATTR_NEXT_OCCUPANCY,
    ATTR_NEXT_UP,
    ATTR_NEXT_UP_DUE_IN,
    ATTR_OCCUPANCY,
    ATTR_ROUTE,
    ATTR_STOP_ID,
    CONF_APIKEY,
    CONF_DEPARTURES,
    CONF_HEADERS,
    CONF_ROUTE,
    CONF_STOP_ID,
    CONF_TRIP_UPDATE_URL,
    CONF_VEHICLE_POSITION_URL,
    CONF_X_API_KEY,
    COUNTDOWN_INTERVAL,
    DEFAULT_ICON,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_LISTED_DEPARTURES,
    SUBENTRY_TYPE_DEPARTURE,
    TIME_STR_FORMAT,
)
from .coordinator import GtfsRtConfigEntry, GtfsRtCoordinator
from .feed import Departure

PARALLEL_UPDATES = 0

PLATFORM_SCHEMA = SENSOR_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_TRIP_UPDATE_URL): cv.string,
        vol.Exclusive(CONF_API_KEY, "headers"): cv.string,
        vol.Exclusive(CONF_X_API_KEY, "headers"): cv.string,
        vol.Exclusive(CONF_APIKEY, "headers"): cv.string,
        vol.Exclusive(CONF_HEADERS, "headers"): {cv.string: cv.string},
        vol.Optional(CONF_VEHICLE_POSITION_URL): cv.string,
        vol.Optional(CONF_DEPARTURES, default=[]): [
            {
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
                vol.Required(CONF_STOP_ID): cv.string,
                vol.Optional(CONF_ROUTE): cv.string,
                vol.Optional(CONF_UNIQUE_ID): cv.string,
            }
        ],
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up sensors from a YAML `sensor: - platform: gtfs_rt` block."""
    coordinator = GtfsRtCoordinator(
        hass,
        None,
        trip_update_url=config[CONF_TRIP_UPDATE_URL],
        vehicle_position_url=config.get(CONF_VEHICLE_POSITION_URL),
        headers=_yaml_headers(config),
        update_interval=max(
            config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            DEFAULT_SCAN_INTERVAL,
        ),
    )
    await coordinator.async_register_shutdown()
    # A failed first poll leaves the entities unavailable until the feed recovers.
    await coordinator.async_refresh()
    async_add_entities(
        GtfsRtDepartureSensor(
            coordinator,
            departure[CONF_STOP_ID],
            departure.get(CONF_ROUTE),
            unique_id=departure.get(CONF_UNIQUE_ID),
            name=departure[CONF_NAME],
        )
        for departure in config[CONF_DEPARTURES]
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GtfsRtConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one sensor per departure subentry."""
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_DEPARTURE:
            continue
        async_add_entities(
            [
                GtfsRtDepartureSensor(
                    entry.runtime_data,
                    subentry.data[CONF_STOP_ID],
                    subentry.data.get(CONF_ROUTE),
                    unique_id=subentry_id,
                    device_info=DeviceInfo(
                        identifiers={(DOMAIN, subentry_id)},
                        name=subentry.title,
                        entry_type=DeviceEntryType.SERVICE,
                    ),
                )
            ],
            config_subentry_id=subentry_id,
        )


def _yaml_headers(config: ConfigType) -> dict[str, str]:
    """Merge the legacy auth shortcuts into a single header mapping."""
    headers = dict(config.get(CONF_HEADERS, {}))
    if (api_key := config.get(CONF_API_KEY)) is not None:
        headers["Authorization"] = api_key
    elif (apikey := config.get(CONF_APIKEY)) is not None:
        headers["apikey"] = apikey
    elif (x_api_key := config.get(CONF_X_API_KEY)) is not None:
        headers["x-api-key"] = x_api_key
    return headers


class GtfsRtDepartureSensor(CoordinatorEntity[GtfsRtCoordinator], SensorEntity):
    """Minutes until the next departure from a stop, optionally for one route."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_translation_key = "departure"
    _unrecorded_attributes = frozenset({ATTR_DEPARTURES})

    def __init__(
        self,
        coordinator: GtfsRtCoordinator,
        stop_id: str,
        route_id: str | None,
        *,
        unique_id: str | None,
        name: str | None = None,
        device_info: DeviceInfo | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._stop_id = stop_id
        self._route_id = route_id
        self._attr_unique_id = unique_id
        if device_info is None:
            # YAML entities keep their 1.x name and icon so entity IDs and
            # dashboards are unchanged. Unregistered entities can't use icon
            # translations, so the icon is set directly.
            self._attr_name = name
            self._attr_icon = DEFAULT_ICON
        else:
            self._attr_has_entity_name = True
            self._attr_name = None
            self._attr_device_info = device_info
        self._update_from_data()

    async def async_added_to_hass(self) -> None:
        """Keep the countdown moving between feed polls."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_count_down,
                COUNTDOWN_INTERVAL,
                cancel_on_shutdown=True,
            )
        )

    @callback
    def _async_count_down(self, _now: datetime) -> None:
        self._update_from_data()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_from_data()
        super()._handle_coordinator_update()

    def _update_from_data(self) -> None:
        now = dt_util.utcnow()
        upcoming = self._upcoming(now)
        self._attr_native_value = (
            _minutes_until(upcoming[0].time, now) if upcoming else None
        )

        attrs: dict[str, Any] = {
            ATTR_DUE_IN: self._attr_native_value if upcoming else "-",
            ATTR_STOP_ID: self._stop_id,
            ATTR_ROUTE: self._route_id,
        }
        if upcoming:
            first = upcoming[0]
            attrs[ATTR_DUE_AT] = _local_time(first.time)
            attrs[ATTR_OCCUPANCY] = first.occupancy
            if first.latitude is not None and first.longitude is not None:
                attrs[ATTR_LATITUDE] = first.latitude
                attrs[ATTR_LONGITUDE] = first.longitude
        if len(upcoming) > 1:
            second = upcoming[1]
            attrs[ATTR_NEXT_UP] = _local_time(second.time)
            attrs[ATTR_NEXT_UP_DUE_IN] = _minutes_until(second.time, now)
            attrs[ATTR_NEXT_OCCUPANCY] = second.occupancy
        attrs[ATTR_DEPARTURES] = [
            {
                "route_id": departure.route_id,
                "trip_id": departure.trip_id,
                "vehicle_id": departure.vehicle_id,
                "time": dt_util.as_local(departure.time).isoformat(),
                "due_in": _minutes_until(departure.time, now),
                "occupancy": departure.occupancy,
            }
            for departure in upcoming[:MAX_LISTED_DEPARTURES]
        ]
        self._attr_extra_state_attributes = attrs

    def _upcoming(self, now: datetime) -> list[Departure]:
        return [
            departure
            for departure in self.coordinator.data.departures.get(self._stop_id, ())
            if departure.time > now
            and (self._route_id is None or departure.route_id == self._route_id)
        ]


def _minutes_until(when: datetime, now: datetime) -> int:
    return int((when - now).total_seconds() / 60)


def _local_time(when: datetime) -> str:
    return dt_util.as_local(when).strftime(TIME_STR_FORMAT)
