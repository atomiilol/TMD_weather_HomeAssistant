"""Public AWS station observations; independent of authenticated NWP sessions."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic

from aiohttp import ClientSession, DummyCookieJar
from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE, UnitOfLength, UnitOfSpeed
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util.unit_conversion import DistanceConverter, SpeedConverter

from .api import (
    TMDAPIError,
    TMDAuthError,
    TMDInvalidLocationError,
    TMDInvalidResponseError,
    TMDRateLimitError,
    TMDServiceError,
    WeatherValues,
    async_request_json,
)
from .const import (
    AWS_BASE_URL,
    AWS_DISCOVERY_CONCURRENCY,
    AWS_DISCOVERY_TIMEOUT,
    AWS_MAX_DISTANCE_KM,
    AWS_METADATA_TTL,
    AWS_STALE_AFTER,
    DOMAIN,
)
from .observations import _numeric, distance_km
from .stations import TMDAWSStationRecord, async_load_stations


@dataclass(frozen=True, slots=True)
class TMDAWSStationStatus:
    """Public metadata returned for the requested catalog ID."""

    aws_id: int
    api_name_en: str | None
    api_name_th: str | None
    latitude: float | None
    longitude: float | None
    altitude: float | None
    online: bool
    connected: bool
    has_visibility_sensor: bool


@dataclass(frozen=True, slots=True)
class TMDAWSObservation:
    """One coherent hourly observation; wind is knots and visibility is meters."""

    timestamp: datetime
    temperature: float | None
    humidity: float | None
    pressure: float | None
    precipitation: float | None
    wind_speed: float | None
    wind_bearing: float | None
    wind_direction_text: str | None
    visibility: float | None

    @property
    def values(self) -> WeatherValues:
        """Normalize current readings to the existing entity/forecast native units."""
        return WeatherValues(
            temperature=self.temperature,
            humidity=self.humidity,
            pressure=self.pressure,
            precipitation=self.precipitation,
            wind_speed=SpeedConverter.convert(
                self.wind_speed, UnitOfSpeed.KNOTS, UnitOfSpeed.METERS_PER_SECOND
            )
            if self.wind_speed is not None
            else None,
            wind_bearing=self.wind_bearing,
            visibility=DistanceConverter.convert(
                self.visibility, UnitOfLength.METERS, UnitOfLength.KILOMETERS
            )
            if self.visibility is not None
            else None,
        )


@dataclass(frozen=True, slots=True)
class TMDAWSResponse:
    """Station status remains available even when no observation is usable."""

    status: TMDAWSStationStatus
    observation: TMDAWSObservation | None

    def available(self, now: datetime) -> bool:
        """An online flag alone never makes missing, stale or future data usable."""
        return (
            self.status.online
            and self.observation is not None
            and datetime.min.replace(tzinfo=UTC) <= self.observation.timestamp <= now
            and now - self.observation.timestamp <= AWS_STALE_AFTER
        )


def observation_timestamp(item: dict[str, object]) -> datetime:
    """Isolate the beta assumption that yyyy/mm/dd/hh describe a UTC hour."""
    parts = []
    for key in ("yyyy", "mm", "dd", "hh"):
        value = item.get(key)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TMDInvalidResponseError("Invalid AWS observation clock")
        try:
            parts.append(int(value))
        except ValueError:
            raise TMDInvalidResponseError("Invalid AWS observation clock") from None
    try:
        return datetime(parts[0], parts[1], parts[2], parts[3], tzinfo=UTC)
    except ValueError, OverflowError:
        raise TMDInvalidResponseError("Invalid AWS observation clock") from None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def parse_aws_response(payload: object, aws_id: int, now: datetime) -> TMDAWSResponse:
    """Validate identity and choose the newest nonfuture row with actual readings."""
    if (
        not isinstance(payload, dict)
        or isinstance(payload.get("result"), bool)
        or payload.get("result") != 1
        or not isinstance(data := payload.get("data"), dict)
        or not isinstance(site := data.get("site"), dict)
    ):
        raise TMDInvalidResponseError("Invalid AWS station response")
    returned_id = _numeric(site.get("AWSID"), minimum=1)
    if returned_id != aws_id:
        raise TMDInvalidResponseError("AWS station ID does not match selection")
    status = TMDAWSStationStatus(
        aws_id,
        _text(site.get("SNAME")),
        _text(site.get("FNAME")),
        _numeric(site.get("LAT"), minimum=-90, maximum=90),
        _numeric(site.get("LON"), minimum=-180, maximum=180),
        _numeric(site.get("ALT")),
        site.get("ONLINE") == "y",
        site.get("CNCT_YN") == "y",
        site.get("WV_SENSOR") == "y",
    )
    if not isinstance(items := data.get("items"), list):
        raise TMDInvalidResponseError("Missing AWS observation rows")
    newest: TMDAWSObservation | None = None
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        try:
            timestamp = observation_timestamp(item)
        except TMDInvalidResponseError:
            continue
        if timestamp > now:
            continue
        observation = TMDAWSObservation(
            timestamp,
            _numeric(item.get("temp"), minimum=-90, maximum=60),
            _numeric(item.get("humidity"), minimum=0, maximum=100),
            _numeric(item.get("pressure"), minimum=0),
            _numeric(item.get("precipitation"), minimum=0),
            _numeric(item.get("windspeed"), minimum=0),
            _numeric(item.get("winddir"), minimum=0, maximum=360),
            _text(item.get("winddirsign")),
            _numeric(item.get("visibility"), minimum=0)
            if status.has_visibility_sensor
            else None,
        )
        if not any(
            value is not None
            for value in (
                observation.temperature,
                observation.humidity,
                observation.pressure,
                observation.precipitation,
                observation.wind_speed,
                observation.wind_bearing,
                observation.visibility,
            )
        ):
            continue
        if newest is None or timestamp > newest.timestamp:
            newest = observation
    return TMDAWSResponse(status, newest)


@dataclass(slots=True)
class TMDAWSClient:
    """No credentials, cookies, redirects or station-list requests."""

    session: ClientSession = field(repr=False)
    catalog: tuple[TMDAWSStationRecord, ...]
    _retry_at: float = field(default=0, init=False)

    async def async_get_station(self, aws_id: int) -> TMDAWSResponse:
        """Use only normalized IDs present in the supplied local catalog."""
        if (
            isinstance(aws_id, bool)
            or not isinstance(aws_id, int)
            or not any(station.aws_id == aws_id for station in self.catalog)
        ):
            raise TMDInvalidLocationError("Station ID is absent from the local catalog")
        if monotonic() < self._retry_at:
            raise TMDRateLimitError("AWS retry deadline has not elapsed")
        try:
            payload = await async_request_json(
                self.session,
                f"{AWS_BASE_URL}/{aws_id}",
                {},
                headers={"Accept": "application/json"},
            )
        except TMDAuthError:
            raise TMDServiceError("Public AWS endpoint rejected the request") from None
        except TMDRateLimitError as error:
            self._retry_at = monotonic() + error.retry_after
            raise
        return parse_aws_response(payload, aws_id, datetime.now(UTC))


class TMDAWSDiscovery:
    """Cache coordinates for setup; normal polling never invokes discovery."""

    def __init__(self, client: TMDAWSClient) -> None:
        self.client = client
        self.cache: dict[int, tuple[float, TMDAWSStationStatus]] = {}
        self.lock = asyncio.Lock()

    async def async_nearest(self, latitude: float, longitude: float) -> int:
        """Bound discovery concurrency, reuse metadata and recheck candidate health."""
        if (
            _numeric(latitude, minimum=-90, maximum=90) is None
            or _numeric(longitude, minimum=-180, maximum=180) is None
        ):
            raise TMDInvalidLocationError("Invalid AWS selection coordinates")
        async with self.lock, asyncio.timeout(AWS_DISCOVERY_TIMEOUT):
            fresh: dict[int, TMDAWSResponse] = {}
            semaphore = asyncio.Semaphore(AWS_DISCOVERY_CONCURRENCY)

            async def discover(record: TMDAWSStationRecord) -> None:
                if (cached := self.cache.get(record.aws_id)) and monotonic() - cached[
                    0
                ] < AWS_METADATA_TTL:
                    return
                async with semaphore:
                    try:
                        response = await self.client.async_get_station(record.aws_id)
                    except TMDAPIError:
                        return
                    self.cache[record.aws_id] = (monotonic(), response.status)
                    fresh[record.aws_id] = response

            await asyncio.gather(*(discover(record) for record in self.client.catalog))
            candidates = []
            for record in self.client.catalog:
                if not (cached := self.cache.get(record.aws_id)):
                    continue
                status = cached[1]
                if status.latitude is None or status.longitude is None:
                    continue
                distance = distance_km(
                    latitude, longitude, status.latitude, status.longitude
                )
                if distance <= AWS_MAX_DISTANCE_KM:
                    candidates.append((distance, record.aws_id))
            for _, aws_id in sorted(candidates):
                try:
                    response = fresh.get(aws_id) or await self.client.async_get_station(
                        aws_id
                    )
                except TMDAPIError:
                    continue
                self.cache[aws_id] = (monotonic(), response.status)
                # Recheck coordinates too: a station's metadata can change.
                status = response.status
                if (
                    status.latitude is None
                    or status.longitude is None
                    or distance_km(
                        latitude, longitude, status.latitude, status.longitude
                    )
                    > AWS_MAX_DISTANCE_KM
                ):
                    continue
                if response.available(datetime.now(UTC)):
                    return aws_id
        raise TMDInvalidLocationError("No usable recent AWS station within 100 km")


async def async_aws_discovery(hass: HomeAssistant) -> TMDAWSDiscovery:
    """Own a separate unauthenticated, cookie-free session until HA shutdown."""
    key = f"{DOMAIN}_aws_discovery"
    if isinstance(existing := hass.data.get(key), TMDAWSDiscovery):
        return existing
    catalog = await async_load_stations(hass)
    # No await between checking and storing the singleton after loading.
    if isinstance(existing := hass.data.get(key), TMDAWSDiscovery):
        return existing
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=DummyCookieJar()
    )

    @callback
    def close_session(event: Event) -> None:
        session.detach()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, close_session)
    service = TMDAWSDiscovery(TMDAWSClient(session, catalog))
    hass.data[key] = service
    return service
