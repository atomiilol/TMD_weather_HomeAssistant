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
from .aws import TMDAWSClient, TMDAWSResponse
from .const import (
    AWS_SOURCE,
    AWS_UPDATE_INTERVAL,
    CONF_AWS_STATION_ID,
    CONF_CURRENT_SOURCE,
    CONF_FETCH_INTERVAL,
    CONF_FORECAST_SOURCE,
    CONF_OBSERVATION_UID,
    DAILY_UPDATE_INTERVAL,
    DOMAIN,
    HOURLY_UPDATE_INTERVAL,
    MANUFACTURER,
    OBSERVATION_UPDATE_INTERVAL,
)
from .observations import StationObservation, TMDObservationClient
from .stations import TMDAWSStationRecord

_LOGGER = logging.getLogger(__name__)


def polling_interval(entry: TMDWeatherConfigEntry, default: timedelta) -> timedelta:
    """Use the saved minute interval, retaining legacy schedules until configured."""
    if (minutes := entry.options.get(CONF_FETCH_INTERVAL)) is not None:
        return timedelta(minutes=minutes)
    return default


@dataclass(slots=True)
class TMDWeatherData:
    """Client and independently cached datasets owned by a config entry."""

    client: TMDClient
    hourly_coordinator: TMDHourlyForecastDataUpdateCoordinator
    daily_coordinator: TMDDailyForecastDataUpdateCoordinator
    observation_coordinator: TMDObservationDataUpdateCoordinator | None = None
    aws_coordinator: TMDAWSObservationDataUpdateCoordinator | None = None


type TMDWeatherConfigEntry = ConfigEntry[TMDWeatherData]


def device_info(entry: TMDWeatherConfigEntry) -> DeviceInfo:
    """Describe the configured weather service with stable coordinate identity."""
    assert entry.unique_id is not None
    domain2 = entry.options.get(CONF_FORECAST_SOURCE) == "domain_2"
    if entry.data.get(CONF_CURRENT_SOURCE) == AWS_SOURCE:
        record = (
            entry.runtime_data.aws_coordinator.record
            if entry.runtime_data.aws_coordinator
            else None
        )
        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry.unique_id)},
            manufacturer=MANUFACTURER,
            model=f"TMD AWS: {record.station_type} ({record.region})"
            if record
            else "TMD AWS station",
            name=entry.title,
            configuration_url="https://www.tmd.go.th/",
        )
    return DeviceInfo(
        entry_type=DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer=MANUFACTURER,
        model=("TMD station + Domain 2 forecast" if domain2 else "TMD station + NWP")
        if entry.data.get(CONF_OBSERVATION_UID)
        else (
            "TMD Domain 2 (modeled weather)" if domain2 else "TMD NWP (modeled weather)"
        ),
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
            update_interval=polling_interval(entry, interval),
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
        """Poll at the configured interval, defaulting to every 30 minutes."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} observations",
            update_interval=polling_interval(entry, OBSERVATION_UPDATE_INTERVAL),
            always_update=True,
        )
        self.client = client

    async def _async_update_data(self) -> StationObservation:
        """Reselect from fresh stations if a station disappears or lacks data."""
        try:
            return await _async_fetch(self.client.async_get_current_conditions)
        except ConfigEntryAuthFailed:
            # Saved legacy entries remain readable, but their retired credentials
            # cannot be replaced in the NWP-only reauthentication dialog.
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="legacy_observations_unavailable",
            ) from None


class TMDAWSObservationDataUpdateCoordinator(
    TimestampDataUpdateCoordinator[TMDAWSResponse]
):
    """Only poll the saved station; discovery is confined to configuration."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TMDWeatherConfigEntry,
        client: TMDAWSClient,
        record: TMDAWSStationRecord,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} AWS observations",
            update_interval=AWS_UPDATE_INTERVAL,
            always_update=True,
        )
        self.client = client
        self.record = record
        self.aws_id = entry.data[CONF_AWS_STATION_ID]

    async def _async_update_data(self) -> TMDAWSResponse:
        """Retain offline metadata without substituting model measurements."""
        return await _async_fetch(lambda: self.client.async_get_station(self.aws_id))


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
