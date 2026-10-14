# TMD AWS stations beta — 0.5.0b1

This feature lives on `beta/aws-stations`. It is experimental and does not change
the stable `main` branch or replace the latest stable GitHub Release. Requires
Home Assistant 2026.10.1 or later. AWS means **Automatic Weather Station** here.

## Install for testing

Back up Home Assistant before switching an existing installation to the beta.
The beta uses the same `tmd_weather` domain, so install one copy at a time.

For HACS, add this repository as a custom **Integration** repository using the
[README instructions](../README.md#installation). Opt in to beta/prerelease
versions and select **v0.5.0b1** when downloading or redownloading the integration.
The GitHub prerelease is the supported beta version; merely adding the repository
without selecting the beta continues to install the stable release.

For a manual install, download the [beta branch ZIP](https://github.com/atomiilol/TMD_weather_HomeAssistant/archive/refs/heads/beta/aws-stations.zip)
or the source ZIP from the `v0.5.0b1` prerelease. Copy the complete
`custom_components/tmd_weather` folder into Home Assistant's `custom_components`
directory, including its **data** directory. No separate catalog download is
needed. Restart Home Assistant and refresh the browser/app after installation.

## Configure current observations

For a new location, add **Thai Meteorological Department (TMD)** through
**Settings → Devices & services → Add integration**. Enter your NWP token for
forecasts. The default current source in this beta is **AWS physical station**.
Legacy observation `uid`/`ukey` credentials are not required for AWS.

For an existing location, use the entry's **Reconfigure** action and select
**AWS physical station (beta)**. Your NWP token, forecast source, forecast
coordinates and supported entity IDs are retained. Existing entries do not switch
to AWS automatically when installing the beta.

Choose one of these station-selection methods:

- **Automatic:** defaults to Home Assistant's location from **Settings → System →
  General → Location**. Uncheck **Use Home Assistant location** to specify separate
  observation search coordinates. The flow checks catalog stations, skips
  unreachable/offline stations and missing, future or older-than-three-hour
  observations, then offers the nearest usable station within **100 km**.
  Review its number/name before saving.
- **Manual:** choose a region, then search/select a station by number or name.
  The API request uses the catalog's integer `value`, not the padded display
  `number`. An offline station can be saved manually; its current readings remain
  absent until fresh observations arrive.

The station catalog is the supplied 227-record `data/tmd_aws_stations.json`,
packaged unchanged. No station-list website is scraped. Region/type/name text is
preserved, including original spellings. The live response must confirm the
selected AWSID; differences in station names do not change identity.

## Discovery and polling

The catalog has no coordinates. A first automatic scan may request each of its
227 station IDs from `http://122.155.135.49/api/home/site/<AWSID>`. It uses at most
**four concurrent requests**, a **10-second timeout per request**, and a
**10-minute overall discovery limit**. The UI displays progress and supports
cancellation. Use manual selection to avoid a complete discovery scan.

Station coordinates/status are cached in memory for **six hours**. Later automatic
selections reuse that geometry and check candidate stations' current health/data.
The cache is lost when Home Assistant restarts; the selected AWSID is persisted
in the config entry, so restarting does **not** rediscover all stations.
Failed endpoints are skipped during discovery; a station whose geometry cannot
be obtained cannot participate in the nearest-station comparison.

Normal AWS updates call only the saved station, **every 10 minutes**. The existing
configurable fetch interval continues to control NWP/legacy datasets; it does not
reduce AWS's ten-minute interval. Automatic selection runs only during setup or
reconfiguration. If the selected station goes offline later, the integration
keeps that station instead of silently switching to another source. Reconfigure
automatic mode to select a different usable station.

## Measurements and forecasts

| API field | Meaning / API units | Integration native units |
| --- | --- | --- |
| `temp` | Temperature, °C | °C |
| `humidity` | Relative humidity, % | % |
| `pressure` | **Station pressure**, hPa | hPa, labeled Station pressure |
| `windspeed` | Knots | m/s, then HA's chosen display units |
| `winddir` | Bearing, degrees | Degrees |
| `precipitation` | Rainfall, mm | mm; no invented cumulative statistics |
| `visibility` | Meters, only with `WV_SENSOR = y` | km |

Zero visibility from a station without a visibility sensor is treated as absent.
Null readings are never converted to zero. The parser uses one coherent latest
valid observation row and does not combine measurements from different hours.
Rows with malformed clocks or future timestamps are excluded.

`ONLINE = y` must accompany an actual recent observation. `CNCT_YN = y` alone
does not establish availability. Unsupported current fields, including sky
condition and dew point, are not fabricated.

During AWS outages, measurement sensors become unavailable. The weather entity's
current readings are absent and its condition is unknown; healthy hourly/daily
forecasts remain usable, including through `weather.get_forecasts`. Home Assistant
filters unavailable entities out of that service, so the weather entity stays
available while its forecast source is healthy. NWP measurements are never used
as a fallback for AWS current readings.

Hourly and daily forecasts keep the existing NWP provider, chosen source and
**user's forecast coordinates**. Station coordinates never replace those values.
AWS `forecast24HourItems`, `forecast7DayItems` and `windroseItems` are ignored.

## Diagnostics and limitations

Diagnostics include selection mode, catalog ID/number/name/type/region, live
English/Thai names, online/connected flags, public station coordinates/altitude,
visibility capability, distance and the latest measurement timestamp. User
coordinates and credentials remain redacted. API names may differ from catalog
names; the catalog is never rewritten at runtime.

The provided endpoint uses **plain HTTP**. A separate, cookie-free session sends
no NWP credentials, Home Assistant tokens or authentication cookies and follows
no redirects. HTTP does not authenticate or encrypt the observations themselves.

The beta interprets `yyyy/mm/dd/hh` as **UTC** in one isolated helper, following
the evidence supplied with the request. Field units also follow that supplied
evidence. Development live verification was blocked by the cloud network
allowlist; tests use synthetic responses based on the supplied schema. These
assumptions need confirmation against live station measurements before a stable
release. An environment draft adds the AWS IP for future live verification.

## Test checklist

1. Install the beta and verify both automatic and region/name manual selection.
2. Compare temperature, station pressure and knots against the chosen AWS station.
3. Check UTC timestamps against the observation time displayed by TMD.
4. Test an offline station: current sensors become unavailable and forecasts work.
5. Confirm visibility is absent when `WV_SENSOR = n`.
6. Reconfigure station/mode and verify forecast coordinates and entity IDs persist.
7. Restart Home Assistant and confirm only the saved station is polled normally.
8. Return to the stable version through HACS or replace the integration folder
   manually and restart. The stable version ignores AWS-specific entry fields;
   AWS-only entries use modeled current conditions until legacy credentials are
   configured. Beta-only customizations such as Station pressure labels may remain
   in the entity registry; verify labels after switching back.

No stable release or merge to `main` is part of this beta task.
