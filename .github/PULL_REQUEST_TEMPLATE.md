## What and why

<!-- What does this change, and which issue does it fix? -->

## How it was tested

<!-- Tests added, and how you checked it on a real relay and valves if it touches device behaviour. -->

## Checklist

- [ ] `ruff format .` and `ruff check .` pass
- [ ] `mypy --strict --ignore-missing-imports custom_components/net_trv` passes
- [ ] `pytest -q` passes and new code has tests
- [ ] Version bumped in manifest, `const.py`, every `.py` header and `pyproject.toml`
- [ ] `CHANGELOG.md` updated
- [ ] New strings in `strings.json`, `translations/en.json` and `translations/da.json`
- [ ] No secrets, tokens, webhook URLs or personal data in code, tests or this description
- [ ] No extra Netatmo API calls, or the number per hour is stated above
