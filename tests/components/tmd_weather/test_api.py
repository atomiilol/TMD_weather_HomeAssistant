"""Contract tests for HTTP handling, parsing and modeled-current selection."""

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientConnectionError, ClientSession
import pytest

from custom_components.tmd_weather.api import (
    TMDAuthError,
    TMDClient,
    TMDConnectionError,
    TMDInvalidLocationError,
    TMDInvalidResponseError,
    TMDRateLimitError,
    TMDServiceError,
    _retry_delay,
    current_point,
    location_id,
    number,
    parse_forecast,
)
from custom_components.tmd_weather.const import (
    DAILY_ENDPOINT,
    HOURLY_ENDPOINT,
    NWP_BASE_URL,
)


@pytest.fixture(autouse=True)
def mock_all_requests(aioclient_mock):
    """Also mock caller-owned sessions used by the standalone API tests."""
    with patch(
        "aiohttp.ClientSession._request", side_effect=aioclient_mock.match_request
    ):
        yield


def test_hourly(hourly_json):
    """Accept fractional and integral fields and preserve UTC+7."""
    result = parse_forecast(hourly_json, "hourly", 13.75, 100.5)
    assert len(result.points) == 3
    point = result.points[0]
    assert point.time.isoformat() == "2026-10-11T10:00:00+07:00"
    assert point.values.temperature == 30.5
    assert point.values.pressure == 1008.4
    assert point.values.wind_speed == 3.5
    assert result.points[1].values.precipitation == 0
    assert result.points[2].values.humidity is None


def test_daily(daily_json):
    """Use daily high/low and millimeters of rain."""
    result = parse_forecast(daily_json, "daily", 13.75, 100.5)
    assert result.points[0].values.temperature == 34.5
    assert result.points[0].values.temperature_low == 25
    assert result.points[0].values.precipitation == 12.5


@pytest.mark.parametrize("kind", ["hourly", "daily"])
def test_official_documentation_examples(kind):
    """Parse exact coordinate examples retrieved from the official TMD docs."""
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / f"documented_{kind}.json").read_text()
    )
    result = parse_forecast(payload, kind, 13.10, 100.10)
    assert result.latitude == 13.0068
    assert result.longitude == 100.0829
    assert len(result.points) == 2
    assert result.points[0].values.temperature == 28.56
    assert result.points[0].values.humidity == 88.54
    assert result.points[0].time.isoformat().endswith("+07:00")


@pytest.mark.parametrize(
    "envelope", [None, [], {}, {"locations": None}, {"locations": []}]
)
def test_invalid_daily_envelope(envelope):
    """Reject malformed nested daily envelopes without inventing a series."""
    with pytest.raises(TMDInvalidResponseError):
        parse_forecast({"weather_forecast": envelope}, "daily", 13.75, 100.5)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"WeatherForcasts": []},
        {"WeatherForcasts": None},
        {"WeatherForcasts": [None]},
        {"WeatherForcasts": [{"location": None}]},
    ],
)
def test_invalid_envelope(payload):
    """Reject structurally unusable responses."""
    with pytest.raises(TMDInvalidResponseError):
        parse_forecast(payload, "hourly", 13.75, 100.5)


@pytest.mark.parametrize(
    "field,value",
    [
        ("forecasts", []),
        ("forecasts", None),
        ("forecasts", [{}]),
        ("forecasts", [{"time": "2026-10-11T10:00:00+07:00", "data": None}]),
        ("location", {"lat": "invalid", "lon": 100}),
    ],
)
def test_invalid_structure(hourly_json, field, value):
    """Invalid structure fails the update instead of losing previous good data."""
    hourly_json["WeatherForcasts"][0][field] = value
    with pytest.raises(TMDInvalidResponseError):
        parse_forecast(hourly_json, "hourly", 13.75, 100.5)


@pytest.mark.parametrize(
    "timestamp", [None, "bad", "2026-10-11T10:00:00", "2026-10-11"]
)
def test_invalid_time(hourly_json, timestamp):
    """Never assume an unspecified timestamp is UTC or Bangkok time."""
    hourly_json["WeatherForcasts"][0]["forecasts"][0]["time"] = timestamp
    with pytest.raises(TMDInvalidResponseError):
        parse_forecast(hourly_json, "hourly", 13.75, 100.5)


def test_duplicate_and_empty_values(hourly_json):
    """Duplicate times and entirely unusable temperatures are invalid."""
    points = hourly_json["WeatherForcasts"][0]["forecasts"]
    original = points[1]["time"]
    points[1]["time"] = points[0]["time"]
    with pytest.raises(TMDInvalidResponseError):
        parse_forecast(hourly_json, "hourly", 13.75, 100.5)
    points[1]["time"] = original
    for point in points:
        point["data"] = {}
    with pytest.raises(TMDInvalidResponseError):
        parse_forecast(hourly_json, "hourly", 13.75, 100.5)


@pytest.mark.parametrize(
    "value", [None, "12", True, float("nan"), float("inf"), 10**1000]
)
def test_invalid_numbers(value):
    """Do not coerce strings, booleans or nonfinite values into weather."""
    assert number(value) is None


def test_optional_values(hourly_json):
    """Optional malformed fields do not invalidate real temperatures."""
    hourly_json["WeatherForcasts"][0]["forecasts"][0]["data"] = {
        "tc": 0,
        "rh": 101,
        "slp": None,
        "rain": -1,
        "ws10m": -5,
        "wd10m": 999,
        "cond": 1.5,
    }
    values = parse_forecast(hourly_json, "hourly", 13.75, 100.5).points[0].values
    assert values.temperature == 0
    assert values.humidity is None
    assert values.precipitation is None
    assert values.pressure is None
    assert values.wind_speed is None
    assert values.wind_bearing is None
    assert values.condition_code is None


def test_multiple_locations_and_spelling(hourly_json):
    """Choose one nearest model location deterministically, regardless of order."""
    far = deepcopy(hourly_json["WeatherForcasts"][0])
    far["location"] = {"lat": 16, "lon": 101}
    hourly_json["WeatherForecasts"] = [far, *hourly_json.pop("WeatherForcasts")]
    result = parse_forecast(hourly_json, "hourly", 13.75, 100.5)
    assert result.latitude == 13.75


@pytest.mark.parametrize(
    "time,temperature",
    [
        ("2026-10-11T02:59:59+00:00", None),
        ("2026-10-11T03:00:00+00:00", 30.5),
        ("2026-10-11T03:59:59+00:00", 30.5),
        ("2026-10-11T04:00:00+00:00", 31),
        ("2026-10-11T06:00:00+00:00", None),
    ],
)
def test_current_interval(hourly_json, time, temperature):
    """Current means the active model hour, never the first arbitrary forecast."""
    point = current_point(
        parse_forecast(hourly_json, "hourly", 13.75, 100.5),
        datetime.fromisoformat(time),
    )
    assert (point.values.temperature if point else None) == temperature


def test_location_identity():
    """Stable coordinate identity is independent of title and signed zero."""
    assert location_id(13.750001, 100.500001) == "13.7500,100.5000"
    assert location_id(-0.000001, -0.0) == "0.0000,0.0000"


@pytest.mark.parametrize("kind", ["hourly", "daily"])
async def test_http_success(aioclient_mock, hourly_json, daily_json, kind):
    """Send only the NWP token and useful fields to the fixed HTTPS endpoint."""
    endpoint = HOURLY_ENDPOINT if kind == "hourly" else DAILY_ENDPOINT
    aioclient_mock.get(
        NWP_BASE_URL + endpoint, json=hourly_json if kind == "hourly" else daily_json
    )
    async with ClientSession() as session:
        client = TMDClient(session, "test-secret", 13.75, 100.5)
        result = await (
            client.async_get_hourly_forecast()
            if kind == "hourly"
            else client.async_get_daily_forecast()
        )
        assert result.points
        assert "test-secret" not in repr(client)
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert headers == {
        "Authorization": "Bearer test-secret",
        "Accept": "application/json",
    }
    assert "test-secret" not in str(url)
    assert url.query["date"] == "2026-10-11"
    assert url.query["duration"] == ("48" if kind == "hourly" else "7")
    if kind == "hourly":
        assert url.query["hour"] == "10"
    assert "swdown" not in url.query["fields"]


@pytest.mark.parametrize(
    "status,error",
    [
        (401, TMDAuthError),
        (403, TMDAuthError),
        (404, TMDServiceError),
        (422, TMDInvalidLocationError),
        (429, TMDRateLimitError),
        (500, TMDServiceError),
        (503, TMDServiceError),
        (302, TMDServiceError),
    ],
)
async def test_http_errors(aioclient_mock, status, error):
    """Status errors never echo provider bodies or credentials."""
    aioclient_mock.get(
        NWP_BASE_URL + HOURLY_ENDPOINT, status=status, text="test-secret"
    )
    async with ClientSession() as session:
        with pytest.raises(error) as caught:
            await TMDClient(
                session, "test-secret", 13.75, 100.5
            ).async_get_hourly_forecast()
    assert "test-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "error", [ClientConnectionError("test-secret"), TimeoutError("test-secret")]
)
async def test_network_errors(aioclient_mock, error):
    """Connection errors and timeouts expose no request internals."""
    aioclient_mock.get(NWP_BASE_URL + HOURLY_ENDPOINT, exc=error)
    async with ClientSession() as session:
        with pytest.raises(TMDConnectionError) as caught:
            await TMDClient(
                session, "test-secret", 13.75, 100.5
            ).async_get_hourly_forecast()
    assert "test-secret" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "body", ["not JSON", "null", "[]", "{}", "x" * (2 * 1024 * 1024 + 1)]
)
async def test_bad_body(aioclient_mock, body):
    """Malformed JSON, empty data and oversized responses fail safely."""
    aioclient_mock.get(NWP_BASE_URL + HOURLY_ENDPOINT, text=body)
    async with ClientSession() as session:
        with pytest.raises(TMDInvalidResponseError):
            await TMDClient(
                session, "test-secret", 13.75, 100.5
            ).async_get_hourly_forecast()


async def test_retry_after(aioclient_mock, hourly_json):
    """Block both endpoints until the account-wide Retry-After has passed."""
    aioclient_mock.get(
        NWP_BASE_URL + HOURLY_ENDPOINT, status=429, headers={"Retry-After": "120"}
    )
    async with ClientSession() as session:
        client = TMDClient(session, "test-secret", 13.75, 100.5)
        with (
            patch("custom_components.tmd_weather.api.monotonic", return_value=100),
            pytest.raises(TMDRateLimitError),
        ):
            await client.async_get_hourly_forecast()
        with (
            patch("custom_components.tmd_weather.api.monotonic", return_value=219),
            pytest.raises(TMDRateLimitError),
        ):
            await client.async_get_daily_forecast()
        assert aioclient_mock.call_count == 1
        aioclient_mock.clear_requests()
        aioclient_mock.get(NWP_BASE_URL + HOURLY_ENDPOINT, json=hourly_json)
        with patch("custom_components.tmd_weather.api.monotonic", return_value=220):
            assert (await client.async_get_hourly_forecast()).points


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 3600),
        ("bad", 3600),
        ("nan", 3600),
        ("60", 60),
        ("0", 1),
        ("Sun, 11 Oct 2026 03:32:00 GMT", 120),
    ],
)
def test_retry_delay(value, expected):
    """Support Retry-After dates and conservative defaults."""
    assert _retry_delay(value) == expected


@pytest.mark.parametrize(
    "when,date,hour",
    [
        ("2024-02-28T17:30:00+00:00", "2024-02-29", "0"),
        ("2024-02-29T17:00:00+00:00", "2024-03-01", "0"),
        ("2026-12-31T17:00:00+00:00", "2027-01-01", "0"),
    ],
)
async def test_bangkok_rollover(aioclient_mock, hourly_json, freezer, when, date, hour):
    """Request dates use Bangkok independently of HA/server timezone."""
    freezer.move_to(datetime.fromisoformat(when).astimezone(UTC))
    aioclient_mock.get(NWP_BASE_URL + HOURLY_ENDPOINT, json=hourly_json)
    async with ClientSession() as session:
        await TMDClient(
            session, "test-secret", 13.75, 100.5
        ).async_get_hourly_forecast()
    query = aioclient_mock.mock_calls[0][1].query
    assert query["date"] == date
    assert query["hour"] == hour


@pytest.mark.parametrize(
    "latitude,longitude", [(91, 100), (13, 181), (float("nan"), 100)]
)
async def test_invalid_coordinates(latitude, longitude):
    """Reject unusable coordinates before issuing an HTTP request."""
    async with ClientSession() as session:
        with pytest.raises(TMDInvalidLocationError):
            TMDClient(session, "test-secret", latitude, longitude)


@pytest.mark.parametrize("token", ["", " ", "abc\r\ninjected"])
async def test_invalid_token(token):
    """Reject blank or header-injection credentials locally."""
    async with ClientSession() as session:
        with pytest.raises(TMDAuthError):
            TMDClient(session, token, 13.75, 100.5)


async def test_invalid_duration():
    """Keep duration inside the deliberately supported ranges."""
    async with ClientSession() as session:
        client = TMDClient(session, "test-secret", 13.75, 100.5)
        with pytest.raises(ValueError):
            await client.async_get_hourly_forecast(duration=49)
        with pytest.raises(ValueError):
            await client.async_get_daily_forecast(duration=8)
