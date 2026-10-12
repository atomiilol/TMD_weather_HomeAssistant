"""Exercise separate measurements, source consistency and daily predictions."""

from dataclasses import replace

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.tmd_weather.const import DOMAIN


def sensor_id(entity_registry, entry, key):
    """Look up stable identity without depending on entity creation order."""
    return entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}_{key}"
    )


@pytest.mark.parametrize("source", ["model", "station"])
@pytest.mark.parametrize(
    "key,modeled,observed,unit,device_class,state_class",
    [
        ("model_hour_rain", "0.2", "0.0", "mm", "precipitation", None),
        ("temperature", "30.5", "28.2", "°C", "temperature", "measurement"),
        ("humidity", "75", "87", "%", "humidity", "measurement"),
        ("pressure", "1008.4", "1013.46", "hPa", "atmospheric_pressure", "measurement"),
        ("wind_speed", "12.6", "0.0", "km/h", "wind_speed", "measurement"),
        ("wind_bearing", "180", "0.0", "°", "wind_direction", "measurement_angle"),
        ("dew_point", STATE_UNKNOWN, "25.8", "°C", "temperature", "measurement"),
        ("visibility", STATE_UNKNOWN, "10.0", "km", "distance", "measurement"),
    ],
)
async def test_current_measurements(
    hass,
    current_entry,
    entity_registry,
    source,
    key,
    modeled,
    observed,
    unit,
    device_class,
    state_class,
):
    """Expose source values, native units and appropriate statistics in real HA."""
    entry = current_entry
    entity_id = sensor_id(entity_registry, entry, key)
    expected = observed if source == "station" else modeled
    if expected == STATE_UNKNOWN:
        assert entity_id is None
        return
    state = hass.states.get(entity_id)
    assert float(state.state) == float(expected)
    assert state.attributes["unit_of_measurement"] == unit
    assert state.attributes["device_class"] == device_class
    assert state.attributes.get("state_class") == state_class
    assert state.attributes["data_source"] == (
        "station" if source == "station" else "nwp_model"
    )
    assert "test-secret" not in str(state)
    if key == "wind_speed":
        entity = hass.data["sensor"].get_entity(entity_id)
        assert entity.native_unit_of_measurement == "m/s"
        assert entity.native_value == (0 if source == "station" else 3.5)


@pytest.mark.parametrize(
    "key,value,unit",
    [
        ("forecast_temperature_high", "34.5", "°C"),
        ("forecast_temperature_low", "25", "°C"),
        ("forecast_rainfall", "12.5", "mm"),
    ],
)
async def test_daily_predictions(hass, setup_entry, entity_registry, key, value, unit):
    """Label today's forecast separately and exclude predictions from statistics."""
    state = hass.states.get(sensor_id(entity_registry, setup_entry, key))
    assert float(state.state) == float(value)
    assert state.attributes["unit_of_measurement"] == unit
    assert state.attributes["forecast_date"] == "2026-10-11"
    assert state.attributes["data_source"] == "nwp_forecast"
    assert "state_class" not in state.attributes


async def test_bangkok_midnight_rollover(
    hass, config_entry, entity_registry, mock_client, freezer
):
    """Cached predictions advance at midnight without making an API request."""
    freezer.move_to("2026-10-11T16:59:00+00:00")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    high = sensor_id(entity_registry, config_entry, "forecast_temperature_high")
    assert hass.states.get(high).state == "34.5"
    freezer.move_to("2026-10-11T17:00:01+00:00")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    state = hass.states.get(high)
    assert float(state.state) == 33
    assert state.attributes["forecast_date"] == "2026-10-12"
    mock_client.async_get_daily_forecast.assert_awaited_once_with()
    mock_client.async_get_hourly_forecast.assert_awaited_once_with()


async def test_missing_today(hass, setup_entry, entity_registry):
    """Tomorrow's prediction cannot stand in for a missing forecast for today."""
    coordinator = setup_entry.runtime_data.daily_coordinator
    coordinator.async_set_updated_data(
        replace(coordinator.data, points=(coordinator.data.points[1],))
    )
    await hass.async_block_till_done()
    high = sensor_id(entity_registry, setup_entry, "forecast_temperature_high")
    assert hass.states.get(high).state == STATE_UNAVAILABLE
    assert hass.data["sensor"].get_entity(high).native_value is None


async def test_daily_failure_is_independent(hass, station_setup, entity_registry):
    """Forecast errors do not invalidate current station measurement sensors."""
    station_setup.runtime_data.daily_coordinator.async_set_update_error(
        UpdateFailed("Temporary failure")
    )
    await hass.async_block_till_done()
    assert (
        hass.states.get(
            sensor_id(entity_registry, station_setup, "forecast_rainfall")
        ).state
        == STATE_UNAVAILABLE
    )
    assert (
        hass.states.get(sensor_id(entity_registry, station_setup, "temperature")).state
        == "28.2"
    )


async def test_station_failure_is_independent(hass, station_setup, entity_registry):
    """No modeled substitution on failure; daily predictions continue working."""
    station_setup.runtime_data.observation_coordinator.async_set_update_error(
        UpdateFailed("Temporary failure")
    )
    await hass.async_block_till_done()
    for key in ("temperature", "humidity", "pressure", "wind_speed", "model_hour_rain"):
        state = hass.states.get(sensor_id(entity_registry, station_setup, key))
        assert state.state == STATE_UNAVAILABLE
        entity = hass.data["sensor"].get_entity(state.entity_id)
        assert entity.extra_state_attributes["data_source"] == "station"
    assert (
        hass.states.get(
            sensor_id(entity_registry, station_setup, "forecast_temperature_high")
        ).state
        == "34.5"
    )


@pytest.mark.parametrize("source", ["model", "station"])
async def test_expired_source(hass, current_entry, entity_registry, source, freezer):
    """Every current measurement rejects expired data from either source."""
    entry = current_entry
    freezer.move_to("2026-10-11T11:00:00+00:00")
    for key in (
        "temperature",
        "humidity",
        "dew_point",
        "visibility",
        "model_hour_rain",
    ):
        entity_id = sensor_id(entity_registry, entry, key)
        if entity_id is None:
            continue
        entity = hass.data["sensor"].get_entity(entity_id)
        assert not entity.available
        assert entity.native_value is None


async def test_shared_update(hass, setup_entry, entity_registry, mock_client):
    """Cached changes reach sensors and weather without duplicate requests."""
    hourly = setup_entry.runtime_data.hourly_coordinator
    data = hourly.data
    point = data.points[0]
    hourly.async_set_updated_data(
        replace(
            data,
            points=(
                replace(point, values=replace(point.values, temperature=29)),
                *data.points[1:],
            ),
        )
    )
    await hass.async_block_till_done()
    assert (
        hass.states.get(sensor_id(entity_registry, setup_entry, "temperature")).state
        == "29"
    )
    weather = hass.states.get(hass.states.async_entity_ids("weather")[0])
    assert weather.attributes["temperature"] == 29
    mock_client.async_get_hourly_forecast.assert_awaited_once_with()
    mock_client.async_get_daily_forecast.assert_awaited_once_with()


async def test_missing_optional_measurement(hass, setup_entry, entity_registry):
    """A temporary gap is unavailable, retaining a supported reading for recovery."""
    hourly = setup_entry.runtime_data.hourly_coordinator
    data = hourly.data
    point = data.points[0]
    hourly.async_set_updated_data(
        replace(
            data,
            points=(replace(point, values=replace(point.values, humidity=None)),),
        )
    )
    await hass.async_block_till_done()
    assert (
        hass.states.get(sensor_id(entity_registry, setup_entry, "humidity")).state
        == STATE_UNAVAILABLE
    )
    assert (
        hass.states.get(sensor_id(entity_registry, setup_entry, "temperature")).state
        == "30.5"
    )


async def test_existing_entities_survive_upgrade(
    hass, config_entry, mock_client, entity_registry
):
    """Add new sensors while retaining customized IDs and a disabled old sensor."""
    config_entry.add_to_hass(hass)
    weather = entity_registry.async_get_or_create(
        "weather",
        DOMAIN,
        config_entry.unique_id,
        config_entry=config_entry,
        suggested_object_id="my_tmd_weather",
    )
    rainfall = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{config_entry.unique_id}_model_hour_rain",
        config_entry=config_entry,
        suggested_object_id="my_tmd_rainfall",
        disabled_by=RegistryEntryDisabler.USER,
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(weather.entity_id) is not None
    assert (
        sensor_id(entity_registry, config_entry, "model_hour_rain")
        == rainfall.entity_id
    )
    assert (
        entity_registry.async_get(rainfall.entity_id).disabled_by
        is RegistryEntryDisabler.USER
    )
    assert hass.states.get(rainfall.entity_id) is None
    assert hass.states.get(sensor_id(entity_registry, config_entry, "temperature"))
    assert len(hass.states.async_entity_ids("sensor")) == 8


async def test_cleanup_legacy_unsupported_sensors(
    hass, config_entry, mock_client, entity_registry
):
    """Remove the old modeled placeholders while retaining weather and forecasts."""
    config_entry.add_to_hass(hass)
    old_ids = []
    for key in ("dew_point", "visibility"):
        old_ids.append(
            entity_registry.async_get_or_create(
                "sensor",
                DOMAIN,
                f"{config_entry.unique_id}_{key}",
                config_entry=config_entry,
            ).entity_id
        )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in old_ids:
        assert entity_registry.async_get(entity_id) is None
        assert hass.states.get(entity_id) is None
    assert hass.states.async_entity_ids("weather")
    assert hass.states.get(
        sensor_id(entity_registry, config_entry, "forecast_rainfall")
    )


async def test_discover_later_measurement(
    hass, config_entry, mock_client, entity_registry
):
    """Skip absent initial fields, add them once supplied, and retain IDs on gaps."""
    original = mock_client.async_get_hourly_forecast.return_value
    mock_client.async_get_hourly_forecast.return_value = replace(
        original,
        points=tuple(
            replace(point, values=replace(point.values, pressure=None))
            for point in original.points
        ),
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert sensor_id(entity_registry, config_entry, "pressure") is None
    coordinator = config_entry.runtime_data.hourly_coordinator
    coordinator.async_set_updated_data(original)
    await hass.async_block_till_done()
    entity_id = sensor_id(entity_registry, config_entry, "pressure")
    assert hass.states.get(entity_id).state == "1008.4"
    coordinator.async_set_updated_data(
        replace(
            original,
            points=tuple(
                replace(point, values=replace(point.values, pressure=None))
                for point in original.points
            ),
        )
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert sensor_id(entity_registry, config_entry, "pressure") == entity_id
    coordinator.async_set_updated_data(original)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "1008.4"
    assert sensor_id(entity_registry, config_entry, "pressure") == entity_id


async def test_discover_later_forecast_field(
    hass, config_entry, mock_client, entity_registry
):
    """Forecast fields without supplied values are also deferred until available."""
    original = mock_client.async_get_daily_forecast.return_value
    mock_client.async_get_daily_forecast.return_value = replace(
        original,
        points=tuple(
            replace(point, values=replace(point.values, temperature_low=None))
            for point in original.points
        ),
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert sensor_id(entity_registry, config_entry, "forecast_temperature_low") is None
    coordinator = config_entry.runtime_data.daily_coordinator
    coordinator.async_set_updated_data(original)
    await hass.async_block_till_done()
    entity_id = sensor_id(entity_registry, config_entry, "forecast_temperature_low")
    assert float(hass.states.get(entity_id).state) == 25
