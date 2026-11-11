"""Webhook edge cases: URL choice, Nabu Casa, failed registration, bad bodies."""

from __future__ import annotations

from collections.abc import Generator
import hashlib
import hmac
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import homeassistant.components
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError
from pyatmo.exceptions import ApiError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.net_trv import webhook as net_webhook
from custom_components.net_trv.const import CONF_CLOUDHOOK_URL, DOMAIN

from .conftest import CLIENT_SECRET

# The autouse ``webhook_url`` fixture patches this; keep the real one.
REAL_WEBHOOK_URL = net_webhook._async_webhook_url
GENERATE_URL = "custom_components.net_trv.webhook.webhook.async_generate_url"
CLOUDHOOK = "https://hooks.nabu.casa/abc"


class CloudNotAvailable(Exception):
    """Stand-in for the cloud integration's exception."""


@pytest.fixture
def cloud(hass: HomeAssistant) -> Generator[SimpleNamespace]:
    """A fake Nabu Casa cloud module; the real one pulls in camera deps."""
    fake = SimpleNamespace(
        CloudNotAvailable=CloudNotAvailable,
        async_active_subscription=MagicMock(return_value=True),
        async_get_or_create_cloudhook=AsyncMock(return_value=CLOUDHOOK),
        async_delete_cloudhook=AsyncMock(),
    )
    hass.config.components.add("cloud")
    with (
        patch.dict("sys.modules", {"homeassistant.components.cloud": fake}),
        patch.object(homeassistant.components, "cloud", fake, create=True),
    ):
        yield fake


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_WEBHOOK_ID: "hook"})
    entry.add_to_hass(hass)
    return entry


async def test_url_reuses_stored_cloudhook(
    hass: HomeAssistant, cloud: SimpleNamespace, entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CLOUDHOOK_URL: "https://hooks.nabu.casa/old"}
    )
    assert await REAL_WEBHOOK_URL(hass, entry, "hook") == "https://hooks.nabu.casa/old"
    cloud.async_get_or_create_cloudhook.assert_not_called()


async def test_url_creates_and_stores_cloudhook(
    hass: HomeAssistant, cloud: SimpleNamespace, entry: MockConfigEntry
) -> None:
    assert await REAL_WEBHOOK_URL(hass, entry, "hook") == CLOUDHOOK
    assert entry.data[CONF_CLOUDHOOK_URL] == CLOUDHOOK


async def test_url_falls_back_to_external_url(
    hass: HomeAssistant, cloud: SimpleNamespace, entry: MockConfigEntry
) -> None:
    cloud.async_get_or_create_cloudhook.side_effect = CloudNotAvailable
    with patch(GENERATE_URL, return_value="https://ext.example/api/webhook/hook"):
        url = await REAL_WEBHOOK_URL(hass, entry, "hook")
    assert url == "https://ext.example/api/webhook/hook"


@pytest.mark.parametrize(
    ("generated", "expected"),
    [
        (NoURLAvailableError(), None),
        ("http://192.168.0.5:8123/api/webhook/hook", None),
        (
            "https://ext.example/api/webhook/hook",
            "https://ext.example/api/webhook/hook",
        ),
    ],
)
async def test_url_without_cloud(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    generated: Any,
    expected: str | None,
) -> None:
    kwargs = (
        {"side_effect": generated}
        if isinstance(generated, Exception)
        else {"return_value": generated}
    )
    with patch(GENERATE_URL, **kwargs):
        assert await REAL_WEBHOOK_URL(hass, entry, "hook") == expected


async def test_remove_webhook_deletes_cloudhook(
    hass: HomeAssistant, cloud: SimpleNamespace, entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CLOUDHOOK_URL: CLOUDHOOK}
    )
    await net_webhook.async_remove_webhook(hass, entry)
    cloud.async_delete_cloudhook.assert_awaited_once_with(hass, "hook")

    # Nabu Casa gone in the meantime: removal still succeeds.
    cloud.async_delete_cloudhook.side_effect = CloudNotAvailable
    await net_webhook.async_remove_webhook(hass, entry)


async def test_remove_webhook_without_cloud(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    await net_webhook.async_remove_webhook(hass, entry)


@pytest.mark.usefixtures("credentials", "mock_api")
class TestRegistration:
    """Registration branches that need a loaded entry."""

    async def _setup(self, hass: HomeAssistant, config_entry: MockConfigEntry) -> Any:
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        return config_entry.runtime_data

    async def test_missing_client_secret(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ) -> None:
        coordinator = await self._setup(hass, config_entry)
        coordinator.oauth_implementation = None
        assert not await net_webhook.async_register_with_netatmo(hass, config_entry)
        assert "client secret" in coordinator.webhook.last_error

    async def test_registration_error(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ) -> None:
        coordinator = await self._setup(hass, config_entry)
        with patch.object(
            coordinator.auth, "async_addwebhook", side_effect=ApiError("conflict")
        ):
            assert not await net_webhook.async_register_with_netatmo(hass, config_entry)
        assert coordinator.webhook.last_error.startswith("registration failed")

    async def test_lookup_error_registers_anyway(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ) -> None:
        coordinator = await self._setup(hass, config_entry)
        add = AsyncMock()
        with (
            patch.object(
                coordinator.auth,
                "async_get_api_request",
                side_effect=TimeoutError,
            ),
            patch.object(coordinator.auth, "async_addwebhook", add),
        ):
            assert await net_webhook.async_register_with_netatmo(
                hass, config_entry, force=False
            )
        add.assert_awaited_once()

    @pytest.mark.parametrize(("body", "status"), [(b"not json", 400), (b"[1]", 200)])
    async def test_unusable_body(
        self,
        hass: HomeAssistant,
        hass_client_no_auth: Any,
        config_entry: MockConfigEntry,
        body: bytes,
        status: int,
    ) -> None:
        coordinator = await self._setup(hass, config_entry)
        client = await hass_client_no_auth()
        sig = hmac.new(CLIENT_SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = await client.post(
            f"/api/webhook/{config_entry.data[CONF_WEBHOOK_ID]}",
            data=body,
            headers={"X-Netatmo-secret": sig},
        )
        await hass.async_block_till_done()
        assert resp.status == status
        assert coordinator.webhook.events_received == 0
