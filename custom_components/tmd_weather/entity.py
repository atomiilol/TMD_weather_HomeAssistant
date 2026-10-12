"""Shared current-weather selection for weather and measurement entities."""

from homeassistant.util import dt as dt_util

from .api import WeatherValues, current_point
from .coordinator import TMDWeatherData
from .observations import observation_is_fresh


def current_values(runtime: TMDWeatherData) -> WeatherValues:
    """Select the configured source, without substituting a model during outages."""
    if observation := runtime.observation_coordinator:
        if observation.data and observation_is_fresh(
            observation.data, dt_util.utcnow()
        ):
            return observation.data.values
        return WeatherValues()
    hourly = runtime.hourly_coordinator
    if hourly.data and (point := current_point(hourly.data, dt_util.utcnow())):
        return point.values
    return WeatherValues()
