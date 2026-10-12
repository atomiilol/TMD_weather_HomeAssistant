"""Test real weather platform state, native units and forecast service calls."""

from dataclasses import replace
from datetime import timedelta

from homeassistant.core import callback
import pytest

from custom_components.tmd_weather.weather import TMDWeatherEntity


async def test_native_state(hass, setup_entry):
    """Expose only legitimate source values in native TMD units."""
    entity = TMDWeatherEntity(setup_entry)
    assert entity.condition == "rainy"
    assert entity.native_temperature == 30.5
    assert entity.humidity == 75
    assert entity.native_pressure == 1008.4
    assert entity.native_wind_speed == 3.5
    assert entity.wind_bearing == 180
    assert entity.native_temperature_unit == "°C"
    assert entity.native_pressure_unit == "hPa"
    assert entity.native_wind_speed_unit == "m/s"
    assert entity.native_precipitation_unit == "mm"
    assert entity.cloud_coverage is None
    assert entity.native_visibility is None
    assert entity.native_dew_point is None
    assert entity.uv_index is None
    assert entity.native_apparent_temperature is None
    state = hass.states.get(hass.states.async_entity_ids("weather")[0])
    assert state.state == "rainy"
    assert state.attributes["temperature"] == 30.5
    assert state.attributes["attribution"].endswith("(TMD)")
    assert "test-secret" not in str(state)


@pytest.mark.parametrize("kind", ["hourly", "daily"])
async def test_forecast_service(hass, setup_entry, kind):
    """Use the public HA weather.get_forecasts service and unit conversion."""
    entity_id = hass.states.async_entity_ids("weather")[0]
    response = await hass.services.async_call(
        "weather",
        "get_forecasts",
        {"entity_id": entity_id, "type": kind},
        blocking=True,
        return_response=True,
    )
    points = response[entity_id]["forecast"]
    assert points
    point = points[0]
    assert point["datetime"].endswith("+07:00")
    assert point["temperature"] == (30.5 if kind == "hourly" else 34.5)
    assert point["precipitation"] == (0.2 if kind == "hourly" else 12.5)
    assert point["condition"] == ("rainy" if kind == "hourly" else "lightning-rainy")
    if kind == "daily":
        assert point["templow"] == 25
    assert "precipitation_probability" not in point
    assert "uv_index" not in point
    assert "cloud_coverage" not in point


@pytest.mark.parametrize(
    "code,condition",
    [
        (1, "sunny"),
        (2, "partlycloudy"),
        (3, "cloudy"),
        (4, "cloudy"),
        (5, "rainy"),
        (6, "rainy"),
        (7, "pouring"),
        (8, "lightning-rainy"),
        (9, None),
        (10, None),
        (11, None),
        (12, None),
        (999, None),
        (None, None),
    ],
)
async def test_condition_mapping(hass, setup_entry, code, condition):
    """Temperature-only and unknown condition codes do not fabricate sky state."""
    coordinator = setup_entry.runtime_data.hourly_coordinator
    data = coordinator.data
    point = data.points[0]
    point = replace(point, values=replace(point.values, condition_code=code))
    coordinator.async_set_updated_data(replace(data, points=(point, *data.points[1:])))
    assert TMDWeatherEntity(setup_entry).condition == condition


async def test_missing_fields(hass, setup_entry):
    """Absent optional measurements stay unknown without breaking the entity."""
    coordinator = setup_entry.runtime_data.hourly_coordinator
    data = coordinator.data
    point = data.points[0]
    point = replace(
        point,
        values=replace(
            point.values,
            humidity=None,
            pressure=None,
            wind_speed=None,
            wind_bearing=None,
            precipitation=None,
        ),
    )
    coordinator.async_set_updated_data(replace(data, points=(point, *data.points[1:])))
    entity = TMDWeatherEntity(setup_entry)
    assert entity.available
    assert entity.humidity is None
    assert entity.native_pressure is None
    assert entity.native_wind_speed is None
    assert entity.wind_bearing is None
    point = (await entity.async_forecast_hourly())[0]
    assert point["native_precipitation"] is None


async def test_stale_current_and_forecast_filtering(hass, setup_entry, freezer):
    """Select the active model hour and never retain an expired current value."""
    entity = TMDWeatherEntity(setup_entry)
    freezer.tick(timedelta(hours=1))
    assert entity.native_temperature == 31
    assert len(entity._async_forecast_hourly()) == 2
    freezer.tick(timedelta(hours=2))
    assert entity.native_temperature is None
    assert not entity.available
    assert entity._async_forecast_hourly() == []


async def test_rain_sensor(hass, setup_entry):
    """Expose one forecast-period rainfall amount, without cumulative statistics."""
    state = hass.states.get(hass.states.async_entity_ids("sensor")[0])
    assert state.state == "0.2"
    assert state.attributes["unit_of_measurement"] == "mm"
    assert state.attributes["device_class"] == "precipitation"
    assert "state_class" not in state.attributes


async def test_device_identity(hass, setup_entry, device_registry, entity_registry):
    """Weather and sensors share one service device with stable location identity."""
    device = device_registry.async_get_device_by_identifier(
        ("tmd_weather", setup_entry.unique_id), setup_entry.entry_id
    )
    assert device is not None
    assert device.name == "บ้าน"
    assert device.entry_type == "service"
    assert "modeled" in device.model
    entities = [
        entry
        for entry in entity_registry.entities.values()
        if entry.config_entry_id == setup_entry.entry_id
    ]
    assert len(entities) == 10
    assert all(entry.device_id == device.id for entry in entities)


async def test_forecast_listener(hass, setup_entry):
    """Use CoordinatorWeatherEntity's current subscription/listener mechanism."""
    entity_id = hass.states.async_entity_ids("weather")[0]
    entity = hass.data["weather"].get_entity(entity_id)
    coordinator = setup_entry.runtime_data.daily_coordinator
    original_listeners = len(coordinator._listeners)
    updates = []

    @callback
    def receive(points):
        updates.append(points)

    unsub = entity.async_subscribe_forecast("daily", receive)
    data = coordinator.data
    point = data.points[0]
    revised = replace(point, values=replace(point.values, temperature=35))
    coordinator.async_set_updated_data(
        replace(data, points=(revised, *data.points[1:]))
    )
    await hass.async_block_till_done()
    assert updates
    assert updates[-1][0]["temperature"] == 35
    unsub()
    assert len(coordinator._listeners) == original_listeners
