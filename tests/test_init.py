"""Tests for config entry setup, entities and unloading."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.gtfs_rt.const import DOMAIN

from .common import TRIP_URL, VEHICLE_URL


async def test_setup_creates_a_sensor_per_departure(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Each departure subentry gets a named service device with one sensor."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    states = {state.entity_id: state for state in hass.states.async_all("sensor")}
    assert set(states) == {"sensor.downtown_to_airport", "sensor.any_bus_at_514"}
    assert states["sensor.downtown_to_airport"].state == "5"
    assert states["sensor.any_bus_at_514"].state == "3"
    assert states["sensor.downtown_to_airport"].attributes["friendly_name"] == (
        "Downtown to airport"
    )

    for subentry_id, subentry in config_entry.subentries.items():
        entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, subentry_id)
        assert entity_id is not None
        entity = entity_registry.async_get(entity_id)
        assert entity is not None
        assert entity.config_subentry_id == subentry_id
        assert entity.device_id is not None
        device = device_registry.async_get(entity.device_id)
        assert device is not None
        assert device.name == subentry.title
        assert device.identifiers == {(DOMAIN, subentry_id)}
        assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_feed_down_at_startup_retries(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Setup is retried until the feed responds."""
    aioclient_mock.get(TRIP_URL, status=503)
    aioclient_mock.get(VEHICLE_URL, status=503)
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_failure_message(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Rejected credentials are called out in the setup error."""
    aioclient_mock.get(TRIP_URL, status=401)
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert "rejected the credentials" in str(config_entry.reason)


async def test_unload_and_remove_departure(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Removing a departure removes its sensor; unloading stops the feed."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    subentry_id = next(
        s.subentry_id for s in config_entry.subentries.values() if s.unique_id == "|514"
    )

    hass.config_entries.async_remove_subentry(config_entry, subentry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.any_bus_at_514") is None
    assert entity_registry.async_get_entity_id("sensor", DOMAIN, subentry_id) is None
    assert hass.states.get("sensor.downtown_to_airport") is not None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_unknown_subentry_types_are_ignored(
    hass: HomeAssistant, mock_feeds: AiohttpClientMocker
) -> None:
    """Only departure subentries create sensors."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"trip_update_url": TRIP_URL, "headers": {}},
        subentries_data=[
            ConfigSubentryData(
                data={}, subentry_type="future_type", title="Future", unique_id=None
            )
        ],
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.async_entity_ids("sensor") == []
