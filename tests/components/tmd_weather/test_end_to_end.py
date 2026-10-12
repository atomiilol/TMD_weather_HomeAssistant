"""Exercise UI, the real client, parsing, coordinators and public HA services."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.tmd_weather.const import (
    DAILY_ENDPOINT,
    DOMAIN,
    HOURLY_ENDPOINT,
    NWP_BASE_URL,
)


async def test_http_to_weather(hass, aioclient_mock, hourly_json, daily_json):
    """Create a real integration from HTTP fixtures and request both forecasts."""
    aioclient_mock.get(NWP_BASE_URL + HOURLY_ENDPOINT, json=hourly_json)
    aioclient_mock.get(NWP_BASE_URL + DAILY_ENDPOINT, json=daily_json)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={"access_token": "test-secret", "latitude": 13.75, "longitude": 100.5},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.state == ConfigEntryState.LOADED
    assert aioclient_mock.call_count == 4
    session = async_get_clientsession(hass)
    assert entry.runtime_data.client.session is session
    entity_id = hass.states.async_entity_ids("weather")[0]
    assert hass.states.get(entity_id).state == "rainy"
    for kind in ("hourly", "daily"):
        response = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": entity_id, "type": kind},
            blocking=True,
            return_response=True,
        )
        assert response[entity_id]["forecast"][0]["precipitation"] > 0
    # Reading current attributes and fresh forecasts makes no extra requests.
    assert aioclient_mock.call_count == 4
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not session.closed


async def test_domain2_http_switch(
    hass, aioclient_mock, hourly_json, daily_json, entity_registry
):
    """Switch an existing entry through the UI using the real request/parser path."""
    from copy import deepcopy

    from custom_components.tmd_weather.const import (
        CONF_FETCH_INTERVAL,
        CONF_FORECAST_SOURCE,
        DOMAIN2_ENDPOINT,
    )

    aioclient_mock.get(NWP_BASE_URL + HOURLY_ENDPOINT, json=hourly_json)
    aioclient_mock.get(NWP_BASE_URL + DAILY_ENDPOINT, json=daily_json)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={"access_token": "test-secret", "latitude": 13.75, "longitude": 100.5},
    )
    await hass.async_block_till_done()
    entry = result["result"]
    identities = {
        row.unique_id: row.entity_id for row in entity_registry.entities.values()
    }
    points = deepcopy(hourly_json["WeatherForcasts"])
    points[0]["forecasts"][0]["data"]["tc"] = 29.5
    for point in points[0]["forecasts"]:
        point["time"] = point["time"].removesuffix("+07:00")
    aioclient_mock.get(
        NWP_BASE_URL + DOMAIN2_ENDPOINT, json={"weather_forecast": points}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_FETCH_INTERVAL: 17,
            CONF_FORECAST_SOURCE: "domain_2",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_FORECAST_SOURCE] == "domain_2"
    assert entry.runtime_data.client.forecast_source == "domain_2"
    assert identities == {
        row.unique_id: row.entity_id for row in entity_registry.entities.values()
    }
    weather = hass.states.get(hass.states.async_entity_ids("weather")[0])
    assert weather.attributes["temperature"] == 29.5
    sensor_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}_temperature"
    )
    assert float(hass.states.get(sensor_id).state) == 29.5
    # One validation request + hourly/daily setup refresh, shared by all entities.
    assert aioclient_mock.call_count == 7
    assert [call[1].path for call in aioclient_mock.mock_calls[4:]] == [
        "/nwpapi/v1" + DOMAIN2_ENDPOINT,
        "/nwpapi/v1" + DOMAIN2_ENDPOINT,
        "/nwpapi/v1" + DAILY_ENDPOINT,
    ]
    assert await hass.config_entries.async_unload(entry.entry_id)
