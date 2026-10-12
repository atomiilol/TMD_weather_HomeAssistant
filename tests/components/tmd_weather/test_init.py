"""Test setup, first refresh, lifecycle and automatic authentication repairs."""

from homeassistant.config_entries import ConfigEntryState
import pytest

from custom_components.tmd_weather.api import TMDAuthError, TMDConnectionError
from custom_components.tmd_weather.const import DOMAIN


async def test_setup_and_unload(hass, setup_entry, mock_client):
    """Set up both datasets once, create both platforms and unload cleanly."""
    assert setup_entry.state == ConfigEntryState.LOADED
    assert len(hass.states.async_entity_ids("weather")) == 1
    assert len(hass.states.async_entity_ids("sensor")) == 11
    mock_client.async_get_hourly_forecast.assert_awaited_once_with()
    mock_client.async_get_daily_forecast.assert_awaited_once_with()
    hourly = setup_entry.runtime_data.hourly_coordinator
    daily = setup_entry.runtime_data.daily_coordinator
    assert hourly.last_update_success_time is not None
    assert daily.last_update_success_time is not None
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state == ConfigEntryState.NOT_LOADED
    assert not list(hourly.async_contexts())
    assert not list(daily.async_contexts())
    assert not hourly._listeners
    assert not daily._listeners


@pytest.mark.parametrize("dataset", ["hourly", "daily"])
async def test_failed_first_refresh(hass, config_entry, mock_client, dataset):
    """A transient first-refresh failure retries setup and creates no entities."""
    getattr(
        mock_client, f"async_get_{dataset}_forecast"
    ).side_effect = TMDConnectionError
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state == ConfigEntryState.SETUP_RETRY
    assert not hass.states.async_entity_ids("weather")
    if dataset == "hourly":
        mock_client.async_get_daily_forecast.assert_not_awaited()


@pytest.mark.parametrize("dataset", ["hourly", "daily"])
async def test_auth_failure_starts_reauth(hass, config_entry, mock_client, dataset):
    """Authentication failure is distinct from ordinary update failures."""
    getattr(mock_client, f"async_get_{dataset}_forecast").side_effect = TMDAuthError
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state == ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["step_id"] == "reauth_confirm"
