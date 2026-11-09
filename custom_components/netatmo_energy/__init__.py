"""The Netatmo Energy integration: Netatmo relay (NAPlug) and valves (NRV)."""
# VERSION = "0.1.1"

from __future__ import annotations

from aiohttp import ClientResponseError
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    aiohttp_client,
    config_entry_oauth2_flow,
    device_registry as dr,
)

from .api import NetatmoEnergyAuth
from .const import DOMAIN, VALVE_TYPES
from .coordinator import NetatmoEnergyConfigEntry, NetatmoEnergyCoordinator
from .entity import module_device_info, relay_of

PLATFORMS: list[Platform] = [Platform.CLIMATE]


async def async_setup_entry(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry
) -> bool:
    """Set up Netatmo Energy from a config entry."""
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
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _register_devices(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _register_devices(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    coordinator: NetatmoEnergyCoordinator,
) -> None:
    """Register relay and valve devices up front.

    The relay must exist before rooms and valves point at it via
    ``via_device_id`` (its registry id), so it is registered first and its id
    kept on the coordinator for the room entities. Valves get their own
    entities in step 1b; registering them now already shows the tree.
    """
    registry = dr.async_get(hass)
    for home in coordinator.homes:
        relay_device_id: str | None = None
        if (relay := relay_of(home)) is not None:
            relay_device = registry.async_get_or_create(
                config_entry_id=entry.entry_id, **module_device_info(relay)
            )
            relay_device_id = relay_device.id
            coordinator.relay_device_ids[home.entity_id] = relay_device_id
        for module in home.modules.values():
            if module.device_type in VALVE_TYPES:
                registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    **module_device_info(module, relay_device_id),
                )


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removing devices that Netatmo no longer reports."""
    known: set[str] = set()
    for home in entry.runtime_data.homes:
        known.update(home.modules)
        known.update(f"{home.entity_id}-{room_id}" for room_id in home.rooms)
    return not any(
        identifier[0] == DOMAIN and identifier[1] in known
        for identifier in device_entry.identifiers
    )
