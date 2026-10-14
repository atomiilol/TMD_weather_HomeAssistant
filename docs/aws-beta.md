# TMD AWS stations beta — 0.5.0b3 (historical)

AWS support is now in stable 0.5.0. See the [current station guide](aws-stations.md).
The instructions below describe the historical beta release.

This feature lives on `beta/aws-stations`. It is experimental and does not change
the stable `main` branch or replace the latest stable GitHub Release. Requires
Home Assistant 2026.10.1 or later. AWS means **Automatic Weather Station** here.

## Install for testing

Back up Home Assistant before switching an existing installation to the beta.
The beta uses the same `tmd_weather` domain, so install one copy at a time.

For HACS, add this repository as a custom **Integration** repository using the
[README instructions](../README.md#installation). Opt in to beta/prerelease
versions and select **v0.5.0b3** when downloading or redownloading the integration.
The GitHub prerelease is the supported beta version; merely adding the repository
without selecting the beta continues to install the stable release.

For a manual install, download the [beta branch ZIP](https://github.com/atomiilol/TMD_weather_HomeAssistant/archive/refs/heads/beta/aws-stations.zip)
or the source ZIP from the `v0.5.0b3` prerelease. Copy the complete
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
  observation search coordinates. The flow sorts nearby catalog stations locally
  by distance, checks their live data nearest first, skips
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

The automatic confirmation page contains a **Selected AWS station** dropdown,
prefilled with the chosen station's number/name. Nearby alternatives include
their distance. Choosing a different station uses manual selection and validates
its response before saving.

## See and change the selected station

Open the integration's device page. **Selected AWS station** identifies the
station even during outages. **AWS station status** reports online with recent
observations, offline, stale observations, no usable observations or a connection
error; its attributes include the latest observation time.

Use **AWS station (current region)** to choose another station in the same region.
The dropdown saves only after a valid response confirms its AWSID. For another
region, use **Reconfigure → Manual → Region → Station**. Forecast coordinates and
supported entity IDs are preserved. Add the station/status sensors and station
selector to a dashboard through the usual entity picker.

## Discovery and polling

The supplied catalog includes **latitude and longitude for all 227 stations**.
Automatic selection calculates great-circle distances locally, keeps stations
within **100 km**, and checks their observations **one at a time, nearest first**
through `http://122.155.135.49/api/home/site/<AWSID>`. It stops at the first online
station with usable observations from the last three hours. When that nearest
candidate is healthy, only **one station request** is needed, even on first setup.
There is no full-catalog coordinate scan, reverse-geocoding API, or province lookup.
Stations across region/province borders remain eligible if they are closer.
The confirmation dropdown lists nearby stations and distances directly from the
catalog, including alternatives that have not yet been requested.

Each live request has a **10-second timeout**, with a **10-minute overall search
limit**. The UI reports how many nearby candidates have been considered and
supports cancellation. Unreachable, offline, empty, stale or future-only responses
are skipped; live coordinates must also confirm the station is within 100 km.
Several failed nearby candidates can still make a search slower; network conditions
and TMD response times determine its duration.

Public station metadata is cached for **six hours** and saved to Home Assistant
storage. Catalog coordinates remain available regardless of cache expiry or
restart, and each selected candidate is freshly validated. Stations already found
offline or without usable recent observations
are skipped for up to **ten minutes** to avoid immediately repeating slow checks.
They become eligible for a new check after that short cache expires; fresh live
evidence from polling or manual selection can clear it sooner. The selected AWSID
is persisted in the config entry, so normal restarting still polls only that ID.
Existing entries retain their selected AWSID when updating this beta. Reconfigure
automatic mode to run the new search; normal updates do not search other stations.

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
does not establish availability. Unsupported measured fields, including sky
condition and dew point, are not fabricated. The weather card's **condition label**
comes from the NWP model's current-hour `cond` value; measurements remain AWS
observations. The weather entity is named **Weather (modeled condition)** and
exposes `condition_source: nwp_model` separately from `measurement_source: aws_station`.
If the model supplies no usable current-hour sky code, the condition remains
unknown. Temperature and rainfall are never used to guess a sky condition.

During AWS outages, measurement sensors become unavailable. The weather entity's
current measured readings are absent. A healthy modeled condition and hourly/daily
forecasts remain usable, including through `weather.get_forecasts`. Home Assistant
filters unavailable entities out of that service, so the weather entity stays
available while its forecast source is healthy. NWP measurements are never used
as a fallback for AWS current readings. The modeled condition label is the only
current-weather field supplied by NWP.

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
evidence. Live AWS requests and automatic selection now work in development, and
the parser was checked against a sanitized real response from station 2. Its
hourly clock is consistent with UTC interpretation. Units still follow the
supplied evidence and need confirmation before a stable release. Tests include
synthetic responses and the sanitized live fixture; embedded forecast/token
metadata is excluded.

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
