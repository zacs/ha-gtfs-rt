"""Config flow for the GTFS-Realtime integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol
from yarl import URL

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    ObjectSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_HEADERS,
    CONF_ROUTE,
    CONF_STOP_ID,
    CONF_TRIP_UPDATE_URL,
    CONF_VEHICLE_POSITION_URL,
    DOMAIN,
    SUBENTRY_TYPE_DEPARTURE,
)
from .feed import (
    FeedAuthError,
    FeedFetchError,
    InvalidFeedError,
    async_fetch,
    parse_feed,
)

_LOGGER = logging.getLogger(__name__)

_URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))

FEED_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME): TextSelector(),
        vol.Required(CONF_TRIP_UPDATE_URL): _URL_SELECTOR,
        vol.Optional(CONF_VEHICLE_POSITION_URL): _URL_SELECTOR,
        vol.Optional(CONF_HEADERS): ObjectSelector(),
    }
)

DEPARTURE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME): TextSelector(),
        vol.Required(CONF_STOP_ID): TextSelector(),
        vol.Optional(CONF_ROUTE): TextSelector(),
    }
)


class GtfsRtConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a GTFS-realtime feed."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this integration supports."""
        return {SUBENTRY_TYPE_DEPARTURE: DepartureSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a feed."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            self._async_abort_entries_match(
                {CONF_TRIP_UPDATE_URL: user_input[CONF_TRIP_UPDATE_URL].strip()}
            )
            data, errors, placeholders = await _async_validate_feed(
                self.hass, user_input
            )
            if not errors:
                return self.async_create_entry(title=_feed_title(user_input), data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(FEED_SCHEMA, user_input),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change a feed's URLs or headers, for example to rotate an API key."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_TRIP_UPDATE_URL].strip()
            if any(
                other.entry_id != entry.entry_id
                and other.data.get(CONF_TRIP_UPDATE_URL) == url
                for other in self._async_current_entries(include_ignore=False)
            ):
                return self.async_abort(reason="already_configured")
            data, errors, placeholders = await _async_validate_feed(
                self.hass, user_input
            )
            if not errors:
                # The entry's update listener reloads it. (ConfigFlow only gained
                # async_update_and_abort after the minimum supported release.)
                self.hass.config_entries.async_update_entry(
                    entry, title=_feed_title(user_input), data=data
                )
                return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                FEED_SCHEMA, user_input or {**entry.data, CONF_NAME: entry.title}
            ),
            errors=errors,
            description_placeholders=placeholders,
        )


class DepartureSubentryFlow(ConfigSubentryFlow):
    """Add or change a departure (a stop, optionally limited to one route)."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a departure."""
        if user_input is not None:
            title, data, unique_id = _departure(user_input)
            if self._unique_id_in_use(unique_id):
                return self.async_abort(reason="already_configured")
            return self.async_create_entry(title=title, data=data, unique_id=unique_id)

        return self.async_show_form(step_id="user", data_schema=DEPARTURE_SCHEMA)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a departure."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            title, data, unique_id = _departure(user_input)
            if self._unique_id_in_use(unique_id, ignore=subentry.subentry_id):
                return self.async_abort(reason="already_configured")
            # The entry's update listener reloads it.
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=title,
                data=data,
                unique_id=unique_id,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                DEPARTURE_SCHEMA, {**subentry.data, CONF_NAME: subentry.title}
            ),
        )

    def _unique_id_in_use(self, unique_id: str, ignore: str | None = None) -> bool:
        return any(
            subentry.unique_id == unique_id and subentry.subentry_id != ignore
            for subentry in self._get_entry().subentries.values()
        )


async def _async_validate_feed(
    hass: HomeAssistant, user_input: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Fetch and parse each feed; return entry data, errors and placeholders."""
    headers = user_input.get(CONF_HEADERS) or {}
    if not isinstance(headers, Mapping) or any(
        isinstance(value, (Mapping, list)) for value in headers.values()
    ):
        return {}, {CONF_HEADERS: "invalid_headers"}, {}

    data: dict[str, Any] = {
        CONF_TRIP_UPDATE_URL: user_input[CONF_TRIP_UPDATE_URL].strip(),
        CONF_HEADERS: {str(key): str(value) for key, value in headers.items()},
    }
    if vehicle_url := (user_input.get(CONF_VEHICLE_POSITION_URL) or "").strip():
        data[CONF_VEHICLE_POSITION_URL] = vehicle_url

    errors: dict[str, str] = {}
    placeholders: dict[str, str] = {}
    session = async_get_clientsession(hass)
    for key in (CONF_TRIP_UPDATE_URL, CONF_VEHICLE_POSITION_URL):
        if key not in data:
            continue
        try:
            content = await async_fetch(session, data[key], data[CONF_HEADERS])
            await hass.async_add_executor_job(parse_feed, content)
        except FeedAuthError:
            errors[key] = "invalid_auth"
        except FeedFetchError as err:
            errors[key] = "cannot_connect"
            placeholders["details"] = str(err)
        except InvalidFeedError as err:
            errors[key] = "invalid_feed"
            placeholders["details"] = str(err)
        except Exception:
            _LOGGER.exception("Unexpected error validating %s", key)
            errors["base"] = "unknown"
    return data, errors, placeholders


def _feed_title(user_input: Mapping[str, Any]) -> str:
    if name := (user_input.get(CONF_NAME) or "").strip():
        return name
    return URL(user_input[CONF_TRIP_UPDATE_URL].strip()).host or "GTFS-Realtime"


def _departure(user_input: Mapping[str, Any]) -> tuple[str, dict[str, str], str]:
    """Return the title, data and unique ID for a departure subentry."""
    stop_id = user_input[CONF_STOP_ID].strip()
    route = (user_input.get(CONF_ROUTE) or "").strip()
    data = {CONF_STOP_ID: stop_id}
    if route:
        data[CONF_ROUTE] = route
    default_title = f"{route} at {stop_id}" if route else f"Stop {stop_id}"
    title = (user_input.get(CONF_NAME) or "").strip() or default_title
    return title, data, f"{route}|{stop_id}"
