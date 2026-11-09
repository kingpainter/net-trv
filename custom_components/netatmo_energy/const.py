"""Constants for the Netatmo Energy integration."""
# VERSION = "0.2.0"

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "netatmo_energy"
VERSION: Final = "0.2.0"
MANUFACTURER: Final = "Netatmo"

OAUTH2_AUTHORIZE: Final = "https://api.netatmo.com/oauth2/authorize"
OAUTH2_TOKEN: Final = "https://api.netatmo.com/oauth2/token"

# Only the energy scopes: keeps the consent screen honest and keeps this
# app's per-user rate limit (500 calls/hour) free of camera/weather traffic.
API_SCOPES: Final = ("read_thermostat", "write_thermostat")

# Gateway types this integration manages. NAPlug is the relay that bridges
# the NRV valves; homes without one are ignored.
GATEWAY_TYPES: Final = frozenset({"NAPlug"})
VALVE_TYPES: Final = frozenset({"NRV"})

# Polling. 1a has no webhook yet, so /homestatus runs at the "webhook dead"
# cadence from the plan. Topology (/homesdata) changes rarely.
STATUS_INTERVAL: Final = timedelta(seconds=60)
THROTTLED_INTERVAL: Final = timedelta(seconds=300)
TOPOLOGY_INTERVAL: Final = timedelta(hours=3)
# Delay before re-reading state after a write, so Netatmo has applied it.
CONFIRM_DELAY_SECONDS: Final = 5

# Netatmo per-user limits (dev.netatmo.com/guideline): 50 per 10 s, 500 per hour.
RATE_LIMIT_PER_HOUR: Final = 500
RATE_LIMIT_WINDOW_SECONDS: Final = 3600

# Valve setpoint range (NRV display range is 5-30 C; Netatmo accepts 7-30).
MIN_TEMP: Final = 7.0
MAX_TEMP: Final = 30.0
TEMP_STEP: Final = 0.5

# Netatmo setpoint modes (room level, /homestatus therm_setpoint_mode).
NA_MODE_SCHEDULE: Final = "schedule"
NA_MODE_HOME: Final = "home"
NA_MODE_MANUAL: Final = "manual"
NA_MODE_MAX: Final = "max"
NA_MODE_AWAY: Final = "away"
NA_MODE_FROSTGUARD: Final = "hg"
NA_MODE_OFF: Final = "off"

# Climate presets exposed to Home Assistant.
PRESET_SCHEDULE: Final = "schedule"
PRESET_AWAY: Final = "away"
PRESET_FROST_GUARD: Final = "frost_guard"
PRESET_BOOST: Final = "boost"
PRESETS: Final = (PRESET_SCHEDULE, PRESET_AWAY, PRESET_FROST_GUARD, PRESET_BOOST)
