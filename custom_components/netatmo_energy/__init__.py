"""The NET-TRV integration: Netatmo relay (NAPlug) and valves (NRV)."""
# VERSION = "0.4.0"

from __future__ import annotations

import logging

from aiohttp import ClientResponseError
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    aiohttp_client,
    config_entry_oauth2_flow,
    device_registry as dr,
)
from homeassistant.helpers.start import async_at_started

from .api import NetatmoEnergyAuth
from .const import DOMAIN
from .coordinator import NetatmoEnergyConfigEntry, NetatmoEnergyCoordinator
from .entity import relay_device_info, relay_of, room_identifier, valves_in
from .webhook import async_remove_webhook, async_setup_webhook

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry
) -> bool:
    """Set up NET-TRV from a config entry."""
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    try:
        await oauth_session.async_ensure_token_valid()
    except ClientResponseError as err:
        if 400 <= err.status < 500:
            raise ConfigEntryAuthFailed from err
        raise ConfigEntryNotReady from err

    auth = NetatmoEnergyAuth(
        aiohttp_client.async_get_clientsession(hass), oauth_session
    )
    coordinator = NetatmoEnergyCoordinator(hass, entry, auth)
    coordinator.oauth_implementation = implementation
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _register_relays(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_stale_devices(hass, entry, coordinator)

    # Register the webhook once HA has started, so Nabu Casa is connected.
    async def _start_webhook(hass: HomeAssistant) -> None:
        await async_setup_webhook(hass, entry)

    entry.async_on_unload(async_at_started(hass, _start_webhook))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry
) -> None:
    """Clean up the Nabu Casa cloudhook when the integration is removed."""
    await async_remove_webhook(hass, entry)


def _register_relays(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    coordinator: NetatmoEnergyCoordinator,
) -> None:
    """Register the relay of each home before the room entities are added.

    Room devices point at the relay via ``via_device_id`` (its registry id),
    so the relay must exist first; its id is kept on the coordinator.
    """
    registry = dr.async_get(hass)
    for home in coordinator.homes:
        if (relay := relay_of(home)) is not None:
            relay_device = registry.async_get_or_create(
                config_entry_id=entry.entry_id, **relay_device_info(relay, home)
            )
            coordinator.relay_device_ids[home.entity_id] = relay_device.id


def _expected_identifiers(coordinator: NetatmoEnergyCoordinator) -> set[str]:
    """Return the device identifiers this integration should currently own."""
    expected: set[str] = set()
    for home in coordinator.homes:
        if (relay := relay_of(home)) is not None:
            expected.add(relay.entity_id)
        expected.update(
            room_identifier(room) for room in home.rooms.values() if valves_in(room)
        )
    return expected


def _remove_stale_devices(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    coordinator: NetatmoEnergyCoordinator,
) -> None:
    """Detach this entry from devices it no longer provides.

    Covers rooms without valves and the per-valve devices from 0.1.0/0.1.1.
    Only devices of this config entry are touched; HA deletes a device once
    no config entry references it.
    """
    registry = dr.async_get(hass)
    expected = _expected_identifiers(coordinator)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        ours = {ident for domain, ident in device.identifiers if domain == DOMAIN}
        if ours and not ours & expected:
            _LOGGER.debug("Removing stale device %s (%s)", device.name, ours)
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removing devices that this integration no longer provides."""
    expected = _expected_identifiers(entry.runtime_data)
    return not any(
        domain == DOMAIN and ident in expected
        for domain, ident in device_entry.identifiers
    )
