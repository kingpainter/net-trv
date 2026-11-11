"""Netatmo webhook (v1) for NET-TRV.

Every payload is verified
(HMAC-SHA256 of the raw body with the app's client secret, sent in
``X-Netatmo-secret``), answered with 200 at once (Netatmo allows 5 s and
suspends slow endpoints for up to 10 min), and kept in a short ring buffer for
diagnostics, then merged into the model by the coordinator (phase 2).
"""
# VERSION = "0.11.1"

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import secrets
from typing import Any

from aiohttp import ClientError, web
from homeassistant.components import webhook
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.util import dt as dt_util
from pyatmo.const import WEBHOOK_ENDPOINT
from pyatmo.exceptions import ApiError

from .const import CONF_CLOUDHOOK_URL, DOMAIN
from .coordinator import NetatmoEnergyConfigEntry

_LOGGER = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Netatmo-secret"
PUSH_ACTIVATION = "webhook_activation"
PUSH_DELETION = "webhook_deletion"

# Repair issues (quality scale repair-issues). Without a webhook NET-TRV still
# works on polling, so these are warnings the user can act on, not errors.
ISSUE_NO_URL = "webhook_no_public_url"
ISSUE_REGISTRATION = "webhook_registration_failed"
WEBHOOK_DOCS_URL = "https://github.com/kingpainter/net-trv#webhook"


@callback
def async_update_webhook_issue(
    hass: HomeAssistant, issue_id: str | None, error: str | None = None
) -> None:
    """Show at most one webhook issue; ``None`` clears them all."""
    for known in (ISSUE_NO_URL, ISSUE_REGISTRATION):
        if known != issue_id:
            ir.async_delete_issue(hass, DOMAIN, known)
    if issue_id is None:
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=issue_id,
        translation_placeholders={"error": error} if error else None,
        learn_more_url=WEBHOOK_DOCS_URL,
    )


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
        # Nabu Casa cloudhooks arrive as a MockRequest, which has no read();
        # request.content.read() returns the raw bytes for both kinds, and the
        # HMAC must be computed over the raw bytes.
        body = await request.content.read()
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
        entry.async_create_background_task(
            hass,
            coordinator.async_apply_webhook(payload),
            "net_trv_apply_webhook",
        )

    webhook.async_register(
        hass, DOMAIN, "NET-TRV", webhook_id, _handle, local_only=False
    )
    entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))

    await async_register_with_netatmo(hass, entry, force=False)


async def _is_registered(entry: NetatmoEnergyConfigEntry, url: str) -> bool:
    """Return True when Netatmo already has exactly our URL for this app."""
    try:
        resp = await entry.runtime_data.auth.async_get_api_request(
            endpoint=WEBHOOK_ENDPOINT
        )
        data = await resp.json()
    except ApiError, ClientError, TimeoutError:
        return False
    body = data.get("body") if isinstance(data, dict) else None
    return any(isinstance(item, dict) and item.get("url") == url for item in body or [])


async def async_register_with_netatmo(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry, *, force: bool = True
) -> bool:
    """Tell Netatmo our URL (also the ``register_webhook`` action).

    At startup (``force=False``) an existing registration of our URL is kept:
    re-adding it makes Netatmo answer "conflict", and pyatmo then deletes and
    re-adds the webhook, so every restart produced two delete/activate pairs.
    """
    coordinator = entry.runtime_data
    state = coordinator.webhook
    webhook_id = await async_ensure_webhook_id(hass, entry)
    if getattr(coordinator.oauth_implementation, "client_secret", None) is None:
        state.last_error = "client secret unavailable; webhook not registered"
        _LOGGER.warning("NET-TRV: %s", state.last_error)
        async_update_webhook_issue(hass, ISSUE_REGISTRATION, state.last_error)
        coordinator.async_signal_webhook()
        return False

    url = await _async_webhook_url(hass, entry, webhook_id)
    if url is None:
        state.last_error = "no public HTTPS URL (Nabu Casa or external URL needed)"
        _LOGGER.warning("NET-TRV webhook not registered: %s", state.last_error)
        async_update_webhook_issue(hass, ISSUE_NO_URL)
        coordinator.async_signal_webhook()
        return False

    if not force and await _is_registered(entry, url):
        state.registered = True
        state.last_error = None
        _LOGGER.debug("NET-TRV webhook already registered with Netatmo")
        async_update_webhook_issue(hass, None)
        coordinator.async_signal_webhook()
        return True

    try:
        await coordinator.auth.async_addwebhook(url)
    except (ApiError, TimeoutError) as err:
        state.last_error = f"registration failed: {err}"
        _LOGGER.warning("NET-TRV webhook %s", state.last_error)
        async_update_webhook_issue(hass, ISSUE_REGISTRATION, str(err))
        coordinator.async_signal_webhook()
        return False
    state.registered = True
    state.deleted_by_netatmo = False
    state.last_error = None
    _LOGGER.info("NET-TRV webhook registered")
    async_update_webhook_issue(hass, None)
    coordinator.async_signal_webhook()
    return True


async def async_unregister_with_netatmo(entry: NetatmoEnergyConfigEntry) -> None:
    """Ask Netatmo to stop sending events (``unregister_webhook`` action).

    The local handler stays, so a later ``register_webhook`` works without a
    reload. Polling falls back to 60 s once events stop.
    """
    coordinator = entry.runtime_data
    await coordinator.auth.async_dropwebhook()
    coordinator.webhook.registered = False
    coordinator.webhook.last_error = "unregistered by user"
    # A deliberate stop is not something to repair.
    async_update_webhook_issue(coordinator.hass, None)
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
