# Thai Meteorological Department (TMD) for Home Assistant

<img src="docs/tmd-banner.png" alt="TMD emblem" width="690">

A UI-configured custom integration using official Thai Meteorological Department
(กรมอุตุนิยมวิทยา) NWP coordinate APIs. Domain: `tmd_weather`.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=atomiilol&repository=TMD_weather_HomeAssistant&category=integration)

**Version 0.5.0:** physical AWS station observations, local nearest-station
selection, manual region/name selection and independent NWP forecasts.
See [AWS station setup and behavior](docs/aws-stations.md).

## Current weather

New installations default to **AWS physical station**. Station measurements need
no observation credentials. Choose **Modeled NWP weather** to use model estimates
instead. An NWP token is required for forecasts in either mode. AWS weather cards
use an explicitly labeled modeled condition from NWP alongside real station
measurements; current measurements never switch source during an outage.

Existing installations keep their saved source until reconfigured. Saved legacy
Weather3Hours entries remain compatible. Their retired credential fields are no
longer shown; selecting AWS or modeled weather clears those old credentials.

## Features

- One `weather` entity per coordinate: AWS temperature, humidity, station pressure,
  wind and supported visibility, or explicitly modeled NWP current measurements.
- Hourly forecasts (up to 48 hours) and daily forecasts (up to 7 days), including
  rainfall amounts and daily high/low temperatures.
- Optional **Domain 2** source for modeled current weather and hourly forecasts,
  using the model TMD currently documents as a 3 km grid.
- One rainfall sensor: **Station rainfall** or **Modeled rainfall this hour**,
  according to the configured current source; never a cumulative counter or probability.
- Separate supported measurement sensors and today's forecast high/low temperature
  and rainfall sensors, sharing weather/forecast caches. A station indicator,
  status sensor and region station selector identify and control the AWS source.
- UI setup using HA's coordinates by default, duplicate detection, reauthentication,
  English/Thai translations and redacted diagnostics.
- Bounded async requests, rate-limit backoff and recovery from temporary failures.

Requires **Home Assistant 2026.10.1 or later** and its Python 3.14 runtime.
Tested with Home Assistant 2026.10.1 / Python 3.14.8. Earlier versions are not
supported: this uses current `probatio` secret fields, typed runtime data and
timestamped weather coordinators. Future releases still need compatibility testing.

## Separate sensor entities

Each configured location has one weather entity and sensors for the measurements
TMD supplies. AWS visibility appears only when a station reports a visibility
sensor. AWS and modeled NWP do not provide dew point.
Use the sensors directly in dashboards, automations and history, as with
AccuWeather's separate measurement entities. Find them under **Settings → Devices
& services → Thai Meteorological Department (TMD)** and open your location's device.

| Sensor | Native unit | Source |
| --- | --- | --- |
| Temperature | °C | Configured current source |
| Humidity | % | Configured current source |
| Station / sea-level pressure | hPa | AWS station pressure / NWP sea-level pressure |
| Wind speed | m/s | Configured current source |
| Wind direction | ° | Configured current source |
| Visibility | km | AWS stations with a visibility sensor |
| Selected AWS station | — | Saved station number/name |
| AWS station status | — | Online/offline/stale/connection status |
| Station rainfall / Modeled rainfall this hour | mm | Configured current source |
| Forecast high temperature today | °C | Daily NWP forecast |
| Forecast low temperature today | °C | Daily NWP forecast |
| Forecast rainfall today | mm | Daily NWP forecast |

Current sensors use the same station or modeled values as the weather entity.
Their `data_source` attribute identifies `aws_station` or `nwp_model` (`station`
for compatible legacy entries); they never switch sources during outages. New optional sensors are added only after TMD supplies
values, including when they first appear in a later update. Missing readings on
previously supported sensors, expired data or a failed update make those sensors
**unavailable**, while preserving their IDs for recovery.

Forecast sensors explicitly identify `nwp_forecast` and a `forecast_date`. "Today"
uses Thailand's **Asia/Bangkok** calendar. A daily forecast failure affects these
three sensors independently of current conditions. Forecasts and rainfall amounts
do not collect cumulative or measurement statistics; current measurement sensors
use Home Assistant's measurement statistics, including circular wind-direction
statistics. Home Assistant handles supported display-unit conversions.

## Configure the fetch interval

1. Open **Settings → Devices & services → Thai Meteorological Department (TMD)**.
2. Select **Configure** for the location you want to change.
3. Set **Weather fetch interval (minutes)** to a whole number from **1 to 1440**.
4. Select **Submit**. The setting takes effect automatically; it also survives
   Home Assistant restarts. You do not need to re-enter credentials.

The interval applies to NWP modeled current weather and active hourly/daily
forecast polling. **AWS station observations retain their separate ten-minute
interval.** Sensors share those caches rather than making individual requests.
Until an interval is saved, hourly forecasts default to 60 minutes and daily
forecasts to 180 minutes; compatible legacy observations default to 30 minutes.
Hourly forecasts in station mode also refresh on demand without subscribers.

Polling more often does not make TMD publish fresh data sooner. Authentication
handling, request timeouts and the existing server rate-limit backoff continue to
apply at every interval.
Cached modeled values still advance as the model hour changes, and expired station
measurements become unavailable between fetches. These checks make no API requests.

## Select Domain 2 forecasts

Open the location's **Configure** dialog and select **Forecast source → Domain 2
(3 km grid)**, then **Submit**. The integration tests one forecast sample with your
saved NWP token before accepting the change. Authentication, coverage or response
errors leave your previous settings intact. Your coordinates, credentials,
weather entity and supported sensor IDs are preserved, including when switching
back to **Existing forecast source**.

TMD warns that area forecasts take longer to generate. Domain 2 requests allow
up to **60 seconds** for the response, while opening a connection remains limited
to **10 seconds**. A response timeout is reported separately from a failed
connection, rejected credentials or invalid forecast data. Wait for the check to
finish before retrying; failed checks keep your previous source and interval.

If the dialog shows raw labels such as `forecast_source`, `domain_2` or
`cannot_connect`, update the integration in HACS and **restart Home Assistant**,
then refresh the browser page (or close and reopen the app). Home Assistant and
the frontend cache integration translations. The dialog includes English and
Thai labels and error explanations.

Domain 2 changes **modeled current conditions and hourly forecasts**. If station
observations are configured, they remain the source of current measurements.
To use modeled Domain 2 values for current weather too, use **Reconfigure** and
select **Modeled NWP weather**.
Daily forecasts keep their existing coordinate endpoint; they are not relabeled
as Domain 2 or as 3 km data. The existing forecast source remains the default.

TMD's [current introduction](https://data.tmd.go.th/nwpapi/doc/) describes Domain 2
as a **3 km spatial grid** with hourly forecasts out to 72 hours. The integration
requests up to 48 hours. Grid spacing describes the forecast model, rather than
the accuracy of a station measurement. This option additionally selects only the
nearest returned grid point within **3 km** of your coordinates, using a small
bounding-box request. Locations without a suitable point cannot select this source.

**API version and model domain are separate.** This option uses the documented
`/nwpapi/v1/forecast/area/box` endpoint with `domain=2`, `bottom-left`, `top-right`,
`starttime`, `endtime` and the existing hourly fields. It does not use `/nwpapi/v2/`:
both tested coordinate `/v2/` routes returned HTTP 404. AWS observations use a
separate station-detail endpoint.

The [area endpoint documentation](https://data.tmd.go.th/nwpapi/doc/apidoc/forecast_area.html)
still has older resolution figures and an offset-free response example. The adapter
interprets its response on the same Asia/Bangkok clock used for the request, and returned
timestamps must match the requested hour and hourly cadence. Coordinate responses
still require explicit offsets. The registered Domain 2 endpoint returned HTTP
401 without credentials; authenticated live Domain 2 data has not been verified
in development. The options flow performs that check using your own token.

## Installation

### HACS custom repository (recommended)

Install and configure [HACS](https://www.hacs.xyz/docs/use/) first. This repository
is installed as a **custom repository**; default-store listing is not claimed.
The repository changes must be published on GitHub before HACS can download them.

1. Open **HACS** in Home Assistant.
2. Select the **three-dot menu** at the top right, then **Custom repositories**.
3. Enter `https://github.com/atomiilol/TMD_weather_HomeAssistant`.
4. Select **Integration** as the type, then **Add**.
5. Search the HACS dashboard for **Thai Meteorological Department (TMD)** and open it.
6. Select **Download** and confirm the version. The latest compatible published
   release is recommended; HACS can use the default branch before the first release.
7. Restart Home Assistant, then complete the configuration below.

The button above opens the supported My Home Assistant HACS repository link. It
helps you find/add the repository; confirm the HACS download before restarting.
These steps follow the current HACS dashboard UI, without the obsolete instruction
to open a separate Integrations tab.

### Manual installation

1. Copy the entire `custom_components/tmd_weather` directory to
   `/config/custom_components/tmd_weather/`. That directory must directly contain
   `manifest.json`, the Python modules, `brand/`, `translations/` and **`data/`**.
2. Restart Home Assistant.

### Configure after either installation method

1. Open **Settings → Devices & services → Add integration** and search for
   **Thai Meteorological Department (TMD)**.
2. Enter your NWP token and check the Home Assistant forecast coordinates.
3. Choose **AWS physical station** for measurements, or **Modeled NWP weather**.
4. For AWS, choose automatic selection using Home Assistant's location, or select
   a region and station manually. Automatic selection ranks the local coordinates
   and checks nearby stations nearest first. Review the chosen station before saving.
5. Finish setup. No observation UID/UKEY or integration YAML is needed.

HACS installs/updates files only. A manually installed copy has identical behavior
and does not require HACS at runtime.

The main entity uses the location/device name, typically `weather.home`.
Coordinates default to Home Assistant's location; other coordinates use a
coordinate-based title. Thai names work. Rename devices through HA as usual.
Changing a name does not move its source location: remove and re-add the entry
to change coordinates. Use the integration menu in **Devices & services** to remove it.

## Credentials

Forecasts require an **NWP bearer access token**, sent as
`Authorization: Bearer <token>` to fixed HTTPS NWP endpoints. Enter just the token,
without `Bearer `. AWS station measurements require no token or key.

Following TMD's [official getting-started guide](https://data.tmd.go.th/nwpapi/doc/main/getting_start.html):

1. [Register for NWP access](https://data.tmd.go.th/nwpapi/register).
2. [Log in](https://data.tmd.go.th/nwpapi/login).
3. Under **Personal Access Tokens**, select **Create New Token**.
4. Copy the token immediately and enter it in the integration's secret field.

TMD documents website-issued personal tokens, with no automatic refresh grant or
fixed token lifetime established. NWP HTTP 401/403 errors start Home Assistant
reauthentication for that token. Credentials remain in config-entry storage and
are redacted from diagnostics. They are never sent to the public AWS endpoint.

Use the entry's **Reconfigure** action to change current-weather source or station.
Your NWP token, forecast coordinates and supported entity IDs are preserved.
Saved legacy station access continues working until explicitly changed; if that
access is rejected, reconfigure to AWS or modeled weather.

## Sources, polling and time

| Dataset | Source | Implementation |
| --- | --- | --- |
| AWS current measurements | `http://122.155.135.49/api/home/site/<AWSID>` | Saved station; every 10 minutes; no credentials |
| Modeled current weather | Current NWP hourly timestep | Explicit modeled source |
| Hourly forecast | `https://data.tmd.go.th/nwpapi/v1/forecast/location/hourly/at` | Up to 48 hours; 60-minute default, configurable |
| Domain 2 hourly forecast | `https://data.tmd.go.th/nwpapi/v1/forecast/area/box` with `domain=2` | Nearest grid point within 3 km; shared hourly cache |
| Daily forecast | `https://data.tmd.go.th/nwpapi/v1/forecast/location/daily/at` | 7 days; 180-minute default, configurable |

AWS selection uses the supplied 227-station coordinate catalog, sorts candidates
within **100 km** locally, and checks their live responses nearest first. It stops
at the first online station with usable observations no older than **three hours**.
Candidates across region borders remain eligible. A healthy nearest station needs
one request even during first setup; no external geocoding API is used.

Regular polling only requests the saved station. A later station outage makes
current readings unavailable while healthy NWP forecasts remain accessible.
The integration retains the selection until you reconfigure it. It never silently
substitutes modeled measurements or moves the forecast coordinates.
See [AWS station details](docs/aws-stations.md) for units, timestamps and status.

Fresh attribute/forecast reads make no extra request. With entities enabled and a
daily subscriber, default scheduled traffic is about 24 hourly + 8 daily requests
per day, plus validation/setup, manual refreshes and failure retries. These are
conservative polling choices, independent of model publication cadence. TMD's guide
documents **60 requests/minute and 100,000 datapoints/hour**, where datapoints are
locations × duration × fields. Its later header descriptions inconsistently say
"hour" for the request limit, so deployments should honor actual server limits.
AWS mode adds approximately 144 station requests/day by default; its quota is not
verified. HA handles retries. NWP HTTP 429
honors `Retry-After` for both forecast endpoints of that entry;
an absent/invalid header defaults to one hour. The client never loops or sleeps.

Request dates/hours use `Asia/Bangkok`, independent of HA/server timezone.
Responses must contain timezone-aware timestamps. Current model data is selected
only from `time <= now < time + 1 hour`; future/expired points are never presented
as current. That interval interpretation still needs live TMD confirmation.
Diagnostics distinguish forecast **valid times** from fetch times; no model
issuance time is invented.

For compatible saved Weather3Hours entries, the legacy parser handles numeric
strings and `MM/DD/YYYY HH:MM:SS` timestamps in Bangkok time. AWS's separate
hourly clock interpretation and units are described in the station guide.

## Field mapping

| TMD | Home Assistant | Native units / behavior |
| --- | --- | --- |
| `tc` | Current/hourly `native_temperature` | °C; current is modeled |
| `tc_max` | Daily `native_temperature` | °C |
| `tc_min` | Daily `native_templow` | °C |
| `rh` | `humidity` | % |
| `slp` | `native_pressure`, including forecasts | hPa |
| `rain` | Forecast `native_precipitation`; modeled-rain sensor | mm, never probability |
| `ws10m` | `native_wind_speed` | m/s; HA handles display conversions |
| `wd10m` | `wind_bearing` | degrees |
| `cond` | `condition` | Mapping below |
| `cloudlow/med/high` | Not requested/exposed | No verified total; layers overlap |
| `psfc` | Not requested | Do not confuse surface Pa with sea-level hPa |
| `swdown` | Not requested/exposed | Shortwave radiation is not UV index |

AWS measurement fields are documented in [the station guide](docs/aws-stations.md#measurements-and-forecasts).
The following table applies only to previously saved Weather3Hours entries.

| Legacy Weather3Hours field | Current HA value | Native units |
| --- | --- | --- |
| `AirTemperature` | `native_temperature` | °C |
| `RelativeHumidity` | `humidity` | % |
| `MeanSeaLevelPressure` | `native_pressure` | hPa |
| `WindSpeed` | `native_wind_speed` | Source km/h normalized to m/s to share the weather entity's wind unit with NWP forecasts |
| `WindDirection` | `wind_bearing` | degrees |
| `LandVisibility` | `native_visibility` | km |
| `DewPoint` | `native_dew_point` | °C, supplied directly; never derived |
| `Rainfall` | Station rainfall sensor | mm; accumulation-window alignment is not independently verified |

Current rainfall uses a sensor because HA's current-weather entity has no
`native_precipitation` property; forecast dictionaries do. Missing/null/invalid
optional values are not fabricated. Sensors omit unsupported fields and mark
temporarily missing readings unavailable. NWP booleans, numeric strings and nonfinite numbers
are not treated as JSON numeric measurements; legacy numeric strings are parsed
according to the verified observation schema. Missing structure, timezone offsets,
duplicate times or an entirely unusable temperature series fail the update.

| TMD condition | HA condition |
| --- | --- |
| 1 — clear | `sunny` |
| 2 — partly cloudy | `partlycloudy` |
| 3 / 4 — cloudy / overcast | `cloudy` |
| 5 / 6 — light / moderate rain | `rainy` |
| 7 — heavy rain | `pouring` |
| 8 — thunderstorm | `lightning-rainy` |
| 9–12 — temperature descriptions | Unknown (`None`) |
| Missing/unknown | Unknown (`None`) |

These are valid current HA constants, and the TMD meanings/units are confirmed by
the official hourly/daily field tables. Clear conditions use `sunny`;
astronomical day/night refinement is not implemented. No sky condition is inferred
from temperature alone.

AWS does not provide a sky-condition code. Its weather entity is explicitly named
**Weather (modeled condition)** and uses NWP's current-hour code for the icon/label.
Attributes identify `condition_source=nwp_model` and `measurement_source=aws_station`.
Missing, expired or unsupported model codes still produce an unknown condition.
AWS temperature, wind, humidity and pressure remain observed measurements.

## Limitations and troubleshooting

- Authenticated production NWP responses, account-specific limits, geographical
  coverage, exact rainfall interval alignment and model publication schedules
  remain unverified. Official endpoint/auth/schema/field documentation is verified.
- Total cloud cover, apparent temperature, UV, precipitation probability, warnings
  and radar are not exposed without a verified source. AWS visibility requires a
  reported sensor; dew point is not supplied by AWS or modeled NWP.
- Unknown icons with valid measurements are expected when `cond` only describes
  temperature or is missing/unrecognized.
- **Cannot connect:** check Internet/DNS and HTTPS access to `data.tmd.go.th`,
  plus HTTP access to `122.155.135.49` for AWS. Station requests time out after
  10 seconds; Domain 2 requests allow up to 60 seconds.
- **Invalid authentication:** use an NWP token, not a legacy key pair. HA starts
  reauthentication automatically for authentication failures during updates.
- **Rate limited:** wait. Manual refreshes do not bypass the existing client's cooldown.
- **Invalid response:** data may be absent or the schema may differ. Download
  redacted integration diagnostics and report the issue. Never post tokens or
  raw authenticated requests.
- **Service unavailable / HTTP 404:** an endpoint may have changed; it is not
  silently treated as a bad token or replaced with fabricated data.
- Change polling through the integration's **Configure** button. Disable unwanted
  supported sensors through HA's entity controls.

## Updates through HACS

Published GitHub Releases make new versions available through normal HACS update
checks. Open the Home Assistant update notification, read the release notes and
select **Install**, or use HACS's **Pending update → Redownload** action. Restart
Home Assistant after updating. Do not remove/re-add the integration for ordinary
upgrades: HA stores config entries/entity IDs separately from the downloaded files.

Release compatibility is declared in `hacs.json`; a future data-schema change
must include a config-entry migration. See [changelog](CHANGELOG.md) and
[release procedure](docs/releases.md).

Download published versions from [GitHub Releases](https://github.com/atomiilol/TMD_weather_HomeAssistant/releases/latest).
HACS uses the latest published release tag as the available version. Future
`vX.Y.Z` tags automatically publish a release after version checks, tests, HACS
validation and Hassfest pass. A commit to `main` alone does not publish an update.
If HACS has not refreshed yet, open this repository in HACS and use **Update
information** from its menu, then install the offered version and restart Home
Assistant. Users tracking `main` can select a published version through
**Redownload** to follow release updates instead.

## Development

Use the existing checkout; cloud tasks already have an isolated workspace and
do not need a new Git worktree. With [uv](https://docs.astral.sh/uv/) installed:

```sh
uv sync --frozen --group dev
uv run --frozen pytest tests/components/tmd_weather --cov --cov-report=term-missing
uv run --frozen ruff check custom_components tests script
uv run --frozen ruff format --check custom_components tests script
uv run --frozen mypy custom_components/tmd_weather script/check_release.py
uv run --frozen python script/check_release.py --tag v0.5.0
```

`uv.lock` pins development dependencies, including HA 2026.10.1. Tests use real
HA flows/platforms/services with `pytest-homeassistant-custom-component` and mocked
HTTP; outbound sockets are disabled. No actual TMD credential is needed.
See [fixture provenance](tests/components/tmd_weather/fixtures/README.md) and
[architecture/verification notes](docs/research.md). CI runs the same checks.

```text
custom_components/tmd_weather/
├── __init__.py
├── api.py
├── observations.py
├── brand/                   # Supplied TMD emblem as local icon/logo assets
├── config_flow.py
├── const.py
├── coordinator.py
├── diagnostics.py
├── icons.json
├── manifest.json
├── sensor.py
├── strings.json
├── weather.py
└── translations/
    ├── en.json
    └── th.json
tests/components/tmd_weather/
├── conftest.py
├── fixtures/                 # Synthetic hourly/daily contract examples
├── test_api.py
├── test_config_flow.py
├── test_coordinator.py
├── test_diagnostics.py
├── test_end_to_end.py
├── test_init.py
├── test_metadata.py
├── test_observations.py
├── test_release.py
└── test_weather.py
docs/research.md
docs/hacs.md
docs/releases.md
CHANGELOG.md
hacs.json
script/check_release.py
pyproject.toml
uv.lock
.github/workflows/            # tests, HACS, hassfest, release version checks
```

## Attribution

Provider: **Thai Meteorological Department — กรมอุตุนิยมวิทยา**.
This community integration does not claim official TMD or HA Core endorsement.

- [Official website](https://www.tmd.go.th/)
- [Official API directory](https://data.tmd.go.th/api/index1.php)
- [NWP hourly documentation](https://data.tmd.go.th/nwpapi/doc/apidoc/location/forecast_hourly.html)
- [NWP daily documentation](https://data.tmd.go.th/nwpapi/doc/apidoc/location/forecast_daily.html)
- [AccuWeather architecture reference (current dev)](https://github.com/home-assistant/core/tree/dev/homeassistant/components/accuweather)

Code uses the repository's [MIT license](LICENSE). That does not establish
redistribution rights for TMD data; consult TMD's current service terms.
