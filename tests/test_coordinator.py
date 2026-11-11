"""Tests for the NET-TRV coordinator: error mapping, outages, webhook merge."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientResponseError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pyatmo.exceptions import (
    ApiError,
    ApiHomeReachabilityError,
    ApiThrottlingError,
)
from pyatmo.webhook import RefreshScope
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.net_trv.const import (
    CONFIRM_DELAY_SECONDS,
    STATUS_INTERVAL,
    THROTTLED_INTERVAL,
)
from custom_components.net_trv.coordinator import NetatmoEnergyCoordinator

pytestmark = pytest.mark.usefixtures("credentials", "mock_api")


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry
) -> NetatmoEnergyCoordinator:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def _response_error(status: int) -> ClientResponseError:
    return ClientResponseError(MagicMock(), (), status=status)


async def _raise(err: BaseException) -> None:
    raise err


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_response_error(400), ConfigEntryAuthFailed),
        (_response_error(500), UpdateFailed),
        (ApiError("denied", status=403), ConfigEntryAuthFailed),
        (ApiError("server", status=500), UpdateFailed),
        (TimeoutError(), UpdateFailed),
    ],
)
async def test_guard_maps_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    error: BaseException,
    expected: type[Exception],
) -> None:
    coordinator = await _setup(hass, config_entry)
    with pytest.raises(expected):
        await coordinator._async_guard(_raise(error))


async def test_guard_backs_off_on_rate_limit(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, config_entry)
    with pytest.raises(UpdateFailed):
        await coordinator._async_guard(_raise(ApiThrottlingError("slow down")))
    assert coordinator.update_interval == THROTTLED_INTERVAL

    # The next successful call restores the normal interval.
    await coordinator._async_guard(AsyncMock()())
    assert coordinator.update_interval == STATUS_INTERVAL


async def test_no_energy_home_retries_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.net_trv.coordinator.is_energy_home", return_value=False
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unreachable_relay_is_logged_once(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator = await _setup(hass, config_entry)
    update = AsyncMock(side_effect=ApiHomeReachabilityError("offline"))
    with patch.object(coordinator.account, "async_update_status", update):
        await coordinator._async_refresh_all()
        await coordinator._async_refresh_all()
    assert caplog.text.count("is unreachable") == 1

    await coordinator._async_refresh_all()
    assert "is reachable again" in caplog.text
    assert not coordinator._unreachable_logged


async def test_stale_topology_is_reloaded(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    coordinator = await _setup(hass, config_entry)
    coordinator._topology_at = None
    mock_api.reset_mock()
    await coordinator._async_refresh_all()
    urls = [c.kwargs["url"] for c in mock_api.call_args_list]
    assert any(url.endswith("api/homesdata") for url in urls)


async def test_bad_webhook_payload_is_swallowed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator = await _setup(hass, config_entry)
    with patch(
        "custom_components.net_trv.coordinator.process_webhook",
        side_effect=ValueError("garbage"),
    ):
        await coordinator.async_apply_webhook(
            {"push_type": "display_change", "home_id": "x"}
        )
    assert coordinator.webhook.events_applied == 0
    assert "Could not apply Netatmo webhook event" in caplog.text


async def test_topology_event_schedules_confirm_refresh(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    coordinator = await _setup(hass, config_entry)
    result: Any = SimpleNamespace(
        event_type="home_event_changed",
        refresh_scope=frozenset({RefreshScope.TOPOLOGY}),
    )
    with patch(
        "custom_components.net_trv.coordinator.process_webhook",
        return_value=result,
    ):
        await coordinator.async_apply_webhook(
            {"push_type": "home_event_changed", "home_id": "x", "ts": 1}
        )
    assert coordinator._topology_at is None
    assert coordinator.webhook.applied_by_type == {"home_event_changed": 1}

    mock_api.reset_mock()
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=CONFIRM_DELAY_SECONDS + 1)
    )
    await hass.async_block_till_done()
    urls = [c.kwargs["url"] for c in mock_api.call_args_list]
    assert any(url.endswith("api/homesdata") for url in urls)
    assert any(url.endswith("api/homestatus") for url in urls)
