"""Visible, validated AWS station control for the configured region."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import TMDAPIError
from .const import CONF_AWS_SELECTION_MODE, CONF_AWS_STATION_ID
from .coordinator import (
    TMDAWSObservationDataUpdateCoordinator,
    TMDWeatherConfigEntry,
    device_info,
)
from .stations import get_stations_by_region

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TMDWeatherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Offer a station dropdown only for AWS entries."""
    if entry.runtime_data.aws_coordinator is not None:
        async_add_entities([TMDAWSStationSelect(entry)])


class TMDAWSStationSelect(
    CoordinatorEntity[TMDAWSObservationDataUpdateCoordinator], SelectEntity
):
    """Select another catalog station without recreating the integration."""

    _attr_has_entity_name = True
    _attr_translation_key = "aws_station"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: TMDWeatherConfigEntry) -> None:
        coordinator = entry.runtime_data.aws_coordinator
        assert coordinator is not None
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_aws_station_select"
        self._attr_device_info = device_info(entry)
        self._choices = {
            f"{record.number} — {record.name}": record.aws_id
            for record in get_stations_by_region(coordinator.record.region)
        }
        self._attr_options = list(self._choices)

    @property
    def available(self) -> bool:
        """Keep the control usable when the currently selected station is offline."""
        return True

    @property
    def current_option(self) -> str:
        return f"{self.coordinator.record.number} — {self.coordinator.record.name}"

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        return {
            "aws_station_id": self.coordinator.aws_id,
            "region": self.coordinator.record.region,
        }

    async def async_select_option(self, option: str) -> None:
        """Validate the response before saving; preserve NWP coordinates and IDs."""
        if option not in self._choices:
            raise HomeAssistantError(
                translation_domain="tmd_weather",
                translation_key="invalid_aws_selection",
            )
        selected = self._choices[option]
        if selected == self.coordinator.aws_id:
            return
        try:
            await self.coordinator.client.async_get_station(selected)
        except TMDAPIError:
            raise HomeAssistantError(
                translation_domain="tmd_weather",
                translation_key="aws_station_change_failed",
            ) from None
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_AWS_STATION_ID: selected,
                CONF_AWS_SELECTION_MODE: "manual",
            },
        )
