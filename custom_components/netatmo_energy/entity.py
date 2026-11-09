"""Base entities and device info for Netatmo Energy.

Device tree in Home Assistant:

    Relay (NAPlug)
    ├── Room (one per Netatmo room, holds the climate entity)
    └── Valve (NRV, one per valve, holds diagnostics from step 1b)
"""
# VERSION = "0.1.0"

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyatmo.home import Home
from pyatmo.modules.module import Module
from pyatmo.room import Room

from .const import DOMAIN, GATEWAY_TYPES, MANUFACTURER
from .coordinator import NetatmoEnergyCoordinator

MODEL_NAMES = {
    "NAPlug": "Thermostat relay",
    "NRV": "Smart radiator valve",
}


def relay_of(home: Home) -> Module | None:
    """Return the home's relay module, if any."""
    return next(
        (m for m in home.modules.values() if m.device_type in GATEWAY_TYPES), None
    )


def module_device_info(module: Module) -> DeviceInfo:
    """Device info for a physical module (relay or valve)."""
    info = DeviceInfo(
        identifiers={(DOMAIN, module.entity_id)},
        manufacturer=MANUFACTURER,
        model=MODEL_NAMES.get(str(module.device_type), str(module.device_type)),
        model_id=str(module.device_type),
        name=module.name,
        serial_number=module.entity_id,
    )
    if (firmware := getattr(module, "firmware_revision", None)) is not None:
        info["sw_version"] = str(firmware)
    if module.bridge:
        info["via_device"] = (DOMAIN, module.bridge)
    return info


def room_device_info(room: Room) -> DeviceInfo:
    """Device info for a Netatmo room (logical device under the relay)."""
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{room.home.entity_id}-{room.entity_id}")},
        manufacturer=MANUFACTURER,
        model="Room",
        name=room.name,
    )
    if (relay := relay_of(room.home)) is not None:
        info["via_device"] = (DOMAIN, relay.entity_id)
    return info


class NetatmoEnergyRoomEntity(CoordinatorEntity[NetatmoEnergyCoordinator]):
    """Base for entities that belong to a room."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NetatmoEnergyCoordinator, room: Room) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.room = room
        self._attr_device_info = room_device_info(room)

    @property
    def available(self) -> bool:
        """Unavailable when Netatmo says the room cannot be reached."""
        return super().available and self.room.reachable is not False
