# Contributing to NET-TRV

Thanks for helping. NET-TRV is a Home Assistant custom integration for the
Netatmo thermostat relay (NAPlug) and smart radiator valves (NRV). This page
explains how to report problems, propose changes and get a pull request
merged.

## Scope

In scope: everything the Netatmo Energy API offers for the relay and the
valves, the webhook, schedules, room history and public weather.

Out of scope: other Netatmo products (cameras, weather stations, the smart
thermostat NATherm1 as a primary device, Legrand/BTicino/Somfy). Use the core
`netatmo` integration for those.

## Reporting a bug

1. Check the [known limitations](README.md#known-limitations) and open issues.
2. Open a **Bug report** issue and fill in the form.
3. Attach diagnostics (Settings → Devices & services → NET-TRV → ⋮ →
   Download diagnostics). Tokens, the webhook id and URL, your e-mail and home
   name are redacted automatically.
4. **Never** paste your client id, client secret, tokens or webhook URL in an
   issue. Security problems go through [SECURITY.md](SECURITY.md).

## Development setup

Requirements: Python 3.14 (older versions install an old Home Assistant and
miss deprecations), git.

```bash
git clone https://github.com/kingpainter/net-trv.git
cd net-trv
python3.14 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements_test.txt ruff
```

Run the same checks as CI before you open a pull request:

```bash
ruff format .
ruff check .
mypy --strict --ignore-missing-imports custom_components/net_trv
pytest -q
```

CI also runs hassfest and the HACS validation (`.github/workflows/validate.yml`).

## Rules for changes

- **Style:** ruff (config in `pyproject.toml`), strict typing (`mypy --strict`,
  `py.typed`). Code, comments and commit messages in English.
- **Home Assistant patterns:** use `entry.runtime_data` (never
  `hass.data[DOMAIN]`), translated entity names and exceptions, and
  `DataUpdateCoordinator`. Follow the developer docs:
  - Tests: <https://contributors.home-assistant.io/core/development/tests/>
  - Code checks: <https://contributors.home-assistant.io/core/development/checks/>
  - Quality scale: <https://contributors.home-assistant.io/core/quality-scale/>
    (status in [`quality_scale.yaml`](quality_scale.yaml); target Platinum)
- **Tests:** every change comes with tests (`pytest-homeassistant-custom-component`,
  fixtures in `tests/fixtures`). No real Netatmo calls in tests; use the
  `mock_api` fixture. Coverage must not drop.
- **Translations:** every new user-facing string goes into `strings.json`,
  `translations/en.json` and `translations/da.json`. Custom integrations do
  not resolve `[%key:...]` references, so write the text out.
- **Dependencies:** `pyatmo` follows the version Home Assistant core uses. The
  manifest uses a minimum version (`pyatmo>=…`); `requirements_test.txt` pins
  the exact version. Do not change it without checking core.
- **API budget:** Netatmo allows 500 calls per hour per user. New features must
  state how many calls they add and prefer webhook events over polling.
- **Webhook:** answer within 5 seconds, verify the HMAC signature, never
  register on another app's webhook.
- **Breaking changes:** avoid renaming entity unique ids, services or
  statistic ids. If unavoidable, mark the commit `feat!:` and describe the
  migration in the changelog.

## Versioning and changelog

Semantic versioning. A release bumps the version in **all** of these:

| File | Field |
| --- | --- |
| `custom_components/net_trv/manifest.json` | `"version"` |
| `custom_components/net_trv/const.py` | `VERSION` |
| every `.py` file in `custom_components/net_trv/` | `# VERSION = "X.Y.Z"` top comment |
| `pyproject.toml` | `version` |
| `CHANGELOG.md` | new section ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/)) |

Releases are tagged `vX.Y.Z` with the release title `vX.Y.Z`.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): short description (X.Y.Z)
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `trans`.
Scopes: `climate`, `sensor`, `select`, `number`, `coordinator`, `webhook`,
`realtime`, `weather`, `history`, `schedules`, `services`, `config_flow`,
`api`, `tests`, `ci`, `docs`.

## Pull requests

- One topic per pull request; keep it small and testable.
- Fill in the pull request template checklist.
- CI (lint, tests, hassfest) must be green.
- Describe how you tested it on a real relay and valves if the change touches
  device behaviour.

By contributing you agree that your contribution is licensed under the
[MIT License](LICENSE) and that you follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
