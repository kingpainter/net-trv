"""Schedule select and the core-parity actions (0.6.0)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.net_trv.const import DOMAIN

pytestmark = pytest.mark.usefixtures("credentials")

STUEN = "climate.stuen"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _schedule_select(hass: HomeAssistant) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        SELECT_DOMAIN, DOMAIN, "home1-schedule"
    )
    assert entity_id is not None
    return entity_id


def _last(mock_api: AsyncMock, endpoint: str) -> dict[str, str]:
    calls = [c for c in mock_api.call_args_list if c.kwargs["url"].endswith(endpoint)]
    assert calls, f"no call to {endpoint}"
    return dict(calls[-1].kwargs["params"])


async def test_schedule_select(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    entity_id = _schedule_select(hass)
    state = hass.states.get(entity_id)
    assert state.state == "Standard"
    assert state.attributes["options"] == ["Standard", "Vinter"]

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "Vinter"},
        blocking=True,
    )

    assert _last(mock_api, "api/switchhomeschedule") == {
        "home_id": "home1",
        "schedule_id": "sched2",
    }
    assert hass.states.get(entity_id).state == "Vinter"


async def test_set_schedule_action(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {ATTR_ENTITY_ID: STUEN, "schedule_name": "Vinter"},
        blocking=True,
    )
    assert _last(mock_api, "api/switchhomeschedule")["schedule_id"] == "sched2"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {ATTR_ENTITY_ID: STUEN, "schedule_name": "Findes ikke"},
            blocking=True,
        )


async def test_set_temperature_with_time_period(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    before = int(dt_util.utcnow().timestamp())

    await hass.services.async_call(
        DOMAIN,
        "set_temperature_with_time_period",
        {ATTR_ENTITY_ID: STUEN, "target_temperature": 22.5, "time_period": "02:00"},
        blocking=True,
    )

    params = _last(mock_api, "api/setroomthermpoint")
    assert params["mode"] == "manual"
    assert params["temp"] == "22.5"
    end = int(params["endtime"])
    assert before + 7200 <= end <= before + 7300
    state = hass.states.get(STUEN)
    assert state.attributes["temperature"] == 22.5
    assert state.attributes["setpoint_end_time"] is not None


async def test_set_temperature_with_end_datetime_in_past(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    past = (dt_util.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_temperature_with_end_datetime",
            {ATTR_ENTITY_ID: STUEN, "target_temperature": 20, "end_datetime": past},
            blocking=True,
        )


async def test_preset_with_end_datetime(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    end = dt_util.now() + timedelta(days=2)
    end_text = end.strftime("%Y-%m-%d %H:%M:%S")

    # Away: home-wide, with end time.
    await hass.services.async_call(
        DOMAIN,
        "set_preset_mode_with_end_datetime",
        {ATTR_ENTITY_ID: STUEN, "preset_mode": "away", "end_datetime": end_text},
        blocking=True,
    )
    params = _last(mock_api, "api/setthermmode")
    assert params["mode"] == "away"
    assert abs(int(params["endtime"]) - int(end.timestamp())) <= 1
    assert hass.states.get(STUEN).attributes["preset_mode"] == "away"

    # Boost: this room only, with end time.
    await hass.services.async_call(
        DOMAIN,
        "set_preset_mode_with_end_datetime",
        {ATTR_ENTITY_ID: STUEN, "preset_mode": "boost", "end_datetime": end_text},
        blocking=True,
    )
    params = _last(mock_api, "api/setroomthermpoint")
    assert params["mode"] == "max"
    assert "endtime" in params


async def test_clear_temperature_setting(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        DOMAIN, "clear_temperature_setting", {ATTR_ENTITY_ID: STUEN}, blocking=True
    )
    assert _last(mock_api, "api/setroomthermpoint")["mode"] == "home"


async def test_webhook_actions(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    webhook = config_entry.runtime_data.webhook
    assert webhook.registered

    with patch(
        "custom_components.net_trv.api.NetatmoEnergyAuth.async_dropwebhook"
    ) as drop:
        await hass.services.async_call(DOMAIN, "unregister_webhook", blocking=True)
    drop.assert_awaited_once()
    assert not webhook.registered

    await hass.services.async_call(DOMAIN, "register_webhook", blocking=True)
    assert webhook.registered
    assert webhook.last_error is None
