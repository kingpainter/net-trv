"""Tests for the NET-TRV climate platform."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.netatmo_energy.climate import resolve_action, resolve_state
from custom_components.netatmo_energy.const import DOMAIN

pytestmark = pytest.mark.usefixtures("credentials")

STUEN = "climate.stuen"
BAD = "climate.bad"


@pytest.mark.parametrize(
    ("room_mode", "home_mode", "target", "expected"),
    [
        ("schedule", "schedule", 21, (HVACMode.AUTO, "schedule")),
        ("home", "schedule", 21, (HVACMode.AUTO, "schedule")),
        ("schedule", "away", 12, (HVACMode.AUTO, "away")),
        ("away", None, 12, (HVACMode.AUTO, "away")),
        ("hg", None, 7, (HVACMode.AUTO, "frost_guard")),
        ("manual", "schedule", 21, (HVACMode.HEAT, None)),
        ("manual", "schedule", 7, (HVACMode.OFF, None)),
        ("max", "schedule", 30, (HVACMode.HEAT, "boost")),
        ("off", "schedule", None, (HVACMode.OFF, None)),
        (None, None, None, (HVACMode.AUTO, "schedule")),
    ],
)
def test_resolve_state(room_mode, home_mode, target, expected) -> None:
    assert resolve_state(room_mode, home_mode, target) == expected


def test_resolve_action() -> None:
    assert resolve_action(HVACMode.AUTO, 40, False) == HVACAction.HEATING
    assert resolve_action(HVACMode.AUTO, 0, False) == HVACAction.IDLE
    assert resolve_action(HVACMode.AUTO, 40, True) == HVACAction.IDLE
    assert resolve_action(HVACMode.OFF, 40, False) == HVACAction.OFF


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_rooms_become_climate_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)

    stuen = hass.states.get(STUEN)
    assert stuen is not None
    assert stuen.state == HVACMode.AUTO
    assert stuen.attributes[ATTR_PRESET_MODE] == "schedule"
    assert stuen.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert stuen.attributes["current_temperature"] == 20.5
    assert stuen.attributes[ATTR_TEMPERATURE] == 21

    bad = hass.states.get(BAD)
    assert bad is not None
    assert bad.state == HVACMode.OFF


async def test_device_tree(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    registry = dr.async_get(hass)
    areas = ar.async_get(hass)

    def device(identifier: str) -> dr.DeviceEntry | None:
        # async_get_device is deprecated since HA 2026.10; identifiers are only
        # unique per config entry now.
        found = registry.async_get_devices(
            identifiers={(DOMAIN, identifier)}, config_entry_id=config_entry.entry_id
        )
        assert len(found) <= 1
        return found[0] if found else None

    relay = device("70:ee:50:00:00:01")
    room = device("home1-1001")
    assert relay is not None
    assert room is not None

    # Room device carries the valve: serial, firmware, model, parent relay.
    assert room.via_device_id == relay.id
    assert room.serial_number == "09:00:00:00:00:01"
    assert room.sw_version == "79"
    assert room.model == "Smart radiator valve"
    assert room.area_id is None

    # Relay lands in the room it sits in; no separate valve devices; no
    # device for a room without a valve.
    assert areas.async_get_area(relay.area_id).name == "Gang"
    assert device("09:00:00:00:00:01") is None
    assert device("home1-1003") is None
    assert hass.states.get("climate.gang") is None
    assert len(dr.async_entries_for_config_entry(registry, config_entry.entry_id)) == 3


async def test_stale_devices_are_removed(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Valve devices and valve-less rooms from 0.1.0/0.1.1 are cleaned up."""
    config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    for identifier in ("09:00:00:00:00:01", "home1-1003"):
        registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, identifier)},
        )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    remaining = {
        ident
        for device in dr.async_entries_for_config_entry(registry, config_entry.entry_id)
        for _, ident in device.identifiers
    }
    assert remaining == {"70:ee:50:00:00:01", "home1-1001", "home1-1002"}


async def test_set_temperature_calls_setroomthermpoint(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    mock_api.reset_mock()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: STUEN, ATTR_TEMPERATURE: 21.5},
        blocking=True,
    )

    url = mock_api.call_args.kwargs["url"]
    params = mock_api.call_args.kwargs["params"]
    assert url.endswith("api/setroomthermpoint")
    assert params["room_id"] == "1001"
    assert params["mode"] == "manual"
    assert params["temp"] == "21.5"
    state = hass.states.get(STUEN)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 21.5


async def test_hvac_off_sets_minimum(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    mock_api.reset_mock()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: STUEN, "hvac_mode": HVACMode.OFF},
        blocking=True,
    )

    params = mock_api.call_args.kwargs["params"]
    assert params["mode"] == "manual"
    assert params["temp"] == "7.0"
    assert hass.states.get(STUEN).state == HVACMode.OFF


async def test_away_preset_is_home_wide(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    mock_api.reset_mock()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: STUEN, ATTR_PRESET_MODE: "away"},
        blocking=True,
    )

    url = mock_api.call_args.kwargs["url"]
    params = mock_api.call_args.kwargs["params"]
    assert url.endswith("api/setthermmode")
    assert params == {"home_id": "home1", "mode": "away"}
