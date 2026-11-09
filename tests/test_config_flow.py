"""Tests for the Netatmo Energy config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
import pytest

from custom_components.netatmo_energy.const import (
    DOMAIN,
    OAUTH2_AUTHORIZE,
    OAUTH2_TOKEN,
)

from .conftest import CLIENT_ID


@pytest.mark.usefixtures("credentials", "current_request_with_host")
async def test_full_flow(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {
            "flow_id": result["flow_id"],
            "redirect_uri": "https://example.com/auth/external/callback",
        },
    )

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["url"].startswith(
        f"{OAUTH2_AUTHORIZE}?response_type=code&client_id={CLIENT_ID}"
    )
    assert "scope=read_thermostat+write_thermostat" in result["url"]

    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200

    aioclient_mock.post(
        OAUTH2_TOKEN,
        json={
            "refresh_token": "refresh",
            "access_token": "access",
            "type": "Bearer",
            "expires_in": 3600,
        },
    )

    with patch(
        "custom_components.netatmo_energy.async_setup_entry", return_value=True
    ) as mock_setup:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Netatmo Energy"
    assert result["data"]["token"]["access_token"] == "access"
    assert len(mock_setup.mock_calls) == 1
