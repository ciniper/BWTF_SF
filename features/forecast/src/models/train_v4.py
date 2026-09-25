#!/usr/bin/env python3
"""
FORECAST v4 TRAINING — four basins, regional rain, the Poo Bot archive, and a
scorecard for "model vs what happened".

What changed from v3 (train_v2.py, promoted 2026-09-02):

  * Central basin. Mission Creek's outfalls (CSD-018..028, 52% of citywide
    discharge volume) get their own stage-1 model; BAY#220 Mission Creek gets
    its own stage-2 group. Every registry station is now served.
  * Regional rain. Each basin is evaluated on the citywide two-gauge average
    (v3) AND on its local gauge — Oceanside 047767 for Westside, Downtown
    047772 for the bay basins — and keeps whichever scores better on the
    chronological holdout. The chosen source is stored with the model so
    serving feeds the same gauge.
  * Poo Bot archive (data/poobot/). SFPUC's own feed, polled twice daily
    Mar 2016 – Jan 2017: Westside discharge onsets for a season CIWQS does not
    cover (Oceanside reports start Jan 2018) and 1,097 ENTERO samples from
    3.5 years before DataSF's floor for the stage-2 impact table. Gated by a
    recall check against CIWQS Bayside events.
  * Volume-unknown events. Archive events carry no volume; the volume head's
    prediction stands in (exactly what serving does), and they are excluded
    from fitting the volume heads themselves.
  * Scorecard artifact. For every day of the training span: rain, per-basin
    P(discharge) from the final model and from the holdout-fit model, the
    composed per-group and per-zone risk, and what actually happened —
    discharges (with the beaches they post, via shared/outfalls.py) and
    bacteria exceedances. Feeds /forecast's "model check" view.

Run:  venv/bin/python features/forecast/src/models/train_v4.py
Artifacts → data/models/v4/ (promote by copying into data/models/).
"""
from __future__ import annotations

import json
import pickle
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from train_v2 import (  # noqa: E402  — shared formulas, kept in lockstep
    HOLDOUT_START, INTENSITY_FEATURES, MARQUEE_STORMS, MODEL_PARAMS, TRAIN_END,
    DATA_DIR, RAW_DIR, build_hourly_features, fit_final, get_feature_columns,
    get_feature_columns_v21, persistence_baseline, season_cv_scores, wet_season,
)
from csd_labels import APP_BASINS, build_daily_labels, load_events  # noqa: E402
from impact import compose, smooth_table  # noqa: E402
from shared.outfalls import FEED_NAME_TO_OUTFALLS, OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

V4_DIR = DATA_DIR / "models" / "v4"
SERVE_DIR = DATA_DIR / "models"
POOBOT_DIR = DATA_DIR / "poobot"
TRAIN_START = pd.Timestamp("2016-03-01")
ARCHIVE_START, ARCHIVE_END = pd.Timestamp("2016-03-19"), pd.Timestamp("2017-01-10")
ARCHIVE_MIN_RECALL = 0.75   # feed must have shown ≥75% of CIWQS Bayside event-days

from groups import BASIN_KEYS, GROUPS_BY_BASIN, SITE_GROUPS, ZONE_GROUPS  # noqa: E402  (shared with serving)
from scorecard import zone_confusion  # noqa: E402  (shared with serving)

# Which ACIS gauge sees each basin's rain (both are in data/raw/historical_rain.csv)
LOCAL_GAUGE = {"Westside": "SF Oceanside", "North Shore": "SF Downtown",
               "Central": "SF Downtown", "Southeast": "SF Downtown"}
RAIN_SOURCES = ["avg", "SF Oceanside", "SF Downtown"]
# adopt the local gauge only if it clearly wins on the holdout
ADOPT_PR_GAIN, ADOPT_BRIER_SLACK = 0.01, 0.0005

from shared.standards import STANDARDS, flag_exceedances, parse_result  # noqa: E402  (one source for 'over standard')


# ── Rain features, per source ───────────────────────────────────────────────

def rain_series(source: str) -> tuple:
    """Daily rain (inches) for `source` ('avg' or a gauge name) + fill note."""
    rain_df = pd.read_csv(RAW_DIR / "historical_rain.csv", parse_dates=["date"])
    rp = rain_df.pivot_table(index="date", columns="rain_station_name",
                             values="precip_inches", aggfunc="first").sort_index()
    cols = list(rp.columns)
    if source == "avg":
        return rp[cols].mean(axis=1).fillna(0.0), {"filled_from_other_gauge": 0}
    other = [c for c in cols if c != source][0]
    s = rp[source]
    filled = int(s.isna().sum() - (s.isna() & rp[other].isna()).sum())
    return s.fillna(rp[other]).fillna(0.0), {"filled_from_other_gauge": filled}


def rain_features(source: str) -> pd.DataFrame:
    """Daily features over an arbitrary daily series — the shared formula
    (src/models/rain_features.py), the same one serving applies."""
    from rain_features import add_daily_features
    s, _ = rain_series(source)
    rp = add_daily_features(pd.DataFrame({"date": s.index, "precip_inches": s.values}))
    return rp[["date"] + get_feature_columns()]


# ── Poo Bot archive → labels ────────────────────────────────────────────────

def archive_tables() -> dict:
    status = pd.read_csv(POOBOT_DIR / "feed_status.csv", parse_dates=["snapshot"])
    onsets = pd.read_csv(POOBOT_DIR / "discharge_onsets.csv", parse_dates=["date", "snapshot"])
    status["date"] = status["snapshot"].dt.normalize()
    covered = sorted(status["date"].unique())
    # active basins per snapshot date (a continuing discharge is "active", not an onset)
    active = {}
    for _, r in status.iterrows():
        for name in str(r["cso_structures"] or "").split("|"):
            if name and name != "nan":
                for oid in FEED_NAME_TO_OUTFALLS.get(name, []):
                    active.setdefault(r["date"], set()).add(OUTFALLS[oid].basin)
    onsets = onsets[onsets["mapped"] == True].copy()  # noqa: E712
    onsets["basin"] = onsets["basin"].str.split("|").str[0]
    return {"covered_dates": pd.DatetimeIndex(covered), "active": active, "onsets": onsets}


def archive_recall(arch: dict) -> dict:
    """Did the feed show the CIWQS-reported Bayside discharges during the
    archive window? (same day or the next snapshot day)"""
    ev = load_events()
    ev = ev[(ev["event_date"] >= ARCHIVE_START) & (ev["event_date"] <= ARCHIVE_END)
            & (ev["app_basin"] != "Westside")]
    days = ev.groupby(["app_basin", "event_date"]).size().index
    hit = 0
    for basin, d in days:
        if basin in arch["active"].get(d, ()) or basin in arch["active"].get(d + pd.Timedelta(days=1), ()):
            hit += 1
    flagged = {(b, d) for d, bs in arch["active"].items() for b in bs if b != "Westside"}
    ciwqs = {(b, d) for b, d in days}
    ciwqs_pm1 = ciwqs | {(b, d - pd.Timedelta(days=1)) for b, d in ciwqs} | {(b, d + pd.Timedelta(days=1)) for b, d in ciwqs}
    precision = sum(1 for f in flagged if f in ciwqs_pm1) / len(flagged) if flagged else None
    return {"ciwqs_bayside_event_days": len(days), "seen_in_feed": hit,
            "recall": round(hit / len(days), 3) if len(days) else None,
            "feed_flag_days_bayside": len(flagged),
            "precision_vs_ciwqs_pm1d": round(precision, 3) if precision is not None else None}


def apply_archive_labels(df: pd.DataFrame, arch: dict, rain_avg: pd.Series) -> dict:
    """Mark archive dates covered where CIWQS is not, and stamp onset days.

    Onset shift: the feed was polled ~07:00 and ~15:00, so a discharge that
    began the previous evening first shows in the morning snapshot. When the
    morning snapshot shows a new structure and yesterday was the rainy day
    (≥0.2", more than today), the event is dated yesterday — the convention
    CIWQS event_date uses.
    """
    notes = {"shifted_onsets": 0, "onset_days": {}, "new_covered_days": {}}
    covered = df["date"].isin(arch["covered_dates"])
    rain = rain_avg.reindex(df["date"]).fillna(0).values
    date_pos = {d: i for i, d in enumerate(df["date"])}
    for basin in APP_BASINS:
        newly = covered & (df[f"{basin}_covered"] == 0)
        df.loc[newly, f"{basin}_covered"] = 1
        df.loc[newly, f"{basin}_volume_known"] = 0
        df.loc[newly, f"{basin}_label_source"] = "poobot"
        notes["new_covered_days"][basin] = int(newly.sum())
        ons = arch["onsets"][arch["onsets"]["basin"] == basin]
        stamped = set()
        for _, r in ons.iterrows():
            d = r["date"].normalize()
            i = date_pos.get(d)
            if i is None:
                continue
            if r["snapshot"].hour < 12 and i > 0 and rain[i - 1] >= 0.2 and rain[i - 1] > rain[i]:
                d, i = df["date"].iloc[i - 1], i - 1
                notes["shifted_onsets"] += 1
            if df.at[i, f"{basin}_label_source"] != "poobot":
                continue  # CIWQS already covers this day
            df.at[i, f"{basin}_csd"] = 1
            df.at[i, f"{basin}_outfalls"] = max(int(df.at[i, f"{basin}_outfalls"]), len(str(r["outfall_ids"]).split("|")))
            stamped.add(d)
        notes["onset_days"][basin] = sorted(str(d.date()) for d in stamped)
    return notes


# ── Dataset ─────────────────────────────────────────────────────────────────

def build_dataset() -> tuple:
    """Returns ({rain_source: feature+label frame}, notes). Label columns are
    identical across sources; only the rain features differ."""
    labels = build_daily_labels()
    days = pd.DataFrame({"date": pd.date_range(TRAIN_START, TRAIN_END)})
    df = days.merge(labels, on="date", how="left")
    for basin in APP_BASINS:
        for col, fill in ((f"{basin}_csd", 0), (f"{basin}_volume_mg", 0.0), (f"{basin}_outfalls", 0), (f"{basin}_covered", 0)):
            df[col] = df[col].fillna(fill)
        df[f"{basin}_volume_known"] = df[f"{basin}_covered"].astype(int)
        df[f"{basin}_label_source"] = np.where(df[f"{basin}_covered"] == 1, "ciwqs", "")

    rain_avg, _ = rain_series("avg")
    arch = archive_tables()
    recall = archive_recall(arch)
    notes = {"archive_recall": recall, "archive_used": False}
    if recall["recall"] is not None and recall["recall"] >= ARCHIVE_MIN_RECALL:
        notes["archive_used"] = True
        notes["archive_labels"] = apply_archive_labels(df, arch, rain_avg)

    cov_cols = [f"{b}_covered" for b in APP_BASINS]
    df["csd_any"] = (df[[f"{b}_csd" for b in APP_BASINS]].sum(axis=1) > 0).astype(int)
    df["csd_volume_mg"] = df[[f"{b}_volume_mg" for b in APP_BASINS]].sum(axis=1)
    df["csd_outfalls"] = df[[f"{b}_outfalls" for b in APP_BASINS]].sum(axis=1)
    df["fully_covered"] = (df[cov_cols].sum(axis=1) == len(cov_cols)).astype(int)
    df["season"] = wet_season(df["date"])

    hourly = build_hourly_features()
    out = {}
    for src in RAIN_SOURCES:
        f = df.merge(rain_features(src), on="date", how="left").merge(hourly, on="date", how="left")
        f[INTENSITY_FEATURES] = f[INTENSITY_FEATURES].fillna(0)
        f[get_feature_columns()] = f[get_feature_columns()].fillna(0)
        out[src] = f.reset_index(drop=True)
        notes[f"rain_{src}"] = rain_series(src)[1]
    return out, notes


def target_frame(df: pd.DataFrame, basin: str) -> pd.DataFrame:
    if basin == "citywide":
        sub = df[df["fully_covered"] == 1].copy()
        sub["y"] = sub["csd_any"]
    else:
        sub = df[df[f"{basin}_covered"] == 1].copy()
        sub["y"] = sub[f"{basin}_csd"]
    return sub


# ── Stage 1 ─────────────────────────────────────────────────────────────────

def fit_holdout_model(sub: pd.DataFrame, features: list):
    tr = sub[sub["date"] < HOLDOUT_START]
    if tr["y"].sum() < 5:
        return None
    m = GradientBoostingClassifier(**MODEL_PARAMS)
    m.fit(tr[features], tr["y"])
    return m


def holdout_scores(sub: pd.DataFrame, features: list, model=None) -> dict:
    te = sub[sub["date"] >= HOLDOUT_START]
    model = model or fit_holdout_model(sub, features)
    if model is None or te["y"].sum() == 0:
        return {}
    p = model.predict_proba(te[features])[:, 1]
    return {"n_test": len(te), "pos_test": int(te["y"].sum()),
            "roc_auc": roc_auc_score(te["y"], p), "pr_auc": average_precision_score(te["y"], p),
            "brier": brier_score_loss(te["y"], p)}


def calibrated(model_data: dict, X: pd.DataFrame) -> np.ndarray:
    """The dashboard's rain-scaled dry-day offset (live_dashboard._predict_calibrated)."""
    raw = model_data["model"].predict_proba(X[model_data["features"]])[:, 1]
    rain_factor = np.clip(1.0 - X["rain_3d_cum"].values * 2.0, 0.0, 1.0)
    return np.clip(raw - model_data["calibration_offset"] * rain_factor, 0.0, 1.0)


def fit_volume_heads(frames: dict, chosen: dict, features: list) -> dict:
    heads = {}
    for basin in APP_BASINS:
        sub = target_frame(frames[chosen[basin]], basin)
        ev = sub[(sub["y"] == 1) & (sub[f"{basin}_volume_known"] == 1)]
        if len(ev) < 20:
            continue
        m = GradientBoostingRegressor(n_estimators=150, max_depth=2, learning_rate=0.05,
                                      min_samples_leaf=8, random_state=42)
        yv = np.log1p(ev[f"{basin}_volume_mg"])
        m.fit(ev[features], yv)
        resid = yv - m.predict(ev[features])
        heads[basin] = {"model": m, "features": features, "target": "log1p_volume_mg",
                        "rain_source": chosen[basin], "n_events": len(ev),
                        "resid_std": round(float(resid.std()), 3)}
    return heads


def predicted_volume(head: dict, X: pd.DataFrame) -> np.ndarray:
    return np.maximum(0.0, np.expm1(head["model"].predict(X[head["features"]])))


def empirical_thresholds(frames: dict, chosen: dict) -> dict:
    bins = [(0.0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 1.5), (1.5, 99.0)]
    out = {}
    for basin in APP_BASINS + ["citywide"]:
        sub = target_frame(frames[chosen.get(basin, "avg")], basin)
        rows = []
        for lo, hi in bins:
            b = sub[(sub["precip_avg"] >= lo) & (sub["precip_avg"] < hi)]
            if len(b) >= 5:
                rows.append({"rain_lo": lo, "rain_hi": hi, "n_days": len(b),
                             "p_discharge": round(float(b["y"].mean()), 3)})
        out[basin] = rows
    return out


# ── Stage 2 ─────────────────────────────────────────────────────────────────

def _parse_value(raw) -> float | None:
    s = str(raw).strip()
    try:
        if s.startswith("<"):
            return float(s[1:]) / 2
        if s.startswith(">"):
            return float(s[1:])
        return float(s)
    except ValueError:
        return None


def load_samples() -> pd.DataFrame:
    """Per-sample exceedances: DataSF (2020-07 →) plus the Poo Bot archive
    (2015-12 → 2017-01), deduplicated on (station, date, analyte). "Over
    standard" is recomputed here from the raw values with the shared rule
    (shared/standards.py, including the total-coliform ratio rule) rather
    than trusted from the collector's stored column."""
    b = pd.read_csv(RAW_DIR / "historical_bacteria.csv", parse_dates=["sample_date"])
    b = b[["station", "sample_date", "analyte", "value_raw"]].rename(columns={"value_raw": "data"}).assign(source="datasf")
    p = pd.read_csv(POOBOT_DIR / "samples.csv", parse_dates=["sample_date"])
    p = p[p["source"].isin(STATIONS) & p["analyte"].isin(STANDARDS)].rename(columns={"source": "station"})
    p = p[["station", "sample_date", "analyte", "data"]].assign(source="poobot")
    allrows = pd.concat([b, p])
    allrows = allrows[allrows["analyte"].isin(STANDARDS)].copy()
    allrows["value"] = allrows["data"].map(parse_result)
    # Same-day resamples exist (e.g. two fecal results for one station and
    # day). Keep the HIGHEST per (station, date, analyte): stage 2 asks "was
    # the beach elevated that day", so any exceedance must survive the dedupe.
    # DataSF wins ties over the archive.
    allrows = allrows.sort_values(["value", "source"], ascending=[False, True], na_position="last")
    allrows = allrows.drop_duplicates(["station", "sample_date", "analyte"], keep="first").reset_index(drop=True)
    recs = allrows.rename(columns={"sample_date": "date"}).to_dict("records")
    flag_exceedances(recs)
    allrows["exceeds_standard"] = [r["exceeds"] for r in recs]
    return allrows[["station", "sample_date", "analyte", "exceeds_standard", "source", "value"]]


def fit_impact_table(frames: dict, chosen: dict, heads: dict, samples: pd.DataFrame) -> tuple:
    """P(group's beaches elevated | days since the basin's last discharge, size).
    Unknown volumes (archive events) use the volume head's prediction — the
    same thing serving does. Also returns the outfall-count diagnostic."""
    table, outfall_diag = {}, {}
    for group, (basin, stations) in SITE_GROUPS.items():
        df = frames[chosen[basin]]
        sub = df[df[f"{basin}_covered"] == 1]
        ev = sub[sub[f"{basin}_csd"] == 1].copy()
        if basin in heads:
            pred = predicted_volume(heads[basin], ev)
            ev["vol"] = np.where(ev[f"{basin}_volume_known"] == 1, ev[f"{basin}_volume_mg"], pred)
        else:
            ev["vol"] = ev[f"{basin}_volume_mg"]
        events = ev.set_index("date")["vol"]
        n_out = ev.set_index("date")[f"{basin}_outfalls"]
        known = ev[ev[f"{basin}_volume_known"] == 1][f"{basin}_volume_mg"]
        med = float(known.median()) if len(known) else 0.0

        smp = samples[samples["station"].isin(stations)]
        daily = smp.groupby("sample_date").agg(elevated=("exceeds_standard", "max")).reset_index()
        daily = daily[(daily["sample_date"] >= sub["date"].min()) & (daily["sample_date"] <= sub["date"].max())]
        # only sample days where the basin's label is known
        daily = daily[daily["sample_date"].isin(sub["date"])]

        rows = []
        for _, r in daily.iterrows():
            d = r["sample_date"]
            past = events[(events.index <= d) & (events.index >= d - pd.Timedelta(days=7))]
            if past.empty:
                rows.append({"elevated": bool(r["elevated"]), "days_since": None, "large": False, "multi": False})
            else:
                last = past.index.max()
                rows.append({"elevated": bool(r["elevated"]), "days_since": (d - last).days,
                             "large": float(past.loc[last]) >= med, "multi": int(n_out.loc[last]) >= 2})
        dd = pd.DataFrame(rows)

        def bucket(d):
            if d is None or (isinstance(d, float) and np.isnan(d)):
                return "none_7d"
            d = int(d)
            return str(d) if d <= 3 else ("4-5" if d <= 5 else "6-7")

        dd["bucket"] = dd["days_since"].map(bucket)
        out = {}
        base = dd[dd["bucket"] == "none_7d"]
        out["baseline_no_recent_discharge"] = {"p_elevated": round(float(base["elevated"].mean()), 3) if len(base) else 0.0, "n": len(base)}
        for buck in ["0", "1", "2", "3", "4-5", "6-7"]:
            for size, mask in [("large", dd["large"]), ("small", ~dd["large"])]:
                g = dd[(dd["bucket"] == buck) & mask]
                if len(g) >= 3:
                    out[f"d{buck}_{size}"] = {"p_elevated": round(float(g["elevated"].mean()), 3), "n": len(g)}
        table[group] = {"basin": basin, "stations": stations, "median_event_volume_mg": round(med, 2),
                        "n_sample_days": int(len(dd)), "buckets": out}
        near = dd[dd["days_since"].notna() & (dd["days_since"] <= 2)]
        outfall_diag[group] = {
            "single_outfall": {"p_elevated": round(float(near[~near["multi"]]["elevated"].mean()), 3) if (~near["multi"]).sum() else None, "n": int((~near["multi"]).sum())},
            "multi_outfall": {"p_elevated": round(float(near[near["multi"]]["elevated"].mean()), 3) if near["multi"].sum() else None, "n": int(near["multi"].sum())},
        }
    return table, outfall_diag


# ── Scorecard: model vs what happened, every day ───────────────────────────

def posted_stations_by_day(frames: dict, arch: dict, archive_used: bool) -> dict:
    """date → {registry station id} that a discharge that day would post
    (CIWQS events via the outfall registry; archive onsets via feed names)."""
    ev = load_events()
    out = {}
    for _, r in ev.iterrows():
        o = OUTFALLS.get(r["outfall_id"])
        if o:
            out.setdefault(r["event_date"].normalize(), set()).update(o.stations)
    if archive_used:
        for _, r in arch["onsets"].iterrows():
            for oid in str(r["outfall_ids"]).split("|"):
                if oid in OUTFALLS:
                    out.setdefault(r["date"].normalize(), set()).update(OUTFALLS[oid].stations)
    by_sfpuc = {s.sfpuc_id: sid for sid, s in STATIONS.items()}
    return {d: {by_sfpuc[x] for x in sids if x in by_sfpuc} for d, sids in out.items()}


def build_scorecard(frames: dict, chosen: dict, finals: dict, holdout_models: dict, heads: dict,
                    impact_raw: dict, samples: pd.DataFrame, arch: dict, archive_used: bool, features: list) -> dict:
    table = smooth_table(impact_raw)
    base = frames["avg"]
    n = len(base)
    dates = list(base["date"])
    day_probs, day_probs_h, day_vols = [dict() for _ in range(n)], [dict() for _ in range(n)], [dict() for _ in range(n)]
    for basin in APP_BASINS:
        key = BASIN_KEYS[basin]
        X = frames[chosen[basin]]
        p = calibrated(finals[key], X)
        ph = calibrated({**finals[key], "model": holdout_models[key]}, X) if holdout_models.get(key) is not None else None
        v = predicted_volume(heads[basin], X) if basin in heads else np.zeros(n)
        for i in range(n):
            day_probs[i][key] = float(p[i])
            day_vols[i][key] = float(v[i])
            if ph is not None and dates[i] >= HOLDOUT_START:
                day_probs_h[i][key] = float(ph[i])
    cw = calibrated(finals["citywide"], base)

    posted = posted_stations_by_day(frames, arch, archive_used)
    smp = samples.copy()
    smp["group"] = smp["station"].map({sid: g for g, (_, sids) in SITE_GROUPS.items() for sid in sids})
    elevated = smp.groupby(["sample_date", "group"]).agg(elevated=("exceeds_standard", "max"), n=("exceeds_standard", "size"))
    elevated_idx = {(d, g): (bool(e), int(k)) for (d, g), (e, k) in elevated.iterrows()}
    station_zone = {sid: zk for zk, groups in ZONE_GROUPS.items() for g in groups for sid in SITE_GROUPS[g][1]}

    days = []
    for i, d in enumerate(dates):
        row = base.iloc[i]
        per_basin, per_group = compose(table, GROUPS_BY_BASIN, day_probs, day_vols, i)
        if day_probs_h[i]:
            probs_h = [day_probs_h[j] if day_probs_h[j] else day_probs[j] for j in range(n)]
            _, per_group_h = compose(table, GROUPS_BY_BASIN, probs_h, day_vols, i)
        else:
            per_group_h = None
        basins = {}
        for basin in APP_BASINS:
            key = BASIN_KEYS[basin]
            covered = int(row[f"{basin}_covered"]) == 1
            basins[key] = {"p": round(day_probs[i][key], 3),
                           "ph": round(day_probs_h[i][key], 3) if key in day_probs_h[i] else None,
                           "y": int(row[f"{basin}_csd"]) if covered else None,
                           "vol": round(float(row[f"{basin}_volume_mg"]), 2) if covered and row[f"{basin}_volume_known"] else None,
                           "outfalls": int(row[f"{basin}_outfalls"]) if covered else None,
                           "src": row[f"{basin}_label_source"] or None}
        groups = {}
        for g in SITE_GROUPS:
            e = elevated_idx.get((d, g))
            groups[g] = {"risk": per_group[g], "risk_h": per_group_h[g] if per_group_h else None,
                         "elevated": e[0] if e else None, "n_samples": e[1] if e else 0}
        posted_today = posted.get(d, set())
        zones = {}
        for zk, zgroups in ZONE_GROUPS.items():
            zb = {SITE_GROUPS[g][0] for g in zgroups}
            covered = all(int(row[f"{b}_covered"]) == 1 for b in zb)
            posts = any(station_zone.get(s) == zk for s in posted_today)
            el = [groups[g]["elevated"] for g in zgroups if groups[g]["elevated"] is not None]
            zones[zk] = {"risk": max(per_group[g] for g in zgroups),
                         "risk_h": max(per_group_h[g] for g in zgroups) if per_group_h else None,
                         "discharge": (posts if covered else None),
                         "elevated": (any(el) if el else None)}
        days.append({"date": str(d.date()), "season": int(row["season"]), "rain": round(float(row["precip_avg"]), 3),
                     "rain_by_gauge": {src: round(float(frames[src].iloc[i]["precip_avg"]), 3) for src in RAIN_SOURCES if src != "avg"},
                     "citywide_p": round(float(cw[i]), 3), "basins": basins, "groups": groups, "zones": zones})

    # zone confusion on the holdout window, model = holdout-fit — the same
    # function serving uses to time-box the scorecard (src/models/scorecard.py)
    confusion = zone_confusion(days, list(ZONE_GROUPS), holdout_only=True)

    return {"trained_at": datetime.now().isoformat(), "holdout_start": str(HOLDOUT_START.date()),
            "span": [days[0]["date"], days[-1]["date"]],
            "basins": {BASIN_KEYS[b]: {"name": b, "rain_source": chosen[b]} for b in APP_BASINS},
            "groups": {g: {"basin": BASIN_KEYS[b], "stations": sids} for g, (b, sids) in SITE_GROUPS.items()},
            "zones": {zk: {"label": ZONES[zk].label, "groups": gs} for zk, gs in ZONE_GROUPS.items()},
            "zone_confusion_holdout": confusion, "days": days}


# ── Backtest ────────────────────────────────────────────────────────────────

def backtest(base: pd.DataFrame, finals: dict, exclude: pd.Series = None) -> dict:
    """Marquee storms must score high, verified-dry stretches low; count real
    event days with meaningful rain that score <10%. `exclude` masks days
    whose labels stage 1 chose not to trust (archive-only basins)."""
    fully = base[(base["fully_covered"] == 1) & ~(exclude if exclude is not None else False)].reset_index(drop=True)
    p = calibrated(finals["citywide"], fully)
    out = {"marquee": [], "critical_fn": []}
    for d in MARQUEE_STORMS:
        m = fully.index[fully["date"] == d]
        if len(m):
            i = m[0]
            out["marquee"].append({"date": d, "rain": round(float(fully.at[i, "precip_avg"]), 2),
                                   "citywide_p": round(float(p[i]), 3), "actual_any": int(fully.at[i, "csd_any"])})
    dry = fully["rain_7d_cum"] < 0.05
    out["dry_days_n"] = int(dry.sum())
    out["dry_day_avg_citywide_p"] = round(float(p[dry.values].mean()), 4) if dry.sum() else None
    for i, r in fully.iterrows():
        if r["csd_any"] == 1 and r["rain_3d_cum"] > 0.25 and p[i] < 0.10:
            out["critical_fn"].append({"date": str(r["date"].date()), "p": round(float(p[i]), 3), "rain_3d": round(float(r["rain_3d_cum"]), 2)})
    return out


# ── Main ────────────────────────────────────────────────────────────────────

def main(promote: bool = False) -> dict:
    print("=" * 64)
    print("FORECAST v4 TRAINING — four basins · regional rain · Poo Bot archive")
    print("=" * 64)
    frames, notes = build_dataset()
    features = get_feature_columns_v21()
    base = frames["avg"]
    print(f"dataset: {len(base)} days {base['date'].min().date()} → {base['date'].max().date()}")
    ar = notes["archive_recall"]
    print(f"archive recall vs CIWQS Bayside: {ar['seen_in_feed']}/{ar['ciwqs_bayside_event_days']} = {ar['recall']}"
          f"  (precision ±1d {ar['precision_vs_ciwqs_pm1d']}) → archive labels {'USED' if notes['archive_used'] else 'SKIPPED'}")
    if notes["archive_used"]:
        al = notes["archive_labels"]
        print(f"   new covered days {al['new_covered_days']}; onset days {{{', '.join(f'{b}: {len(v)}' for b, v in al['onset_days'].items())}}}; shifted {al['shifted_onsets']}")

    report = {"trained_at": datetime.now().isoformat(), "version": "v4", "feature_set": "v21",
              "train_window": [str(base["date"].min().date()), str(base["date"].max().date())],
              "notes": notes, "targets": {}, "rain_source_chosen": {}}
    finals, holdout_models, chosen, stage1_archive = {}, {}, {}, {}

    for basin in APP_BASINS:
        key = BASIN_KEYS[basin]
        cands = {}
        for src in ("avg", LOCAL_GAUGE[basin]):
            sub = target_frame(frames[src], basin)
            hm = fit_holdout_model(sub, features)
            cands[src] = {"season_cv": season_cv_scores(sub, features), "holdout": holdout_scores(sub, features, hm), "_model": hm, "_sub": sub}
        a, l = cands["avg"]["holdout"], cands[LOCAL_GAUGE[basin]]["holdout"]
        use_local = bool(a and l and l["pr_auc"] >= a["pr_auc"] + ADOPT_PR_GAIN and l["brier"] <= a["brier"] + ADOPT_BRIER_SLACK)
        src = LOCAL_GAUGE[basin] if use_local else "avg"
        chosen[basin] = src
        sub = cands[src]["_sub"]
        n_arch = int((sub[f"{basin}_label_source"] == "poobot").sum())
        print(f"\n── {basin}: {len(sub)} covered days, {int(sub['y'].sum())} event days ({n_arch} archive-covered days)")
        for s, c in cands.items():
            cv, ho = c["season_cv"], c["holdout"]
            print(f"   [{s:>12}] season-CV: ROC {cv['roc_auc']:.3f}  PR {cv['pr_auc']:.3f}  Brier {cv['brier']:.4f}"
                  + (f" | holdout: ROC {ho['roc_auc']:.3f}  PR {ho['pr_auc']:.3f}  Brier {ho['brier']:.4f}" if ho else ""))
        print(f"   → rain source: {src}")
        # Archive ablation. The holdout is 2023+, so this measures whether the
        # extra 2016-17 season helps or hurts generalization; a basin whose
        # holdout gets clearly WORSE with the archive labels is fit without
        # them (stage 1 only — stage 2 still uses the feed's posting dates).
        ablation, use_archive = None, True
        if notes["archive_used"] and n_arch:
            no_arch = sub[sub[f"{basin}_label_source"] != "poobot"]
            ablation = {"with_archive": cands[src]["holdout"], "without_archive": holdout_scores(no_arch, features)}
            w, wo = ablation["with_archive"], ablation["without_archive"]
            if w and wo:
                use_archive = not (wo["pr_auc"] >= w["pr_auc"] + ADOPT_PR_GAIN)
                print(f"   archive ablation (holdout): with PR {w['pr_auc']:.3f} Brier {w['brier']:.4f} | "
                      f"without PR {wo['pr_auc']:.3f} Brier {wo['brier']:.4f} → stage 1 {'uses' if use_archive else 'DROPS'} archive labels")
            if not use_archive:
                sub = no_arch
                cands[src]["season_cv"] = season_cv_scores(sub, features)
                cands[src]["_model"] = fit_holdout_model(sub, features)
                cands[src]["holdout"] = holdout_scores(sub, features, cands[src]["_model"])
        pers = persistence_baseline(sub)
        final = fit_final(sub, features)
        finals[key] = {**final, "features": features}
        holdout_models[key] = cands[src]["_model"]
        stage1_archive[basin] = use_archive
        print(f"   final fit : dry-day offset {final['calibration_offset']:.4f}; top: {', '.join(f for f, _ in final['importances'][:4])}")
        report["targets"][key] = {"basin": basin, "n_days": len(sub), "n_events": int(sub["y"].sum()),
                                  "rain_source": src, "candidates": {s: {"season_cv": c["season_cv"], "holdout": c["holdout"]} for s, c in cands.items()},
                                  "season_cv": cands[src]["season_cv"], "holdout": cands[src]["holdout"],
                                  "archive_ablation": ablation, "stage1_uses_archive_labels": use_archive,
                                  "persistence_baseline_on_holdout": pers,
                                  "calibration_offset": final["calibration_offset"], "top_features": final["importances"][:8]}
        report["rain_source_chosen"][key] = src

    # citywide (avg rain); archive-covered days only if every basin kept them
    sub = target_frame(base, "citywide")
    if not all(stage1_archive.values()):
        sub = sub[~(sub[[f"{b}_label_source" for b in APP_BASINS]] == "poobot").any(axis=1)]
    report["stage1_archive_labels"] = stage1_archive
    cv, hm = season_cv_scores(sub, features), fit_holdout_model(sub, features)
    ho = holdout_scores(sub, features, hm)
    final = fit_final(sub, features)
    finals["citywide"] = {**final, "features": features}
    holdout_models["citywide"] = hm
    chosen["citywide"] = "avg"
    print(f"\n── citywide: {len(sub)} fully-covered days, {int(sub['y'].sum())} event days")
    print(f"   season-CV: ROC {cv['roc_auc']:.3f}  PR {cv['pr_auc']:.3f}  Brier {cv['brier']:.4f} | holdout: ROC {ho['roc_auc']:.3f}  PR {ho['pr_auc']:.3f}  Brier {ho['brier']:.4f}")
    report["targets"]["citywide"] = {"n_days": len(sub), "n_events": int(sub["y"].sum()), "rain_source": "avg",
                                     "season_cv": cv, "holdout": ho, "calibration_offset": final["calibration_offset"],
                                     "top_features": final["importances"][:8]}

    print("\n── volume heads / impact table / thresholds / scorecard")
    heads = fit_volume_heads(frames, chosen, features)
    samples = load_samples()
    impact_raw, outfall_diag = fit_impact_table(frames, chosen, heads, samples)
    thresholds = empirical_thresholds(frames, chosen)
    untrusted = pd.Series(False, index=base.index)
    for b, keep in stage1_archive.items():
        if not keep:
            untrusted |= base[f"{b}_label_source"] == "poobot"
    bt = backtest(base, finals, untrusted)
    report["backtest"] = bt
    report["outfall_count_diagnostic"] = outfall_diag
    report["samples"] = {"n": len(samples), "datasf": int((samples["source"] == "datasf").sum()),
                         "poobot": int((samples["source"] == "poobot").sum()),
                         "span": [str(samples["sample_date"].min().date()), str(samples["sample_date"].max().date())]}
    for g, t in impact_raw.items():
        b = t["buckets"]
        print(f"   {g:14} median event {t['median_event_volume_mg']:>7.2f} MG · {t['n_sample_days']} sample days · baseline {b['baseline_no_recent_discharge']['p_elevated']:.2f} (n={b['baseline_no_recent_discharge']['n']})"
              + "".join(f" · d{k} {b[f'd{k}_large']['p_elevated']:.2f}/{b[f'd{k}_small']['p_elevated']:.2f}" for k in ("0", "1", "2") if f"d{k}_large" in b and f"d{k}_small" in b))
    for m in bt["marquee"]:
        print(f"   {m['date']}  rain {m['rain']:.2f}\"  citywide P {m['citywide_p']:.0%}  actual={'CSD' if m['actual_any'] else 'none'}")
    print(f"   dry days (7d rain <0.05\", n={bt['dry_days_n']}): avg citywide P {bt['dry_day_avg_citywide_p']:.2%}")
    print(f"   critical FN (event, 3d rain >0.25\", P<10%): {len(bt['critical_fn'])}")

    scorecard = build_scorecard(frames, chosen, finals, holdout_models, heads, impact_raw, samples,
                                archive_tables(), notes["archive_used"], features)
    for zk, c in scorecard["zone_confusion_holdout"].items():
        d = c["0.25"]["vs_discharge_posting"]; b = c["0.25"]["vs_bacteria_elevated"]
        rec = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else float("nan")
        far = d["fp"] / (d["fp"] + d["tn"]) if d["fp"] + d["tn"] else float("nan")
        brec = b["tp"] / (b["tp"] + b["fn"]) if b["tp"] + b["fn"] else float("nan")
        print(f"   zone {zk:12} @25%: discharge recall {rec:.2f}, false-alarm rate {far:.3f} | bacteria recall {brec:.2f} (n={sum(b.values())})")
    report["zone_confusion_holdout"] = scorecard["zone_confusion_holdout"]

    # ── save ──
    V4_DIR.mkdir(parents=True, exist_ok=True)
    for key, final in finals.items():
        with open(V4_DIR / f"{key}_model.pkl", "wb") as f:
            pickle.dump({"model": final["model"], "features": features, "calibration_offset": final["calibration_offset"],
                         "label": "csd_event_reported", "rain_source": chosen.get(next((b for b, k in BASIN_KEYS.items() if k == key), "citywide"), "avg"),
                         "auc": report["targets"][key]["season_cv"]["roc_auc"], "trained_at": report["trained_at"], "version": "v4"}, f)
    for basin, head in heads.items():
        with open(V4_DIR / f"{BASIN_KEYS[basin]}_volume.pkl", "wb") as f:
            pickle.dump(head, f)
    (V4_DIR / "impact_table.json").write_text(json.dumps(impact_raw, indent=2))
    (V4_DIR / "thresholds.json").write_text(json.dumps(thresholds, indent=2))
    (V4_DIR / "eval_report.json").write_text(json.dumps(report, indent=2, default=str))
    import gzip
    with gzip.open(V4_DIR / "scorecard.json.gz", "wt") as f:
        json.dump(scorecard, f, separators=(",", ":"), default=str)
    for stale in (V4_DIR / "scorecard.json", SERVE_DIR / "scorecard.json"):
        if stale.exists():
            stale.unlink()
    print(f"\nartifacts → {V4_DIR}/")
    if promote:
        for p in V4_DIR.iterdir():
            if p.suffix in (".pkl", ".json", ".gz"):
                shutil.copy2(p, SERVE_DIR / p.name)
        print(f"promoted → {SERVE_DIR}/")
    return report


if __name__ == "__main__":
    main(promote="--promote" in sys.argv)
