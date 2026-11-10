"""Tests for the NET-TRV webhook (step 1c: receive, verify, record)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components import webhook
from homeassistant.const import CONF_WEBHOOK_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.util.aiohttp import MockRequest
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.netatmo_energy.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.netatmo_energy.webhook import verify_signature

from .conftest import CLIENT_SECRET

pytestmark = pytest.mark.usefixtures("credentials")

WEBHOOK_SENSOR = "binary_sensor.gang_relay_webhook"


def _sign(body: bytes, secret: str = CLIENT_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature() -> None:
    body = b'{"push_type":"device_event"}'
    assert verify_signature(body, "s3cret", _sign(body, "s3cret"))
    assert verify_signature(body, "s3cret", _sign(body, "s3cret").upper())
    assert not verify_signature(body, "s3cret", _sign(body, "other"))
    assert not verify_signature(body, "s3cret", None)
    assert not verify_signature(body + b" ", "s3cret", _sign(body, "s3cret"))


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return str(entry.data[CONF_WEBHOOK_ID])


async def _post(client: Any, webhook_id: str, payload: dict[str, Any], sig: str | None):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sig is not None:
        headers["X-Netatmo-secret"] = sig if sig else _sign(body)
    return await client.post(f"/api/webhook/{webhook_id}", data=body, headers=headers)


async def test_registers_with_netatmo(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)

    calls = [c for c in mock_api.call_args_list if "webhooks/v1" in c.kwargs["url"]]
    assert len(calls) == 1
    assert calls[0].kwargs["params"] == {
        "json": {"url": "https://example.com/api/webhook/test"}
    }
    assert hass.states.get(WEBHOOK_SENSOR).state == STATE_ON


@pytest.mark.parametrize("webhook_url", [None], indirect=True)
async def test_no_public_url_means_inactive(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)

    assert not [c for c in mock_api.call_args_list if "webhooks/v1" in c.kwargs["url"]]
    state = hass.states.get(WEBHOOK_SENSOR)
    assert state.state == STATE_OFF
    assert "public HTTPS URL" in state.attributes["last_error"]


async def test_signed_event_is_recorded(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_client_no_auth: Any,
) -> None:
    webhook_id = await _setup(hass, config_entry)
    client = await hass_client_no_auth()
    payload = {
        "push_type": "device_event",
        "user_id": "secret-user",
        "home_id": "home1",
        "extra_params": {"modules": [{"id": "09:00:00:00:00:01", "reachable": True}]},
    }

    resp = await _post(client, webhook_id, payload, sig="")
    assert resp.status == 200
    await hass.async_block_till_done()

    state = hass.states.get(WEBHOOK_SENSOR)
    assert state.attributes["events_received"] == 1
    assert state.attributes["last_event"] is not None

    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    recent = diag["webhook"]["recent"]
    assert recent[0]["push_type"] == "device_event"
    assert recent[0]["payload"]["user_id"] == "**REDACTED**"
    assert recent[0]["payload"]["extra_params"]["modules"][0]["id"] == (
        "09:00:00:00:00:01"
    )
    assert diag["entry"]["token"] == "**REDACTED**"


async def test_bad_or_missing_signature_is_rejected(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_client_no_auth: Any,
) -> None:
    webhook_id = await _setup(hass, config_entry)
    client = await hass_client_no_auth()
    payload = {"push_type": "device_event"}

    assert (await _post(client, webhook_id, payload, sig="00" * 32)).status == 401
    assert (await _post(client, webhook_id, payload, sig=None)).status == 401
    await hass.async_block_till_done()

    state = hass.states.get(WEBHOOK_SENSOR)
    assert state.attributes["events_received"] == 0
    assert state.attributes["rejected_signatures"] == 2


async def test_deletion_and_activation_events(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_client_no_auth: Any,
) -> None:
    webhook_id = await _setup(hass, config_entry)
    client = await hass_client_no_auth()

    await _post(client, webhook_id, {"push_type": "webhook_deletion"}, sig="")
    await hass.async_block_till_done()
    assert hass.states.get(WEBHOOK_SENSOR).state == STATE_OFF

    await _post(client, webhook_id, {"push_type": "webhook_activation"}, sig="")
    await hass.async_block_till_done()
    assert hass.states.get(WEBHOOK_SENSOR).state == STATE_ON


@pytest.mark.parametrize("signed", [True, False])
async def test_cloudhook_mock_request(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    signed: bool,
) -> None:
    """Nabu Casa delivers a MockRequest (no read()); it must be handled too."""
    webhook_id = await _setup(hass, config_entry)
    body = json.dumps({"push_type": "device_event"}).encode()
    headers = {"Content-Type": "application/json"}
    if signed:
        headers["X-Netatmo-secret"] = _sign(body)
    request = MockRequest(
        content=body, mock_source="cloud", method="POST", headers=headers
    )

    response = await webhook.async_handle_webhook(hass, webhook_id, request)
    await hass.async_block_till_done()

    state = hass.states.get(WEBHOOK_SENSOR)
    if signed:
        assert response.status == 200
        assert state.attributes["events_received"] == 1
    else:
        assert response.status == 401
        assert state.attributes["rejected_signatures"] == 1


async def test_diagnostics_lists_netatmo_webhooks(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    get = AsyncMock()
    get.return_value.json = AsyncMock(
        return_value={"body": [{"url": "https://hooks.example/abcdefghijkl"}]}
    )
    with patch(
        "custom_components.netatmo_energy.api.NetatmoEnergyAuth.async_get_request",
        get,
    ):
        diag = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diag["webhook"]["netatmo_webhooks"] == {"body": [{"url": "...efghijkl"}]}
