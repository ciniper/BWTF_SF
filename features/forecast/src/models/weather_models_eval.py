#!/usr/bin/env python3
"""S1 · Rain forecast vs gauges: which weather model stands in for the gauges, and how good is it at each lead?

The stage 1 models were trained on the rain that fell: two NOAA gauges (SF
Downtown 047772, SF Oceanside 047767) for the daily totals, ERA5 hourly for the
peak intensities. A weather model is an INPUT: on the forecast days its rain is
fed where the gauge reading would go. So a weather model is graded here the way
an input should be — on how close its rain for a day comes to what the gauges
then recorded — and is chosen on that alone (STAGES_DESIGN.md §3.1).

Leads (src/collectors/openmeteo_previous_runs.py says exactly what each is):
  lead 0   Open-Meteo's Historical Forecast archive: for every hour, the newest
           run covering it, stitched — the first ≈0–6 hours of each run. A
           SHORT LEAD, fresher than anything the page shows past the next few
           hours, so its scores are OPTIMISTIC. (The 2026-09-29 report graded
           this archive and called it a "day-0 / day-1 lead"; it is neither.)
  lead L   Open-Meteo's Previous Runs archive, ``precipitation_previous_dayL``,
           L = 1…5: the value predicted 24·L hours before each hour, so a daily
           total at lead L is the forecast for D as it stood on D−L. Lead 1 is
           the page's Tomorrow row; the S1 primary.
ICON, ECMWF IFS and GFS hold leads 1–5 from Jan/Feb 2024; ICON's lead 0 is
cached back to 2022-11-16 and scored on its own, tagged optimistic.

Truth is the ACIS daily total per gauge, with ``gauge_outage_v1`` applied on
every day (a dead gauge reads 0.00), a trace as 0 and a missing day as missing
— never filled from the other gauge for the per-gauge series. The two-gauge
mean is the mean of the gauges that are usable that day. A model-day with fewer
than 24 archived hours is left out, never summed as dry (X-S1-NWPGAP: ECMWF's
Feb 2024 gap used to read as 14 dry days). Within a lead, every input is scored
on the same days, so differences are paired.

Scores (shared verification library, src/models/verify.py; JWGFVR names):
ME, MAE, RMSE, r and multiplicative bias on all days, observed-wet,
forecast-wet and either-wet days (≥ 0.1"); POD, FAR, POFD, frequency bias,
CSI, ETS, Peirce (PSS) and HSS at 0.1 / 0.25 / 0.5 / 1.0"; SEDI at 1.0". By
lead × gauge series × season, with ISO-week block-bootstrap 90% CIs and paired
differences against the served model. Primary: MAE on either-wet days, lead 1,
two-gauge mean, with ETS at 0.5" as companion; a difference whose CI includes
0 is "no clear difference". Benchmarks: persistence (the last gauge day before
the issue day), monthly climatology, ERA5 (a reanalysis, reference only), and
the representativeness floor — one gauge as a perfect point forecast of the
other — outage-masked and unmasked.

Appendix: the served stage 1 fed each input's rain at a lead, scored with the
Brier skill score against a training-fold climatology, and paired against the
same model fed the gauges (rain known), on post-training days only (the
weights saw every earlier day: X-ALL-INSAMPLE) and on one row set for every
arm. It shows the error an input passes downstream; it never picks the model. Nothing here weighs misses against
false alarms or picks a line (Chase, 2026-10-01: fixed public risk levels).

Outputs (the json is a report artifact, not a served file):
    features/forecast/data/models/weather_models_eval.json
    reports/2026-09_weather_models.html

Usage
    venv/bin/python features/forecast/src/collectors/openmeteo_previous_runs.py --fetch   # refresh the archives
    venv/bin/python features/forecast/src/models/weather_models_eval.py
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for p in (str(REPO), str(FORECAST), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import leaderboard  # noqa: E402,F401  (weights pipelines reference leaderboard.add_hinges)
from shared.clock import now_pacific  # noqa: E402
from src.collectors import openmeteo_previous_runs as OMP  # noqa: E402
from src.collectors.csd_labels import build_daily_labels  # noqa: E402
from src.models import candidates  # noqa: E402
from src.models import verify as V  # noqa: E402
from src.models.groups import BASIN_KEYS  # noqa: E402
from src.models.rain_features import (  # noqa: E402
    DAILY_FEATURES, INTENSITY_FEATURES, GAUGE_OUTAGE_RULE, add_daily_features, hourly_intensity, mask_gauge_outages,
)

_main = sys.modules.get("__main__")
if _main is not None and not hasattr(_main, "add_hinges"):
    _main.add_hinges = leaderboard.add_hinges

RAW_DIR = FORECAST / "data" / "raw"
MODEL_DIR = FORECAST / "data" / "models"
OUT_JSON = MODEL_DIR / "weather_models_eval.json"
OUT_HTML = REPO / "reports" / "2026-09_weather_models.html"

WX_MODELS = {"ecmwf_ifs025": "ECMWF IFS", "gfs_seamless": "GFS", "icon_seamless": "ICON"}
SERVED_WX = "icon_seamless"   # served since 2026-09-30 (ECMWF IFS 2026-09-04 → 09-30)
INPUTS = {**WX_MODELS, "mean3": "mean of the three"}          # the candidate inputs
BENCHMARKS = {"persistence": "persistence (last gauge day before issue)", "climatology": "monthly climatology"}
REFERENCE = {"era5": "ERA5 reanalysis (training-time hourly source)"}
LABEL = {**INPUTS, **BENCHMARKS, **REFERENCE}
LEADS = OMP.LEADS                      # 0 (short lead, optimistic), 1…5 (fixed)
PRIMARY_LEAD = 1
LEAD_LABEL = {0: "lead 0 (short, optimistic)", **{L: f"lead {L}" for L in OMP.FIXED_LEADS}}
WET_DAY_IN = 0.1                       # a day at or above this is "wet"
RAIN_THRESHOLDS = (0.1, 0.25, 0.5, 1.0)
SEDI_AT = 1.0                          # SEDI is for rare events: reported at the 1" threshold only
SUBSETS = ("all", "obs_wet", "fc_wet", "either_wet")
SUBSET_LABEL = {"all": "all days", "obs_wet": "gauge wet", "fc_wet": "model wet", "either_wet": "either wet"}
CONT = ("me", "mae", "rmse", "r", "mbias")
CAT = ("pod", "far", "pofd", "fbi", "csi", "ets", "pss", "hss")
DT, OC = "SF Downtown", "SF Oceanside"
GAUGES = ["avg", DT, OC]
GAUGE_LABEL = {DT: "SF Downtown (047772)", OC: "SF Oceanside (047767)", "avg": "two-gauge mean"}
TRACE_IN = 0.001                       # how the ACIS collector stores 'T'; scored as 0
CLIM_FROM = pd.Timestamp("2016-01-01")
LABELS_FROM = pd.Timestamp("2016-10-01")
POST_START = pd.Timestamp("2025-11-01")  # first day no served weight saw
ICON_LEAD0_FROM = OMP.ICON_SHORT_LEAD_FROM
TOP_DAYS = 25
APPENDIX_ARMS = [(k, L) for k in INPUTS for L in (0, 1)] + [(SERVED_WX, L) for L in (2, 3, 4, 5)]
N_BOOT, SEED, LEVEL = 2000, 0, 0.9

BASINS = list(BASIN_KEYS.values())     # westside, north_shore, central, southeast
BASIN_NAME = {v: k for k, v in BASIN_KEYS.items()}


# ── truth ───────────────────────────────────────────────────────────────────

def load_gauges() -> dict:
    """The gauge record as S1 truth.

    ``gauge_outage_v1`` runs on the raw record exactly as serving runs it, on
    every day; then a trace scores as 0. Per gauge: NaN = missing (X-S1-MISSING)
    or masked (X-S1-OUTAGE), never filled from the other gauge. The two-gauge
    mean averages the gauges usable that day.
    """
    rain = pd.read_csv(RAW_DIR / "historical_rain.csv", parse_dates=["date"])
    rp = rain.pivot_table(index="date", columns="rain_station_name", values="precip_inches", aggfunc="first").sort_index()[[DT, OC]]
    tmp, runs = mask_gauge_outages(rp.reset_index(), gauges=(DT, OC))
    masked = tmp.set_index("date")[[DT, OC]]
    days = pd.date_range(rp.index.min(), rp.index.max(), name="date")
    rp, masked = rp.reindex(days), masked.reindex(days)
    status = pd.DataFrame("ok", index=days, columns=[DT, OC])
    status[rp.isna()] = "missing"
    status[masked.isna() & rp.notna()] = "outage"

    def series(f: pd.DataFrame) -> dict:
        f = f.mask(f == TRACE_IN, 0.0)
        return {DT: f[DT], OC: f[OC], "avg": f[[DT, OC]].mean(axis=1)}

    st_avg = pd.Series(np.where((status == "ok").any(axis=1), "ok", np.where((status == "missing").any(axis=1), "missing", "outage")), index=days)
    return {"truth": series(masked), "truth_unmasked": series(rp), "runs": runs,
            "status": {DT: status[DT], OC: status[OC], "avg": st_avg}}


def served_gauge_daily(input_rules: list | None) -> pd.DataFrame:
    """date × {SF Downtown, SF Oceanside, avg}: the gauge record as serving and
    post-training rescoring read it (the outage rule applied, a missing gauge
    day taking the other gauge, then zero). The stage 1 input in the appendix,
    and the "as served" floor (the design's preliminary 0.184" / 0.73)."""
    rain = pd.read_csv(RAW_DIR / "historical_rain.csv", parse_dates=["date"])
    rp = rain.pivot_table(index="date", columns="rain_station_name", values="precip_inches", aggfunc="first").sort_index()
    cols = list(rp.columns)
    if input_rules and GAUGE_OUTAGE_RULE["name"] in input_rules:
        tmp, _ = mask_gauge_outages(rp.reset_index(), gauges=tuple(cols))
        rp = tmp.set_index("date")[cols]
    out = pd.DataFrame(index=rp.index)
    out["avg"] = rp[cols].mean(axis=1).fillna(0.0)
    for c in cols:
        other = [o for o in cols if o != c][0]
        out[c] = rp[c].fillna(rp[other]).fillna(0.0)
    return out


def season_of(d: pd.Timestamp) -> str:
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def iso_week_blocks(dates) -> np.ndarray:
    """S1's bootstrap blocks: ISO weeks (§5.4), so a storm's days resample together."""
    iso = pd.DatetimeIndex(dates).isocalendar()
    return (iso["year"].astype(int) * 100 + iso["week"].astype(int)).to_numpy()


# ── forecasts ───────────────────────────────────────────────────────────────

def load_forecasts() -> dict:
    """model → {daily: date × lead (NaN = fewer than 24 hours), span: lead → (first, last) archived day, hourly}.

    mean3 is the hour-by-hour mean of the three models, defined only where all
    three are (a two-model mean is a different input).
    """
    fc = {}
    for m in WX_MODELS:
        daily, span, hourly = {}, {}, {}
        for L in LEADS:
            h = OMP.load_hourly(m, L)
            daily[L], span[L], hourly[L] = OMP.daily_totals(h)["total"], OMP.archive_span(h), h
        fc[m] = {"daily": pd.DataFrame(daily).sort_index(), "span": span, "hourly": hourly}
    hourly3, daily3 = {}, {}
    for L in LEADS:
        hh = pd.concat([fc[m]["hourly"][L].set_index("timestamp")["precip_inches"].rename(m) for m in WX_MODELS], axis=1, join="inner")
        h3 = hh.mean(axis=1, skipna=False).rename("precip_inches").reset_index()
        hourly3[L], daily3[L] = h3, OMP.daily_totals(h3)["total"]
    fc["mean3"] = {"daily": pd.DataFrame(daily3).sort_index(), "hourly": hourly3}
    return fc


def model_day_status(fc: dict, m: str, L: int, dates: pd.DatetimeIndex) -> pd.Series:
    """ok / nolead (outside the archived span, X-S1-NOLEAD) / gap (inside it, < 24 hours, X-S1-NWPGAP)."""
    lo, hi = fc[m]["span"][L]
    d = fc[m]["daily"][L].reindex(dates)
    st = pd.Series(np.where(d.notna(), "ok", "gap"), index=dates)
    if lo is None:
        st[:] = "nolead"
    else:
        st[(dates < lo) | (dates > hi)] = "nolead"
    return st


def era5_daily() -> tuple:
    h = pd.read_csv(RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"])
    return OMP.daily_totals(h)["total"], hourly_intensity(h).set_index("date")


# ── scores (every number through verify.py) ─────────────────────────────────

def suite(fc, obs) -> dict:
    """The S1 deterministic suite for one input × truth: continuous scores on the four subsets, the 2×2 at each threshold."""
    f, o = np.asarray(fc, float), np.asarray(obs, float)
    keep = np.isfinite(f) & np.isfinite(o)
    f, o = f[keep], o[keep]
    masks = V.wet_masks(f, o, WET_DAY_IN)
    cat = {}
    for t in RAIN_THRESHOLDS:
        ct = V.contingency_at(f, o, t)
        if t != SEDI_AT:
            ct.pop("sedi")
        cat[f"{t:g}"] = ct
    return {"n": int(len(f)), **{f"n_{s}": int(masks[s].sum()) for s in SUBSETS[1:]},
            "total_fc_in": float(f.sum()), "total_obs_in": float(o.sum()),
            "continuous": {s: V.continuous(f, o, wet=masks[s]) for s in SUBSETS}, "thresholds": cat}


def _flat(s: dict) -> list:
    return ([s["continuous"][sub][k] for sub in SUBSETS for k in CONT]
            + [s["thresholds"][f"{t:g}"][k] for t in RAIN_THRESHOLDS for k in CAT] + [s["thresholds"][f"{SEDI_AT:g}"]["sedi"]])


def suite_ci(fc, obs, dates) -> dict:
    """``suite`` plus ISO-week block-bootstrap 90% CIs on every score (one resample for all of them)."""
    f, o, d = np.asarray(fc, float), np.asarray(obs, float), pd.DatetimeIndex(dates)
    keep = np.isfinite(f) & np.isfinite(o)
    f, o, d = f[keep], o[keep], d[keep]
    s = suite(f, o)
    blocks = iso_week_blocks(d)
    _, lo, hi = V.block_bootstrap(lambda a, b: _flat(suite(a, b)), (f, o), blocks, n=N_BOOT, seed=SEED, level=LEVEL)
    lo, hi = np.atleast_1d(lo), np.atleast_1d(hi)
    it = iter(zip(lo, hi))
    ci = {"level": LEVEL, "n_blocks": int(len(np.unique(blocks))), "continuous": {}, "thresholds": {}}
    for sub in SUBSETS:
        ci["continuous"][sub] = {k: list(next(it)) for k in CONT}
    for t in RAIN_THRESHOLDS:
        ci["thresholds"][f"{t:g}"] = {k: list(next(it)) for k in CAT}
    ci["thresholds"][f"{SEDI_AT:g}"]["sedi"] = list(next(it))
    s["ci"] = ci
    s["first"], s["last"] = (str(d.min().date()), str(d.max().date())) if len(d) else (None, None)
    return s


def mae(y, p) -> float:
    return V.continuous(p, y)["mae"]


def ets_loss(y, p) -> float:
    """1 − ETS at 0.5", so a paired difference reads like a loss (below 0 = the first arm is better)."""
    return 1.0 - V.contingency_at(p, y, 0.5)["ets"]


def paired_vs(obs, fc_a, fc_b, dates) -> dict:
    """Input a vs input b (the served model) on identical days.

    MAE on days where the gauge or either input was wet (one subset for both
    arms, so the difference is paired); ETS at 0.5" on all days, reported as
    ΔETS (above 0 = a is better). ISO-week blocks; 90% CIs; verdict words.
    """
    o, a, b = (np.asarray(x, float) for x in (obs, fc_a, fc_b))
    d = pd.DatetimeIndex(dates)
    keep = np.isfinite(o) & np.isfinite(a) & np.isfinite(b)
    o, a, b, d = o[keep], a[keep], b[keep], d[keep]
    blocks = iso_week_blocks(d)
    wet = (o >= WET_DAY_IN) | (a >= WET_DAY_IN) | (b >= WET_DAY_IN)
    dm = V.paired_delta(o[wet], a[wet], b[wet], blocks[wet], metric=mae, n=N_BOOT, seed=SEED, level=LEVEL)
    de = V.paired_delta(o, a, b, blocks, metric=ets_loss, n=N_BOOT, seed=SEED, level=LEVEL)
    ets = {"n": de["n"], "n_blocks": de["n_blocks"], "a": 1 - de["a"], "b": 1 - de["b"], "delta": -de["delta"],
           "lo": -de["hi"], "hi": -de["lo"], "p_better": de["p_neg"], "verdict": de["verdict"], "level": LEVEL}
    return {"mae_either_wet": dm, "ets_0.5": ets}


def floor_table(g: dict, start, end, ci: bool = True) -> dict:
    """The representativeness floor: one gauge as a perfect point forecast of the other.

    No one-point forecast for the city can be expected to beat this against a
    single gauge. Variants: ``masked`` (outage rule on every day, masked and
    missing days left out: the floor), ``unmasked`` (the raw record, dead-gauge
    zeros included) and ``as_served`` (the served gauge record: a masked or
    missing day takes the other gauge, so those days agree by construction;
    the design's preliminary 0.184" / 0.73 at 0.5").
    """
    days = pd.date_range(pd.Timestamp(start), pd.Timestamp(end))
    sv = served_gauge_daily([GAUGE_OUTAGE_RULE["name"]]).reindex(days)
    src = {"masked": g["truth"], "unmasked": g["truth_unmasked"], "as_served": {DT: sv[DT], OC: sv[OC]}}
    out = {}
    for variant, s in src.items():
        out[variant] = {}
        for truth_g, fc_g in ((DT, OC), (OC, DT)):
            t, f = s[truth_g].reindex(days), s[fc_g].reindex(days)
            out[variant][f"{fc_g} as {truth_g}"] = (suite_ci if ci else lambda a, b, _d: suite(a, b))(f.to_numpy(), t.to_numpy(), days)
    return out


# ── appendix: the served stage 1 fed each input ─────────────────────────────

def scenario_rows(gauge: pd.Series, dates: pd.DatetimeIndex, daily_by_lead: pd.DataFrame | None, lead: int,
                  inten: pd.DataFrame) -> pd.DataFrame:
    """Features for each D as the page builds them ``lead`` days ahead.

    The gauge record through D−lead−1, then the input's forecast for D−lead … D
    (day D−lead+j at lead j), and D's peak hours from the input's lead-``lead``
    hours. ``daily_by_lead`` None = rain known: the gauges every day, ERA5
    peaks (how the weights were trained). A row with any forecast day missing
    is NaN (never filled).
    """
    idx = {d: i for i, d in enumerate(gauge.index)}
    vals = gauge.to_numpy(dtype=float)
    rows = []
    for d in dates:
        i = idx[d]
        s = vals[max(0, i - 31):i + 1].copy()
        ok = True
        if daily_by_lead is not None:
            for j in range(lead + 1):
                v = daily_by_lead[j].get(d - pd.Timedelta(days=lead - j), np.nan)
                if not np.isfinite(v):
                    ok = False
                    break
                s[len(s) - 1 - (lead - j)] = v
        row = {"date": d}
        if ok:
            f = add_daily_features(pd.DataFrame({"precip_inches": s})).iloc[-1]
            row.update({k: float(f[k]) for k in DAILY_FEATURES})
            for k in INTENSITY_FEATURES:
                v = inten.at[d, k] if d in inten.index else np.nan
                row[k] = float(v) if pd.notna(v) else (0.0 if daily_by_lead is None else np.nan)
        rows.append(row)
    return pd.DataFrame(rows).set_index("date").reindex(columns=DAILY_FEATURES + INTENSITY_FEATURES)


def load_served_stage1() -> dict:
    out = {}
    for key in BASINS:
        with open(MODEL_DIR / f"{key}_model.pkl", "rb") as f:
            out[key] = pickle.load(f)
    return out


def predict(md: dict, feats: pd.DataFrame) -> np.ndarray:
    """The serving rule (live_dashboard._predict_calibrated); NaN rows stay NaN."""
    p = np.full(len(feats), np.nan)
    ok = feats.notna().all(axis=1).to_numpy()
    if ok.any():
        x = feats[ok]
        raw = md["model"].predict_proba(x[md["features"]])[:, 1]
        factor = np.maximum(0.0, 1.0 - x["rain_3d_cum"].to_numpy() * 2.0)
        p[ok] = np.clip(raw - md.get("calibration_offset", 0.0) * factor, 0.0, 1.0)
    return p


def stage1_appendix(fc: dict, dates: pd.DatetimeIndex, rain_for_blocks: pd.Series, served: dict, sv_gauges: pd.DataFrame,
                    era5_inten: pd.DataFrame) -> dict:
    """BSS of the served stage 1 per basin fed each input at a lead, and ΔBS against the same model fed the gauges.

    Post-training days only (from POST_START): the served weights were fit
    through 2025-10-31, so on every earlier day they had seen the answer — an
    in-sample score is never emitted (X-ALL-INSAMPLE, design §4.3, Part B 1),
    only counted. Every arm is scored on the same basin-days (those where
    every arm has a complete forecast), so a BSS reads across inputs as well
    as against rain known (Part B 16). The reference is the base rate of the
    weights' own training span, per basin × calendar month (±1 month).
    """
    stage1 = load_served_stage1()
    sources = served.get("rain_sources") or {}
    labels = build_daily_labels().set_index("date")
    dates = dates[dates.isin(labels.index)]
    insample = {b: int((labels.reindex(dates[dates < POST_START])[f"{BASIN_NAME[b]}_covered"].fillna(0).astype(int) == 1).sum()) for b in BASINS}
    dates = dates[dates >= POST_START]
    inten = {(k, L): hourly_intensity(fc[k]["hourly"][L]).set_index("date") for k, L in APPENDIX_ARMS}
    feats = {}
    for src in sorted({stage1[b].get("rain_source", sources.get(b, "avg")) for b in BASINS}):
        g = sv_gauges[src]
        feats[(src, "gauges")] = scenario_rows(g, dates, None, 0, era5_inten)
        for k, L in APPENDIX_ARMS:
            feats[(src, (k, L))] = scenario_rows(g, dates, fc[k]["daily"], L, inten[(k, L)])
    # training-fold climatology per basin × calendar month (±1 month), from the labelled days the weights were fit on
    y_tr, b_tr, m_tr = [], [], []
    for b in BASINS:
        lab = labels[(labels.index >= LABELS_FROM) & (labels.index < POST_START)]
        cov = lab[f"{BASIN_NAME[b]}_covered"].fillna(0).astype(int) == 1
        y_tr += list(lab.loc[cov, f"{BASIN_NAME[b]}_csd"].fillna(0).astype(int))
        b_tr += [b] * int(cov.sum())
        m_tr += list(lab.index[cov].month)
    arms = ["gauges"] + APPENDIX_ARMS
    rows = {a: {"y": [], "p": [], "b": [], "d": []} for a in arms}
    per_basin = {}
    for b in BASINS:
        src = stage1[b].get("rain_source", sources.get(b, "avg"))
        lab = labels.reindex(dates)
        cov = (lab[f"{BASIN_NAME[b]}_covered"].fillna(0).astype(int) == 1).to_numpy()
        y = lab[f"{BASIN_NAME[b]}_csd"].fillna(0).astype(int).to_numpy()[cov]
        dd = dates[cov]
        for a in arms:
            p = predict(stage1[b], feats[(src, a)])[cov]
            rows[a]["y"].append(y); rows[a]["p"].append(p); rows[a]["b"] += [b] * len(y); rows[a]["d"].append(dd)
        per_basin[b] = {"rain_source": src, "covered_days": int(cov.sum()), "events": int(y.sum())}
    cat = {a: {"y": np.concatenate(rows[a]["y"]), "p": np.concatenate(rows[a]["p"]), "b": np.array(rows[a]["b"]),
               "d": pd.DatetimeIndex(np.concatenate([x.to_numpy() for x in rows[a]["d"]]))} for a in arms}
    base = cat["gauges"]
    ok = np.logical_and.reduce([np.isfinite(cat[a]["p"]) for a in arms])      # one row set for every arm
    y, bb, dd = base["y"][ok], base["b"][ok], base["d"][ok]
    ref = V.climatology_ref(y_tr, (b_tr, m_tr), (bb, dd.month))
    blocks = V.storm_blocks(dd, rain_for_blocks.reindex(dd).to_numpy())
    for b in BASINS:
        m = bb == b
        per_basin[b].update(scored_days=int(m.sum()), scored_events=int(y[m].sum()), few_events=bool(y[m].sum() < 10))
    out = {"arms": [], "basins": per_basin, "window": [str(dates[0].date()), str(dates[-1].date())],
           "n": int(ok.sum()), "events": int(y.sum()), "n_blocks": int(len(np.unique(blocks))),
           "excluded": {"n_total": int(len(ok)) + sum(insample.values()), "X-ALL-INSAMPLE": sum(insample.values()),
                        "no_forecast": int((~ok).sum()), "n_scored": int(ok.sum())},
           "reference": "climatology per basin × calendar month (±1 month) from the labelled days the weights were fit on (2016-10 → 2025-10)",
           "blocks": "storms from the two-gauge mean (padded 1 day before, 7 after), ISO weeks between"}
    for a in arms:
        p = cat[a]["p"][ok]
        rec = {"arm": "gauges" if a == "gauges" else {"input": a[0], "lead": a[1]},
               "n": int(len(y)), "events": int(y.sum()),
               "brier": V.brier(y, p), "bss": V.bss(y, p, ref), "logs": V.log_score(y, p)[0], "per_basin": {}}
        if a != "gauges":
            rec["delta"] = V.paired_delta(y, p, base["p"][ok], blocks, n=N_BOOT, seed=SEED, level=LEVEL)
        for b in BASINS:
            m = bb == b
            rec["per_basin"][b] = {"n": int(m.sum()), "events": int(y[m].sum()), "brier": V.brier(y[m], p[m]),
                                   "bss": V.bss(y[m], p[m], ref[m])}
        out["arms"].append(rec)
    return out


# ── main ────────────────────────────────────────────────────────────────────

def legacy_verification(s: dict) -> dict:
    """The 2026-09 field names (export_how_it_works reads wet_mae_in and thresholds['0.5'].csi)."""
    c, w = s["continuous"]["all"], s["continuous"]["obs_wet"]
    return {"n_days": s["n"], "wet_days": s["n_obs_wet"], "bias_in": c["me"], "mae_in": c["mae"], "rmse_in": c["rmse"], "r": c["r"],
            "wet_bias_in": w["me"], "wet_mae_in": w["mae"], "wet_rmse_in": w["rmse"], "wet_r": w["r"],
            "total_in": {"gauge": s["total_obs_in"], "model": s["total_fc_in"]},
            "thresholds": {k: {"gauge_days": t["a"] + t["c"], "model_days": t["a"] + t["b"], "hit": t["a"], "miss": t["c"], "false": t["b"],
                               "pod": t["pod"], "far": t["far"], "csi": t["csi"], "freq_bias": t["fbi"]} for k, t in s["thresholds"].items()}}


def run() -> dict:
    served = candidates.SERVED
    input_rules = list(served.get("input_rules_post") or [])
    g = load_gauges()
    fc = load_forecasts()
    e5_daily, e5_inten = era5_daily()
    labels_end = build_daily_labels()["date"].max()
    end = min(g["truth"]["avg"].last_valid_index(), labels_end, e5_daily.last_valid_index(),
              min(fc[m]["span"][0][1] for m in WX_MODELS))
    # each lead's days: those every model archived in full at that lead (identical rows within a lead)
    window_days = pd.date_range(min(max(fc[m]["daily"][L].first_valid_index() for m in WX_MODELS) for L in LEADS), end)
    start = window_days[0]
    status = {(m, L): model_day_status(fc, m, L, window_days) for m in WX_MODELS for L in LEADS}
    fc_ok = {L: np.logical_and.reduce([(status[(m, L)] == "ok").to_numpy() for m in WX_MODELS]) for L in LEADS}
    clim_src = {s: g["truth"][s][(g["truth"][s].index >= CLIM_FROM) & (g["truth"][s].index < start)] for s in GAUGES}
    clim = {s: clim_src[s].groupby(clim_src[s].index.month).mean() for s in GAUGES}
    seasons = pd.Series([season_of(d) for d in window_days], index=window_days)

    result = {
        "schema": "bwtf.s1_weather_models/2", "generated": now_pacific().strftime("%Y-%m-%d %H:%M"),
        "window": [str(start.date()), str(end.date())],
        "served": {"name": served["name"], "stage1": served["stage1"], "line": served.get("line"), "weather_model": SERVED_WX},
        "input_rules": input_rules, "rain_sources": served.get("rain_sources") or {}, "wet_day_in": WET_DAY_IN,
        "archive_first_day": {m: str(fc[m]["daily"][0].first_valid_index().date()) for m in WX_MODELS},
        "leads": {str(L): {"kind": OMP.LEAD_KIND[L], "label": LEAD_LABEL[L],
                           "first_day_all_models": str(window_days[fc_ok[L]].min().date()) if fc_ok[L].any() else None} for L in LEADS},
        "archive": {m: {str(L): {"first_complete_day": str(fc[m]["daily"][L].first_valid_index().date()),
                                 "nolead_days_in_window": int((status[(m, L)] == "nolead").sum()),
                                 "gap_days_in_window": int((status[(m, L)] == "gap").sum())} for L in LEADS} for m in WX_MODELS},
        "primary": {"lead": PRIMARY_LEAD, "series": "avg", "subset": "either_wet", "metric": "mae", "companion": "ets at 0.5 in"},
        "gauge_days": {}, "verification": {}, "by_lead": {}, "paired": {}, "by_season": {}, "floor": {}, "exclusions": {},
        "icon_lead0_since_2022": {}, "top_days": [], "intensity": {}, "appendix": {},
    }

    def inputs_at(L: int, days: pd.DatetimeIndex) -> dict:
        return {k: fc[k]["daily"][L].reindex(days) for k in INPUTS}

    # 1. by lead × series: every input on identical days; benchmarks and ERA5 beside them
    for L in LEADS:
        result["by_lead"][str(L)], result["paired"][str(L)], result["exclusions"][str(L)] = {}, {}, {}
        for s in GAUGES:
            st = g["status"][s].reindex(window_days).fillna("missing")
            any_nolead = np.logical_or.reduce([(status[(m, L)] == "nolead").to_numpy() for m in WX_MODELS])
            any_gap = np.logical_or.reduce([(status[(m, L)] == "gap").to_numpy() for m in WX_MODELS])
            ex = {"n_total": int(len(window_days)), "X-S1-MISSING": int((st == "missing").sum()),
                  "X-S1-OUTAGE": int((st == "outage").sum())}
            ok_truth = (st == "ok").to_numpy()
            ex["X-S1-NOLEAD"] = int((ok_truth & any_nolead).sum())
            ex["X-S1-NWPGAP"] = int((ok_truth & ~any_nolead & any_gap).sum())
            scored = ok_truth & fc_ok[L]
            ex["n_scored"] = int(scored.sum())
            assert ex["n_total"] == ex["n_scored"] + sum(v for k, v in ex.items() if k.startswith("X-")), ex
            result["exclusions"][str(L)][s] = ex
            days = window_days[scored]
            truth = g["truth"][s].reindex(days)
            ins = inputs_at(L, days)
            blk = {"n_days": int(len(days)), "rows": [str(days.min().date()), str(days.max().date())] if len(days) else None,
                   "inputs": {k: suite_ci(v.to_numpy(), truth.to_numpy(), days) for k, v in ins.items()},
                   "benchmarks": {"persistence": suite(g["truth"][s].shift(L + 1).reindex(days).to_numpy(), truth.to_numpy()),
                                  "climatology": suite(np.array([clim[s].get(d.month, np.nan) for d in days]), truth.to_numpy())},
                   "reference": {"era5": suite(e5_daily.reindex(days).to_numpy(), truth.to_numpy())}}
            result["by_lead"][str(L)][s] = blk
            result["paired"][str(L)][s] = {k: paired_vs(truth.to_numpy(), ins[k].to_numpy(), ins[SERVED_WX].to_numpy(), days)
                                           for k in INPUTS if k != SERVED_WX}
            if L == 0:   # the 2026-09 field names, kept for export_how_it_works (lead 0, gauge-wet days)
                result["verification"][s] = {**{k: legacy_verification(v) for k, v in blk["inputs"].items()},
                                             "era5": legacy_verification(blk["reference"]["era5"])}
                result["gauge_days"][s] = {"wet_days": int((truth >= WET_DAY_IN).sum()),
                                           **{f"ge_{t}": int((truth >= t).sum()) for t in RAIN_THRESHOLDS}}
    result["verification_note"] = "legacy fields: lead 0 (short lead, optimistic), gauge-wet days; the S1 scores are in by_lead"

    # 2. by season, two-gauge mean
    for season in sorted(seasons.unique()):
        result["by_season"][season] = {}
        for L in LEADS:
            sel = (seasons == season).to_numpy() & (g["status"]["avg"].reindex(window_days).fillna("missing") == "ok").to_numpy() & fc_ok[L]
            days = window_days[sel]
            truth = g["truth"]["avg"].reindex(days)
            if int((truth >= WET_DAY_IN).sum()) == 0:     # a summer stub: nothing to verify
                continue
            result["by_season"][season][str(L)] = {"n_days": int(len(days)), "wet_days": int((truth >= WET_DAY_IN).sum()),
                                                   "gauge_total_in": float(truth.sum()),
                                                   "inputs": {k: suite_ci(v.to_numpy(), truth.to_numpy(), days) for k, v in inputs_at(L, days).items()}}
        if not result["by_season"][season]:
            del result["by_season"][season]

    # 3. ICON lead 0 back to 2022-11-16, alone (tagged optimistic)
    icon0 = fc[SERVED_WX]["daily"][0]
    ext_days = pd.date_range(ICON_LEAD0_FROM, end)
    blk = {"kind": OMP.LEAD_KIND[0], "window": [str(ext_days[0].date()), str(end.date())], "series": {}, "by_season": {}}
    for s in GAUGES:
        sel = (g["status"][s].reindex(ext_days).fillna("missing") == "ok").to_numpy() & icon0.reindex(ext_days).notna().to_numpy()
        days = ext_days[sel]
        blk["series"][s] = suite_ci(icon0.reindex(days).to_numpy(), g["truth"][s].reindex(days).to_numpy(), days)
        if s == "avg":
            for season in sorted({season_of(d) for d in days}):
                dd = days[[season_of(d) == season for d in days]]
                t = g["truth"]["avg"].reindex(dd)
                if int((t >= WET_DAY_IN).sum()):
                    blk["by_season"][season] = suite_ci(icon0.reindex(dd).to_numpy(), t.to_numpy(), dd)
    result["icon_lead0_since_2022"] = blk

    # 4. the representativeness floor, over the window
    result["floor"] = {"window": result["window"], **floor_table(g, start, end)}

    # 5. the biggest gauge days, every input beside them (lead 1; ICON's lead 0 too)
    avg = g["truth"]["avg"].reindex(window_days)
    for d in avg.sort_values(ascending=False).head(TOP_DAYS).index.sort_values():
        row = {"date": str(d.date()), "season": season_of(d), "downtown_in": g["truth"][DT].get(d), "oceanside_in": g["truth"][OC].get(d),
               "mean_in": avg.get(d), "lead1": {k: fc[k]["daily"][PRIMARY_LEAD].get(d) for k in INPUTS},
               "icon_lead0": icon0.get(d), "era5": e5_daily.get(d)}
        result["top_days"].append(row)

    # 6. peak intensity on gauge-wet days vs ERA5 (a reference: no hourly gauge record, X-S1-PEAK)
    wet = window_days[(avg >= WET_DAY_IN).to_numpy()]
    for m in WX_MODELS:
        result["intensity"][m] = {}
        for L in (0, PRIMARY_LEAD):
            mi = hourly_intensity(fc[m]["hourly"][L]).set_index("date").reindex(wet)
            complete = fc[m]["daily"][L].reindex(wet).notna().to_numpy()
            ei = e5_inten.reindex(wet)
            blk = {}
            for k in ("rain_max1h", "rain_max3h"):
                f, o = mi[k].to_numpy()[complete], ei[k].to_numpy()[complete]
                c = V.continuous(f, o)
                blk[k] = {"n": c["n"], "bias_in": c["me"], "mae_in": c["mae"], "mbias": c["mbias"],
                          "era5_mean_in": float(np.nanmean(o)), "model_mean_in": float(np.nanmean(f))}
            result["intensity"][m][str(L)] = blk

    # 7. appendix: the served stage 1 fed each input (BSS; ΔBS vs rain known)
    result["appendix"] = stage1_appendix(fc, window_days, g["truth"]["avg"], served, served_gauge_daily(input_rules), e5_inten)
    return V.clean(result)


# ── report ──────────────────────────────────────────────────────────────────

def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def f2(v, signed=False, nd=2):
    if v is None:
        return "—"
    return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"


def pc(v):
    return "—" if v is None else f"{100*v:.0f}%"


def ci(v, lohi, nd=2, signed=False, unit=""):
    """'0.21" [0.18, 0.25]': the estimate (with its unit) and its 90% interval."""
    if v is None:
        return "—"
    lo, hi = (lohi or [None, None])
    rng = f" <span class='small'>[{f2(lo, signed, nd)}, {f2(hi, signed, nd)}]</span>" if lo is not None and hi is not None else ""
    return f"{f2(v, signed, nd)}{unit}{rng}"


IN = '"'


def words(v: str) -> str:
    cls = {"better": "ok", "worse": "warn", "no clear difference": "info"}.get(v, "info")
    return f"<span class='badge {cls}'>{esc(v)}</span>"


def build_html(r: dict) -> str:
    css = (HERE / "stage2_explorer_template.html").read_text().split("<style>")[1].split("</style>")[0]
    P = str(PRIMARY_LEAD)
    bl = r["by_lead"]
    prim = bl[P]["avg"]
    pin = prim["inputs"]
    pmae = lambda k, L=P, s="avg": bl[L][s]["inputs"][k]["continuous"]["either_wet"]["mae"]  # noqa: E731
    rank = sorted(INPUTS, key=lambda k: pmae(k))
    pair = r["paired"][P]["avg"]
    fl = r["floor"]["masked"]
    fl_dt = fl[f"{OC} as {DT}"]
    better = [k for k in pair if pair[k]["mae_either_wet"]["verdict"] == "better"]
    worse = [k for k in pair if pair[k]["mae_either_wet"]["verdict"] == "worse"]
    l0 = bl["0"]["avg"]["inputs"][SERVED_WX]["continuous"]["either_wet"]["mae"]
    t5 = lambda k: pin[k]["thresholds"]["0.5"]  # noqa: E731
    verdict = (
        f"For tomorrow (lead 1), against the two-gauge mean on the {pin[SERVED_WX]['n']} days of {esc(r['window'][0])} → {esc(r['window'][1])} "
        f"every model archived in full, the closest input on the days the gauges or the input called rain is the <b>{esc(LABEL[rank[0]])}</b>: "
        f"mean absolute error {ci(pmae(rank[0]), pin[rank[0]]['ci']['continuous']['either_wet']['mae'], unit=IN)}. Then "
        + "; ".join(f"{esc(LABEL[k])} {ci(pmae(k), pin[k]['ci']['continuous']['either_wet']['mae'], unit=IN)}" for k in rank[1:])
        + ". Against ICON (served since 2026-09-30) on identical days: "
        + "; ".join(f"{esc(LABEL[k])} {f2(pair[k]['mae_either_wet']['delta'], True, 3)}\" [{f2(pair[k]['mae_either_wet']['lo'], True, 3)}, {f2(pair[k]['mae_either_wet']['hi'], True, 3)}] {words(pair[k]['mae_either_wet']['verdict'])}" for k in pair)
        + ". At the half-inch mark (ETS, the companion score): " + "; ".join(f"{esc(LABEL[k])} {f2(t5(k)['ets'])} (frequency bias {f2(t5(k)['fbi'])})" for k in INPUTS)
        + ". "
        + (f"{' and '.join(esc(LABEL[k]) for k in better)} {'is' if len(better) == 1 else 'are'} closer than ICON beyond the noise. " if better else
           "No input is closer than ICON beyond the noise, so ICON stays: a weather model is chosen on this stage alone. ")
        + (f"{' and '.join(esc(LABEL[k]) for k in worse)} {'is' if len(worse) == 1 else 'are'} farther. " if worse else "")
        + f"ICON was chosen on part of this window (a mild winner's curse). The floor is graded against one gauge, so read it beside a single-gauge score: "
        f"Oceanside as a perfect forecast of Downtown misses by {ci(fl_dt['continuous']['either_wet']['mae'], fl_dt['ci']['continuous']['either_wet']['mae'], unit=IN)} "
        f"on the days either gauge was wet, against ICON's {f2(pmae(SERVED_WX, P, DT))}\" for Downtown at lead 1, so much of a one-point forecast's miss is the gap between the gauges themselves. "
        f"The lead-0 archive the 2026-09 report graded is optimistic: ICON's error there is {f2(l0)}\" against {f2(pmae(SERVED_WX))}\" at lead 1.")

    def lead_table(metric_path, title, nd=2, signed=False, show_ci=True, extra=("persistence", "era5")) -> str:
        """Inputs × leads. Benchmarks only where they compare: monthly climatology is "wet" every winter
        day, so its either-wet subset and its threshold calls mean something else; it joins all-days rows only."""
        sub, key = metric_path
        rows = [f"<h3>{esc(title)}</h3><table><tr><th>input</th>" + "".join(f"<th class='num'>{esc(LEAD_LABEL[L])}</th>" for L in LEADS) + "</tr>"]
        for k in list(INPUTS) + list(extra):
            cls = " class='best'" if k == SERVED_WX else (" class='ref'" if k in REFERENCE or k in BENCHMARKS else "")
            cells = []
            for L in LEADS:
                blk = bl[str(L)]["avg"]
                s = blk["inputs"].get(k) or blk["benchmarks"].get(k) or blk["reference"].get(k)
                if sub in SUBSETS:
                    v = s["continuous"][sub][key]
                    lohi = s.get("ci", {}).get("continuous", {}).get(sub, {}).get(key) if show_ci else None
                else:
                    v = s["thresholds"][sub][key]
                    lohi = s.get("ci", {}).get("thresholds", {}).get(sub, {}).get(key) if show_ci else None
                cells.append(f"<td class='num'>{ci(v, lohi, nd, signed)}</td>")
            rows.append(f"<tr{cls}><td>{esc(LABEL[k])}</td>" + "".join(cells) + "</tr>")
        n = "".join(f"<td class='num small'>{bl[str(L)]['avg']['n_days']} days, {bl[str(L)]['avg']['inputs'][SERVED_WX]['n_either_wet']} either-wet</td>" for L in LEADS)
        rows.append(f"<tr><td class='small'>days scored</td>{n}</tr></table>")
        return "".join(rows)

    def full_tables(s: str, L: str) -> str:
        blk = bl[L][s]
        out = [f"<h3>{esc(GAUGE_LABEL[s])} · {esc(LEAD_LABEL[int(L)])} · {blk['n_days']} days</h3>",
               "<table><tr><th>input</th><th>days</th><th class='num'>n</th><th class='num'>ME (in)</th><th class='num'>MAE (in)</th><th class='num'>RMSE (in)</th><th class='num'>r</th><th class='num'>× bias</th></tr>"]
        for k in INPUTS:
            st = blk["inputs"][k]
            for sub in SUBSETS:
                c, cc = st["continuous"][sub], st["ci"]["continuous"][sub]
                cls = " class='best'" if k == SERVED_WX else ""
                out.append(f"<tr{cls}><td>{esc(LABEL[k]) if sub == 'all' else ''}</td><td>{esc(SUBSET_LABEL[sub])}</td><td class='num'>{c['n']}</td>"
                           f"<td class='num'>{ci(c['me'], cc['me'], 3, True)}</td><td class='num'>{ci(c['mae'], cc['mae'], 3)}</td><td class='num'>{ci(c['rmse'], cc['rmse'], 3)}</td>"
                           f"<td class='num'>{ci(c['r'], cc['r'])}</td><td class='num'>{ci(c['mbias'], cc['mbias'])}</td></tr>")
        out.append("</table><table><tr><th>input</th><th class='num'>threshold</th><th class='num'>hits / misses / false</th><th class='num'>POD</th><th class='num'>FAR</th><th class='num'>POFD</th><th class='num'>freq. bias</th><th class='num'>CSI</th><th class='num'>ETS</th><th class='num'>PSS</th><th class='num'>HSS</th><th class='num'>SEDI</th></tr>")
        for k in INPUTS:
            st = blk["inputs"][k]
            for t in RAIN_THRESHOLDS:
                c, cc = st["thresholds"][f"{t:g}"], st["ci"]["thresholds"][f"{t:g}"]
                cls = " class='best'" if k == SERVED_WX else ""
                sedi = ci(c.get("sedi"), cc.get("sedi")) if t == SEDI_AT else ""
                out.append(f"<tr{cls}><td>{esc(LABEL[k]) if t == RAIN_THRESHOLDS[0] else ''}</td><td class='num'>≥ {t:g}\"</td><td class='num'>{c['a']} / {c['c']} / {c['b']}</td>"
                           + "".join(f"<td class='num'>{ci(c[m], cc[m])}</td>" for m in CAT) + f"<td class='num'>{sedi}</td></tr>")
        out.append("</table>")
        return "".join(out)

    def compact(s: str, L: str) -> str:
        blk = bl[L][s]
        out = [f"<h3>{esc(GAUGE_LABEL[s])} · {esc(LEAD_LABEL[int(L)])} · {blk['n_days']} days</h3><table><tr><th>input</th><th class='num'>either-wet days</th><th class='num'>MAE, either wet</th><th class='num'>ME, either wet</th><th class='num'>× bias</th><th class='num'>CSI ≥ 0.5\"</th><th class='num'>ETS ≥ 0.5\"</th><th class='num'>freq. bias ≥ 0.5\"</th><th class='num'>POD / FAR ≥ 0.5\"</th></tr>"]
        for k in INPUTS:
            st = blk["inputs"][k]
            c, cc, t, tc = st["continuous"]["either_wet"], st["ci"]["continuous"]["either_wet"], st["thresholds"]["0.5"], st["ci"]["thresholds"]["0.5"]
            cls = " class='best'" if k == SERVED_WX else ""
            out.append(f"<tr{cls}><td>{esc(LABEL[k])}</td><td class='num'>{c['n']}</td><td class='num'>{ci(c['mae'], cc['mae'], 3)}</td><td class='num'>{ci(c['me'], cc['me'], 3, True)}</td>"
                       f"<td class='num'>{ci(st['continuous']['all']['mbias'], st['ci']['continuous']['all']['mbias'])}</td><td class='num'>{ci(t['csi'], tc['csi'])}</td><td class='num'>{ci(t['ets'], tc['ets'])}</td>"
                       f"<td class='num'>{ci(t['fbi'], tc['fbi'])}</td><td class='num'>{pc(t['pod'])} / {pc(t['far'])}</td></tr>")
        out.append("</table>")
        return "".join(out)

    win = f"{esc(r['window'][0])} → {esc(r['window'][1])}"
    first_days = "; ".join(f"{WX_MODELS[m]} lead 1 {r['archive'][m]['1']['first_complete_day']}, lead 0 {r['archive'][m]['0']['first_complete_day']}" for m in WX_MODELS)
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Which weather model stands in for the gauges</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css}
tr.best td{{background:#e0f0ea}} tr.ref td{{color:#8a8d9b;font-style:italic}} .verdict{{background:#0072BC;color:#fff;border-radius:16px;padding:16px 20px;font-size:16px}} .verdict b{{font-size:18px}}
.verdict .badge{{background:rgba(255,255,255,.18);color:#fff}} .verdict .small{{color:#e3ebf2}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}} .small{{font-size:12px;color:#54576F}} td.big{{font-weight:700}} .scroll{{overflow-x:auto}}
</style></head><body><div class="wrap">
<header><h1>Which weather model stands in for the gauges</h1>
<p class="sub">Stage 1 of the forecast: how much rain, and when. The stage 1 models were trained on the rain that fell — the two NOAA gauges for daily totals, ERA5 hourly for peak intensity. A weather model is an <b>input</b>: on the forecast days its rain is fed where the gauge reading would go, so it is graded as an input — how close its rain for a day comes to what the gauges then recorded — and chosen on that alone. Graded at fixed leads: <b>lead 1</b> is the forecast for a day as it stood the day before (the page's Tomorrow row), lead 5 five days before. <b>Lead 0</b> is the archive the 2026-09 version of this page graded: each run's first few hours stitched together, fresher than anything the page shows past the next few hours, so it flatters every model.</p>
<div class="meta"><span>window {win}</span><span>first archived days: {esc(first_days)}</span><span>wet day = ≥ {WET_DAY_IN}"</span><span>gauges: outage rule on every day, missing days left out</span><span>90% CIs, ISO-week blocks</span><span>generated {esc(r['generated'])}</span></div></header>
<nav><a href="#verdict">Verdict</a><a href="#leads">By lead</a><a href="#differences">Against ICON</a><a href="#gauges">Lead 1 in full</a><a href="#floor">The floor</a><a href="#seasons">By season</a><a href="#icon2022">ICON since 2022</a><a href="#storms">The biggest days</a><a href="#intensity">Peak intensity</a><a href="#appendix">Through stage 1</a><a href="#leftout">Left out</a><a href="#caveats">Caveats</a></nav>
<section id="verdict"><div class="verdict">{verdict}</div>
<p class="lead">How to read the tables. <b>ME</b> (bias) is input minus gauge, in inches (positive = the model is wetter); <b>MAE</b> is the average size of the miss; <b>RMSE</b> weighs the big misses more; <b>r</b> is the correlation; <b>× bias</b> is the input's total over the gauge's (1 = right on). Each is computed on all days, on days the gauge read {WET_DAY_IN}" or more (<i>gauge wet</i>), on days the input said so (<i>model wet</i>) and on days either did (<i>either wet</i>, the headline: grading only the gauge's wet days would not count a model's rain on dry days against it). At each threshold: <b>POD</b> = the share of the gauge's days the input also called; <b>FAR</b> = the share of the input's calls that were false; <b>POFD</b> = the share of the gauge's dry days the input called wet; <b>frequency bias</b> = how many days the input called over how many the gauge saw (above 1 = calls it too often); <b>CSI</b> = hits ÷ (hits + misses + false alarms); <b>ETS</b> = CSI with the hits a random forecast would get taken out; <b>PSS</b> = POD − POFD; <b>HSS</b> = proportion correct beyond chance; <b>SEDI</b> = a skill score that stays fair for rare events (1"). Brackets are 90% intervals from resampling whole weeks. Lower MAE and higher ETS are better.</p></section>
"""]

    parts.append(f'<section id="leads"><h2>By lead, two-gauge mean</h2><p class="lead">Within a column every input is scored on the same days: days every model archived in full at that lead. Green = ICON, the served model. Grey italic rows are benchmarks: persistence (the gauge\'s last full day before the issue day), ERA5 (a reanalysis, not a forecast, so it does not depend on the lead) and, on all-days rows only, the monthly mean daily rain of 2016 → {esc(r["window"][0])}. On either-wet days each input is graded on its own wet days, so a column compares like with like only through the paired differences below.</p><div class="scroll">')
    parts.append(lead_table(("either_wet", "mae"), "Mean absolute error on either-wet days (in) — the primary score", 3))
    parts.append(lead_table(("0.5", "ets"), "Equitable threat score at 0.5\" — the companion score"))
    parts.append(lead_table(("0.5", "fbi"), "Frequency bias at 0.5\" (1 = calls half-inch days as often as they happen)"))
    parts.append(lead_table(("0.5", "csi"), "Critical success index at 0.5\""))
    parts.append(lead_table(("all", "mae"), "Mean absolute error on all days (in)", 3, extra=("persistence", "climatology", "era5")))
    parts.append(lead_table(("all", "mbias"), "Total rain, input ÷ gauge (multiplicative bias, all days)"))
    parts.append("</div></section>")

    # paired differences
    parts.append('<section id="differences"><h2>Against ICON, on identical days</h2><p class="lead">Each input minus ICON. MAE is compared on days where the gauge or either of the two inputs was wet, so both are graded on the same days; ETS on all days (above 0 = the input is better). "better" or "worse" only when the whole 90% interval is on one side of 0; the smallest detectable MAE difference is shown beside it.</p><div class="scroll"><table><tr><th>lead</th><th>series</th><th>input</th><th class="num">days</th><th class="num">ΔMAE (in)</th><th>MAE verdict</th><th class="num">detectable</th><th class="num">ΔETS ≥ 0.5"</th><th>ETS verdict</th></tr>')
    for L in LEADS:
        for s in GAUGES:
            for k, d in r["paired"][str(L)][s].items():
                dm, de = d["mae_either_wet"], d["ets_0.5"]
                parts.append(f"<tr><td>{esc(LEAD_LABEL[L])}</td><td>{esc(GAUGE_LABEL[s])}</td><td>{esc(LABEL[k])}</td><td class='num'>{dm['n']}</td>"
                             f"<td class='num'>{f2(dm['delta'], True, 3)} <span class='small'>[{f2(dm['lo'], True, 3)}, {f2(dm['hi'], True, 3)}]</span></td><td>{words(dm['verdict'])}</td><td class='num'>{f2(dm['mde'], False, 3)}</td>"
                             f"<td class='num'>{f2(de['delta'], True)} <span class='small'>[{f2(de['lo'], True)}, {f2(de['hi'], True)}]</span></td><td>{words(de['verdict'])}</td></tr>")
    parts.append("</table></div></section>")

    parts.append(f'<section id="gauges"><h2>Lead 1 in full</h2><p class="lead">The full suite at the primary lead. Westside stage 1 reads the two-gauge mean; the bay basins read Downtown alone. The same one-point model value is fed to both, so a model is graded against all three. Lead 0 and leads 2–5 are in the json beside this page.</p><div class="scroll">{full_tables("avg", P)}{compact(DT, P)}{compact(OC, P)}</div></section>')

    # floor
    parts.append('<section id="floor"><h2>The floor: one gauge as a forecast of the other</h2><p class="lead">The two gauges are 8 km apart. Using one as a perfect point forecast of the other shows how far apart two points of the city are on the same day: a single value for the whole city carries that spread into every single-gauge score, so read the floor beside the Downtown and Oceanside tables, not the two-gauge mean. <b>masked</b> is the floor (the outage rule on every day, masked and missing days left out); <b>unmasked</b> keeps the dead-gauge zeros; <b>as served</b> is the gauge record serving reads, where a masked or missing day takes the other gauge — those days agree by construction, which is how the design\'s first estimate (0.184", CSI 0.73) came out lower.</p><table><tr><th>variant</th><th>forecast → truth</th><th class="num">days</th><th class="num">MAE, gauge wet</th><th class="num">MAE, either wet</th><th class="num">ME, either wet</th><th class="num">CSI ≥ 0.5"</th><th class="num">ETS ≥ 0.5"</th><th class="num">freq. bias ≥ 0.5"</th></tr>')
    for variant in ("masked", "unmasked", "as_served"):
        for name, st in r["floor"][variant].items():
            c, cc = st["continuous"], st["ci"]["continuous"]
            t, tc = st["thresholds"]["0.5"], st["ci"]["thresholds"]["0.5"]
            parts.append(f"<tr{' class=best' if variant == 'masked' else ''}><td>{esc(variant.replace('_', ' '))}</td><td>{esc(name)}</td><td class='num'>{st['n']}</td><td class='num'>{ci(c['obs_wet']['mae'], cc['obs_wet']['mae'], 3)}</td>"
                         f"<td class='num'>{ci(c['either_wet']['mae'], cc['either_wet']['mae'], 3)}</td><td class='num'>{ci(c['either_wet']['me'], cc['either_wet']['me'], 3, True)}</td>"
                         f"<td class='num'>{ci(t['csi'], tc['csi'])}</td><td class='num'>{ci(t['ets'], tc['ets'])}</td><td class='num'>{ci(t['fbi'], tc['fbi'])}</td></tr>")
    parts.append("</table></section>")

    # seasons
    parts.append('<section id="seasons"><h2>By season, two-gauge mean</h2><p class="lead">Whether the ranking holds from one winter to the next (seasons run July to June). Per season, lead 1 and lead 0. Within a season, every input on the same days.</p><div class="scroll"><table><tr><th>season</th><th>lead</th><th class="num">days / wet</th><th class="num">gauge total (in)</th>'
                 + "".join(f"<th class='num'>{esc(LABEL[k])}: MAE either wet · ETS ≥ 0.5\" · freq. bias</th>" for k in INPUTS) + "</tr>")
    for season, leads in r["by_season"].items():
        for L in (P, "0"):
            if L not in leads:
                continue
            b = leads[L]
            cells = "".join(f"<td class='num'>{ci(b['inputs'][k]['continuous']['either_wet']['mae'], b['inputs'][k]['ci']['continuous']['either_wet']['mae'], 3)} · {f2(b['inputs'][k]['thresholds']['0.5']['ets'])} · {f2(b['inputs'][k]['thresholds']['0.5']['fbi'])}</td>" for k in INPUTS)
            parts.append(f"<tr><td>{esc(season)}</td><td>{esc(LEAD_LABEL[int(L)])}</td><td class='num'>{b['n_days']} / {b['wet_days']}</td><td class='num'>{b['gauge_total_in']:.1f}</td>{cells}</tr>")
    parts.append("</table></div></section>")

    # ICON lead 0 since 2022
    ic = r["icon_lead0_since_2022"]
    parts.append(f'<section id="icon2022"><h2>ICON since 2022, lead 0 (optimistic)</h2><p class="lead">ICON\'s short-lead archive reaches back to {esc(ic["window"][0])}, a season further than the fixed leads. Lead 0 only, so read it as an upper bound on skill; it is never compared with the fixed leads as if it were one.</p><table><tr><th>truth</th><th>days</th><th class="num">days / wet / ≥ 0.5"</th><th class="num">MAE, either wet</th><th class="num">ME, either wet</th><th class="num">CSI ≥ 0.5"</th><th class="num">ETS ≥ 0.5"</th><th class="num">freq. bias ≥ 0.5"</th></tr>')
    rows_ic = [(GAUGE_LABEL[s], f"{st['first']} → {st['last']}", st) for s, st in ic["series"].items()] + [(f"two-gauge mean, {season}", f"{st['first']} → {st['last']}", st) for season, st in ic["by_season"].items()]
    for name, span, st in rows_ic:
        c, cc, t, tc = st["continuous"]["either_wet"], st["ci"]["continuous"]["either_wet"], st["thresholds"]["0.5"], st["ci"]["thresholds"]["0.5"]
        parts.append(f"<tr><td>{esc(name)}</td><td>{esc(span)}</td><td class='num'>{st['n']} / {st['n_obs_wet']} / {t['a'] + t['c']}</td><td class='num'>{ci(c['mae'], cc['mae'], 3)}</td><td class='num'>{ci(c['me'], cc['me'], 3, True)}</td>"
                     f"<td class='num'>{ci(t['csi'], tc['csi'])}</td><td class='num'>{ci(t['ets'], tc['ets'])}</td><td class='num'>{ci(t['fbi'], tc['fbi'])}</td></tr>")
    parts.append("</table></section>")

    # top days
    parts.append(f'<section id="storms"><h2>The {TOP_DAYS} biggest gauge days</h2><p class="lead">Ranked by the two-gauge mean, in date order. Each input\'s rain at lead 1 (the day before), ICON\'s lead 0 and ERA5 beside the gauges; bold marks a value within a quarter inch of the gauge mean.</p><div class="scroll"><table><tr><th>date</th><th>season</th><th class="num">Downtown</th><th class="num">Oceanside</th><th class="num">mean</th>'
                 + "".join(f"<th class='num'>{esc(LABEL[k])}, lead 1</th>" for k in INPUTS) + "<th class='num'>ICON, lead 0</th><th class='num'>ERA5</th></tr>")
    for row in r["top_days"]:
        vals = [row["lead1"][k] for k in INPUTS] + [row["icon_lead0"], row["era5"]]
        m = row["mean_in"]
        parts.append(f"<tr><td>{row['date']}</td><td>{row['season']}</td><td class='num'>{f2(row['downtown_in'])}</td><td class='num'>{f2(row['oceanside_in'])}</td><td class='num'>{f2(m)}</td>"
                     + "".join(("<td class='num'>—</td>" if v is None else f"<td class='num{' big' if m is not None and abs(v - m) <= 0.25 else ''}'>{v:.2f}</td>") for v in vals) + "</tr>")
    parts.append("</table></div></section>")

    # intensity
    parts.append('<section id="intensity"><h2>Peak intensity</h2><p class="lead">Three of the 19 features are the day\'s peak 1-, 3- and 6-hour rain. They were trained on ERA5 hourly, so each model\'s peaks on gauge-wet days are compared with ERA5\'s for the same days. A reference, not a gauge check: no hourly gauge record for the city is committed yet (X-S1-PEAK). ECMWF\'s precipitation is three-hourly, spread to hourly by Open-Meteo; GFS and ICON are hourly natively.</p><table><tr><th>model</th><th>lead</th><th class="num">wet days</th><th class="num">peak 1h: model / ERA5 mean (in)</th><th class="num">bias / MAE</th><th class="num">peak 3h: model / ERA5 mean (in)</th><th class="num">bias / MAE</th></tr>')
    for m in WX_MODELS:
        for L, b in r["intensity"][m].items():
            parts.append(f"<tr><td>{esc(WX_MODELS[m])}</td><td>{esc(LEAD_LABEL[int(L)])}</td><td class='num'>{b['rain_max1h']['n']}</td><td class='num'>{f2(b['rain_max1h']['model_mean_in'], nd=3)} / {f2(b['rain_max1h']['era5_mean_in'], nd=3)}</td><td class='num'>{f2(b['rain_max1h']['bias_in'], True, 3)} / {f2(b['rain_max1h']['mae_in'], nd=3)}</td>"
                         f"<td class='num'>{f2(b['rain_max3h']['model_mean_in'], nd=3)} / {f2(b['rain_max3h']['era5_mean_in'], nd=3)}</td><td class='num'>{f2(b['rain_max3h']['bias_in'], True, 3)} / {f2(b['rain_max3h']['mae_in'], nd=3)}</td></tr>")
    parts.append("</table></section>")

    # appendix
    app = r["appendix"]
    ex = app["excluded"]
    few = [BASIN_NAME[b] for b, v in app["basins"].items() if v["few_events"]]
    parts.append(f'<section id="appendix"><h2>Appendix: what the input error does to the overflow forecast</h2><p class="lead">The served <b>{esc(r["served"]["name"])}</b> stage 1 replayed on the labelled post-training days, {esc(app["window"][0])} → {esc(app["window"][1])}, the way the page builds a day L days ahead: the gauge record to the day before the issue day, then the input\'s forecast for the days after it, its peak hours for the day itself. Graded against the filed discharge days with the Brier skill score (BSS: 1 is perfect, 0 is no better than the {esc(app["reference"])}), and paired against the same model fed the gauges for every day (<i>rain known</i>, the ceiling). ΔBS above 0 = a higher Brier score than rain known, i.e. worse. Every row is scored on the same {ex["n_scored"]} basin-days ({app["events"]} discharge days, {app["n_blocks"]} storm and week blocks), so the BSS column reads across inputs too. The {ex["X-ALL-INSAMPLE"]} basin-days before 2025-11-01 are left out: the served weights were fit on them, and an in-sample score is never shown; {ex["no_forecast"]} more lacked a complete forecast for some input. {("Fewer than 10 discharge days in " + esc(", ".join(few)) + ", so those columns are shown with no weight. ") if few else ""}Shown, never used to choose the input.</p><div class="scroll"><table><tr><th>rain for the day</th><th class="num">basin-days / events</th><th class="num">BSS</th><th class="num">ΔBS vs rain known</th><th>verdict</th>'
                 + "".join(f"<th class='num'>BSS {esc(BASIN_NAME[b])} <span class='small'>({app['basins'][b]['scored_events']} events)</span></th>" for b in BASINS) + "</tr>")
    for a in app["arms"]:
        name = "rain known (the gauges)" if a["arm"] == "gauges" else f"{LABEL[a['arm']['input']]}, {LEAD_LABEL[a['arm']['lead']]}"
        d = a.get("delta")
        dtxt = f"{f2(d['delta'], True, 4)} <span class='small'>[{f2(d['lo'], True, 4)}, {f2(d['hi'], True, 4)}]</span>" if d else "—"
        parts.append(f"<tr{' class=best' if a['arm'] == 'gauges' else ''}><td>{esc(name)}</td><td class='num'>{a['n']} / {a['events']}</td><td class='num'>{f2(a['bss'])}</td><td class='num'>{dtxt}</td><td>{words(d['verdict']) if d else ''}</td>"
                     + "".join(f"<td class='num'>{f2(a['per_basin'][b]['bss'])}</td>" for b in BASINS) + "</tr>")
    parts.append("</table></div></section>")

    # exclusions
    parts.append('<section id="leftout"><h2>Left out of the scores</h2><p class="lead">Every day of the window is either scored or left out for one reason, checked in this order: the gauge has no value (<i>missing</i>); the gauge is inside a dead-gauge run (<i>outage</i>, it reads 0.00 while the other gauge records rain); some model has no archive yet at that lead (<i>not archived</i>); some model archived fewer than 24 hours of the day (<i>gap</i>, never counted as dry).</p><table><tr><th>lead</th><th>truth</th><th class="num">days</th><th class="num">missing</th><th class="num">outage</th><th class="num">not archived</th><th class="num">gap</th><th class="num">scored</th></tr>')
    for L in LEADS:
        for s in GAUGES:
            e = r["exclusions"][str(L)][s]
            parts.append(f"<tr><td>{esc(LEAD_LABEL[L])}</td><td>{esc(GAUGE_LABEL[s])}</td><td class='num'>{e['n_total']}</td><td class='num'>{e['X-S1-MISSING']}</td><td class='num'>{e['X-S1-OUTAGE']}</td><td class='num'>{e['X-S1-NOLEAD']}</td><td class='num'>{e['X-S1-NWPGAP']}</td><td class='num'>{e['n_scored']}</td></tr>")
    gaps = {m: ", ".join(f"lead {L} {r['archive'][m][str(L)]['gap_days_in_window']}" for L in LEADS if r["archive"][m][str(L)]["gap_days_in_window"]) for m in WX_MODELS}
    parts.append("</table><p class='small'>Gap days per model in the window: " + "; ".join(f"{WX_MODELS[m]} {gaps[m] or 'none'}" for m in WX_MODELS) + ".</p></section>")

    parts.append("""<section id="caveats"><h2>Caveats</h2><ul>
<li><b>Leads.</b> Lead L is "the value predicted 24·L hours before each hour", so a day's total at lead 1 mixes runs issued over the day before; the page's own Tomorrow row uses one morning run for the whole day. Close, not identical. Lead 0 is optimistic and is never treated as a fixed lead.</li>
<li><b>One grid cell.</b> Every model is read at one point for the whole city (ICON on the Ocean Beach shore, GFS downtown, ECMWF's nearest land cell across the bay in Alameda), while the two gauges differ by a factor of two on some storm days. The floor above is that spatial gap measured.</li>
<li><b>Calendar days.</b> The archive's days are a fixed UTC−7 day (Open-Meteo's offset when the caches were first fetched); in winter that runs 11 pm to 11 pm Pacific standard time, an hour earlier than the page's day. Gauge days are NOAA observation days. A storm crossing midnight can move a few tenths from one day to the next.</li>
<li><b>Power.</b> About a hundred wet days and thirty half-inch days in two and a half wet seasons; a few storms decide any ranking, which is why every number carries an interval and differences are only called when the interval says so.</li>
<li><b>Selection.</b> ICON was chosen on part of this window (2026-09-30), so it is slightly favoured here. Re-run each spring: <code>openmeteo_previous_runs.py --fetch</code>, then this script.</li>
</ul></section></div></body></html>""")
    return "".join(parts)


def rounded(obj, nd: int = 5):
    """Floats to ``nd`` decimals for the json (a report artifact; the scores are not that precise)."""
    if isinstance(obj, dict):
        return {k: rounded(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rounded(v, nd) for v in obj]
    return round(obj, nd) if isinstance(obj, float) else obj


def dumps(r: dict) -> str:
    """One top-level key per line, each compact: about half a megabyte instead of 1.2 with an indent."""
    r = rounded(r)
    return "{\n" + ",\n".join(f"{json.dumps(k)}: {json.dumps(v, separators=(',', ':'), ensure_ascii=False)}" for k, v in r.items()) + "\n}\n"


def main():
    r = run()
    OUT_JSON.write_text(dumps(r))
    OUT_HTML.write_text(build_html(r))
    P = str(PRIMARY_LEAD)
    firsts = ", ".join(f"{L}: from {v['first_day_all_models']}" for L, v in r["leads"].items())
    print(f"window {r['window'][0]} → {r['window'][1]}; leads {firsts}")
    for L in LEADS:
        b = r["by_lead"][str(L)]["avg"]
        print(f"lead {L} ({b['n_days']} days): " + "; ".join(
            f"{LABEL[k]} MAE {b['inputs'][k]['continuous']['either_wet']['mae']:.3f} ETS {f2(b['inputs'][k]['thresholds']['0.5']['ets'])} FBI {f2(b['inputs'][k]['thresholds']['0.5']['fbi'])}" for k in INPUTS))
    for k, d in r["paired"][P]["avg"].items():
        dm = d["mae_either_wet"]
        print(f"lead 1 {LABEL[k]} − ICON: ΔMAE {dm['delta']:+.4f} [{dm['lo']:+.4f}, {dm['hi']:+.4f}] {dm['verdict']}; ΔETS {d['ets_0.5']['delta']:+.3f} {d['ets_0.5']['verdict']}")
    for variant in ("masked", "unmasked", "as_served"):
        for name, st in r["floor"][variant].items():
            print(f"floor {variant:9s} {name}: MAE gauge-wet {st['continuous']['obs_wet']['mae']:.3f} either-wet {st['continuous']['either_wet']['mae']:.3f} CSI0.5 {f2(st['thresholds']['0.5']['csi'])}")
    for a in r["appendix"]["arms"]:
        d = a.get("delta")
        print(f"appendix {a['arm']}: BSS {f2(a['bss'], nd=3)}" + (f"  ΔBS {d['delta']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}] {d['verdict']}" if d else ""))
    print(f"wrote {OUT_JSON.relative_to(REPO)} and {OUT_HTML.relative_to(REPO)}")


if __name__ == "__main__":
    main()
