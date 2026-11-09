"""Netatmo webhook (v1) for NET-TRV.

Step 1c only *receives and records* events: every payload is verified
(HMAC-SHA256 of the raw body with the app's client secret, sent in
``X-Netatmo-secret``), answered with 200 at once (Netatmo allows 5 s and
suspends slow endpoints for up to 10 min), and kept in a short ring buffer for
diagnostics. Merging events into state comes in phase 2.
"""
# VERSION = "0.3.1"

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import secrets
from typing import Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.util import dt as dt_util
from pyatmo.exceptions import ApiError

from .const import CONF_CLOUDHOOK_URL, DOMAIN
from .coordinator import NetatmoEnergyConfigEntry

_LOGGER = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Netatmo-secret"
PUSH_ACTIVATION = "webhook_activation"
PUSH_DELETION = "webhook_deletion"


def verify_signature(body: bytes, secret: str, signature: str | None) -> bool:
    """Return True when ``signature`` is the hex HMAC-SHA256 of ``body``."""
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


async def async_ensure_webhook_id(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry
) -> str:
    """Return the entry's webhook id, creating one on first use."""
    if webhook_id := entry.data.get(CONF_WEBHOOK_ID):
        return str(webhook_id)
    webhook_id = secrets.token_hex()
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_WEBHOOK_ID: webhook_id}
    )
    return webhook_id


async def _async_webhook_url(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry, webhook_id: str
) -> str | None:
    """Return a public HTTPS URL: Nabu Casa cloudhook first, else external URL."""
    # Imported lazily and only when Nabu Casa is set up: the cloud package
    # pulls in heavy optional deps (camera, turbojpeg).
    if "cloud" in hass.config.components:
        from homeassistant.components import cloud  # noqa: PLC0415

        if cloud.async_active_subscription(hass):
            if url := entry.data.get(CONF_CLOUDHOOK_URL):
                return str(url)
            try:
                url = await cloud.async_get_or_create_cloudhook(hass, webhook_id)
            except cloud.CloudNotAvailable:
                _LOGGER.debug("Nabu Casa not available; trying the external URL")
            else:
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_CLOUDHOOK_URL: url}
                )
                return url
    try:
        url = webhook.async_generate_url(hass, webhook_id, prefer_external=True)
    except NoURLAvailableError:
        return None
    # Netatmo only delivers to HTTPS on the default port.
    return url if url.startswith("https://") else None


async def async_setup_webhook(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry
) -> None:
    """Register the local handler, then the URL with Netatmo."""
    coordinator = entry.runtime_data
    state = coordinator.webhook
    webhook_id = await async_ensure_webhook_id(hass, entry)
    secret = getattr(coordinator.oauth_implementation, "client_secret", None)

    async def _handle(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        body = await request.read()
        if not secret or not verify_signature(
            body, secret, request.headers.get(SIGNATURE_HEADER)
        ):
            state.rejected_signatures += 1
            _LOGGER.warning("Rejected Netatmo webhook call with a bad signature")
            hass.loop.call_soon(coordinator.async_signal_webhook)
            return web.Response(status=401)
        try:
            payload = json.loads(body)
        except ValueError:
            return web.Response(status=400)
        # Answer first; recording is cheap but must never delay the 200.
        hass.loop.call_soon(_record, payload)
        return web.Response(status=200)

    @callback
    def _record(payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        push_type = payload.get("push_type")
        state.events_received += 1
        state.last_event_at = dt_util.utcnow()
        state.recent.append(
            {
                "received": state.last_event_at.isoformat(),
                "push_type": push_type,
                "payload": payload,
            }
        )
        if push_type == PUSH_ACTIVATION:
            state.deleted_by_netatmo = False
        elif push_type == PUSH_DELETION:
            state.deleted_by_netatmo = True
        _LOGGER.debug("Netatmo webhook event: %s", push_type)
        coordinator.async_signal_webhook()

    webhook.async_register(
        hass, DOMAIN, "NET-TRV", webhook_id, _handle, local_only=False
    )
    entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))

    if secret is None:
        state.last_error = "client secret unavailable; webhook not registered"
        _LOGGER.warning("NET-TRV: %s", state.last_error)
        coordinator.async_signal_webhook()
        return

    url = await _async_webhook_url(hass, entry, webhook_id)
    if url is None:
        state.last_error = "no public HTTPS URL (Nabu Casa or external URL needed)"
        _LOGGER.warning("NET-TRV webhook not registered: %s", state.last_error)
        coordinator.async_signal_webhook()
        return

    try:
        await coordinator.auth.async_addwebhook(url)
    except (ApiError, TimeoutError) as err:
        state.last_error = f"registration failed: {err}"
        _LOGGER.warning("NET-TRV webhook %s", state.last_error)
    else:
        state.registered = True
        state.last_error = None
        _LOGGER.info("NET-TRV webhook registered")
    coordinator.async_signal_webhook()


async def async_remove_webhook(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry
) -> None:
    """Forget the cloudhook when the entry is removed."""
    if "cloud" not in hass.config.components:
        return
    from homeassistant.components import cloud  # noqa: PLC0415

    if (webhook_id := entry.data.get(CONF_WEBHOOK_ID)) and entry.data.get(
        CONF_CLOUDHOOK_URL
    ):
        with contextlib.suppress(cloud.CloudNotAvailable):
            await cloud.async_delete_cloudhook(hass, webhook_id)
