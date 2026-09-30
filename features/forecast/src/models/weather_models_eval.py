#!/usr/bin/env python3
"""Which weather model should feed the forecast days?

The stage 1 models were trained on the rain that fell (two NOAA gauges for the
daily totals, ERA5 hourly for the peak intensities). A weather model only
enters at serving time, where the forecast days' rain comes from one Open-Meteo
model (ECMWF IFS since 2026-09-04). This script asks whether that is the right
one, and whether several would be better, by replaying the "tomorrow" scenario:

    for every day D in the window, features are built from the GAUGE record up
    to D-1 plus the weather model's own rain for D (its daily total and its
    hourly peaks), exactly as the page builds tomorrow's row; the served stage 1
    scores those features; the result is graded against the CIWQS discharge
    record for D.

Open-Meteo's historical-forecast archive supplies each model's past forecasts
(the model run covering each day, so a day-0 / day-1 lead — the page's Today
and Tomorrow rows; longer leads are not archived). The archive holds ECMWF IFS
0.25° from 2024-02, GFS from before 2021 and ICON from 2023, so the comparison
window is Feb 2024 → the end of the hourly record: two wet seasons.

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
from datetime import date, datetime
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
SERVED_WX = "ecmwf_ifs025"
START = pd.Timestamp("2024-02-01")     # first month the archive holds all three models
LINES = (0.25, 0.5)
MISS_WEIGHT = 2.0                      # Chase: a miss costs two false alarms
RAIN_THRESHOLDS = (0.25, 0.5, 1.0)     # inches, for the rain contingency tables
DISAGREE = 0.25                        # max − min basin probability across models

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
        d = r.json()
        h = d["hourly"]
        col = next(k for k in h if k != "time")
        frames.append(pd.DataFrame({"timestamp": pd.to_datetime(h["time"]), "precip_mm": h[col]}))
    df = pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df["precip_inches"] = df["precip_mm"].astype(float) / 25.4
    df.to_csv(path, index=False)
    return df


def gauge_daily(input_rules: list | None) -> pd.DataFrame:
    """date × {SF Downtown, SF Oceanside, avg}, the gauge outage rule applied
    as post-training rescoring applies it; missing days filled as training did."""
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
    """(daily totals Series by date, intensity frame by date) from an hourly series."""
    h = h.copy()
    h["precip_inches"] = h["precip_inches"].astype(float).fillna(0.0)
    h["date"] = pd.to_datetime(h["timestamp"]).dt.normalize()
    daily = h.groupby("date")["precip_inches"].sum()
    inten = hourly_intensity(h).set_index("date")
    return daily, inten


# ── the tomorrow scenario ───────────────────────────────────────────────────

def scenario_rows(gauge: pd.Series, dates: pd.DatetimeIndex, day_value: pd.Series | None, inten: pd.DataFrame) -> pd.DataFrame:
    """Features for each D in `dates`: the gauge series up to D-1, then `day_value[D]`
    (None → the gauge itself, the hindcast ceiling) for D; peak intensities for D
    from `inten`. Returns one row per date with the 19 features."""
    g = gauge.copy()
    idx = {d: i for i, d in enumerate(g.index)}
    vals = g.values.astype(float)
    rows = []
    for d in dates:
        i = idx[d]
        lo = max(0, i - 31)
        s = vals[lo:i + 1].copy()
        if day_value is not None:
            v = day_value.get(d, np.nan)
            s[-1] = float(v) if pd.notna(v) else vals[i]
        f = add_daily_features(pd.DataFrame({"precip_inches": s})).iloc[-1]
        row = {k: float(f[k]) for k in DAILY_FEATURES}
        if d in inten.index:
            for k in INTENSITY_FEATURES:
                row[k] = float(inten.at[d, k]) if pd.notna(inten.at[d, k]) else 0.0
        else:
            for k in INTENSITY_FEATURES:
                row[k] = 0.0
        row["date"] = d
        row["rain_day"] = float(s[-1])
        rows.append(row)
    return pd.DataFrame(rows).set_index("date")


def load_stage1(name: str) -> dict:
    """{basin key: pickle dict} for the served set (data/models) or a candidate."""
    if name == candidates.SERVED["name"]:
        out = {}
        for key in BASINS:
            with open(MODEL_DIR / f"{key}_model.pkl", "rb") as f:
                out[key] = pickle.load(f)
        return out
    return {k: v for k, v in candidates.load_models(name).items() if k in BASINS}


def predict(md: dict, feats: pd.DataFrame) -> np.ndarray:
    """The serving rule: raw probability minus a calibration offset that fades
    to zero by 0.5" of 3-day rain (live_dashboard._predict_calibrated)."""
    X = feats[md["features"]]
    raw = md["model"].predict_proba(X)[:, 1]
    factor = np.maximum(0.0, 1.0 - feats["rain_3d_cum"].values * 2.0)
    return np.clip(raw - md.get("calibration_offset", 0.0) * factor, 0.0, 1.0)


# ── grading ─────────────────────────────────────────────────────────────────

def grade(y: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    out = {"n": int(len(y)), "pos": int(y.sum())}
    if 0 < y.sum() < len(y):
        out["pr_auc"] = float(average_precision_score(y, p))
        out["roc_auc"] = float(roc_auc_score(y, p))
    out["brier"] = float(brier_score_loss(y, p))
    for line in LINES:
        pred = p >= line
        tp = int((pred & (y == 1)).sum()); fn = int((~pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum())
        out[f"line_{int(line*100)}"] = {"tp": tp, "fn": fn, "fp": fp, "cost": fp + MISS_WEIGHT * fn}
    return out


def rain_skill(truth: pd.Series, model: pd.Series) -> dict:
    both = pd.concat([truth.rename("t"), model.rename("m")], axis=1).dropna()
    wet = both[both["t"] >= 0.1]
    out = {"n_days": int(len(both)), "bias_in": float((both["m"] - both["t"]).mean()),
           "wet_days": int(len(wet)), "wet_mae_in": float((wet["m"] - wet["t"]).abs().mean()) if len(wet) else None,
           "wet_bias_in": float((wet["m"] - wet["t"]).mean()) if len(wet) else None,
           "total_in": {"gauge": float(both["t"].sum()), "model": float(both["m"].sum())}, "thresholds": {}}
    for t in RAIN_THRESHOLDS:
        a = both["t"] >= t; b = both["m"] >= t
        hit = int((a & b).sum()); miss = int((a & ~b).sum()); false = int((~a & b).sum())
        out["thresholds"][str(t)] = {"gauge_days": int(a.sum()), "hit": hit, "miss": miss, "false": false,
                                     "pod": hit / (hit + miss) if hit + miss else None, "far": false / (hit + false) if hit + false else None}
    return out


# ── main ────────────────────────────────────────────────────────────────────

def run(refresh: bool = False) -> dict:
    served = candidates.SERVED
    input_rules = list(served.get("input_rules_post") or [])
    sources = served.get("rain_sources") or {}
    gauges = gauge_daily(input_rules).set_index("date")
    era5_hourly = pd.read_csv(RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"])
    era5_daily, era5_inten = daily_from_hourly(era5_hourly)
    labels = build_daily_labels().set_index("date")

    end = min(gauges.index.max(), era5_daily.index.max(), labels.index.max())
    dates = pd.date_range(START, end)
    wx = {}
    for m in WX_MODELS:
        h = fetch_model_hourly(m, START - pd.Timedelta(days=1), end, refresh=refresh)
        d, inten = daily_from_hourly(h)
        first = h.dropna(subset=["precip_mm"])["timestamp"].min()
        wx[m] = {"daily": d, "inten": inten, "first": str(first.date()) if pd.notna(first) else None}

    sets = [served["name"]] + [c for c in ("gb_v1",) if (MODEL_DIR / "candidates" / c / "manifest.json").exists()]
    stage1 = {s: load_stage1(s) for s in sets}

    # features per (source, scenario)
    scen_names = ["gauges"] + list(WX_MODELS)
    feats = {}
    for src in sorted(set(sources.get(b, "avg") for b in BASINS)):
        g = gauges[src]
        feats[(src, "gauges")] = scenario_rows(g, dates, None, era5_inten)
        for m in WX_MODELS:
            feats[(src, m)] = scenario_rows(g, dates, wx[m]["daily"], wx[m]["inten"])

    result = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "window": [str(dates[0].date()), str(dates[-1].date())],
              "served": {"name": served["name"], "stage1": served["stage1"], "line": served.get("line"), "weather_model": SERVED_WX},
              "input_rules": input_rules, "rain_sources": sources, "miss_weight": MISS_WEIGHT,
              "archive_first_day": {m: wx[m]["first"] for m in WX_MODELS},
              "rain": {}, "stage1": {}, "ensembles": {}, "disagreement": {}, "per_day": []}

    # rain skill per source
    for src in sorted(set(sources.get(b, "avg") for b in BASINS)):
        truth = gauges.loc[dates, src]
        result["rain"][src] = {m: rain_skill(truth, wx[m]["daily"].reindex(dates)) for m in WX_MODELS}
        result["rain"][src]["gauge_wet_days"] = int((truth >= 0.1).sum())

    # stage 1 per set × basin × scenario
    per_day = {}
    for s in sets:
        result["stage1"][s] = {}
        for b in BASINS:
            src = stage1[s][b].get("rain_source", sources.get(b, "avg"))
            if (src, "gauges") not in feats:   # a candidate trained on a source the served set does not use
                g = gauges[src]
                feats[(src, "gauges")] = scenario_rows(g, dates, None, era5_inten)
                for m in WX_MODELS:
                    feats[(src, m)] = scenario_rows(g, dates, wx[m]["daily"], wx[m]["inten"])
            covered = labels.loc[dates, f"{BASIN_NAME[b]}_covered"].fillna(0).astype(int).values == 1
            y = labels.loc[dates, f"{BASIN_NAME[b]}_csd"].fillna(0).astype(int).values[covered]
            probs = {}
            for sc in scen_names:
                p = predict(stage1[s][b], feats[(src, sc)])
                probs[sc] = p
                per_day[(s, b, sc)] = p
            block = {sc: grade(y, probs[sc][covered]) for sc in scen_names}
            arr = np.vstack([probs[m] for m in WX_MODELS])
            block["mean3"] = grade(y, arr.mean(axis=0)[covered])
            block["max3"] = grade(y, arr.max(axis=0)[covered])
            block["covered_days"] = int(covered.sum()); block["rain_source"] = src
            result["stage1"][s][b] = block

        # totals across basins at each line
        tot = {}
        for sc in scen_names + ["mean3", "max3"]:
            tot[sc] = {}
            for line in LINES:
                k = f"line_{int(line*100)}"
                tot[sc][k] = {m: int(sum(result["stage1"][s][b][sc][k][m] for b in BASINS)) for m in ("tp", "fn", "fp")}
                tot[sc][k]["cost"] = tot[sc][k]["fp"] + MISS_WEIGHT * tot[sc][k]["fn"]
        result["stage1"][s]["total"] = tot

    # disagreement as a signal (served set): days the three models' basin
    # probabilities spread by ≥ DISAGREE, and how often a discharge followed
    s = served["name"]
    for b in BASINS:
        arr = np.vstack([per_day[(s, b, m)] for m in WX_MODELS])
        spread = arr.max(axis=0) - arr.min(axis=0)
        covered = labels.loc[dates, f"{BASIN_NAME[b]}_covered"].fillna(0).astype(int).values == 1
        y = labels.loc[dates, f"{BASIN_NAME[b]}_csd"].fillna(0).astype(int).values
        dis = (spread >= DISAGREE) & covered
        agree_wet = (spread < DISAGREE) & covered & (arr.max(axis=0) >= 0.25)
        result["disagreement"][b] = {
            "days": int(dis.sum()), "discharge_days": int(y[dis].sum()),
            "agree_wet_days": int(agree_wet.sum()), "agree_wet_discharge_days": int(y[agree_wet].sum()),
            "examples": [{"date": str(d.date()), **{WX_MODELS[m]: round(float(per_day[(s, b, m)][i]), 2) for m in WX_MODELS},
                          "gauges": round(float(per_day[(s, b, "gauges")][i]), 2), "discharge": int(y[i])}
                         for i, d in enumerate(dates) if dis[i]][:12],
        }

    # per-day table for the served set (rain + probabilities), for the report's storm list
    for i, d in enumerate(dates):
        row = {"date": str(d.date())}
        wet = False
        for b in BASINS:
            src = sources.get(b, "avg")
            row[f"{b}_gauge_in"] = round(float(gauges.at[d, src]), 2)
            for sc in scen_names:
                row[f"{b}_p_{sc}"] = round(float(per_day[(s, b, sc)][i]), 3)
            row[f"{b}_csd"] = int(labels.at[d, f"{BASIN_NAME[b]}_csd"]) if d in labels.index and pd.notna(labels.at[d, f"{BASIN_NAME[b]}_csd"]) else 0
            wet = wet or row[f"{b}_gauge_in"] >= 0.5 or row[f"{b}_csd"] == 1
        for m in WX_MODELS:
            v = wx[m]["daily"].get(d, np.nan)
            row[f"{m}_in"] = round(float(v), 2) if pd.notna(v) else None
        if wet:
            result["per_day"].append(row)
    return result


# ── report ──────────────────────────────────────────────────────────────────

def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def f3(v):
    return "—" if v is None else f"{v:.3f}"


def pc(v):
    return "—" if v is None else f"{100*v:.0f}%"


def build_html(r: dict) -> str:
    css = (HERE / "stage2_explorer_template.html").read_text().split("<style>")[1].split("</style>")[0]
    s = r["served"]["name"]
    line_k = f"line_{int((r['served']['line'] or 0.25)*100)}"
    scen = ["gauges"] + list(WX_MODELS) + ["mean3", "max3"]
    label = {"gauges": "gauges (hindcast ceiling)", "mean3": "mean of the three", "max3": "max of the three", **WX_MODELS}

    # verdict from the served set's totals at the served line
    tot = r["stage1"][s]["total"]
    options = list(WX_MODELS) + ["mean3"]
    ranked = sorted(options, key=lambda m: tot[m][line_k]["cost"])
    best, served_m = ranked[0], SERVED_WX
    ceiling = tot["gauges"][line_k]["cost"]
    other_k = "line_50" if line_k != "line_50" else "line_25"
    ranked50 = sorted(options, key=lambda m: tot[m][other_k]["cost"])
    cell = lambda m, k: f"{esc(label[m])} {tot[m][k]['cost']:.0f} ({tot[m][k]['tp']} of {tot[m][k]['tp'] + tot[m][k]['fn']} caught, {tot[m][k]['fp']} false alarms)"  # noqa: E731
    verdict = (f"At the served {int(float(line_k.split('_')[1]))}% line, over {r['window'][0]} → {r['window'][1]}, the cheapest rain for tomorrow's row of the served {esc(s)} stage 1 is the "
               f"<b>{esc(label[best])}</b>: cost {tot[best][line_k]['cost']:.0f} across the four basins "
               f"({tot[best][line_k]['tp']} of {tot[best][line_k]['tp'] + tot[best][line_k]['fn']} discharge days caught, {tot[best][line_k]['fp']} false alarms). Then "
               + "; ".join(cell(m, line_k) for m in ranked[1:])
               + f". Knowing the day's gauge rain (the hindcast the Model check grades) would cost {ceiling:.0f}. "
               + f"At the {int(float(other_k.split('_')[1]))}% line the order is " + "; ".join(cell(m, other_k) for m in ranked50) + ". "
               + (f"The served choice, {esc(WX_MODELS[served_m])}, is the cheapest at the served line." if best == served_m else
                  f"The served choice, {esc(WX_MODELS[served_m])}, catches the most discharge days of any single model but pays for it in false alarms at the served line."))

    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Which weather model feeds the forecast days</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css}
tr.best td{{background:#e0f0ea}} .verdict{{background:#0072BC;color:#fff;border-radius:16px;padding:16px 20px;font-size:16px}} .verdict b{{font-size:18px}}
td.num,th.num{{text-align:right}} .small{{font-size:13px;color:#54576F}}
</style></head><body><div class="wrap">
<header><h1>Which weather model feeds the forecast days</h1>
<p class="sub">The stage 1 models were trained on the rain that fell — two NOAA gauges for daily totals, ERA5 hourly for peak intensity — so a weather model is not part of the model; it is what stands in for the gauges on the forecast days. This replays the page's <b>Tomorrow</b> row for every day since {esc(r['window'][0])}: the gauge record up to the day before, the weather model's own rain for the day (daily total and hourly peaks), the served <b>{esc(s)}</b> stage 1 on top, graded against the CIWQS discharge record. Three Open-Meteo models are archived for the whole window; the page has served ECMWF IFS since 2026-09-04.</p>
<div class="meta"><span>window {esc(r['window'][0])} → {esc(r['window'][1])}</span><span>archive starts: {esc(", ".join(f"{WX_MODELS[m]} {r['archive_first_day'][m]}" for m in WX_MODELS))}</span><span>post-training rain rules: {esc(", ".join(r['input_rules']) or 'none')}</span><span>a miss costs {r['miss_weight']:g} false alarms</span><span>generated {esc(r['generated'])}</span></div></header>
<nav><a href="#verdict">Verdict</a><a href="#rain">Rain accuracy</a><a href="#stage1">Stage 1 with each model's rain</a><a href="#ens">Ensembles</a><a href="#dis">Disagreement</a><a href="#storms">Storm days</a><a href="#caveats">Caveats</a></nav>
<section id="verdict"><div class="verdict">{verdict}</div></section>
"""]

    # rain accuracy
    parts.append('<section id="rain"><h2>Rain accuracy — each model\'s day total against the gauges</h2><p class="lead">Per rain source the served basins use (the two-gauge mean for the Westside, Downtown for the bay basins). Bias is model minus gauge over all days; wet-day error is on days the gauge read 0.1" or more. The threshold rows read: of the days the gauge reached the threshold, how many the model also did (POD), and of the days the model reached it, how many were false (FAR).</p>')
    for src, block in r["rain"].items():
        parts.append(f"<h3>{esc(src if src != 'avg' else 'Downtown + Oceanside mean')} — {block['gauge_wet_days']} wet days</h3><table><tr><th>model</th><th class='num'>bias, all days (in)</th><th class='num'>wet-day MAE (in)</th><th class='num'>wet-day bias (in)</th><th class='num'>season total, model / gauge (in)</th>"
                     + "".join(f"<th class='num'>≥ {t}\" POD / FAR</th>" for t in RAIN_THRESHOLDS) + "</tr>")
        for m in WX_MODELS:
            k = block[m]
            parts.append(f"<tr><td>{esc(WX_MODELS[m])}</td><td class='num'>{k['bias_in']:+.3f}</td><td class='num'>{f3(k['wet_mae_in'])}</td><td class='num'>{k['wet_bias_in']:+.3f}</td><td class='num'>{k['total_in']['model']:.1f} / {k['total_in']['gauge']:.1f}</td>"
                         + "".join(f"<td class='num'>{pc(k['thresholds'][str(t)]['pod'])} / {pc(k['thresholds'][str(t)]['far'])} <span class='small'>({k['thresholds'][str(t)]['gauge_days']} d)</span></td>" for t in RAIN_THRESHOLDS) + "</tr>")
        parts.append("</table>")
    parts.append("</section>")

    # stage 1
    parts.append('<section id="stage1"><h2>Stage 1 with each model\'s rain for the day</h2><p class="lead">The tomorrow scenario, graded on covered days against the filed discharge days. "gauges" is the hindcast: the gauge total for the day itself, the ceiling any forecast can reach. PR-AUC ranks the days; the line columns count caught discharge days, false alarms and cost at the two operating lines.</p>')
    for sname, sblock in r["stage1"].items():
        parts.append(f"<h3>{esc(sname)}{' (served)' if sname == s else ' (candidate)'}</h3>")
        for b in BASINS:
            k = sblock[b]
            parts.append(f"<h4>{esc(BASIN_NAME[b])} — {k['covered_days']} covered days, {k['gauges']['pos']} discharge days, rain source {esc(k['rain_source'])}</h4><table><tr><th>rain for the day</th><th class='num'>PR-AUC</th><th class='num'>ROC-AUC</th><th class='num'>Brier</th>"
                         + "".join(f"<th class='num'>{int(l*100)}%: caught</th><th class='num'>false alarms</th><th class='num'>cost</th>" for l in LINES) + "</tr>")
            costs = {sc: k[sc][line_k]["cost"] for sc in WX_MODELS}
            bestm = min(costs, key=costs.get)
            for sc in scen:
                g = k[sc]
                cls = " class='best'" if sc == bestm else ""
                parts.append(f"<tr{cls}><td>{esc(label[sc])}</td><td class='num'>{f3(g.get('pr_auc'))}</td><td class='num'>{f3(g.get('roc_auc'))}</td><td class='num'>{g['brier']:.4f}</td>"
                             + "".join(f"<td class='num'>{g[f'line_{int(l*100)}']['tp']}/{g[f'line_{int(l*100)}']['tp'] + g[f'line_{int(l*100)}']['fn']}</td><td class='num'>{g[f'line_{int(l*100)}']['fp']}</td><td class='num'>{g[f'line_{int(l*100)}']['cost']:.0f}</td>" for l in LINES) + "</tr>")
            parts.append("</table>")
        t = sblock["total"]
        parts.append("<h4>All four basins</h4><table><tr><th>rain for the day</th>" + "".join(f"<th class='num'>{int(l*100)}%: caught</th><th class='num'>false alarms</th><th class='num'>cost</th>" for l in LINES) + "</tr>")
        bestm = min(WX_MODELS, key=lambda m: t[m][line_k]["cost"])
        for sc in scen:
            cls = " class='best'" if sc == bestm else ""
            parts.append(f"<tr{cls}><td>{esc(label[sc])}</td>" + "".join(f"<td class='num'>{t[sc][f'line_{int(l*100)}']['tp']}/{t[sc][f'line_{int(l*100)}']['tp'] + t[sc][f'line_{int(l*100)}']['fn']}</td><td class='num'>{t[sc][f'line_{int(l*100)}']['fp']}</td><td class='num'>{t[sc][f'line_{int(l*100)}']['cost']:.0f}</td>" for l in LINES) + "</tr>")
        parts.append("</table>")
    parts.append("</section>")

    # ensembles
    parts.append(f'<section id="ens"><h2>Ensembles</h2><p class="lead">Two ways to use all three at once, without retraining: run stage 1 on each model\'s rain and take the <b>mean</b> of the three probabilities (a hedge), or the <b>max</b> (the cautious reading). Rain is never averaged before the model — the thresholds are nonlinear. Their rows are in the tables above; at the served line the mean costs {r["stage1"][s]["total"]["mean3"][line_k]["cost"]:.0f} and the max {r["stage1"][s]["total"]["max3"][line_k]["cost"]:.0f} across the basins, against {min(r["stage1"][s]["total"][m][line_k]["cost"] for m in WX_MODELS):.0f} for the best single model.</p></section>')

    # disagreement
    parts.append(f'<section id="dis"><h2>Disagreement as a signal</h2><p class="lead">The README\'s rule of thumb: at one to two days out the models should agree, and disagreement is itself worth flagging. Here a day "disagrees" when the three models\' basin probabilities (served set) spread by {int(DISAGREE*100)} points or more; "agree wet" is a spread under that with at least one model at 25% or more.</p><table><tr><th>basin</th><th class="num">disagreeing days</th><th class="num">…with a discharge</th><th class="num">agree-wet days</th><th class="num">…with a discharge</th></tr>')
    for b in BASINS:
        d = r["disagreement"][b]
        parts.append(f"<tr><td>{esc(BASIN_NAME[b])}</td><td class='num'>{d['days']}</td><td class='num'>{d['discharge_days']}</td><td class='num'>{d['agree_wet_days']}</td><td class='num'>{d['agree_wet_discharge_days']}</td></tr>")
    parts.append("</table>")
    for b in BASINS:
        ex = r["disagreement"][b]["examples"]
        if ex:
            parts.append(f"<h4>{esc(BASIN_NAME[b])}: the disagreeing days</h4><table><tr><th>date</th>" + "".join(f"<th class='num'>{esc(WX_MODELS[m])}</th>" for m in WX_MODELS) + "<th class='num'>gauges</th><th class='num'>discharge</th></tr>")
            for e in ex:
                parts.append(f"<tr><td>{e['date']}</td>" + "".join(f"<td class='num'>{e[WX_MODELS[m]]:.2f}</td>" for m in WX_MODELS) + f"<td class='num'>{e['gauges']:.2f}</td><td class='num'>{'yes' if e['discharge'] else ''}</td></tr>")
            parts.append("</table>")
    parts.append("</section>")

    # storm days
    parts.append('<section id="storms"><h2>Storm days — rain and served-model probability by source</h2><p class="lead">Every day a basin\'s gauge read 0.5" or more or a discharge was filed. Inches per weather model beside the gauges; then each basin\'s stage 1 probability with the gauge rain and with each model\'s rain for the day. Bold marks a filed discharge.</p><table><tr><th>date</th>'
                 + "".join(f"<th class='num'>{esc(WX_MODELS[m])} in</th>" for m in WX_MODELS)
                 + "".join(f"<th class='num'>{esc(BASIN_NAME[b])} gauge in</th><th class='num'>p gauges</th>" + "".join(f"<th class='num'>p {esc(WX_MODELS[m])}</th>" for m in WX_MODELS) for b in BASINS) + "</tr>")
    for row in r["per_day"]:
        parts.append(f"<tr><td>{row['date']}</td>" + "".join(f"<td class='num'>{'—' if row[f'{m}_in'] is None else f'{row[f'{m}_in']:.2f}'}</td>" for m in WX_MODELS)
                     + "".join((f"<td class='num'>{'<b>' if row[f'{b}_csd'] else ''}{row[f'{b}_gauge_in']:.2f}{'</b>' if row[f'{b}_csd'] else ''}</td><td class='num'>{row[f'{b}_p_gauges']:.2f}</td>"
                                + "".join(f"<td class='num'>{row[f'{b}_p_{m}']:.2f}</td>" for m in WX_MODELS)) for b in BASINS) + "</tr>")
    parts.append("</table></section>")

    parts.append("""<section id="caveats"><h2>Caveats</h2><ul>
<li><b>Lead time.</b> Open-Meteo's archive keeps the model run covering each day, a day-0 / day-1 forecast. It says nothing about the page's day-3 to day-5 rows, where the models differ most; those need the previous-runs archive, which only reaches back a few weeks.</li>
<li><b>One grid cell.</b> Every model is read at one point for the whole city, so the same number stands in for the Westside mean and for Downtown. The gauges differ by a factor of two on some storm days (Oceanside vs Downtown).</li>
<li><b>Hourly peaks.</b> ECMWF's precipitation is three-hourly, interpolated to hourly by Open-Meteo; its peak-intensity features run smoother than ERA5's. GFS and ICON are hourly natively.</li>
<li><b>Two seasons.</b> Feb 2024 to the end of the record is two wet seasons and a few dozen discharge days per basin; a few storms decide the ranking. The Oceanside record ends July 2026, the bay side August.</li>
<li><b>Stage 1 only.</b> The final beach percentage composes eight days of probabilities; only the day itself is a forecast here, so stage 2 is not part of the question.</li>
</ul></section></div></body></html>""")
    return "".join(parts)


def main():
    refresh = "--refresh" in sys.argv
    r = run(refresh=refresh)
    OUT_JSON.write_text(json.dumps(r, indent=1))
    OUT_HTML.write_text(build_html(r))
    s = r["served"]["name"]
    line_k = f"line_{int((r['served']['line'] or 0.25)*100)}"
    print(f"window {r['window'][0]} → {r['window'][1]}; archive first days {r['archive_first_day']}")
    for src, block in r["rain"].items():
        print(f"rain [{src}] wet days {block['gauge_wet_days']}: " + "; ".join(
            f"{WX_MODELS[m]} bias {block[m]['bias_in']:+.3f} wet-MAE {block[m]['wet_mae_in']:.3f} POD/FAR@0.5 {pc(block[m]['thresholds']['0.5']['pod'])}/{pc(block[m]['thresholds']['0.5']['far'])}" for m in WX_MODELS))
    for sname, sb in r["stage1"].items():
        t = sb["total"]
        print(f"[{sname}] totals @{line_k}: " + "; ".join(f"{sc} cost {t[sc][line_k]['cost']:.0f} ({t[sc][line_k]['tp']}/{t[sc][line_k]['tp'] + t[sc][line_k]['fn']} caught, {t[sc][line_k]['fp']} FA)" for sc in ["gauges"] + list(WX_MODELS) + ["mean3", "max3"]))
        for b in BASINS:
            k = sb[b]
            print(f"   {b:12s} pos {k['gauges']['pos']:3d}: " + "; ".join(f"{sc} PR {f3(k[sc].get('pr_auc'))} cost {k[sc][line_k]['cost']:.0f}" for sc in ["gauges"] + list(WX_MODELS) + ["mean3"]))
    for b in BASINS:
        d = r["disagreement"][b]
        print(f"disagree {b}: {d['days']} days, {d['discharge_days']} with a discharge; agree-wet {d['agree_wet_days']} days, {d['agree_wet_discharge_days']} with a discharge")
    print(f"wrote {OUT_JSON.relative_to(REPO)} and {OUT_HTML.relative_to(REPO)}")


if __name__ == "__main__":
    main()
