"""Constants shared by the TMD client and integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "tmd_weather"
NAME: Final = "Thai Meteorological Department (TMD)"
MANUFACTURER: Final = "Thai Meteorological Department"
ATTRIBUTION: Final = "Data provided by the Thai Meteorological Department (TMD)"
CONF_ACCESS_TOKEN: Final = "access_token"
CURRENT_SOURCE: Final = "nwp_model"
CONF_OBSERVATION_UID: Final = "observation_uid"
CONF_OBSERVATION_KEY: Final = "observation_key"
CONF_FETCH_INTERVAL: Final = "fetch_interval"
CONF_FORECAST_SOURCE: Final = "forecast_source"
DEFAULT_FORECAST_SOURCE: Final = "coordinate"
FORECAST_SOURCES: Final = ("coordinate", "domain_2")
DOMAIN2_ENDPOINT: Final = "/forecast/area/box"
DOMAIN2_SEARCH_RADIUS_KM: Final = 3
MIN_FETCH_INTERVAL: Final = 1
MAX_FETCH_INTERVAL: Final = 1440
OBSERVATION_SOURCE: Final = "station"
OBSERVATION_URL: Final = "https://data.tmd.go.th/api/Weather3Hours/V2/"
OBSERVATION_UPDATE_INTERVAL: Final = timedelta(minutes=30)
OBSERVATION_MAX_AGE: Final = timedelta(hours=6)
OBSERVATION_MAX_DISTANCE_KM: Final = 100
CONF_CURRENT_SOURCE: Final = "current_source"
AWS_SOURCE: Final = "aws_station"
CONF_AWS_STATION_ID: Final = "aws_station_id"
CONF_AWS_SELECTION_MODE: Final = "aws_selection_mode"
CONF_AWS_USE_HA_LOCATION: Final = "aws_use_ha_location"
CONF_AWS_LATITUDE: Final = "aws_latitude"
CONF_AWS_LONGITUDE: Final = "aws_longitude"
AWS_BASE_URL: Final = "http://122.155.135.49/api/home/site"
AWS_UPDATE_INTERVAL: Final = timedelta(minutes=10)
AWS_STALE_AFTER: Final = timedelta(hours=3)
AWS_METADATA_TTL: Final = 6 * 3600
AWS_UNUSABLE_CACHE_TTL: Final = 10 * 60
AWS_DISCOVERY_TIMEOUT: Final = 600
AWS_MAX_DISTANCE_KM: Final = 100
NWP_BASE_URL: Final = "https://data.tmd.go.th/nwpapi/v1"
HOURLY_ENDPOINT: Final = "/forecast/location/hourly/at"
DAILY_ENDPOINT: Final = "/forecast/location/daily/at"
HOURLY_FIELDS: Final = "tc,rh,slp,rain,ws10m,wd10m,cond"
DAILY_FIELDS: Final = "tc_max,tc_min,rh,slp,rain,ws10m,wd10m,cond"
HOURLY_DURATION: Final = 48
DAILY_DURATION: Final = 7
REQUEST_TIMEOUT: Final = 10
DOMAIN2_REQUEST_TIMEOUT: Final = 60
MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
HOURLY_UPDATE_INTERVAL: Final = timedelta(hours=1)
DAILY_UPDATE_INTERVAL: Final = timedelta(hours=3)
