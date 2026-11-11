"""Tests for the NET-TRV config flow."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pyatmo.exceptions import ApiError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.net_trv.const import (
    DOMAIN,
    OAUTH2_AUTHORIZE,
    OAUTH2_TOKEN,
)

from .conftest import CLIENT_ID, load_fixture

POST = "custom_components.net_trv.api.NetatmoEnergyAuth.async_post_request"


async def _finish_oauth(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    flow_id: str,
    access: str = "access",
) -> dict[str, Any]:
    """Run the external OAuth step and return the final flow result."""
    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {
            "flow_id": flow_id,
            "redirect_uri": "https://example.com/auth/external/callback",
        },
    )
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200
    aioclient_mock.post(
        OAUTH2_TOKEN,
        json={
            "refresh_token": "refresh",
            "access_token": access,
            "type": "Bearer",
            "expires_in": 3600,
        },
    )
    with patch("custom_components.net_trv.async_setup_entry", return_value=True):
        return await hass.config_entries.flow.async_configure(flow_id)


def _homesdata_without_relay() -> dict[str, Any]:
    body = load_fixture("homesdata.json")
    for home in body["body"]["homes"]:
        home["modules"] = []
        home["rooms"] = []
    return body


@pytest.mark.usefixtures("credentials", "current_request_with_host", "mock_api")
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


@pytest.mark.usefixtures("credentials", "current_request_with_host", "mock_api")
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


@pytest.mark.usefixtures("credentials", "current_request_with_host")
@pytest.mark.parametrize(
    ("side_effect", "reason"),
    [
        (ApiError("boom"), "cannot_connect"),
        (ClientError("down"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
    ],
)
async def test_abort_when_netatmo_fails(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    side_effect: Exception,
    reason: str,
) -> None:
    """test-before-configure: no entry when /homesdata cannot be read."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(POST, side_effect=side_effect):
        result = await _finish_oauth(
            hass, hass_client_no_auth, aioclient_mock, result["flow_id"]
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.usefixtures("credentials", "current_request_with_host")
@pytest.mark.parametrize(
    "body",
    [
        _homesdata_without_relay(),
        {"status": "ok", "body": {"homes": [], "user": {}}},
    ],
    ids=["no_relay", "no_homes"],
)
async def test_abort_without_energy_home(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    body: dict[str, Any],
) -> None:
    """test-before-configure: an account without a relay is refused."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    resp = MagicMock()
    resp.json = AsyncMock(return_value=body)
    with patch(POST, return_value=resp):
        result = await _finish_oauth(
            hass, hass_client_no_auth, aioclient_mock, result["flow_id"]
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_energy_home"
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.usefixtures("credentials", "current_request_with_host", "mock_api")
async def test_reauth(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    result = await _finish_oauth(
        hass, hass_client_no_auth, aioclient_mock, result["flow_id"], "re-access"
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["token"]["access_token"] == "re-access"


RELAY_HOMEKIT = ZeroconfServiceInfo(
    ip_address=ip_address("192.168.0.50"),
    ip_addresses=[ip_address("192.168.0.50")],
    hostname="Netatmo-Relay-g12345.local.",
    name="Netatmo Relay g12345._hap._tcp.local.",
    port=5001,
    type="_hap._tcp.local.",
    properties={"md": "Netatmo Relay", "id": "AA:BB:CC:DD:EE:FF", "sf": "1"},
)


@pytest.mark.usefixtures("credentials", "current_request_with_host", "mock_api")
async def test_homekit_discovery(
    hass: HomeAssistant, hass_client_no_auth: Any, aioclient_mock: Any
) -> None:
    """The relay found over HomeKit leads to a confirm step, then OAuth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HOMEKIT},
        data=RELAY_HOMEKIT,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "homekit_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    result = await _finish_oauth(
        hass, hass_client_no_auth, aioclient_mock, result["flow_id"]
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("credentials")
async def test_homekit_discovery_when_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Discovery is silent once NET-TRV is set up."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HOMEKIT},
        data=RELAY_HOMEKIT,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


@pytest.mark.usefixtures("credentials", "current_request_with_host", "mock_api")
async def test_new_entry_gets_home_unique_id(
    hass: HomeAssistant, hass_client_no_auth: Any, aioclient_mock: Any
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await _finish_oauth(
        hass, hass_client_no_auth, aioclient_mock, result["flow_id"]
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "home1"


@pytest.mark.usefixtures("credentials", "current_request_with_host", "mock_api")
@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_other_account_is_refused(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    config_entry: MockConfigEntry,
    source: str,
) -> None:
    """Signing in with an account without the entry's home changes nothing."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id="other-home")
    if source == "reauth":
        result = await config_entry.start_reauth_flow(hass)
    else:
        result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _finish_oauth(
        hass, hass_client_no_auth, aioclient_mock, result["flow_id"], "intruder"
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data["token"]["access_token"] == "access"
    assert config_entry.unique_id == "other-home"


@pytest.mark.usefixtures("credentials", "current_request_with_host", "mock_api")
async def test_reauth_sets_missing_unique_id(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    config_entry: MockConfigEntry,
) -> None:
    """An entry from before 0.11.0 takes the home id on reauth."""
    config_entry.add_to_hass(hass)
    assert config_entry.unique_id is None
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _finish_oauth(
        hass, hass_client_no_auth, aioclient_mock, result["flow_id"]
    )
    assert result["reason"] == "reauth_successful"
    assert config_entry.unique_id == "home1"
