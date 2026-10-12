"""Test cache retention, independent availability and runtime reauth."""

from datetime import timedelta

from homeassistant.const import STATE_UNAVAILABLE
import pytest

from custom_components.tmd_weather.api import (
    TMDAuthError,
    TMDConnectionError,
    TMDInvalidResponseError,
    TMDRateLimitError,
)
from custom_components.tmd_weather.const import DOMAIN


@pytest.mark.parametrize(
    "error", [TMDConnectionError, TMDInvalidResponseError, TMDRateLimitError]
)
async def test_hourly_failure_and_recovery(hass, setup_entry, mock_client, error):
    """Preserve good cache, mark unavailable, and recover on the next success."""
    coordinator = setup_entry.runtime_data.hourly_coordinator
    before = coordinator.data
    entity_id = hass.states.async_entity_ids("weather")[0]
    mock_client.async_get_hourly_forecast.side_effect = error
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data is before
    assert not coordinator.last_update_success
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    mock_client.async_get_hourly_forecast.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    assert hass.states.get(entity_id).state == "rainy"


async def test_daily_failure_independent(hass, setup_entry, mock_client):
    """Daily errors leave current weather and hourly data available."""
    coordinator = setup_entry.runtime_data.daily_coordinator
    before = coordinator.data
    mock_client.async_get_daily_forecast.side_effect = TMDConnectionError
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert coordinator.data is before
    entity_id = hass.states.async_entity_ids("weather")[0]
    assert hass.states.get(entity_id).state == "rainy"
    assert setup_entry.runtime_data.hourly_coordinator.last_update_success


@pytest.mark.parametrize("dataset", ["hourly", "daily"])
async def test_runtime_auth_failure(hass, setup_entry, mock_client, dataset):
    """An HTTP authentication error during a later refresh starts reauth."""
    getattr(mock_client, f"async_get_{dataset}_forecast").side_effect = TMDAuthError
    await getattr(setup_entry.runtime_data, f"{dataset}_coordinator").async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["step_id"] == "reauth_confirm"


async def test_errors_do_not_leak_tokens(hass, setup_entry, mock_client, caplog):
    """Even accidentally sensitive client exception text stays out of HA logs."""
    mock_client.async_get_hourly_forecast.side_effect = TMDConnectionError(
        "test-secret"
    )
    await setup_entry.runtime_data.hourly_coordinator.async_refresh()
    assert "test-secret" not in caplog.text


async def test_intervals(setup_entry):
    """Polling cadence reflects the documented integration choices."""
    assert (
        setup_entry.runtime_data.hourly_coordinator.update_interval.total_seconds()
        == 3600
    )
    assert (
        setup_entry.runtime_data.daily_coordinator.update_interval.total_seconds()
        == 10800
    )


async def test_unchanged_forecast_advances_current(hass, setup_entry, freezer):
    """An identical response must still update which model hour is current."""
    entity_id = hass.states.async_entity_ids("weather")[0]
    assert hass.states.get(entity_id).attributes["temperature"] == 30.5
    freezer.tick(timedelta(hours=1))
    await setup_entry.runtime_data.hourly_coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["temperature"] == 31
