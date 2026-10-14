"""Common fixtures; all external I/O is mocked."""

from collections.abc import Generator
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tmd_weather.api import parse_forecast
from custom_components.tmd_weather.const import DOMAIN


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the integration from the repository."""


@pytest.fixture(autouse=True)
def fixed_time(freezer):
    """Keep the supplied model timestep current."""
    freezer.move_to("2026-10-11T03:30:00+00:00")


@pytest.fixture
def hourly_json():
    """Load synthetic hourly contract data."""
    return json.loads((Path(__file__).parent / "fixtures/hourly.json").read_text())


@pytest.fixture
def daily_json():
    """Load synthetic daily contract data."""
    return json.loads((Path(__file__).parent / "fixtures/daily.json").read_text())


@pytest.fixture
def observation_json():
    """Load station data with synthetic times matching the frozen test clock."""
    return json.loads((Path(__file__).parent / "fixtures/observation.json").read_text())


@pytest.fixture
def station_config_entry(config_entry):
    """Keep the same location identity when actual observations are enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=config_entry.title,
        unique_id=config_entry.unique_id,
        data={
            **config_entry.data,
            "observation_uid": "test-uid",
            "observation_key": "test-observation-secret",
        },
    )


@pytest.fixture
def mock_observation_client(observation_json):
    """Mock only observation I/O while exercising station coordinator behavior."""
    from datetime import datetime

    from custom_components.tmd_weather.observations import (
        nearest_station,
        parse_observations,
    )

    with (
        patch(
            "custom_components.tmd_weather.TMDObservationClient", autospec=True
        ) as factory,
        patch(
            "custom_components.tmd_weather.config_flow.TMDObservationClient",
            new=factory,
        ),
    ):
        client = factory.return_value
        client.async_get_current_conditions.return_value = nearest_station(
            parse_observations(observation_json),
            13.75,
            100.5,
            datetime.fromisoformat("2026-10-11T10:30:00+07:00"),
        )
        yield client


@pytest.fixture
async def station_setup(
    hass, station_config_entry, mock_client, mock_observation_client
):
    """Set up current measurements and independent NWP forecasts."""
    station_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(station_config_entry.entry_id)
    await hass.async_block_till_done()
    return station_config_entry


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Create a location entry without adding it to Home Assistant."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="บ้าน",
        unique_id="13.7500,100.5000",
        data={"access_token": "test-secret", "latitude": 13.75, "longitude": 100.5},
    )


@pytest.fixture
def mock_client(hourly_json, daily_json) -> Generator[AsyncMock]:
    """Patch API construction while exercising the real HA integration."""
    with (
        patch("custom_components.tmd_weather.TMDClient", autospec=True) as factory,
        patch("custom_components.tmd_weather.config_flow.TMDClient", new=factory),
    ):
        client = factory.return_value
        client.async_get_hourly_forecast.return_value = parse_forecast(
            hourly_json, "hourly", 13.75, 100.5
        )
        client.async_get_daily_forecast.return_value = parse_forecast(
            daily_json, "daily", 13.75, 100.5
        )
        yield client


@pytest.fixture
async def setup_entry(hass, config_entry, mock_client):
    """Set up all real platforms against the mocked API client."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


@pytest.fixture
def current_entry(request, source):
    """Resolve the selected async setup fixture before entering the test loop."""
    return request.getfixturevalue(
        "station_setup" if source == "station" else "setup_entry"
    )


@pytest.fixture
def aws_payload():
    return {
        "result": 1,
        "data": {
            "site": {
                "AWSID": 2,
                "SNAME": "Tak Weather Observing Station",
                "FNAME": "สถานีตาก",
                "LAT": 13.75,
                "LON": 100.5,
                "ALT": 124,
                "ONLINE": "y",
                "CNCT_YN": "y",
                "WV_SENSOR": "y",
            },
            "items": [
                {
                    "yyyy": 2026,
                    "mm": "10",
                    "dd": "11",
                    "hh": "02",
                    "temp": 28,
                    "humidity": 80,
                },
                {
                    "yyyy": 2026,
                    "mm": "10",
                    "dd": "11",
                    "hh": "03",
                    "temp": 30.12,
                    "humidity": 85,
                    "pressure": 998,
                    "windspeed": 2,
                    "winddir": 240,
                    "winddirsign": "WSW",
                    "precipitation": 0,
                    "visibility": 1500,
                },
                {
                    "yyyy": 2026,
                    "mm": "10",
                    "dd": "11",
                    "hh": "04",
                    "temp": None,
                    "humidity": None,
                },
            ],
        },
    }


@pytest.fixture
async def nearby_aws_catalog(hass):
    """Three synthetic nearby coordinates; retain supplied station IDs/names."""
    from dataclasses import replace

    from custom_components.tmd_weather.stations import async_load_stations

    records = tuple(
        replace(record, latitude=13.75 + record.aws_id * 0.01, longitude=100.5)
        for record in (await async_load_stations(hass))[:3]
    )
    with patch(
        "custom_components.tmd_weather.aws.async_load_stations", return_value=records
    ):
        yield records


@pytest.fixture
def mock_aws_client(aws_payload):
    """Mock station HTTP, retaining real catalog, parsing and discovery."""
    from copy import deepcopy

    from homeassistant.util import dt as dt_util

    from custom_components.tmd_weather.aws import parse_aws_response

    async def fetch(aws_id):
        payload = deepcopy(aws_payload)
        payload["data"]["site"]["AWSID"] = aws_id
        if aws_id != 2:
            payload["data"]["site"]["LAT"] = 18.75
        return parse_aws_response(payload, aws_id, dt_util.utcnow())

    with patch(
        "custom_components.tmd_weather.aws.TMDAWSClient.async_get_station",
        side_effect=fetch,
    ) as request:
        yield request


@pytest.fixture
def aws_config_entry(config_entry):
    return MockConfigEntry(
        domain=DOMAIN,
        title=config_entry.title,
        unique_id=config_entry.unique_id,
        data={
            **config_entry.data,
            "current_source": "aws_station",
            "aws_station_id": 2,
            "aws_selection_mode": "manual",
            "aws_latitude": 13.75,
            "aws_longitude": 100.5,
        },
    )


@pytest.fixture
async def aws_setup(hass, aws_config_entry, mock_client, mock_aws_client):
    aws_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(aws_config_entry.entry_id)
    await hass.async_block_till_done()
    return aws_config_entry
