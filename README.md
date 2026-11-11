<p align="center"><img src="custom_components/net_trv/brand/icon@2x.png" width="160" alt="NET-TRV"></p>

# NET-TRV

[![Release](https://img.shields.io/github/v/release/kingpainter/net-trv?include_prereleases&sort=semver)](https://github.com/kingpainter/net-trv/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.10%2B-18BCF2?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Validate](https://github.com/kingpainter/net-trv/actions/workflows/validate.yml/badge.svg)](https://github.com/kingpainter/net-trv/actions/workflows/validate.yml)
[![Lint and tests](https://github.com/kingpainter/net-trv/actions/workflows/tests.yml/badge.svg)](https://github.com/kingpainter/net-trv/actions/workflows/tests.yml)
[![Quality scale](https://img.shields.io/badge/quality%20scale-silver-C0C0C0)](quality_scale.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

NET-TRV is a Home Assistant custom integration for the Netatmo thermostat
relay (NAPlug) and Netatmo smart radiator valves (NRV). It focuses only on
heating and covers what the Netatmo Energy API offers for those devices, with
real-time updates through Netatmo's webhook.

> Not affiliated with or endorsed by Netatmo.

## What it does

- One climate entity per Netatmo room, with `hvac_action` derived from the
  valves' heating demand (works on district heating, where there is no boiler
  behind the relay).
- Real-time state from Netatmo's webhook (signature-verified); polling only
  reconciles, every 5 minutes while events arrive, every minute when not.
- Everything the core `netatmo` integration does for heating, with the same
  action names, plus schedule editing, room history, valve position and
  public weather.
- Device tree: relay (hub) → one device per room with a valve. The room
  device carries the valve's serial number and firmware; rooms without a
  valve are skipped.
- Uses its own Netatmo app with only `read_thermostat` and `write_thermostat`,
  so it does not share rate limits or the webhook with the core `netatmo`
  integration. Typical use: about 15 API calls per hour of the 500 allowed.

## Supported devices

| Device | Netatmo type | Supported |
| --- | --- | --- |
| Thermostat relay (the bridge in the room) | NAPlug | ✅ as the hub device |
| Smart radiator valve | NRV | ✅ one device per room |
| Smart thermostat | NATherm1 | ❌ not tested; no hardware to test against |
| OpenTherm thermostat, boiler data | OTM / BNS | ❌ (`setstate`, `getmeasure`) |
| Weather station, cameras, smoke alarms, other Netatmo devices | — | ❌ use the core `netatmo` integration |

The relay is found automatically on the network (HomeKit announcement
"Netatmo Relay"); Home Assistant then offers NET-TRV under **Discovered**.
The relay's own HomeKit pairing (HomeKit Device integration) is still
offered, and both can be used side by side.

## Use cases

- **District heating or any home without a boiler signal.** `hvac_action`
  comes from the valves' heating demand, so a room shows *heating* when its
  valve opens, not when a boiler fires.
- **Replace the core `netatmo` integration for heating.** Same action names
  and climate behaviour, but real-time through the webhook, on your own
  Netatmo app and rate limit.
- **Heating automations that react in seconds**, e.g. pause heating when a
  window opens or boost a room on a button press.
- **Follow the house over time**: room temperature and setpoint history in
  long-term statistics, including the 30 days before installation.
- **Edit heating schedules from Home Assistant**: zone temperatures,
  away / frost guard temperatures, copies of schedules for the season.
- **Local outdoor weather without a station**: the median of public Netatmo
  stations around your home.

## Features (0.11.0)

| Feature | Status |
| --- | --- |
| OAuth2 config flow with reauth and reconfigure; checks the account for a relay before setup | ✅ |
| Discovery of the relay on the network (HomeKit announcement) | ✅ |
| Climate per room (auto / heat / off, presets) | ✅ |
| Real-time state from webhook events (temperature, demand, setpoint, home mode, schedule) | ✅ |
| Valve and relay diagnostics (battery, signal, firmware, connection, valve position) | ✅ |
| Heating schedule select and the core `netatmo` heating actions (with end time) | ✅ |
| Schedule editing: away / frost guard temperatures, zone temperatures, rename, create | ✅ |
| Room history (`getroommeasure`) imported as long-term statistics | ✅ |
| Public weather from nearby Netatmo stations (median of the area) | ✅ |
| Diagnostics with the last 50 raw webhook events (personal data redacted) | ✅ |
| System health: Netatmo reachability, API calls, webhook, valves online | ✅ |

## Requirements

- Home Assistant 2026.10 or newer.
- A Netatmo developer app from [dev.netatmo.com](https://dev.netatmo.com/apps)
  (client id and client secret), used only by this integration.
- A Netatmo thermostat relay (NAPlug) with radiator valves (NRV).
- For real-time updates: Home Assistant Cloud (Nabu Casa) or an external
  HTTPS URL on port 443.

## Installation

### HACS

1. HACS → Integrations → ⋮ → Custom repositories → add
   `https://github.com/kingpainter/net-trv` as *Integration*.
2. Install **NET-TRV** and restart Home Assistant.

### Manual

Copy `custom_components/net_trv` to `config/custom_components/` and restart
Home Assistant.

## Configuration

1. In your Netatmo app on dev.netatmo.com, set the redirect URI to
   `https://my.home-assistant.io/redirect/oauth`.
2. Settings → Devices & services → ⋮ → **Application credentials** → add the
   client id and secret for *NET-TRV*.
3. Settings → Devices & services → **Add integration** → *NET-TRV* and
   sign in to Netatmo.
4. Set up the webhook (below) for real-time updates.

### Options

Settings → Devices & services → NET-TRV → **Configure**:

| Option | Default | What it does |
| --- | --- | --- |
| Import room history | On | Hourly room temperature and setpoint into long-term statistics. |
| Show public weather | On | Weather from public Netatmo stations near you. |
| Area | 1 km around home | Circle of stations used for public weather. |

## Entities

| Entity | Description |
| --- | --- |
| `climate.<room>` | Room thermostat. Attributes: `heating_power_request`, `open_window`, `anticipating`, `netatmo_setpoint_mode`, `setpoint_end_time`. |
| `sensor.<room>_battery` / `_battery_state` | Valve battery (% is approximate, derived from Netatmo's state). |
| `sensor.<room>_radio_signal` | Valve radio signal to the relay, dBm. Disabled by default. |
| `sensor.<room>_firmware` | Valve firmware revision. Disabled by default. |
| `sensor.<room>_valve_position` | Disabled by default. Raw valve motor position from the webhook (lower = more open, observed ~1470–2450; scale undocumented). Attribute `motor_cmd`. |
| `binary_sensor.<room>_connectivity` | Valve reachable by the relay. |
| `select.<relay>_heating_schedule` | Active Netatmo heating schedule (e.g. Sommer / Vinter); selecting one switches the whole home. |
| `number.<relay>_away_temperature` / `_frost_guard_temperature` | Away and frost guard temperatures of the active schedule. |
| `sensor.<relay>_wi_fi_signal` / `_firmware` | Relay Wi-Fi signal (dBm) and firmware. Disabled by default. |
| `sensor.<relay>_valves_online` | Reachable valves; attributes `total` and `offline`. |
| `binary_sensor.<relay>_connectivity` | Relay reachable by Netatmo. |
| `binary_sensor.<relay>_webhook` | Webhook registered with Netatmo; attributes `events_received`, `events_applied`, `events_ignored`, `last_event`, `rejected_signatures`, `last_error`. |
| `sensor.public_weather_*` | Temperature, humidity, pressure, precipitation (now, last hour, last 24 h), wind speed, gusts, wind direction, stations in area. |

Entities marked *disabled by default* are diagnostic and rarely needed;
enable them under the device's entity list. Entities already enabled on an
existing installation stay enabled.

### Actions

| Action | What it does |
| --- | --- |
| `net_trv.set_schedule` | Switch the home to a heating schedule by name. |
| `net_trv.set_preset_mode_with_end_datetime` | Away / frost guard (whole home) or boost (room) until a time. |
| `net_trv.set_temperature_with_end_datetime` | Manual setpoint until a time. |
| `net_trv.set_temperature_with_time_period` | Manual setpoint for a duration. |
| `net_trv.clear_temperature_setting` | Room back to the schedule. |
| `net_trv.set_schedule_temperature` | This room's temperature in one zone (e.g. Comfort) of a schedule. |
| `net_trv.rename_schedule` | Rename a schedule. |
| `net_trv.create_schedule` | New schedule as a copy of an existing one. |
| `net_trv.register_webhook` / `unregister_webhook` | Start or stop Netatmo's real-time events. |

The first five target NET-TRV climate entities and match the core `netatmo`
actions of the same name, so automations move over by changing `netatmo.` to
`net_trv.`. Times without a time zone are Home Assistant local time.

Schedule edits read the schedule fresh from Netatmo and send it back whole
with only the requested change, because `synchomeschedule` replaces the full
schedule. Deleting schedules is not offered: the endpoint is not documented
and a delete cannot be undone; use the Netatmo app.

### Room history

Every hour NET-TRV reads each room's hourly temperature (mean, min, max) and
setpoint from Netatmo and adds them to Home Assistant's long-term statistics
as `net_trv:<room_id>_temperature` and `net_trv:<room_id>_setpoint`. The first
run imports the last 30 days, so graphs start with history from before
NET-TRV and have no gaps when Home Assistant was down. Use them in a
**Statistics graph** card. One call per room per hour; it needs the recorder.

Netatmo's API has no heating time per room; `getroommeasure` only offers
temperature, setpoint and daily min/max.

### Public weather

A **Public weather** service device shows weather from public Netatmo
stations around your home. Each value is the median of the stations in the
area (wind direction a circular mean), and its `stations` attribute says how
many stations reported it. It is read every 10 minutes (6 API calls per hour)
on its own coordinator, so a weather outage never affects heating.

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

NET-TRV registers the webhook through the API once Home Assistant has started
and keeps an existing registration. Netatmo allows one webhook per app, so
use a dedicated Netatmo app for this integration. Every event is checked
against the HMAC signature Netatmo sends; unsigned or wrong calls get 401.

## How data is updated

| Source | When | What |
| --- | --- | --- |
| Webhook (push) | Within about a second of a change | Room temperature, heating demand, setpoint, home mode, schedule, valve position |
| Status poll (`/homestatus`) | Every 5 min while webhook events arrive (one in the last 15 min), otherwise every 60 s | All room and module state; reconciles anything the webhook missed |
| Topology (`/homesdata`) | Every 3 hours, and after webhook events that change the home setup | Homes, rooms, modules, schedules, away / frost guard temperatures |
| After a change from Home Assistant | 5 s later | One status poll to confirm what Netatmo applied |
| Room history (`getroommeasure`) | Every hour | Hourly temperature and setpoint into long-term statistics |
| Public weather (`getpublicdata`) | Every 10 minutes | Nearby public stations |

When Netatmo rate-limits the account, polling backs off to every 5 minutes
until a call succeeds again. Webhook setpoint messages carry no timestamp, so
NET-TRV confirms them with a status poll 5 s later. Without a public HTTPS
URL there is no webhook, and the integration runs on polling only.

## Examples

Warn when a window is opened while the room is heating:

```yaml
automation:
  - alias: "Window open in the living room"
    triggers:
      - trigger: state
        entity_id: climate.living_room
        attribute: open_window
        to: true
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "🪟 Window open"
          message: "Netatmo paused the living room valve."
```

Boost the bathroom for an hour from a button:

```yaml
automation:
  - alias: "Bathroom boost"
    triggers:
      - trigger: state
        entity_id: input_button.bathroom_boost
    actions:
      - action: net_trv.set_preset_mode_with_end_datetime
        target:
          entity_id: climate.bathroom
        data:
          preset_mode: boost
          end_datetime: "{{ (now() + timedelta(hours=1)).isoformat() }}"
```

Away mode for the whole home until you are back:

```yaml
action: net_trv.set_preset_mode_with_end_datetime
target:
  entity_id: climate.living_room
data:
  preset_mode: away
  end_datetime: "2026-12-27 15:00:00"
```

Switch to the winter schedule on the first of October:

```yaml
automation:
  - alias: "Winter heating schedule"
    triggers:
      - trigger: template
        value_template: "{{ now().month == 10 and now().day == 1 }}"
    actions:
      - action: select.select_option
        target:
          entity_id: select.gang_relay_heating_schedule
        data:
          option: Vinter
```

## Removal

1. Settings → Devices & services → NET-TRV → ⋮ → **Delete**. This also
   removes the Nabu Casa cloudhook.
2. Optional: remove the application credentials and delete the Netatmo app on
   dev.netatmo.com.
3. Remove `custom_components/net_trv` (or uninstall in HACS) and restart.

Imported room history stays in long-term statistics until you delete it under
Developer tools → Statistics.

## Troubleshooting

Settings → System → Repairs → ⋮ → **System information** → NET-TRV shows whether Netatmo is reachable, API calls in the last hour, webhook state and valves online. Its link opens your apps on dev.netatmo.com, where the webhook URL is set.

Setup stops with **"Failed to connect to Netatmo"** when the account cannot be read right after sign-in (try again later), or with **"No Netatmo home with a thermostat relay"** when the account has no relay (NAPlug). Nothing is created in either case.

Download diagnostics from the integration page: it shows the webhook state,
API calls in the last hour, room history and weather status, and the last 50
raw events.

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.net_trv: debug
    pyatmo: debug
```

## Known limitations

- Netatmo only exposes data through its cloud; there is no local API.
- Valve position comes only from webhook events, as a raw motor value.
- No heating time per room and no schedule deletion (not in the API).
- The integration requires the same `pyatmo` version as Home Assistant's core
  `netatmo` integration while both are installed.

## Contributing

Bug reports, ideas and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and rules,
[SECURITY.md](SECURITY.md) for reporting security problems and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE)
