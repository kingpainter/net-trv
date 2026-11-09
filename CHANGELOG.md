# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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
