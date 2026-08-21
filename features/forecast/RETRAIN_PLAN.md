# Forecast v2: retraining on ground-truth CSD events

**Status: planned** — data landed 2026-08-21 (`data/csd/`), retrain not yet implemented.

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

## Keeping labels fresh

CIWQS exposes monthly SMR attachments ~2–3 months after filing (Nov 2025+
not yet public as of Aug 2026). Re-run `src/collectors/csd_ciwqs/` quarterly
(see its README) and append to `data/csd/sf_csd_events.csv`. The gap that
needs a records request (drafted in `data/csd/records_request_draft.md`):
per-event Westside data 2013–2017.
