# SF Sewage Overflow Forecast

Predicts the likelihood of Combined Sewer Overflow (CSO) events in San Francisco based on rainfall models — both historical correlation and real-time forecasts.

## The Problem

San Francisco has a **combined sewer system** — stormwater and sewage share the same pipes. During heavy rain, the system's 200 million gallons of underground storage can overflow, discharging partially treated wastewater into the ocean and bay. The city posts beaches *after* a discharge occurs, but by then people may have already been in the water.

**This project aims to predict CSO events *before* they happen**, giving surfers, swimmers, and beachgoers advance warning.

## Approach

### Phase 1: Historical Correlation Model
Build a dataset correlating **cumulative rainfall** with **known CSO discharge events** to establish thresholds:
- At what cumulative rainfall (over what time window) do discharges begin?
- How do antecedent conditions (prior rain, soil saturation) affect the threshold?
- Do different drainage basins (Westside, North Shore, Southeast) have different thresholds?

### Phase 2: Real-Time Forecasting
Use weather forecast models to predict CSO likelihood in the next 1-7 days:
- **1-2 days out**: GFS, ECMWF, ICON models should agree — high confidence
- **3-7 days out**: ECMWF most reliable — moderate confidence
- **7+ days**: Low confidence, general awareness only

### Phase 3: Hyperlocal Adjustments
Account for SF's microclimates and topography:
- Winds from the south → less impact (rain shadow)
- Winds from the west → more impact (orographic lifting over Twin Peaks, Mt. Davidson, Seacliff headlands)
- Even low hills significantly increase local rainfall beyond what models predict

## Domain Knowledge

*From Rubin Goldberg, former SFPUC engineer:*

### Key Rainfall Thresholds (Marina District reference)
| Metric | Level of Interest | Likely CSO |
|--------|------------------|------------|
| 3-hour total | ~0.5" with preceding rain | ⚠️ Interesting |
| Running 24-hour total | ~0.75" | ⚠️ Of interest |
| Running 24-hour total | >1.0" | 🚨 Definitely interesting |

### CSO Discharge Behavior (observed Feb 2025 storm)
- ~1.0" in NE city, ~1.3" in SE, ~1.5" in west
- Ocean Beach outfalls: **discharging** (Westside basin, lower threshold)
- Islais Creek & Mission Creek: **discharging** (Southeast basin)
- North Shore & Southeast main outfalls: **not discharging** (higher capacity)
- This suggests **different basins have different overflow thresholds**

### Weather Model Guidance
| Timeframe | Best Model | Reliability |
|-----------|-----------|-------------|
| 1-2 days | GFS, ECMWF, ICON (should agree) | High |
| 3-7 days | ECMWF | Moderate |
| 7+ days | Any | Low (guesswork) |

If models **disagree** at 1-2 days, that itself is a signal worth flagging.

## Data Sources

### Rainfall — Historical & Real-Time

| Source | Type | Access | Notes |
|--------|------|--------|-------|
| **SFPUC Rain Gauges** | 21 gauges citywide | Not publicly shared in useful format | Best data but hard to get |
| **Weather Underground PWS** | Personal weather stations | Web scrape / API | Can export as txt |
| **NOAA/NWS** | Professional stations | Free API (MesoWest) | SFOC1 station |
| **NOAA Tides & Currents** | Met observations | Free API | Includes precip |
| **Windy (ECMWF)** | Forecast model | Web scrape only (license blocks API) | 3-hr totals, 7-day forecast |
| **GFS** | Forecast model | Free API (NOAA) | Hourly, global |
| **ICON** | Forecast model | Free (DWD) | German model, good for Pacific storms |

#### Key Weather Stations (SF)
| Station | Location | ID |
|---------|----------|----|
| Marina Blvd & Fillmore | Marina District | KCASANFR2075 |
| 1762 North Point | Marina | KCASANFR1851 |
| Casa Sanchez | Mission/SOMA | KCASANFR2077 |
| Outer Sunset | Sunset District | KCASANFR1317 |
| Meadowlark | Windy PWS | f07ed4a1 |
| SFOC1 | SF (NWS/MesoWest) | SFOC1 |

### CSO Events — Historical

| Source | Type | Notes |
|--------|------|-------|
| **SFPUC Beach Map** | Real-time CSO status | `infrastructure.sfwater.org/lims.asmx/getBeaches` |
| **BWTF repo** | Real-time scraper | Can poll and log events |
| **Friends of Mission Creek** | CSO tracking | https://www.friendsofmissioncreek.org/cso |
| **SFPUC Annual Reports** | Historical discharge data | May need FOIA/public records request |
| **EPA ECHO** | NPDES permit violations | Discharge monitoring reports |

### Beach Postings — Historical (signs up / signs down)

| Source | Type | Notes |
|--------|------|-------|
| **CA State Water Board — BeachWatch** | Every SF beach advisory since 1999, per SFPUC station, with posting and reopening dates and cause | [data.ca.gov dataset](https://data.ca.gov/dataset/beach-water-quality-postings-and-closures); stored under `data/beachwatch/` by `src/collectors/beachwatch.py --refresh` (see its NOTES.md). DataSF and the live feed carry samples, not postings. |

## CSO Outfall Geography

### Drainage Basins & Outfalls
```
                    Golden Gate
                   ┌───────────┐
    North Shore ──►│ Aquatic Pk │  NSB outfalls
    Basin          │ Crissy Fld │  (higher capacity)
                   │ Crane Cove │
                   │ Mission Ck │
                   └───────────┘
                                        SF Bay
    ┌──────────┐                   ┌───────────┐
    │Ocean Bch │◄── Westside       │Islais Ck  │◄── Southeast
    │Fort Fun  │    Basin          │Candlestk  │    Basin
    │China Bch │    CSD-001-007    │           │    SEB outfalls
    │Baker Bch │    (lower thresh) │           │
    └──────────┘                   └───────────┘
      Pacific Ocean                   SF Bay (south)
```

**Key insight**: Westside basin (Ocean Beach) appears to have a **lower overflow threshold** than North Shore, likely due to pipe capacity and storage differences.

## Project Structure

```
sf_sewage_forecast/
├── config/
│   ├── stations.yaml          # Rain gauge stations & metadata
│   └── basins.yaml            # Drainage basin thresholds & outfalls
├── data/
│   ├── raw/                   # Raw collected data
│   ├── processed/             # Cleaned, aligned datasets
│   └── models/                # Trained model artifacts
├── src/
│   ├── collectors/
│   │   ├── nws_rain.py        # NWS/MesoWest rainfall collector
│   │   ├── wunderground.py    # Weather Underground PWS scraper
│   │   ├── noaa_met.py        # NOAA met observations (precip)
│   │   ├── forecast_models.py # GFS/ECMWF forecast data
│   │   ├── csd_labels.py      # Ground-truth CSD daily labels (data/csd/)
│   │   └── csd_ciwqs/         # CIWQS SMR scraping pipeline (see its README)
│   ├── models/
│   │   ├── cumulative_rain.py # Cumulative rainfall calculator
│   │   ├── threshold_model.py # Basin-specific CSO threshold model
│   │   └── forecast.py        # Prediction engine
│   └── api/
│       └── predict.py         # Prediction API / CLI
├── notebooks/
│   └── 01_exploration.ipynb   # Data exploration & model dev
├── tests/
├── requirements.txt
└── README.md
```

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Quick Start

```bash
# Collect recent rainfall data
python -m src.collectors.nws_rain

# Collect CSO events from SFPUC
# CSO events now come from data/csd/ (historical) and the Supabase
# alert_log written by the pg_cron watcher (live) — no polling script needed

# Run prediction for next 48 hours
python -m src.api.predict
```

## Swapping a part (weather model, weights, gauge, basins, live corrections)

See [SWAPS.md](SWAPS.md): build the new piece next to the served one, score it with the stages build, then promote.

## Reports (what to read)

Static pages under `reports/`, served by the app at `/reports/<name>.html`; `/reports/`
is the index (`export_reports_index.py` → `reports/index.html`), linked from the
forecast page and the Model check. Current, in reading order: the forecast, stage
by stage (`export_stages_report.py`); how the forecast works
(`export_how_it_works.py`); inside the overflow model (S2), one explorer per model
set (`export_model_explorer.py`); from an overflow to beach risk (S3–S4,
`export_stage2_explorer.py`). Archived pages are listed in the index's `ARCHIVED`
table, stamped with a banner and never regenerated. Every number on a page is
computed from the model files or the stage artifacts; the explorers end with a
self-check against scikit-learn / the stored scorecard.

```bash
venv/bin/python features/forecast/src/models/export_stages_report.py                   # the forecast, stage by stage
venv/bin/python features/forecast/src/models/export_how_it_works.py                    # how the forecast works
venv/bin/python features/forecast/src/models/export_model_explorer.py --offline        # today's set's S2 explorer
venv/bin/python features/forecast/src/models/export_model_explorer.py --offline --model icon-w38-nosplit-lt1-bflags   # a candidate's
venv/bin/python features/forecast/src/models/export_stage2_explorer.py                 # S3–S4, every model set
venv/bin/python features/forecast/src/models/export_reports_index.py                   # the index, and the archive banners
```

An S3–S4 variant (stored as a "stage 2 variant": the beach split and its lingering table) is fit and paired
with any overflow model as a candidate set:

```bash
venv/bin/python features/forecast/src/models/stage2_variants.py fit                 # fit a variant (the outfall split)
venv/bin/python features/forecast/src/models/stage2_variants.py save --stage1 icon-trees-nosplit-lt1-bflags --variant v2   # saved as icon-trees-osplit-lt2-bflags
```

Re-run after `train_v4.py` / `--rescore` / `leaderboard.py --save`.

Every set on disk, with its description, its five stages and its weights as plain numbers:
`data/models/catalog.json` (generated by `src/models/export_catalog.py`; SWAPS.md, "The model catalog").

Names: overflow models `gb_v1` (trees) and `logit_v1` (weights); stage 2
versions `v1` (basin composition) and `v2` (outfall split). A set is named by
its five stages' codes at the time it was made (`shared/lineup.py`,
STAGES_DESIGN.md Part B 36): the live forecast is `icon-w38-osplit-lt2-bflags`
(ICON · 38-weight · Outfall split · Linger table 2 · Basin flags). Names before
2026-10-07 (`logit_v1_s2v2`, `gb_v1`) are in `lineup.RENAMED`. The old release
label "v4" is retired (2026-09-26).

## References

- [SFPUC Beach Water Quality Map](https://webapps.sfpuc.org/sapps/beachesandbay.html)
- [Friends of Mission Creek — CSO Tracking](https://www.friendsofmissioncreek.org/cso)
- [SFPUC Combined Sewer System](https://www.sfpuc.gov/about-us/our-systems/sewer-system/our-combined-sewer)
- [SFPUC Ocean & Beach Monitoring](https://www.sfpuc.gov/programs/ocean-and-beach-monitoring)
- [Windy ECMWF Forecast](https://www.windy.com/37.803/-122.437?rain)
- [MesoWest API](https://mesowest.utah.edu/cgi-bin/droman/mesomap.cgi)

## License

MIT
