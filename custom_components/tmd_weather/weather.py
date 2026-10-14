"""Native weather and forecast entities backed by TMD's coordinate model."""

from homeassistant.components.weather import (
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SUNNY,
    ATTR_FORECAST_CONDITION,
    ATTR_FORECAST_HUMIDITY,
    ATTR_FORECAST_NATIVE_PRECIPITATION,
    ATTR_FORECAST_NATIVE_PRESSURE,
    ATTR_FORECAST_NATIVE_TEMP,
    ATTR_FORECAST_NATIVE_TEMP_LOW,
    ATTR_FORECAST_NATIVE_WIND_SPEED,
    ATTR_FORECAST_TIME,
    ATTR_FORECAST_WIND_BEARING,
    CoordinatorWeatherEntity,
    Forecast,
)
from homeassistant.components.weather.const import WeatherEntityFeature
from homeassistant.const import (
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import BANGKOK, ForecastPoint, WeatherValues
from .const import ATTRIBUTION
from .coordinator import (
    TMDAWSObservationDataUpdateCoordinator,
    TMDDailyForecastDataUpdateCoordinator,
    TMDHourlyForecastDataUpdateCoordinator,
    TMDObservationDataUpdateCoordinator,
    TMDWeatherConfigEntry,
    device_info,
)
from .entity import current_values

PARALLEL_UPDATES = 0

# Temperature descriptions (9-12) and unknown codes say nothing about the sky.
CONDITION_MAP: dict[int, str] = {
    1: ATTR_CONDITION_SUNNY,
    2: ATTR_CONDITION_PARTLYCLOUDY,
    3: ATTR_CONDITION_CLOUDY,
    4: ATTR_CONDITION_CLOUDY,
    5: ATTR_CONDITION_RAINY,
    6: ATTR_CONDITION_RAINY,
    7: ATTR_CONDITION_POURING,
    8: ATTR_CONDITION_LIGHTNING_RAINY,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TMDWeatherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the main weather entity."""
    async_add_entities([TMDWeatherEntity(entry)])


def forecast(point: ForecastPoint, *, daily: bool) -> Forecast:
    """Translate only values supported by HA's native forecast schema."""
    data = point.values
    result: Forecast = {
        ATTR_FORECAST_TIME: point.time.isoformat(),
        ATTR_FORECAST_CONDITION: CONDITION_MAP.get(data.condition_code or 0),
        ATTR_FORECAST_NATIVE_TEMP: data.temperature,
        ATTR_FORECAST_HUMIDITY: data.humidity,
        ATTR_FORECAST_NATIVE_PRECIPITATION: data.precipitation,
        ATTR_FORECAST_NATIVE_PRESSURE: data.pressure,
        ATTR_FORECAST_NATIVE_WIND_SPEED: data.wind_speed,
        ATTR_FORECAST_WIND_BEARING: data.wind_bearing,
    }
    if daily:
        result[ATTR_FORECAST_NATIVE_TEMP_LOW] = data.temperature_low
    return result


class TMDWeatherEntity(
    CoordinatorWeatherEntity[
        TMDHourlyForecastDataUpdateCoordinator
        | TMDObservationDataUpdateCoordinator
        | TMDAWSObservationDataUpdateCoordinator,
        TMDDailyForecastDataUpdateCoordinator,
        TMDHourlyForecastDataUpdateCoordinator,
    ]
):
    """Configured station or modeled current weather with independent forecasts."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_attribution = ATTRIBUTION
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_visibility_unit = UnitOfLength.KILOMETERS
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_HOURLY | WeatherEntityFeature.FORECAST_DAILY
    )

    def __init__(self, entry: TMDWeatherConfigEntry) -> None:
        """Use the same cached hourly data for current state and forecasts."""
        runtime = entry.runtime_data
        self._runtime = runtime
        super().__init__(
            observation_coordinator=runtime.aws_coordinator
            or runtime.observation_coordinator
            or runtime.hourly_coordinator,
            hourly_coordinator=runtime.hourly_coordinator,
            daily_coordinator=runtime.daily_coordinator,
        )
        self._hourly = runtime.hourly_coordinator
        self._observation = runtime.observation_coordinator
        self._daily = runtime.daily_coordinator
        self._attr_unique_id = entry.unique_id
        self._attr_device_info = device_info(entry)

    @property
    def extra_state_attributes(self) -> dict[str, str | int] | None:
        """Distinguish station pressure and disclose the saved observation source."""
        if aws := self._runtime.aws_coordinator:
            return {
                "data_source": "aws_station",
                "aws_station_id": aws.aws_id,
                "station_name": aws.record.name,
                "pressure_type": "station",
            }
        return None

    @property
    def _current(self) -> WeatherValues:
        """Return current values only while the configured source is fresh."""
        return current_values(self._runtime)

    @property
    def available(self) -> bool:
        """Daily failures cannot invalidate current weather; stale hours can."""
        if self._runtime.aws_coordinator is not None:
            # HA filters unavailable weather entities out of get_forecasts.
            # Keep independent predictions usable while station readings are
            # absent; current state is unknown, with no model substitution.
            return (
                (super().available and self._current.temperature is not None)
                or self._hourly.last_update_success
                or self._daily.last_update_success
            )
        return super().available and self._current.temperature is not None

    @property
    def condition(self) -> str | None:
        """Translate sky/precipitation codes without guessing temperature codes."""
        return CONDITION_MAP.get(self._current.condition_code or 0)

    @property
    def native_temperature(self) -> float | None:
        """Return current temperature in Celsius."""
        return self._current.temperature

    @property
    def humidity(self) -> float | None:
        """Return current relative humidity in percent."""
        return self._current.humidity

    @property
    def native_pressure(self) -> float | None:
        """Return current sea-level pressure in hPa."""
        return self._current.pressure

    @property
    def native_wind_speed(self) -> float | None:
        """Return current wind in m/s; HA handles display conversions."""
        return self._current.wind_speed

    @property
    def wind_bearing(self) -> float | None:
        """Return the current source's wind direction in degrees."""
        return self._current.wind_bearing

    @property
    def native_dew_point(self) -> float | None:
        """Return station-supplied dew point; no modeled value is derived."""
        return self._current.dew_point

    @property
    def native_visibility(self) -> float | None:
        """Return station-supplied land visibility in kilometers."""
        return self._current.visibility

    @callback
    def _async_forecast_hourly(self) -> list[Forecast] | None:
        """Return active/future hours from the timestamped coordinator cache."""
        if not self._hourly.data:
            return None
        now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        return [
            forecast(point, daily=False)
            for point in self._hourly.data.points
            if point.time >= now
        ]

    @callback
    def _async_forecast_daily(self) -> list[Forecast] | None:
        """Return today and future days using the model's Bangkok calendar."""
        if not self._daily.data:
            return None
        today = dt_util.utcnow().astimezone(BANGKOK).date()
        return [
            forecast(point, daily=True)
            for point in self._daily.data.points
            if point.time.astimezone(BANGKOK).date() >= today
        ]
