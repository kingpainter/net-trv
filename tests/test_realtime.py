"""Phase 2: webhook events update entities without waiting for a poll.

Payload shapes are taken from a real NAPlug + NRV home (diagnostics,
2026-11-10), re-pointed at the test fixture's home, rooms and valves.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.components.climate import ATTR_HVAC_ACTION, HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.netatmo_energy.realtime import (
    EventFilter,
    normalize_payload,
    valve_position,
)

from .conftest import CLIENT_SECRET

pytestmark = pytest.mark.usefixtures("credentials")

STUEN = "climate.stuen"


def _device_event(**extra: Any) -> dict[str, Any]:
    return {
        "push_type": "device_event",
        "user_id": "u",
        "device_id": "70:ee:50:00:00:01",
        "home_id": "home1",
        "home_name": "Hjem",
        "extra_params": {"device_type": "NAPlug", **extra},
    }


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return str(entry.data[CONF_WEBHOOK_ID])


async def _push(client: Any, webhook_id: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode()
    sig = hmac.new(CLIENT_SECRET.encode(), body, hashlib.sha256).hexdigest()
    resp = await client.post(
        f"/api/webhook/{webhook_id}",
        data=body,
        headers={"Content-Type": "application/json", "X-Netatmo-secret": sig},
    )
    assert resp.status == 200


# --- pure helpers -------------------------------------------------------------


def test_normalize_nested_home_event_changed() -> None:
    nested = {
        "push_type": "home_event_changed",
        "home_id": "home1",
        "extra_params": {
            "event_type": "therm_mode",
            "home": {"id": "home1", "therm_mode": "away"},
            "mode": "away",
        },
    }
    flat = normalize_payload(nested)
    assert flat["event_type"] == "therm_mode"
    assert flat["home"] == {"id": "home1", "therm_mode": "away"}
    # Already flat payloads are untouched.
    assert normalize_payload(flat) is flat


def test_event_filter_drops_repeats_and_older_events() -> None:
    f = EventFilter()
    new = _device_event(
        event_type="temperature_variation_event",
        room_id="1001",
        temperature=21.3,
        ts=200,
    )
    old = _device_event(
        event_type="temperature_variation_event",
        room_id="1001",
        temperature=20.0,
        ts=100,
    )
    assert f.accept(new)
    assert not f.accept(new)  # exact repeat
    assert not f.accept(old)  # older than what we already applied
    other_room = _device_event(
        event_type="temperature_variation_event",
        room_id="1002",
        temperature=20.0,
        ts=100,
    )
    assert f.accept(other_room)


def test_valve_position_parsing() -> None:
    payload = _device_event(
        event_type="motor_data_event",
        module_id="09:00:00:00:00:01",
        current_position=1708,
        motor_cmd=200,
        ts=5,
    )
    module_id, report = valve_position(payload)
    assert module_id == "09:00:00:00:00:01"
    assert (report.position, report.motor_cmd, report.ts) == (1708, 200, 5)
    assert valve_position({"push_type": "display_change"}) is None


# --- end to end through the webhook -------------------------------------------


async def test_events_update_entities_without_polling(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_client_no_auth: Any,
) -> None:
    webhook_id = await _setup(hass, config_entry)
    client = await hass_client_no_auth()
    mock_api.reset_mock()

    # Measured temperature, 0.1 C resolution.
    await _push(
        client,
        webhook_id,
        _device_event(
            event_type="temperature_variation_event",
            mode="home",
            room_id="1001",
            setpoint=21,
            temperature=21.3,
            ts=1794334404,
        ),
    )
    # Heating demand stops in a room that was heating (fixture: Stuen at 40 %).
    await _push(
        client,
        webhook_id,
        _device_event(
            event_type="heating_power_request_event",
            heating_power_request=0,
            room_id="1001",
            ts=1794334405,
        ),
    )
    # Setpoint changes arrive as display_change (flat, home.rooms list).
    await _push(
        client,
        webhook_id,
        {
            "push_type": "display_change",
            "user_id": "u",
            "home_id": "home1",
            "device_id": "70:ee:50:00:00:01",
            "event_type": "setpoint_event",
            "type": "setpoint_event",
            "room_id": "1001",
            "mode": "manual",
            "temperature": 22.5,
            "home": {
                "id": "home1",
                "rooms": [
                    {
                        "id": "1001",
                        "therm_setpoint_mode": "manual",
                        "therm_setpoint_temperature": 22.5,
                        "therm_setpoint_start_time": 1794334122,
                        "therm_setpoint_end_time": 1794339222,
                    }
                ],
            },
        },
    )
    # Valve motor position (webhook only).
    await _push(
        client,
        webhook_id,
        _device_event(
            event_type="motor_data_event",
            module_id="09:00:00:00:00:01",
            current_position=1708,
            motor_cmd=200,
            ts=1794334406,
        ),
    )
    await hass.async_block_till_done()

    stuen = hass.states.get(STUEN)
    assert stuen.attributes["current_temperature"] == 21.3
    assert stuen.attributes[ATTR_TEMPERATURE] == 22.5
    assert stuen.state == HVACMode.HEAT
    assert stuen.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE
    position = hass.states.get("sensor.stuen_valve_position")
    assert position.state == "1708"
    assert position.attributes["motor_cmd"] == 200

    # None of this needed an API call.
    assert not [c for c in mock_api.call_args_list if "homestatus" in c.kwargs["url"]]
    webhook_state = hass.states.get("binary_sensor.gang_relay_webhook")
    assert webhook_state.attributes["events_applied"] == 4


async def test_out_of_order_event_is_ignored(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_client_no_auth: Any,
) -> None:
    webhook_id = await _setup(hass, config_entry)
    client = await hass_client_no_auth()

    for temperature, ts in ((21.3, 200), (19.0, 100)):
        await _push(
            client,
            webhook_id,
            _device_event(
                event_type="temperature_variation_event",
                room_id="1001",
                temperature=temperature,
                ts=ts,
            ),
        )
    await hass.async_block_till_done()

    assert hass.states.get(STUEN).attributes["current_temperature"] == 21.3
    attrs = hass.states.get("binary_sensor.gang_relay_webhook").attributes
    assert attrs["events_ignored"] == 1


async def test_nested_home_mode_event_updates_home(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_client_no_auth: Any,
) -> None:
    webhook_id = await _setup(hass, config_entry)
    client = await hass_client_no_auth()

    await _push(
        client,
        webhook_id,
        {
            "push_type": "home_event_changed",
            "user_id": "u",
            "home_id": "home1",
            "extra_params": {
                "event_type": "therm_mode",
                "home": {"id": "home1", "therm_mode": "away"},
                "home_id": "home1",
                "mode": "away",
                "previous_mode": "schedule",
            },
        },
    )
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    assert coordinator.account.homes["home1"].therm_mode == "away"


async def test_fresh_webhook_slows_polling(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_client_no_auth: Any,
) -> None:
    webhook_id = await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=60)

    client = await hass_client_no_auth()
    await _push(
        client,
        webhook_id,
        _device_event(
            event_type="heating_power_request_event",
            heating_power_request=0,
            room_id="1001",
            ts=1,
        ),
    )
    await hass.async_block_till_done()

    assert coordinator.update_interval == timedelta(seconds=300)
