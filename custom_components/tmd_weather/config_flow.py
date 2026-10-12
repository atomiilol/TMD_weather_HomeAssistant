"""UI-only configuration and reauthentication for a TMD NWP location."""

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
from homeassistant.util import dt as dt_util
import probatio

from .api import (
    TMDAPIError,
    TMDAuthError,
    TMDClient,
    TMDConnectionError,
    TMDInvalidLocationError,
    TMDInvalidResponseError,
    TMDRateLimitError,
    TMDServiceError,
    current_point,
    location_id,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_FETCH_INTERVAL,
    CONF_OBSERVATION_KEY,
    CONF_OBSERVATION_UID,
    DOMAIN,
    HOURLY_UPDATE_INTERVAL,
    MAX_FETCH_INTERVAL,
    MIN_FETCH_INTERVAL,
    OBSERVATION_UPDATE_INTERVAL,
)
from .observations import TMDObservationClient


class TMDWeatherConfigFlow(ConfigFlow, domain=DOMAIN):
    """One stable entry per requested model coordinate, regardless of token."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Allow polling changes without replacing a location or credentials."""
        return TMDWeatherOptionsFlow()

    async def _validate(self, data: Mapping[str, Any]) -> str | None:
        """Verify a current hourly sample and a daily sample with bounded calls."""
        uid, key = data.get(CONF_OBSERVATION_UID), data.get(CONF_OBSERVATION_KEY)
        if bool(uid) != bool(key):
            return "observation_credentials_required"
        try:
            client = TMDClient(
                async_get_clientsession(self.hass),
                data[CONF_ACCESS_TOKEN],
                data[CONF_LATITUDE],
                data[CONF_LONGITUDE],
            )
            hourly = await client.async_get_hourly_forecast(duration=1)
            if not uid and current_point(hourly, dt_util.utcnow()) is None:
                raise TMDInvalidResponseError("No usable current model interval")
            await client.async_get_daily_forecast(duration=1)
            if uid and key:
                await TMDObservationClient(
                    async_get_clientsession(self.hass),
                    uid,
                    key,
                    data[CONF_LATITUDE],
                    data[CONF_LONGITUDE],
                ).async_get_current_conditions()
        except TMDAuthError:
            return "invalid_auth"
        except TMDConnectionError, TimeoutError:
            return "cannot_connect"
        except TMDRateLimitError:
            return "rate_limited"
        except TMDInvalidLocationError:
            return "invalid_location"
        except TMDInvalidResponseError:
            return "invalid_response"
        except TMDServiceError:
            return "service_unavailable"
        except TMDAPIError:
            return "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use HA coordinates by default and disclose the modeled source."""
        errors: dict[str, str] = {}
        if user_input is not None:
            latitude = user_input[CONF_LATITUDE]
            longitude = user_input[CONF_LONGITUDE]
            await self.async_set_unique_id(location_id(latitude, longitude))
            self._abort_if_unique_id_configured()
            if error := await self._validate(user_input):
                errors["base"] = error
            else:
                title = (
                    self.hass.config.location_name
                    if location_id(latitude, longitude)
                    == location_id(
                        self.hass.config.latitude, self.hass.config.longitude
                    )
                    else location_id(latitude, longitude)
                )
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="user",
            description_placeholders={
                "token_url": "https://data.tmd.go.th/nwpapi/login"
            },
            data_schema=probatio.Schema(
                {
                    probatio.Required(probatio.Secret(CONF_ACCESS_TOKEN)): str,
                    probatio.Optional(probatio.Secret(CONF_OBSERVATION_UID)): str,
                    probatio.Optional(probatio.Secret(CONF_OBSERVATION_KEY)): str,
                    probatio.Required(
                        CONF_LATITUDE, default=self.hass.config.latitude
                    ): cv.latitude,
                    probatio.Required(
                        CONF_LONGITUDE, default=self.hass.config.longitude
                    ): cv.longitude,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start credential replacement for the existing location."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the new credential and reload without creating an entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            # Replacing legacy credentials must not require re-entering a valid
            # NWP token. Blank optional fields retain their existing binding.
            updates = {key: value for key, value in user_input.items() if value}
            data = {**entry.data, **updates}
            if error := await self._validate(data):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=updates,
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {
                    probatio.Optional(probatio.Secret(CONF_ACCESS_TOKEN)): str,
                    probatio.Optional(probatio.Secret(CONF_OBSERVATION_UID)): str,
                    probatio.Optional(probatio.Secret(CONF_OBSERVATION_KEY)): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add/replace observation credentials while preserving coordinates and IDs."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            updates = {
                key: value
                for key, value in user_input.items()
                if key != "use_observations"
            }
            use_observations = user_input.get(
                "use_observations", bool(entry.data.get(CONF_OBSERVATION_UID))
            )
            if not use_observations:
                updates.update({CONF_OBSERVATION_UID: "", CONF_OBSERVATION_KEY: ""})
            data = {**entry.data, **updates}
            if use_observations and not (
                data.get(CONF_OBSERVATION_UID) and data.get(CONF_OBSERVATION_KEY)
            ):
                errors["base"] = "observation_credentials_required"
            elif error := await self._validate(data):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates=updates)
        return self.async_show_form(
            step_id="reconfigure",
            errors=errors,
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        "use_observations",
                        default=bool(entry.data.get(CONF_OBSERVATION_UID)),
                    ): bool,
                    probatio.Optional(probatio.Secret(CONF_OBSERVATION_UID)): str,
                    probatio.Optional(probatio.Secret(CONF_OBSERVATION_KEY)): str,
                }
            ),
        )


class TMDWeatherOptionsFlow(OptionsFlow):
    """Set the weather-data polling interval in whole minutes."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Save a bounded interval and let the entry listener apply it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            minutes = user_input.get(CONF_FETCH_INTERVAL)
            if (
                isinstance(minutes, bool)
                or not isinstance(minutes, (int, float))
                or not MIN_FETCH_INTERVAL <= minutes <= MAX_FETCH_INTERVAL
                or int(minutes) != minutes
            ):
                errors[CONF_FETCH_INTERVAL] = "invalid_fetch_interval"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        **self.config_entry.options,
                        CONF_FETCH_INTERVAL: int(minutes),
                    },
                )
        default = (
            OBSERVATION_UPDATE_INTERVAL
            if self.config_entry.data.get(CONF_OBSERVATION_UID)
            else HOURLY_UPDATE_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_FETCH_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_FETCH_INTERVAL, int(default.total_seconds() / 60)
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_FETCH_INTERVAL,
                            max=MAX_FETCH_INTERVAL,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                }
            ),
            errors=errors,
        )
