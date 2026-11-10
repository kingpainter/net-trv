"""Data coordinator for NET-TRV.

The pyatmo ``AsyncAccount`` is the single source of truth: entities read
straight from its ``Home``/``Room``/``Module`` objects. Webhook events are
merged into it as they arrive (phase 2); polling reconciles, at 300 s while
the webhook is alive and 60 s when it has gone quiet.
"""
# VERSION = "0.6.0"

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from time import monotonic
from typing import TYPE_CHECKING, Any

from aiohttp import ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
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
from pyatmo.webhook import RefreshScope, process_webhook

if TYPE_CHECKING:
    from .weather import PublicWeatherCoordinator

from .api import NetatmoEnergyAuth
from .const import (
    CONFIRM_DELAY_SECONDS,
    DOMAIN,
    GATEWAY_TYPES,
    STATUS_INTERVAL,
    THROTTLED_INTERVAL,
    TOPOLOGY_INTERVAL,
    WEBHOOK_BUFFER_SIZE,
    WEBHOOK_INTERVAL,
    WEBHOOK_STALE_AFTER,
)
from .realtime import (
    DISPLAY_CHANGE,
    LIFECYCLE_PUSH_TYPES,
    EventFilter,
    ValvePosition,
    normalize_payload,
    valve_position,
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


@dataclass
class WebhookState:
    """What we know about the Netatmo webhook (entity + diagnostics)."""

    registered: bool = False
    deleted_by_netatmo: bool = False
    last_error: str | None = None
    last_event_at: datetime | None = None
    events_received: int = 0
    rejected_signatures: int = 0
    events_applied: int = 0
    events_ignored: int = 0
    applied_by_type: dict[str, int] = field(default_factory=dict)
    recent: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=WEBHOOK_BUFFER_SIZE)
    )

    @property
    def active(self) -> bool:
        """Registered with Netatmo and not removed again."""
        return self.registered and not self.deleted_by_netatmo

    @property
    def fresh(self) -> bool:
        """Active and an event arrived recently enough to trust it."""
        return (
            self.active
            and self.last_event_at is not None
            and dt_util.utcnow() - self.last_event_at < WEBHOOK_STALE_AFTER
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
        self.webhook = WebhookState()
        # Set by async_setup_entry; the webhook needs its client secret.
        self.oauth_implementation: object | None = None
        self._topology_at: float | None = None
        # Public weather has its own coordinator (set in __init__.py).
        self.weather: PublicWeatherCoordinator | None = None
        self._confirm_unsub: CALLBACK_TYPE | None = None
        self._unreachable_logged: set[str] = set()
        self._event_filter = EventFilter()
        # module id -> last valve motor report (webhook only, not in /homestatus)
        self.valve_positions: dict[str, ValvePosition] = {}

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
            self.update_interval = self._poll_interval()

    def _poll_interval(self) -> timedelta:
        """Poll slowly while the webhook delivers, fast when it is quiet."""
        return WEBHOOK_INTERVAL if self.webhook.fresh else STATUS_INTERVAL

    async def async_apply_webhook(self, payload: dict[str, Any]) -> None:
        """Merge one verified webhook payload into the model."""
        if payload.get("push_type") in LIFECYCLE_PUSH_TYPES:
            return
        payload = normalize_payload(payload)
        if not self._event_filter.accept(payload):
            self.webhook.events_ignored += 1
            self.async_signal_webhook()
            return

        if (report := valve_position(payload)) is not None:
            module_id, position = report
            self.valve_positions[module_id] = position

        try:
            result = await process_webhook(self.account, payload)
        except Exception:
            # A malformed payload must never break the integration; polling
            # still reconciles.
            _LOGGER.exception("Could not apply Netatmo webhook event")
            return

        self.webhook.events_applied += 1
        key = result.event_type or str(payload.get("push_type"))
        self.webhook.applied_by_type[key] = self.webhook.applied_by_type.get(key, 0) + 1
        if RefreshScope.TOPOLOGY in result.refresh_scope:
            self._topology_at = None
        # Setpoints only come from display_change, which has no timestamp, so
        # order cannot be checked; a debounced /homestatus read settles it.
        if result.refresh_scope or payload.get("push_type") == DISPLAY_CHANGE:
            self.async_schedule_confirm()
        # The fresh webhook can slow polling down right away.
        self.update_interval = self._poll_interval()
        self.async_update_listeners()

    @callback
    def async_signal_webhook(self) -> None:
        """Tell entities that the webhook state changed."""
        self.async_update_listeners()

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
