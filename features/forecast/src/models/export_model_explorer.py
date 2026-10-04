#!/usr/bin/env python3
"""Inside the overflow model (S2): one self-contained HTML page per model set
that shows its S2 exactly as it is, in the five stages (STAGES_DESIGN.md A8):
the rain gauges and live feeds (S1), the 19 input features and their formulas,
every basin model's weights or trees (exported so the page runs them in the
browser, with a what-if rain editor), the volume heads, the S3 split and S4
lingering table it composes with, the basins → groups → zones map, and a
self-check that the in-page arithmetic matches scikit-learn on real storms.

    venv/bin/python features/forecast/src/models/export_model_explorer.py
    → reports/2026-09_forecast_<served>_model_explorer.html  (today's forecast; served at /reports/…)
    venv/bin/python features/forecast/src/models/export_model_explorer.py --model logit_v1
    → reports/2026-09_forecast_logit_v1_model_explorer.html  (a candidate set: its
      own S2 weights with its own S3–S4; the page says so)
    … --offline   skip the gauge metadata lookup (ACIS) and use the cached values below

A page titles its set by its lineup's plain words (export_reports_index.model_sets,
shared/lineup.py: "today's forecast" for the served set); stored names stay
identifiers, in small print. Re-run after retraining or `train_v4.py --rescore`
so the page matches the pickles. Everything numeric on the page comes from the
pickles, the lingering table, data/raw and the scorecard artifact — nothing is retyped.
"""
from __future__ import annotations

import gzip
import html as _html
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
import export_reports_index as RI  # noqa: E402  (each set's lineup words and the page's title)
from groups import BASIN_KEYS, GROUPS_BY_BASIN, SITE_GROUPS, ZONE_GROUPS  # noqa: E402
from impact import smooth_table  # noqa: E402
from rain_features import DAILY_FEATURES, INTENSITY_FEATURES  # noqa: E402
from shared import lineup as LU  # noqa: E402
from shared import risk_levels  # noqa: E402
from shared.outfalls import OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

REPO = HERE.parents[3]
SERVE_DIR = T.SERVE_DIR
RAW_DIR = T.RAW_DIR
import candidates as _cand  # noqa: E402
OUT = REPO / "reports" / f"2026-09_forecast_{_cand.SERVED['name']}_model_explorer.html"   # the served set's page
TEMPLATE = HERE / "model_explorer_template.html"

ACIS_GAUGES = {"SF Downtown": "047772", "SF Oceanside": "047767"}
# what the page shows if ACIS metadata is unreachable or skipped (--offline); values fetched 2026-10-01
GAUGE_META_FALLBACK = {
    "047772": {"name": "SAN FRANCISCO DOWNTOWN", "lat": 37.7705, "lon": -122.4269, "elev_ft": 150.0, "valid_through": "2026-09-29"},
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
    return {"family": "gb", "n_estimators": int(gb.n_estimators), "max_depth": int(gb.max_depth), "learning_rate": float(gb.learning_rate),
            "init_raw": init, "trees": [export_tree(gb.estimators_[k, 0].tree_) for k in range(gb.n_estimators)],
            "importances": {f: round(float(i), 5) for f, i in zip(features, gb.feature_importances_)},
            "params": {k: v for k, v in gb.get_params().items() if k in ("min_samples_leaf", "subsample", "random_state", "loss")}}


def export_logit(pipe, features, C=None) -> dict:
    """The weights model as numbers the page can run: hinge knots (in the exact
    column order leaderboard.add_hinges builds), the scaler's mean/sd per
    column, one weight per standardised column, the intercept. Full precision.
    ``importances`` is the analogue of the trees' split-gain share: each base
    feature's share of Σ|weight per sd| over its own column and its hinges.
    A shared-terms model (logit_v2_shared*, a "bands" pipeline) ships its band
    columns instead — [{f, lo, hi}], value clip(x_f − lo, 0, hi − lo) — and the
    page builds those; every weight of such a model is ≥ 0."""
    import leaderboard as LB
    lr, sc = pipe.named_steps["lr"], pipe.named_steps["scale"]
    coef, means, scales = [float(c) for c in lr.coef_[0]], [float(v) for v in sc.mean_], [float(v) for v in sc.scale_]
    if "bands" in pipe.named_steps:
        cols = LB.band_columns(pipe.named_steps["bands"].kw_args["design"])
        names = [LB.band_name(*c) for c in cols]
        assert len(names) == len(coef) == len(means), "column layout drifted from leaderboard.add_bands"
        agg = {f: 0.0 for f in features}
        for (f, _, _), c in zip(cols, coef):
            agg[f] += abs(c)
        tot = sum(agg.values()) or 1.0
        return {"family": "logit", "C": C if C is not None else float(lr.C), "intercept": float(lr.intercept_[0]),
                "names": names, "coef": coef, "means": means, "scales": scales, "hinges": {},
                "bands": [{"f": f, "lo": lo, "hi": hi} for f, lo, hi in cols], "nonneg": True,
                "importances": {f: round(v / tot, 5) for f, v in agg.items() if v},
                "params": {"penalty": "l2", "constraint": "every weight >= 0", "max_iter": int(lr.max_iter), "n_band_terms": len(cols)}}
    names = list(features) + LB.HINGE_NAMES
    assert len(names) == len(coef) == len(means), "column layout drifted from leaderboard.add_hinges"
    agg = {f: 0.0 for f in features}
    for n, c in zip(names, coef):
        agg[n.split(">")[0]] += abs(c)
    tot = sum(agg.values()) or 1.0
    return {"family": "logit", "C": C if C is not None else float(lr.C), "intercept": float(lr.intercept_[0]),
            "names": names, "coef": coef, "means": means, "scales": scales,
            "hinges": LB.HINGES, "importances": {f: round(v / tot, 5) for f, v in agg.items()},
            "params": {"penalty": "l2", "max_iter": int(lr.max_iter), "n_hinge_terms": len(LB.HINGE_NAMES)}}


def export_stage1(m: dict, features) -> dict:
    """Dispatch on the pickle's family (pickles saved before 2026-09-25 predate the key → gb)."""
    fam = m.get("family") or ("logit" if hasattr(m["model"], "named_steps") else "gb")
    if fam == "logit":
        return export_logit(m["model"], features, m.get("C"))
    return export_gb(m["model"], features)


def load_model_set(name: str | None) -> tuple[dict, dict, dict]:
    """({key: pickle dict}, manifest-or-eval-report holdout block, meta) for the
    served set (name None) or a candidate under data/models/candidates/."""
    features = T.get_feature_columns_v21()
    if name is None:
        ev = json.loads((SERVE_DIR / "eval_report.json").read_text())
        sv = _cand.served_info()
        import leaderboard  # noqa: F401  (weights pipelines reference leaderboard.add_hinges)
        models = {}
        for basin in T.APP_BASINS + ["citywide"]:
            key = BASIN_KEYS.get(basin, "citywide")
            with open(SERVE_DIR / f"{key}_model.pkl", "rb") as f:
                models[key] = pickle.load(f)
        if sv.get("per_basin"):        # a promoted set: holdout picks from its manifest
            holdout = {}
            for k, pb in sv["per_basin"].items():
                h = pb.get("holdout") or {}
                holdout[k] = {"pr_auc": h.get("pr_auc"), "roc_auc": h.get("roc_auc"), "brier": h.get("brier"),
                              "n_test": h.get("n"), "pos_test": h.get("pos"), "n_events": pb.get("n_events"), "n_days": None,
                              "C": pb.get("C"), "season_cv": pb.get("season_cv_pre_holdout")}
        else:                          # the original gb_v1 bundle: its training report
            holdout = {k: {"pr_auc": t["holdout"].get("pr_auc"), "roc_auc": t["holdout"].get("roc_auc"), "brier": t["holdout"].get("brier"),
                           "n_test": t["holdout"].get("n_test"), "pos_test": t["holdout"].get("pos_test"), "n_events": t.get("n_events"), "n_days": t.get("n_days")}
                       for k, t in ev.get("targets", {}).items() if t.get("holdout")}
        s2_path = SERVE_DIR / "stage2.json"
        meta = {"name": sv["name"], "label": f'{sv["name"]} (served)', "served": True, "version": sv["stage1"],
                "trained_at": sv.get("created_at") or ev.get("trained_at"),
                "train_window": ev.get("train_window"), "feature_set": ev.get("feature_set"), "note": sv.get("note"),
                "stage1_name": sv["stage1"], "stage1_from": "served",
                "stage2": json.loads(s2_path.read_text()) if s2_path.exists() else None,
                "scorecard": SERVE_DIR / "scorecard.json.gz"}
    else:
        import candidates
        models = candidates.load_models(name)
        manifest = json.loads((candidates.candidate_dir(name) / "manifest.json").read_text())
        ev = json.loads((SERVE_DIR / "eval_report.json").read_text())
        holdout = {}
        for k, pb in manifest.get("per_basin", {}).items():
            h = pb.get("holdout") or {}
            holdout[k] = {"pr_auc": h.get("pr_auc"), "roc_auc": h.get("roc_auc"), "brier": h.get("brier"),
                          "n_test": h.get("n"), "pos_test": h.get("pos"), "n_events": pb.get("n_events"), "n_days": None,
                          "C": pb.get("C"), "season_cv": pb.get("season_cv_pre_holdout")}
        meta = {"name": name, "label": f"{name} (candidate, not served)", "served": False, "version": name,
                "trained_at": manifest.get("created_at"), "train_window": ev.get("train_window"), "feature_set": ev.get("feature_set"),
                "note": manifest.get("note"), "family": manifest.get("family"), "C_grid": manifest.get("C_grid"),
                "stage1_from": (manifest.get("stage1") or {}).get("from", "fit"),
                "stage1_name": (manifest.get("stage1") or {}).get("name", name),
                "stage2": candidates.load_stage2(name),   # None = no split and the first lingering table (stored as stage 2 v1)
                "scorecard": candidates.candidate_dir(name) / "scorecard.json.gz"}
    for m in models.values():
        assert m["features"] == features, "feature contract drifted"
    return models, holdout, meta


def gauge_meta(offline: bool = False) -> dict:
    out = {sid: dict(m) for sid, m in GAUGE_META_FALLBACK.items()}
    if offline:
        return out
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
    # outage runs by the shared rule (rain_features.GAUGE_OUTAGE_RULE) — the same
    # detector serving and post-training rescoring apply
    from rain_features import find_gauge_outages
    series["outages"] = find_gauge_outages(p.reset_index(), gauges=tuple(n for n in ACIS_GAUGES if n in p))
    return series


def main(model_name: str | None = None, offline: bool = False) -> None:
    features = T.get_feature_columns_v21()
    if model_name in ("served", _cand.SERVED["name"]):
        model_name = None  # the served set's page is the default one
    raw_models, holdout, meta = load_model_set(model_name)
    # the set by its lineup (A8): S2 · S3 · S4 in plain words; the page's title says which set it opens
    row = RI.model_set(meta["name"])
    words = {c: LU.words(c, row["parts"][c]) for c in RI.SET_COLS}
    title = f"{RI.EXPLORER_TITLE}: {RI.set_title(row)}"
    models, heads, rain_source = {}, {}, {}
    for basin in T.APP_BASINS + ["citywide"]:
        key = BASIN_KEYS.get(basin, "citywide")
        m = raw_models[key]
        models[key] = {**export_stage1(m, features), "calibration_offset": float(m["calibration_offset"]),
                       "rain_source": m.get("rain_source", "avg"), "trained_at": m.get("trained_at"), "version": m.get("version"),
                       "basin": basin}
        rain_source[key] = m.get("rain_source", "avg")
        # the volume heads (S2's size) are the served ones for every model set
        vp = SERVE_DIR / f"{key}_volume.pkl"
        if vp.exists():
            with open(vp, "rb") as f:
                h = pickle.load(f)
            heads[key] = {**export_gb(h["model"], h["features"]), "n_events": h.get("n_events"), "resid_std": h.get("resid_std"),
                          "target": h.get("target"), "rain_source": h.get("rain_source")}
    ev = json.loads((SERVE_DIR / "eval_report.json").read_text())
    impact_raw = json.loads((SERVE_DIR / "impact_table.json").read_text())
    # a set's S3–S4 (stored as its "stage 2"): v1 = no split and the first lingering table; v2 = today's split's shares + its refit table
    s2 = meta.get("stage2")
    if s2 and s2.get("impact_table"):
        impact_raw = s2["impact_table"]
    stage2_out = {"variant": (s2 or {}).get("variant", "v1"), "kind": (s2 or {}).get("kind", "basin composition"),
                  "impact_table_refit": bool(s2 and s2.get("impact_table"))}
    if s2 and s2.get("variant", "v1") != "v1":
        stage2_out.update({k: s2.get(k) for k in ("shares", "group_outfalls", "median_event_volume_mg", "definition", "fitted_at", "impact_table_note")})
    with gzip.open(meta["scorecard"], "rt") as f:
        sc = json.load(f)
    sc_by_date = {d["date"]: d for d in sc["days"]}
    families = sorted({m["family"] for m in models.values()})
    out_path = OUT if model_name is None else REPO / "reports" / f"2026-09_forecast_{model_name}_model_explorer.html"

    # frames exactly as training/rescore builds them → sample days with history + sklearn answers.
    # Pre-training days were scored on the raw record; post-training days on inputs treated by
    # the artifact's input rules (--rescore --replace-post; today the gauge-outage rule) — so each
    # sample day is rebuilt from the frame set its stored risks came from.
    end = pd.Timestamp(sc["span"][1])
    srcs = sorted(set(rain_source.values()) | set(T.RAIN_SOURCES))
    frames, notes = T.build_dataset(end=end, sources=srcs)
    rules_post = sc.get("input_rules_post") or []
    frames_post = T.build_dataset(end=end, sources=srcs, input_rules=rules_post)[0] if rules_post else frames
    finals = {k: {"model": raw_models[k]["model"], "features": features, "calibration_offset": models[k]["calibration_offset"]} for k in models}
    head_objs = {b: pickle.load(open(SERVE_DIR / f"{BASIN_KEYS[b]}_volume.pkl", "rb")) for b in T.APP_BASINS
                 if (SERVE_DIR / f"{BASIN_KEYS[b]}_volume.pkl").exists()}
    base = frames["avg"]
    samples = []
    for ds in SAMPLE_DAYS:
        d = pd.Timestamp(ds)
        stored0 = sc_by_date.get(ds, {})
        fr = frames_post if stored0.get("post_training") else frames        # the frame set this day's stored risks came from
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
            hist[src] = [float(v) for v in fr[src]["precip_avg"].iloc[lo:i + 1]]   # the source's daily total (rule-treated for post-training days)
        hist["intensity"] = {f: [float(v) for v in base[f].iloc[lo:i + 1]] for f in INTENSITY_FEATURES}
        hist["input_rules"] = rules_post if stored0.get("post_training") else []
        feats = {src: {f: float(fr[src].iloc[i][f]) for f in features} for src in T.RAIN_SOURCES}
        expected = {}
        for basin in T.APP_BASINS + ["citywide"]:
            key = BASIN_KEYS.get(basin, "citywide")
            X = fr[rain_source[key]].iloc[[i]]
            expected[key] = {"p": round(float(T.calibrated(finals[key], X)[0]), 6)}
            if basin in head_objs:
                # a volume head reads the rain source it was trained on, not the
                # S2 model's (build_scorecard, live_dashboard and the page do too)
                Xv = fr[head_objs[basin].get("rain_source", rain_source[key])].iloc[[i]]
                expected[key]["volume_mg"] = round(float(T.predicted_volume(head_objs[basin], Xv)[0]), 4)
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
        "model_set": {"name": meta["name"], "label": RI.set_title(row), "title": title, "words": words, "served": meta["served"], "note": meta.get("note"),
                      "families": families, "C_grid": meta.get("C_grid"), "stage1_from": meta.get("stage1_from", "served" if meta["served"] else "fit"),
                      "stage1_name": meta.get("stage1_name", "gb_v1" if meta["served"] else meta["name"]),
                      "stage2": stage2_out,
                      "stage2_from": "volume heads: the served bundle's, shared by every set; S3's split and S4's lingering table: this set's own"},
        "stage2": stage2_out,
        "trained_at": meta.get("trained_at"), "version": meta.get("version"), "feature_set": meta.get("feature_set"),
        "train_window": meta.get("train_window"), "holdout_start": sc.get("holdout_start"),
        "trained_through": sc.get("trained_through") or sc["span"][1], "rescored_at": sc.get("rescored_at"), "span": sc["span"],
        "input_rules_post": rules_post,
        "features": {"daily": DAILY_FEATURES, "intensity": INTENSITY_FEATURES, "all": features},
        "model_params": {k: v for k, v in T.MODEL_PARAMS.items()},
        "models": models, "volume_heads": heads,
        "holdout": holdout,
        "impact_raw": impact_raw, "impact_smoothed": smooth_table(impact_raw),
        "basins": {BASIN_KEYS[b]: {"name": b, "rain_source": rain_source[BASIN_KEYS[b]],
                                   "outfalls": sorted(o.id for o in OUTFALLS.values() if o.basin == b),
                                   "groups": GROUPS_BY_BASIN[BASIN_KEYS[b]]} for b in T.APP_BASINS},
        "groups": {g: {"basin": BASIN_KEYS[b], "stations": [{"id": s, "name": STATIONS[s].name} for s in sids]}
                   for g, (b, sids) in SITE_GROUPS.items()},
        "zones": {zk: {"label": ZONES[zk].label, "groups": gs, "gauge": getattr(ZONES[zk], "gauge", None),
                       "stations": [STATIONS[s].name for s in ZONES[zk].source_ids]} for zk, gs in ZONE_GROUPS.items()},
        "gauges": {sid: {**gm, "label": name} for gm_all in [gauge_meta(offline)] for name, sid in ACIS_GAUGES.items() for m_sid, gm in gm_all.items() if m_sid == sid},
        "gauge_series": gauge_series(),
        "live_feeds": [
            {"role": "S1 · past complete days — daily totals", "source": "NOAA daily gauges via ACIS (data.rcc-acis.org/StnData)",
             "detail": "Station 047772 SF Downtown and 047767 SF Oceanside. 'avg' = their mean; a gauge source = that gauge, falling back to the other gauge, then to the hourly total. Missing (M/S) is left missing; trace (T) is 0. These are the exact series the models were trained on (data/raw/historical_rain.csv)."},
            {"role": "S1 · recent hours — observed", "source": "NWS hourly observations at KSFO (api.weather.gov/stations/KSFO/observations)",
             "detail": "precipitationLastHour bucketed to the hour, max per hour when specials and the METAR overlap. A day with fewer than 12 observations is treated as unknown and keeps the model's hours — a gauge outage must never zero out a storm."},
            {"role": "S1 · today and the next 5 days — forecast", "source": "Open-Meteo forecast API, model icon_seamless (ICON, DWD; ECMWF IFS 2026-09-04 → 09-30); was ecmwf_ifs025 (ECMWF IFS 0.25°)",
             "detail": "Hourly precipitation for 37.7749, −122.4194, past_days=7, forecast_days=6, America/Los_Angeles. ICON since 2026-09-30: the closest single model to the two NOAA gauges over Feb 2024 → Aug 2026 (reports/2026-09_weather_models.html, archived; S1 in reports/2026-10_forecast_stages.html scores it now), with native hourly rain, so the forecast-day peak-intensity features match the ERA5 hourly the models were trained on. ECMWF IFS (2026-09-04 → 09-30) ran wet and its 3-hourly rain, spread to hourly, halved the peaks; Open-Meteo's best_match missed real rain on 2026-09-03."},
            {"role": "S1 · hourly intensity for hindcasts", "source": "Open-Meteo archive API (archive-api.open-meteo.com), ERA5 hourly",
             "detail": "rain_max1h / 3h / 6h for past days in training and in the Model check hindcast (data/raw/hourly_rain_openmeteo.csv)."},
            {"role": "S5 · observed discharges (override, live only)", "source": "BWTF watcher alert_log (Supabase) — SFPUC's real-time CSO flag",
             "detail": "A day the watcher saw a CSO onset in a basin gets p = 1 for that basin in the composition, replacing the model's probability."},
        ],
        "notes": notes.get("archive_recall"),
        "samples": samples,
        "calibration": "p = clip(raw_p − offset × clip(1 − rain_3d_cum × 2, 0, 1), 0, 1): the dry-day offset (mean predicted probability on training days with < 0.01\" rain) is subtracted in full on dry days and fades out as the trailing 3-day rain approaches 0.5\".",
    }
    html = TEMPLATE.read_text().replace("__TITLE__", _html.escape(title, quote=False))\
        .replace("__DATA__", json.dumps(data, separators=(",", ":"), default=str))\
        .replace("__RISK_LEVELS__", json.dumps(risk_levels.export(), separators=(",", ":")))   # Low / Medium / High / Extreme, the page colours (A3)
    out_path.write_text(html)
    n_nodes = sum(len(t["f"]) for m in models.values() for t in m.get("trees", [])) + sum(len(t["f"]) for h in heads.values() for t in h["trees"])
    n_w = sum(len(m.get("coef", [])) for m in models.values())
    print(f"wrote {out_path.relative_to(REPO)}: {out_path.stat().st_size / 1e6:.2f} MB, model set {meta['name']} ({'/'.join(families)}): "
          f"{len(models)} classifiers ({n_nodes:,} tree nodes, {n_w} weights) + {len(heads)} volume heads, "
          f"{len(samples)} sample days, {len(data['gauge_series']['dates'])} gauge days")


if __name__ == "__main__":
    # venv/bin/python features/forecast/src/models/export_model_explorer.py [--model <candidate name>] [--offline]
    name = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
    main(name, offline="--offline" in sys.argv)
