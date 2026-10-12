# Changelog

Versions follow semantic versioning. A version becomes available through HACS
after the corresponding GitHub Release is published; a tag alone is insufficient.

## 0.4.1 — 2026-10-12

Requires **Home Assistant 2026.10.1 or later**.

- Allow Domain 2 area forecasts up to 60 seconds to complete instead of the
  previous 10-second limit, following TMD's warning about slower area requests.
  Keep connection attempts limited to 10 seconds and other endpoint budgets
  unchanged. Response size limits and rate-limit backoff still apply.
- Report response timeouts separately from connection failures, with English and
  Thai explanations. Failed source checks retain the saved source, fetch interval,
  configuration and entities; no automatic retries are added.
- Verify options labels, source selector labels and errors through Home Assistant's
  translation loader. Document restarting and refreshing after updates to clear
  cached translations when the dialog displays raw keys.

The endpoint responded with HTTP 401 to an unauthenticated live check.

## 0.4.0 — 2026-10-12

Requires **Home Assistant 2026.10.1 or later**.

- Add **Configure → Forecast source → Domain 2 (3 km grid)** for modeled current
  conditions and hourly forecasts. Preserve the existing source as the default.
- Request `domain=2` through TMD's documented `/v1/forecast/area/box` API, using
  a small bounding box and accepting only a nearest grid point within 3 km.
  Retain the 48-hour forecast limit. Daily forecasts and actual station readings
  keep their existing sources.
- Validate a sample with the saved token before applying a source change. Reject
  invalid coverage, mismatched clock/cadence and service/authentication errors
  without replacing the previous settings. Preserve configuration and entity IDs.
- Support the documented area response envelope and its local Bangkok clock,
  retaining strict timestamp handling for coordinate responses and the shared
  NWP rate-limit cooldown. Expose the selected source in device info/diagnostics.

TMD's newer introduction documents a 3 km grid, while older endpoint
pages still contain previous resolution figures. Grid spacing does not guarantee
measurement accuracy.

## 0.3.0 — 2026-10-12

Requires **Home Assistant 2026.10.1 or later**.

- Remove unsupported sensor placeholders, including dew point and visibility in
  modeled mode, and clean up their old registry entries automatically.
- Defer new sensors until TMD supplies a value. Add them automatically when a
  later update provides data. Supported sensors with temporary gaps become
  unavailable and recover using their existing IDs.
- Keep the main weather entity and its forecasts, including TMD's unknown
  sky-condition state when no condition code is supplied.
- Add **Configure → Weather fetch interval (minutes)**, accepting whole numbers
  from 1 to 1440. The saved option applies to current conditions and active
  hourly/daily forecast polling and persists across restarts. Existing schedules
  are retained until configured; server rate-limit backoff still applies.
- Preserve location configuration, credentials and supported entity IDs when
  applying options. Include active polling intervals in redacted diagnostics.
- Advance cached model hours and expire stale station measurements between fetches,
  so long intervals cannot leave old current values displayed as fresh weather.

Authenticated production NWP forecasts remain experimental and need validation
with a user-owned token. Weather3Hours supplies no observed sky-condition code.

## 0.2.0 — 2026-10-12

First published release. Includes UI configuration, station or explicitly modeled
current weather, hourly/daily NWP forecasts, and English/Thai translations.
Requires **Home Assistant 2026.10.1 or later**.

- Use the supplied TMD emblem as the integration's local icon and logo, including
  high-density variants, and display it in the README.
- Add separate current temperature, humidity, sea-level pressure, wind speed,
  wind direction, dew point and visibility sensors alongside the existing rainfall
  sensor. All measurements share the configured current source and cached data.
- Add explicitly labeled daily forecast high/low temperature and rainfall sensors
  for today in Asia/Bangkok, with independent forecast availability.
- Preserve existing configuration and weather/rainfall entity IDs. No additional
  API calls or new configuration are needed; station-only fields stay unknown in
  model mode, and stale data is unavailable.
- GitHub release publishing for HACS updates: version tags must match the manifest,
  and tests, HACS validation and Hassfest must pass before publishing.

Authenticated production NWP forecasts remain experimental and need validation
with a user-owned token. Weather3Hours supplies no observed sky-condition code;
current conditions can be unknown while station measurements are available.

## 0.1.0 — prepared, not yet released

- UI setup, reauthentication and optional legacy station credentials.
- Current station measurements from Weather3Hours, selecting the nearest fresh
  station within 100 km; explicit NWP current estimates when no station credentials
  are configured. No silent source switching during outages.
- Hourly and daily NWP forecasts with the distinct official response envelopes.
- Current rainfall sensor, station-supplied visibility/dew point and diagnostics.
- English/Thai translations, typed async clients and independent coordinators.
- HACS metadata, local brand icon, release validation, hassfest/HACS CI, lint,
  mocked integration tests and reproducible development dependencies.

Known limitations: live NWP forecasts need a user-owned token for validation;
Weather3Hours does not provide a current sky-condition code. Observation service
credential lifecycle and quota details require confirmation with TMD.
