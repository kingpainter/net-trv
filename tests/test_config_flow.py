"""Tests for the NET-TRV config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
import pytest

from custom_components.net_trv.const import (
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
        "custom_components.net_trv.async_setup_entry", return_value=True
    ) as mock_setup:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NET-TRV"
    assert result["data"]["token"]["access_token"] == "access"
    assert len(mock_setup.mock_calls) == 1


@pytest.mark.usefixtures("credentials", "current_request_with_host")
async def test_reconfigure_keeps_webhook_id(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock, config_entry
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        data={
            **config_entry.data,
            "webhook_id": "keep-me",
            "cloudhook_url": "https://hooks.example/keep-me",
        },
    )

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {
            "flow_id": result["flow_id"],
            "redirect_uri": "https://example.com/auth/external/callback",
        },
    )
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200

    aioclient_mock.post(
        OAUTH2_TOKEN,
        json={
            "refresh_token": "new-refresh",
            "access_token": "new-access",
            "type": "Bearer",
            "expires_in": 3600,
        },
    )
    with patch("custom_components.net_trv.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data["token"]["access_token"] == "new-access"
    assert config_entry.data["webhook_id"] == "keep-me"
    assert config_entry.data["cloudhook_url"] == "https://hooks.example/keep-me"
