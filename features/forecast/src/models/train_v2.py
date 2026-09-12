#!/usr/bin/env python3
"""
Model Training Pipeline v2 — ground-truth CSD labels.

Trains per-basin P(discharge today | rain) classifiers on SFPUC's reported
discharge events (data/csd/, see RETRAIN_PLAN.md) instead of the bacteria
proxy label v1 used. Also fits:
  - a volume head (log1p MG regression on event days), and
  - the Stage-2 impact table: P(basin bacteria elevated | days since
    discharge, discharge size), from beach samples joined to real events.

Design rules:
  - Feature engineering is IDENTICAL to v1 (train.py) — live_dashboard.py
    computes these features at inference; do not diverge.
  - Days in months without trustworthy CSD coverage are dropped, never
    treated as negatives (csd_labels.py coverage discipline).
  - Validation is season-grouped (leave-one-wet-season-out) plus a
    chronological holdout. No shuffled K-fold: adjacent storm days leak.

Artifacts → data/models/v2/ with the same pkl schema live_dashboard.py loads
({"model", "features", "calibration_offset", ...}), so promotion is a file
copy once metrics are accepted.
"""

import json
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "collectors"))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from csd_labels import APP_BASINS, build_daily_labels  # noqa: E402
from shared.stations import STATIONS  # noqa: E402

DATA_DIR = Path(__file__).parent.parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
V2_DIR = DATA_DIR / "models" / "v2"
V1_DIR = DATA_DIR / "models"

TRAIN_END = pd.Timestamp("2025-10-31")      # public CSD coverage ends here
HOLDOUT_START = pd.Timestamp("2023-07-01")  # chronological test = last 2 wet seasons

# Storms every credible model must flag (all have reported discharges)
MARQUEE_STORMS = [
    "2021-10-24",  # bomb cyclone — all 7 Westside outfalls discharged
    "2022-12-31",  # New Year's Eve storm (2nd-wettest day on record)
    "2023-01-04", "2023-01-09", "2023-01-14",  # January 2023 atmospheric rivers
    "2023-03-09", "2023-03-21",
    "2024-02-04",
]

BASIN_KEYS = {"Westside": "westside", "North Shore": "north_shore",
              "Southeast": "southeast"}
# Superseded by train_v4.py (2026-09), which adds the Central basin. Kept as
# the reference implementation; restrict to the basins this script models.
APP_BASINS = [b for b in APP_BASINS if b in BASIN_KEYS]

# Stage-2 site groups: beaches within one basin respond differently to a
# discharge, so the impact table is fit per group (basin display = worst
# group). Membership is validated against the canonical registry
# (shared/stations.py) — before 2026-09 this table carried the same
# mislabeled ids the app did ("Baker-China" actually pointed at Mission
# Creek/Crane Cove, "Southeast" at a phantom BAY#315_SL). BAY#220_SL
# (Mission Creek) is deliberately excluded: its discharges come from the
# Central (Mission Creek) basin, which has no stage-1 model — mirrors
# OBSERVED_STATION_BASIN in live_dashboard.py leaving id 4618 unmapped.
SITE_GROUPS = {
    "Ocean Beach": ("Westside", ["OCEAN#18_SL", "OCEAN#19_SL", "OCEAN#20_SL",
                                 "OCEAN#21_SL", "OCEAN#21.1_SL", "OCEAN#22_SL"]),
    "Baker-China": ("Westside", ["OCEAN#15_SL", "OCEAN#15EAST_SL",
                                 "OCEAN#16_SL", "OCEAN#17_SL"]),
    "Crissy Field": ("North Shore", ["BAY#202.4_SL", "BAY#202.5_SL"]),
    "Aquatic Park": ("North Shore", ["BAY#210.1_SL", "BAY#211_SL"]),
    "Southeast": ("Southeast", ["BAY#230_SL", "BAY#300.1_SL", "BAY#301.1_SL",
                                "BAY#301.2_SL", "BAY#320_SL"]),
}
assert all(STATIONS[sid].basin == basin
           for basin, sids in SITE_GROUPS.values() for sid in sids), \
    "SITE_GROUPS disagrees with the shared/stations.py registry"
assert {sid for _, sids in SITE_GROUPS.values() for sid in sids} \
    == set(STATIONS) - {"BAY#220_SL"}, \
    "SITE_GROUPS must cover every registry station except Mission Creek"


# ── Features — ONE implementation, shared with serving: rain_features.py ────
from rain_features import DAILY_FEATURES, INTENSITY_FEATURES, add_daily_features, hourly_intensity  # noqa: E402


def get_feature_columns() -> list:
    return list(DAILY_FEATURES)


def get_feature_columns_v21() -> list:
    """v2 features + Open-Meteo hourly intensity maxima. The intensity
    features come from the SAME source at train and inference time
    (Open-Meteo hourly), avoiding v1's precip_max scale-mismatch trap."""
    return get_feature_columns() + list(INTENSITY_FEATURES)


def build_hourly_features() -> pd.DataFrame:
    """Per-date peak-intensity features from Open-Meteo hourly rain."""
    h = pd.read_csv(RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"])
    return hourly_intensity(h)


def build_rain_features() -> pd.DataFrame:
    """Daily features from the two-gauge mean (SF Downtown + SF Oceanside)."""
    rain_df = pd.read_csv(RAW_DIR / "historical_rain.csv", parse_dates=["date"])
    rp = rain_df.pivot_table(index="date", columns="rain_station_name",
                             values="precip_inches", aggfunc="first").reset_index()
    rp.columns.name = None
    precip_cols = [c for c in rp.columns if c != "date"]
    rp["precip_inches"] = rp[precip_cols].mean(axis=1).fillna(0)
    rp = add_daily_features(rp.sort_values("date").reset_index(drop=True))
    return rp[["date"] + get_feature_columns()]


def wet_season(dates: pd.Series) -> pd.Series:
    """Season label: Jul 2016 – Jun 2017 → 2016, etc."""
    return dates.dt.year.where(dates.dt.month >= 7, dates.dt.year - 1)


def build_dataset() -> pd.DataFrame:
    feats = build_rain_features()
    hourly = build_hourly_features()
    labels = build_daily_labels()
    df = labels.merge(feats, on="date", how="inner")
    df = df.merge(hourly, on="date", how="left")
    df[INTENSITY_FEATURES] = df[INTENSITY_FEATURES].fillna(0)
    df = df[df["date"] <= TRAIN_END].reset_index(drop=True)
    df["season"] = wet_season(df["date"])
    return df


# ── Training / evaluation ───────────────────────────────────────────────────

MODEL_PARAMS = dict(n_estimators=200, max_depth=3, learning_rate=0.04,
                    min_samples_leaf=20, subsample=0.8, random_state=42)


def target_frame(df: pd.DataFrame, basin: str) -> pd.DataFrame:
    """Rows where this target's label is trustworthy."""
    if basin == "citywide":
        sub = df[df["fully_covered"] == 1].copy()
        sub["y"] = sub["csd_any"]
    else:
        sub = df[df[f"{basin}_covered"] == 1].copy()
        sub["y"] = sub[f"{basin}_csd"]
    return sub


def season_cv_scores(sub: pd.DataFrame, features: list) -> dict:
    """Leave-one-wet-season-out CV."""
    rows = []
    for season in sorted(sub["season"].unique()):
        tr, te = sub[sub["season"] != season], sub[sub["season"] == season]
        if te["y"].sum() == 0 or tr["y"].sum() < 5:
            continue
        m = GradientBoostingClassifier(**MODEL_PARAMS)
        m.fit(tr[features], tr["y"])
        p = m.predict_proba(te[features])[:, 1]
        rows.append({
            "season": int(season), "n": len(te), "pos": int(te["y"].sum()),
            "roc_auc": roc_auc_score(te["y"], p),
            "pr_auc": average_precision_score(te["y"], p),
            "brier": brier_score_loss(te["y"], p),
        })
    per = pd.DataFrame(rows)
    return {
        "per_season": rows,
        "roc_auc": float(per["roc_auc"].mean()) if len(per) else None,
        "pr_auc": float(per["pr_auc"].mean()) if len(per) else None,
        "brier": float(per["brier"].mean()) if len(per) else None,
    }


def holdout_scores(sub: pd.DataFrame, features: list) -> dict:
    """Chronological: train < HOLDOUT_START, test after."""
    tr = sub[sub["date"] < HOLDOUT_START]
    te = sub[sub["date"] >= HOLDOUT_START]
    if te["y"].sum() == 0 or tr["y"].sum() < 5:
        return {}
    m = GradientBoostingClassifier(**MODEL_PARAMS)
    m.fit(tr[features], tr["y"])
    p = m.predict_proba(te[features])[:, 1]
    return {
        "n_test": len(te), "pos_test": int(te["y"].sum()),
        "roc_auc": roc_auc_score(te["y"], p),
        "pr_auc": average_precision_score(te["y"], p),
        "brier": brier_score_loss(te["y"], p),
    }


def persistence_baseline(sub: pd.DataFrame) -> dict:
    """Field-standard null model: predict today = yesterday's label
    (Virtual Beach convention). The bar any real model must clear."""
    te = sub[sub["date"] >= HOLDOUT_START].sort_values("date")
    y, yprev = te["y"].values[1:], te["y"].values[:-1]
    if y.sum() == 0:
        return {}
    tp = int(((yprev == 1) & (y == 1)).sum())
    fp = int(((yprev == 1) & (y == 0)).sum())
    fn = int(((yprev == 0) & (y == 1)).sum())
    return {
        "brier": brier_score_loss(y, yprev.astype(float)),
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def v1_baseline_scores(sub: pd.DataFrame, v1_name: str) -> dict:
    """Score the production (proxy-trained) model on the same real labels.

    Note v1's training data (bacteria through Mar 2026) OVERLAPS this window,
    so v1 gets an in-sample advantage here; v2 numbers on the same window are
    from the chronological holdout fit. If v2 still wins, the gap is real.
    """
    path = V1_DIR / f"{v1_name}_model.pkl"
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        md = pickle.load(f)
    te = sub[sub["date"] >= HOLDOUT_START]
    if te["y"].sum() == 0:
        return {}
    X = te[md["features"]].fillna(0)
    raw = md["model"].predict_proba(X)[:, 1]
    # same rain-scaled calibration the dashboard applies
    rain_factor = np.clip(1.0 - te["rain_3d_cum"].fillna(0).values * 2.0, 0, 1)
    p = np.clip(raw - md.get("calibration_offset", 0) * rain_factor, 0, 1)
    return {
        "roc_auc": roc_auc_score(te["y"], p),
        "pr_auc": average_precision_score(te["y"], p),
        "brier": brier_score_loss(te["y"], p),
    }


def fit_final(sub: pd.DataFrame, features: list) -> dict:
    m = GradientBoostingClassifier(**MODEL_PARAMS)
    m.fit(sub[features], sub["y"])
    p = m.predict_proba(sub[features])[:, 1]
    dry = sub["precip_avg"] < 0.01
    offset = round(float(p[dry.values].mean()), 4) if dry.sum() else 0.0
    importances = sorted(zip(features, m.feature_importances_),
                         key=lambda x: -x[1])
    return {"model": m, "calibration_offset": offset,
            "importances": [(f, round(float(i), 4)) for f, i in importances]}


def empirical_thresholds(df: pd.DataFrame) -> dict:
    """P(discharge | same-day rain bin) per basin — the sourced threshold table."""
    bins = [(0.0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75),
            (0.75, 1.0), (1.0, 1.5), (1.5, 99.0)]
    out = {}
    for basin in APP_BASINS + ["citywide"]:
        sub = target_frame(df, basin if basin != "citywide" else "citywide")
        rows = []
        for lo, hi in bins:
            b = sub[(sub["precip_avg"] >= lo) & (sub["precip_avg"] < hi)]
            if len(b) < 5:
                continue
            rows.append({"rain_lo": lo, "rain_hi": hi, "n_days": len(b),
                         "p_discharge": round(float(b["y"].mean()), 3)})
        out[basin] = rows
    return out


def fit_volume_heads(df: pd.DataFrame, features: list) -> dict:
    """log1p(volume MG) regression on event days, per basin."""
    heads = {}
    for basin in APP_BASINS:
        sub = target_frame(df, basin)
        ev = sub[sub["y"] == 1]
        if len(ev) < 20:
            continue
        m = GradientBoostingRegressor(n_estimators=150, max_depth=2,
                                      learning_rate=0.05, min_samples_leaf=8,
                                      random_state=42)
        yv = np.log1p(ev[f"{basin}_volume_mg"])
        m.fit(ev[features], yv)
        resid = yv - m.predict(ev[features])
        heads[basin] = {"model": m, "features": features,
                        "target": "log1p_volume_mg",
                        "n_events": len(ev),
                        "resid_std": round(float(resid.std()), 3)}
    return heads


def fit_impact_table(df: pd.DataFrame) -> dict:
    """Stage 2: P(beach-group bacteria elevated | days since discharge, size).

    Fit per SITE GROUP (module-level SITE_GROUPS), not per basin: different
    beaches in one basin sit in different flushing regimes, and one
    basin-wide curve blurred them. Discharge events/volumes still come from
    the group's parent basin (stage 1 is hydraulics); only the beach response
    is group-specific. Empirical buckets; the dashboard applies isotonic
    smoothing at load.
    """
    bact = pd.read_csv(RAW_DIR / "historical_bacteria.csv", parse_dates=["sample_date"])
    table = {}
    for group, (basin, stations) in SITE_GROUPS.items():
        sub = df[df[f"{basin}_covered"] == 1][["date", f"{basin}_csd", f"{basin}_volume_mg"]]
        events = sub[sub[f"{basin}_csd"] == 1].set_index("date")[f"{basin}_volume_mg"]
        med = float(events.median()) if len(events) else 0.0

        b = bact[bact["station"].isin(stations)]
        daily = b.groupby("sample_date").agg(elevated=("exceeds_standard", "max")).reset_index()
        daily = daily[(daily["sample_date"] >= sub["date"].min()) &
                      (daily["sample_date"] <= sub["date"].max())]

        def last_event(d):
            past = events[(events.index <= d) & (events.index >= d - pd.Timedelta(days=7))]
            if past.empty:
                return None, None
            last = past.index.max()
            return (d - last).days, float(past.loc[last])

        rows = []
        for _, r in daily.iterrows():
            days_since, vol = last_event(r["sample_date"])
            rows.append({"elevated": bool(r["elevated"]), "days_since": days_since,
                         "large": (vol is not None and vol >= med)})
        dd = pd.DataFrame(rows)

        def bucket(d):
            if d is None or (isinstance(d, float) and np.isnan(d)):
                return "none_7d"
            d = int(d)
            if d <= 3:
                return str(d)
            return "4-5" if d <= 5 else "6-7"

        dd["bucket"] = dd["days_since"].map(bucket)
        out = {}
        base = dd[dd["bucket"] == "none_7d"]
        out["baseline_no_recent_discharge"] = {
            "p_elevated": round(float(base["elevated"].mean()), 3), "n": len(base)}
        for buck in ["0", "1", "2", "3", "4-5", "6-7"]:
            for size, mask in [("large", dd["large"]), ("small", ~dd["large"])]:
                g = dd[(dd["bucket"] == buck) & mask]
                if len(g) >= 3:
                    out[f"d{buck}_{size}"] = {
                        "p_elevated": round(float(g["elevated"].mean()), 3), "n": len(g)}
        table[group] = {"basin": basin, "stations": stations,
                        "median_event_volume_mg": round(med, 2), "buckets": out}
    return table


def backtest(df: pd.DataFrame, finals: dict, features: list) -> dict:
    """Marquee storms must score high; verified-zero stretches must stay low;
    count real-event days with meaningful rain that score <10%."""
    out = {"marquee": [], "dry_month_avg": None, "critical_fn": []}
    fully = df[df["fully_covered"] == 1]
    probs = {name: f["model"].predict_proba(fully[features])[:, 1]
             for name, f in finals.items()}

    for d in MARQUEE_STORMS:
        row = fully[fully["date"] == d]
        if row.empty:
            continue
        i = row.index[0]
        pos = fully.index.get_loc(i)
        out["marquee"].append({
            "date": d, "rain": round(float(row["precip_avg"].iloc[0]), 2),
            "citywide_p": round(float(probs["citywide"][pos]), 3),
            "actual_any": int(row["csd_any"].iloc[0]),
        })

    dry = fully[fully["rain_7d_cum"] < 0.05]
    out["dry_days_n"] = len(dry)
    out["dry_day_avg_citywide_p"] = round(
        float(probs["citywide"][[fully.index.get_loc(i) for i in dry.index]].mean()), 4)

    cw = fully.reset_index(drop=True)
    p = probs["citywide"]
    for i, r in cw.iterrows():
        if r["csd_any"] == 1 and r["rain_3d_cum"] > 0.25 and p[i] < 0.10:
            out["critical_fn"].append({"date": str(r["date"].date()),
                                       "p": round(float(p[i]), 3),
                                       "rain_3d": round(float(r["rain_3d_cum"]), 2)})
    return out


def main(feature_set: str = "v21"):
    """feature_set: 'v2' (daily only) or 'v21' (+ hourly intensity). Both are
    evaluated side by side regardless; `feature_set` picks the final fit."""
    print("=" * 64)
    print("FORECAST v2 TRAINING — ground-truth CSD labels")
    print("=" * 64)
    df = build_dataset()
    feat_sets = {"v2": get_feature_columns(), "v21": get_feature_columns_v21()}
    features = feat_sets[feature_set]
    print(f"dataset: {len(df)} days {df['date'].min().date()} → {df['date'].max().date()}")
    print(f"final-fit feature set: {feature_set}")

    report = {"trained_at": datetime.now().isoformat(),
              "feature_set": feature_set,
              "train_window": [str(df['date'].min().date()), str(df['date'].max().date())],
              "targets": {}}
    finals = {}

    targets = [("citywide", "citywide", "citywide")] + \
              [(b, BASIN_KEYS[b], BASIN_KEYS[b]) for b in APP_BASINS]

    for basin, key, v1_name in targets:
        sub = target_frame(df, basin)
        print(f"\n── {basin}: {len(sub)} covered days, {int(sub['y'].sum())} event days")
        evals = {}
        for fs_name, fs in feat_sets.items():
            cv = season_cv_scores(sub, fs)
            ho = holdout_scores(sub, fs)
            evals[fs_name] = {"season_cv": cv, "holdout": ho}
            print(f"   [{fs_name:>3}] season-CV: ROC {cv['roc_auc']:.3f}  PR {cv['pr_auc']:.3f}  "
                  f"Brier {cv['brier']:.4f}" +
                  (f" | holdout: ROC {ho['roc_auc']:.3f}  PR {ho['pr_auc']:.3f}  "
                   f"Brier {ho['brier']:.4f}" if ho else ""))
        # NOTE: the one-time v1 (proxy-model) comparison was removed after
        # v2 was promoted over the v1 pkls it loaded (it began comparing the
        # production model against itself in-sample). The recorded v1
        # numbers live in RETRAIN_PLAN.md: holdout PR-AUC citywide 0.56,
        # Westside 0.54, North Shore 0.91*, Southeast 0.28.
        pers = persistence_baseline(sub)
        if pers:
            print(f"   persistence (y=yesterday): Brier {pers['brier']:.4f}  "
                  f"precision {pers['precision']:.2f}  recall {pers['recall']:.2f}")
        final = fit_final(sub, features)
        finals[key] = final
        print(f"   final fit : dry-day offset {final['calibration_offset']:.4f}; "
              f"top: {', '.join(f for f, _ in final['importances'][:4])}")
        report["targets"][key] = {
            "n_days": len(sub), "n_events": int(sub["y"].sum()),
            "feature_set_evals": evals,
            "season_cv": evals[feature_set]["season_cv"],
            "holdout": evals[feature_set]["holdout"],
            "persistence_baseline_on_holdout": pers,
            "calibration_offset": final["calibration_offset"],
            "top_features": final["importances"][:8],
        }

    print("\n── volume heads / impact table / thresholds / backtest")
    volume_heads = fit_volume_heads(df, features)
    impact = fit_impact_table(df)
    thresholds = empirical_thresholds(df)
    bt = backtest(df, finals, features)
    report["backtest"] = bt
    for m in bt["marquee"]:
        print(f"   {m['date']}  rain {m['rain']:.2f}\"  citywide P {m['citywide_p']:.0%}"
              f"  actual={'CSD' if m['actual_any'] else 'none'}")
    print(f"   dry days (7d rain <0.05\", n={bt['dry_days_n']}): "
          f"avg citywide P {bt['dry_day_avg_citywide_p']:.2%}")
    print(f"   critical FN (event, 3d rain >0.25\", P<10%): {len(bt['critical_fn'])}")

    # ── save ──
    V2_DIR.mkdir(parents=True, exist_ok=True)
    for key, final in finals.items():
        with open(V2_DIR / f"{key}_model.pkl", "wb") as f:
            pickle.dump({
                "model": final["model"], "features": features,
                "calibration_offset": final["calibration_offset"],
                "label": "csd_event_reported",
                "auc": report["targets"][key]["season_cv"]["roc_auc"],
                "trained_at": report["trained_at"],
            }, f)
    for basin, head in volume_heads.items():
        with open(V2_DIR / f"{BASIN_KEYS[basin]}_volume.pkl", "wb") as f:
            pickle.dump(head, f)
    with open(V2_DIR / "impact_table.json", "w") as f:
        json.dump(impact, f, indent=2)
    with open(V2_DIR / "thresholds.json", "w") as f:
        json.dump(thresholds, f, indent=2)
    with open(V2_DIR / "eval_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n💾 artifacts → {V2_DIR}/")
    return report


if __name__ == "__main__":
    main()
