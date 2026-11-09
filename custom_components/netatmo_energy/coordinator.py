"""Data coordinator for Netatmo Energy.

The pyatmo ``AsyncAccount`` is the single source of truth: entities read
straight from its ``Home``/``Room``/``Module`` objects. The coordinator only
decides *when* to refresh it (polling now, webhook from step 1c).
"""
# VERSION = "0.1.2"

from __future__ import annotations

from collections.abc import Awaitable
import logging
from time import monotonic

from aiohttp import ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import pyatmo
from pyatmo.enums import ScheduleType
from pyatmo.exceptions import (
    ApiError,
    ApiHomeReachabilityError,
    ApiThrottlingError,
    ApiTooManyRequestError,
)
from pyatmo.home import Home
from pyatmo.schedule import Schedule

from .api import NetatmoEnergyAuth
from .const import (
    CONFIRM_DELAY_SECONDS,
    DOMAIN,
    GATEWAY_TYPES,
    STATUS_INTERVAL,
    THROTTLED_INTERVAL,
    TOPOLOGY_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type NetatmoEnergyConfigEntry = ConfigEntry[NetatmoEnergyCoordinator]


def is_energy_home(home: Home) -> bool:
    """Return True when the home has a Netatmo relay (NAPlug)."""
    return any(module.device_type in GATEWAY_TYPES for module in home.modules.values())


def selected_therm_schedule(home: Home) -> Schedule | None:
    """Return the active heating schedule.

    pyatmo's ``Home.get_selected_schedule`` requires
    ``temperature_control_mode``, which NAPlug-only homes may not report, so
    it can return None here. We match on ``selected`` and the therm type only.
    """
    return next(
        (
            schedule
            for schedule in home.schedules.values()
            if schedule.selected and schedule.type == ScheduleType.THERM
        ),
        None,
    )


class NetatmoEnergyCoordinator(DataUpdateCoordinator[None]):
    """Keep the pyatmo account fresh and tell entities when it changed."""

    config_entry: NetatmoEnergyConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NetatmoEnergyConfigEntry,
        auth: NetatmoEnergyAuth,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=STATUS_INTERVAL,
        )
        self.auth = auth
        self.account = pyatmo.AsyncAccount(auth)
        self.home_ids: list[str] = []
        # home id -> device registry id of its relay (for via_device_id).
        self.relay_device_ids: dict[str, str] = {}
        self._topology_at: float | None = None
        self._confirm_unsub: CALLBACK_TYPE | None = None
        self._unreachable_logged: set[str] = set()

    @property
    def homes(self) -> list[Home]:
        """Return the energy homes in topology order."""
        return [self.account.homes[home_id] for home_id in self.home_ids]

    async def _async_setup(self) -> None:
        """Load the topology once before the first status refresh."""
        await self._async_guard(self._async_refresh_topology())
        if not self.home_ids:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="no_energy_home"
            )

    async def _async_update_data(self) -> None:
        """Refresh topology when stale, then the status of every energy home."""
        await self._async_guard(self._async_refresh_all())

    async def _async_refresh_all(self) -> None:
        if (
            self._topology_at is None
            or monotonic() - self._topology_at > TOPOLOGY_INTERVAL.total_seconds()
        ):
            await self._async_refresh_topology()

        for home_id in self.home_ids:
            try:
                await self.account.async_update_status(home_id)
            except ApiHomeReachabilityError:
                # The relay is offline: pyatmo has already marked its modules
                # unreachable, so entities go unavailable. Not a coordinator
                # failure; log once per outage.
                if home_id not in self._unreachable_logged:
                    _LOGGER.warning("Netatmo relay in home %s is unreachable", home_id)
                    self._unreachable_logged.add(home_id)
            else:
                if home_id in self._unreachable_logged:
                    _LOGGER.info("Netatmo relay in home %s is reachable again", home_id)
                    self._unreachable_logged.discard(home_id)

    async def _async_refresh_topology(self) -> None:
        await self.account.async_update_topology()
        self.home_ids = [
            home_id
            for home_id, home in self.account.homes.items()
            if is_energy_home(home)
        ]
        self._topology_at = monotonic()

    async def _async_guard(self, call: Awaitable[None]) -> None:
        """Translate pyatmo/aiohttp errors to coordinator semantics."""
        try:
            await call
        except ClientResponseError as err:
            # Raised by the OAuth2 session when the refresh token is refused.
            if 400 <= err.status < 500:
                raise ConfigEntryAuthFailed from err
            raise UpdateFailed(f"Netatmo request failed: {err}") from err
        except (ApiThrottlingError, ApiTooManyRequestError) as err:
            # Back off until a call succeeds again.
            self.update_interval = THROTTLED_INTERVAL
            raise UpdateFailed(f"Netatmo rate limit reached: {err}") from err
        except ApiError as err:
            if err.status in (401, 403):
                raise ConfigEntryAuthFailed from err
            raise UpdateFailed(f"Netatmo API error: {err}") from err
        except TimeoutError as err:
            raise UpdateFailed("Timeout talking to Netatmo") from err
        else:
            self.update_interval = STATUS_INTERVAL

    @callback
    def async_schedule_confirm(self) -> None:
        """Re-read state shortly after a write, so the UI reflects Netatmo."""
        if self._confirm_unsub is not None:
            self._confirm_unsub()

        @callback
        def _confirm(_now: object) -> None:
            self._confirm_unsub = None
            self.hass.async_create_task(self.async_request_refresh())

        self._confirm_unsub = async_call_later(
            self.hass, CONFIRM_DELAY_SECONDS, _confirm
        )

    async def async_shutdown(self) -> None:
        """Cancel a pending confirm refresh."""
        if self._confirm_unsub is not None:
            self._confirm_unsub()
            self._confirm_unsub = None
        await super().async_shutdown()
