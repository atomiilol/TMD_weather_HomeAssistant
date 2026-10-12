"""Bounded, asynchronous TMD NWP client independent of Home Assistant.

The schema adapter supports the distinct envelopes in TMD's official examples.
Authenticated production forecast responses still need verification.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import json
import math
from time import monotonic
from typing import Literal
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    DAILY_DURATION,
    DAILY_ENDPOINT,
    DAILY_FIELDS,
    HOURLY_DURATION,
    HOURLY_ENDPOINT,
    HOURLY_FIELDS,
    MAX_RESPONSE_BYTES,
    NWP_BASE_URL,
    REQUEST_TIMEOUT,
)

BANGKOK = ZoneInfo("Asia/Bangkok")
type ForecastKind = Literal["hourly", "daily"]


class TMDAPIError(Exception):
    """Base error containing only safe, locally generated messages."""


class TMDAuthError(TMDAPIError):
    """A configured TMD credential was rejected."""


class TMDRateLimitError(TMDAPIError):
    """The service's retry deadline has not elapsed."""

    def __init__(
        self, message: str = "TMD request limit reached", *, retry_after: float = 3600
    ) -> None:
        """Carry only a safe retry interval, never headers or response bodies."""
        super().__init__(message)
        self.retry_after = retry_after


class TMDConnectionError(TMDAPIError):
    """The request could not complete within the timeout."""


class TMDInvalidResponseError(TMDAPIError):
    """The response cannot safely be interpreted as forecast data."""


class TMDInvalidLocationError(TMDAPIError):
    """The coordinates are invalid or were rejected by TMD."""


class TMDServiceError(TMDAPIError):
    """TMD reported a service or endpoint failure."""


@dataclass(frozen=True, slots=True)
class WeatherValues:
    """Validated metric forecast values; unavailable fields remain absent."""

    temperature: float | None = None
    temperature_low: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    precipitation: float | None = None
    wind_speed: float | None = None
    wind_bearing: float | None = None
    condition_code: int | None = None
    dew_point: float | None = None
    visibility: float | None = None


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """Forecast valid time, not the model's issuance time."""

    time: datetime
    values: WeatherValues


@dataclass(frozen=True, slots=True)
class ForecastData:
    """One selected model location and its ordered forecast points."""

    latitude: float
    longitude: float
    points: tuple[ForecastPoint, ...]


def number(value: object) -> float | None:
    """Accept finite JSON numbers, including integer zero, but not booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def _bounded(value: object, minimum: float, maximum: float) -> float | None:
    result = number(value)
    return result if result is not None and minimum <= result <= maximum else None


def _nonnegative(value: object) -> float | None:
    result = number(value)
    return result if result is not None and result >= 0 else None


def _values(data: dict[str, object], kind: ForecastKind) -> WeatherValues:
    code = number(data.get("cond"))
    return WeatherValues(
        temperature=number(data.get("tc" if kind == "hourly" else "tc_max")),
        temperature_low=number(data.get("tc_min")) if kind == "daily" else None,
        humidity=_bounded(data.get("rh"), 0, 100),
        pressure=_nonnegative(data.get("slp")),
        precipitation=_nonnegative(data.get("rain")),
        wind_speed=_nonnegative(data.get("ws10m")),
        wind_bearing=_bounded(data.get("wd10m"), 0, 360),
        condition_code=int(code) if code is not None and code.is_integer() else None,
    )


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TMDInvalidResponseError("Missing forecast timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise TMDInvalidResponseError("Invalid forecast timestamp") from None
    # Reject naive dates instead of guessing the timezone of an unknown schema.
    if result.tzinfo is None or result.utcoffset() is None:
        raise TMDInvalidResponseError("Forecast timestamps must include an offset")
    return result


def parse_forecast(
    payload: object, kind: ForecastKind, latitude: float, longitude: float
) -> ForecastData:
    """Validate the NWP envelope and select the nearest returned model location.

    TMD documents two top-level spellings and a nested daily envelope.
    Never combine points from different locations or response envelopes.
    """
    if not isinstance(payload, dict):
        raise TMDInvalidResponseError("Expected a JSON object")
    locations = payload.get("WeatherForcasts", payload.get("WeatherForecasts"))
    if "WeatherForcasts" not in payload and "WeatherForecasts" not in payload:
        daily_envelope = payload.get("weather_forecast")
        if isinstance(daily_envelope, dict):
            locations = daily_envelope.get("locations")
    if not isinstance(locations, list) or not locations:
        raise TMDInvalidResponseError("Missing or empty forecast locations")
    candidates: list[ForecastData] = []
    for item in locations:
        if not isinstance(item, dict) or not isinstance(
            loc := item.get("location"), dict
        ):
            raise TMDInvalidResponseError("Invalid forecast location")
        lat = _bounded(loc.get("lat"), -90, 90)
        lon = _bounded(loc.get("lon"), -180, 180)
        if lat is None or lon is None:
            raise TMDInvalidResponseError("Invalid model coordinates")
        forecasts = item.get("forecasts")
        if not isinstance(forecasts, list) or not forecasts:
            raise TMDInvalidResponseError("Missing or empty forecast points")
        points: dict[datetime, ForecastPoint] = {}
        for forecast in forecasts:
            if not isinstance(forecast, dict) or not isinstance(
                data := forecast.get("data"), dict
            ):
                raise TMDInvalidResponseError("Missing forecast data")
            timestamp = _timestamp(forecast.get("time"))
            if timestamp in points:
                raise TMDInvalidResponseError("Duplicate forecast timestamp")
            points[timestamp] = ForecastPoint(timestamp, _values(data, kind))
        if not any(point.values.temperature is not None for point in points.values()):
            raise TMDInvalidResponseError("No usable forecast temperatures")
        candidates.append(
            ForecastData(lat, lon, tuple(points[t] for t in sorted(points)))
        )
    return min(
        candidates,
        key=lambda result: (
            _distance(latitude, longitude, result.latitude, result.longitude),
            result.latitude,
            result.longitude,
        ),
    )


def _distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return haversine angular distance for model-location selection."""
    a, b = math.radians(lat1), math.radians(lat2)
    return (
        math.sin((a - b) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lon1 - lon2) / 2) ** 2
    )


def current_point(data: ForecastData, now: datetime) -> ForecastPoint | None:
    """Select the active one-hour model interval, never a future or stale point."""
    return next(
        (
            point
            for point in reversed(data.points)
            if point.time <= now < point.time + timedelta(hours=1)
            and point.values.temperature is not None
        ),
        None,
    )


def location_id(latitude: float, longitude: float) -> str:
    """Identify requested coordinates to four decimals, normalizing signed zero."""
    lat, lon = round(latitude, 4) + 0.0, round(longitude, 4) + 0.0
    return f"{lat:.4f},{lon:.4f}"


def _retry_delay(value: str | None) -> float:
    """Honor Retry-After seconds or HTTP date; otherwise wait one hour."""
    if value is not None:
        try:
            seconds = float(value)
            if math.isfinite(seconds):
                return max(1, seconds)
        except ValueError:
            pass
        try:
            date = parsedate_to_datetime(value)
            return max(1, (date - datetime.now(UTC)).total_seconds())
        except TypeError, ValueError, OverflowError:
            pass
    return 3600


async def async_request_json(
    session: ClientSession,
    url: str,
    params: dict[str, str | int | float],
    *,
    headers: dict[str, str] | None = None,
) -> object:
    """Read bounded JSON from TMD without redirects or sensitive error chaining."""
    try:
        async with session.get(
            url,
            params=params,
            headers=headers or {"Accept": "application/json"},
            timeout=ClientTimeout(total=REQUEST_TIMEOUT),
            allow_redirects=False,
        ) as response:
            if response.status in (401, 403):
                raise TMDAuthError("TMD rejected authentication")
            if response.status == 429:
                raise TMDRateLimitError(
                    retry_after=_retry_delay(response.headers.get("Retry-After"))
                )
            if response.status == 422:
                raise TMDInvalidLocationError("TMD rejected the coordinate request")
            if response.status != 200:
                raise TMDServiceError("TMD service request failed")
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise TMDInvalidResponseError("TMD response exceeded size limit")
            try:
                payload: object = json.loads(body)
            except ValueError, UnicodeError, RecursionError:
                raise TMDInvalidResponseError("TMD returned invalid JSON") from None
    except ClientError, TimeoutError:
        # Legacy credentials are in query parameters; aiohttp errors can contain
        # full URLs. Never chain them or log the request/response body.
        raise TMDConnectionError("Unable to connect to TMD") from None
    return payload


@dataclass(slots=True)
class TMDClient:
    """Use a caller-owned session and a manually supplied NWP bearer token."""

    session: ClientSession = field(repr=False)
    token: str = field(repr=False)
    latitude: float
    longitude: float
    _retry_at: float = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        """Reject invalid configuration without exposing credential values."""
        if (
            _bounded(self.latitude, -90, 90) is None
            or _bounded(self.longitude, -180, 180) is None
        ):
            raise TMDInvalidLocationError("Invalid coordinates")
        if not self.token.strip() or "\r" in self.token or "\n" in self.token:
            raise TMDAuthError("Invalid access token")

    async def async_get_hourly_forecast(
        self, *, duration: int = HOURLY_DURATION
    ) -> ForecastData:
        """Get up to 48 hours beginning at the current Bangkok hour."""
        if not 1 <= duration <= HOURLY_DURATION:
            raise ValueError("Hourly duration must be between 1 and 48")
        now = datetime.now(BANGKOK)
        return await self._forecast(
            HOURLY_ENDPOINT,
            "hourly",
            {
                "date": now.date().isoformat(),
                "hour": now.hour,
                "duration": duration,
                "fields": HOURLY_FIELDS,
            },
        )

    async def async_get_daily_forecast(
        self, *, duration: int = DAILY_DURATION
    ) -> ForecastData:
        """Get seven days beginning on today's Bangkok date."""
        if not 1 <= duration <= DAILY_DURATION:
            raise ValueError("Daily duration must be between 1 and 7")
        return await self._forecast(
            DAILY_ENDPOINT,
            "daily",
            {
                "date": datetime.now(BANGKOK).date().isoformat(),
                "duration": duration,
                "fields": DAILY_FIELDS,
            },
        )

    async def _forecast(
        self, endpoint: str, kind: ForecastKind, params: dict[str, str | int | float]
    ) -> ForecastData:
        if monotonic() < self._retry_at:
            raise TMDRateLimitError("TMD retry deadline has not elapsed")
        params.update(lat=self.latitude, lon=self.longitude)
        try:
            payload = await async_request_json(
                self.session,
                NWP_BASE_URL + endpoint,
                params,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
            )
        except TMDRateLimitError as error:
            self._retry_at = monotonic() + error.retry_after
            raise
        return parse_forecast(payload, kind, self.latitude, self.longitude)
