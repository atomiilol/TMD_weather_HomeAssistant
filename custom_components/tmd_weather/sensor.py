"""Separate current measurements and today's forecast from shared TMD caches."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import BANGKOK, ForecastPoint, WeatherValues
from .const import ATTRIBUTION, DOMAIN
from .coordinator import (
    TMDAWSObservationDataUpdateCoordinator,
    TMDDailyForecastDataUpdateCoordinator,
    TMDHourlyForecastDataUpdateCoordinator,
    TMDObservationDataUpdateCoordinator,
    TMDWeatherConfigEntry,
)
from .coordinator import device_info as tmd_device_info
from .entity import current_values

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class TMDSensorDescription(SensorEntityDescription):
    """Describe a supported measurement without coupling it to API payloads."""

    value_fn: Callable[[WeatherValues], float | None]
    station_only: bool = False


CURRENT_SENSORS: tuple[TMDSensorDescription, ...] = (
    TMDSensorDescription(
        # Retain the existing rainfall entity's ID and statistics semantics.
        key="model_hour_rain",
        translation_key="model_hour_rain",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        value_fn=lambda values: values.precipitation,
    ),
    TMDSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda values: values.temperature,
    ),
    TMDSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda values: values.humidity,
    ),
    TMDSensorDescription(
        key="pressure",
        translation_key="pressure",
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        native_unit_of_measurement=UnitOfPressure.HPA,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda values: values.pressure,
    ),
    TMDSensorDescription(
        key="wind_speed",
        translation_key="wind_speed",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda values: values.wind_speed,
    ),
    TMDSensorDescription(
        key="wind_bearing",
        translation_key="wind_bearing",
        device_class=SensorDeviceClass.WIND_DIRECTION,
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
        value_fn=lambda values: values.wind_bearing,
    ),
    TMDSensorDescription(
        key="dew_point",
        station_only=True,
        translation_key="dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda values: values.dew_point,
    ),
    TMDSensorDescription(
        key="visibility",
        station_only=True,
        translation_key="visibility",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda values: values.visibility,
    ),
)

# Forecast values are predictions, so they have no measurement/total state class.
DAILY_SENSORS: tuple[TMDSensorDescription, ...] = (
    TMDSensorDescription(
        key="forecast_temperature_high",
        translation_key="forecast_temperature_high",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda values: values.temperature,
    ),
    TMDSensorDescription(
        key="forecast_temperature_low",
        translation_key="forecast_temperature_low",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda values: values.temperature_low,
    ),
    TMDSensorDescription(
        key="forecast_rainfall",
        translation_key="forecast_rainfall",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        value_fn=lambda values: values.precipitation,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TMDWeatherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add supported readings, including newly supplied values on later updates."""
    runtime = entry.runtime_data
    registry = er.async_get(hass)
    source = (
        runtime.aws_coordinator
        or runtime.observation_coordinator
        or runtime.hourly_coordinator
    )
    created_current: set[str] = set()
    created_daily: set[str] = set()
    unsupported = {
        description.key
        for description in CURRENT_SENSORS
        if description.station_only
        and runtime.observation_coordinator is None
        and (runtime.aws_coordinator is None or description.key == "dew_point")
    }
    # Clean up only fields the selected source cannot provide. Transient gaps in
    # supported readings must not remove an entity's customized identity/history.
    for key in unsupported:
        if entity_id := registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.unique_id}_{key}"
        ):
            registry.async_remove(entity_id)

    @callback
    def add_current_sensors() -> None:
        """Discover available fields while preserving previously supported sensors."""
        if not source.last_update_success:
            return
        values = current_values(runtime)
        entities = []
        for description in CURRENT_SENSORS:
            if description.key in unsupported or description.key in created_current:
                continue
            if description.value_fn(
                values
            ) is None and not registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.unique_id}_{description.key}"
            ):
                continue
            entities.append(TMDCurrentSensor(entry, description))
            created_current.add(description.key)
        if entities:
            async_add_entities(entities)

    @callback
    def add_daily_sensors() -> None:
        """Discover forecast fields across the cached forecast horizon."""
        daily = runtime.daily_coordinator
        if not daily.last_update_success or not daily.data:
            return
        entities = []
        for description in DAILY_SENSORS:
            if description.key in created_daily:
                continue
            if not any(
                description.value_fn(point.values) is not None
                for point in daily.data.points
            ) and not registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.unique_id}_{description.key}"
            ):
                continue
            entities.append(TMDDailySensor(entry, description))
            created_daily.add(description.key)
        if entities:
            async_add_entities(entities)

    add_current_sensors()
    add_daily_sensors()
    entry.async_on_unload(source.async_add_listener(add_current_sensors))
    entry.async_on_unload(
        runtime.daily_coordinator.async_add_listener(add_daily_sensors)
    )


class TMDCurrentSensor(
    CoordinatorEntity[
        TMDHourlyForecastDataUpdateCoordinator
        | TMDObservationDataUpdateCoordinator
        | TMDAWSObservationDataUpdateCoordinator
    ],
    SensorEntity,
):
    """A current reading from the explicitly configured station or model source."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: TMDSensorDescription

    def __init__(
        self, entry: TMDWeatherConfigEntry, description: TMDSensorDescription
    ) -> None:
        """Use the same source and location identity as the weather entity."""
        self._runtime = entry.runtime_data
        super().__init__(
            self._runtime.aws_coordinator
            or self._runtime.observation_coordinator
            or self._runtime.hourly_coordinator
        )
        self.entity_description = description
        if description.key == "model_hour_rain" and (
            self._runtime.observation_coordinator is not None
            or self._runtime.aws_coordinator is not None
        ):
            self._attr_translation_key = "station_rainfall"
        if description.key == "pressure" and self._runtime.aws_coordinator is not None:
            self._attr_translation_key = "station_pressure"
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = tmd_device_info(entry)

    @property
    def native_value(self) -> float | None:
        """Missing optional values remain unknown; never synthesize a reading."""
        return self.entity_description.value_fn(current_values(self._runtime))

    @property
    def available(self) -> bool:
        """Follow current-source update success and freshness."""
        return (
            super().available
            and (
                self._runtime.aws_coordinator is not None
                or current_values(self._runtime).temperature is not None
            )
            and self.native_value is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Disclose modeled values explicitly, including when a source fails."""
        return {
            "data_source": "aws_station"
            if self._runtime.aws_coordinator is not None
            else "station"
            if self._runtime.observation_coordinator is not None
            else "nwp_model"
        }


class TMDDailySensor(
    CoordinatorEntity[TMDDailyForecastDataUpdateCoordinator], SensorEntity
):
    """A prediction for today using the model's Bangkok calendar."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: TMDSensorDescription

    def __init__(
        self, entry: TMDWeatherConfigEntry, description: TMDSensorDescription
    ) -> None:
        """Subscribe to the independent daily cache."""
        super().__init__(entry.runtime_data.daily_coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = tmd_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Advance the cached prediction at Bangkok midnight without fetching."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_utc_time_change(
                self.hass,
                self._async_midnight_update,
                # Asia/Bangkok is UTC+7 year-round: local midnight is 17:00 UTC.
                hour=17,
                minute=0,
                second=0,
            )
        )

    @callback
    def _async_midnight_update(self, now: datetime) -> None:
        """Select today's cached point, even between normal three-hour polls."""
        self.async_write_ha_state()

    @property
    def _today(self) -> ForecastPoint | None:
        """Use exactly today, never a stale prediction or tomorrow's forecast."""
        if self.coordinator.data:
            today = dt_util.utcnow().astimezone(BANGKOK).date()
            return next(
                (
                    point
                    for point in self.coordinator.data.points
                    if point.time.astimezone(BANGKOK).date() == today
                ),
                None,
            )
        return None

    @property
    def native_value(self) -> float | None:
        """Return a documented forecast value without cumulative statistics."""
        if point := self._today:
            return self.entity_description.value_fn(point.values)
        return None

    @property
    def available(self) -> bool:
        """Daily failures affect only these predictions, not current measurements."""
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Make the prediction's source and Bangkok date visible."""
        return {
            "data_source": "nwp_forecast",
            "forecast_date": dt_util.utcnow().astimezone(BANGKOK).date().isoformat(),
        }
