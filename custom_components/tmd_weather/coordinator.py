"""Independent NWP refresh schedules and typed config-entry runtime data."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import (
    TimestampDataUpdateCoordinator,
    UpdateFailed,
)

from .api import ForecastData, TMDAPIError, TMDAuthError, TMDClient, TMDRateLimitError
from .const import (
    CONF_OBSERVATION_UID,
    DAILY_UPDATE_INTERVAL,
    DOMAIN,
    HOURLY_UPDATE_INTERVAL,
    MANUFACTURER,
    OBSERVATION_UPDATE_INTERVAL,
)
from .observations import StationObservation, TMDObservationClient

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TMDWeatherData:
    """Client and independently cached datasets owned by a config entry."""

    client: TMDClient
    hourly_coordinator: TMDHourlyForecastDataUpdateCoordinator
    daily_coordinator: TMDDailyForecastDataUpdateCoordinator
    observation_coordinator: TMDObservationDataUpdateCoordinator | None = None


type TMDWeatherConfigEntry = ConfigEntry[TMDWeatherData]


def device_info(entry: TMDWeatherConfigEntry) -> DeviceInfo:
    """Describe the configured weather service with stable coordinate identity."""
    assert entry.unique_id is not None
    return DeviceInfo(
        entry_type=DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer=MANUFACTURER,
        model="TMD station + NWP"
        if entry.data.get(CONF_OBSERVATION_UID)
        else "TMD NWP (modeled weather)",
        name=entry.title,
        configuration_url="https://www.tmd.go.th/",
    )


class TMDForecastDataUpdateCoordinator(TimestampDataUpdateCoordinator[ForecastData]):
    """Cache a dataset and translate errors without leaking request details."""

    config_entry: TMDWeatherConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TMDWeatherConfigEntry,
        name: str,
        interval: timedelta,
        fetch: Callable[[], Awaitable[ForecastData]],
        *,
        always_update: bool = False,
    ) -> None:
        """Initialize a coordinator bound to the config-entry lifecycle."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {name}",
            update_interval=interval,
            always_update=always_update,
        )
        self._fetch = fetch

    async def _async_update_data(self) -> ForecastData:
        """Fetch once; the coordinator owns retries and retains previous data."""
        return await _async_fetch(self._fetch)


async def _async_fetch[T](fetch: Callable[[], Awaitable[T]]) -> T:
    """Translate API errors with no sensitive exception text or context."""
    try:
        return await fetch()
    except TMDAuthError:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from None
    except TMDRateLimitError:
        raise UpdateFailed(
            translation_domain=DOMAIN, translation_key="rate_limited"
        ) from None
    except TMDAPIError:
        raise UpdateFailed(
            translation_domain=DOMAIN, translation_key="update_failed"
        ) from None


class TMDObservationDataUpdateCoordinator(
    TimestampDataUpdateCoordinator[StationObservation]
):
    """Current weather availability follows actual station updates independently."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TMDWeatherConfigEntry,
        client: TMDObservationClient,
    ) -> None:
        """Poll every 30 minutes for the three-hour station publication cadence."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} observations",
            update_interval=OBSERVATION_UPDATE_INTERVAL,
            always_update=True,
        )
        self.client = client

    async def _async_update_data(self) -> StationObservation:
        """Reselect from fresh stations if a station disappears or lacks data."""
        return await _async_fetch(self.client.async_get_current_conditions)


class TMDHourlyForecastDataUpdateCoordinator(TMDForecastDataUpdateCoordinator):
    """Hourly forecast also supplies explicitly modeled current conditions."""

    def __init__(
        self, hass: HomeAssistant, entry: TMDWeatherConfigEntry, client: TMDClient
    ) -> None:
        """Initialize the shared current/hourly cache, without duplicate requests."""
        super().__init__(
            hass,
            entry,
            "hourly",
            HOURLY_UPDATE_INTERVAL,
            client.async_get_hourly_forecast,
            # Current conditions move through model hours even when TMD
            # returns the same forecast document on the next poll.
            always_update=True,
        )


class TMDDailyForecastDataUpdateCoordinator(TMDForecastDataUpdateCoordinator):
    """Daily forecasts can fail independently of the current model state."""

    def __init__(
        self, hass: HomeAssistant, entry: TMDWeatherConfigEntry, client: TMDClient
    ) -> None:
        """Initialize the daily cache."""
        super().__init__(
            hass, entry, "daily", DAILY_UPDATE_INTERVAL, client.async_get_daily_forecast
        )
