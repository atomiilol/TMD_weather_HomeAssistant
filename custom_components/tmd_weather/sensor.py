"""One useful measurement absent from HA's current-weather property schema."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfPrecipitationDepth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import current_point
from .const import ATTRIBUTION
from .coordinator import (
    TMDHourlyForecastDataUpdateCoordinator,
    TMDObservationDataUpdateCoordinator,
    TMDWeatherConfigEntry,
    device_info,
)
from .observations import observation_is_fresh

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TMDWeatherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add current model-hour rain, explicitly named as a forecast amount."""
    async_add_entities([TMDRainSensor(entry)])


class TMDRainSensor(
    CoordinatorEntity[
        TMDHourlyForecastDataUpdateCoordinator | TMDObservationDataUpdateCoordinator
    ],
    SensorEntity,
):
    """Station or forecast rainfall amount, never a cumulative counter."""

    _attr_has_entity_name = True
    _attr_translation_key = "model_hour_rain"
    _attr_device_class = SensorDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_attribution = ATTRIBUTION
    # No state_class: intervals can be revised and are not a cumulative gauge.

    def __init__(self, entry: TMDWeatherConfigEntry) -> None:
        """Share the configured current-weather cache without another API call."""
        self._observation = entry.runtime_data.observation_coordinator
        super().__init__(self._observation or entry.runtime_data.hourly_coordinator)
        if self._observation is not None:
            self._attr_translation_key = "station_rainfall"
        # Keep the original unique ID when enabling observations on an existing
        # modeled entry, so its entity history/registry configuration survives.
        self._attr_unique_id = f"{entry.unique_id}_model_hour_rain"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> float | None:
        """Return the configured current source's rainfall in millimeters."""
        if self._observation is not None:
            if self._observation.data and observation_is_fresh(
                self._observation.data, dt_util.utcnow()
            ):
                return self._observation.data.values.precipitation
            return None
        hourly = self.coordinator
        assert isinstance(hourly, TMDHourlyForecastDataUpdateCoordinator)
        if hourly.data and (point := current_point(hourly.data, dt_util.utcnow())):
            return point.values.precipitation
        return None

    @property
    def available(self) -> bool:
        """Follow update success and the presence of an active model interval."""
        if self._observation is not None:
            return super().available and bool(
                self._observation.data
                and observation_is_fresh(self._observation.data, dt_util.utcnow())
            )
        hourly = self.coordinator
        assert isinstance(hourly, TMDHourlyForecastDataUpdateCoordinator)
        return super().available and bool(
            hourly.data and current_point(hourly.data, dt_util.utcnow())
        )
