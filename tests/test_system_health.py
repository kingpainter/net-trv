"""System health info (0.9.1)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    get_system_health_info,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.net_trv.const import DOMAIN
from custom_components.net_trv.system_health import NETATMO_API

pytestmark = pytest.mark.usefixtures("credentials")


async def test_system_health(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.get(NETATMO_API, text="")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert await async_setup_component(hass, "system_health", {})
    await hass.async_block_till_done()

    info = await get_system_health_info(hass, DOMAIN)
    for key, value in info.items():
        if hasattr(value, "__await__"):
            info[key] = await value

    assert info["can_reach_server"] == "ok"
    assert info["webhook"] == "registered"
    assert info["valves_online"] == "2 / 2"
    assert info["api_calls_last_hour"].endswith("/ 500")
    assert info["public_weather"] == "ok"
    assert info["room_history"] == "off"  # no recorder in this test


async def test_system_health_without_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without a loaded entry only the reachability check is shown."""
    aioclient_mock.get(NETATMO_API, text="")
    assert await async_setup_component(hass, DOMAIN, {})
    assert await async_setup_component(hass, "system_health", {})
    await hass.async_block_till_done()

    info = await get_system_health_info(hass, DOMAIN)
    assert list(info) == ["can_reach_server"]
    assert await info["can_reach_server"] == "ok"
