"""Shared fixtures for NET-TRV tests."""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.net_trv.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components in every test."""


@pytest.fixture(autouse=True)
def webhook_url(request: pytest.FixtureRequest) -> Generator[str | None]:
    """Give the webhook a public URL; tests can override via indirect param.

    Nabu Casa is not set up in tests, so the URL lookup is patched.
    """
    url = getattr(request, "param", "https://example.com/api/webhook/test")
    with patch(
        "custom_components.net_trv.webhook._async_webhook_url",
        return_value=url,
    ):
        yield url


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
async def credentials(hass: HomeAssistant) -> None:
    """Register application credentials for the OAuth flow."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET), DOMAIN
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A config entry holding a valid token."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="NET-TRV",
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": time.time() + 3600,
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "read_thermostat write_thermostat",
            },
        },
    )


def room_measure(params: dict[str, Any]) -> dict[str, Any]:
    """Hourly history like Netatmo: ts centred in the hour, optimize=false."""
    begin, end = int(params["date_begin"]), int(params["date_end"])
    body = {
        str(start + 1800): [20.0, 19.5, 20.5, 21]
        for start in range(begin // 3600 * 3600, end, 3600)
    }
    return {"status": "ok", "body": body}


@pytest.fixture
def mock_api() -> Generator[AsyncMock]:
    """Answer Netatmo API calls from the fixtures, no network."""

    async def _post(url: str, params: dict[str, Any] | None = None) -> MagicMock:
        if url.endswith("api/homesdata"):
            body = load_fixture("homesdata.json")
        elif url.endswith("api/homestatus"):
            body = load_fixture("homestatus.json")
        elif url.endswith("api/getpublicdata"):
            body = load_fixture("publicdata.json")
        elif url.endswith("api/getroommeasure"):
            body = room_measure(params or {})
        else:
            body = {"status": "ok", "time_server": 1762700000}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=body)
        return resp

    async def _get(url: str, params: dict[str, Any] | None = None) -> MagicMock:
        # webhooks/v1: no webhook registered yet unless a test says otherwise.
        resp = MagicMock()
        resp.json = AsyncMock(return_value={"status": "ok", "body": []})
        return resp

    with (
        patch(
            "custom_components.net_trv.api.NetatmoEnergyAuth.async_post_request",
            side_effect=_post,
            autospec=False,
        ) as mock_post,
        patch(
            "custom_components.net_trv.api.NetatmoEnergyAuth.async_get_request",
            side_effect=_get,
            autospec=False,
        ),
    ):
        yield mock_post


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable entities that are disabled by default (as in HA core's tests)."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield
