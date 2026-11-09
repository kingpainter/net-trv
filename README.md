# Netatmo Energy

[![Validate](https://github.com/OWNER/netatmo-energy/actions/workflows/validate.yml/badge.svg)](https://github.com/OWNER/netatmo-energy/actions/workflows/validate.yml)
[![Lint and tests](https://github.com/OWNER/netatmo-energy/actions/workflows/tests.yml/badge.svg)](https://github.com/OWNER/netatmo-energy/actions/workflows/tests.yml)

A Home Assistant custom integration for the Netatmo thermostat relay (NAPlug)
and Netatmo smart radiator valves (NRV). It focuses only on heating and aims
to expose everything the Netatmo Energy API offers for those devices.

> Not affiliated with or endorsed by Netatmo.

## What it does

- One climate entity per Netatmo room, with `hvac_action` derived from the
  valves' heating demand (works on district heating, where there is no boiler
  behind the relay).
- Device tree: relay → rooms and valves (`via_device`).
- Uses its own Netatmo app with only `read_thermostat` and `write_thermostat`,
  so it does not share rate limits or the webhook with the core `netatmo`
  integration.

## Features (0.1.0)

| Feature | Status |
| --- | --- |
| OAuth2 config flow with reauth | ✅ |
| Climate per room (auto / heat / off, presets) | ✅ |
| Polling every 60 s with rate-limit backoff | ✅ |
| Valve and relay diagnostics (battery, RF, firmware) | Planned (0.2.0) |
| Real-time updates via Netatmo webhook | Planned (0.3.0) |
| Schedule services and room history | Planned |

## Requirements

- Home Assistant 2025.1 or newer.
- A Netatmo developer app from [dev.netatmo.com](https://dev.netatmo.com/apps)
  (client id and client secret).
- A Netatmo thermostat relay (NAPlug) with radiator valves (NRV).

## Installation

### HACS

1. HACS → Integrations → ⋮ → Custom repositories → add this repository as
   *Integration*.
2. Install **Netatmo Energy** and restart Home Assistant.

### Manual

Copy `custom_components/netatmo_energy` to `config/custom_components/` and
restart Home Assistant.

## Configuration

1. In your Netatmo app on dev.netatmo.com, set the redirect URI to
   `https://my.home-assistant.io/redirect/oauth`.
2. Settings → Devices & services → ⋮ → **Application credentials** → add the
   client id and secret for *Netatmo Energy*.
3. Settings → Devices & services → **Add integration** → *Netatmo Energy* and
   sign in to Netatmo.

## Entities

| Entity | Description |
| --- | --- |
| `climate.<room>` | Room thermostat. Attributes: `heating_power_request`, `open_window`, `anticipating`, `netatmo_setpoint_mode`, `setpoint_end_time`. |

### Modes and presets

| Home Assistant | Netatmo |
| --- | --- |
| `auto` | Room follows the home schedule |
| `heat` + temperature | Manual setpoint (home default duration) |
| `off` | Manual setpoint at 7 °C (valves have no real off) |
| Preset `boost` | Room `max` |
| Preset `away` / `frost_guard` | **Home-wide** away / frost guard |
| Preset `schedule` | Home back to schedule, room follows it |

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
