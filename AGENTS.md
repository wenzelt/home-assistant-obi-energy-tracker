# AGENTS.md

Notes for coding agents (and humans) working on this Home Assistant custom
integration for the OBI ENERGY TRACKER cloud API.

## Layout

- `custom_components/obi_energy_tracker/`: the integration.
  - `auth.py`: passwordless Keycloak login (email, then a six-character OTP), done
    by scraping Keycloak's HTML forms with Authorization Code + PKCE.
  - `api.py`: REST client with refresh-token handling. No Home Assistant imports.
  - `util.py`: pure helpers (form parsing, PKCE, timestamps, power, hourly
    bucketing). No Home Assistant imports.
  - `coordinator.py`, `sensor.py`, `binary_sensor.py`, `entity.py`: polling and entities.
  - `statistics.py`: backfills hourly external statistics from the history endpoint.
  - `config_flow.py`: setup and reauth flows.
- `docs/openapi-public.json`: OBI's published API spec, checked in unchanged.
  Don't edit it; contract tests load it directly.
- `tests/`: fast tests for `auth`, `api`, `util` and `const` that don't need
  Home Assistant. `conftest.py` imports those modules without running `__init__.py`
  and provides `FakeSession`/`FakeResponse` instead of real HTTP.
- `tests_ha/`: tests that run the integration inside Home Assistant
  (`pytest-homeassistant-custom-component`, with a recorder).

## Running checks

```bash
# HA-independent tests (Python 3.12+)
python -m pip install -r requirements-test.txt
python -m pytest -q

# Home Assistant runtime tests (Python 3.13)
python -m pip install -r tests_ha/requirements.txt
python -m pytest tests_ha -q
```

CI (`.github/workflows/validate.yml`) runs both suites plus Hassfest and HACS. The
HACS job fails until the GitHub repository has a description and topics. Those are
repository settings, not code, so don't try to fix that failure in a commit.

## Things that are easy to get wrong

- **Redirect URI.** OBI's server rejects the spec's `http://localhost/callback`
  with `Invalid parameter: redirect_uri`. It accepts
  `https://my.home-assistant.io/redirect/oauth` (`REDIRECT_URI` in `const.py`).
  The integration never follows the redirect; it only reads `code` from the
  `Location` header. `test_openapi_contract.py` asserts both the spec value and
  the value in use, so a spec update shows up as a failing test.
- **Shared cookie jar.** Home Assistant's shared aiohttp session keeps cookies
  until restart. `auth.py` clears `auth.obi.com` cookies when a login starts and
  after it succeeds. Without that, a stale Keycloak session breaks every retry.
- **No secrets in logs or UI.** Never log or display response bodies, URLs (they
  carry codes and PKCE values), cookies, tokens, OTPs or email addresses. Error
  messages are fixed local strings with a stage, an HTTP status and, at most, a
  recognised reason. These strings also appear on the config-flow error, so the
  same rule applies there.
- **Error classes.** HTTP 401 refreshes the token once and then starts reauth.
  403 is an API error, not expired auth. 408, 429 and 5xx, and timeouts, are
  connection errors.
- **Units are assumed, not documented.** Values are treated as cumulative Wh
  counters and converted to kWh. The spec doesn't confirm this. Don't change the
  interpretation without evidence from a real meter, because it rewrites users'
  long-term statistics.
- **History import.** Statistics are external (`obi_energy_tracker:<sensor>_grid_consumption`
  and `_grid_export`), hourly and complete-hour only. They resume from the last
  stored statistic. A counter decrease counts as a reset.
- **Live testing.** Tests use fakes or mocks only. A real login needs a heyOBI
  account and sends an OTP email, so don't attempt one without the owner.

## Conventions

- Import order follows Home Assistant style (`from x import y` mixed with
  `import x`, sorted by module). Don't let a generic isort reorder untouched imports.
- User-facing strings live in `strings.json`, `translations/en.json` (identical
  to `strings.json`) and `translations/de.json`. Change all three together.
- Release: bump `version` in `manifest.json`. If new Home Assistant APIs are
  used, raise `homeassistant` in `hacs.json`. HACS installs from the default
  branch (`master`); there are no GitHub releases.
- Add a test for each behaviour change. Prefer `tests/` when Home Assistant isn't
  needed.
