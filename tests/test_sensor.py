"""Tests for the NET-TRV sensor and binary_sensor platforms."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import conftest

pytestmark = pytest.mark.usefixtures("credentials")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _patched_fixtures(edit: Any) -> Any:
    """Return a load_fixture replacement that lets a test edit the payloads."""
    original = conftest.load_fixture

    def _load(name: str) -> dict[str, Any]:
        data = copy.deepcopy(original(name))
        edit(name, data)
        return data

    return patch.object(conftest, "load_fixture", _load)


async def test_valve_and_relay_sensors(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)

    assert hass.states.get("sensor.stuen_battery").state == "90"
    assert hass.states.get("sensor.stuen_battery_state").state == "full"
    assert hass.states.get("sensor.stuen_radio_signal").state == "-51"
    assert hass.states.get("sensor.stuen_firmware").state == "79"
    assert hass.states.get("binary_sensor.stuen_connectivity").state == STATE_ON
    assert hass.states.get("sensor.bad_battery_state").state == "medium"

    assert hass.states.get("sensor.gang_relay_wi_fi_signal").state == "-42"
    assert hass.states.get("sensor.gang_relay_firmware").state == "174"
    assert hass.states.get("binary_sensor.gang_relay_connectivity").state == STATE_ON
    online = hass.states.get("sensor.gang_relay_valves_online")
    assert online.state == "2"
    assert online.attributes["total"] == 2
    assert online.attributes["offline"] == []


async def test_unreachable_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Battery keeps its last value; live signal goes unavailable."""

    def edit(name: str, data: dict[str, Any]) -> None:
        if name == "homestatus.json":
            for module in data["body"]["home"]["modules"]:
                if module["id"] == "09:00:00:00:00:02":
                    module["reachable"] = False

    with _patched_fixtures(edit):
        await _setup(hass, config_entry)

    assert hass.states.get("binary_sensor.bad_connectivity").state == STATE_OFF
    assert hass.states.get("sensor.bad_battery_state").state == "medium"
    assert hass.states.get("sensor.bad_radio_signal").state == STATE_UNAVAILABLE
    online = hass.states.get("sensor.gang_relay_valves_online")
    assert online.state == "1"
    assert online.attributes["offline"] == ["Valve Bad"]


async def test_room_with_two_valves_names_each_valve(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    def edit(name: str, data: dict[str, Any]) -> None:
        home = (
            data["body"]["homes"][0]
            if name == "homesdata.json"
            else data["body"]["home"]
        )
        if name == "homesdata.json":
            home["rooms"][0]["module_ids"].append("09:00:00:00:00:03")
            home["modules"].append(
                {
                    "id": "09:00:00:00:00:03",
                    "type": "NRV",
                    "name": "Valve Stuen 2",
                    "room_id": "1001",
                    "bridge": "70:ee:50:00:00:01",
                }
            )
        else:
            home["modules"].append(
                {
                    "id": "09:00:00:00:00:03",
                    "type": "NRV",
                    "reachable": True,
                    "battery_state": "low",
                    "rf_strength": 70,
                    "firmware_revision": 79,
                    "bridge": "70:ee:50:00:00:01",
                }
            )

    with _patched_fixtures(edit):
        await _setup(hass, config_entry)

    assert hass.states.get("sensor.stuen_battery_state_valve_stuen").state == "full"
    assert hass.states.get("sensor.stuen_battery_state_valve_stuen_2").state == "low"
    assert hass.states.get("climate.stuen") is not None
