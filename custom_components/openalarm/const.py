"""Constants for the OpenAlarm integration."""

from datetime import timedelta

DOMAIN = "openalarm"

DEFAULT_BASE_URL = "https://api.openalarm.io"
APP_URL = "https://console.openalarm.io/"
MANUFACTURER = "OpenAlarm"

CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"
CONF_LOCATION_ID = "location_id"
CONF_LOCATION_NAME = "location_name"
CONF_READINESS = "readiness"
CONF_TARGET = "target"
CONF_SENSORS = "sensors"

ATTR_MODE = "mode"

SERVICE_ARM = "alarm_arm"
SERVICE_DISARM = "alarm_disarm"
SERVICE_TRIGGER = "alarm_trigger"
SERVICE_CLEAR = "alarm_clear"
SERVICE_PANIC = "panic_trigger"
SERVICE_PANIC_CLEAR = "panic_clear"

KIND_ALARM = "alarm"
KIND_PANIC = "panic"

UPDATE_INTERVAL = timedelta(hours=6)
STATE_UPDATE_INTERVAL = timedelta(seconds=60)
STATE_UPDATE_INTERVAL_REALTIME = timedelta(minutes=5)
REQUEST_TIMEOUT = 15
