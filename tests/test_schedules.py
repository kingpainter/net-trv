"""Schedule editing: away / frost guard numbers and schedule actions (0.8.0)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.net_trv.const import DOMAIN

pytestmark = pytest.mark.usefixtures("credentials")

STUEN = "climate.stuen"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _number(hass: HomeAssistant, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        NUMBER_DOMAIN, DOMAIN, f"home1-{key}"
    )
    assert entity_id is not None
    return entity_id


def _last(mock_api: AsyncMock, endpoint: str) -> dict[str, Any]:
    calls = [c for c in mock_api.call_args_list if c.kwargs["url"].endswith(endpoint)]
    assert calls, f"no call to {endpoint}"
    return dict(calls[-1].kwargs["params"])


async def test_away_and_frost_guard_numbers(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    away, frost = _number(hass, "away_temp"), _number(hass, "hg_temp")
    assert hass.states.get(away).state == "12.0"
    assert hass.states.get(frost).state == "7.0"
    assert hass.states.get(away).attributes["schedule"] == "Standard"

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: away, ATTR_VALUE: 14},
        blocking=True,
    )

    sent = _last(mock_api, "api/synchomeschedule")
    # The schedule keeps its own name (pyatmo would send "Default").
    assert sent["params"] == {
        "home_id": "home1",
        "schedule_id": "sched1",
        "name": "Standard",
    }
    body = sent["json"]
    assert body["away_temp"] == 14
    assert body["hg_temp"] == 7
    # Zones and timetable go back untouched.
    assert body["zones"][0]["name"] == "Comfort"
    assert body["zones"][0]["rooms"] == [
        {"id": "1001", "therm_setpoint_temperature": 21},
        {"id": "1002", "therm_setpoint_temperature": 22},
    ]
    assert body["timetable"] == [{"zone_id": 0, "m_offset": 0}]
    assert hass.states.get(away).state == "14.0"


async def test_set_schedule_temperature(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        DOMAIN,
        "set_schedule_temperature",
        {ATTR_ENTITY_ID: STUEN, "zone_name": "Comfort", "temperature": 20},
        blocking=True,
    )
    rooms = _last(mock_api, "api/synchomeschedule")["json"]["zones"][0]["rooms"]
    assert rooms[0] == {"id": "1001", "therm_setpoint_temperature": 20.0}
    assert rooms[1]["therm_setpoint_temperature"] == 22  # other room unchanged

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule_temperature",
            {ATTR_ENTITY_ID: STUEN, "zone_name": "Findes ikke", "temperature": 20},
            blocking=True,
        )


async def test_rename_and_create_schedule(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        DOMAIN,
        "rename_schedule",
        {"schedule_name": "Vinter", "new_name": "Vinter 2026"},
        blocking=True,
    )
    sent = _last(mock_api, "api/synchomeschedule")
    assert sent["params"]["schedule_id"] == "sched2"
    assert sent["params"]["name"] == "Vinter 2026"

    await hass.services.async_call(
        DOMAIN,
        "create_schedule",
        {"name": "Ferie", "copy_from": "Vinter"},
        blocking=True,
    )
    sent = _last(mock_api, "api/createnewhomeschedule")
    assert sent["params"] == {"home_id": "home1", "name": "Ferie"}
    assert sent["json"]["zones"][0]["name"] == "Comfort"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "create_schedule", {"name": "Standard"}, blocking=True
        )
