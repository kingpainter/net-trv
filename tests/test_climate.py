"""Tests for the Netatmo Energy climate platform."""

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
from homeassistant.helpers import device_registry as dr
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

    relay = registry.async_get_device(identifiers={(DOMAIN, "70:ee:50:00:00:01")})
    valve = registry.async_get_device(identifiers={(DOMAIN, "09:00:00:00:00:01")})
    room = registry.async_get_device(identifiers={(DOMAIN, "home1-1001")})
    assert relay is not None
    assert valve is not None
    assert room is not None
    assert valve.via_device_id == relay.id
    assert room.via_device_id == relay.id
    assert valve.sw_version == "79"


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
