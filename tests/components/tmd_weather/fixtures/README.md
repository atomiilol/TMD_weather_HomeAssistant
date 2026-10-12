# Fixture provenance

`hourly.json` and `daily.json` are **synthetic, credential-free** examples.
They are not production captures. Tests freeze time to match the model hour.
The daily fixture uses the officially documented `weather_forecast.locations`
envelope.

`documented_hourly.json` and `documented_daily.json` reproduce the coordinate
endpoint JSON examples retrieved on 2026-10-11 from:

- https://data.tmd.go.th/nwpapi/doc/apidoc/location/forecast_hourly.html
- https://data.tmd.go.th/nwpapi/doc/apidoc/location/forecast_daily.html

These official examples contain dates in August 2017. They verify compatibility
with documentation, not current authenticated production responses. The differing
hourly and daily envelopes are intentional and protected by regression tests.

The short series covers offsets, missing fields, rainfall and temperature-only
condition codes. Actual requests default to 48 hours / 7 days; shorter nonempty
series are accepted.

`observation_live.json` is a sanitized three-station subset of the official
Weather3Hours JSON response captured on 2026-10-12 using the public example access
linked from TMD's API directory. It preserves the original measurement strings
and 07:00 Bangkok timestamps. The credential-bearing Header/Uri was removed.
`observation.json` uses the same measurements with synthetic October 11 timestamps
to match the default frozen test clock. It is not a second production capture.

Add sanitized real fixtures only after authorized production verification, with
provenance and capture dates. Never include tokens or raw authenticated request
metadata.
