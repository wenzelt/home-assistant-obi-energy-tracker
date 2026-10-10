"""Constants for the OBI Energy Tracker integration."""

from datetime import timedelta

DOMAIN = "obi_energy_tracker"
PLATFORMS = ["sensor", "binary_sensor"]

API_BASE_URL = "https://api.obi.com/energytracker/api"
AUTH_BASE_URL = (
    "https://auth.obi.com/auth/realms/energy-tracker-clients/"
    "protocol/openid-connect"
)
AUTHORIZE_URL = f"{AUTH_BASE_URL}/auth"
TOKEN_URL = f"{AUTH_BASE_URL}/token"
CLIENT_ID = "home-assistant-user"
# OBI's server now rejects the spec's http://localhost/callback with
# "Invalid parameter: redirect_uri" and accepts Home Assistant's standard OAuth
# callback instead (verified 2026-10-10). The integration never follows this
# redirect; it only reads the authorization code from the Location header.
REDIRECT_URI = "https://my.home-assistant.io/redirect/oauth"
SPEC_REDIRECT_URI = "http://localhost/callback"
# Match the login example in docs/openapi-public.json. Offline access is not
# documented for this client; do not assume the server grants that scope.
OAUTH_SCOPE = "openid"

USER_ACCEPT = "application/vnd.obi.companion.energy-tracking.user.v2+json"
HISTORY_ACCEPT = (
    "application/vnd.obi.companion.energy-tracking.historical-record.v2+json"
)

CONF_ACCOUNT_ID = "account_id"
CONF_TOKEN = "token"

DEFAULT_UPDATE_INTERVAL = timedelta(minutes=5)
DEFAULT_HISTORY_DURATION = "PT30M"
# Backfill at most one year, fetched one week per request.
HISTORY_IMPORT_MAX_AGE = timedelta(days=365)
HISTORY_IMPORT_WINDOW = timedelta(days=7)
HISTORY_IMPORT_INTERVAL = timedelta(hours=1)
TOKEN_EXPIRY_LEEWAY_SECONDS = 60
