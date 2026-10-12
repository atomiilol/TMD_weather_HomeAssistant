"""Thai Meteorological Department custom weather integration."""

from datetime import datetime

from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_utc_time_change

from .api import TMDClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_FORECAST_SOURCE,
    CONF_OBSERVATION_KEY,
    CONF_OBSERVATION_UID,
    DEFAULT_FORECAST_SOURCE,
)
from .coordinator import (
    TMDDailyForecastDataUpdateCoordinator,
    TMDHourlyForecastDataUpdateCoordinator,
    TMDObservationDataUpdateCoordinator,
    TMDWeatherConfigEntry,
    TMDWeatherData,
)
from .observations import TMDObservationClient

PLATFORMS = [Platform.WEATHER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TMDWeatherConfigEntry) -> bool:
    """Validate both advertised datasets before creating any entities."""
    client = TMDClient(
        async_get_clientsession(hass),
        entry.data[CONF_ACCESS_TOKEN],
        entry.data[CONF_LATITUDE],
        entry.data[CONF_LONGITUDE],
        forecast_source=entry.options.get(
            CONF_FORECAST_SOURCE, DEFAULT_FORECAST_SOURCE
        ),
    )
    hourly = TMDHourlyForecastDataUpdateCoordinator(hass, entry, client)
    daily = TMDDailyForecastDataUpdateCoordinator(hass, entry, client)
    # Sequential initial requests avoid sending a second request after a 401/429.
    await hourly.async_config_entry_first_refresh()
    await daily.async_config_entry_first_refresh()
    observation = None
    if entry.data.get(CONF_OBSERVATION_UID):
        observation = TMDObservationDataUpdateCoordinator(
            hass,
            entry,
            TMDObservationClient(
                async_get_clientsession(hass),
                entry.data[CONF_OBSERVATION_UID],
                entry.data[CONF_OBSERVATION_KEY],
                entry.data[CONF_LATITUDE],
                entry.data[CONF_LONGITUDE],
            ),
        )
        await observation.async_config_entry_first_refresh()
    entry.runtime_data = TMDWeatherData(client, hourly, daily, observation)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    @callback
    def update_cached_current(now: datetime) -> None:
        """Advance model hours and expire station data between configured fetches."""
        (observation or hourly).async_update_listeners()

    entry.async_on_unload(
        async_track_utc_time_change(hass, update_cached_current, second=0)
    )
    return True


async def async_reload_entry(hass: HomeAssistant, entry: TMDWeatherConfigEntry) -> None:
    """Apply options using the same configuration and entity identities."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TMDWeatherConfigEntry) -> bool:
    """Unload entities and let entry-bound coordinators remove their listeners."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
