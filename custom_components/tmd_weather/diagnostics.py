"""Safe diagnostics built from parsed values, never raw responses or errors."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import current_point
from .const import (
    AWS_SOURCE,
    CONF_ACCESS_TOKEN,
    CONF_AWS_LATITUDE,
    CONF_AWS_LONGITUDE,
    CONF_AWS_SELECTION_MODE,
    CONF_AWS_USE_HA_LOCATION,
    CONF_FORECAST_SOURCE,
    CONF_OBSERVATION_KEY,
    CONF_OBSERVATION_UID,
    CURRENT_SOURCE,
    DEFAULT_FORECAST_SOURCE,
    OBSERVATION_SOURCE,
)
from .coordinator import TMDForecastDataUpdateCoordinator, TMDWeatherConfigEntry
from .observations import distance_km

TO_REDACT = {
    CONF_ACCESS_TOKEN,
    CONF_OBSERVATION_UID,
    CONF_OBSERVATION_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_AWS_LATITUDE,
    CONF_AWS_LONGITUDE,
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
    result: dict[str, Any] = {
        "config": async_redact_data(dict(config_entry.data), TO_REDACT),
        "forecast_source": config_entry.options.get(
            CONF_FORECAST_SOURCE, DEFAULT_FORECAST_SOURCE
        ),
        "requested_hourly_domain": 2
        if config_entry.options.get(CONF_FORECAST_SOURCE) == "domain_2"
        else None,
        "fetch_intervals_minutes": {
            name: coordinator.update_interval.total_seconds() / 60
            if coordinator is not None and coordinator.update_interval is not None
            else None
            for name, coordinator in (
                ("hourly", runtime.hourly_coordinator),
                ("daily", runtime.daily_coordinator),
                ("observations", observation),
            )
        },
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
    if aws := runtime.aws_coordinator:
        response = aws.data
        status = response.status if response else None
        observation_aws = response.observation if response else None
        latitude = config_entry.data.get(
            CONF_AWS_LATITUDE, config_entry.data[CONF_LATITUDE]
        )
        longitude = config_entry.data.get(
            CONF_AWS_LONGITUDE, config_entry.data[CONF_LONGITUDE]
        )
        result.update(
            {
                "current_source": AWS_SOURCE,
                "current_model_values": None,
                "current_valid_time": observation_aws.timestamp.isoformat()
                if observation_aws
                else None,
                "current_observation_values": asdict(observation_aws.values)
                if observation_aws
                else None,
                "observation_update_success": aws.last_update_success,
                "observation_station": {"id": aws.aws_id, "name": aws.record.name},
                "aws": {
                    "selection_mode": config_entry.data.get(
                        CONF_AWS_SELECTION_MODE, "manual"
                    ),
                    "use_home_assistant_location": config_entry.data.get(
                        CONF_AWS_USE_HA_LOCATION, True
                    ),
                    "selection_location": async_redact_data(
                        {CONF_LATITUDE: latitude, CONF_LONGITUDE: longitude}, TO_REDACT
                    ),
                    "station_database": asdict(aws.record),
                    "station_live": asdict(status) if status else None,
                    "observation_available": bool(
                        response and response.available(dt_util.utcnow())
                    ),
                    "latest_observation_timestamp": (
                        observation_aws.timestamp.isoformat()
                        if observation_aws
                        else None
                    ),
                    "distance_km": distance_km(
                        latitude, longitude, status.latitude, status.longitude
                    )
                    if status
                    and status.latitude is not None
                    and status.longitude is not None
                    else None,
                    "timestamp_assumption": "UTC",
                    "pressure_type": "station",
                    "response_wind_unit": "kn",
                    "response_visibility_unit": "m",
                },
            }
        )
        result["fetch_intervals_minutes"]["aws"] = 10
    return result
