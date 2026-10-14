"""Station parsing, selection, real unit normalization and current-source behavior."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientConnectionError
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.tmd_weather.api import (
    TMDAuthError,
    TMDConnectionError,
    TMDInvalidLocationError,
    TMDInvalidResponseError,
    TMDRateLimitError,
)
from custom_components.tmd_weather.const import OBSERVATION_URL
from custom_components.tmd_weather.diagnostics import async_get_config_entry_diagnostics
from custom_components.tmd_weather.observations import (
    TMDObservationClient,
    distance_km,
    nearest_station,
    parse_observations,
)
from custom_components.tmd_weather.weather import TMDWeatherEntity

NOW = datetime.fromisoformat("2026-10-11T10:30:00+07:00")


def test_live_fixture():
    """Parse an actual sanitized TMD capture without changing its timestamps."""
    payload = json.loads(
        (Path(__file__).parent / "fixtures/observation_live.json").read_text()
    )
    stations = parse_observations(payload)
    assert len(stations) == 3
    assert stations[0].time.isoformat() == "2026-10-12T07:00:00+07:00"
    assert stations[0].values.temperature == 28.2
    assert stations[0].values.pressure == 1013.46
    assert stations[0].values.dew_point == 25.8
    assert stations[0].values.visibility == 10


def test_units_and_missing_values(observation_json):
    """Normalize km/h into the common m/s weather unit; retain unknown fields."""
    data = observation_json["Stations"]["Station"][0]["Observation"]
    data["WindSpeed"] = "36"
    data["MeanSeaLevelPressure"] = "-999"
    data["DewPoint"] = {}
    data["RelativeHumidity"] = "NaN"
    data["LandVisibility"] = ""
    values = parse_observations(observation_json)[0].values
    assert values.wind_speed == 10
    assert values.pressure is None
    assert values.dew_point is None
    assert values.visibility is None
    assert values.humidity is None
    assert values.condition_code is None


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"Stations": None}, {"Stations": {"Station": None}}]
)
def test_invalid_envelope(payload):
    """Malformed station responses cannot masquerade as valid weather."""
    with pytest.raises(TMDInvalidResponseError):
        parse_observations(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("Latitude", "bad"),
        ("Longitude", "999"),
        ("WmoStationNumber", ""),
        ("Observation", None),
        ("Observation", {}),
    ],
)
def test_invalid_station(observation_json, field, value):
    """Exclude stations with no coordinate, identity, observation or temperature."""
    records = observation_json["Stations"]["Station"]
    records[0][field] = value
    assert len(parse_observations(observation_json)) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("DateTime", "bad"),
        ("DateTime", None),
        ("AirTemperature", "-9999"),
        ("AirTemperature", "N/A"),
        ("AirTemperature", True),
    ],
)
def test_invalid_observation(observation_json, field, value):
    """Exclude missing/sentinel measurements and ambiguous or invalid times."""
    observation_json["Stations"]["Station"][0]["Observation"][field] = value
    assert len(parse_observations(observation_json)) == 2


def test_single_station_shape(observation_json):
    """Support the verified XML serializer's possible single-object shape."""
    observation_json["Stations"]["Station"] = observation_json["Stations"]["Station"][0]
    assert len(parse_observations(observation_json)) == 1


def test_nearest_and_tie(observation_json):
    """Only usable fresh stations qualify; equal-distance ties use WMO ID."""
    stations = parse_observations(observation_json)
    assert nearest_station(stations, 13.75, 100.5, NOW).station_id == "48455"
    tie = replace(stations[0], station_id="00000")
    assert nearest_station((stations[0], tie), 13.75, 100.5, NOW).station_id == "00000"
    stale = replace(stations[0], time=NOW - timedelta(hours=7))
    assert (
        nearest_station((stale, *stations[1:]), 13.75, 100.5, NOW).station_id == "48454"
    )
    assert 6 < distance_km(13.75, 100.5, 13.72639, 100.56) < 8


@pytest.mark.parametrize("change", [timedelta(hours=-7), timedelta(hours=1)])
def test_stale_or_future(observation_json, change):
    """Never choose future or excessively old data."""
    stations = tuple(
        replace(s, time=NOW + change) for s in parse_observations(observation_json)
    )
    with pytest.raises(TMDInvalidLocationError):
        nearest_station(stations, 13.75, 100.5, NOW)


def test_far_or_empty(observation_json):
    """A remote station must not represent the requested location."""
    with pytest.raises(TMDInvalidLocationError):
        nearest_station(parse_observations(observation_json), 50, 0, NOW)
    with pytest.raises(TMDInvalidLocationError):
        nearest_station((), 13.75, 100.5, NOW)


async def test_http(hass, aioclient_mock, observation_json):
    """Use legacy credentials only on the fixed HTTPS endpoint, once per refresh."""
    aioclient_mock.get(OBSERVATION_URL, json=observation_json)
    client = TMDObservationClient(
        async_get_clientsession(hass),
        "test-uid",
        "test-observation-secret",
        13.75,
        100.5,
    )
    result = await client.async_get_current_conditions()
    assert result.station_id == "48455"
    assert aioclient_mock.call_count == 1
    assert aioclient_mock.mock_calls[0][1].query["format"] == "json"
    assert "test-observation-secret" not in repr(client)
    assert "test-uid" not in repr(client)


@pytest.mark.parametrize(
    "status,error", [(401, TMDAuthError), (429, TMDRateLimitError)]
)
async def test_http_failure(hass, aioclient_mock, status, error):
    """HTTP failures contain no legacy credentials or provider body."""
    aioclient_mock.get(OBSERVATION_URL, status=status, text="test-observation-secret")
    client = TMDObservationClient(
        async_get_clientsession(hass),
        "test-uid",
        "test-observation-secret",
        13.75,
        100.5,
    )
    with pytest.raises(error) as caught:
        await client.async_get_current_conditions()
    assert "test-observation-secret" not in str(caught.value)
    if status == 429:
        with pytest.raises(TMDRateLimitError):
            await client.async_get_current_conditions()
        assert aioclient_mock.call_count == 1


async def test_no_url_leak(hass, aioclient_mock):
    """Suppress aiohttp errors containing a sensitive query URL."""
    aioclient_mock.get(
        OBSERVATION_URL, exc=ClientConnectionError("ukey=test-observation-secret")
    )
    with pytest.raises(TMDConnectionError) as caught:
        await TMDObservationClient(
            async_get_clientsession(hass),
            "test-uid",
            "test-observation-secret",
            13.75,
            100.5,
        ).async_get_current_conditions()
    assert "test-observation-secret" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "uid,key,lat", [("", "secret", 13.75), ("uid", "", 13.75), ("uid", "secret", 100)]
)
async def test_invalid_configuration(hass, uid, key, lat):
    """Reject missing credentials and invalid coordinates locally."""
    with pytest.raises((TMDAuthError, TMDInvalidLocationError)):
        TMDObservationClient(async_get_clientsession(hass), uid, key, lat, 100.5)


async def test_observed_state(hass, station_setup):
    """Actual station measurements override current model estimates explicitly."""
    entity = TMDWeatherEntity(station_setup)
    assert entity.available
    assert entity.native_temperature == 28.2
    assert entity.native_pressure == 1013.46
    assert entity.native_dew_point == 25.8
    assert entity.native_visibility == 10
    assert entity.native_wind_speed == 0
    assert entity.humidity == 87
    # Weather3Hours has no observed sky-condition code. Do not borrow the model's.
    assert entity.condition is None
    sensor = hass.states.get(hass.states.async_entity_ids("sensor")[0])
    assert sensor.state == "0.0"
    assert "Station rainfall" in sensor.attributes["friendly_name"]


async def test_forecasts_fail_independently(hass, station_setup, mock_client):
    """Hourly NWP failures leave valid observed current measurements available."""
    mock_client.async_get_hourly_forecast.side_effect = TMDConnectionError
    await station_setup.runtime_data.hourly_coordinator.async_refresh()
    await hass.async_block_till_done()
    entity = TMDWeatherEntity(station_setup)
    assert entity.available
    assert entity.native_temperature == 28.2


async def test_observation_failure_and_recovery(
    hass, station_setup, mock_observation_client
):
    """Observation failure cannot silently substitute a successful NWP estimate."""
    coordinator = station_setup.runtime_data.observation_coordinator
    before = coordinator.data
    mock_observation_client.async_get_current_conditions.side_effect = (
        TMDConnectionError
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data is before
    assert not TMDWeatherEntity(station_setup).available
    assert (
        hass.states.get(hass.states.async_entity_ids("weather")[0]).state
        == STATE_UNAVAILABLE
    )
    mock_observation_client.async_get_current_conditions.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert TMDWeatherEntity(station_setup).available


async def test_observation_stale(hass, station_setup, freezer):
    """Current properties and the rainfall sensor reject expired observations."""
    freezer.tick(timedelta(hours=7))
    entity = TMDWeatherEntity(station_setup)
    assert not entity.available
    assert entity.native_temperature is None
    sensor = hass.data["sensor"].get_entity(hass.states.async_entity_ids("sensor")[0])
    assert sensor.native_value is None
    assert not sensor.available


async def test_diagnostics(hass, station_setup):
    """Station source is visible while all three credentials stay redacted."""
    result = await async_get_config_entry_diagnostics(hass, station_setup)
    encoded = json.dumps(result)
    for value in ["test-secret", "test-uid", "test-observation-secret"]:
        assert value not in encoded
    assert result["current_source"] == "station"
    assert result["observation_station"]["id"] == "48455"
    assert result["current_model_values"] is None
    assert result["current_observation_values"]["temperature"] == 28.2


async def test_station_config_flow(
    hass, station_config_entry, mock_client, mock_observation_client
):
    """New entries cannot retain retired credentials supplied outside the UI."""
    with patch("custom_components.tmd_weather.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            "tmd_weather",
            context={"source": "user"},
            data=dict(station_config_entry.data),
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    mock_observation_client.async_get_current_conditions.assert_not_awaited()
    assert "observation_uid" not in result["data"]
    assert "observation_key" not in result["data"]


async def test_unpaired_credentials(hass, config_entry, mock_client):
    """Ignored retired fields never prevent a valid forecast configuration."""
    with patch("custom_components.tmd_weather.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            "tmd_weather",
            context={"source": "user"},
            data={**config_entry.data, "observation_uid": "uid"},
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert "observation_uid" not in result["data"]


async def test_reconfigure_preserves_entities(
    hass, setup_entry, mock_aws_client, entity_registry
):
    """Enable stations on an existing model entry without losing entity identity."""
    old = {
        entry.unique_id: entry.entity_id
        for entry in entity_registry.entities.values()
        if entry.config_entry_id == setup_entry.entry_id
    }
    result = await hass.config_entries.flow.async_init(
        "tmd_weather",
        context={"source": "reconfigure", "entry_id": setup_entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "current_source": "aws_station",
        },
    )
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
    assert result["reason"] == "reconfigure_successful"
    assert setup_entry.runtime_data.aws_coordinator is not None
    new = {
        entry.unique_id: entry.entity_id
        for entry in entity_registry.entities.values()
        if entry.config_entry_id == setup_entry.entry_id
    }
    assert old.items() <= new.items()


async def test_disable_observations(hass, station_setup):
    """Disabling the source explicitly reloads modeled data without deletion."""
    result = await hass.config_entries.flow.async_init(
        "tmd_weather",
        context={"source": "reconfigure", "entry_id": station_setup.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"current_source": "nwp_model"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert station_setup.runtime_data.observation_coordinator is None
    assert TMDWeatherEntity(station_setup).native_temperature == 30.5
    assert "observation_uid" not in station_setup.data
    assert "observation_key" not in station_setup.data


async def test_station_reauth_keeps_token(hass, station_setup, mock_observation_client):
    """NWP reauthentication preserves a saved legacy source and its station keys."""
    result = await station_setup.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"access_token": "replacement-nwp-secret"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert station_setup.data["access_token"] == "replacement-nwp-secret"
    assert station_setup.data["observation_key"] == "test-observation-secret"


async def test_observation_first_refresh_failure(
    hass, station_config_entry, mock_client, mock_observation_client
):
    """Configured current observations must pass first refresh before setup succeeds."""
    mock_observation_client.async_get_current_conditions.side_effect = (
        TMDConnectionError
    )
    station_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(station_config_entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.states.async_entity_ids("weather")


async def test_observation_auth_does_not_request_retired_credentials(
    hass, station_setup, mock_observation_client
):
    """Legacy access failure cannot trigger a misleading NWP-token dialog."""
    mock_observation_client.async_get_current_conditions.side_effect = TMDAuthError
    await station_setup.runtime_data.observation_coordinator.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler("tmd_weather")
    assert not flows
    assert not station_setup.runtime_data.observation_coordinator.last_update_success


@pytest.mark.parametrize(
    "exception,error",
    [
        (TMDAuthError, "invalid_auth"),
        (TMDConnectionError, "cannot_connect"),
    ],
)
async def test_failed_reconfigure_keeps_entry(
    hass, setup_entry, mock_client, exception, error
):
    """An invalid source change keeps the working config and runtime intact."""
    original = dict(setup_entry.data)
    mock_client.async_get_hourly_forecast.side_effect = exception
    result = await hass.config_entries.flow.async_init(
        "tmd_weather",
        context={"source": "reconfigure", "entry_id": setup_entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"current_source": "nwp_model"}
    )
    assert result["errors"] == {"base": error}
    assert dict(setup_entry.data) == original
    assert setup_entry.runtime_data.observation_coordinator is None
