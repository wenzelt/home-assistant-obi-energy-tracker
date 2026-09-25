# OBI Energy Tracker for Home Assistant

Unofficial Home Assistant custom integration for the **OBI ENERGY TRACKER Public API**. It uses OBI's passwordless OAuth flow and creates cumulative energy sensors suitable for the Home Assistant Energy Dashboard.

## Features

- UI setup through **Settings → Devices & services**
- No OBI password, API key, or client secret
- Email one-time-code login using Authorization Code + PKCE S256
- Automatic OAuth refresh-token handling
- Automatic bridge and sensor discovery through `GET /users/me`
- Grid-consumption and grid-export counters in kWh
- `device_class: energy` and `state_class: total_increasing`
- Sensor battery and connectivity diagnostics
- Five-minute cloud polling, matching OBI's meter upload interval
- HACS-compatible repository layout

> This project is community-built and is not affiliated with, endorsed by, or supported by OBI or heyOBI.

## Authentication and “secrets”

There is **no client secret to obtain**. OBI publishes a fixed public OAuth client:

- Client ID: `home-assistant-user`
- Flow: Authorization Code
- PKCE: S256, required
- Login: email address followed by a six-character one-time code
- Redirect URI used by the published flow: `http://localhost/callback`

During setup, the integration asks for your heyOBI email address and then the OTP sent by OBI. Home Assistant stores the resulting access/refresh token in the config entry. It does not use `secrets.yaml`. Treat Home Assistant's `.storage` directory and backups as sensitive because config-entry data is not designed as an encrypted secret vault.

## Installation with HACS

1. In HACS, open the three-dot menu and select **Custom repositories**.
2. Add `https://github.com/wenzelt/home-assistant-obi-energy-tracker` as category **Integration**.
3. Install **OBI Energy Tracker**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration** and search for **OBI Energy Tracker**.
6. Enter your heyOBI email address, then the six-character code sent by OBI.

### Manual installation

Copy `custom_components/obi_energy_tracker` into your Home Assistant configuration directory under `custom_components/`, then restart Home Assistant.

## Reconnecting and diagnosing login failures

A reconnect prompt means the integration could not continue using the saved
credentials. It does not prove why they became unusable: expiration, revocation,
and rejected refresh requests need to be distinguished using the logs.

If requesting a new email code fails, look in **Settings → System → Logs** for
`OBI login start failed`. The warning identifies the failed stage (authorization
page, email submission, or missing expected form) and HTTP status when available.
It deliberately excludes email addresses, HTML bodies, cookies, and login URLs.
For authorization-page HTTP 400, it can also identify a rejected redirect URI,
OAuth client, scope, or PKCE parameter if the response contains a recognizable
error. A bare HTTP 400 does not identify which part of OBI's request was rejected;
please include the new warning when reporting the issue. Repeated submissions
will not fix a rejected authorization request.
HTTP 408, 429, and 5xx responses are temporary connection/service failures, not
evidence of an incorrect email address. For 429, wait before requesting another
code; do not repeatedly press Submit. A missing OTP form can indicate that OBI
returned the email page, changed its login flow, or rejected the request.

New logins request `openid`, matching the example in the supplied
[API specification](docs/openapi-public.json). Version 0.1.3 requested the
additional `offline_access` scope, which is not documented by this spec.
This alignment does not prove that OBI rejects offline access. Existing saved
tokens are not discarded or rewritten by the scope change, and refresh-token
rotation remains supported. Ordinary sessions may eventually need a new OTP;
the spec does not define refresh-token or offline-session lifetimes.

When reporting a failure, include the integration and Home Assistant versions,
whether the official OBI app still works, and the warning above. Do not upload
tokens, OTPs, `.storage`, raw HTTP traces, or full backups. The generic login-start
error alone cannot establish that `offline_access` is unsupported. HTTP 403 means
forbidden access, not necessarily token expiration; it is reported as an API
error instead of requesting another login. HTTP 401 still refreshes once and
requests reauthentication if credentials remain invalid.

## Energy Dashboard

After the first successful update:

1. Open **Settings → Dashboards → Energy**.
2. Under **Electricity grid**, select the OBI **Grid consumption** sensor for consumption.
3. For photovoltaic export, select the OBI **Grid export** sensor under return to grid.

The integration interprets API values as cumulative Wh meter readings and
converts them to kWh. **The supplied spec does not define the energy unit or
whether values are cumulative.** This existing interpretation must be checked
against the meter/OBI app before relying on Energy Dashboard totals. The raw
value (currently labelled `raw_value_wh`) and measurement timestamp are retained
as entity attributes; that label is not independent evidence of the unit.

## API contract

The implementation is checked against the included [API contract](docs/openapi-public.json):

- `GET /users/me`
- `GET /historical-data/{bridgeId}/measures`
- `Authorization: Bearer <access_token>`
- Vendor-specific v2 `Accept` media types

Import (`energy`) and export (`negative_energy`) are requested separately because the published response schema does not include a measure name in each record.

See the [contract audit](docs/testing/openapi-audit-2026-09-18.md) for the complete
mapping, spec ambiguities, and the boundary between automated checks and live
Home Assistant validation. Contract tests load the actual checked-in spec rather
than comparing implementation constants only with copies of themselves.

## Security

- No password is requested or stored.
- OAuth tokens are never exposed as entity states or attributes.
- Diagnostics redact account and token fields.
- Do not post Home Assistant `.storage` files, backups, tokens, OTPs, or email addresses in issues.

## Known limitations

- This is a cloud integration; it requires OBI's API and internet access.
- OBI can change the service or OAuth flow.
- Newly paired sensors require an integration reload before new entities are created.
- The integration has been validated statically against the published OpenAPI document, but a real OBI account/device is required for end-to-end testing.

## Development

The repository includes unit-test, HACS, and Hassfest validation workflows.
Run the HA-independent tests locally with `python -m pip install -r requirements-test.txt`
and `python -m pytest -q`. These use HTTP test doubles and do not prove compatibility
with OBI's live service or exercise Home Assistant's config-flow runtime.
Before publishing a release, verify login, refresh-token rotation, both energy
counters, and long-term statistics on a real Home Assistant instance.

## License

MIT
