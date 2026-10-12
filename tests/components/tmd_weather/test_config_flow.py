"""Exercise the real config flow, validation errors and credential replacement."""

from dataclasses import replace
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
import probatio
import pytest

from custom_components.tmd_weather.api import (
    TMDAPIError,
    TMDAuthError,
    TMDConnectionError,
    TMDInvalidLocationError,
    TMDInvalidResponseError,
    TMDRateLimitError,
    TMDServiceError,
)
from custom_components.tmd_weather.const import DOMAIN


async def test_user_defaults_and_success(hass, mock_client):
    """Default to HA coordinates, validate both APIs, and preserve Thai names."""
    hass.config.latitude = 13.75
    hass.config.longitude = 100.5
    hass.config.location_name = "บ้าน"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    data = result["data_schema"]({"access_token": "test-secret"})
    assert data == {
        "access_token": "test-secret",
        "latitude": 13.75,
        "longitude": 100.5,
    }
    with patch("custom_components.tmd_weather.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "บ้าน"
    assert result["result"].unique_id == "13.7500,100.5000"
    mock_client.async_get_hourly_forecast.assert_awaited_once_with(duration=1)
    mock_client.async_get_daily_forecast.assert_awaited_once_with(duration=1)


async def test_custom_location_title(hass, mock_client, config_entry):
    """Use a stable coordinate title when configuring a different location."""
    hass.config.latitude = 50
    with patch("custom_components.tmd_weather.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=dict(config_entry.data)
        )
        await hass.async_block_till_done()
    assert result["title"] == "13.7500,100.5000"


async def test_duplicate(hass, mock_client, config_entry):
    """Duplicate coordinates abort before spending API quota."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=dict(config_entry.data)
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_client.async_get_hourly_forecast.assert_not_awaited()


@pytest.mark.parametrize(
    "exception,error",
    [
        (TMDAuthError, "invalid_auth"),
        (TMDConnectionError, "cannot_connect"),
        (TimeoutError, "cannot_connect"),
        (TMDRateLimitError, "rate_limited"),
        (TMDInvalidLocationError, "invalid_location"),
        (TMDInvalidResponseError, "invalid_response"),
        (TMDServiceError, "service_unavailable"),
        (TMDAPIError, "unknown"),
    ],
)
async def test_flow_errors(hass, mock_client, config_entry, exception, error):
    """Return translated errors and never exception strings."""
    mock_client.async_get_hourly_forecast.side_effect = exception("test-secret")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=dict(config_entry.data)
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert "test-secret" not in str(result["errors"])
    mock_client.async_get_daily_forecast.assert_not_awaited()


async def test_daily_error(hass, mock_client, config_entry):
    """Do not configure an advertised forecast type without validating it."""
    mock_client.async_get_daily_forecast.side_effect = TMDAuthError
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=dict(config_entry.data)
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_missing_current_hour(hass, mock_client, config_entry):
    """Future hourly data must not be presented as current weather."""
    data = mock_client.async_get_hourly_forecast.return_value
    mock_client.async_get_hourly_forecast.return_value = replace(
        data, points=data.points[1:]
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=dict(config_entry.data)
    )
    assert result["errors"] == {"base": "invalid_response"}


async def test_coordinate_validation(hass):
    """Use HA's range validators in the UI schema."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with pytest.raises(probatio.Invalid):
        result["data_schema"](
            {"access_token": "test-secret", "latitude": 91, "longitude": 100}
        )
    with pytest.raises(probatio.Invalid):
        result["data_schema"](
            {"access_token": "test-secret", "latitude": 13, "longitude": -181}
        )


@pytest.mark.parametrize(
    "error",
    [TMDAuthError, TMDConnectionError, TMDRateLimitError, TMDInvalidResponseError],
)
async def test_reauth_failed_then_success(hass, mock_client, config_entry, error):
    """A failed replacement preserves the token; success reloads the same entry."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    mock_client.async_get_hourly_forecast.side_effect = error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"access_token": "replacement-secret"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]
    assert config_entry.data["access_token"] == "test-secret"
    mock_client.async_get_hourly_forecast.side_effect = None
    with patch(
        "custom_components.tmd_weather.async_setup_entry", return_value=True
    ) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"access_token": "replacement-secret"}
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["access_token"] == "replacement-secret"
    assert config_entry.unique_id == "13.7500,100.5000"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    setup.assert_awaited_once()
