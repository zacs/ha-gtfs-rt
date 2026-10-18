"""Constants for the GTFS-Realtime integration."""

from datetime import timedelta

DOMAIN = "gtfs_rt"

CONF_TRIP_UPDATE_URL = "trip_update_url"
CONF_VEHICLE_POSITION_URL = "vehicle_position_url"
CONF_HEADERS = "headers"
# Legacy YAML shortcuts for a single auth header.
CONF_APIKEY = "apikey"
CONF_X_API_KEY = "x_api_key"
CONF_DEPARTURES = "departures"
CONF_STOP_ID = "stopid"
CONF_ROUTE = "route"

SUBENTRY_TYPE_DEPARTURE = "departure"

DEFAULT_NAME = "Next Bus"
DEFAULT_ICON = "mdi:bus"
# Also the minimum: 1.x throttled every feed to one request per minute.
DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
# How often "Due in" is recalculated from the last poll, as 1.x did.
COUNTDOWN_INTERVAL = timedelta(seconds=30)
TIME_STR_FORMAT = "%H:%M"
MAX_LISTED_DEPARTURES = 10

# State attribute names from 1.x, kept verbatim so templates and cards keep working.
ATTR_STOP_ID = "Stop ID"
ATTR_ROUTE = "Route"
ATTR_DUE_IN = "Due in"
ATTR_DUE_AT = "Due at"
ATTR_OCCUPANCY = "Occupancy"
ATTR_NEXT_UP = "Next bus"
ATTR_NEXT_UP_DUE_IN = "Next bus due in"
ATTR_NEXT_OCCUPANCY = "Next bus occupancy"
ATTR_DEPARTURES = "departures"
