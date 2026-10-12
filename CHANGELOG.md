# Changelog

Versions follow semantic versioning. A version becomes available through HACS
after the corresponding GitHub Release is published; a tag alone is insufficient.

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
