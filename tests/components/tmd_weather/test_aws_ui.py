"""Station confirmation, persistent controls/status and explicit modeled labels."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.tmd_weather.api import TMDConnectionError
from custom_components.tmd_weather.aws import (
    TMDAWSClient,
    TMDAWSDiscovery,
    parse_aws_response,
)
from custom_components.tmd_weather.sensor import TMDAWSStatusSensor
from custom_components.tmd_weather.stations import async_load_stations
from custom_components.tmd_weather.weather import TMDWeatherEntity


async def test_confirmation_can_choose_nearby_alternative(
    hass, mock_client, mock_aws_client, config_entry
):
    """The confirmation dropdown validates and saves a different nearby station."""
    from dataclasses import replace

    original = mock_aws_client.side_effect

    async def fetch(aws_id):
        response = await original(aws_id)
        if aws_id == 3:
            return replace(response, status=replace(response.status, latitude=13.8))
        return response

    mock_aws_client.side_effect = fetch
    hass.config.latitude = 13.75
    hass.config.longitude = 100.5
    result = await hass.config_entries.flow.async_init(
        "tmd_weather",
        context={"source": "user"},
        data={**config_entry.data, "current_source": "aws_station"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_selection_mode": "automatic"}
    )
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    from homeassistant.data_entry_flow import FlowResultType

    if result["type"] == FlowResultType.SHOW_PROGRESS_DONE:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["data_schema"]({})["aws_station_id"] == "2"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_station_id": "3"}
    )
    await hass.async_block_till_done()
    assert result["data"]["aws_station_id"] == 3
    assert result["data"]["aws_selection_mode"] == "manual"
    assert (result["data"]["latitude"], result["data"]["longitude"]) == (13.75, 100.5)


async def test_station_controls_and_change(
    hass, aws_setup, entity_registry, mock_aws_client
):
    """Visible station controls preserve the forecast location when changed."""
    selected = entity_registry.async_get_entity_id(
        "sensor", "tmd_weather", f"{aws_setup.unique_id}_aws_station"
    )
    control = entity_registry.async_get_entity_id(
        "select", "tmd_weather", f"{aws_setup.unique_id}_aws_station_select"
    )
    status = entity_registry.async_get_entity_id(
        "sensor", "tmd_weather", f"{aws_setup.unique_id}_aws_station_status"
    )
    assert hass.states.get(selected).state == "0002 — Tak Weather Observing Station"
    assert hass.states.get(status).state == "online"
    assert hass.states.get(control).state == hass.states.get(selected).state
    assert not any(
        "0060" in option for option in hass.states.get(control).attributes["options"]
    )
    before = (aws_setup.data["latitude"], aws_setup.data["longitude"])
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": control, "option": "0003 — Phitanulok Weather Observing Station"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert aws_setup.data["aws_station_id"] == 3
    assert (aws_setup.data["latitude"], aws_setup.data["longitude"]) == before
    assert hass.states.get(selected).attributes["aws_station_id"] == 3
    assert (
        hass.states.get(control).state == "0003 — Phitanulok Weather Observing Station"
    )


async def test_failed_station_change_retains_selection(
    hass, aws_setup, entity_registry, mock_aws_client
):
    control = entity_registry.async_get_entity_id(
        "select", "tmd_weather", f"{aws_setup.unique_id}_aws_station_select"
    )
    mock_aws_client.side_effect = TMDConnectionError
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": control,
                "option": "0001 — Chiang Mai Airport Weather Observing Station",
            },
            blocking=True,
        )
    assert aws_setup.data["aws_station_id"] == 2


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("offline", "offline"),
        ("empty", "no_observations"),
        ("stale", "stale"),
        ("failed", "connection_error"),
    ],
)
async def test_station_status_explains_missing_readings(
    hass, aws_setup, aws_payload, scenario, expected
):
    coordinator = aws_setup.runtime_data.aws_coordinator
    value = deepcopy(aws_payload)
    if scenario == "offline":
        value["data"]["site"]["ONLINE"] = "n"
    if scenario == "empty":
        value["data"]["items"] = []
    if scenario == "stale":
        for row in value["data"]["items"]:
            row["dd"] = "10"
    coordinator.async_set_updated_data(parse_aws_response(value, 2, dt_util.utcnow()))
    if scenario == "failed":
        coordinator.last_update_success = False
    assert TMDAWSStatusSensor(aws_setup).native_value == expected
    assert TMDAWSStatusSensor(aws_setup).available
    assert TMDWeatherEntity(aws_setup).native_temperature is None
    assert TMDWeatherEntity(aws_setup).condition == "rainy"


async def test_no_stale_or_guessed_modeled_condition(hass, aws_setup, freezer):
    from dataclasses import replace

    entity = TMDWeatherEntity(aws_setup)
    hourly = aws_setup.runtime_data.hourly_coordinator
    assert entity.condition == "rainy"
    assert entity.native_temperature == 30.12
    data = hourly.data
    hourly.async_set_updated_data(
        replace(
            data,
            points=tuple(
                replace(p, values=replace(p.values, condition_code=12))
                for p in data.points
            ),
        )
    )
    assert entity.condition is None
    assert entity.native_temperature == 30.12
    hourly.async_set_updated_data(data)
    hourly.last_update_success = False
    assert entity.condition is None
    hourly.last_update_success = True
    freezer.tick(timedelta(hours=5))
    assert entity.condition is None


async def test_discovery_persists_geometry_and_reports_progress(hass, aws_payload):
    records = (await async_load_stations(hass))[:3]
    client = TMDAWSClient(AsyncMock(spec=ClientSession), records)
    stored = {}
    store = AsyncMock()
    store.async_load.return_value = None

    async def save(data):
        stored.update(deepcopy(data))

    store.async_save.side_effect = save
    progress = []

    async def fetch(aws_id):
        value = deepcopy(aws_payload)
        value["data"]["site"]["AWSID"] = aws_id
        value["data"]["site"]["LAT"] = 13.75 + aws_id * 0.01
        return parse_aws_response(value, aws_id, dt_util.utcnow())

    with patch.object(TMDAWSClient, "async_get_station", side_effect=fetch) as request:
        first = TMDAWSDiscovery(client, store)
        assert (
            await first.async_nearest(
                13.75, 100.5, lambda c, t: progress.append((c, t))
            )
            == 1
        )
        assert progress[0] == (0, 3)
        assert progress[-1] == (3, 3)
        assert len(stored["stations"]) == 3
        assert "access_token" not in json.dumps(stored)
        assert "items" not in json.dumps(stored)
        store.async_load.return_value = stored
        request.reset_mock()
        restored = TMDAWSDiscovery(client, store)
        assert await restored.async_nearest(13.75, 100.5) == 1
        request.assert_awaited_once_with(1)
        assert len(restored.cache) == 3


async def test_cache_expiry_and_invalid_data(hass, aws_payload):
    records = (await async_load_stations(hass))[:1]
    client = TMDAWSClient(AsyncMock(spec=ClientSession), records)
    store = AsyncMock()
    store.async_load.return_value = {
        "stations": [
            {"expires_at": dt_util.utcnow().timestamp() - 1, "site": {"AWSID": 1}},
            {
                "expires_at": dt_util.utcnow().timestamp() + 3600,
                "site": {"AWSID": 99999},
            },
        ]
    }
    value = deepcopy(aws_payload)
    value["data"]["site"]["AWSID"] = 1
    with patch.object(
        TMDAWSClient,
        "async_get_station",
        return_value=parse_aws_response(value, 1, dt_util.utcnow()),
    ) as request:
        assert await TMDAWSDiscovery(client, store).async_nearest(13.75, 100.5) == 1
        request.assert_awaited_once_with(1)


async def test_optional_cache_errors_do_not_block_selection(hass, aws_payload):
    records = (await async_load_stations(hass))[:1]
    client = TMDAWSClient(AsyncMock(spec=ClientSession), records)
    store = AsyncMock()
    store.async_load.side_effect = OSError
    store.async_save.side_effect = OSError
    value = deepcopy(aws_payload)
    value["data"]["site"]["AWSID"] = 1
    with patch.object(
        TMDAWSClient,
        "async_get_station",
        return_value=parse_aws_response(value, 1, dt_util.utcnow()),
    ):
        assert await TMDAWSDiscovery(client, store).async_nearest(13.75, 100.5) == 1


async def test_unusable_station_cache_expires_and_recovers(
    hass, aws_payload, monkeypatch
):
    """Recheck unusable candidates after expiry and recover on fresh live evidence."""
    records = (await async_load_stations(hass))[:2]
    client = TMDAWSClient(AsyncMock(spec=ClientSession), records)
    current = [100.0]
    monkeypatch.setattr(
        "custom_components.tmd_weather.aws.monotonic", lambda: current[0]
    )
    online = False

    async def fetch(aws_id):
        payload = deepcopy(aws_payload)
        payload["data"]["site"]["AWSID"] = aws_id
        payload["data"]["site"]["LAT"] = 13.75 + aws_id * 0.01
        if aws_id == 1 and not online:
            payload["data"]["site"]["ONLINE"] = "n"
        return parse_aws_response(payload, aws_id, dt_util.utcnow())

    discovery = TMDAWSDiscovery(client)
    with patch.object(TMDAWSClient, "async_get_station", side_effect=fetch) as request:
        assert await discovery.async_nearest(13.75, 100.5) == 2
        online = True
        request.reset_mock()
        assert await discovery.async_nearest(13.75, 100.5) == 2
        request.assert_awaited_once_with(2)
        current[0] += 601
        assert await discovery.async_nearest(13.75, 100.5) == 1
        offline = deepcopy(aws_payload)
        offline["data"]["site"]["AWSID"] = 1
        offline["data"]["site"]["ONLINE"] = "n"
        discovery.note_response(1, parse_aws_response(offline, 1, dt_util.utcnow()))
        discovery.note_response(1, await fetch(1))
        assert await discovery.async_nearest(13.75, 100.5) == 1


def test_live_response_shape():
    """Read the sanitized live response without embedded forecasts or token metadata."""
    payload = json.loads((Path(__file__).parent / "fixtures/aws_live.json").read_text())
    response = parse_aws_response(payload, 2, datetime(2026, 10, 14, 2, 30, tzinfo=UTC))
    assert response.available(datetime(2026, 10, 14, 2, 30, tzinfo=UTC))
    assert response.observation.temperature == 31.99
    assert response.observation.visibility is None
    assert response.status.api_name_en == "Tak Weather Observing Station"
