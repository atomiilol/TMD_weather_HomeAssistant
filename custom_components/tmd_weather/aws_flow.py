"""AWS selection shared by initial setup and reconfiguration."""

import asyncio
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import probatio

from .api import TMDAPIError, TMDInvalidLocationError
from .aws import async_aws_discovery
from .const import (
    AWS_SOURCE,
    CONF_AWS_LATITUDE,
    CONF_AWS_LONGITUDE,
    CONF_AWS_SELECTION_MODE,
    CONF_AWS_STATION_ID,
    CONF_AWS_USE_HA_LOCATION,
    CONF_CURRENT_SOURCE,
)
from .stations import (
    TMDStationDatabaseError,
    get_regions,
    get_station_by_id,
    get_stations_by_region,
)


class AWSStationSelectionFlow(ConfigFlow):
    """Preserve forecast coordinates and commit changes only after validation."""

    def __init__(self) -> None:
        super().__init__()
        self._aws_pending: dict[str, Any] = {}
        self._aws_reconfigure = False
        self._aws_region = ""
        self._aws_task: asyncio.Task[int] | None = None
        self._aws_error: str | None = None
        self._aws_checked = 0
        self._aws_total = 0

    async def async_step_aws_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Default to HA location; allow separate observation search coordinates."""
        errors: dict[str, str] = {}
        use_ha_default = self._aws_pending.get(CONF_AWS_USE_HA_LOCATION, True)
        try:
            await async_aws_discovery(self.hass)
        except TMDStationDatabaseError:
            errors["base"] = "invalid_station_database"
        if user_input is not None and not errors:
            if user_input.get(CONF_AWS_SELECTION_MODE) not in ("automatic", "manual"):
                errors["base"] = "invalid_aws_selection"
            else:
                use_ha = user_input.get(CONF_AWS_USE_HA_LOCATION, True)
                self._aws_pending.update(
                    {
                        CONF_CURRENT_SOURCE: AWS_SOURCE,
                        CONF_AWS_SELECTION_MODE: user_input[CONF_AWS_SELECTION_MODE],
                        CONF_AWS_USE_HA_LOCATION: use_ha,
                        CONF_AWS_LATITUDE: self.hass.config.latitude
                        if use_ha
                        else user_input.get(
                            CONF_AWS_LATITUDE, self.hass.config.latitude
                        ),
                        CONF_AWS_LONGITUDE: self.hass.config.longitude
                        if use_ha
                        else user_input.get(
                            CONF_AWS_LONGITUDE, self.hass.config.longitude
                        ),
                    }
                )
                if user_input[CONF_AWS_SELECTION_MODE] == "manual":
                    return await self.async_step_aws_region()
                self._aws_task = None
                return await self.async_step_aws_discover()
        return self.async_show_form(
            step_id="aws_selection",
            errors=errors,
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_AWS_SELECTION_MODE,
                        default=self._aws_pending.get(
                            CONF_AWS_SELECTION_MODE, "automatic"
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=["automatic", "manual"],
                            translation_key="aws_selection_mode",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    probatio.Required(
                        CONF_AWS_USE_HA_LOCATION,
                        default=self._aws_pending.get(CONF_AWS_USE_HA_LOCATION, True),
                    ): bool,
                    probatio.Optional(
                        CONF_AWS_LATITUDE,
                        default=self.hass.config.latitude
                        if use_ha_default
                        else self._aws_pending.get(
                            CONF_AWS_LATITUDE, self.hass.config.latitude
                        ),
                    ): cv.latitude,
                    probatio.Optional(
                        CONF_AWS_LONGITUDE,
                        default=self.hass.config.longitude
                        if use_ha_default
                        else self._aws_pending.get(
                            CONF_AWS_LONGITUDE, self.hass.config.longitude
                        ),
                    ): cv.longitude,
                }
            ),
        )

    async def async_step_aws_region(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List only supplied catalog regions, without network requests."""
        errors = {}
        if user_input is not None:
            if user_input.get("region") not in get_regions():
                errors["base"] = "invalid_aws_selection"
            else:
                self._aws_region = user_input["region"]
                return await self.async_step_aws_station()
        return self.async_show_form(
            step_id="aws_region",
            errors=errors,
            data_schema=probatio.Schema(
                {
                    probatio.Required("region"): SelectSelector(
                        SelectSelectorConfig(
                            options=list(get_regions()),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_aws_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Friendly searchable choices store the normalized catalog value."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                if isinstance(user_input.get(CONF_AWS_STATION_ID), bool):
                    raise ValueError
                aws_id = int(user_input[CONF_AWS_STATION_ID])
                record = get_station_by_id(aws_id)
                if not record or record.region != self._aws_region:
                    raise ValueError
            except KeyError, TypeError, ValueError:
                errors["base"] = "invalid_aws_selection"
            else:
                try:
                    service = await async_aws_discovery(self.hass)
                    await service.client.async_get_station(aws_id)
                except (TMDAPIError, TimeoutError) as error:
                    from .config_flow import api_error_key

                    errors["base"] = api_error_key(error)
                else:
                    self._aws_pending[CONF_AWS_STATION_ID] = aws_id
                    return self._finish_aws()
        return self.async_show_form(
            step_id="aws_station",
            errors=errors,
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_AWS_STATION_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=str(station.aws_id),
                                    label=f"{station.number} — {station.name}",
                                )
                                for station in get_stations_by_region(self._aws_region)
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_aws_discover(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use HA's cancellable progress task to check nearest candidates."""
        if self._aws_task is not None and self._aws_task.done():
            try:
                self._aws_pending[CONF_AWS_STATION_ID] = self._aws_task.result()
                self._aws_error = None
            except TMDInvalidLocationError:
                self._aws_error = "no_aws_station"
            except (TMDAPIError, TimeoutError) as error:
                from .config_flow import api_error_key

                self._aws_error = api_error_key(error)
            return self.async_show_progress_done(next_step_id="aws_confirm")
        if self._aws_task is None:
            service = await async_aws_discovery(self.hass)
            self._aws_checked = 0
            self._aws_total = len(service.client.catalog)

            def discovery_progress(checked: int, total: int) -> None:
                self._aws_checked, self._aws_total = checked, total
                if self._aws_task is not None and self._aws_task.cancelling():
                    return
                self.async_update_progress(checked / total if total else 0)
                self.async_notify_flow_changed()

            self._aws_task = self.hass.async_create_task(
                service.async_nearest(
                    self._aws_pending[CONF_AWS_LATITUDE],
                    self._aws_pending[CONF_AWS_LONGITUDE],
                    discovery_progress,
                )
            )
        return self.async_show_progress(
            step_id="aws_discover",
            progress_action="aws_discovering",
            progress_task=self._aws_task,
            description_placeholders={
                "checked": str(self._aws_checked),
                "total": str(self._aws_total),
            },
        )

    async def async_step_aws_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Review the automatic choice or offer manual selection after failure."""
        if self._aws_error:
            error = self._aws_error
            self._aws_error = None
            result = await self.async_step_aws_selection()
            result["errors"] = {"base": error}
            return result
        record = get_station_by_id(self._aws_pending[CONF_AWS_STATION_ID])
        assert record is not None
        service = await async_aws_discovery(self.hass)
        choices = {
            station.aws_id: f"{station.number} — {station.name} ({distance:.1f} km)"
            for distance, station in service.nearby_stations(
                self._aws_pending[CONF_AWS_LATITUDE],
                self._aws_pending[CONF_AWS_LONGITUDE],
            )
        }
        choices.setdefault(record.aws_id, f"{record.number} — {record.name}")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                selected = int(user_input.get(CONF_AWS_STATION_ID, record.aws_id))
                if selected not in choices:
                    raise ValueError
                if selected != record.aws_id:
                    await service.client.async_get_station(selected)
                    self._aws_pending[CONF_AWS_STATION_ID] = selected
                    self._aws_pending[CONF_AWS_SELECTION_MODE] = "manual"
            except TypeError, ValueError:
                errors["base"] = "invalid_aws_selection"
            except (TMDAPIError, TimeoutError) as error:
                from .config_flow import api_error_key

                errors["base"] = api_error_key(error)
            else:
                return self._finish_aws()
        return self.async_show_form(
            step_id="aws_confirm",
            errors=errors,
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_AWS_STATION_ID, default=str(record.aws_id)
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=str(aws_id), label=label)
                                for aws_id, label in choices.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            description_placeholders={
                "station_name": record.name,
                "station_number": record.number,
                "station_region": record.region,
            },
        )

    def _finish_aws(self) -> ConfigFlowResult:
        """Save the AWSID while retaining the user's forecast location."""
        if self._aws_reconfigure:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data=self._aws_pending
            )
        latitude, longitude = (
            self._aws_pending[CONF_LATITUDE],
            self._aws_pending[CONF_LONGITUDE],
        )
        from .api import location_id

        title = (
            self.hass.config.location_name
            if location_id(latitude, longitude)
            == location_id(self.hass.config.latitude, self.hass.config.longitude)
            else location_id(latitude, longitude)
        )
        return self.async_create_entry(title=title, data=self._aws_pending)
