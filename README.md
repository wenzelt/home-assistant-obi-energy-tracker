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

## Energy Dashboard

After the first successful update:

1. Open **Settings → Dashboards → Energy**.
2. Under **Electricity grid**, select the OBI **Grid consumption** sensor for consumption.
3. For photovoltaic export, select the OBI **Grid export** sensor under return to grid.

The API counters are interpreted as cumulative Wh meter readings and converted to kWh. The original Wh value and measurement timestamp are retained as entity attributes.

## API contract

The implementation follows the included `docs/openapi-public.json` contract:

- `GET /users/me`
- `GET /historical-data/{bridgeId}/measures`
- `Authorization: Bearer <access_token>`
- Vendor-specific v2 `Accept` media types

Import (`energy`) and export (`negative_energy`) are requested separately because the published response schema does not include a measure name in each record.

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

The repository includes HACS and Hassfest validation workflows. Before publishing a release, verify login, refresh-token rotation, both energy counters, and long-term statistics on a real Home Assistant instance.

## License

MIT
