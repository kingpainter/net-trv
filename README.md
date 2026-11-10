# NET-TRV

[![Validate](https://github.com/OWNER/netatmo-energy/actions/workflows/validate.yml/badge.svg)](https://github.com/OWNER/netatmo-energy/actions/workflows/validate.yml)
[![Lint and tests](https://github.com/OWNER/netatmo-energy/actions/workflows/tests.yml/badge.svg)](https://github.com/OWNER/netatmo-energy/actions/workflows/tests.yml)

NET-TRV is a Home Assistant custom integration for the Netatmo thermostat
relay (NAPlug) and Netatmo smart radiator valves (NRV). It focuses only on
heating and aims to expose everything the Netatmo Energy API offers for those
devices.

> Not affiliated with or endorsed by Netatmo.

## What it does

- One climate entity per Netatmo room, with `hvac_action` derived from the
  valves' heating demand (works on district heating, where there is no boiler
  behind the relay).
- Device tree: relay (hub) → one device per room with a valve. The room
  device carries the valve's serial number and firmware; rooms without a
  valve are skipped.
- Uses its own Netatmo app with only `read_thermostat` and `write_thermostat`,
  so it does not share rate limits or the webhook with the core `netatmo`
  integration.

## Features (0.7.0)

| Feature | Status |
| --- | --- |
| OAuth2 config flow with reauth | ✅ |
| Climate per room (auto / heat / off, presets) | ✅ |
| Polling 60 s (300 s while the webhook delivers) with rate-limit backoff | ✅ |
| Valve and relay diagnostics (battery, signal, firmware, connection) | ✅ |
| Netatmo webhook: registered, signature-verified, events recorded | ✅ |
| Real-time state from webhook events (temperature, demand, setpoint, home mode, valve position) | ✅ |
| Public weather from nearby Netatmo stations (median of the area) | ✅ |
| Heating schedule select and the core netatmo heating actions (with end time) | ✅ |
| Room history (`getroommeasure`) imported as long-term statistics | ✅ |
| Schedule editing, away / frost guard temperatures | Planned (0.8.0) |

## Requirements

- Home Assistant 2026.10 or newer.
- A Netatmo developer app from [dev.netatmo.com](https://dev.netatmo.com/apps)
  (client id and client secret).
- A Netatmo thermostat relay (NAPlug) with radiator valves (NRV).

## Installation

### HACS

1. HACS → Integrations → ⋮ → Custom repositories → add this repository as
   *Integration*.
2. Install **NET-TRV** and restart Home Assistant.

### Manual

Copy `custom_components/netatmo_energy` to `config/custom_components/` and
restart Home Assistant.

## Configuration

1. In your Netatmo app on dev.netatmo.com, set the redirect URI to
   `https://my.home-assistant.io/redirect/oauth`.
2. Settings → Devices & services → ⋮ → **Application credentials** → add the
   client id and secret for *NET-TRV*.
3. Settings → Devices & services → **Add integration** → *NET-TRV* and
   sign in to Netatmo.

## Entities

| Entity | Description |
| --- | --- |
| `climate.<room>` | Room thermostat. Attributes: `heating_power_request`, `open_window`, `anticipating`, `netatmo_setpoint_mode`, `setpoint_end_time`. |
| `sensor.<room>_battery` / `_battery_state` | Valve battery (% is approximate, derived from Netatmo's state). |
| `sensor.<room>_radio_signal` | Valve radio signal to the relay, dBm. |
| `sensor.<room>_firmware` | Valve firmware revision. |
| `binary_sensor.<room>_connectivity` | Valve reachable by the relay. |
| `sensor.<room>_valve_position` | Raw valve motor position from the webhook (lower = more open, observed ~1700–2300; scale undocumented). Attribute `motor_cmd`. |
| `sensor.<relay>_wi_fi_signal` / `_firmware` | Relay Wi-Fi signal (dBm) and firmware. |
| `sensor.<relay>_valves_online` | Reachable valves; attributes `total` and `offline`. |
| `binary_sensor.<relay>_connectivity` | Relay reachable by Netatmo. |
| `select.<relay>_heating_schedule` | Active Netatmo heating schedule (e.g. Sommer / Vinter); selecting one switches the whole home. |
| `binary_sensor.<relay>_webhook` | Webhook registered with Netatmo; attributes `events_received`, `last_event`, `rejected_signatures`, `last_error`. |

### Actions

| Action | What it does |
| --- | --- |
| `netatmo_energy.set_schedule` | Switch the home to a heating schedule by name. |
| `netatmo_energy.set_preset_mode_with_end_datetime` | Away / frost guard (whole home) or boost (room) until a time. |
| `netatmo_energy.set_temperature_with_end_datetime` | Manual setpoint until a time. |
| `netatmo_energy.set_temperature_with_time_period` | Manual setpoint for a duration. |
| `netatmo_energy.clear_temperature_setting` | Room back to the schedule. |
| `netatmo_energy.register_webhook` / `unregister_webhook` | Start or stop Netatmo's real-time events. |

The first five target NET-TRV climate entities and match the core `netatmo`
actions of the same name, so automations can be moved by changing the domain.
Times without a time zone are Home Assistant local time.

### Room history

Every hour NET-TRV reads each room's hourly temperature (mean, min, max) and
setpoint from Netatmo and adds them to Home Assistant's long-term statistics
as `netatmo_energy:<room_id>_temperature` and `netatmo_energy:<room_id>_setpoint`.
The first run imports the last 30 days, so graphs start with history from
before NET-TRV and have no gaps when Home Assistant was down. Use them in a
**Statistics graph** card. One call per room per hour; it needs the recorder
and can be switched off under **Configure**.

Netatmo's API has no heating time per room; `getroommeasure` only offers
temperature, setpoint and daily min/max.

### Public weather

A **Public weather** service device shows weather from public Netatmo
stations around your home: temperature, humidity, pressure, precipitation
(now, last hour, last 24 hours), wind speed, gusts and wind direction. Each
value is the median of the stations in the area (wind direction a circular
mean), and its `stations` attribute says how many stations reported it.

Set the area under **Settings → Devices & services → NET-TRV → Configure**.
The default is a 1 km circle around Home Assistant's home location; it can
also be switched off there. It is read every 10 minutes (6 API calls per
hour) on its own coordinator, so a weather outage never affects heating.

### Modes and presets

| Home Assistant | Netatmo |
| --- | --- |
| `auto` | Room follows the home schedule |
| `heat` + temperature | Manual setpoint (home default duration) |
| `off` | Manual setpoint at 7 °C (valves have no real off) |
| Preset `boost` | Room `max` |
| Preset `away` / `frost_guard` | **Home-wide** away / frost guard |
| Preset `schedule` | Home back to schedule, room follows it |

## Webhook

Netatmo only delivers events to a webhook URL that is set on your Netatmo app
when you sign in:

1. Settings → Home Assistant Cloud → Webhooks: copy the NET-TRV URL (or use
   `https://<your external URL>/api/webhook/<id>` without Nabu Casa).
2. dev.netatmo.com → your app → paste it as *Webhook URL* and save.
3. Settings → Devices & services → NET-TRV → ⋮ → **Reconfigure** and sign in
   to Netatmo again.

The integration also registers the webhook through the API once Home
Assistant has started. It uses your Nabu Casa cloudhook when available, otherwise your
external URL, which must be HTTPS on port 443. Netatmo allows one webhook per
app, so use a dedicated Netatmo app for this integration.

Download diagnostics from the integration page to see the last 50 raw events.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.netatmo_energy: debug
    pyatmo: debug
```

## Known limitations

- Netatmo only exposes data through its cloud; there is no local API.
- Valve position is not available; `heating_power_request` (0–100 %) is the
  closest signal.
- The integration requires the same `pyatmo` version as Home Assistant's core
  `netatmo` integration while both are installed.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT
