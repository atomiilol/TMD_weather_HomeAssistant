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
