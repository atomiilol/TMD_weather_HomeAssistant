"""Validate installable metadata and translation completeness."""

import json
from pathlib import Path

from homeassistant import loader
from homeassistant.helpers.translation import async_get_translations
import pytest

COMPONENT = Path(__file__).parents[3] / "custom_components/tmd_weather"


def _leaf_keys(value, prefix=""):
    """Return translation paths independently of their language."""
    result = set()
    for key, item in value.items():
        if isinstance(item, dict):
            result.update(_leaf_keys(item, f"{prefix}{key}."))
        else:
            result.add(f"{prefix}{key}")
    return result


def test_translation_completeness():
    """Both languages implement every source string without unresolved references."""
    strings = json.loads((COMPONENT / "strings.json").read_text())
    en = json.loads((COMPONENT / "translations/en.json").read_text())
    th = json.loads((COMPONENT / "translations/th.json").read_text())
    assert en == strings
    assert _leaf_keys(th) == _leaf_keys(strings)
    assert "[%key:" not in json.dumps(strings)
    assert "[%key:" not in json.dumps(th)
    assert th["entity"]["sensor"]["model_hour_rain"]["name"]


@pytest.mark.parametrize("language", ["en", "th"])
async def test_options_translations_loaded_by_home_assistant(hass, language):
    """Exercise HA's translation loader for the options dialog and selector."""
    source = json.loads((COMPONENT / f"translations/{language}.json").read_text())
    options = await async_get_translations(hass, language, "options", {"tmd_weather"})
    selectors = await async_get_translations(
        hass, language, "selector", {"tmd_weather"}
    )
    prefix = "component.tmd_weather."
    assert (
        options[prefix + "options.step.init.data.forecast_source"]
        == source["options"]["step"]["init"]["data"]["forecast_source"]
    )
    for error in ("cannot_connect", "request_timed_out"):
        assert (
            options[prefix + f"options.error.{error}"]
            == source["options"]["error"][error]
        )
    assert (
        selectors[prefix + "selector.forecast_source.options.domain_2"]
        == source["selector"]["forecast_source"]["options"]["domain_2"]
    )


async def test_manifest(hass):
    """Have HA load the metadata needed for UI discovery."""
    integration = await loader.async_get_integration(hass, "tmd_weather")
    assert integration.domain == "tmd_weather"
    assert integration.config_flow
    assert integration.manifest["integration_type"] == "service"
    assert integration.manifest["iot_class"] == "cloud_polling"
    assert integration.manifest["version"] == "0.5.0b2"
    assert integration.manifest["requirements"] == []
