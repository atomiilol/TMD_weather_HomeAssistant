"""Real HA flows, entity lifecycle and forecast isolation for AWS beta."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.util import dt as dt_util
import pytest

from custom_components.tmd_weather.api import (
    TMDConnectionError,
    TMDInvalidResponseError,
)
from custom_components.tmd_weather.aws import parse_aws_response
from custom_components.tmd_weather.const import DOMAIN
from custom_components.tmd_weather.diagnostics import async_get_config_entry_diagnostics
from custom_components.tmd_weather.stations import TMDStationDatabaseError
from custom_components.tmd_weather.weather import TMDWeatherEntity


async def begin_aws(hass, mock_client, config_entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={**config_entry.data, "current_source": "aws_station"},
    )
    assert result["step_id"] == "aws_selection"
    return result


async def test_manual_setup(hass, mock_client, mock_aws_client, config_entry):
    hass.config.latitude = 13.75
    hass.config.longitude = 100.5
    initial = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert (
        initial["data_schema"]({"access_token": "secret"})["current_source"]
        == "aws_station"
    )
    result = await begin_aws(hass, mock_client, config_entry)
    defaults = result["data_schema"]({})
    assert defaults["aws_selection_mode"] == "automatic"
    assert defaults["aws_latitude"] == hass.config.latitude
    assert defaults["aws_longitude"] == hass.config.longitude
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_selection_mode": "manual"}
    )
    assert result["step_id"] == "aws_region"
    assert not mock_aws_client.call_count
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "Northern"}
    )
    selector = next(iter(result["data_schema"].schema.values()))
    choices = selector.config["options"]
    assert {"value": "2", "label": "0002 — Tak Weather Observing Station"} in choices
    assert not any(choice["value"] == "60" for choice in choices)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_station_id": "2"}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["aws_station_id"] == 2
    assert result["data"]["latitude"] == 13.75
    assert result["data"]["longitude"] == 100.5
    assert result["result"].runtime_data.aws_coordinator.aws_id == 2


async def test_automatic_setup(hass, mock_client, mock_aws_client, config_entry):
    hass.config.latitude = 13.75
    hass.config.longitude = 100.5
    result = await begin_aws(hass, mock_client, config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_selection_mode": "automatic"}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    if result["type"] == FlowResultType.SHOW_PROGRESS_DONE:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["step_id"] == "aws_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.data["aws_station_id"] == 2
    assert entry.data["aws_selection_mode"] == "automatic"
    mock_aws_client.reset_mock()
    await entry.runtime_data.aws_coordinator.async_refresh()
    mock_aws_client.assert_awaited_once_with(2)


async def test_manual_invalid_and_failed_preserves_entry(
    hass, aws_setup, mock_aws_client
):
    original = dict(aws_setup.data)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": aws_setup.entry_id},
        data={"current_source": "aws_station"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_selection_mode": "manual"}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"region": "Unknown"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "North Eastern"}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"aws_station_id": "2"}
        )
    mock_aws_client.side_effect = TMDInvalidResponseError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_station_id": "60"}
    )
    assert result["errors"]["base"] == "invalid_response"
    assert dict(aws_setup.data) == original


async def test_reconfigure_station_preserves_entities_and_forecasts(
    hass, aws_setup, mock_aws_client, entity_registry
):
    ids = {row.unique_id: row.entity_id for row in entity_registry.entities.values()}
    before = dict(aws_setup.data)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": aws_setup.entry_id},
        data={"current_source": "aws_station"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "aws_selection_mode": "manual",
            "aws_use_ha_location": False,
            "aws_latitude": 18.75,
            "aws_longitude": 100.5,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "North Eastern"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_station_id": "60"}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert aws_setup.data["aws_station_id"] == 60
    assert aws_setup.data["latitude"] == before["latitude"]
    assert aws_setup.data["longitude"] == before["longitude"]
    assert aws_setup.data["aws_latitude"] == 18.75
    assert ids == {
        row.unique_id: row.entity_id for row in entity_registry.entities.values()
    }


async def test_runtime_values_diagnostics_and_offline_forecasts(
    hass, aws_setup, aws_payload, mock_client, mock_aws_client
):
    entity = TMDWeatherEntity(aws_setup)
    assert entity.native_temperature == 30.12
    assert entity.native_wind_speed == pytest.approx(1.0288888889)
    assert entity.native_pressure == 998
    assert entity.native_visibility == 1.5
    assert entity.condition is None
    assert entity.extra_state_attributes["pressure_type"] == "station"
    aws = aws_setup.runtime_data.aws_coordinator
    assert aws.update_interval == timedelta(minutes=10)
    diagnostics = await async_get_config_entry_diagnostics(hass, aws_setup)
    assert diagnostics["aws"]["station_database"]["aws_id"] == 2
    assert diagnostics["aws"]["station_live"]["online"]
    assert diagnostics["aws"]["observation_available"]
    assert diagnostics["aws"]["distance_km"] == 0
    assert diagnostics["current_source"] == "aws_station"
    assert "test-secret" not in json.dumps(diagnostics)
    offline = deepcopy(aws_payload)
    offline["data"]["site"]["ONLINE"] = "n"
    aws.async_set_updated_data(parse_aws_response(offline, 2, dt_util.utcnow()))
    await hass.async_block_till_done()
    assert entity.available
    assert entity.native_temperature is None
    entity_id = hass.states.async_entity_ids("weather")[0]
    for kind in ("hourly", "daily"):
        result = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": entity_id, "type": kind},
            blocking=True,
            return_response=True,
        )
        assert result[entity_id]["forecast"][0]["temperature"] == (
            30.5 if kind == "hourly" else 34.5
        )
    mock_client.async_get_hourly_forecast.assert_awaited_once_with()


async def test_stale_cached_aws(hass, aws_setup, freezer):
    entity = TMDWeatherEntity(aws_setup)
    freezer.tick(timedelta(hours=4))
    assert entity.native_temperature is None
    assert entity.available


async def test_startup_aws_failure_does_not_stop_forecasts(
    hass, aws_config_entry, mock_client, mock_aws_client
):
    mock_aws_client.side_effect = TMDConnectionError
    aws_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(aws_config_entry.entry_id)
    await hass.async_block_till_done()
    assert not aws_config_entry.runtime_data.aws_coordinator.last_update_success
    assert aws_config_entry.runtime_data.daily_coordinator.last_update_success
    assert len(hass.states.async_entity_ids("weather")) == 1


async def test_aws_reauth_ignores_observation_outage(hass, aws_setup, mock_aws_client):
    """An unauthenticated AWS outage cannot block replacement NWP credentials."""
    mock_aws_client.side_effect = TMDConnectionError
    result = await aws_setup.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"access_token": "replacement-secret"}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert aws_setup.data["access_token"] == "replacement-secret"
    assert aws_setup.data["aws_station_id"] == 2


async def test_manual_offline_setup(
    hass, mock_client, mock_aws_client, config_entry, aws_payload
):
    """Offline stations can be saved manually; forecasts still load."""
    aws_payload["data"]["site"]["ONLINE"] = "n"
    result = await begin_aws(hass, mock_client, config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_selection_mode": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "Northern"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_station_id": "2"}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert TMDWeatherEntity(result["result"]).native_temperature is None
    assert TMDWeatherEntity(result["result"])._async_forecast_daily()


async def test_invalid_catalog_flow_and_setup(
    hass, mock_client, config_entry, aws_config_entry
):
    """Missing/malformed packaged data produces a clean translated flow/setup error."""
    with patch(
        "custom_components.tmd_weather.aws.async_load_stations",
        side_effect=TMDStationDatabaseError("bad catalog"),
    ):
        result = await begin_aws(hass, mock_client, config_entry)
        assert result["errors"]["base"] == "invalid_station_database"
        aws_config_entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(aws_config_entry.entry_id)
        await hass.async_block_till_done()


async def test_automatic_failure_offers_manual(
    hass, mock_client, mock_aws_client, config_entry
):
    """A failed scan does not save an offline station and leaves a manual choice."""
    from custom_components.tmd_weather.api import TMDInvalidLocationError

    with patch(
        "custom_components.tmd_weather.aws.TMDAWSDiscovery.async_nearest",
        side_effect=TMDInvalidLocationError,
    ):
        result = await begin_aws(hass, mock_client, config_entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"aws_selection_mode": "automatic"}
        )
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        if result["type"] == FlowResultType.SHOW_PROGRESS_DONE:
            result = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert result["step_id"] == "aws_selection"
        assert result["errors"]["base"] == "no_aws_station"
        assert not hass.config_entries.async_entries(DOMAIN)


async def test_switch_back_to_existing_source(hass, aws_setup, mock_aws_client):
    """Existing observation/model sources can be restored without deleting the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": aws_setup.entry_id},
        data={"current_source": "legacy", "use_observations": False},
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert aws_setup.runtime_data.aws_coordinator is None
    assert TMDWeatherEntity(aws_setup).native_temperature == 30.5


async def test_reconfigure_automatic_prefills_current_ha_location(
    hass, aws_setup, mock_aws_client
):
    """HA location changes affect a new automatic search, never forecast coordinates."""
    before = (aws_setup.data["latitude"], aws_setup.data["longitude"])
    hass.config.latitude = 13.8
    hass.config.longitude = 100.5
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": aws_setup.entry_id},
        data={"current_source": "aws_station"},
    )
    assert result["data_schema"]({})["aws_latitude"] == 13.8
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aws_selection_mode": "automatic"}
    )
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    if result["type"] == FlowResultType.SHOW_PROGRESS_DONE:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert aws_setup.data["aws_selection_mode"] == "automatic"
    assert aws_setup.data["aws_latitude"] == 13.8
    assert (aws_setup.data["latitude"], aws_setup.data["longitude"]) == before


async def test_discovery_cancelled_with_flow(
    hass, mock_client, mock_aws_client, config_entry
):
    """Cancelling the progress UI also cancels the station discovery task."""
    started = asyncio.Event()
    never = asyncio.Event()
    cancelled = asyncio.Event()

    async def discover(latitude, longitude):
        started.set()
        try:
            await never.wait()
        finally:
            cancelled.set()

    with patch(
        "custom_components.tmd_weather.aws.TMDAWSDiscovery.async_nearest",
        side_effect=discover,
    ):
        result = await begin_aws(hass, mock_client, config_entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"aws_selection_mode": "automatic"}
        )
        await started.wait()
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()
        assert cancelled.is_set()
