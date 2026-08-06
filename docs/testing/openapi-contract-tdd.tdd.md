# TDD evidence: OBI Energy Tracker vs the published OpenAPI contract

## Source

No `*.plan.md` was provided. This run was initiated directly with the
project's OpenAPI spec (`openapi-public (1).json`, title "OBI ENERGY
TRACKER Public API", version 1.0) and the instruction to "go through the
whole project" against it.

## What kind of TDD run this is

This is a **characterization / contract-verification pass on already-shipped
code**, not a bug fix. `custom_components/obi_energy_tracker/{api,auth,util}.py`
existed and were in production use before this run; the task was to compare
them against the real OpenAPI schema and backfill the test coverage that was
missing (previously only `util.py` had any tests, and those didn't cover its
most important function, `parse_obi_timestamp`).

Because no defect was known going in, the "RED gate" in the skill's Step 3
was interpreted as: write the test, run it against the *unmodified*
implementation, and treat "it fails for a real reason" as a genuine finding
(a bug to fix, RED -> GREEN) versus "it passes immediately" as a
characterization result (the implementation already satisfies the
contract). All 57 tests written in this run passed on first execution
against the existing code — **no implementation bugs or spec drift were
found**, and no production code was changed. Each test file was still
committed as its own checkpoint (`test:` commits), consistent with the
skill's per-stage commit guidance, since no `fix:` commit was applicable.

## Comparison method

1. Loaded `openapi-public (1).json` and read every path (`/users/me`,
   `/historical-data/{bridgeId}/measures`), its Accept-header content type,
   query parameters, and referenced schemas (`UserV2DTO`, `BridgeV2DTO`,
   `SensorV2DTO`, `MultiDeviceTimestreamRecordsDTO`, `TimestreamRecordDTO`).
2. Read `api.py`, `auth.py`, `const.py`, `coordinator.py`, `sensor.py`,
   `binary_sensor.py` field-by-field against those schemas (base URL,
   endpoint paths, vendor Accept media types, `ecomId`/`isOnline`/
   `batteryLevel`/`devices`/`time`/`value` field names).
3. Result: **no drift found**. Base URL, paths, Accept headers, and every
   field name referenced in the integration match the spec exactly.
4. Wrote characterization tests that pin this matching behavior down, plus
   the error-handling and edge-case behavior the spec implies but doesn't
   explicitly test (auth errors, malformed responses, token refresh).

## Test infrastructure notes

- `custom_components/obi_energy_tracker/__init__.py` imports Home Assistant,
  which is not installed in this environment. `api.py`, `auth.py`, `const.py`,
  and `util.py` do not import Home Assistant themselves. `tests/conftest.py`
  registers a namespace stand-in for the `obi_energy_tracker` package in
  `sys.modules` before importing these submodules, so their `from .const
  import ...` relative imports resolve without executing the real
  `__init__.py` (which would fail on the missing Home Assistant import).
- `aioresponses` (aiohttp response mocking) is incompatible with the
  installed aiohttp 3.14 (`ClientResponse.__init__()` signature changed
  upstream). Rather than pin aiohttp down, `tests/conftest.py` provides
  `FakeSession`/`FakeResponse`, minimal test doubles implementing only the
  subset of the `aiohttp.ClientSession`/`ClientResponse` surface that
  `api.py`/`auth.py` actually use (`.get()`/`.post()`/`.request()` returning
  a queued response or raising a queued `ClientError`; `.status`,
  `.headers`, `.cookies`, `.url`, `.history`, `.json()`, `.text()`,
  `.release()`).
- A local venv (`.venv/`, gitignored) with `aiohttp`, `yarl`, `pytest`,
  `pytest-asyncio`, `pytest-cov` was created for this run since none of
  these were installed system-wide.
- `pyproject.toml` gained `asyncio_mode = "auto"` under
  `[tool.pytest.ini_options]` so `async def test_...` functions run without
  per-test markers.

## Task report

| Task | Summary | Command | Result |
|---|---|---|---|
| Set up test venv | Installed aiohttp/yarl/pytest/pytest-asyncio/pytest-cov in `.venv/` | `python3 -m venv .venv && .venv/bin/pip install ...` | OK |
| `api.py` vs OpenAPI | 24 tests: `/users/me` and `/historical-data/{bridgeId}/measures` request shape, response parsing, token refresh, 401 retry, 403/5xx, malformed payloads | `.venv/bin/python -m pytest tests/test_api.py -q` | 24 passed, 0 failed, no code changes |
| `auth.py` PKCE flow | 20 tests: authorize -> email form -> OTP form -> Keycloak redirect chain -> token exchange, including the `cannot_connect` vs `invalid_auth` vs `invalid_otp` split | `.venv/bin/python -m pytest tests/test_auth.py -q` | 20 passed, 0 failed, no code changes |
| `util.py` edge cases | 13 tests: `parse_obi_timestamp` nanosecond truncation / Z suffix / explicit offset / malformed input (previously untested), `latest_record`/`wh_to_kwh` edge cases, PKCE length validation | `.venv/bin/python -m pytest tests/test_util.py -q` | 13 passed, 0 failed, no code changes |
| Full suite + coverage | All tests together with line coverage | `.venv/bin/python -m pytest tests/ -q --cov=custom_components/obi_energy_tracker --cov-report=term-missing` | 57 passed; see below |

## Test specification

| # | What is guaranteed | Test file | Type | Result |
|---|--------------------|-----------|------|--------|
| 1 | `GET /users/me` sends the vendor `v2` Accept header and `Bearer` token, per spec | `tests/test_api.py::test_async_get_user_uses_versioned_accept_header` | unit | PASS |
| 2 | Historical-data query reduces each device's record list to its newest entry by `time` | `tests/test_api.py::test_async_get_latest_measure_returns_newest_record_per_device` | unit | PASS |
| 3 | A response missing the required `devices` key is rejected, not silently accepted | `tests/test_api.py::test_async_get_latest_measure_rejects_missing_devices_key` | unit | PASS |
| 4 | Import (`energy`) and export (`negative_energy`) counters are fetched as two separate requests | `tests/test_api.py::test_async_get_all_latest_measures_requests_both_directions` | unit | PASS |
| 5 | An expiring token is refreshed before the API call, and the callback receives the updated token | `tests/test_api.py::test_expired_token_is_refreshed_before_request` | unit | PASS |
| 6 | A live 401 (not just an expiry timer) forces exactly one refresh+retry, then succeeds | `tests/test_api.py::test_401_mid_flight_forces_one_retry_then_succeeds` | unit | PASS |
| 7 | A 401 that persists after the forced retry raises an auth error instead of looping | `tests/test_api.py::test_401_after_retry_raises_auth_error` | unit | PASS |
| 8 | 403 raises an auth error without attempting a refresh | `tests/test_api.py::test_forbidden_response_raises_auth_error_without_retry` | unit | PASS |
| 9 | A dropped connection maps to `OBIEnergyConnectionError`, not a raw `ClientError` | `tests/test_api.py::test_network_failure_raises_connection_error` | unit | PASS |
| 10 | The authorize -> email-form -> OTP-form hop chain lands on the correct OTP action URL | `tests/test_auth.py::test_async_start_extracts_email_then_otp_form_action` | unit | PASS |
| 11 | A full OTP -> redirect(s) -> code -> token-exchange run returns a usable token, including through an intermediate (non-callback) redirect hop | `tests/test_auth.py::test_async_finish_exchanges_code_for_token`, `test_async_finish_follows_intermediate_redirects_before_callback` | unit | PASS |
| 12 | No `Location` header on the OTP POST means the code was rejected -> `OBIInvalidOTP`, and the OTP form action is refreshed for a safe retry | `tests/test_auth.py::test_async_finish_reshown_otp_form_raises_invalid_otp`, `test_async_finish_updates_otp_action_after_rejection_for_retry` | unit | PASS |
| 13 | A network failure *after* the OTP was accepted (mid redirect/token-exchange) maps to `OBIConnectionError` ("Unable to connect to OBI") — reproduces the first symptom reported against the live integration | `tests/test_auth.py::test_async_finish_network_failure_during_token_exchange_is_connection_error` | unit | PASS |
| 14 | A stale/already-consumed authorization code rejected at the token endpoint maps to a bare `OBIAuthError` ("OBI rejected the authentication request"), distinct from `OBIInvalidOTP` — reproduces the second symptom reported against the live integration | `tests/test_auth.py::test_async_finish_rejected_authorization_code_is_auth_error_not_invalid_otp` | unit | PASS |
| 15 | Redirect loops are bounded (>10 hops raises), and a hop with no `Location` header raises instead of hanging | `tests/test_auth.py::test_async_finish_too_many_redirects_raises_auth_error`, `test_async_finish_redirect_without_location_raises_auth_error` | unit | PASS |
| 16 | `parse_obi_timestamp` correctly handles the exact formats in the OpenAPI schema examples: space-separated nanosecond fractions (`TimestreamRecordDTO.time`) and `Z`-suffixed ISO 8601 (`claimedAt`/`dataVisibleSince`), plus malformed/empty input | `tests/test_util.py::test_parse_obi_timestamp_*` (8 tests) | unit | PASS |
| 17 | PKCE verifier generation and challenge computation reject out-of-spec (RFC 7636) lengths | `tests/test_util.py::test_generate_code_verifier_rejects_out_of_range_length`, `test_compute_code_challenge_rejects_out_of_range_verifier` | unit | PASS |

(17 rows summarize the 57 individual test functions across the three files; see the files themselves for the complete list.)

## Coverage and known gaps

```
Name                                                    Stmts   Miss  Cover   Missing
-------------------------------------------------------------------------------------
custom_components/obi_energy_tracker/__init__.py           24     24     0%   3-44
custom_components/obi_energy_tracker/api.py               105      2    98%   88, 174
custom_components/obi_energy_tracker/auth.py              100      1    99%   45
custom_components/obi_energy_tracker/binary_sensor.py      23     23     0%   3-54
custom_components/obi_energy_tracker/config_flow.py        84     84     0%   3-152
custom_components/obi_energy_tracker/const.py              17      0   100%
custom_components/obi_energy_tracker/coordinator.py        57     57     0%   3-118
custom_components/obi_energy_tracker/diagnostics.py        12     12     0%   3-31
custom_components/obi_energy_tracker/entity.py             16     16     0%   3-33
custom_components/obi_energy_tracker/sensor.py             60     60     0%   3-146
custom_components/obi_energy_tracker/util.py               61      0   100%
-------------------------------------------------------------------------------------
TOTAL                                                     559    279    50%
57 passed in 0.21s
```

(`.venv/bin/python -m pytest tests/ -q --cov=custom_components/obi_energy_tracker --cov-report=term-missing`)

**Modules directly implementing the OpenAPI contract meet the 80% bar:**
`api.py` 98%, `auth.py` 99%, `util.py` 100%, `const.py` 100%. The two
remaining uncovered lines are a concurrent-refresh double-check guard inside
an `asyncio.Lock` (`api.py:88`, only reachable under a genuine race between
two in-flight refreshes) and one line of dead-code safety net
(`api.py:174`) that the surrounding control flow makes unreachable.

**Known gap — intentionally out of scope for this pass:** `__init__.py`,
`config_flow.py`, `coordinator.py`, `entity.py`, `sensor.py`,
`binary_sensor.py`, and `diagnostics.py` all import Home Assistant at
module load time and are untested (0%), which is why the aggregate is 50%
rather than 80%+. Testing them requires either installing the full
`homeassistant` package plus `pytest-homeassistant-custom-component` (a
large, version-pinned dependency tree) or a fake Home Assistant shim
sufficient to satisfy `ConfigEntry`/`HomeAssistant`/`DataUpdateCoordinator`
semantics — both are a materially larger undertaking than this pass and
were not attempted. This matches the project's own stated limitation in
`README.md`: "The integration has been validated statically against the
published OpenAPI document, but a real OBI account/device is required for
end-to-end testing." Manual read-through of these files against the spec
(see "Comparison method" above) found no field-name or endpoint drift, but
that is a weaker guarantee than executed tests.

## Merge evidence

Three checkpoint commits on `master`, each is its own `test:` commit (no
`fix:` commits were needed — no bugs found):

1. `2f27a2f` — `test: characterize OBIEnergyApi against the published OpenAPI contract`
2. `37aacd0` — `test: characterize OBIPasswordlessAuth login flow, including the cannot_connect vs invalid_auth split`
3. `09b44b4` — `test: cover parse_obi_timestamp edge cases from the OpenAPI schema examples`
4. `81e7a6a` — `test: close remaining coverage gaps in api.py, auth.py, util.py`
