"""Tests for the config flow and departure subentry flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.gtfs_rt.const import (
    CONF_HEADERS,
    CONF_ROUTE,
    CONF_STOP_ID,
    CONF_TRIP_UPDATE_URL,
    CONF_VEHICLE_POSITION_URL,
    DOMAIN,
    SUBENTRY_TYPE_DEPARTURE,
)
from custom_components.gtfs_rt.feed import ACCEPT

from .common import TRIP_URL, VEHICLE_URL, standard_feeds

USER_INPUT = {
    CONF_NAME: "Example Transit",
    CONF_TRIP_UPDATE_URL: TRIP_URL,
    CONF_VEHICLE_POSITION_URL: VEHICLE_URL,
    CONF_HEADERS: {"x-api-key": "SECRET"},
}


def suggested_values(result: dict[str, Any]) -> dict[str, Any]:
    """Return the values a form is pre-filled with."""
    return {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description and "suggested_value" in key.description
    }


async def start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow(hass: HomeAssistant, mock_feeds: AiohttpClientMocker) -> None:
    """A valid feed creates an entry; both feeds are checked with the headers."""
    result = await start_user_flow(hass)

    with patch("custom_components.gtfs_rt.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Example Transit"
    assert result["data"] == {
        CONF_TRIP_UPDATE_URL: TRIP_URL,
        CONF_VEHICLE_POSITION_URL: VEHICLE_URL,
        CONF_HEADERS: {"x-api-key": "SECRET"},
    }
    assert [call[3] for call in mock_feeds.mock_calls] == [
        {"x-api-key": "SECRET", "Accept": ACCEPT}
    ] * 2


async def test_user_flow_minimal(
    hass: HomeAssistant, mock_feeds: AiohttpClientMocker
) -> None:
    """Only the trip updates URL is required; the title defaults to the host."""
    result = await start_user_flow(hass)

    with patch("custom_components.gtfs_rt.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TRIP_UPDATE_URL: f"  {TRIP_URL} "}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "transit.example.com"
    assert result["data"] == {CONF_TRIP_UPDATE_URL: TRIP_URL, CONF_HEADERS: {}}
    assert mock_feeds.call_count == 1


@pytest.mark.parametrize(
    ("trip_response", "vehicle_response", "errors"),
    [
        ({"status": 401}, {}, {CONF_TRIP_UPDATE_URL: "invalid_auth"}),
        ({"status": 404}, {}, {CONF_TRIP_UPDATE_URL: "cannot_connect"}),
        ({"exc": TimeoutError()}, {}, {CONF_TRIP_UPDATE_URL: "cannot_connect"}),
        ({"content": b"PK\x03\x04"}, {}, {CONF_TRIP_UPDATE_URL: "invalid_feed"}),
        ({}, {"status": 403}, {CONF_VEHICLE_POSITION_URL: "invalid_auth"}),
        ({}, {"content": b"<html>"}, {CONF_VEHICLE_POSITION_URL: "invalid_feed"}),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    trip_response: dict[str, Any],
    vehicle_response: dict[str, Any],
    errors: dict[str, str],
) -> None:
    """Feed problems are reported on the offending field, and the user can retry."""
    trips, vehicles = standard_feeds()
    aioclient_mock.get(TRIP_URL, **(trip_response or {"content": trips}))
    aioclient_mock.get(VEHICLE_URL, **(vehicle_response or {"content": vehicles}))
    result = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    aioclient_mock.clear_requests()
    aioclient_mock.get(TRIP_URL, content=trips)
    aioclient_mock.get(VEHICLE_URL, content=vehicles)
    with patch("custom_components.gtfs_rt.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_error_details(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The reason a URL isn't a feed is shown to the user."""
    aioclient_mock.get(TRIP_URL, content=b"PK\x03\x04")
    result = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRIP_UPDATE_URL: TRIP_URL}
    )

    assert "static GTFS" in result["description_placeholders"]["details"]


@pytest.mark.parametrize("headers", ["x-api-key: SECRET", {"nested": {"a": 1}}])
async def test_user_flow_invalid_headers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, headers: Any
) -> None:
    """Headers must be a flat mapping; nothing is fetched otherwise."""
    result = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRIP_UPDATE_URL: TRIP_URL, CONF_HEADERS: headers}
    )

    assert result["errors"] == {CONF_HEADERS: "invalid_headers"}
    assert aioclient_mock.call_count == 0


async def test_user_flow_unknown_error(
    hass: HomeAssistant, mock_feeds: AiohttpClientMocker
) -> None:
    """Unexpected exceptions are logged and reported generically."""
    result = await start_user_flow(hass)

    with patch(
        "custom_components.gtfs_rt.config_flow.parse_feed", side_effect=RuntimeError
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TRIP_UPDATE_URL: TRIP_URL}
        )

    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_duplicate(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The same trip updates URL can't be added twice."""
    config_entry.add_to_hass(hass)
    result = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Reconfiguring updates the entry and reloads it with the new settings."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert suggested_values(result)[CONF_NAME] == "Example Transit"

    mock_feeds.clear_requests()
    trips, _ = standard_feeds()
    mock_feeds.get(TRIP_URL, content=trips)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_TRIP_UPDATE_URL: TRIP_URL, CONF_HEADERS: {"x-api-key": "NEW"}},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.title == "transit.example.com"
    assert config_entry.data == {
        CONF_TRIP_UPDATE_URL: TRIP_URL,
        CONF_HEADERS: {"x-api-key": "NEW"},
    }
    # Validation, then the reload's first refresh, both with the new key.
    assert [call[3]["x-api-key"] for call in mock_feeds.mock_calls] == ["NEW", "NEW"]
    assert len(config_entry.subentries) == 2


async def test_reconfigure_default_name_follows_url(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A name that was derived from the URL isn't pinned when the URL changes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="transit.example.com",
        data={CONF_TRIP_UPDATE_URL: TRIP_URL, CONF_HEADERS: {}},
    )
    entry.add_to_hass(hass)
    trips, _ = standard_feeds()
    aioclient_mock.get("https://new.example.org/tu.pb", content=trips)

    result = await entry.start_reconfigure_flow(hass)
    assert CONF_NAME not in suggested_values(result)
    with patch("custom_components.gtfs_rt.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TRIP_UPDATE_URL: "https://new.example.org/tu.pb"}
        )

    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "new.example.org"


async def test_reconfigure_recovers_entry_waiting_to_retry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Fixing a rejected API key loads the entry right away."""
    aioclient_mock.get(TRIP_URL, status=401)
    aioclient_mock.get(VEHICLE_URL, status=401)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY

    trips, _ = standard_feeds()
    aioclient_mock.clear_requests()
    aioclient_mock.get(TRIP_URL, content=trips)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_TRIP_UPDATE_URL: TRIP_URL, CONF_HEADERS: {"x-api-key": "FIXED"}},
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert config_entry.state is ConfigEntryState.LOADED


async def test_reconfigure_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Validation errors keep the form open with the user's input."""
    config_entry.add_to_hass(hass)
    aioclient_mock.get(TRIP_URL, status=401)
    result = await config_entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRIP_UPDATE_URL: TRIP_URL}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TRIP_UPDATE_URL: "invalid_auth"}


async def test_reconfigure_duplicate(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A feed can't be reconfigured to another entry's URL."""
    config_entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN, data={CONF_TRIP_UPDATE_URL: "https://other.example/tu"}
    )
    other.add_to_hass(hass)
    result = await other.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRIP_UPDATE_URL: TRIP_URL}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def start_departure_flow(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    source: str = SOURCE_USER,
    **context: str,
) -> dict[str, Any]:
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DEPARTURE),
        context={"source": source, **context},
    )


@pytest.mark.parametrize(
    ("user_input", "title", "data"),
    [
        (
            {CONF_NAME: "To work", CONF_STOP_ID: "601", CONF_ROUTE: "100"},
            "To work",
            {CONF_STOP_ID: "601", CONF_ROUTE: "100"},
        ),
        (
            {CONF_STOP_ID: " 601 ", CONF_ROUTE: "100"},
            "100 at 601",
            {CONF_STOP_ID: "601", CONF_ROUTE: "100"},
        ),
        ({CONF_STOP_ID: "601", CONF_ROUTE: ""}, "Stop 601", {CONF_STOP_ID: "601"}),
    ],
)
async def test_add_departure(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    user_input: dict[str, str],
    title: str,
    data: dict[str, str],
) -> None:
    """Adding a departure creates a subentry and its sensor."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await start_departure_flow(hass, config_entry)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(s for s in config_entry.subentries.values() if s.title == title)
    assert dict(subentry.data) == data
    assert len(hass.states.async_entity_ids("sensor")) == 3


async def test_add_departure_duplicate(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The same route and stop can't be added twice to a feed."""
    config_entry.add_to_hass(hass)

    result = await start_departure_flow(hass, config_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STOP_ID: "514", CONF_ROUTE: "100"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_departure(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Reconfiguring a departure updates the subentry and its sensor."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    subentry_id = next(
        s.subentry_id
        for s in config_entry.subentries.values()
        if s.unique_id == "100|514"
    )

    result = await start_departure_flow(
        hass, config_entry, SOURCE_RECONFIGURE, subentry_id=subentry_id
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert suggested_values(result) == {
        CONF_NAME: "Downtown to airport",
        CONF_STOP_ID: "514",
        CONF_ROUTE: "100",
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Route 200", CONF_STOP_ID: "514", CONF_ROUTE: "200"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    subentry = config_entry.subentries[subentry_id]
    assert subentry.title == "Route 200"
    assert subentry.unique_id == "200|514"
    state = hass.states.get("sensor.downtown_to_airport")
    assert state is not None
    assert state.state == "3"


async def test_reconfigure_departure_duplicate(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A departure can't be changed into another existing departure."""
    config_entry.add_to_hass(hass)
    subentry_id = next(
        s.subentry_id
        for s in config_entry.subentries.values()
        if s.unique_id == "100|514"
    )

    result = await start_departure_flow(
        hass, config_entry, SOURCE_RECONFIGURE, subentry_id=subentry_id
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STOP_ID: "514"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_departure_default_name_follows_values(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A generated name is regenerated rather than pinned on reconfigure."""
    config_entry.add_to_hass(hass)
    result = await start_departure_flow(hass, config_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STOP_ID: "601", CONF_ROUTE: "100"}
    )
    subentry_id = next(
        s.subentry_id
        for s in config_entry.subentries.values()
        if s.unique_id == "100|601"
    )

    result = await start_departure_flow(
        hass, config_entry, SOURCE_RECONFIGURE, subentry_id=subentry_id
    )
    assert CONF_NAME not in suggested_values(result)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STOP_ID: "601", CONF_ROUTE: "200"}
    )

    assert config_entry.subentries[subentry_id].title == "200 at 601"
