# AQPI and SFPUC rainfall API investigation

Checked 2026-11-20. Branch: `design/aqpi-sfpuc-rain-api`, based on refreshed
`origin/main` at `d2144a3`. The research now has a read-only Admin visualization;
no production collector or model changed.

## Admin visualization

Open **Admin → Rain data** (`/alerts/rain-data`), behind the existing passphrase.
The map and chart now fetch CW3E's latest available observations through the
gated `/alerts/api/rain-data` endpoint. It includes a selectable area/window map, observation and forecast
chart, archive calendar, historical source comparison, and training inventory.

The map and chart aggregate the actual downloaded CSV values. Source URLs and
SHA-256 hashes are returned in the live JSON. The original November 20 snapshot
is retained as a reproducible research artifact and source for the archive
calendar, but its rainfall values are never used as a live fallback.
The research regions reproduce `rain_grids.py`'s rough rectangles; they are not
official sewer drainage boundaries. Missing cell values make the corresponding
regional interval unavailable. Forecast values never enter observation totals.

To rebuild from those raw downloads (paths may vary):

```bash
python scripts/build_rain_snapshot.py \
  --surfrider /tmp/bwtf-aqpi-live.csv \
  --wastewater /tmp/bwtf-aqpi-sfpuc.csv \
  --manifest /Users/ciniper/Downloads/BWTF_Data_Archive/06_aqpi_radar_rain/qpe15min_daily_manifest.csv \
  --checked 2026-11-20 \
  --output features/alerts/data/rain_snapshot.json
```

### Live behavior and limits

- The default is the Surfrider feed: 96 contiguous 15-minute observations.
  The alternate SFPUC-area feed loads 168 hourly observations on selection.
- “24 hours” ends at the provider's newest observation, not at fetch time.
  The UI shows observation end, current lag, download time and provider check
  time. Lag over three hours is flagged; that threshold is a display heuristic,
  not a provider SLA. Forecast columns remain separate throughout.
- Visible tabs check every five minutes and on return to the tab. Manual checks
  obey the same 15-minute server cache. Downloads are serialized per feed per
  worker; failed checks have a one-minute cooldown. A cold server instance may
  download again. This deliberately avoids database writes or new infrastructure.
- Download timeout/size bounds, schema checks, contiguous windows, duplicate
  consistency and nonregressing observation times protect the displayed data.
  Missing cell values stay missing. The parser is shared with the offline builder.
- Failures retain a worker's or browser's last successful download with a visible
  error and its original timestamp. With no last-good result the page shows
  unavailable, never the bundled snapshot. Reloading or redeploying can lose
  this temporary cache. Raw observations are **not durably archived** by this page.
- Archive coverage, historical bias comparisons and KiWIS findings remain dated
  research context. No model, alert rules or public forecast inputs changed.

A separate [automatic archive proposal](aqpi-automatic-archive.md) now preserves
full source files and capture receipts through a scheduled collector. It is
prepared for review, with activation disabled pending explicit approval.

### Recommended next work

1. **Monitor storms now.** Use the live map to compare spatial rainfall and
   short bursts, paying attention to lag and missing intervals. Keep the existing
   prediction model as the baseline while accumulating evidence.
2. **Preserve new data next.** Confirm the provider's update cadence and allowed
   polling with Kyle/CW3E, then schedule durable capture independent of page visits.
   Save changed source files (hash + retrieval time + run time), and deduplicate
   observations by coordinate, interval end and product rather than CSV row.
   Keep forecasts indexed by issue time as well as valid time. This prevents
   losing the rolling 24-hour history and enables honest retrospective evaluation.
3. **Validate by storm and area.** Align time zones and interval definitions,
   compare AQPI against healthy NOAA gauges and any SFPUC city gauges obtained,
   then relate rain to discharge events and bacteria results with their actual
   reporting lags. Hold out complete storms/time periods. Compare incremental
   performance against the current model before promotion; do not apply the
   historical 17–21% difference as a universal correction.
4. **Resolve the gauge gap.** Ask Kyle whether he means the AQPI area CSV or
   raw Telog/RG measurements. For gauges, request the endpoint/access method,
   station coordinates, units, interval semantics, quality flags and history.
   Use verified drainage boundaries for basin features instead of the current
   rough research rectangles when available.

### Validation of the Admin page

- Thirteen targeted pytest tests pass (rain explorer plus existing sample-gap page),
  including invalid/truncated live windows, cache reuse, refresh failure and retry,
  backward provider updates, download size limits and API access protection.
- Live HTTP checks returned 96 Surfrider intervals and 168 SFPUC-area intervals,
  ending Nov 21 00:00 UTC (Nov 20 16:00 Pacific). The city-area totals were
  0.26501 inches / 24 hours and 4.31248 inches / seven days respectively.
  Each fetch and reduction took about 1.2 seconds on the development host.
- Browser checked at narrow and desktop widths: admin unlock, map point popup,
  source/area switching, forecast toggle, exact chart values, calendar and bias
  bars. No browser console errors observed; mobile document overflow corrected.
  The live follow-up verified both feeds, manual refresh/cache reuse, and recovery
  after deliberately stopping the development server. Existing values stayed
  visible with a refresh-failed warning and original fetch time during the outage.
- JavaScript syntax and Git whitespace checks pass.
- Full `scripts/check.sh` rerun for the live follow-up: 79 test files,
  77 pass and two retain the known baseline failures (pytest available in the
  check environment). The targeted tests were rerun after the final parser edit.
  `test_stages_build.py` and `test_stages_s1.py` still fail their existing
  artifact-freshness checks for `features/forecast/live_dashboard.py`.
  The actual hash is `1a7dd7f5bc28660482e71bf4f6daec13a18b3f142dd210608e602122e6c547bc`;
  the stage manifest records `0ba83a5c964ed3e8beca71fe198a29c76fad4aaffce3eef92b90ad654c6a06cc`.
  Both the file and manifest match `origin/main`; no forecast code or model
  artifact was changed here. `test_served_golden.py` passes. This is a known
  inherited validation failure, not a claim of a fully green repository suite.

## Finding

**A working public feed supplies the previous 24 hours of rainfall over SF at
15-minute intervals. It is AQPI radar QPE, not measurements from SFPUC's city
rain gauges.** The Surfrider CSV also contains forecast columns, which must be
kept separate from observed rainfall estimates.

A second public AQPI file, labelled SFPUC Wastewater Enterprise, supplies seven
days of hourly radar estimates. Separately, SFPUC has a working public KiWIS
gauge-data API for its Regional Water System. That catalog is not evidence of
access to the sewer-system Telog/RG gauge network; the recent SF Downtown series
tested here were empty or questionable.

## Verified AQPI endpoints

| Product | URL | Verified contents |
| --- | --- | --- |
| Surfrider live | https://cw3e-datashare.ucsd.edu/AQPI/qpe15min-qpf01/SURFRIDER_FOUNDATION_qpe15min-qpf01.csv | 96 fifteen-minute QPE columns, then 100 hourly QPF columns |
| SFPUC Wastewater live | https://cw3e-datashare.ucsd.edu/AQPI/qpe-qpf01/SFPUC_WASTEWATER_ENTERPRISE_qpe-qpf01.csv | 168 hourly QPE columns, then 100 hourly QPF columns |
| Historical Surfrider archive (user-supplied link) | https://cw3e-datashare.ucsd.edu/AQPI/tmp_archive/qpe15min/ | 306 files, filenames from `202510020000` through `202608300000`; not a current rolling archive |
| Provider guide | https://cw3e-datashare.ucsd.edu/AQPI/CW3E-AQPI_data-feed_quick-guide.pdf | Inches; UTC accumulation-ending timestamps; QPE is radar, QPF is NBM forecast |

These are ordinary HTTPS CSV downloads, with no API key required in this check.
The provider's guide explicitly describes retrieval with HTTP GET. It describes
the hourly product; the 24-hour extent of the fifteen-minute product was verified
directly from the live CSV rather than inferred from that guide.

### Live Surfrider snapshot

Downloaded at 2026-11-20 18:45 UTC (10:45 PST), HTTP 200:

- `Last-Modified: Fri, 20 Nov 2026 18:36:08 GMT`.
- 17,335,250 bytes; 18,444 rows; 16,610 distinct `gridpoint` coordinates.
- Metadata: `name`, `institution`, `gridpoint`, `run_time`.
- `run_time = 2026-11-20 16:00` UTC (08:00 PST).
- Observations: `qpe15min_202611191615` through `qpe15min_202611201600`.
  These represent 24 hours ending at 08:00 PST on Nov 20, not 24 hours ending at
  download time. Observations lagged retrieval by about 2 hours 46 minutes.
- Forecasts: `qpf01_202611201700` through `qpf01_202611242000`.
- All 3,165 distinct points within the existing collector's city box
  (-122.53 to -122.34 longitude, 37.69 to 37.83 latitude) had all 96 QPE values.
  This demonstrates snapshot completeness, not long-term reliability.
- `gridpoint` actually uses WKT `POINT (longitude latitude)`; follow the CSV,
  despite the guide's prose listing latitude first.

For each location, sum only the 96 `qpe15min_` columns to obtain the 24-hour
total. Deduplicate grid coordinates before regional averaging; overlapping
watersheds repeat points. Missing values must not silently become zero.
Use timestamps to validate continuity and expose the observation window and age.

The live grid differs from the archived grid (the archive notes 29,219 rows in
its later files). Never join these files by row number. Confirm resolution and
grid changes with CW3E before assuming the historical 250 m description still
applies unchanged to the current feed.

### SFPUC-labelled AQPI snapshot

The Wastewater file returned 6,278 grid rows with name `SFPUC Waste Water
Enterprise`, run time 2026-11-20 16:00 UTC, and hourly QPE ending
2026-11-13 17:00 through 2026-11-20 16:00 UTC. This is a useful seven-day
lookback candidate. The agency label identifies the extraction recipient/area,
not rain-gauge provenance. The guide identifies the QPE source as the AQPI radar
mosaic and QPF as NBM.

## SFPUC's actual public data API

The public [Regional Water System viewer](https://webapps.sfpuc.org/kiwidgets/wiski/)
embeds this API base:

`https://webserv.sfwater.org:8080/KiWIS/KiWIS`

The viewer describes preliminary meteorological and operational data for the
Hetch Hetchy and local water systems. Its source config identifies precipitation
timeseries group `41736` and cumulative precipitation group `50877`.

Working GET requests (no authentication required in this check):

```bash
# Station catalog: 202 entries returned.
curl --get 'https://webserv.sfwater.org:8080/KiWIS/KiWIS' \
  --data-urlencode 'service=kisters' --data-urlencode 'type=queryServices' \
  --data-urlencode 'request=getStationList' --data-urlencode 'datasource=0' \
  --data-urlencode 'format=json'

# Precipitation series used by the viewer: 18 entries returned.
curl --get 'https://webserv.sfwater.org:8080/KiWIS/KiWIS' \
  --data-urlencode 'service=kisters' --data-urlencode 'type=queryServices' \
  --data-urlencode 'request=getTimeseriesList' --data-urlencode 'datasource=0' \
  --data-urlencode 'format=json' --data-urlencode 'timeseriesgroup_id=41736'

# Example daily values at San Andreas; nulls must remain missing.
curl --get 'https://webserv.sfwater.org:8080/KiWIS/KiWIS' \
  --data-urlencode 'service=kisters' --data-urlencode 'type=queryServices' \
  --data-urlencode 'request=getTimeseriesValues' --data-urlencode 'datasource=0' \
  --data-urlencode 'format=json' --data-urlencode 'ts_id=47224042' \
  --data-urlencode 'from=2026-11-01' --data-urlencode 'to=2026-11-21'
```

Catalog responses are arrays with the field names in the first row. Value
responses are objects per series containing `ts_id`, `rows`, `columns`, `data`.
Series selection can also use `station_id=47821` for SF Downtown. Units can be
requested with `returnfields=station_name,ts_id,ts_name,ts_unitname`.

| Station / series | ID | Observed result |
| --- | --- | --- |
| SF Downtown / `10. P.Day.18MonthMvgWin` | 47397042 | No values for Nov 18–21 |
| SF Downtown / `03. P.60` | 47579042 | No values for Nov 1–21 |
| SF Downtown / `14. P.DayTotal.O` | 47393042 | 18 daily entries Nov 1–18, all zero; not validated as reliable rain observations |
| SF Downtown / `10. P.Day.O` | 48614042 | No values for Nov 1–21 |
| San Andreas Rainfall / `10. P.Day.18MonthMvgWin` | 47224042 | Daily values through Nov 18 (4.44 inches at `2026-11-18T08:00:00Z`); Nov 19 and 20 null |

Units metadata returned `inch` for 47397042, 47579042 and 47224042. A daily
value is not automatically a rolling last-24-hours total; interval boundaries
and freshness still need validation. Station-name/coordinate inspection found
SF Downtown but no identified RG10/RG12/RG13/RG20/RG21/RG34/RG35 city wastewater
gauges. This is a limited public-catalog search, not proof no partner API exists.

## What was already in the repository and local archive

Fetched and pruned origin, then inspected all **35 remote branches** (excluding
the symbolic `origin/HEAD`). The following three files are byte-identical on
33 branches, including main; the two old branches `perf/easy-wins` and
`perf/static-cache` lack them:

- `features/forecast/src/collectors/rain_grids.py`: local archived AQPI files to
  region/hour means, with gap handling. No live AQPI HTTP collector.
- `features/forecast/RAIN_SOURCES.md`: prior comparison against NOAA gauges and
  MRMS, with AQPI wet bias and missing-period findings. Advises confirming the
  endpoint and polling terms with Kyle before production integration.
- `features/forecast/data/rain_gauges_records_request_draft.md`: draft request
  for SFPUC Telog gauge metadata and measurements, still marked NOT FILED YET.

Relevant commits already in main: `ab9e3de` (rain-source research), `14b2e59`
(research merge), `9979246` (gauge request), `6116a18` and `7241fc9` (gauge
evidence in archived reports). The endpoint-reference search across branch tips
did not reveal a live URL or KiWIS integration.

Inspected `/Users/ciniper/Downloads/BWTF_Data_Archive`, including its README,
rain folders and the two archived code snapshots:

- `05_sfpuc_rain_gauges/` contains only the request draft, not delivered raw
  gauge data. SFPUC daily gauge figures also exist in the SMR PDFs under `01`;
  the repository TODO documents their extraction as unfinished.
- `06_aqpi_radar_rain/qpe15min_daily/` contains 306 CSVs, matching the count
  still listed by the remote archive. These are historical radar inputs.
- `06_aqpi_radar_rain/kyle_hourly/aqpi_hourly.csv` has 44,208 rows at six beach
  locations. Its `aqpi_live.csv` rows span 2026-10-06 17:00 through Oct 7 16:00
  UTC. This is strong evidence for the earlier 24-hour-feed reference, although
  Kyle's exact meaning cannot be established from the files alone.
- `07_rain_grids/` contains derived AORC cutouts and AQPI regional means.
- The archive README identifies NOAA/ACIS gauge totals and ERA5 as training
  inputs; AQPI is separately researched, not shown to be the deployed model's
  rain source. It also says weather-model forecast runs live in the repo and
  forecast-history snapshots live outside this folder.
- The collector still hard-codes `~/Personal/BWTF_Data_Archive`, while the
  supplied archive is now in `~/Downloads/BWTF_Data_Archive`. A future collector
  change should make the root configurable.

## Next decision

For a prototype of recent rainfall, use the verified Surfrider CSV with explicit
radar labelling, observation-window age, coordinate matching and missing-data
checks. The SFPUC-labelled hourly AQPI file is a candidate for longer lookback.
Archive snapshots if a durable series is needed; `tmp_archive` currently stops
months before the live feed. Keep forecasts separate and assess the historical
wet bias before using rainfall amounts in model features.

The specific unresolved question for Kyle is: **“Did you mean the CW3E Surfrider
24-hour radar feed, or SFPUC's actual Telog rain gauges? If gauges, what is the
endpoint, access method and station list?”** Also confirm intended polling
cadence, current grid resolution, latency, gauge adjustment and archive support.
The provider guide lists Ricardo Vilela and Jon Rutz as feed contacts. No message
or records request was sent as part of this investigation.
