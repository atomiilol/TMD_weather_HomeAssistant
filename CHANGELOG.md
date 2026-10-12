# Changelog

Versions follow semantic versioning. A version becomes available through HACS
after the corresponding GitHub Release is published; a tag alone is insufficient.

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
