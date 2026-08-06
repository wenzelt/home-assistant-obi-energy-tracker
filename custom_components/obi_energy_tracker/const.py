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
REDIRECT_URI = "http://localhost/callback"
OAUTH_SCOPE = "openid"

USER_ACCEPT = "application/vnd.obi.companion.energy-tracking.user.v2+json"
HISTORY_ACCEPT = (
    "application/vnd.obi.companion.energy-tracking.historical-record.v2+json"
)

CONF_ACCOUNT_ID = "account_id"
CONF_TOKEN = "token"

DEFAULT_UPDATE_INTERVAL = timedelta(minutes=5)
DEFAULT_HISTORY_DURATION = "PT30M"
TOKEN_EXPIRY_LEEWAY_SECONDS = 60
