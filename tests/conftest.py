"""Shared fixtures for Netatmo Energy tests."""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.netatmo_energy.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components in every test."""


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
        title="Netatmo Energy",
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


@pytest.fixture
def mock_api() -> Generator[AsyncMock]:
    """Answer Netatmo API calls from the fixtures, no network."""

    async def _post(url: str, params: dict[str, Any] | None = None) -> MagicMock:
        if url.endswith("api/homesdata"):
            body = load_fixture("homesdata.json")
        elif url.endswith("api/homestatus"):
            body = load_fixture("homestatus.json")
        else:
            body = {"status": "ok", "time_server": 1762700000}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=body)
        return resp

    with patch(
        "custom_components.netatmo_energy.api.NetatmoEnergyAuth.async_post_request",
        side_effect=_post,
        autospec=False,
    ) as mock_post:
        yield mock_post
