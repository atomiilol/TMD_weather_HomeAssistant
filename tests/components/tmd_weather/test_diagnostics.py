"""Diagnostics must never disclose tokens, precise locations or raw responses."""

import json

from custom_components.tmd_weather.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics(hass, setup_entry):
    """Expose source identity and health while redacting all location credentials."""
    result = await async_get_config_entry_diagnostics(hass, setup_entry)
    serialized = json.dumps(result)
    assert "test-secret" not in serialized
    assert "13.75" not in serialized
    assert "100.5" not in serialized
    assert result["current_source"] == "nwp_model"
    assert result["observation_station"] is None
    assert result["hourly"]["last_update_success"]
    assert result["hourly"]["forecast_count"] == 3
    assert result["daily"]["forecast_count"] == 2
    assert result["current_model_values"]["temperature"] == 30.5
    assert result["current_valid_time"].endswith("+07:00")
