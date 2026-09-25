#!/usr/bin/env python3
"""Model explorer — one self-contained HTML page that shows the served v4
forecast exactly as it is: the rain gauges and live feeds, the 19 input
features and their formulas, every basin model's trees (exported so the page
runs them in the browser, with a what-if rain editor), the volume heads, the
stage-2 impact table and composition, the basins → groups → zones map, and a
self-check that the in-page arithmetic matches scikit-learn on real storms.

    venv/bin/python features/forecast/src/models/export_model_explorer.py
    → reports/2026-09_forecast_v4_model_explorer.html  (served at /reports/…)

Re-run after retraining or `train_v4.py --rescore` so the page matches the
served pickles. Everything numeric on the page comes from the pickles, the
impact table, data/raw and the scorecard artifact — nothing is retyped.
"""
from __future__ import annotations

import gzip
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import train_v4 as T  # noqa: E402  (frame builder, calibration, volume — the training code itself)
from groups import BASIN_KEYS, GROUPS_BY_BASIN, SITE_GROUPS, ZONE_GROUPS  # noqa: E402
from impact import smooth_table  # noqa: E402
from rain_features import DAILY_FEATURES, INTENSITY_FEATURES  # noqa: E402
from shared.outfalls import OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

REPO = HERE.parents[3]
SERVE_DIR = T.SERVE_DIR
RAW_DIR = T.RAW_DIR
OUT = REPO / "reports" / "2026-09_forecast_v4_model_explorer.html"
TEMPLATE = HERE / "model_explorer_template.html"

ACIS_GAUGES = {"SF Downtown": "047772", "SF Oceanside": "047767"}
# what the page shows if ACIS metadata is unreachable (values fetched 2026-09-24)
GAUGE_META_FALLBACK = {
    "047772": {"name": "SAN FRANCISCO DOWNTOWN", "lat": 37.7705, "lon": -122.4269, "elev_ft": 150.0, "valid_through": "2026-09-23"},
    "047767": {"name": "SAN FRANCISCO OCEANSIDE", "lat": 37.728, "lon": -122.5052, "elev_ft": 8.0, "valid_through": "2026-08-17"},
}
SAMPLE_DAYS = ["2025-11-13", "2025-12-22", "2025-12-25", "2026-01-05", "2026-02-16", "2026-02-19",
               "2026-04-11", "2025-10-13", "2024-01-13", "2023-01-04", "2026-03-15"]
HISTORY_DAYS = 120   # trailing daily rain shipped per sample day (features look back 30; composition 7 more)


def export_tree(tree) -> dict:
    """Compact node arrays: children, split feature index, threshold, leaf value."""
    # thresholds and leaf values at full float64 precision: sklearn compares
    # float32(x) <= threshold, and a threshold rounded to 6 dp flips splits
    return {"l": tree.children_left.tolist(), "r": tree.children_right.tolist(),
            "f": tree.feature.tolist(),
            "t": [float(x) for x in tree.threshold],
            "v": [float(x) for x in tree.value[:, 0, 0]]}


def export_gb(gb, features) -> dict:
    X0 = pd.DataFrame([{f: 0.0 for f in features}])
    init = float(gb._raw_predict_init(X0[features].values)[0, 0]) if hasattr(gb, "_raw_predict_init") else None
    if init is None:  # regressor / older sklearn
        init = float(gb.init_.constant_[0, 0])
    return {"n_estimators": int(gb.n_estimators), "max_depth": int(gb.max_depth), "learning_rate": float(gb.learning_rate),
            "init_raw": init, "trees": [export_tree(gb.estimators_[k, 0].tree_) for k in range(gb.n_estimators)],
            "importances": {f: round(float(i), 5) for f, i in zip(features, gb.feature_importances_)},
            "params": {k: v for k, v in gb.get_params().items() if k in ("min_samples_leaf", "subsample", "random_state", "loss")}}


def gauge_meta() -> dict:
    out = {sid: dict(m) for sid, m in GAUGE_META_FALLBACK.items()}
    try:
        r = requests.post("https://data.rcc-acis.org/StnMeta", json={"sids": ",".join(ACIS_GAUGES.values()),
                          "meta": "name,ll,elev,valid_daterange", "elems": "pcpn"}, timeout=30)
        for m in r.json().get("meta", []):
            sid = next((s for s in ACIS_GAUGES.values() if any(x.startswith(s) for x in m.get("sids", []) or [])), None)
            if sid is None:  # match on name
                sid = next((s for s, f in GAUGE_META_FALLBACK.items() if f["name"] == m.get("name")), None)
            if sid:
                out[sid] = {"name": m["name"], "lat": m["ll"][1], "lon": m["ll"][0], "elev_ft": m.get("elev"),
                            "valid_through": (m.get("valid_daterange") or [[None, None]])[0][1]}
    except Exception as exc:  # noqa: BLE001
        print("ACIS StnMeta unavailable, using cached metadata:", exc)
    return out


def gauge_series(days: int = 420) -> dict:
    r = pd.read_csv(RAW_DIR / "historical_rain.csv", parse_dates=["date"])
    p = r.pivot_table(index="date", columns="rain_station_name", values="precip_inches", aggfunc="first").sort_index()
    p = p[p.index >= p.index.max() - pd.Timedelta(days=days)]
    series = {"dates": [str(d.date()) for d in p.index]}
    for name in ACIS_GAUGES:
        series[name] = [None if pd.isna(v) else round(float(v), 2) for v in p[name]] if name in p else []
    # outage runs: one gauge stuck at exactly 0.00 for ≥2 days while the other totals ≥0.5"
    runs = []
    for g, o in (("SF Oceanside", "SF Downtown"), ("SF Downtown", "SF Oceanside")):
        z = (p[g] == 0)
        start = None
        for d, v in list(z.items()) + [(None, False)]:
            if v and start is None:
                start = d
            if not v and start is not None:
                end = (d - pd.Timedelta(days=1)) if d is not None else p.index[-1]
                tot = float(p.loc[start:end, o].sum())
                if (end - start).days + 1 >= 2 and tot >= 0.5:
                    runs.append({"gauge": g, "start": str(start.date()), "end": str(end.date()),
                                 "days": int((end - start).days + 1), "other_total": round(tot, 2)})
                start = None
    series["outages"] = runs
    return series


def main() -> None:
    features = T.get_feature_columns_v21()
    models, heads, rain_source = {}, {}, {}
    for basin in T.APP_BASINS + ["citywide"]:
        key = BASIN_KEYS.get(basin, "citywide")
        with open(SERVE_DIR / f"{key}_model.pkl", "rb") as f:
            m = pickle.load(f)
        assert m["features"] == features, key
        models[key] = {**export_gb(m["model"], features), "calibration_offset": float(m["calibration_offset"]),
                       "rain_source": m.get("rain_source", "avg"), "trained_at": m.get("trained_at"), "version": m.get("version"),
                       "basin": basin}
        rain_source[key] = m.get("rain_source", "avg")
        vp = SERVE_DIR / f"{key}_volume.pkl"
        if vp.exists():
            with open(vp, "rb") as f:
                h = pickle.load(f)
            heads[key] = {**export_gb(h["model"], h["features"]), "n_events": h.get("n_events"), "resid_std": h.get("resid_std"),
                          "target": h.get("target"), "rain_source": h.get("rain_source")}
    ev = json.loads((SERVE_DIR / "eval_report.json").read_text())
    impact_raw = json.loads((SERVE_DIR / "impact_table.json").read_text())
    with gzip.open(SERVE_DIR / "scorecard.json.gz", "rt") as f:
        sc = json.load(f)
    sc_by_date = {d["date"]: d for d in sc["days"]}

    # frames exactly as training/rescore builds them → sample days with history + sklearn answers
    end = pd.Timestamp(sc["span"][1])
    frames, notes = T.build_dataset(end=end)
    finals = {k: {"model": pickle.load(open(SERVE_DIR / f"{k}_model.pkl", "rb"))["model"], "features": features,
                  "calibration_offset": models[k]["calibration_offset"]} for k in models}
    head_objs = {b: pickle.load(open(SERVE_DIR / f"{BASIN_KEYS[b]}_volume.pkl", "rb")) for b in T.APP_BASINS
                 if (SERVE_DIR / f"{BASIN_KEYS[b]}_volume.pkl").exists()}
    base = frames["avg"]
    samples = []
    for ds in SAMPLE_DAYS:
        d = pd.Timestamp(ds)
        idx = base.index[base["date"] == d]
        if not len(idx):
            print("sample day outside frames:", ds)
            continue
        i = int(idx[0])
        lo = max(0, i - HISTORY_DAYS + 1)
        hist = {"dates": [str(x.date()) for x in base["date"].iloc[lo:i + 1]]}
        # full precision on purpose: a value rounded to 4 dp can sit on the other
        # side of a tree split and move a probability by whole points
        for src in T.RAIN_SOURCES:
            hist[src] = [float(v) for v in frames[src]["precip_avg"].iloc[lo:i + 1]]   # the source's daily total
        hist["intensity"] = {f: [float(v) for v in base[f].iloc[lo:i + 1]] for f in INTENSITY_FEATURES}
        feats = {src: {f: float(frames[src].iloc[i][f]) for f in features} for src in T.RAIN_SOURCES}
        expected = {}
        for basin in T.APP_BASINS + ["citywide"]:
            key = BASIN_KEYS.get(basin, "citywide")
            X = frames[rain_source[key]].iloc[[i]]
            expected[key] = {"p": round(float(T.calibrated(finals[key], X)[0]), 6)}
            if basin in head_objs:
                expected[key]["volume_mg"] = round(float(T.predicted_volume(head_objs[basin], X)[0]), 4)
        stored = sc_by_date.get(ds, {})
        samples.append({"date": ds, "season": int(base.iloc[i]["season"]), "history": hist, "features": feats,
                        "expected": expected,
                        "stored": {"zones": {zk: z.get("risk") for zk, z in stored.get("zones", {}).items()},
                                   "groups": {g: v.get("risk") for g, v in stored.get("groups", {}).items()},
                                   "basins": {k: {"y": v.get("y"), "vol": v.get("vol"), "src": v.get("src")} for k, v in stored.get("basins", {}).items()},
                                   "post_training": bool(stored.get("post_training"))},
                        "labels": {b: {"discharge": int(base.iloc[i][f"{b}_csd"]) if int(base.iloc[i][f"{b}_covered"]) else None,
                                       "volume_mg": round(float(base.iloc[i][f"{b}_volume_mg"]), 2), "outfalls": int(base.iloc[i][f"{b}_outfalls"])}
                                   for b in T.APP_BASINS}})

    by_sfpuc = {s.sfpuc_id: sid for sid, s in STATIONS.items()}
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "trained_at": ev.get("trained_at"), "version": ev.get("version"), "feature_set": ev.get("feature_set"),
        "train_window": ev.get("train_window"), "holdout_start": sc.get("holdout_start"),
        "trained_through": sc.get("trained_through") or sc["span"][1], "rescored_at": sc.get("rescored_at"), "span": sc["span"],
        "features": {"daily": DAILY_FEATURES, "intensity": INTENSITY_FEATURES, "all": features},
        "model_params": {k: v for k, v in T.MODEL_PARAMS.items()},
        "models": models, "volume_heads": heads,
        "holdout": {k: {"pr_auc": t["holdout"].get("pr_auc"), "roc_auc": t["holdout"].get("roc_auc"), "brier": t["holdout"].get("brier"),
                        "n_test": t["holdout"].get("n_test"), "pos_test": t["holdout"].get("pos_test"), "n_events": t.get("n_events"), "n_days": t.get("n_days")}
                    for k, t in ev.get("targets", {}).items() if t.get("holdout")},
        "impact_raw": impact_raw, "impact_smoothed": smooth_table(impact_raw),
        "basins": {BASIN_KEYS[b]: {"name": b, "rain_source": rain_source[BASIN_KEYS[b]],
                                   "outfalls": sorted(o.id for o in OUTFALLS.values() if o.basin == b),
                                   "groups": GROUPS_BY_BASIN[BASIN_KEYS[b]]} for b in T.APP_BASINS},
        "groups": {g: {"basin": BASIN_KEYS[b], "stations": [{"id": s, "name": STATIONS[s].name} for s in sids]}
                   for g, (b, sids) in SITE_GROUPS.items()},
        "zones": {zk: {"label": ZONES[zk].label, "groups": gs, "gauge": getattr(ZONES[zk], "gauge", None),
                       "stations": [STATIONS[s].name for s in ZONES[zk].source_ids]} for zk, gs in ZONE_GROUPS.items()},
        "gauges": {sid: {**meta, "label": name} for name, sid in ACIS_GAUGES.items() for m_sid, meta in gauge_meta().items() if m_sid == sid},
        "gauge_series": gauge_series(),
        "live_feeds": [
            {"role": "Past complete days — daily totals", "source": "NOAA daily gauges via ACIS (data.rcc-acis.org/StnData)",
             "detail": "Station 047772 SF Downtown and 047767 SF Oceanside. 'avg' = their mean; a gauge source = that gauge, falling back to the other gauge, then to the hourly total. Missing (M/S) is left missing; trace (T) is 0. These are the exact series the models were trained on (data/raw/historical_rain.csv)."},
            {"role": "Recent hours — observed", "source": "NWS hourly observations at KSFO (api.weather.gov/stations/KSFO/observations)",
             "detail": "precipitationLastHour bucketed to the hour, max per hour when specials and the METAR overlap. A day with fewer than 12 observations is treated as unknown and keeps the model's hours — a gauge outage must never zero out a storm."},
            {"role": "Today and the next 5 days — forecast", "source": "Open-Meteo forecast API, model ecmwf_ifs025 (ECMWF IFS 0.25°)",
             "detail": "Hourly precipitation for 37.7749, −122.4194, past_days=7, forecast_days=6, America/Los_Angeles. IFS is used rather than Open-Meteo's best_match because the intensity features were trained on ERA5 (ECMWF's reanalysis). IFS precipitation is 3-hourly interpolated to hourly, so forecast-day peak-intensity features run smoother than the archive's."},
            {"role": "Hourly intensity for hindcasts", "source": "Open-Meteo archive API (archive-api.open-meteo.com), ERA5 hourly",
             "detail": "rain_max1h / 3h / 6h for past days in training and in the Model check hindcast (data/raw/hourly_rain_openmeteo.csv)."},
            {"role": "Observed discharges (override)", "source": "BWTF watcher alert_log (Supabase) — SFPUC's real-time CSO flag",
             "detail": "A day the watcher saw a CSO onset in a basin gets p = 1 for that basin in the composition, replacing the model's probability."},
        ],
        "notes": notes.get("archive_recall"),
        "samples": samples,
        "calibration": "p = clip(raw_p − offset × clip(1 − rain_3d_cum × 2, 0, 1), 0, 1): the dry-day offset (mean predicted probability on training days with < 0.01\" rain) is subtracted in full on dry days and fades out as the trailing 3-day rain approaches 0.5\".",
    }
    html = TEMPLATE.read_text().replace("__DATA__", json.dumps(data, separators=(",", ":"), default=str))
    OUT.write_text(html)
    n_nodes = sum(len(t["f"]) for m in models.values() for t in m["trees"]) + sum(len(t["f"]) for h in heads.values() for t in h["trees"])
    print(f"wrote {OUT.relative_to(REPO)}: {OUT.stat().st_size / 1e6:.2f} MB, {len(models)} classifiers + {len(heads)} volume heads "
          f"({n_nodes:,} tree nodes), {len(samples)} sample days, {len(data['gauge_series']['dates'])} gauge days")


if __name__ == "__main__":
    main()
