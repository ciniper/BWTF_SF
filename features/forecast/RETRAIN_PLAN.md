# Forecast v2: retraining on ground-truth CSD events

**Status: v3 deployed on this branch** (2026-09-02) — the training basin
tables now come from the canonical station registry (`shared/stations.py`);
before, the stage-2 "Baker-China" group actually pointed at Mission
Creek/Crane Cove (BAY#220/230) and "Southeast" at a phantom BAY#315_SL.
Stage 1 (rain→discharge) never used station basins: retrained on refreshed
2016→present data, metrics reproduce v2.1 exactly (holdout PR-AUC citywide
0.911, Westside 0.828, North Shore 0.790, Southeast 0.812; zero critical
FNs). Stage 2 impact curves were refit on the corrected memberships and
changed materially: Ocean Beach's baseline fell 0.19→0.05 (it had absorbed
Baker/China), Baker-China (now truly OCEAN#15/15E/16/17) disperses in ~1-2
days, and the real Southeast group (Islais + Crane Cove + Candlestick trio)
holds contamination through day 6-7 (d6-7 large 0.83 over a 0.35 baseline).
BAY#220 Mission Creek is excluded from stage 2 — Central-basin discharges,
no stage-1 model (matches OBSERVED_STATION_BASIN leaving 4618 unmapped).
Production `thresholds.json` is now the v2 empirical P(discharge|rain)
table (the old v1-format file was read by no frontend; the simulator's
threshold display is hard-coded HTML).

v2.1 history (2026-08-21): rain data extended to 2016,
`src/models/train_v2.py` trained on ground truth, v2 pkls promoted to
production paths, dashboard shows composed risk (P(discharge) × isotonic-
smoothed persistence by discharge size), observed SFPUC CSO onsets from
the Supabase alert_log override model probabilities for past days, hourly
peak-intensity features, per-site-group impact curves (basin display =
worst group, per-group in `predictions.groups`). Historical baselines: v2
holdout PR-AUC was 0.87/0.77/0.78/0.80; proxy-trained v1 scored
0.56/0.54/0.91*/0.28 (*in-sample); persistence null recalls 0.08–0.15.
Remaining: the v3 list below (tide/swell/UV, concentration targets,
housekeeping).

## Why

Until now the models trained on a proxy label: "3+ bacteria stations elevated
across 2+ basins" from weekly beach samples. We now have SFPUC's own per-event
discharge records (1,007 events, Oct 2016 – Oct 2025) extracted from monthly
self-monitoring reports on CIWQS (see `data/csd/NOTES.md`).

Measured against ground truth on the 637 overlapping sample days
(Jul 2020 – Oct 2025, fully-covered months):

| Proxy label quality | Value | Meaning |
|---|---|---|
| Precision | **79%** | 18 of 84 proxy positives had **no** discharge in the prior 3 days — the old model was trained to alarm on bird/runoff bacteria |
| Recall | **50%** | 65 of 131 sample days that followed a real discharge were labeled negative — the old model was trained to stay quiet on half of real events |
| Label days | ~2/week | Proxy exists only on sample days; ground truth covers **every** day (2,892 fully-covered days) |

Ground-truth rain→discharge dose-response (same-day citywide avg rain,
fully-covered days 2020-07 → 2025-10):

| Rain | n | Westside | North Shore | Southeast | Citywide any |
|---|---|---|---|---|---|
| 0.10–0.25" | 68 | 0% | 0% | 1% | 1% |
| 0.25–0.50" | 68 | 1% | 1% | 9% | 10% |
| 0.50–0.75" | 26 | 27% | 8% | 12% | 35% |
| 0.75–1.00" | 15 | 33% | 33% | 60% | 80% |
| 1.00–1.50" | 14 | 57% | 57% | 93% | 93% |
| ≥1.50" | 8 | 100% | 100% | 100% | 100% |

Clean, monotonic, basin-differentiated — Southeast (Islais) trips earliest at
moderate rain; Westside/North Shore step up hard past 0.5". This table alone
replaces the hand-tuned `thresholds.json`.

## Label source

`src/collectors/csd_labels.py` (new) builds the daily table:

- Per app basin (`Westside`, `North Shore`, `Southeast`, plus `Mission Creek`
  which has no bacteria stations): `_csd` (0/1), `_volume_mg`, `_outfalls`,
  `_covered`.
- Positives in the fully-covered window: Westside 51, North Shore 32,
  Southeast 59, Mission Creek 65 event days; 78 citywide.
- **Coverage discipline:** days in non-covered months (`_covered = 0`, e.g.
  Oceanside before 2018) are *unknown*, never negatives. The monthly grid
  (`data/csd/sf_csd_monthly_coverage.csv`) is the source of truth.

## Architecture: split the two problems the old model conflated

**Stage 1 — hydrology: P(basin discharges today | rain).**
Train on ground truth over all covered days (not just sample days).
- Start with one pooled GradientBoosting/logistic model with basin as a
  feature + per-basin isotonic calibration (32–78 positives per basin is thin
  for fully separate models; pooling shares the rain response).
- Add a magnitude head: regression on log1p(volume_MG) for event days —
  volume drives how long beaches stay posted.
- Keep features that are train/inference-consistent (daily totals, cumulative
  windows, lags — the ACIS vs Open-Meteo lesson in `train.py` still applies).
  New candidates now that we have event *start times*: max 1h/3h/6h intensity
  and wet-hours count from hourly data (Open-Meteo historical hourly for
  training, same API at inference — consistent by construction).

**Stage 2 — beach impact: P(elevated bacteria | discharge recency/size).**
Small model (logistic or empirical decay table) on sample days:
features = days-since-last-CSD in basin, log volume, outfall count; target =
basin bacteria exceedance. This is measurable now — e.g. 131 of 637 sample
days followed a discharge within 3 days. Product `P(discharge) × P(impact |
discharge, t)` is what the dashboard shows for "risk today / next 3 days".

Keep the current direct rain→bacteria model as a champion/challenger baseline
during rollout; the bacteria samples remain the *impact* ground truth.

## Data work before training

1. **Extend rain history to Oct 2016** (`historical_rain.csv` starts Jul 2020;
   ACIS has the earlier years — parameterize the start date in
   `src/collectors/historical.py`). Unlocks 4 more wet seasons: sample-day
   labels 2016-10 → 2020-06 exist in the CSD data already.
2. **Hourly rain features** from Open-Meteo historical (aligned to gauge days).
3. Optional back-extension: `data/csd/sf_csd_events_bayside_legacy_2013_2016.csv`
   gives Bayside occurrence (hours, no volumes) back to 2013 for Stage 1
   occurrence training only.

## Validation (replaces shuffled StratifiedKFold — it leaks adjacent storm days)

- Chronological: train ≤ Jun 2022, validate Jul 2022 – Jun 2024, test
  Jul 2024 – Oct 2025. Or leave-one-wet-season-out CV (9 seasons available).
- Metrics: PR-AUC and Brier per basin against **real events**; report the old
  proxy-trained model on the same split as the baseline to beat.
- `backtest_models()` gets real events instead of bacteria flags. Marquee
  storms that must score high: 2021-10-24 (bomb cyclone, all 7 Westside
  outfalls), 2022-12-31 → 2023-01-16 (New Year's storms), 2023-03-09/21,
  2024-02-04. Verified-zero dry months must stay near 0%.

## Serving changes

- `thresholds.json` → regenerate from the empirical P(CSD | rain) table above.
- Dashboard copy can now say "SFPUC reported discharges on days like this
  N% of the time" — a defensible, sourced claim.
- Simulator (`simulator.py`) can replay actual storm dates against reported
  event volumes.

## v3 ideas (from the "Model Overview to Share" design doc, Aug 2026)

Distilled from the nowcasting design review; ordered by value-for-effort.
The doc's core recommendations that v2 already satisfies: threshold/regime
framing (flat-then-cliff — our empirical curve confirms), antecedent
precipitation index + days-since-rain, time-aware validation, decay/lag
terms, per-system (Westside vs Bayside) feature separation, and assembling
the historical CSD event series (done, better than hoped).

1. **DONE (2026-08-21) — SFPUC's observed CSD flags in the composition.**
   The Supabase watcher (db/migrations/001+004) already polls SFPUC every
   tick: `watcher_state` holds the current ok/posted/cso status per station
   and `alert_log` durably records every observed CSO onset (event_type=
   'cso', station_ids, created_at; exclude simulated=true). For PAST days
   in the persistence composition, map alert-log stations → basins and set
   P(discharge)=1 on observed basin-days; keep model probabilities for
   forecast days and for past days with no observation (absence of a row
   means "not observed," not "no discharge" — don't force to zero).
   Cheapest, highest-value upgrade — the doc is right that
   "hours-since-CSD will always be one of the strongest predictors."
   History accrues from Phase-1 go-live onward (log is all-simulated as of
   Aug 2026; first real wet-season events will flow in automatically).
   cso_events.py retired. Note: alert_log station_ids are NUMERIC LIMS ids
   (4601-4620), mapped in live_dashboard.OBSERVED_STATION_BASIN. The impact
   table is now isotonic-smoothed (weighted PAVA, non-increasing over
   days-since) at load — raw small-n buckets made the decay jagged once
   observed events exposed the curve directly.
2. **DONE (2026-08-21) — per-site-group impact curves.** Fit per group
   (Ocean Beach | Baker-China | Crissy Field | Aquatic Park | Southeast);
   basin display = worst group, per-group values in the payload
   (`predictions.groups`). **CORRECTION (2026-09-02):** the original
   "data reversed the doc's expectation" finding — Baker/China holding
   contamination to day 3 while Ocean Beach cleared — was an artifact of
   mislabeled station ids: that "Baker-China" curve was fit on Mission
   Creek/Crane Cove samples (BAY#220/230). On the corrected registry the
   doc's physical intuition stands: Ocean Beach AND Baker/China (Golden
   Gate flushing) both disperse in ~1-2 days, and it's the Southeast
   group that holds contamination most of a week. Aquatic Park's feared
   bird baseline still doesn't show (4.7%, the lowest).
3. **Stage-2 environmental modifiers: tide, swell, solar/UV.** Discharge
   prediction (stage 1) is hydraulics and stays rain-only; persistence
   (stage 2) is where tide stage (NOAA CO-OPS 9414290), wave height (NDBC
   46026/46237), and insolation (CIMIS/NSRDB) belong — flushing and UV
   inactivation set decay speed. Fold into the impact model as features
   when moving from the empirical table to a small logistic model.
4. **Aquatic Park dry-weather baseline.** Enclosed, poorly flushed cove
   with a bird/boat-driven floor — the doc flags it as the site most
   likely to underperform. Model its non-CSD baseline explicitly (or at
   minimum surface the measured baseline in the UI as background risk).
5. **Persistence-baseline benchmark** (today = yesterday's measured value)
   in eval_report — the field's standard null model (Virtual Beach
   convention); makes our numbers legible to EPA/Surfrider reviewers.
6. **Per-indicator log-concentration targets** (entero / E. coli / total
   coliform as three responses; total coliform is noisiest) once stage 2
   graduates from exceedance table to regression — enables predicted
   MPN/100ml against the Title-17 thresholds instead of binary risk.
7. Rain inputs: Greg's Wunderground PWS list (Outer Sunset, Marina, etc.)
   could give basin-specific real-time rain (collectors/wunderground.py
   already stubs this); SFPUC's 21-gauge network isn't public.

Reference anchors from the doc worth keeping: Hart et al. 2020 (12-h
antecedent rain r=0.57 with entero — supports the planned hourly-intensity
features), Hynes et al. 2024 (outfall-proximity mapping — validates the
per-outfall dataset design), Gonzalez & Noble 2012/2014 (multi-day
antecedent windows; dry-weather exceedances at enclosed sites), PLOS One
2021 review (MLR/PLS/GBM as the accepted toolkit).

## Inference rain source (changed 2026-09-04)

Live dashboard now uses ECMWF IFS (`models=ecmwf_ifs025`) instead of
Open-Meteo `best_match` (GFS/HRRR blend) — like-for-like with the ERA5
training data, and best_match reported 0.0 mm for 2026-09-03 while SF gauges
logged light rain that IFS did forecast. Past hours are overridden with KSFO
gauge observations (`_overlay_observed_rain`), so past days are measured,
not hindcast; the day cards say which (`rain_source`).

Open item: Open-Meteo interpolates IFS's 3-hourly precipitation to hourly,
so rain_max1h/3h from FORECAST hours are smoother than ERA5's native hourly.
Check at the next recalibration: compare the rain_max1h distribution of IFS
forecast days against ERA5 for the same dates; if forecast-day risk runs
systematically low, either scale the intensity features or fit the
calibrator on IFS-forecast inputs.

## Keeping labels fresh

CIWQS exposes monthly SMR attachments ~2–3 months after filing (Nov 2025+
not yet public as of Aug 2026). Re-run `src/collectors/csd_ciwqs/` quarterly
(see its README) and append to `data/csd/sf_csd_events.csv`. The gap that
needs a records request (drafted in `data/csd/records_request_draft.md`):
per-event Westside data 2013–2017.

## v4 (2026-09-05): four basins · regional rain · Poo Bot archive · scorecard

`src/models/train_v4.py` (imports the shared formulas from train_v2.py, which
stays as the reference implementation). Artifacts `data/models/v4/`, promoted
into `data/models/` with `--promote`. Full write-up with the numbers:
`reports/2026-09_forecast_v4.html` (regenerate with `src/models/report_v4.py`).

What changed:

- **Central basin model.** Mission Creek's outfalls (CSD-018..027) are the
  city's largest discharge source and had no model; BAY#220 Mission Creek is
  now served (stage-1 `central_model.pkl`, stage-2 group "Mission Creek").
  The station registry says its basin is Central; the outfall registry
  (`shared/outfalls.py`) is the source of truth for report basin → app basin.
- **Discharge day = bad day.** `x(0,·) ≡ 1` in the composition; the decay
  applies from day 1. One implementation for training and serving:
  `src/models/impact.py` (PAVA smoothing, impact fraction, compose).
- **Regional rain.** Each basin is evaluated on the two-gauge mean and on its
  local NOAA gauge (Westside ← Oceanside 047767; bay basins ← Downtown
  047772); it keeps the local gauge only if the holdout PR-AUC improves by
  ≥0.01 without a Brier regression. Result: North Shore and Central use
  Downtown; Westside and Southeast keep the mean. The model pickle records
  `rain_source`; serving builds one daily frame per source and re-bases
  complete past days onto the ACIS daily totals for those gauges (before,
  observed rain came only from KSFO, 12 miles south of the city).
- **Poo Bot archive** (`src/collectors/poobot_archive.py` → `data/poobot/`):
  SFPUC's getCSV feed, 552 snapshots Mar 2016 – Jan 2017. Gate: the feed
  must show ≥75% of CIWQS Bayside event-days in the window (it shows 87%).
  Used for coverage (the 2016-17 season becomes labelled), stage-2 samples
  (1,097 ENTERO + coliforms, Dec 2015 – Jan 2017) and the "what happened"
  view. Per-basin ablation on the holdout decides whether stage 1 trains on
  archive-labelled days: Westside DROPS them (the feed flags Westside
  structures 1–2 days after the rain — several onsets sit on 0.00" days —
  and the holdout PR-AUC fell 0.828 → 0.748 with them); the bay basins keep
  them.
- **Volume-unknown events** (archive) use the volume head's prediction in
  stage 2 and are excluded from fitting the heads.
- **Scorecard artifact** `scorecard.json.gz`: every day of the span with
  per-basin P (final model and holdout-fit model), composed group and zone
  risk, the labels, reported volume, and bacteria exceedances; plus zone
  confusion matrices on the holdout at 10/25/50%. Feeds `/forecast/api/scorecard`.
- **Geography module** `src/models/groups.py`: basins ↔ groups ↔ zones,
  validated against the registries at import; `shared/zones.py` is the
  canonical zone list (signup imports it too).

Holdout (2023-07 → 2025-10), PR-AUC v3 → v4: Westside 0.828 → 0.828 (avg
rain, CIWQS labels only), North Shore 0.790 → 0.953 (Downtown), Central — →
0.967 (Downtown), Southeast 0.812 → 0.835 (avg), citywide 0.911 → 0.911.

Open items:
- The archive onset dating. A better rule than "morning snapshot → previous
  rainy day" (e.g. snap to the nearest preceding day with ≥0.25") might
  rescue the Westside 2016-17 labels; evaluate against the same ablation.
- Regional inference for today/forecast days is still one series (ECMWF
  0.25° = one grid cell for the whole city); only complete past days differ
  by gauge. Sunset/Bayview/Dogpatch CoCoRaHS gauges exist in ACIS from
  2019–2021 — too short for training, usable as a live sanity check.
- Bacteria recall at Baker & China (~10%) is dry-weather creek runoff, not
  sewage; the forecast is a sewage-risk forecast and the alerts cover the
  rest via the SFPUC posting feed.
