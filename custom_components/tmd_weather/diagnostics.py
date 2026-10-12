"""Safe diagnostics built from parsed values, never raw responses or errors."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import current_point
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_OBSERVATION_KEY,
    CONF_OBSERVATION_UID,
    CURRENT_SOURCE,
    OBSERVATION_SOURCE,
)
from .coordinator import TMDForecastDataUpdateCoordinator, TMDWeatherConfigEntry

TO_REDACT = {
    CONF_ACCESS_TOKEN,
    CONF_OBSERVATION_UID,
    CONF_OBSERVATION_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
}


def _metadata(coordinator: TMDForecastDataUpdateCoordinator) -> dict[str, Any]:
    """Report cache health and valid times, not a fabricated model issue time."""
    return {
        "last_update_success": coordinator.last_update_success,
        "last_successful_fetch": coordinator.last_update_success_time.isoformat()
        if coordinator.last_update_success_time
        else None,
        "forecast_count": len(coordinator.data.points) if coordinator.data else 0,
        "first_valid_time": coordinator.data.points[0].time.isoformat()
        if coordinator.data
        else None,
        "last_valid_time": coordinator.data.points[-1].time.isoformat()
        if coordinator.data
        else None,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: TMDWeatherConfigEntry,
) -> dict[str, Any]:
    """Redact credentials and exact location; allowlist useful parsed values."""
    runtime = config_entry.runtime_data
    point = current_point(runtime.hourly_coordinator.data, dt_util.utcnow())
    observation = runtime.observation_coordinator
    station = observation.data if observation is not None else None
    return {
        "config": async_redact_data(dict(config_entry.data), TO_REDACT),
        "current_source": OBSERVATION_SOURCE
        if observation is not None
        else CURRENT_SOURCE,
        "observation_station": {"id": station.station_id, "name": station.name}
        if station
        else None,
        "observation_update_success": observation.last_update_success
        if observation is not None
        else None,
        "current_valid_time": station.time.isoformat()
        if station
        else point.time.isoformat()
        if point
        else None,
        "current_observation_values": asdict(station.values) if station else None,
        "current_model_values": asdict(point.values)
        if point and observation is None
        else None,
        "hourly": _metadata(runtime.hourly_coordinator),
        "daily": _metadata(runtime.daily_coordinator),
    }
