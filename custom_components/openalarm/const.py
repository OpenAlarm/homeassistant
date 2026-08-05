"""Constants for the OpenAlarm integration."""

from datetime import timedelta

DOMAIN = "openalarm"

DEFAULT_BASE_URL = "https://api.openalarm.io"
APP_URL = "https://app.openalarm.io/"
MANUFACTURER = "OpenAlarm"

CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"
CONF_LOCATION_ID = "location_id"
CONF_LOCATION_NAME = "location_name"

ATTR_MODE = "mode"

SERVICE_ARM = "arm"
SERVICE_DISARM = "disarm"
SERVICE_TRIGGER = "trigger"
SERVICE_CLEAR = "clear"
SERVICE_PANIC = "panic"
SERVICE_PANIC_CLEAR = "panic_clear"

KIND_ALARM = "alarm"
KIND_PANIC = "panic"

UPDATE_INTERVAL = timedelta(hours=6)
REQUEST_TIMEOUT = 15
