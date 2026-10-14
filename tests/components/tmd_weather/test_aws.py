"""AWS identity, freshness, units, HTTP isolation and bounded discovery."""

from copy import deepcopy
from dataclasses import replace
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
from custom_components.tmd_weather.observations import distance_km
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


async def test_discovery_stops_at_nearest_usable(nearby_aws_catalog, aws_payload):
    """Never contact a farther station once a usable nearer candidate is found."""
    records = nearby_aws_catalog
    calls = []

    async def fetch(aws_id):
        calls.append(aws_id)
        value = deepcopy(aws_payload)
        value["data"]["site"]["AWSID"] = aws_id
        value["data"]["site"]["LAT"] = 13.75 + aws_id * 0.01
        if aws_id == 1:
            value["data"]["site"]["ONLINE"] = "n"
        return parse_aws_response(value, aws_id, NOW)

    client = TMDAWSClient(AsyncMock(spec=ClientSession), records)
    with patch.object(TMDAWSClient, "async_get_station", side_effect=fetch):
        discovery = TMDAWSDiscovery(client)
        assert await discovery.async_nearest(13.75, 100.5) == 2
        assert calls == [1, 2]
        calls.clear()
        assert await discovery.async_nearest(13.75, 100.5) == 2
        assert calls == [
            2
        ]  # Skip a recently verified offline station; recheck the choice.
        calls.clear()
        with pytest.raises(TMDInvalidLocationError):
            await discovery.async_nearest(0, 0)
        assert not calls


async def test_discovery_skips_stale_missing_failed(nearby_aws_catalog, aws_payload):
    records = nearby_aws_catalog
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


async def test_full_catalog_needs_only_nearest_request(hass, aws_payload):
    """Cold selection works with all supplied coordinates and no metadata scan."""
    catalog = await async_load_stations(hass)
    discovery = TMDAWSDiscovery(TMDAWSClient(AsyncMock(spec=ClientSession), catalog))
    nearest = catalog[2]
    payload = deepcopy(aws_payload)
    payload["data"]["site"].update(
        AWSID=nearest.aws_id, LAT=nearest.latitude, LON=nearest.longitude
    )
    response = parse_aws_response(payload, nearest.aws_id, NOW)
    progress = []
    with patch.object(
        TMDAWSClient, "async_get_station", return_value=response
    ) as request:
        assert (
            await discovery.async_nearest(
                16.82, 100.27, lambda checked, total: progress.append((checked, total))
            )
            == nearest.aws_id
        )
        request.assert_awaited_once_with(nearest.aws_id)
    candidates = discovery.nearby_stations(16.82, 100.27)
    assert 1 < len(candidates) < len(catalog)
    assert progress == [(0, len(candidates)), (1, len(candidates))]
    assert all(distance <= 100 for distance, _ in candidates)
    assert candidates[0][0] == pytest.approx(
        distance_km(16.82, 100.27, nearest.latitude, nearest.longitude)
    )


@pytest.mark.parametrize(
    "failure", ["offline", "empty", "stale", "future", "missing_location", "moved"]
)
async def test_nearest_falls_back_after_live_validation(
    nearby_aws_catalog, aws_payload, failure
):
    """Local distance alone never bypasses live health/location validation."""
    discovery = TMDAWSDiscovery(
        TMDAWSClient(AsyncMock(spec=ClientSession), nearby_aws_catalog)
    )

    async def fetch(aws_id):
        payload = deepcopy(aws_payload)
        payload["data"]["site"]["AWSID"] = aws_id
        if aws_id == 1:
            match failure:
                case "offline":
                    payload["data"]["site"]["ONLINE"] = "n"
                case "empty":
                    payload["data"]["items"] = []
                case "stale":
                    for row in payload["data"]["items"]:
                        row["yyyy"] = 2025
                case "future":
                    for row in payload["data"]["items"]:
                        row["yyyy"] = 2027
                case "missing_location":
                    payload["data"]["site"]["LAT"] = None
                case "moved":
                    payload["data"]["site"]["LAT"] = 18.75
        return parse_aws_response(payload, aws_id, NOW)

    with patch.object(TMDAWSClient, "async_get_station", side_effect=fetch) as request:
        assert await discovery.async_nearest(13.75, 100.5) == 2
        assert [call.args[0] for call in request.await_args_list] == [1, 2]


async def test_nearest_crosses_region_borders(nearby_aws_catalog, aws_payload):
    """Region text never excludes a closer station across an administrative border."""
    records = (
        nearby_aws_catalog[0],
        replace(nearby_aws_catalog[1], latitude=13.75, region="Central"),
    )
    discovery = TMDAWSDiscovery(TMDAWSClient(AsyncMock(spec=ClientSession), records))
    payload = deepcopy(aws_payload)
    with patch.object(
        TMDAWSClient,
        "async_get_station",
        return_value=parse_aws_response(payload, 2, NOW),
    ) as request:
        assert await discovery.async_nearest(13.75, 100.5) == 2
        request.assert_awaited_once_with(2)


async def test_equal_distances_have_stable_order(nearby_aws_catalog):
    records = (
        replace(nearby_aws_catalog[1], latitude=13.76),
        nearby_aws_catalog[0],
    )
    discovery = TMDAWSDiscovery(TMDAWSClient(AsyncMock(spec=ClientSession), records))
    assert [record.aws_id for _, record in discovery.nearby_stations(13.75, 100.5)] == [
        1,
        2,
    ]


async def test_no_nearby_candidates_never_requests_weather(nearby_aws_catalog):
    store = AsyncMock()
    discovery = TMDAWSDiscovery(
        TMDAWSClient(AsyncMock(spec=ClientSession), nearby_aws_catalog), store
    )
    with patch.object(TMDAWSClient, "async_get_station") as request:
        with pytest.raises(TMDInvalidLocationError):
            await discovery.async_nearest(0, 0)
        request.assert_not_awaited()
        store.async_load.assert_not_awaited()


@pytest.mark.parametrize(
    "latitude,longitude",
    [(True, 100.5), (91, 100.5), (13.75, -181), (float("nan"), 100.5)],
)
async def test_invalid_search_coordinates(nearby_aws_catalog, latitude, longitude):
    discovery = TMDAWSDiscovery(
        TMDAWSClient(AsyncMock(spec=ClientSession), nearby_aws_catalog)
    )
    with patch.object(TMDAWSClient, "async_get_station") as request:
        with pytest.raises(TMDInvalidLocationError):
            await discovery.async_nearest(latitude, longitude)
        request.assert_not_awaited()
