#!/usr/bin/env python3
"""Stage 2 explorer — one self-contained HTML page that opens up the
discharge-probability → beach-risk transformation (src/models/impact.py):
the empirical impact table (raw and smoothed, with sample counts), the decay
curves and the small/large volume blend, the volume heads and how well they
predict reported volumes, and a day-by-day composition walk-through for ANY
day in the hindcast, for the served v4 set and every candidate set side by
side — so what is shared (the whole of stage 2) and what differs per model
(the stage-1 probabilities and rain sources feeding it) is visible, and a
self-check proves the page's composition reproduces the stored artifacts.

    venv/bin/python features/forecast/src/models/export_stage2_explorer.py
    → reports/2026-09_forecast_stage2_explorer.html  (served at /reports/…)

Re-run after retraining, `train_v4.py --rescore` or saving a candidate.
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

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import candidates  # noqa: E402
import train_v4 as T  # noqa: E402
from export_model_explorer import export_gb  # noqa: E402
from groups import BASIN_KEYS, GROUPS_BY_BASIN, SITE_GROUPS, ZONE_GROUPS  # noqa: E402
from impact import smooth_table  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

REPO = HERE.parents[3]
SERVE_DIR = T.SERVE_DIR
OUT = REPO / "reports" / "2026-09_forecast_stage2_explorer.html"
TEMPLATE = HERE / "stage2_explorer_template.html"
BASIN_ORDER = [BASIN_KEYS[b] for b in T.APP_BASINS]


def _r(v, nd):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), nd)


def load_sets() -> list[dict]:
    """The served v4 set plus every candidate: {name, label, served, models{key: pickle}, scorecard, manifest}."""
    served = {}
    for basin in T.APP_BASINS + ["citywide"]:
        key = BASIN_KEYS.get(basin, "citywide")
        with open(SERVE_DIR / f"{key}_model.pkl", "rb") as f:
            served[key] = pickle.load(f)
    with gzip.open(SERVE_DIR / "scorecard.json.gz", "rt") as f:
        sc = json.load(f)
    ev = json.loads((SERVE_DIR / "eval_report.json").read_text())
    sets = [{"name": "v4", "label": "v4 (served)", "served": True, "models": served, "scorecard": sc,
             "note": f"the live forecast; trained {str(ev.get('trained_at', ''))[:10]}", "family": "gb",
             "stage1_name": "gb_v1", "stage1_from": "v4"}]
    for man in candidates.list_candidates():
        name = man["name"]
        try:
            models = candidates.load_models(name)
        except Exception as exc:  # noqa: BLE001
            print(f"candidate {name}: cannot unpickle ({exc}); skipped")
            continue
        s2 = candidates.load_stage2(name)
        sets.append({"name": name, "label": f"{name} (candidate)", "served": False, "models": models,
                     "scorecard": candidates.load_scorecard(name), "note": man.get("note"), "family": man.get("family"),
                     "stage2": s2, "stage1_from": (man.get("stage1") or {}).get("from", "fit"),
                     "stage1_name": (man.get("stage1") or {}).get("name", name)})
    return sets


def main() -> None:
    features = T.get_feature_columns_v21()
    sets = load_sets()
    heads = {}
    for basin in T.APP_BASINS:
        vp = SERVE_DIR / f"{BASIN_KEYS[basin]}_volume.pkl"
        if vp.exists():
            with open(vp, "rb") as f:
                heads[basin] = pickle.load(f)
    impact_raw = json.loads((SERVE_DIR / "impact_table.json").read_text())
    impact_smoothed = smooth_table(impact_raw)

    master = sets[0]["scorecard"]
    dates = [d["date"] for d in master["days"]]
    sources = set(T.RAIN_SOURCES)
    for s in sets:
        sources |= {m.get("rain_source", "avg") for m in s["models"].values()}
    sources |= {h.get("rain_source", "avg") for h in heads.values()}
    frames, notes = T.build_dataset(end=pd.Timestamp(dates[-1]), sources=sorted(sources))
    # post-training days were re-scored on rule-treated inputs (input_rules_post); rebuild them from the same
    rules_post = master.get("input_rules_post") or []
    frames_post = T.build_dataset(end=pd.Timestamp(dates[-1]), sources=sorted(sources), input_rules=rules_post)[0] if rules_post else frames
    trained_through = master.get("trained_through") or master["span"][1]
    post_from = next((i for i, d in enumerate(dates) if d > trained_through), len(dates))
    def spliced(src: str, fn):
        """fn(frame) → per-day array; raw frame before post_from, rule-treated after."""
        a, b = list(fn(frames[src])), list(fn(frames_post[src]))
        return a[:post_from] + b[post_from:]
    base = frames["avg"]
    assert [str(d.date()) for d in base["date"]] == dates, "frame dates do not line up with the served artifact"
    n = len(dates)

    # shared stage 2: predicted volume per basin per day, from the head's OWN rain source
    vol_pred = {}
    for basin in T.APP_BASINS:
        key = BASIN_KEYS[basin]
        if basin in heads:
            vol_pred[key] = [_r(v, 3) for v in spliced(heads[basin].get("rain_source", "avg"), lambda X: T.predicted_volume(heads[basin], X))]
        else:
            vol_pred[key] = [0.0] * n

    # per model set: stage-1 probabilities per basin per day (exactly as build_scorecard computes them)
    model_sets, stage2_variants = [], {}
    for s in sets:
        probs, spec = {}, {}
        for basin in T.APP_BASINS:
            key = BASIN_KEYS[basin]
            m = s["models"][key]
            src = m.get("rain_source", "avg")
            fin = {"model": m["model"], "features": m["features"], "calibration_offset": m["calibration_offset"]}
            probs[key] = [_r(v, 6) for v in spliced(src, lambda X: T.calibrated(fin, X))]
            spec[key] = {"rain_source": src, "calibration_offset": _r(m["calibration_offset"], 5),
                         "family": m.get("family") or ("logit" if hasattr(m["model"], "named_steps") else "gb"),
                         "C": m.get("C")}
        sc = s["scorecard"]
        by_date = {d["date"]: d for d in sc.get("days", [])}
        stored_groups = {g: [_r((by_date.get(d, {}).get("groups", {}).get(g) or {}).get("risk"), 3) for d in dates] for g in SITE_GROUPS}
        s2 = s.get("stage2")
        refit = bool(s2 and s2.get("impact_table"))
        stage2_out = {"variant": (s2 or {}).get("variant", "v1"), "kind": (s2 or {}).get("kind", "basin composition"), "impact_table_refit": refit}
        if s2 and s2.get("variant", "v1") != "v1":
            stage2_out.update({"shares": s2["shares"], "group_outfalls": s2["group_outfalls"],
                               "median_event_volume_mg": s2["median_event_volume_mg"], "definition": s2.get("definition"),
                               "fitted_at": s2.get("fitted_at"), "impact_table_note": s2.get("impact_table_note")})
            if s2["variant"] not in stage2_variants:
                stage2_variants[s2["variant"]] = {"impact_raw": s2.get("impact_table"), "impact_smoothed": smooth_table(s2["impact_table"]) if refit else None,
                                                  "shares": s2["shares"], "group_outfalls": s2["group_outfalls"],
                                                  "median_event_volume_mg": s2["median_event_volume_mg"], "definition": s2.get("definition"),
                                                  "impact_table_note": s2.get("impact_table_note"), "train_window": s2.get("train_window")}
        model_sets.append({"name": s["name"], "label": s["label"], "served": s["served"], "note": s["note"], "family": s["family"],
                           "stage1_from": s.get("stage1_from", "v4" if s["served"] else "fit"), "stage1_name": s.get("stage1_name", s["name"]),
                           "trained_through": sc.get("trained_through") or (sc.get("span") or [None, None])[1],
                           "span": sc.get("span"), "probs": probs, "spec": spec, "stored_groups": stored_groups,
                           "stage2": stage2_out,
                           # the table this set composes with (smoothed, as serving/build_scorecard do); None = the served one
                           "impact_table": smooth_table(s2["impact_table"]) if refit else None})

    # labels straight from the served artifact (identical across sets)
    labels = {"basin_y": {}, "basin_vol": {}, "group_elevated": {}, "group_n": {}, "zone_discharge": {}}
    for key in BASIN_ORDER:
        labels["basin_y"][key] = [d["basins"][key]["y"] for d in master["days"]]
        labels["basin_vol"][key] = [d["basins"][key].get("vol") for d in master["days"]]
    for g in SITE_GROUPS:
        labels["group_elevated"][g] = [None if d["groups"][g]["elevated"] is None else int(d["groups"][g]["elevated"]) for d in master["days"]]
        labels["group_n"][g] = [d["groups"][g].get("n_samples", 0) for d in master["days"]]
    for zk in ZONE_GROUPS:
        labels["zone_discharge"][zk] = [None if d["zones"][zk]["discharge"] is None else int(d["zones"][zk]["discharge"]) for d in master["days"]]
    rain = [_r(d["rain"], 3) for d in master["days"]]
    post_training = [bool(d.get("post_training")) for d in master["days"]]

    # volume heads: description + actual-vs-predicted on event days with a reported volume
    vol_heads = {}
    for basin in T.APP_BASINS:
        key = BASIN_KEYS[basin]
        if basin not in heads:
            vol_heads[key] = None
            continue
        h = heads[basin]
        sub = T.target_frame(frames[h.get("rain_source", "avg")], basin)
        ev = sub[(sub["y"] == 1) & (sub[f"{basin}_volume_known"] == 1)]
        pred = T.predicted_volume(h, ev)
        pts = [{"date": str(d.date()), "actual": _r(a, 2), "pred": _r(p, 2), "rain": _r(r, 2), "holdout": bool(d >= T.HOLDOUT_START)}
               for d, a, p, r in zip(ev["date"], ev[f"{basin}_volume_mg"], pred, ev["precip_avg"])]
        exp = export_gb(h["model"], h["features"])
        vol_heads[key] = {"n_estimators": exp["n_estimators"], "max_depth": exp["max_depth"], "learning_rate": exp["learning_rate"],
                          "prior_mg": _r(np.expm1(exp["init_raw"]), 2), "importances": exp["importances"],
                          "n_events": h.get("n_events"), "resid_std": h.get("resid_std"), "target": h.get("target"),
                          "rain_source": h.get("rain_source", "avg"), "points": pts}

    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "span": master["span"], "holdout_start": master.get("holdout_start"),
        "trained_through": master.get("trained_through") or master["span"][1], "rescored_at": master.get("rescored_at"),
        "input_rules_post": rules_post,
        "impact_raw": impact_raw, "impact_smoothed": impact_smoothed,
        "impact_fitted_from": {"sample_days": {g: t.get("n_sample_days") for g, t in impact_raw.items()},
                               "note": "fit by train_v4.fit_impact_table at v4 training (2026-09-12) from DataSF + Poo Bot samples and CIWQS discharges; "
                                       "the served file is data/models/impact_table.json; the page smooths it exactly as serving does (impact.smooth_table)"},
        "basins": {BASIN_KEYS[b]: {"name": b, "groups": GROUPS_BY_BASIN[BASIN_KEYS[b]]} for b in T.APP_BASINS},
        "groups": {g: {"basin": BASIN_KEYS[b], "stations": [{"id": s, "name": STATIONS[s].name} for s in sids]} for g, (b, sids) in SITE_GROUPS.items()},
        "zones": {zk: {"label": ZONES[zk].label, "groups": gs} for zk, gs in ZONE_GROUPS.items()},
        "volume_heads": vol_heads,
        "dates": dates, "rain": rain, "post_training": post_training, "vol_pred": vol_pred, "labels": labels,
        "model_sets": model_sets, "stage2_variants": stage2_variants,
        "live_override": "Live serving adds one input the hindcast does not have: a day the watcher saw a CSO onset in a basin gets p = 1 for that basin (live_dashboard._fetch_observed_cso → impact.compose observed=…). A day with no flag keeps the model's probability — absence of a flag is never treated as 'no discharge' (see TODO: discount by the feed's miss rate).",
    }
    html = TEMPLATE.read_text().replace("__DATA__", json.dumps(data, separators=(",", ":"), default=str))
    OUT.write_text(html)
    print(f"wrote {OUT.relative_to(REPO)}: {OUT.stat().st_size / 1e6:.2f} MB — {len(model_sets)} model sets "
          f"({', '.join(m['name'] for m in model_sets)}), {n} days {dates[0]} → {dates[-1]}, "
          f"{sum(len(v['points']) for v in vol_heads.values() if v)} volume points")


if __name__ == "__main__":
    main()
