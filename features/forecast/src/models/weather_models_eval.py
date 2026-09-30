#!/usr/bin/env python3
"""Which weather model should stand in for the gauges on the forecast days?

The stage 1 models were trained on the rain that fell: two NOAA gauges (SF
Downtown 047772, SF Oceanside 047767) for the daily totals, ERA5 hourly for the
peak intensities. A weather model is an INPUT: on the forecast days its rain is
fed where the gauge reading would go. So a weather model is graded here the way
an input should be — on how close its rain for a day comes to what the gauges
then recorded — not on what the discharge model does with it.

Open-Meteo archives each model's past forecasts (the model run covering each
day: a day-0 / day-1 lead, the page's Today and Tomorrow rows). ECMWF IFS 0.25°
is archived from Feb 2024, GFS and ICON earlier, so the comparison window is
Feb 2024 → the end of the hourly record: two wet seasons. Inputs compared:
ECMWF IFS (served 2026-09-04 → 09-30), GFS, ICON (served since 2026-09-30), and the mean of the three
(the multi-model ensemble mean). ERA5, the training-time hourly source, is
shown as a reference row: it is a reanalysis, not a forecast.

Appendix: the same days replayed through the served stage 1 with each input's
rain for the day — what the input error does to the forecast. Secondary; the
verdict rests on the rain.

Outputs
    features/forecast/data/raw/openmeteo_hist_forecast_<model>.csv   (hourly, cached)
    features/forecast/data/models/weather_models_eval.json
    reports/2026-09_weather_models.html

Usage
    venv/bin/python features/forecast/src/models/weather_models_eval.py [--refresh]
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for p in (str(REPO), str(FORECAST), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import leaderboard  # noqa: E402,F401  (weights pipelines reference leaderboard.add_hinges)
from src.collectors.csd_labels import build_daily_labels  # noqa: E402
from src.models import candidates  # noqa: E402
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

LAT, LON = 37.7749, -122.4194          # the point serving asks Open-Meteo for
TZ = "America/Los_Angeles"
ARCHIVE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
WX_MODELS = {"ecmwf_ifs025": "ECMWF IFS", "gfs_seamless": "GFS", "icon_seamless": "ICON"}
SERVED_WX = "icon_seamless"   # served since 2026-09-30 (ECMWF IFS 2026-09-04 → 09-30)
INPUTS = {**WX_MODELS, "mean3": "mean of the three"}          # the candidate inputs
REFERENCE = {"era5": "ERA5 reanalysis (training-time hourly source)"}
START = pd.Timestamp("2024-02-01")     # first month the archive holds all three models
WET_DAY_IN = 0.1                       # a gauge day at or above this is "wet"
RAIN_THRESHOLDS = (0.1, 0.25, 0.5, 1.0)
TOP_DAYS = 25
GAUGES = ["SF Downtown", "SF Oceanside", "avg"]
GAUGE_LABEL = {"SF Downtown": "SF Downtown (047772)", "SF Oceanside": "SF Oceanside (047767)", "avg": "two-gauge mean"}
LINES = (0.25, 0.5)
MISS_WEIGHT = 2.0                      # Chase: a miss costs two false alarms (appendix only)

BASINS = list(BASIN_KEYS.values())     # westside, north_shore, central, southeast
BASIN_NAME = {v: k for k, v in BASIN_KEYS.items()}


# ── data ────────────────────────────────────────────────────────────────────

def fetch_model_hourly(model: str, start: pd.Timestamp, end: pd.Timestamp, refresh: bool = False) -> pd.DataFrame:
    """Hourly precipitation (inches, local time) from Open-Meteo's archive of
    past forecasts, cached under data/raw."""
    path = RAW_DIR / f"openmeteo_hist_forecast_{model}.csv"
    if path.exists() and not refresh:
        df = pd.read_csv(path, parse_dates=["timestamp"])
        if df["timestamp"].min() <= start and df["timestamp"].max() >= end - pd.Timedelta(hours=23):
            return df
    frames = []
    for y0 in pd.date_range(start, end, freq="YS").union([start]):
        y1 = min(pd.Timestamp(year=y0.year, month=12, day=31), end)
        if y0 > end:
            continue
        r = requests.get(ARCHIVE_URL, params={
            "latitude": LAT, "longitude": LON, "hourly": "precipitation", "models": model,
            "start_date": y0.strftime("%Y-%m-%d"), "end_date": y1.strftime("%Y-%m-%d"), "timezone": TZ,
        }, timeout=90)
        r.raise_for_status()
        h = r.json()["hourly"]
        col = next(k for k in h if k != "time")
        frames.append(pd.DataFrame({"timestamp": pd.to_datetime(h["time"]), "precip_mm": h[col]}))
    df = pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df["precip_inches"] = df["precip_mm"].astype(float) / 25.4
    df.to_csv(path, index=False)
    return df


def gauge_daily(input_rules: list | None) -> pd.DataFrame:
    """date × {SF Downtown, SF Oceanside, avg}: the gauge record as serving and
    post-training rescoring read it (the outage rule applied, a missing gauge
    day taking the other gauge, then zero)."""
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
    return out.reset_index()


def daily_from_hourly(h: pd.DataFrame) -> tuple:
    """(daily totals by date, peak-intensity frame by date) from an hourly series."""
    h = h.copy()
    h["precip_inches"] = h["precip_inches"].astype(float).fillna(0.0)
    h["date"] = pd.to_datetime(h["timestamp"]).dt.normalize()
    daily = h.groupby("date")["precip_inches"].sum()
    inten = hourly_intensity(h).set_index("date")
    return daily, inten


def season_of(d: pd.Timestamp) -> str:
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


# ── rain verification ───────────────────────────────────────────────────────

def verify(truth: pd.Series, model: pd.Series) -> dict:
    """Standard forecast verification of a daily total against a gauge:
    bias / MAE / RMSE / r on all days and on wet days, season totals, and the
    contingency table (POD, FAR, CSI, frequency bias) at each threshold."""
    both = pd.concat([truth.rename("t"), model.rename("m")], axis=1).dropna()
    err = both["m"] - both["t"]
    wet = both[both["t"] >= WET_DAY_IN]
    werr = wet["m"] - wet["t"]
    out = {
        "n_days": int(len(both)), "wet_days": int(len(wet)),
        "bias_in": float(err.mean()), "mae_in": float(err.abs().mean()), "rmse_in": float(np.sqrt((err ** 2).mean())),
        "r": float(both["t"].corr(both["m"])) if both["t"].std() > 0 else None,
        "wet_bias_in": float(werr.mean()) if len(wet) else None, "wet_mae_in": float(werr.abs().mean()) if len(wet) else None,
        "wet_rmse_in": float(np.sqrt((werr ** 2).mean())) if len(wet) else None,
        "wet_r": float(wet["t"].corr(wet["m"])) if len(wet) > 2 and wet["t"].std() > 0 else None,
        "total_in": {"gauge": float(both["t"].sum()), "model": float(both["m"].sum())},
        "thresholds": {},
    }
    for t in RAIN_THRESHOLDS:
        a = both["t"] >= t; b = both["m"] >= t
        hit = int((a & b).sum()); miss = int((a & ~b).sum()); false = int((~a & b).sum())
        out["thresholds"][str(t)] = {
            "gauge_days": int(a.sum()), "model_days": int(b.sum()), "hit": hit, "miss": miss, "false": false,
            "pod": hit / (hit + miss) if hit + miss else None,
            "far": false / (hit + false) if hit + false else None,
            "csi": hit / (hit + miss + false) if hit + miss + false else None,
            "freq_bias": (hit + false) / (hit + miss) if hit + miss else None,
        }
    return out


# ── appendix: the tomorrow scenario through stage 1 ─────────────────────────

def scenario_rows(gauge: pd.Series, dates: pd.DatetimeIndex, day_value: pd.Series | None, inten: pd.DataFrame) -> pd.DataFrame:
    """Features for each D: the gauge series to D-1, `day_value[D]` for D (None →
    the gauge itself), peak intensities for D from `inten`."""
    idx = {d: i for i, d in enumerate(gauge.index)}
    vals = gauge.values.astype(float)
    rows = []
    for d in dates:
        i = idx[d]
        s = vals[max(0, i - 31):i + 1].copy()
        if day_value is not None:
            v = day_value.get(d, np.nan)
            s[-1] = float(v) if pd.notna(v) else vals[i]
        f = add_daily_features(pd.DataFrame({"precip_inches": s})).iloc[-1]
        row = {k: float(f[k]) for k in DAILY_FEATURES}
        for k in INTENSITY_FEATURES:
            row[k] = float(inten.at[d, k]) if d in inten.index and pd.notna(inten.at[d, k]) else 0.0
        row["date"] = d
        rows.append(row)
    return pd.DataFrame(rows).set_index("date")


def load_served_stage1() -> dict:
    out = {}
    for key in BASINS:
        with open(MODEL_DIR / f"{key}_model.pkl", "rb") as f:
            out[key] = pickle.load(f)
    return out


def predict(md: dict, feats: pd.DataFrame) -> np.ndarray:
    """The serving rule (live_dashboard._predict_calibrated)."""
    raw = md["model"].predict_proba(feats[md["features"]])[:, 1]
    factor = np.maximum(0.0, 1.0 - feats["rain_3d_cum"].values * 2.0)
    return np.clip(raw - md.get("calibration_offset", 0.0) * factor, 0.0, 1.0)


def grade(y: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import average_precision_score
    out = {"n": int(len(y)), "pos": int(y.sum()), "pr_auc": float(average_precision_score(y, p)) if 0 < y.sum() < len(y) else None}
    for line in LINES:
        pred = p >= line
        tp = int((pred & (y == 1)).sum()); fn = int((~pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum())
        out[f"line_{int(line*100)}"] = {"tp": tp, "fn": fn, "fp": fp, "cost": fp + MISS_WEIGHT * fn}
    return out


# ── main ────────────────────────────────────────────────────────────────────

def run(refresh: bool = False) -> dict:
    served = candidates.SERVED
    input_rules = list(served.get("input_rules_post") or [])
    sources = served.get("rain_sources") or {}
    gauges = gauge_daily(input_rules).set_index("date")
    era5_daily, era5_inten = daily_from_hourly(pd.read_csv(RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"]))
    labels = build_daily_labels().set_index("date")

    end = min(gauges.index.max(), era5_daily.index.max(), labels.index.max())
    dates = pd.date_range(START, end)
    wx = {}
    for m in WX_MODELS:
        h = fetch_model_hourly(m, START - pd.Timedelta(days=1), end, refresh=refresh)
        d, inten = daily_from_hourly(h)
        first = h.dropna(subset=["precip_mm"])["timestamp"].min()
        wx[m] = {"daily": d.reindex(dates), "inten": inten, "first": str(first.date()) if pd.notna(first) else None}
    mean3 = pd.concat([wx[m]["daily"] for m in WX_MODELS], axis=1).mean(axis=1)
    inputs = {**{m: wx[m]["daily"] for m in WX_MODELS}, "mean3": mean3}
    reference = {"era5": era5_daily.reindex(dates)}

    result = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "window": [str(dates[0].date()), str(dates[-1].date())],
              "served": {"name": served["name"], "stage1": served["stage1"], "line": served.get("line"), "weather_model": SERVED_WX},
              "input_rules": input_rules, "rain_sources": sources, "wet_day_in": WET_DAY_IN,
              "archive_first_day": {m: wx[m]["first"] for m in WX_MODELS},
              "gauge_days": {}, "verification": {}, "by_season": {}, "top_days": [], "intensity": {}, "appendix": {}}

    # 1. verification against each gauge and the mean, all inputs + the reference
    for g in GAUGES:
        truth = gauges.loc[dates, g]
        result["gauge_days"][g] = {"wet_days": int((truth >= WET_DAY_IN).sum()), **{f"ge_{t}": int((truth >= t).sum()) for t in RAIN_THRESHOLDS}}
        result["verification"][g] = {k: verify(truth, s) for k, s in {**inputs, **reference}.items()}

    # 2. by season (two-gauge mean): wet-day MAE and POD/FAR at 0.5"
    seasons = pd.Series([season_of(d) for d in dates], index=dates)
    for s in sorted(seasons.unique()):
        sel = (seasons == s).values
        truth = gauges.loc[dates[sel], "avg"]
        if int((truth >= WET_DAY_IN).sum()) == 0:   # Jul–Aug of the current season: nothing to verify yet
            continue
        result["by_season"][s] = {"days": int(sel.sum()), "wet_days": int((truth >= WET_DAY_IN).sum()), "gauge_total_in": float(truth.sum()),
                                  "inputs": {k: verify(truth, v[sel]) for k, v in inputs.items()}}

    # 3. the biggest gauge days, every input beside them
    top = gauges.loc[dates, "avg"].sort_values(ascending=False).head(TOP_DAYS).index.sort_values()
    for d in top:
        row = {"date": str(d.date()), "season": season_of(d), "downtown_in": round(float(gauges.at[d, "SF Downtown"]), 2),
               "oceanside_in": round(float(gauges.at[d, "SF Oceanside"]), 2), "mean_in": round(float(gauges.at[d, "avg"]), 2)}
        for k, s in {**inputs, **reference}.items():
            v = s.get(d, np.nan)
            row[k] = round(float(v), 2) if pd.notna(v) else None
        result["top_days"].append(row)

    # 4. peak intensity: each model's 1h / 3h maxima on wet days against ERA5's (the source the
    #    intensity features were trained on — a reanalysis, so a reference, not a gauge)
    wet_mask = (gauges.loc[dates, "avg"] >= WET_DAY_IN).values
    for m in WX_MODELS:
        mi = wx[m]["inten"].reindex(dates)
        ei = era5_inten.reindex(dates)
        block = {}
        for k in ("rain_max1h", "rain_max3h"):
            e = (mi[k] - ei[k])[wet_mask].dropna()
            block[k] = {"bias_in": float(e.mean()), "mae_in": float(e.abs().mean()), "n": int(len(e)),
                        "era5_mean_in": float(ei[k][wet_mask].mean()), "model_mean_in": float(mi[k][wet_mask].mean())}
        result["intensity"][m] = block

    # 5. appendix: the tomorrow scenario through the served stage 1
    stage1 = load_served_stage1()
    scen = {"gauges": None, **inputs}
    feats = {}
    for src in sorted(set(sources.get(b, "avg") for b in BASINS)):
        g = gauges[src]
        for name, s in scen.items():
            inten = era5_inten if name in ("gauges", "mean3") else wx[name]["inten"]
            feats[(src, name)] = scenario_rows(g, dates, s, inten)
    app = {"basins": {}, "total": {}}
    for b in BASINS:
        src = stage1[b].get("rain_source", sources.get(b, "avg"))
        covered = labels.loc[dates, f"{BASIN_NAME[b]}_covered"].fillna(0).astype(int).values == 1
        y = labels.loc[dates, f"{BASIN_NAME[b]}_csd"].fillna(0).astype(int).values[covered]
        app["basins"][b] = {"rain_source": src, "covered_days": int(covered.sum()),
                            **{name: grade(y, predict(stage1[b], feats[(src, name)])[covered]) for name in scen}}
    for name in scen:
        app["total"][name] = {}
        for line in LINES:
            k = f"line_{int(line*100)}"
            tot = {m: int(sum(app["basins"][b][name][k][m] for b in BASINS)) for m in ("tp", "fn", "fp")}
            tot["cost"] = tot["fp"] + MISS_WEIGHT * tot["fn"]
            app["total"][name][k] = tot
    result["appendix"] = app
    return result


# ── report ──────────────────────────────────────────────────────────────────

def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def f2(v, signed=False):
    if v is None:
        return "—"
    return f"{v:+.2f}" if signed else f"{v:.2f}"


def pc(v):
    return "—" if v is None else f"{100*v:.0f}%"


def build_html(r: dict) -> str:
    css = (HERE / "stage2_explorer_template.html").read_text().split("<style>")[1].split("</style>")[0]
    label = {**INPUTS, **REFERENCE}
    ver = r["verification"]

    # ranking on the two-gauge mean: wet-day MAE, then CSI at 0.5"
    rank_mae = sorted(INPUTS, key=lambda k: ver["avg"][k]["wet_mae_in"])
    rank_csi = sorted(INPUTS, key=lambda k: -(ver["avg"][k]["thresholds"]["0.5"]["csi"] or 0))
    best_single = min(WX_MODELS, key=lambda k: ver["avg"][k]["wet_mae_in"])
    m3, sv = ver["avg"]["mean3"], ver["avg"][SERVED_WX]
    t5 = lambda k: ver["avg"][k]["thresholds"]["0.5"]  # noqa: E731
    verdict = (f"Against the two-gauge mean on the {r['gauge_days']['avg']['wet_days']} wet days since {r['window'][0]}, the closest input is the "
               f"<b>{esc(label[rank_mae[0]])}</b>: mean absolute error {ver['avg'][rank_mae[0]]['wet_mae_in']:.2f}\" per wet day, bias {ver['avg'][rank_mae[0]]['wet_bias_in']:+.2f}\". Then "
               + "; ".join(f"{esc(label[k])} {ver['avg'][k]['wet_mae_in']:.2f}\" ({ver['avg'][k]['wet_bias_in']:+.2f}\")" for k in rank_mae[1:])
               + f". At the half-inch mark the best skill score is the {esc(label[rank_csi[0]])} (CSI {t5(rank_csi[0])['csi']:.2f}: it catches {pc(t5(rank_csi[0])['pod'])} of the gauges' half-inch days and {pc(t5(rank_csi[0])['far'])} of its own half-inch calls are false); "
               + "; ".join(f"{esc(label[k])} CSI {t5(k)['csi']:.2f} ({pc(t5(k)['pod'])} caught, {pc(t5(k)['far'])} false)" for k in rank_csi[1:])
               + f". ICON, served since 2026-09-30, sits at {sv['wet_mae_in']:.2f}\" MAE and CSI {t5(SERVED_WX)['csi']:.2f}; ECMWF IFS, served before it, at {ver['avg']['ecmwf_ifs025']['wet_mae_in']:.2f}\" and {t5('ecmwf_ifs025')['csi']:.2f}; the mean of the three at {m3['wet_mae_in']:.2f}\" and {t5('mean3')['csi']:.2f}. "
               + (f"Among single models {esc(WX_MODELS[best_single])} is closest." if best_single != SERVED_WX else "The served model is the closest single model."))

    def vtable(g: str) -> str:
        gd = r["gauge_days"][g]
        rows = [f"<h3>{esc(GAUGE_LABEL[g])} — {gd['wet_days']} wet days; " + ", ".join(f"{gd[f'ge_{t}']} days ≥ {t}\"" for t in RAIN_THRESHOLDS) + "</h3>",
                "<table><tr><th>input</th><th class='num'>season total, input / gauge (in)</th><th class='num'>bias, all days</th><th class='num'>MAE, all days</th><th class='num'>wet-day bias</th><th class='num'>wet-day MAE</th><th class='num'>wet-day RMSE</th><th class='num'>r, wet days</th>"
                + "".join(f"<th class='num'>≥ {t}\": POD / FAR / CSI</th>" for t in RAIN_THRESHOLDS) + "</tr>"]
        best = min(INPUTS, key=lambda k: ver[g][k]["wet_mae_in"])
        for k in list(INPUTS) + list(REFERENCE):
            v = ver[g][k]
            cls = " class='best'" if k == best else (" class='ref'" if k in REFERENCE else "")
            rows.append(f"<tr{cls}><td>{esc(label[k])}</td><td class='num'>{v['total_in']['model']:.1f} / {v['total_in']['gauge']:.1f}</td><td class='num'>{f2(v['bias_in'], True)}</td><td class='num'>{f2(v['mae_in'])}</td>"
                        f"<td class='num'>{f2(v['wet_bias_in'], True)}</td><td class='num'>{f2(v['wet_mae_in'])}</td><td class='num'>{f2(v['wet_rmse_in'])}</td><td class='num'>{f2(v['wet_r'])}</td>"
                        + "".join(f"<td class='num'>{pc(v['thresholds'][str(t)]['pod'])} / {pc(v['thresholds'][str(t)]['far'])} / {f2(v['thresholds'][str(t)]['csi'])}</td>" for t in RAIN_THRESHOLDS) + "</tr>")
        rows.append("</table>")
        return "".join(rows)

    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Which weather model stands in for the gauges</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css}
tr.best td{{background:#e0f0ea}} tr.ref td{{color:#8a8d9b;font-style:italic}} .verdict{{background:#0072BC;color:#fff;border-radius:16px;padding:16px 20px;font-size:16px}} .verdict b{{font-size:18px}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}} .small{{font-size:13px;color:#54576F}} td.big{{font-weight:700}}
</style></head><body><div class="wrap">
<header><h1>Which weather model stands in for the gauges</h1>
<p class="sub">The stage 1 models were trained on the rain that fell: the two NOAA gauges for daily totals, ERA5 hourly for peak intensity. A weather model is an <b>input</b> — on the forecast days its rain is fed where the gauge reading would go — so it is graded here as an input: how close its rain for a day comes to what the gauges then recorded. Three Open-Meteo models are archived for the whole window, plus their mean; the page served ECMWF IFS from 2026-09-04 and ICON since 2026-09-30. Every number is the model run covering the day itself (day-0 / day-1 lead, the page's Today and Tomorrow rows).</p>
<div class="meta"><span>window {esc(r['window'][0])} → {esc(r['window'][1])}</span><span>archive starts: {esc(", ".join(f"{WX_MODELS[m]} {r['archive_first_day'][m]}" for m in WX_MODELS))}</span><span>wet day = gauge ≥ {WET_DAY_IN}"</span><span>gauge record as served: {esc(", ".join(r['input_rules']) or 'raw')}</span><span>generated {esc(r['generated'])}</span></div></header>
<nav><a href="#verdict">Verdict</a><a href="#gauges">Against each gauge</a><a href="#seasons">By season</a><a href="#storms">The biggest days</a><a href="#intensity">Peak intensity</a><a href="#appendix">Appendix: through stage 1</a><a href="#caveats">Caveats</a></nav>
<section id="verdict"><div class="verdict">{verdict}</div>
<p class="lead">How to read the tables. <b>Bias</b> is input minus gauge (positive = the model is wetter). <b>MAE</b> is the average size of the miss in inches; wet-day figures use only days the gauge read {WET_DAY_IN}" or more, so a model is not rewarded for the dry days everyone gets right. <b>r</b> is the correlation on wet days. The threshold cells read: of the days the gauge reached the threshold, the share the model also did (<b>POD</b>); of the days the model reached it, the share that were false (<b>FAR</b>); and <b>CSI</b> = hits ÷ (hits + misses + false alarms), one number for both, 1.0 being perfect. Lower MAE and higher CSI are better.</p></section>
"""]

    parts.append('<section id="gauges"><h2>Against each gauge</h2><p class="lead">Westside stage 1 reads the two-gauge mean; the bay basins read Downtown alone. The same one-point model value is fed to both, so a model is graded against all three. The grey italic row is ERA5, the reanalysis the intensity features were trained on: not a forecast, a reference for how far even a hindcast sits from the gauges.</p>')
    for g in GAUGES:
        parts.append(vtable(g))
    parts.append("</section>")

    # seasons
    parts.append('<section id="seasons"><h2>By season, two-gauge mean</h2><p class="lead">Whether the ranking holds from one winter to the next. Seasons run July to June.</p><table><tr><th>season</th><th class="num">wet days</th><th class="num">gauge total (in)</th>'
                 + "".join(f"<th class='num'>{esc(label[k])}: total / wet MAE / POD ≥ 0.5\" / FAR</th>" for k in INPUTS) + "</tr>")
    for s, blk in r["by_season"].items():
        parts.append(f"<tr><td>{esc(s)}</td><td class='num'>{blk['wet_days']}</td><td class='num'>{blk['gauge_total_in']:.1f}</td>"
                     + "".join(f"<td class='num'>{blk['inputs'][k]['total_in']['model']:.1f} / {f2(blk['inputs'][k]['wet_mae_in'])} / {pc(blk['inputs'][k]['thresholds']['0.5']['pod'])} / {pc(blk['inputs'][k]['thresholds']['0.5']['far'])}</td>" for k in INPUTS) + "</tr>")
    parts.append("</table></section>")

    # top days
    parts.append(f'<section id="storms"><h2>The {TOP_DAYS} biggest gauge days</h2><p class="lead">Ranked by the two-gauge mean, in date order. Inches per input beside the two gauges; bold marks an input within a quarter inch of the gauge mean.</p><table><tr><th>date</th><th>season</th><th class="num">Downtown</th><th class="num">Oceanside</th><th class="num">mean</th>'
                 + "".join(f"<th class='num'>{esc(label[k])}</th>" for k in list(INPUTS) + list(REFERENCE)) + "</tr>")
    for row in r["top_days"]:
        parts.append(f"<tr><td>{row['date']}</td><td>{row['season']}</td><td class='num'>{row['downtown_in']:.2f}</td><td class='num'>{row['oceanside_in']:.2f}</td><td class='num'>{row['mean_in']:.2f}</td>"
                     + "".join(("<td class='num'>—</td>" if row[k] is None else f"<td class='num{' big' if abs(row[k] - row['mean_in']) <= 0.25 else ''}'>{row[k]:.2f}</td>") for k in list(INPUTS) + list(REFERENCE)) + "</tr>")
    parts.append("</table></section>")

    # intensity
    parts.append('<section id="intensity"><h2>Peak intensity</h2><p class="lead">Three of the 19 features are the day\'s peak 1-, 3- and 6-hour rain. They were trained on ERA5 hourly, so each model\'s peaks on wet days are compared with ERA5\'s for the same days. ECMWF\'s precipitation is three-hourly, spread to hourly by Open-Meteo; GFS and ICON are hourly natively. A reference, not a gauge check: no hourly gauge record exists for the city for this window.</p><table><tr><th>model</th><th class="num">wet days</th><th class="num">peak 1h: model mean / ERA5 mean (in)</th><th class="num">bias / MAE</th><th class="num">peak 3h: model mean / ERA5 mean (in)</th><th class="num">bias / MAE</th></tr>')
    for m in WX_MODELS:
        b = r["intensity"][m]
        parts.append(f"<tr><td>{esc(WX_MODELS[m])}</td><td class='num'>{b['rain_max1h']['n']}</td><td class='num'>{b['rain_max1h']['model_mean_in']:.3f} / {b['rain_max1h']['era5_mean_in']:.3f}</td><td class='num'>{f2(b['rain_max1h']['bias_in'], True)} / {f2(b['rain_max1h']['mae_in'])}</td>"
                     f"<td class='num'>{b['rain_max3h']['model_mean_in']:.3f} / {b['rain_max3h']['era5_mean_in']:.3f}</td><td class='num'>{f2(b['rain_max3h']['bias_in'], True)} / {f2(b['rain_max3h']['mae_in'])}</td></tr>")
    parts.append("</table></section>")

    # appendix
    app = r["appendix"]; s = r["served"]["name"]
    parts.append(f'<section id="appendix"><h2>Appendix: what the input error does to the forecast</h2><p class="lead">Secondary. The page\'s Tomorrow row replayed for every day in the window: the gauge record to the day before, the input\'s rain for the day, the served <b>{esc(s)}</b> stage 1 on top, graded against the filed discharge days (a miss costs {MISS_WEIGHT:g} false alarms). "gauges" is the hindcast — the day\'s own gauge rain, the ceiling. This mixes the input\'s error with the discharge model\'s own; the verdict above rests on the rain alone.</p><table><tr><th>rain for the day</th>'
                 + "".join(f"<th class='num'>{int(l*100)}% line: caught</th><th class='num'>false alarms</th><th class='num'>cost</th>" for l in LINES) + "".join(f"<th class='num'>PR-AUC {esc(BASIN_NAME[b])}</th>" for b in BASINS) + "</tr>")
    for name in ["gauges"] + list(INPUTS):
        t = app["total"][name]
        parts.append(f"<tr><td>{esc(label.get(name, name))}</td>" + "".join(f"<td class='num'>{t[f'line_{int(l*100)}']['tp']}/{t[f'line_{int(l*100)}']['tp'] + t[f'line_{int(l*100)}']['fn']}</td><td class='num'>{t[f'line_{int(l*100)}']['fp']}</td><td class='num'>{t[f'line_{int(l*100)}']['cost']:.0f}</td>" for l in LINES)
                     + "".join(f"<td class='num'>{f2(app['basins'][b][name].get('pr_auc'))}</td>" for b in BASINS) + "</tr>")
    parts.append("</table></section>")

    parts.append("""<section id="caveats"><h2>Caveats</h2><ul>
<li><b>Lead time.</b> Open-Meteo's archive keeps the model run covering each day — a day-0 / day-1 forecast. It says nothing about the page's day-3 to day-5 rows, where models differ most; the previous-runs archive that would answer that only reaches back a few weeks. Logging the page's own forecast days from now on is the way to measure those leads.</li>
<li><b>One grid cell.</b> Every model is read at one point for the whole city, while the two gauges differ by a factor of two on some storm days. Part of every model's "error" against Oceanside or Downtown alone is that spatial gap, which is why the two-gauge mean is the fairest yardstick.</li>
<li><b>Calendar days.</b> Gauge days are NOAA observation days; the model hours are summed over the same local calendar day. A storm crossing midnight lands in the same day for both, but small offsets in the gauge reading time can move a few tenths from one day to the next.</li>
<li><b>Two seasons.</b> About a hundred wet days and forty half-inch days; a few storms decide the ranking. Re-run each spring (the script caches the archive; <code>--refresh</code> refetches).</li>
</ul></section></div></body></html>""")
    return "".join(parts)


def main():
    r = run(refresh="--refresh" in sys.argv)
    OUT_JSON.write_text(json.dumps(r, indent=1))
    OUT_HTML.write_text(build_html(r))
    label = {**INPUTS, **REFERENCE}
    print(f"window {r['window'][0]} → {r['window'][1]}; archive first days {r['archive_first_day']}")
    for g in GAUGES:
        gd = r["gauge_days"][g]
        print(f"vs {g} ({gd['wet_days']} wet days, {gd['ge_0.5']} ≥ 0.5\"):")
        for k in list(INPUTS) + list(REFERENCE):
            v = r["verification"][g][k]; t = v["thresholds"]["0.5"]
            print(f"   {label[k]:44s} total {v['total_in']['model']:6.1f}/{v['total_in']['gauge']:6.1f}  wet bias {v['wet_bias_in']:+.3f}  wet MAE {v['wet_mae_in']:.3f}  RMSE {v['wet_rmse_in']:.3f}  r {f2(v['wet_r'])}  ≥0.5\" POD {pc(t['pod'])} FAR {pc(t['far'])} CSI {f2(t['csi'])} fbias {f2(t['freq_bias'])}")
    for s, blk in r["by_season"].items():
        print(f"season {s}: wet {blk['wet_days']} gauge {blk['gauge_total_in']:.1f}\" | " + "; ".join(f"{label[k]} {blk['inputs'][k]['total_in']['model']:.1f}\" MAE {f2(blk['inputs'][k]['wet_mae_in'])} POD {pc(blk['inputs'][k]['thresholds']['0.5']['pod'])}" for k in INPUTS))
    for m in WX_MODELS:
        b = r["intensity"][m]["rain_max1h"]
        print(f"peak 1h {WX_MODELS[m]}: model {b['model_mean_in']:.3f} vs ERA5 {b['era5_mean_in']:.3f} (bias {b['bias_in']:+.3f}, MAE {b['mae_in']:.3f})")
    line_k = f"line_{int((r['served']['line'] or 0.25)*100)}"
    t = r["appendix"]["total"]
    print(f"appendix @{line_k}: " + "; ".join(f"{label.get(n, n)} cost {t[n][line_k]['cost']:.0f} ({t[n][line_k]['tp']}/{t[n][line_k]['tp'] + t[n][line_k]['fn']}, {t[n][line_k]['fp']} FA)" for n in ["gauges"] + list(INPUTS)))
    print(f"wrote {OUT_JSON.relative_to(REPO)} and {OUT_HTML.relative_to(REPO)}")


if __name__ == "__main__":
    main()
