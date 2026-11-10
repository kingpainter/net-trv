"""Actions for NET-TRV: the core netatmo heating actions, plus webhook control.

Registered once in ``async_setup`` (quality scale: action-setup). Room actions
target our climate entities; webhook actions act on the single config entry.
"""
# VERSION = "0.6.0"

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.climate.const import DOMAIN as CLIMATE_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, service
import probatio as vol
from pyatmo.exceptions import ApiError

from .const import (
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_FROST_GUARD,
)

if TYPE_CHECKING:
    from .coordinator import NetatmoEnergyConfigEntry

ATTR_SCHEDULE_NAME = "schedule_name"
ATTR_PRESET_MODE = "preset_mode"
ATTR_END_DATETIME = "end_datetime"
ATTR_TARGET_TEMPERATURE = "target_temperature"
ATTR_TIME_PERIOD = "time_period"

SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_SET_PRESET_MODE_WITH_END_DATETIME = "set_preset_mode_with_end_datetime"
SERVICE_SET_TEMPERATURE_WITH_END_DATETIME = "set_temperature_with_end_datetime"
SERVICE_SET_TEMPERATURE_WITH_TIME_PERIOD = "set_temperature_with_time_period"
SERVICE_CLEAR_TEMPERATURE_SETTING = "clear_temperature_setting"
SERVICE_REGISTER_WEBHOOK = "register_webhook"
SERVICE_UNREGISTER_WEBHOOK = "unregister_webhook"

TIMED_PRESETS = (PRESET_AWAY, PRESET_FROST_GUARD, PRESET_BOOST)
_TEMPERATURE = vol.All(vol.Coerce(float), vol.Range(min=MIN_TEMP, max=MAX_TEMP))

_ENTITY_SERVICES: tuple[tuple[str, dict[Any, Any], str], ...] = (
    (
        SERVICE_SET_SCHEDULE,
        {vol.Required(ATTR_SCHEDULE_NAME): cv.string},
        "async_service_set_schedule",
    ),
    (
        SERVICE_SET_PRESET_MODE_WITH_END_DATETIME,
        {
            vol.Required(ATTR_PRESET_MODE): vol.In(TIMED_PRESETS),
            vol.Required(ATTR_END_DATETIME): cv.datetime,
        },
        "async_service_set_preset_with_end",
    ),
    (
        SERVICE_SET_TEMPERATURE_WITH_END_DATETIME,
        {
            vol.Required(ATTR_TARGET_TEMPERATURE): _TEMPERATURE,
            vol.Required(ATTR_END_DATETIME): cv.datetime,
        },
        "async_service_set_temperature_with_end",
    ),
    (
        SERVICE_SET_TEMPERATURE_WITH_TIME_PERIOD,
        {
            vol.Required(ATTR_TARGET_TEMPERATURE): _TEMPERATURE,
            vol.Required(ATTR_TIME_PERIOD): vol.All(
                cv.time_period, cv.positive_timedelta
            ),
        },
        "async_service_set_temperature_with_period",
    ),
    (
        SERVICE_CLEAR_TEMPERATURE_SETTING,
        {},
        "async_service_clear_temperature_setting",
    ),
)


def _loaded_entry(hass: HomeAssistant) -> NetatmoEnergyConfigEntry:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )
    return entries[0]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register NET-TRV actions."""
    for name, schema, method in _ENTITY_SERVICES:
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain=CLIMATE_DOMAIN,
            schema=schema,
            func=method,
        )

    # Imported here: webhook.py imports the coordinator, which is heavier.
    from .webhook import (  # noqa: PLC0415
        async_register_with_netatmo,
        async_unregister_with_netatmo,
    )

    async def _register(call: ServiceCall) -> None:
        entry = _loaded_entry(hass)
        if not await async_register_with_netatmo(hass, entry):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="webhook_failed",
                translation_placeholders={
                    "error": str(entry.runtime_data.webhook.last_error)
                },
            )

    async def _unregister(call: ServiceCall) -> None:
        entry = _loaded_entry(hass)
        try:
            await async_unregister_with_netatmo(entry)
        except (ApiError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="webhook_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    hass.services.async_register(DOMAIN, SERVICE_REGISTER_WEBHOOK, _register)
    hass.services.async_register(DOMAIN, SERVICE_UNREGISTER_WEBHOOK, _unregister)
