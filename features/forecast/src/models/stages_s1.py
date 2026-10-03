#!/usr/bin/env python3
"""S1 · Rain forecast vs gauges: the stage's rows and scores, the same for every set (P7b).

STAGES_DESIGN.md Part C §3.1 (the S1 contract), §7 ("S1 is set-independent":
data/models/stages/_s1/<model>.csv.gz plus s1_scores.json) and §6 item 10;
STAGES_PROTOCOL.md §4.1 (S1's primary), §4.3 (the deterministic suite), §6
(S1 blocks are ISO weeks; B = 2,000, seed 0, 90% CIs), §7 (S1's rules) and
§8 (S1's pre-registered comparison).

A weather model is an input: on the forecast days its rain stands where the
gauge reading would. So it is graded the way an input is, on how close its
daily rain comes to what the two NOAA gauges then recorded, and a weather
model is chosen on S1 only (design §3.1 owner rule). No set's weights enter
here, so one build serves every set.

**Rows.** One per (day, series, lead) for each model: icon_seamless (the
served one, read from live_dashboard's METEO_PARAMS), ecmwf_ifs025 and
gfs_seamless; series SF Downtown (047772), SF Oceanside (047767) and the
two-gauge mean; leads 0–5, from the first day of ICON's short-lead archive
(2022-11-16) to the last day exclusions' context covers (2026-08-31 on the
committed data). Every scored window ends at the data end the stages share
(stages_entries.data_end, 2026-08-17 at the freeze); the later rows are kept
so weather_models_eval's rows (to 2026-08-27) can be reproduced on them.
``fc_in`` is the day's archived total at that
lead (stages_entries.model_days: NaN unless all 24 hours are archived);
``obs_in`` is truth.gauges() (gauge_outage_v1 on every day, a trace as 0, the
mean over the usable gauges); ``fc_max1h`` / ``fc_max3h`` are the lead's own
peak hours where every hour of the window is archived. ``excl`` and ``tags``
come from exclusions.apply in protocol §7's first-match order — X-S1-MISSING
→ X-S1-OUTAGE → X-S1-NWPGAP → X-S1-NOLEAD, the archive's hours read through
exclusions.Context.nwp (stages_entries.nwp_hours) — and X-POWER is the tag.
The peak columns are not scored: X-S1-PEAK leaves every day out of the peak
table until KSFO's hourly record is committed (counted in the scores).

**Tier.** S1 has no fitted weights, so every day before the freeze is
weights-clean: rows are T1 (T0 after the freeze), never T3. The choice of ICON
(2026-09-30) looked at 2024-02 → 2026-08 (protocol §2 decision log), so a
served-vs-candidate S1 comparison on that window favours the incumbent; the
scores say so.

**Scores** (s1_scores.json; every number through verify, by way of
weather_models_eval's suite so S1 and the weather-model report cannot
disagree):
- by lead × series × model, on that model's scored rows in the Previous Runs
  window (2024-01-20 → the data end, protocol §8): ME, MAE, RMSE, r and
  multiplicative bias on all, observed-wet, forecast-wet and either-wet days
  (≥ 0.1"); POD, FAR, POFD, frequency bias, CSI, ETS, PSS and HSS at 0.1 /
  0.25 / 0.5 / 1.0", SEDI at 1.0"; ISO-week block-bootstrap 90% CIs. Each
  model's own rows give its n_scored, but the three hold different days (ECMWF
  lacks February 2024), so models are set side by side only in ``common``:
  every model on the days all three scored (point scores), the way
  weather_models_eval grades them. Either-wet days still differ by model
  there; that difference is read in ``paired``;
- benchmarks on the served model's rows: persistence (the gauge on D−L−1,
  the last day before the issue day, as that day read it: a 0.00 the
  record through D−L−1 could not yet call an outage stays 0.00) and monthly
  climatology (gauge means 2016-01 → the day before the window), each with
  ``vs_served``, the benchmark − the served model on identical rows;
- the representativeness floor, the S1 oracle: SF Oceanside as a perfect
  point forecast of SF Downtown and the reverse (entry 'oracle', each gauge's
  own and the other gauge's status applied);
- paired differences against the served model on identical rows, every lead
  and series (weather_models_eval.paired_vs: either-wet MAE on days the gauge
  or either model was wet; ΔETS at 0.5"; each with its bootstrap SE and MDE,
  protocol §6), and the protocol §8 primary — lead 1, two-gauge mean,
  either-wet MAE with ETS at 0.5" as companion; a difference counts only when
  its 90% CI excludes 0, and the primary's verdict is Holm-adjusted across the
  two candidate models (protocol §6; the unadjusted verdict is kept beside it);
- by season (two-gauge mean, on the days all three models scored, no CIs);
  ICON's lead 0 from 2022-11-17 (tagged
  optimistic); the exclusion partition per model, series, entry and tier for
  the window and for every row; the archive's reach per (model, lead);
- reproduction: on weather_models_eval's own rows (its window, days every
  model holds), ICON's lead-1 either-wet MAE matches that report's; on ICON's
  own rows it differs, and the block says which extra days make the gap.

Written with --write (gzip mtime 0, so a rebuild on unchanged inputs gives
the same bytes, apart from built_at in the json):
    data/models/stages/_s1/<model>.csv.gz   date, series, lead, model, fc_in, obs_in, fc_max1h, fc_max3h, excl, tags
    data/models/stages/_s1/s1_scores.json   schema, protocol "<version>@<sha>", built_at, inputs {file: sha256}, …

    venv/bin/python features/forecast/src/models/stages_s1.py [--write]
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (REPO, FORECAST, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import exclusions as X  # noqa: E402
import stages_entries as E  # noqa: E402
import truth as T  # noqa: E402
from shared import clock  # noqa: E402
from shared import geography as G  # noqa: E402
from src.collectors import openmeteo_previous_runs as OMP  # noqa: E402
from src.models import verify as V  # noqa: E402  (the MDE beside the ETS companion)
from src.models import weather_models_eval as W  # noqa: E402  (the S1 suite, paired test, blocks and dumps)

SCHEMA = "bwtf.stages.s1/1"
OUT_DIR = FORECAST / "data" / "models" / "stages" / "_s1"
SCORES_JSON = OUT_DIR / "s1_scores.json"
LIVE_DASHBOARD = E.LIVE_DASHBOARD
EVAL_JSON = W.OUT_JSON                                  # weather_models_eval.json, the reproduction reference
MODELS = E.MODELS
SERIES = T.SERIES                                       # SF Downtown, SF Oceanside, avg
GAUGES = T.GAUGE_SERIES
LEADS = E.LEADS
S1_START = OMP.ICON_SHORT_LEAD_FROM                     # 2022-11-16: ICON's short lead reaches back here
PR_START = pd.Timestamp("2024-01-20")                   # protocol §8: the Previous Runs window, "2024-01-20 →"
PRIMARY = {"lead": W.PRIMARY_LEAD, "series": T.MEAN_SERIES, "subset": "either_wet", "metric": "mae",
           "companion": "ets at 0.5 in", "rule": "the 90% CI of ΔMAE (model − served) excludes 0 (protocol §8)",
           "multiplicity": "Holm across the candidate models (protocol §6: simultaneous candidate claims), on the "
                           "two-sided bootstrap p of ΔMAE at α = 0.10, the 90% CI's level"}
ROW_COLUMNS = ("date", "series", "lead", "model", "fc_in", "obs_in", "fc_max1h", "fc_max3h", "excl", "tags")
FLOOR = "floor"                                         # the floor's rows carry this in place of a model
GEO = "sfpuc4_v1"                                       # any geography: S1's facts are its gauges, not basins
MAX_REACH = {"fc_max1h": ("rain_max1h", 0), "fc_max3h": ("rain_max3h", 2)}   # hours each peak window reads before midnight


# ── what is served ──────────────────────────────────────────────────────────

def served_model() -> str:
    """The weather model the live forecast reads (stages_entries.served_weather_model: METEO_PARAMS)."""
    return E.served_weather_model()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_files() -> list:
    return [T.RAIN_CSV, LIVE_DASHBOARD, EVAL_JSON] + [p for m in MODELS for p in (OMP.short_lead_path(m), OMP.prev_runs_path(m))]


# ── rows ────────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def context() -> X.Context:
    """The exclusion rules' context over S1's days: S1_START → the data end exclusions knows."""
    return X.context(G.get(GEO), start=S1_START)


def _tier(dates: pd.DatetimeIndex) -> np.ndarray:
    return np.where(dates > X.freeze_date(), "T0", "T1").astype(object)


def _skeleton(dates: pd.DatetimeIndex, series, entry: str) -> pd.DataFrame:
    """exclusions.skeleton for one series × entry over consecutive ``dates`` (every rows.csv.gz column,
    nothing scored yet), tiered by date."""
    dates = pd.DatetimeIndex(dates)
    if len(dates) != (dates[-1] - dates[0]).days + 1 or not dates.is_monotonic_increasing:
        raise ValueError("S1 rows run over consecutive days")
    f = X.skeleton("S1", [series], dates[0], dates[-1], entry=entry, tier="T1")
    f["tier"] = _tier(pd.DatetimeIndex(f["date"]))
    return f


def rows(model: str, ctx: X.Context | None = None) -> pd.DataFrame:
    """S1 rows for one weather model: ROW_COLUMNS, one per (day, series, lead), sorted by lead, series, date."""
    if model not in MODELS:
        raise KeyError(f"unknown weather model {model!r}; known: {MODELS}")
    ctx = (ctx or context()).with_inputs(nwp=E.nwp_hours(model))
    dates = pd.date_range(ctx.start, ctx.end, name="date")
    obs = T.gauge_rain(ctx.start, ctx.end)
    parts = []
    for L in LEADS:
        days = E.model_days(model, L).set_index("date")
        fc = days["total"].reindex(dates).to_numpy(dtype=float)
        peaks = {}
        for col, (feat, reach) in MAX_REACH.items():
            pk = E.model_peaks(model, L, reach).set_index("date").reindex(dates)
            peaks[col] = pk[feat].where(pk["complete"].fillna(False).astype(bool)).to_numpy(dtype=float)
        for s in SERIES:
            sk = _skeleton(dates, s, f"L{L}")
            sk["p"], sk["y"] = fc, obs[s].to_numpy(dtype=float)
            sk["fc_max1h"], sk["fc_max3h"] = peaks["fc_max1h"], peaks["fc_max3h"]
            parts.append(sk)
    r = pd.concat(parts, ignore_index=True)
    r = X.apply(r, "S1", ctx)
    scored = r["excl"] == ""
    if r.loc[scored, ["p", "y"]].isna().any().any():
        raise AssertionError(f"{model}: a scored S1 row has no forecast or no gauge reading")
    out = pd.DataFrame({"date": r["date"], "series": r["unit"], "lead": r["lead"].astype(int), "model": model,
                        "fc_in": r["p"], "obs_in": r["y"], "fc_max1h": r["fc_max1h"], "fc_max3h": r["fc_max3h"],
                        "excl": r["excl"], "tags": r["tags"]})
    out.attrs["tier"] = sorted(set(r["tier"]))
    return out.sort_values(["lead", "series", "date"], kind="stable").reset_index(drop=True)


def floor_rows(ctx: X.Context | None = None) -> pd.DataFrame:
    """The S1 oracle: each gauge forecast by the other (entry 'oracle'; no row for the mean). Same columns as
    ``rows`` with model 'floor' and lead -1; a row is left out if either gauge is missing or masked."""
    ctx = ctx or context()
    dates = pd.date_range(ctx.start, ctx.end, name="date")
    obs = T.gauge_rain(ctx.start, ctx.end)
    parts = []
    for truth_g, fc_g in ((GAUGES[0], GAUGES[1]), (GAUGES[1], GAUGES[0])):
        sk = _skeleton(dates, truth_g, "oracle")
        sk["p"], sk["y"] = obs[fc_g].to_numpy(dtype=float), obs[truth_g].to_numpy(dtype=float)
        parts.append(sk)
    r = X.apply(pd.concat(parts, ignore_index=True), "S1", ctx)
    return pd.DataFrame({"date": r["date"], "series": r["unit"], "lead": -1, "model": FLOOR, "fc_in": r["p"],
                         "obs_in": r["y"], "fc_max1h": np.nan, "fc_max3h": np.nan, "excl": r["excl"], "tags": r["tags"]})


def _as_x(r: pd.DataFrame) -> pd.DataFrame:
    """S1 rows back in exclusions' columns, for exclusions.counts."""
    lead = r["lead"].astype(int)
    return pd.DataFrame({"stage": "s1", "unit": r["series"], "entry": np.where(lead < 0, "oracle", "L" + lead.astype(str)),
                         "tier": _tier(pd.DatetimeIndex(r["date"])), "excl": r["excl"], "tags": r["tags"], "stratum": ""})


# ── scores ──────────────────────────────────────────────────────────────────

def window_end() -> pd.Timestamp:
    """The scored windows' last day: the data end every stage shares (stages_entries.data_end, 2026-08-17 at
    the freeze; protocol §2). Rows run on to the last day the gauges and archives reach."""
    return E.data_end()


def _in(r: pd.DataFrame, lo, hi) -> pd.DataFrame:
    return r[(r["date"] >= lo) & (r["date"] <= hi)]


def _scored(r: pd.DataFrame, lead: int, series: str, lo=PR_START, hi=None) -> pd.DataFrame:
    """The scored rows (excl '') of one lead × series inside [lo, hi] (default: the Previous Runs window)."""
    hi = window_end() if hi is None else hi
    m = (r["lead"] == lead) & (r["series"] == series) & (r["excl"] == "") & (r["date"] >= lo) & (r["date"] <= hi)
    return r.loc[m].set_index("date").sort_index()


def _suite(sub: pd.DataFrame, fc: str = "fc_in", ci: bool = True) -> dict:
    f, o = sub[fc].to_numpy(dtype=float), sub["obs_in"].to_numpy(dtype=float)
    if not len(sub):
        raise ValueError("an S1 score with no scored rows (empty join)")
    if not (np.isfinite(f).all() and np.isfinite(o).all()):
        raise ValueError("an S1 score's rows hold a NaN forecast or gauge reading")
    return W.suite_ci(f, o, sub.index) if ci else {**W.suite(f, o), "first": str(sub.index.min().date()), "last": str(sub.index.max().date())}


def _paired_vs(obs, fc_a, fc_b, days) -> dict:
    """weather_models_eval.paired_vs (a − b on identical rows; either-wet MAE, ΔETS at 0.5"), with the ETS
    companion's bootstrap SE and MDE added: protocol §6 writes an MDE beside every comparison, and paired_vs
    keeps it for the MAE only. The same seeded resample, so its CI is paired_vs' own (checked)."""
    o, a, b = (np.asarray(x, dtype=float) for x in (obs, fc_a, fc_b))
    out = W.paired_vs(o, a, b, days)
    keep = np.isfinite(o) & np.isfinite(a) & np.isfinite(b)
    de = V.paired_delta(o[keep], a[keep], b[keep], W.iso_week_blocks(pd.DatetimeIndex(days)[keep]), metric=W.ets_loss,
                        n=W.N_BOOT, seed=W.SEED, level=W.LEVEL)
    ets = out["ets_0.5"]
    if not (np.isclose(-de["hi"], ets["lo"], equal_nan=True) and np.isclose(-de["lo"], ets["hi"], equal_nan=True)):
        raise AssertionError("the ETS companion's resample is not paired_vs' own")
    ets.update(se=de["se"], mde=de["mde"])                 # in ETS units: Δ(1 − ETS) = −ΔETS
    return out


def _paired(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Model a vs the served model b on the days both scored (the gauge reading is the same row)."""
    days = a.index.intersection(b.index)
    if not len(days):
        raise ValueError("no day both models scored (empty join)")
    obs = a.loc[days, "obs_in"].to_numpy(dtype=float)
    if not np.array_equal(obs, b.loc[days, "obs_in"].to_numpy(dtype=float)):
        raise AssertionError("two models' rows disagree on the gauge reading")
    out = _paired_vs(obs, a.loc[days, "fc_in"].to_numpy(dtype=float), b.loc[days, "fc_in"].to_numpy(dtype=float), days)
    out["days"] = [str(days.min().date()), str(days.max().date())]
    return out


def _persistence(hist: pd.DataFrame, raw: pd.DataFrame, days: pd.DatetimeIndex, lead: int) -> pd.DataFrame:
    """days × SERIES: each series' gauge reading on I − 1 (I = D − lead, the issue day) as I read it. That is
    S1's truth (masked, a trace as 0, missing as NaN; the mean over the usable gauges), except on the gauge-days
    the record through I − 1 could not yet call an outage (stages_entries.issue_time_unmasked), which read as
    filed: protocol §3, no benchmark uses what was unknown at issue time."""
    one = pd.Timedelta(days=1)
    prev, issue = days - (lead + 1) * one, days - lead * one
    vals = hist[list(GAUGES)].reindex(prev).to_numpy(dtype=float, copy=True)
    cells = E.issue_time_unmasked()
    at = pd.DataFrame({"issue": issue, "date": prev, "row": np.arange(len(days))})
    for row, g in cells.merge(at, on=["issue", "date"])[["row", "gauge"]].itertuples(index=False):
        v = float(raw.at[prev[row], g])
        if not np.isfinite(v):
            raise AssertionError(f"{g} {prev[row].date()}: an outage day with no reading")
        vals[row, GAUGES.index(g)] = 0.0 if v == T.TRACE_IN else v
    out = pd.DataFrame(vals, columns=list(GAUGES), index=days)
    out[T.MEAN_SERIES] = out[list(GAUGES)].mean(axis=1)
    return out[list(SERIES)]


def _benchmarks(served_rows: pd.DataFrame, lead: int, series: str, hist: pd.DataFrame, raw: pd.DataFrame) -> dict:
    """Persistence (the gauge on D − L − 1, as the issue day read it) and monthly climatology (gauge means
    2016-01 → the day before the window), on the served model's scored rows; a persistence day whose gauge is
    unusable is left out, never filled. Each carries ``vs_served``: the benchmark − the served model, paired on
    identical rows (``_paired_vs``; Δ below 0 = the benchmark is better)."""
    sub = _scored(served_rows, lead, series)
    truth = hist[series]
    if truth.index.min() > W.CLIM_FROM:
        raise ValueError(f"the climatology needs gauges from {W.CLIM_FROM.date()}; they start {truth.index.min().date()}")
    pers = _persistence(hist, raw, sub.index, lead)[series].to_numpy(dtype=float)
    clim_src = truth[(truth.index >= W.CLIM_FROM) & (truth.index < PR_START)].dropna()
    clim = clim_src.groupby(clim_src.index.month).mean()
    if len(clim) != 12:
        raise ValueError(f"monthly climatology for {series} lacks months: {sorted(set(range(1, 13)) - set(clim.index))}")
    out = {}
    for name, fc in (("persistence", pers), ("climatology", clim.reindex(sub.index.month).to_numpy(dtype=float))):
        keep = np.isfinite(fc)
        rows = sub.assign(bench=fc)[keep]
        out[name] = {**_suite(rows, fc="bench"),
                     "vs_served": _paired_vs(rows["obs_in"], rows["bench"], rows["fc_in"], rows.index)}
    out["climatology_months"] = [str(clim_src.index.min().date()), str(clim_src.index.max().date())]
    return out


def _two_sided(p_neg: float) -> float:
    """The two-sided bootstrap p of a paired Δ from P(Δ* < 0): the 90% percentile CI excludes 0 exactly when it is < 0.10."""
    return float(min(1.0, 2 * min(p_neg, 1 - p_neg))) if np.isfinite(p_neg) else float("nan")


def _holm(pvals: dict, alpha: float) -> dict:
    """{key: the difference holds} by Holm's step-down over the family: the i-th smallest p must be ≤ α / (m − i)."""
    out, m = {k: False for k in pvals}, len(pvals)
    for i, k in enumerate(sorted(pvals, key=lambda k: (not np.isfinite(pvals[k]), pvals[k]))):
        if not (np.isfinite(pvals[k]) and pvals[k] <= alpha / (m - i)):
            break
        out[k] = True
    return out


def _common(subs: dict) -> pd.DatetimeIndex:
    """The days every model scored (identical rows), checked to hold one gauge reading per day."""
    days = None
    for r in subs.values():
        days = r.index if days is None else days.intersection(r.index)
    if days is None or not len(days):
        raise ValueError("no day every model scored (empty join)")
    obs = [r.loc[days, "obs_in"].to_numpy(dtype=float) for r in subs.values()]
    if not all(np.array_equal(obs[0], o) for o in obs[1:]):
        raise AssertionError("the models' rows disagree on the gauge reading")
    return days


def _reach(model: str) -> dict:
    out = {}
    for L in LEADS:
        d = E.model_days(model, L)
        comp = d.loc[d["complete"], "date"]
        out[str(L)] = {"kind": OMP.LEAD_KIND[L], "first_complete_day": str(comp.min().date()), "last_complete_day": str(comp.max().date()),
                       "incomplete_days": int((~d["complete"]).sum())}
    return out


def reproduction(by_model: dict, served: str) -> dict:
    """The served model's lead-1 either-wet MAE (two-gauge mean) on weather_models_eval's rows — its window,
    the days every model scored — against that report's number; then on S1's own primary rows, with the days
    that make the two row sets differ."""
    ev = json.loads(EVAL_JSON.read_text())
    lo, hi = (pd.Timestamp(d) for d in ev["window"])
    L, s = PRIMARY["lead"], PRIMARY["series"]
    held = {m: _scored(by_model[m], L, s, lo=S1_START, hi=pd.Timestamp.max) for m in MODELS}
    common = held[served].index
    for m in MODELS:
        common = common.intersection(held[m].index)
    common = common[(common >= lo) & (common <= hi)]
    own = _scored(by_model[served], L, s)
    on_eval = W.suite(held[served].loc[common, "fc_in"], held[served].loc[common, "obs_in"])
    on_own = W.suite(own["fc_in"], own["obs_in"])
    ref = ev["by_lead"][str(L)][s]["inputs"][served]
    only_own, only_eval = own.index.difference(common), common.difference(own.index)
    inside = only_own[(only_own >= lo) & (only_own <= hi)]
    return {"metric": "either-wet MAE, lead 1, two-gauge mean", "model": served, "eval_window": ev["window"],
            "eval": {"n": ref["n"], "n_either_wet": ref["n_either_wet"], "mae": ref["continuous"]["either_wet"]["mae"]},
            "on_eval_rows": {"n": on_eval["n"], "n_either_wet": on_eval["n_either_wet"], "mae": on_eval["continuous"]["either_wet"]["mae"]},
            "on_own_rows": {"n": on_own["n"], "n_either_wet": on_own["n_either_wet"], "mae": on_own["continuous"]["either_wet"]["mae"],
                            "first": str(own.index.min().date()), "last": str(own.index.max().date())},
            "matches": bool(on_eval["n"] == ref["n"] and on_eval["n_either_wet"] == ref["n_either_wet"]
                            and abs(on_eval["continuous"]["either_wet"]["mae"] - ref["continuous"]["either_wet"]["mae"]) < 1e-5),
            "why_own_differs": {
                "only_s1_before_the_report_window": int((only_own < lo).sum()),
                "only_s1_another_model_lacks": {m: int(len(inside.difference(held[m].index))) for m in MODELS if m != served},
                "only_report_after_the_data_end": int((only_eval > own.index.max()).sum()),
                "note": "S1 scores each model on every day it holds, 2024-01-20 to the data end; the report scores all "
                        "three on the days all three hold, 2024-02-03 to 2026-08-27"}}


def build(served: str | None = None) -> tuple[dict, dict]:
    """({model: rows}, scores). Every score of s1_scores.json, from the rows."""
    served = served or served_model()
    ctx = context()
    end = window_end()
    if end > ctx.end:
        raise ValueError(f"the data end {end.date()} is past the rows' last day {ctx.end.date()}")
    by_model = {m: rows(m, ctx) for m in MODELS}
    fl = floor_rows(ctx)
    hist = T.gauge_rain(W.CLIM_FROM, ctx.end)            # the benchmarks read the record from 2016-01
    gg = T.gauges(W.CLIM_FROM, ctx.end)
    raw = gg[gg["series"].isin(GAUGES)].pivot(index="date", columns="series", values="raw")[list(GAUGES)]
    res = {"schema": SCHEMA, "protocol": X.protocol_stamp(), "built_at": clock.utc_iso(),
           "inputs": {str(p.relative_to(REPO)): _sha256(p) for p in input_files()},
           "data_end": str(end.date()), "rows": [str(ctx.start.date()), str(ctx.end.date())],
           "served_model": served, "models": list(MODELS), "series": list(SERIES),
           "leads": {str(L): {"kind": OMP.LEAD_KIND[L], "label": W.LEAD_LABEL[L]} for L in LEADS},
           "tier": {"code": "T1", "why": "S1 has no fitted weights: every day before the freeze is weights-clean (T0 after it)",
                    "selection": "the served model (ICON, 2026-09-30) was chosen on 2024-02 → 2026-08 (protocol §2 decision log): "
                                 "a served-vs-candidate S1 comparison on this window favours the incumbent"},
           "windows": {"previous_runs": [str(PR_START.date()), str(end.date())], "lead0_extension": [str(S1_START.date()), str(end.date())]},
           "blocks": "ISO weeks (protocol §6)", "bootstrap": {"n": W.N_BOOT, "seed": W.SEED, "level": W.LEVEL},
           "wet_day_in": W.WET_DAY_IN, "thresholds_in": list(W.RAIN_THRESHOLDS), "sedi_at_in": W.SEDI_AT,
           "primary": dict(PRIMARY), "by_lead": {}, "paired": {}, "by_season": {}, "floor": {}, "lead0_extension": {},
           "exclusions": {}, "peak": {}, "reach": {m: _reach(m) for m in MODELS}}
    for L in LEADS:
        res["by_lead"][str(L)], res["paired"][str(L)] = {}, {}
        for s in SERIES:
            subs = {m: _scored(by_model[m], L, s) for m in MODELS}
            common = _common(subs)
            res["by_lead"][str(L)][s] = {"models": {m: _suite(subs[m]) for m in MODELS},
                                         "common": {"n": int(len(common)), "days": [str(common.min().date()), str(common.max().date())],
                                                    "models": {m: _suite(subs[m].loc[common], ci=False) for m in MODELS}},
                                         "benchmarks": _benchmarks(by_model[served], L, s, hist, raw)}
            res["paired"][str(L)][s] = {m: _paired(subs[m], subs[served]) for m in MODELS if m != served}
    p = res["paired"][str(PRIMARY["lead"])][PRIMARY["series"]]
    p2 = {m: _two_sided(d["mae_either_wet"]["p_neg"]) for m, d in p.items()}
    held = _holm(p2, 1 - W.LEVEL)
    res["primary"]["vs_served"] = {m: {"mae_either_wet": d["mae_either_wet"], "ets_0.5": d["ets_0.5"], "p_two_sided": p2[m],
                                       "verdict": d["mae_either_wet"]["verdict"] if held[m] else "no clear difference",
                                       "verdict_unadjusted": d["mae_either_wet"]["verdict"], "days": d["days"]} for m, d in p.items()}
    for season in sorted({W.season_of(d) for d in pd.date_range(PR_START, end)}):
        blk = {}
        for L in LEADS:
            subs = {m: _scored(by_model[m], L, T.MEAN_SERIES) for m in MODELS}
            common = _common(subs)
            days = common[[W.season_of(d) == season for d in common]]
            if len(days) and int((subs[served].loc[days, "obs_in"] >= W.WET_DAY_IN).sum()):   # a summer stub: nothing to verify
                blk[str(L)] = {m: _suite(subs[m].loc[days], ci=False) for m in MODELS}
        if blk:
            res["by_season"][season] = blk
    for g in GAUGES:
        other = GAUGES[1] if g == GAUGES[0] else GAUGES[0]
        res["floor"][f"{other} as {g}"] = _suite(_scored(fl, -1, g))
    for s in SERIES:
        res["lead0_extension"][s] = {"model": served, "kind": OMP.LEAD_KIND[0], **_suite(_scored(by_model[served], 0, s, lo=S1_START))}
    for m, r in {**by_model, FLOOR: fl}.items():
        res["exclusions"][m] = {"previous_runs": X.counts(_as_x(_in(r, PR_START, end)))["partition"]["s1"],
                                "all_rows": X.counts(_as_x(r))["partition"]["s1"]}
    for m, r in by_model.items():
        res["peak"][m] = peak_partition(_in(r, PR_START, end), ctx)
    res["reproduction"] = reproduction(by_model, served)
    return by_model, res


def post_training(served: str | None = None, model_rows: pd.DataFrame | None = None, fl: pd.DataFrame | None = None,
                  ctx: X.Context | None = None) -> dict:
    """S1's figure cells on post-training days only (protocol §2's T1 dates, 2025-11-01 → the data end), the days
    the other stages' post-training scores cover: the served model at lead 1 on the two-gauge mean (the S1
    primary) and the floor, either-wet MAE with ISO-week CIs as in s1_scores.json. S1 has no fitted weights, so
    these days are no cleaner than the rest of its window; stages_build puts them in the figure's tooltips.
    Reads the written rows (``load_rows``) and rebuilds the floor's unless given."""
    served = served or served_model()
    lo, hi = X.POST_START, window_end()
    r = load_rows(served) if model_rows is None else model_rows
    fl = floor_rows(ctx) if fl is None else fl
    out = {"window": [str(lo.date()), str(hi.date())], "model": served,
           "lead1": _suite(_scored(r, PRIMARY["lead"], PRIMARY["series"], lo=lo, hi=hi)), "floor": {}}
    for g in GAUGES:
        other = GAUGES[1] if g == GAUGES[0] else GAUGES[0]
        out["floor"][f"{other} as {g}"] = _suite(_scored(fl, -1, g, lo=lo, hi=hi))
    return out


def peak_partition(model_rows: pd.DataFrame, ctx: X.Context | None = None) -> dict:
    """The peak table's partition through exclusions itself (table 'peak': S1's order, then X-S1-PEAK)."""
    ctx = (ctx or context()).with_inputs(nwp=E.nwp_hours(model_rows["model"].iloc[0]))
    sk = []
    for (L, s), g in model_rows.groupby(["lead", "series"]):
        f = _skeleton(pd.DatetimeIndex(g["date"]), s, f"L{L}")
        f["p"], f["y"] = g["fc_max1h"].to_numpy(), g["obs_in"].to_numpy()
        sk.append(f)
    r = X.apply(pd.concat(sk, ignore_index=True), "S1", ctx, table="peak")
    return X.counts(r, table="peak")["partition"]["s1"]


# ── writing ─────────────────────────────────────────────────────────────────

def write(by_model: dict, scores: dict) -> list:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for m, r in by_model.items():
        path = OUT_DIR / f"{m}.csv.gz"
        f = r[list(ROW_COLUMNS)].copy()
        f["date"] = pd.DatetimeIndex(f["date"]).strftime("%Y-%m-%d")
        f.to_csv(path, index=False, float_format="%.6g", compression={"method": "gzip", "mtime": 0})
        out.append(path)
    SCORES_JSON.write_text(W.dumps(scores))
    out.append(SCORES_JSON)
    return out


def load_rows(model: str) -> pd.DataFrame:
    """A written <model>.csv.gz back as rows (date parsed; empty excl / tags as '')."""
    if model not in MODELS:
        raise KeyError(f"unknown weather model {model!r}; known: {MODELS}")
    r = pd.read_csv(OUT_DIR / f"{model}.csv.gz", parse_dates=["date"], keep_default_na=True)
    for c in ("excl", "tags"):
        r[c] = r[c].fillna("").astype(str)
    return r


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help=f"write {OUT_DIR.relative_to(REPO)}/<model>.csv.gz and s1_scores.json")
    a = ap.parse_args(argv)
    by_model, sc = build()
    L, s = str(PRIMARY["lead"]), PRIMARY["series"]
    print(f"S1 rows {sc['rows'][0]} → {sc['rows'][1]}; window {sc['windows']['previous_runs']}; served {sc['served_model']}")
    for lead in map(str, LEADS):
        c = sc["by_lead"][lead][s]["common"]               # side by side only on the days all three scored
        b = c["models"]
        print(f"lead {lead} ({c['n']} common days): " + "; ".join(
            f"{m} MAE {b[m]['continuous']['either_wet']['mae']:.3f} (n {b[m]['n_either_wet']}) "
            f"ETS0.5 {W.f2(b[m]['thresholds']['0.5']['ets'])}" for m in MODELS))
    for m, d in sc["primary"]["vs_served"].items():
        dm = d["mae_either_wet"]
        print(f"primary {m} − {sc['served_model']}: ΔMAE {dm['delta']:+.4f} [{dm['lo']:+.4f}, {dm['hi']:+.4f}] {d['verdict']} "
              f"(Holm; unadjusted {d['verdict_unadjusted']}, p {d['p_two_sided']:.3f}); ΔETS0.5 {d['ets_0.5']['delta']:+.3f} {d['ets_0.5']['verdict']}")
    for k, v in sc["floor"].items():
        print(f"floor {k}: either-wet MAE {v['continuous']['either_wet']['mae']:.3f}, CSI0.5 {W.f2(v['thresholds']['0.5']['csi'])}")
    rp = sc["reproduction"]
    print(f"reproduction: report {rp['eval']['mae']:.5f} (n {rp['eval']['n']}); on its rows {rp['on_eval_rows']['mae']:.5f} "
          f"(n {rp['on_eval_rows']['n']}) → {'match' if rp['matches'] else 'NO MATCH'}; own rows {rp['on_own_rows']['mae']:.5f} (n {rp['on_own_rows']['n']})")
    if a.write:
        for p in write(by_model, sc):
            print(f"wrote {p.relative_to(REPO)}")


if __name__ == "__main__":
    main()
