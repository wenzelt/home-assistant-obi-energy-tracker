# API contract audit — 2026-09-18

## Source and scope

Source: the user-supplied `openapi-public (1)(1).json`, titled
**OBI ENERGY TRACKER Public API**, OpenAPI 3.0.0, API version 1.0.
It is now checked in unchanged at [../openapi-public.json](../openapi-public.json).
The file was absent from both master and the PR branch, although the README and
an earlier testing report referenced it. All paths, parameters, response media
types, five schemas, security settings, and the embedded login example were read.
The embedded shell script was reviewed as documentation, not executed.

Reviewed code: `const.py`, `auth.py`, `api.py`, `config_flow.py`, `__init__.py`,
`coordinator.py`, `entity.py`, `sensor.py`, `binary_sensor.py`, `diagnostics.py`,
and `util.py`, together with the existing tests and user-facing documentation.

## Findings and changes

1. **Missing source artifact:** add the exact supplied spec. The new contract
   tests load it directly; earlier tests largely checked duplicated fixtures
   and constants, so could not reliably detect drift in the source contract.
2. **Undocumented OAuth scope:** the spec's script requests `scope=openid`.
   Version 0.1.3 added `offline_access`; the spec does not document that extension.
   New logins now use `openid` to match the supplied example. This is a
   compatibility choice, **not proof** that OBI rejects offline access or that
   the scope caused the user's failure. Existing tokens and refresh support
   remain intact. Ordinary sessions may still require occasional OTP login.
3. **403 incorrectly implied expiration:** both API paths distinguish 401
   (missing/invalid token) from 403 (forbidden). Previously both led to
   `ConfigEntryAuthFailed("OBI authentication expired")`. Now 403 raises a
   response error, producing an update/setup API error rather than a reconnect
   prompt. 401 still refreshes once before requiring reauthentication.
4. **Unproven energy semantics:** the spec declares `value` only as a number;
   it provides no unit, accumulation/reset semantics, or guarantee of monotonic
   counters. The existing Wh-to-kWh conversion and `TOTAL_INCREASING` entities
   are retained to avoid silently changing existing statistics. The README
   now explicitly requires verification against the physical meter/OBI app.

## Field-by-field mapping

| Contract location | Implementation / outcome |
|---|---|
| `servers[0].url` | `API_BASE_URL` matches production exactly. |
| Global `bearerAuth`, HTTP bearer JWT | Both GET requests send `Authorization: Bearer ...`; no client secret is sent. |
| `GET /users/me`, v2 user media type | URL, method, `Accept` header match. No query parameters are added. |
| `GET /historical-data/{bridgeId}/measures`, v2 history media type | URL, method, `Accept` match; bridge ID comes from the user response. |
| `bridgeId`: required path string, `uuidv4` | Used as supplied by OBI. UUID format is not independently validated at runtime. |
| `duration` | Default `PT30M` is a duration-only ISO 8601 value, allowed by the description. `to` defaults to now on the server. |
| `from`, `to`, `deviceIds` | Optional; not needed by current polling. Omitting device IDs requests all paired devices as documented. |
| `measures`: `energy`, `negative_energy`, `rssi`, `battery` | Import/export are queried separately; the client can request each enum value. RSSI/history-battery entities are not currently exposed. |
| `MultiDeviceTimestreamRecordsDTO.devices` | Required object mapping device IDs to record arrays; parsed correctly. Missing/malformed object raises a response error. |
| `TimestreamRecordDTO.time`, `value` | Required string/number fields used to choose a latest record. No measure discriminator exists, so separate import/export requests avoid ambiguous series. |
| `UserV2DTO.ecomId`, `givenName`, `email` | Account identity, display title, and saved login email. Required profile examples are accepted. Unknown consent/tenant/account fields are tolerated. |
| Optional `UserV2DTO.bridge` | Client accepts an account without a bridge. Coordinator reports no paired bridge as an update failure, not expired auth. |
| `BridgeV2DTO.id`, optional `sensors` | Discovery uses the bridge ID; missing sensors becomes an empty list. No bridge-label assumptions affect identity. |
| `SensorV2DTO.id`, `displayName`, `label`, `model`, `type`, firmware/hardware versions | Entity identity and device metadata use documented fields, with fallbacks for optional/nullable display names and optional model/type. |
| Nullable/optional `batteryLevel` | Battery sensor reads profile field; `None` produces unknown. No historical `battery_level` query is sent. |
| Required boolean `isOnline` | Connectivity binary sensor uses the documented boolean, not inferred network reachability. |
| `uploadInterval` example/default 300 seconds | Five-minute integration polling matches the documented default, not optional 2-second live mode. |
| `claimedAt`, `dataVisibleSince`, history `time` examples | ISO/Z and nanosecond space-separated examples parse. Offset-free history timestamps remain offset-free; the spec gives no timezone guarantee. |
| 401 / 403 / 404 | Retry refresh once for 401, report forbidden for 403, report response error for 404. Only invalid/missing credentials trigger reauth. |
| OAuth prose: realm, public client, PKCE S256, callback, `username`, `code` | URLs, client ID, callback, fields, challenge and token-exchange form match the example. OTP accepts alphanumeric six-character codes, not just digits. |
| `__init__.py` token persistence | Token-update callback persists rotated responses. Refresh grants, token lifetimes and rotation are not specified by this document. |
| Diagnostics | Dumps stored tokens only through redaction and does not change the API protocol. Existing diagnostics still include device metadata/IDs and readings; this audit does not certify the dump as anonymous. Review before sharing. |

## What the spec cannot settle

- The history endpoint description mentions `battery_level` and `outlet_state`,
  but its formal enum says `battery` and omits `outlet_state`. The integration
  uses `energy` and `negative_energy`, common to both; no guessed outlet API
  was added.
- The schema does not identify energy units or cumulative versus interval values.
- Time ordering relies on OBI returning consistently formatted history strings;
  the schema only says string and shows one format. Mixed-offset/time-format
  ordering is not guaranteed by this audit.
- The spec does not define OAuth error responses, HTML forms/hidden fields,
  redirects in detail, refresh grants, offline sessions, or token lifetime.
  The previous PR's defensive HTML handling is independently unit-tested, not
  schema-derived.
- The five-minute OTP validity is stated in the prose and enforced by OBI;
  the integration does not attempt to verify expiration locally.
- No access to the user's Home Assistant logs, tokens, or live account was used.
  The supplied spec alone cannot establish the root cause of the reconnect error.

## Verification

`tests/test_openapi_contract.py` adds 20 spec-backed cases: base URL/media/security,
required-only and full user examples, nullable fields, all four measure values,
both paths' documented error codes, the embedded PKCE login/token-exchange flow,
timestamp examples, and the polling default.

Complete local suite: **108 passed**, including the previous 88 tests (with the
scope and 403 expectations corrected). HTTP calls are test doubles. The
coordinator, Home Assistant config-flow lifecycle, entities, and diagnostics
were inspected statically, not executed in a full Home Assistant runtime.
These results are not an end-to-end certification. Validate a real login, token
refresh, reconnection, and meter readings before publishing a release.

The original attachment and checked-in file are byte-identical (`cmp` passed).

## Repository validation outside the API contract

The previous PR revision passed its GitHub unit-test job, but Hassfest reported
incorrect manifest key order (domain, name, then alphabetical); this revision
corrects that ordering without changing manifest values. HACS also reported
missing repository description, topics, and brand assets. Original, neutral
energy-tracker brand assets now satisfy the in-repository requirement without
using OBI's trademarked logo. The owner must still set the GitHub repository
description and topics; those settings cannot be expressed in a commit. The
workflow has not been weakened or configured to ignore any requirement.
