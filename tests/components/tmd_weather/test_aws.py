"""AWS identity, freshness, units, HTTP isolation and bounded discovery."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, DummyCookieJar
import pytest

from custom_components.tmd_weather.api import (
    TMDConnectionError,
    TMDInvalidLocationError,
    TMDInvalidResponseError,
    TMDRateLimitError,
    TMDServiceError,
)
from custom_components.tmd_weather.aws import (
    TMDAWSClient,
    TMDAWSDiscovery,
    async_aws_discovery,
    observation_timestamp,
    parse_aws_response,
)
from custom_components.tmd_weather.const import AWS_BASE_URL
from custom_components.tmd_weather.stations import async_load_stations

NOW = datetime(2026, 10, 11, 3, 30, tzinfo=UTC)


def test_online_and_units(aws_payload):
    response = parse_aws_response(aws_payload, 2, NOW)
    assert response.available(NOW)
    obs = response.observation
    assert obs.timestamp == datetime(2026, 10, 11, 3, tzinfo=UTC)
    assert obs.wind_speed == 2  # Raw API knots.
    assert obs.values.wind_speed == pytest.approx(1.0288888889)
    assert obs.values.visibility == 1.5
    assert obs.values.pressure == 998  # Station pressure, without sea-level conversion.
    assert obs.precipitation == 0
    assert obs.wind_direction_text == "WSW"
    assert not response.available(NOW + timedelta(hours=4))


@pytest.mark.parametrize("online", ["y", "n"])
def test_offline_or_empty(aws_payload, online):
    aws_payload["data"]["site"]["ONLINE"] = online
    for row in aws_payload["data"]["items"]:
        for field in (
            "temp",
            "humidity",
            "pressure",
            "windspeed",
            "winddir",
            "precipitation",
            "visibility",
        ):
            row[field] = None
    response = parse_aws_response(aws_payload, 2, NOW)
    assert response.status.connected
    assert response.observation is None
    assert not response.available(NOW)


def test_offline_with_old_values(aws_payload):
    aws_payload["data"]["site"]["ONLINE"] = "n"
    assert not parse_aws_response(aws_payload, 2, NOW).available(NOW)


@pytest.mark.parametrize("capability,expected", [("n", None), ("y", 0)])
def test_visibility_capability(aws_payload, capability, expected):
    aws_payload["data"]["site"]["WV_SENSOR"] = capability
    aws_payload["data"]["items"][1]["visibility"] = 0
    assert parse_aws_response(aws_payload, 2, NOW).observation.visibility == expected


def test_unsorted_and_future_rows(aws_payload):
    rows = aws_payload["data"]["items"]
    rows[2]["temp"] = 50
    rows.reverse()
    rows.insert(0, {"yyyy": True, "mm": 1, "dd": 1, "hh": 1, "temp": 10})
    rows.append({"yyyy": 2026, "mm": 13, "dd": 1, "hh": 1, "temp": 10})
    rows.append(None)
    obs = parse_aws_response(aws_payload, 2, NOW).observation
    assert obs.temperature == 30.12


@pytest.mark.parametrize(
    "payload", [None, {}, {"result": True, "data": {}}, {"result": 0}]
)
def test_invalid_envelope(payload):
    with pytest.raises(TMDInvalidResponseError):
        parse_aws_response(payload, 2, NOW)


def test_station_identity_and_missing_rows(aws_payload):
    with pytest.raises(TMDInvalidResponseError, match="does not match"):
        parse_aws_response(aws_payload, 1, NOW)
    aws_payload["data"]["items"] = None
    with pytest.raises(TMDInvalidResponseError, match="rows"):
        parse_aws_response(aws_payload, 2, NOW)


@pytest.mark.parametrize("year", [True, None, "invalid"])
def test_clock_invalid(year):
    with pytest.raises(TMDInvalidResponseError):
        observation_timestamp({"yyyy": year, "mm": 10, "dd": 11, "hh": 3})


async def test_separate_cookie_free_session(hass):
    service = await async_aws_discovery(hass)
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    assert service.client.session is not async_get_clientsession(hass)
    assert isinstance(service.client.session.cookie_jar, DummyCookieJar)
    assert not service.client.session.headers.get("Authorization")
    assert service is await async_aws_discovery(hass)


async def test_public_request(hass, aioclient_mock, aws_payload):
    service = await async_aws_discovery(hass)
    aioclient_mock.get(AWS_BASE_URL + "/2", json=aws_payload)
    with patch.object(
        service.client.session, "get", wraps=service.client.session.get
    ) as request:
        response = await service.client.async_get_station(2)
    assert response.available(NOW)
    assert str(aioclient_mock.mock_calls[0][1]) == AWS_BASE_URL + "/2"
    assert request.call_args.kwargs["headers"] == {"Accept": "application/json"}
    assert request.call_args.kwargs["params"] == {}
    assert request.call_args.kwargs["allow_redirects"] is False
    with pytest.raises(TMDInvalidLocationError):
        await service.client.async_get_station(99999)
    with pytest.raises(TMDInvalidLocationError):
        await service.client.async_get_station(True)


@pytest.mark.parametrize(
    "status,expected",
    [(401, TMDServiceError), (429, TMDRateLimitError), (500, TMDServiceError)],
)
async def test_http_errors(hass, aioclient_mock, status, expected):
    service = await async_aws_discovery(hass)
    aioclient_mock.get(AWS_BASE_URL + "/2", status=status)
    with patch(
        "aiohttp.ClientSession._request", side_effect=aioclient_mock.match_request
    ):
        with pytest.raises(expected):
            await service.client.async_get_station(2)
        if status == 429:
            with pytest.raises(TMDRateLimitError):
                await service.client.async_get_station(2)
            assert aioclient_mock.call_count == 1


async def test_discovery_cache_and_concurrency(hass, aws_payload):
    records = (await async_load_stations(hass))[:3]
    active = 0
    peak = 0
    calls = []

    async def fetch(aws_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls.append(aws_id)
        await asyncio.sleep(0)
        value = deepcopy(aws_payload)
        value["data"]["site"]["AWSID"] = aws_id
        value["data"]["site"]["LAT"] = 13.75 + aws_id * 0.01
        if aws_id == 1:
            value["data"]["site"]["ONLINE"] = "n"
        active -= 1
        return parse_aws_response(value, aws_id, NOW)

    client = TMDAWSClient(AsyncMock(spec=ClientSession), records)
    with patch.object(TMDAWSClient, "async_get_station", side_effect=fetch):
        discovery = TMDAWSDiscovery(client)
        assert await discovery.async_nearest(13.75, 100.5) == 2
        assert sorted(calls) == [1, 2, 3]
        calls.clear()
        assert await discovery.async_nearest(13.75, 100.5) == 2
        assert calls == [
            1,
            2,
        ]  # Cached geometry, fresh health checks; no complete rescan.
        assert peak <= 4
        with pytest.raises(TMDInvalidLocationError):
            await discovery.async_nearest(0, 0)


async def test_discovery_skips_stale_missing_failed(hass, aws_payload):
    records = (await async_load_stations(hass))[:3]
    client = TMDAWSClient(AsyncMock(spec=ClientSession), records)

    async def fetch(aws_id):
        if aws_id == 1:
            raise TMDConnectionError
        payload = deepcopy(aws_payload)
        payload["data"]["site"]["AWSID"] = aws_id
        if aws_id == 2:
            payload["data"]["site"]["LAT"] = None
        return parse_aws_response(payload, aws_id, NOW - timedelta(hours=5))

    with (
        patch.object(TMDAWSClient, "async_get_station", side_effect=fetch),
        pytest.raises(TMDInvalidLocationError),
    ):
        await TMDAWSDiscovery(client).async_nearest(13.75, 100.5)
