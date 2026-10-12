"""Exercise minute-interval options, persistence, reload and scheduled fetching."""

from datetime import timedelta

from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.tmd_weather.const import CONF_FETCH_INTERVAL


@pytest.mark.parametrize("source,default", [("model", 60), ("station", 30)])
async def test_options_defaults(
    hass, config_entry, station_config_entry, source, default
):
    """Existing locations expose the current source's default without changing it."""
    entry = station_config_entry if source == "station" else config_entry
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["data_schema"]({})[CONF_FETCH_INTERVAL] == default
    assert entry.options == {}


@pytest.mark.parametrize(
    "value", [0, -1, 1441, 1.5, True, float("nan"), float("inf"), "5", None]
)
async def test_invalid_interval(hass, config_entry, value):
    """Invalid direct submissions cannot be stored or start an API refresh."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(
        config_entry.entry_id, data={CONF_FETCH_INTERVAL: value}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_FETCH_INTERVAL: "invalid_fetch_interval"}
    assert config_entry.options == {}


@pytest.mark.parametrize("value", [1, 17.0, 1440])
async def test_interval_bounds(hass, config_entry, value):
    """Accept the supported minute range and store whole minutes."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(
        config_entry.entry_id, data={CONF_FETCH_INTERVAL: value}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_FETCH_INTERVAL] == int(value)
    assert isinstance(config_entry.options[CONF_FETCH_INTERVAL], int)
    reopened = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert reopened["data_schema"]({})[CONF_FETCH_INTERVAL] == value


async def test_options_reload_and_polling(
    hass, station_setup, entity_registry, mock_client, mock_observation_client, freezer
):
    """One setting updates all cache schedules while retaining entries and entities."""
    entry = station_setup
    original_data = dict(entry.data)
    original_ids = {
        row.unique_id: row.entity_id
        for row in entity_registry.entities.values()
        if row.config_entry_id == entry.entry_id
    }
    original_runtime = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_FETCH_INTERVAL: 17}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert dict(entry.data) == original_data
    assert entry.runtime_data is not original_runtime
    for coordinator in (
        entry.runtime_data.hourly_coordinator,
        entry.runtime_data.daily_coordinator,
        entry.runtime_data.observation_coordinator,
    ):
        assert coordinator.update_interval == timedelta(minutes=17)
    assert not original_runtime.hourly_coordinator._listeners
    assert not original_runtime.daily_coordinator._listeners
    assert not original_runtime.observation_coordinator._listeners
    assert original_ids == {
        row.unique_id: row.entity_id
        for row in entity_registry.entities.values()
        if row.config_entry_id == entry.entry_id
    }
    assert mock_client.async_get_hourly_forecast.await_count == 2
    assert mock_client.async_get_daily_forecast.await_count == 2
    assert mock_observation_client.async_get_current_conditions.await_count == 2
    # HA polls an hourly forecast cache while a dashboard has a subscription.
    weather_id = hass.states.async_entity_ids("weather")[0]
    weather = hass.data["weather"].get_entity(weather_id)

    @callback
    def receive_forecast(points):
        """Consume the subscribed forecast without requesting additional data."""

    unsubscribe = weather.async_subscribe_forecast("hourly", receive_forecast)
    assert entry.runtime_data.hourly_coordinator._unsub_refresh is not None
    now = dt_util.utcnow()
    async_fire_time_changed(hass, now + timedelta(minutes=16))
    await hass.async_block_till_done()
    assert mock_client.async_get_hourly_forecast.await_count == 2
    async_fire_time_changed(hass, now + timedelta(minutes=18))
    freezer.tick(timedelta(minutes=18))
    await hass.async_block_till_done()
    assert mock_client.async_get_hourly_forecast.await_count == 3
    assert mock_client.async_get_daily_forecast.await_count == 3
    assert mock_observation_client.async_get_current_conditions.await_count == 3
    unsubscribe()
    # Reload applies the persisted value after restart, without re-entering data.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.hourly_coordinator.update_interval == timedelta(
        minutes=17
    )


@pytest.mark.parametrize("source", ["model", "station"])
async def test_long_interval_expires_and_advances_cache(
    hass,
    current_entry,
    source,
    entity_registry,
    mock_client,
    mock_observation_client,
    freezer,
):
    """Long fetch intervals must not leave stale current values displayed."""
    entry = current_entry
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_FETCH_INTERVAL: 1440}
    )
    await hass.async_block_till_done()
    temperature_id = entity_registry.async_get_entity_id(
        "sensor", "tmd_weather", f"{entry.unique_id}_temperature"
    )
    if source == "model":
        freezer.tick(timedelta(hours=1))
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1))
        await hass.async_block_till_done()
        assert float(hass.states.get(temperature_id).state) == 31
    freezer.tick(timedelta(hours=7))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1))
    await hass.async_block_till_done()
    assert hass.states.get(temperature_id).state == "unavailable"
    assert (
        hass.states.get(hass.states.async_entity_ids("weather")[0]).state
        == "unavailable"
    )
    assert mock_client.async_get_hourly_forecast.await_count == 2
    assert mock_client.async_get_daily_forecast.await_count == 2
    if source == "station":
        assert mock_observation_client.async_get_current_conditions.await_count == 2


async def test_source_switch_preserves_entities(
    hass, setup_entry, entity_registry, mock_client
):
    """Validate a new source before saving, retaining configuration and entity IDs."""
    from custom_components.tmd_weather.const import CONF_FORECAST_SOURCE

    data = dict(setup_entry.data)
    identities = {
        row.unique_id: row.entity_id for row in entity_registry.entities.values()
    }
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["data_schema"]({})[CONF_FORECAST_SOURCE] == "coordinate"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_FETCH_INTERVAL: 17,
            CONF_FORECAST_SOURCE: "domain_2",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert setup_entry.options[CONF_FORECAST_SOURCE] == "domain_2"
    assert dict(setup_entry.data) == data
    assert identities == {
        row.unique_id: row.entity_id for row in entity_registry.entities.values()
    }
    mock_client.async_get_hourly_forecast.assert_any_await(duration=1)
    assert setup_entry.runtime_data.hourly_coordinator.update_interval == timedelta(
        minutes=17
    )
    reopened = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert reopened["data_schema"]({})[CONF_FORECAST_SOURCE] == "domain_2"


@pytest.mark.parametrize(
    "error,key",
    [
        ("auth", "invalid_auth"),
        ("connection", "cannot_connect"),
        ("timeout", "request_timed_out"),
        ("location", "invalid_location"),
        ("response", "invalid_response"),
        ("limit", "rate_limited"),
        ("service", "service_unavailable"),
    ],
)
async def test_source_switch_failure(hass, setup_entry, mock_client, error, key):
    """A bad source/auth/coverage request must not replace a working configuration."""
    from custom_components.tmd_weather.api import (
        TMDAuthError,
        TMDConnectionError,
        TMDInvalidLocationError,
        TMDInvalidResponseError,
        TMDRateLimitError,
        TMDServiceError,
        TMDTimeoutError,
    )
    from custom_components.tmd_weather.const import CONF_FORECAST_SOURCE

    errors = {
        "auth": TMDAuthError,
        "connection": TMDConnectionError,
        "timeout": TMDTimeoutError,
        "location": TMDInvalidLocationError,
        "response": TMDInvalidResponseError,
        "limit": TMDRateLimitError,
        "service": TMDServiceError,
    }
    previous = dict(setup_entry.options)
    runtime = setup_entry.runtime_data
    mock_client.async_get_hourly_forecast.side_effect = errors[error]("test-secret")
    result = await hass.config_entries.options.async_init(
        setup_entry.entry_id,
        data={
            CONF_FETCH_INTERVAL: 60,
            CONF_FORECAST_SOURCE: "domain_2",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": key}
    assert dict(setup_entry.options) == previous
    assert setup_entry.runtime_data is runtime
    assert "test-secret" not in str(result["errors"])


async def test_source_switch_stale_sample(hass, setup_entry, mock_client):
    """Future data alone cannot validate modeled current conditions for a switch."""
    from dataclasses import replace

    from custom_components.tmd_weather.const import CONF_FORECAST_SOURCE

    data = mock_client.async_get_hourly_forecast.return_value
    mock_client.async_get_hourly_forecast.return_value = replace(
        data, points=data.points[1:]
    )
    result = await hass.config_entries.options.async_init(
        setup_entry.entry_id,
        data={
            CONF_FETCH_INTERVAL: 60,
            CONF_FORECAST_SOURCE: "domain_2",
        },
    )
    assert result["errors"] == {"base": "invalid_response"}
    assert setup_entry.options == {}


async def test_invalid_source_selection(hass, config_entry, mock_client):
    """Only the verified provider choices can be stored."""
    from custom_components.tmd_weather.const import CONF_FORECAST_SOURCE

    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(
        config_entry.entry_id,
        data={
            CONF_FETCH_INTERVAL: 60,
            CONF_FORECAST_SOURCE: "v2",
        },
    )
    assert result["errors"] == {CONF_FORECAST_SOURCE: "invalid_forecast_source"}
    mock_client.async_get_hourly_forecast.assert_not_awaited()
