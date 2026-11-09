# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.3.1] - 2026-11-09

### Changed
- Display name is now **NET-TRV** (domain stays `netatmo_energy`, so existing
  installs, entities and credentials keep working; rename the existing entry
  in the UI if you want the new title there too).

## [0.3.0] - 2026-11-09

### Added
- Netatmo webhook (v1): registered automatically once Home Assistant has
  started, via the Nabu Casa cloudhook or, without Nabu Casa, the external
  HTTPS URL.
- Every webhook call is verified (HMAC-SHA256 of the raw body with the app's
  client secret, `X-Netatmo-secret`); bad or missing signatures get 401.
- Calls are answered with 200 at once and recorded afterwards, so Netatmo's
  5-second limit is never hit.
- "Webhook" connectivity sensor on the relay with `events_received`,
  `last_event`, `rejected_signatures` and `last_error`.
- Diagnostics download with the last 50 raw webhook payloads (personal fields
  redacted), home/room summary and API calls in the last hour.

### Notes
- Events are only recorded in this release; state still comes from polling
  every 60 s. Merging webhook events into state is phase 2.

## [0.2.0] - 2026-11-09

### Added
- Valve diagnostics on each room device: battery (%), battery state, radio
  signal (dBm), firmware and connection. Battery and firmware keep their last
  value while a valve is unreachable; the radio signal goes unavailable.
- Relay diagnostics: Wi-Fi signal (dBm), firmware, connection and "Valves
  online" (count, with `total` and `offline` attributes).
- Rooms with several valves name each valve's entities after the valve.
- English and Danish names for all new entities.

## [0.1.2] - 2026-11-09

### Changed
- One device per room with a valve: the room device holds the climate entity
  (and from 0.2.0 the valve diagnostics) and carries the valve's serial number
  and firmware. Valves are no longer separate devices.
- Rooms without a valve (e.g. the room the relay sits in) get no device and no
  climate entity.
- The relay suggests the area of the Netatmo room it is placed in (new
  installs only).

### Fixed
- Stale devices from 0.1.0/0.1.1 (per-valve devices, valve-less rooms) are
  removed automatically on startup.

## [0.1.1] - 2026-11-09

### Fixed
- Climate uses the `native_*` temperature attributes on Home Assistant
  2026.11+ (the old names stop working in 2027.11); falls back to the old
  names on 2026.10 so tests keep running until the test package ships 2026.11.
- Devices link to the relay with `via_device_id` instead of the deprecated
  `via_device` (stops working in 2027.8).

### Changed
- Minimum Home Assistant version is now 2026.10.0.
- CI runs on Python 3.14 (tests against current Home Assistant) and adds
  `mypy --strict`.
- Added `py.typed` and `quality_scale.yaml` (target: Silver).

## [0.1.0] - 2026-11-09

### Added
- OAuth2 config flow with application credentials and reauth, requesting only
  `read_thermostat` and `write_thermostat`.
- Coordinator on top of pyatmo: topology every 3 h, home status every 60 s,
  backoff to 300 s when Netatmo rate-limits, API call counter.
- Climate entity per room with `hvac_action` from heating demand.
- Device tree: relay, rooms and valves linked with `via_device`.
- English and Danish translations.
- CI: hassfest, HACS validation, ruff and pytest (Python 3.13).
