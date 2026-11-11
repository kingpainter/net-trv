# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.10.0] - 2026-11-11

Quality scale: Silver.

### Added
- Config flow tests the account before creating or updating the entry: it
  reads `/homesdata` with the new token and aborts with `cannot_connect`
  (API error, network error or timeout) or `no_energy_home` (no relay on the
  account). Quality scale rule `test-before-configure`.
- System health links to dev.netatmo.com/apps (`manage_url`).
- `"quality_scale": "silver"` in the manifest.

### Changed
- System health reads loaded entries with `async_loaded_entries`.

### Tests
- Coverage 92 % → 97 % (95 tests): coordinator error mapping, relay outage,
  webhook merge errors; webhook URL choice (Nabu Casa, external URL, no
  HTTPS), failed registration and bad bodies; API call counter; reauth and
  the new config flow aborts.

## [0.9.2] - 2026-11-10

### Docs
- README shows the NET-TRV icon at the top; quality scale badge targets
  Platinum.
- `quality_scale.yaml` covers all 54 rules (Bronze to Platinum) with status;
  remaining work planned for 0.10.0 (Silver) and 0.11.0 (Gold).

## [0.9.1] - 2026-11-10

### Added
- System health: Netatmo reachability, API calls last hour, webhook state,
  last event, poll interval, valves online, room history and weather status.

### Fixed
- hassfest: requirement is now `pyatmo>=9.9.0` (minimum version) because
  Home Assistant itself ships pyatmo; tests still pin 9.9.0.

### Changed
- Repository renamed to `kingpainter/net-trv`; manifest and README links
  updated. Valve position range in README corrected to ~1470–2450.

## [0.9.0] - 2026-11-10

### Changed (breaking)
- Domain renamed from `netatmo_energy` to `net_trv`; the integration now
  lives in `custom_components/net_trv`. Home Assistant treats it as a new
  integration: add the application credentials and set it up again, set the
  new webhook URL on the Netatmo app and sign in once more. Entity ids,
  actions (`net_trv.*`) and statistic ids (`net_trv:<room_id>_...`) change.
- Manifest points to `kingpainter/netatmo-energy`; `@kingpainter` is code
  owner.

### Docs
- README: badges, options table, removal instructions, actions under the new
  domain.

## [0.8.1] - 2026-11-10

### Fixed
- Startup no longer re-registers a webhook Netatmo already has. Re-adding it
  made Netatmo answer "conflict", and pyatmo then deleted and re-added the
  webhook: two delete/activate pairs on every restart. One extra GET instead.
  The `register_webhook` action still forces a fresh registration.

## [0.8.0] - 2026-11-10

### Added
- Away and frost guard temperatures of the active schedule as number
  entities on the relay.
- Actions `set_schedule_temperature` (room temperature in a schedule zone),
  `rename_schedule` and `create_schedule` (copy of an existing schedule).
- Schedule edits read the schedule fresh and sync it back whole, keeping its
  name; pyatmo's sync helper is not used because it renames to "Default".

## [0.7.0] - 2026-11-10

### Added
- Room history from `/getroommeasure` imported hourly as external long-term
  statistics: temperature (mean/min/max) and setpoint per room. The first run
  backfills 30 days; later runs only ask for new hours.
- Option to switch the history import off; diagnostics show its state.

## [0.6.0] - 2026-11-10

### Added
- Heating schedule select on the relay device (`switchhomeschedule`).
- Actions matching core `netatmo`: `set_schedule`,
  `set_preset_mode_with_end_datetime` (also `boost`),
  `set_temperature_with_end_datetime`, `set_temperature_with_time_period`,
  `clear_temperature_setting`, `register_webhook`, `unregister_webhook`.

### Fixed
- Stale device cleanup uses `async_remove_device`; removing a config entry
  from a device is deprecated in HA 2026.10 and stops working in 2027.8.

## [0.5.0] - 2026-11-10

### Added
- Public weather from nearby Netatmo stations (`/getpublicdata`) on a
  separate service device: temperature, humidity, pressure, precipitation
  (now, last hour, last 24 h), wind speed, gusts, wind direction and the
  number of stations. Values are the median of the area; wind direction is a
  circular mean. Polled every 10 minutes on its own coordinator.
- Options flow to choose the area (circle on a map, default 1 km around the
  home) or switch public weather off; saving reloads the entry.
- Diagnostics: public weather block (area size, station counts, values).

## [0.4.1] - 2026-11-10

### Fixed
- Repeat filter only drops duplicates within 2 minutes. Before, a genuine
  later repeat of a home mode change (which carries no timestamp) was
  ignored until the next poll.
- Setpoint changes (`display_change`, no timestamp) are now confirmed with a
  debounced `/homestatus` read, so a late or reordered delivery cannot leave a
  room on a stale setpoint.

## [0.4.0] - 2026-11-10

### Added
- Real-time updates: verified webhook events are merged into the model as
  they arrive (pyatmo `process_webhook`), so climate and sensors update within
  seconds instead of on the next poll:
  - measured room temperature (`temperature_variation_event`,
    `setpoint_event`), 0.1 °C resolution
  - heating demand and `hvac_action` (`heating_power_request_event`)
  - setpoint, mode and end time (`display_change`)
  - home mode (`home_event_changed`, both flat and nested shapes)
- "Valve position" sensor per valve: raw motor position from
  `motor_data_event` (webhook only; scale not documented by Netatmo), with
  `motor_cmd` attribute.
- Exact repeats and events older than the last one for the same room/valve are
  ignored (Netatmo sends no `sequence_id`).
- Adaptive polling: 300 s while webhook events arrive, back to 60 s after 15
  minutes without events.
- Webhook sensor attributes `events_applied` / `events_ignored`; diagnostics
  show applied events per type, poll interval and valve positions.

### Changed
- `iot_class` is now `cloud_push`.

## [0.3.3] - 2026-11-10

### Fixed
- Webhook calls via Nabu Casa were dropped: cloudhook requests arrive as a
  `MockRequest` without `read()`, so the handler raised before verifying or
  recording anything, and Home Assistant answered 200. The raw body is now read
  with `request.content.read()`, which works for both request types.

### Added
- Diagnostics show Netatmo's own list of registered webhooks
  (`GET /webhooks/v1`) and the last 8 characters of our webhook URL.
- Regression test that sends through a cloud `MockRequest`.

## [0.3.2] - 2026-11-10

### Added
- "Reconfigure" on the integration: sign in to Netatmo again without removing
  NET-TRV. Needed after setting the webhook URL on the Netatmo app, because
  Netatmo only links an app-level webhook to a user at sign-in.

### Fixed
- Reauth and reconfigure merge the new token into the entry instead of
  replacing it, so the webhook id and Nabu Casa cloudhook URL are kept.

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
