"""Actual TMD Weather3Hours observations, based on verified live JSON responses."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
from time import monotonic

from aiohttp import ClientSession

from .api import (
    BANGKOK,
    TMDAuthError,
    TMDInvalidLocationError,
    TMDInvalidResponseError,
    TMDRateLimitError,
    WeatherValues,
    _distance,
    async_request_json,
    number,
)
from .const import OBSERVATION_MAX_AGE, OBSERVATION_MAX_DISTANCE_KM, OBSERVATION_URL


@dataclass(frozen=True, slots=True)
class StationObservation:
    """A physical station and its timestamped measurements, in common native units."""

    station_id: str
    name: str
    latitude: float
    longitude: float
    time: datetime
    values: WeatherValues


def _numeric(
    value: object, *, minimum: float = -math.inf, maximum: float = math.inf
) -> float | None:
    """The verified legacy JSON encodes measurements as decimal strings."""
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    result = number(value)
    return result if result is not None and minimum <= result <= maximum else None


def _observation_time(value: object) -> datetime | None:
    """Parse the verified MM/DD/YYYY local station time explicitly in Bangkok."""
    if not isinstance(value, str):
        return None
    try:
        # Confirmed against Header.LastBuildDate over the Oct 11/12 rollover.
        return datetime.strptime(value, "%m/%d/%Y %H:%M:%S").replace(tzinfo=BANGKOK)
    except ValueError:
        return None


def observation_is_fresh(observation: StationObservation, now: datetime) -> bool:
    """Never present future or excessively old measurements as current."""
    return timedelta(0) <= now - observation.time <= OBSERVATION_MAX_AGE


def parse_observations(payload: object) -> tuple[StationObservation, ...]:
    """Ignore unusable station records while validating the response envelope."""
    if not isinstance(payload, dict) or not isinstance(
        stations := payload.get("Stations"), dict
    ):
        raise TMDInvalidResponseError("Missing observation stations")
    records = stations.get("Station")
    # XML-to-JSON serializers may return one station as an object, not an array.
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise TMDInvalidResponseError("Invalid observation station collection")
    result: list[StationObservation] = []
    for station in records:
        if not isinstance(station, dict) or not isinstance(
            obs := station.get("Observation"), dict
        ):
            continue
        station_id = station.get("WmoStationNumber")
        lat = _numeric(station.get("Latitude"), minimum=-90, maximum=90)
        lon = _numeric(station.get("Longitude"), minimum=-180, maximum=180)
        time = _observation_time(obs.get("DateTime"))
        # Broad physical bounds exclude legacy negative sentinels; they are not
        # limits on Thailand's climate. Missing optional fields remain unknown.
        temperature = _numeric(obs.get("AirTemperature"), minimum=-100, maximum=70)
        if (
            not isinstance(station_id, str)
            or not station_id.strip()
            or lat is None
            or lon is None
            or time is None
            or temperature is None
        ):
            continue
        wind = _numeric(obs.get("WindSpeed"), minimum=0, maximum=500)
        name = station.get("StationNameThai") or station.get("StationNameEnglish")
        result.append(
            StationObservation(
                station_id.strip(),
                name if isinstance(name, str) else station_id,
                lat,
                lon,
                time,
                WeatherValues(
                    temperature=temperature,
                    humidity=_numeric(
                        obs.get("RelativeHumidity"), minimum=0, maximum=100
                    ),
                    pressure=_numeric(
                        obs.get("MeanSeaLevelPressure"), minimum=500, maximum=1200
                    ),
                    precipitation=_numeric(obs.get("Rainfall"), minimum=0),
                    # HA weather announces one wind unit for current AND forecasts.
                    # Normalize legacy km/h to NWP m/s, never to a frontend unit.
                    wind_speed=wind / 3.6 if wind is not None else None,
                    wind_bearing=_numeric(
                        obs.get("WindDirection"), minimum=0, maximum=360
                    ),
                    dew_point=_numeric(obs.get("DewPoint"), minimum=-100, maximum=70),
                    visibility=_numeric(obs.get("LandVisibility"), minimum=0),
                ),
            )
        )
    return tuple(result)


def nearest_station(
    observations: tuple[StationObservation, ...],
    latitude: float,
    longitude: float,
    now: datetime,
) -> StationObservation:
    """Select the nearest fresh station within a documented 100 km radius."""
    candidates = [
        station
        for station in observations
        if observation_is_fresh(station, now)
        and distance_km(latitude, longitude, station.latitude, station.longitude)
        <= OBSERVATION_MAX_DISTANCE_KM
    ]
    if not candidates:
        raise TMDInvalidLocationError(
            "No nearby station with a fresh usable observation"
        )
    return min(
        candidates,
        key=lambda station: (
            _distance(latitude, longitude, station.latitude, station.longitude),
            station.station_id,
        ),
    )


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return spherical great-circle distance using mean Earth radius."""
    return (
        6371.0088
        * 2
        * math.asin(math.sqrt(min(1, max(0, _distance(lat1, lon1, lat2, lon2)))))
    )


@dataclass(slots=True)
class TMDObservationClient:
    """Use separately supplied legacy credentials; never reuse an NWP bearer token."""

    session: ClientSession = field(repr=False)
    uid: str = field(repr=False)
    key: str = field(repr=False)
    latitude: float
    longitude: float
    _retry_at: float = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        """Reject missing credentials and coordinates before making a request."""
        if not self.uid.strip() or not self.key.strip():
            raise TMDAuthError("Missing observation credentials")
        if (
            _numeric(self.latitude, minimum=-90, maximum=90) is None
            or _numeric(self.longitude, minimum=-180, maximum=180) is None
        ):
            raise TMDInvalidLocationError("Invalid coordinates")

    async def async_get_current_conditions(self) -> StationObservation:
        """Fetch one dataset containing both station metadata and measurements."""
        if monotonic() < self._retry_at:
            raise TMDRateLimitError("TMD observation retry deadline has not elapsed")
        try:
            payload = await async_request_json(
                self.session,
                OBSERVATION_URL,
                {"uid": self.uid, "ukey": self.key, "format": "json"},
            )
        except TMDRateLimitError as error:
            self._retry_at = monotonic() + error.retry_after
            raise
        return nearest_station(
            parse_observations(payload),
            self.latitude,
            self.longitude,
            datetime.now(BANGKOK),
        )
