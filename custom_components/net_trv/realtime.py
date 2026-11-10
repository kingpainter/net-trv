"""Phase 2: turn Netatmo webhook payloads into model updates.

pyatmo's ``process_webhook`` already merges most energy events into the
model (measured temperature, heating demand, setpoints via ``display_change``,
home mode). This module covers what it does not, as seen in real payloads
from a NAPlug + NRV home (2026-11-10):

* ``home_event_changed`` arrives in two shapes; in the nested one the fields
  live under ``extra_params`` and pyatmo does not recognise it.
* ``motor_data_event`` carries the valve motor position, which pyatmo only
  stores on shutters, not on valves.
* Events carry no ``sequence_id``; Netatmo may repeat or reorder them, so we
  drop exact repeats within a short window and events older than the last one
  for the same target. The window matters: ``home_event_changed`` has no
  timestamp, so a later, genuine repeat of the same mode change must pass.
* ``display_change`` (the only source of setpoints) has no timestamp either;
  the coordinator confirms those against ``/homestatus`` instead.
"""
# VERSION = "0.9.2"

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from time import monotonic
from typing import Any

HOME_EVENT_CHANGED = "home_event_changed"
DEVICE_EVENT = "device_event"
DISPLAY_CHANGE = "display_change"
MOTOR_DATA_EVENT = "motor_data_event"
LIFECYCLE_PUSH_TYPES = frozenset({"webhook_activation", "webhook_deletion"})


@dataclass(frozen=True)
class ValvePosition:
    """Last motor report from a valve (raw Netatmo units)."""

    position: int
    motor_cmd: int | None
    ts: int | None


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Lift nested ``home_event_changed`` fields to the top level.

    Netatmo sends this push type both flat (``event_type`` at the top) and
    nested (everything under ``extra_params``); pyatmo only reads the flat one.
    """
    extra = payload.get("extra_params")
    if (
        payload.get("push_type") == HOME_EVENT_CHANGED
        and not payload.get("event_type")
        and isinstance(extra, dict)
    ):
        return {**payload, **extra}
    return payload


def _fields(payload: dict[str, Any]) -> dict[str, Any]:
    extra = payload.get("extra_params")
    return extra if isinstance(extra, dict) else payload


def event_target(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Return (event_type, room or module id) for ordering, if any."""
    fields = _fields(payload)
    event_type = fields.get("event_type") or payload.get("event_type")
    target = fields.get("room_id") or fields.get("module_id")
    if not event_type or not target:
        return None
    return str(event_type), str(target)


def event_ts(payload: dict[str, Any]) -> int | None:
    """Return the event's own timestamp (Netatmo ``ts``), if any."""
    ts = _fields(payload).get("ts")
    return int(ts) if isinstance(ts, int | float) else None


def valve_position(payload: dict[str, Any]) -> tuple[str, ValvePosition] | None:
    """Extract a valve motor report from a ``motor_data_event``."""
    if payload.get("push_type") != DEVICE_EVENT:
        return None
    fields = _fields(payload)
    if fields.get("event_type") != MOTOR_DATA_EVENT:
        return None
    module_id = fields.get("module_id")
    position = fields.get("current_position")
    if not module_id or not isinstance(position, int | float):
        return None
    cmd = fields.get("motor_cmd")
    return str(module_id), ValvePosition(
        position=int(position),
        motor_cmd=int(cmd) if isinstance(cmd, int | float) else None,
        ts=event_ts(payload),
    )


class EventFilter:
    """Drop repeats within a short window and out-of-order events per target."""

    def __init__(
        self, window: float = 120.0, clock: Callable[[], float] = monotonic
    ) -> None:
        """Initialize the filter."""
        self._window = window
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._last_ts: dict[tuple[str, str], int] = {}

    def accept(self, payload: dict[str, Any]) -> bool:
        """Return True when the payload is new and not older than its target."""
        now = self._clock()
        self._seen = {
            digest: at for digest, at in self._seen.items() if now - at < self._window
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        if digest in self._seen:
            return False
        self._seen[digest] = now

        target, ts = event_target(payload), event_ts(payload)
        if target is None or ts is None:
            return True
        if ts < self._last_ts.get(target, ts):
            return False
        self._last_ts[target] = ts
        return True
