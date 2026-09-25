#!/usr/bin/env python3
"""Fit a stage 2 variant, and pair any stage 1 with it as a candidate set.

    venv/bin/python features/forecast/src/models/stage2_variants.py fit
        → data/models/stage2/v2.json  (stage 2 v2 = the outfall split: shares by group and size,
          plus the impact table refit on group-attributed discharge days)

    venv/bin/python features/forecast/src/models/stage2_variants.py save \
        --stage1 v4 --variant v2 --name gb_v1_s2v2 [--note "…"]
    venv/bin/python features/forecast/src/models/stage2_variants.py save \
        --stage1 logit_v1 --variant v2 --name logit_v1_s2v2
        → data/models/candidates/<name>/ : the stage-1 pickles copied from the
          source set (served v4 or a candidate), stage2.json, and a scorecard
          composed with the variant. Holdout-fit siblings are refit on the
          pre-holdout rows exactly as the source set fit them (deterministic),
          and checked against the source artifact's stored holdout
          probabilities before anything is written.

The served set is never touched. Fitting uses the training window (through
TRAIN_END), like every served table, so post-training days stay a clean test.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import candidates  # noqa: E402
import stage2 as S2  # noqa: E402
import train_v4 as T  # noqa: E402
from groups import BASIN_KEYS  # noqa: E402

REPO = HERE.parents[3]


def _served_chosen() -> dict:
    ev = json.loads((T.SERVE_DIR / "eval_report.json").read_text())
    chosen = {b: ev["rain_source_chosen"].get(BASIN_KEYS[b], "avg") for b in T.APP_BASINS}
    chosen["citywide"] = "avg"
    return chosen


def fit(variant: str = "v2") -> Path:
    if variant != "v2":
        raise SystemExit(f"no fitter for {variant!r}")
    heads, impact_raw = T.stage2_from_served()
    chosen = _served_chosen()
    sources = sorted(set(T.RAIN_SOURCES) | set(chosen.values()) | {h.get("rain_source", "avg") for h in heads.values()})
    frames, notes = T.build_dataset(sources=sources)          # training window, like every served table
    events = T.load_events()
    spec = S2.fit_outfall_split(frames, chosen, heads, impact_raw, events)
    event_days = S2.group_event_days(frames, chosen, events)
    table, _diag = T.fit_impact_table(frames, chosen, heads, T.load_samples(), event_days=event_days)
    spec["impact_table"] = table
    spec["impact_table_note"] = ("refit on group-attributed discharge days (CIWQS days where one of the group's outfalls "
                                 "discharged, plus the basin's 2016-17 feed-archive days, which carry no outfall detail)")
    spec["train_window"] = [str(frames["avg"]["date"].min().date()), str(frames["avg"]["date"].max().date())]
    print(f"stage 2 {variant} (outfall split): shares by group (share of the basin's CIWQS discharge days on which the group's outfalls took part)")
    for g, s in spec["shares"].items():
        print(f"   {g:14} outfalls {len(spec['group_outfalls'][g]):2}  large {s['large']['p']} (n={s['large']['n']})  small {s['small']['p']} (n={s['small']['n']})  all {s['all']['p']} (n={s['all']['n']})")
    print("impact table refit — sample days per group (v4 → split):")
    for g in table:
        print(f"   {g:14} {impact_raw[g]['n_sample_days']} → {table[g]['n_sample_days']}   baseline {impact_raw[g]['buckets']['baseline_no_recent_discharge']['p_elevated']} → {table[g]['buckets']['baseline_no_recent_discharge']['p_elevated']}")
    out = S2.save_variant(spec)
    print(f"variant → {out.relative_to(REPO)}")
    return out


def _load_stage1(source: str) -> tuple[dict, dict, str, str]:
    """({key: pickle dict}, source scorecard, family, stage-1 name). The served
    v4 bundle's stage 1 is named gb_v1."""
    if source == "v4":
        models = {}
        for basin in T.APP_BASINS + ["citywide"]:
            key = BASIN_KEYS.get(basin, "citywide")
            with open(T.SERVE_DIR / f"{key}_model.pkl", "rb") as f:
                models[key] = pickle.load(f)
        import gzip
        with gzip.open(T.SERVE_DIR / "scorecard.json.gz", "rt") as f:
            sc = json.load(f)
        return models, sc, "gb", "gb_v1"
    models = candidates.load_models(source)
    sc = candidates.load_scorecard(source)
    man = json.loads((candidates.candidate_dir(source) / "manifest.json").read_text())
    return models, sc, man.get("family", "gb"), (man.get("stage1") or {}).get("name", source)


def _refit_holdouts(models: dict, chosen: dict, frames: dict, features: list) -> dict:
    """Holdout-fit siblings, fit exactly as the source set fit them: the same
    rows (per-basin archive-label decision from eval_report), the same family
    and hyper-parameters, pre-holdout dates only."""
    ev = json.loads((T.SERVE_DIR / "eval_report.json").read_text())
    use_archive = ev.get("stage1_archive_labels", {})
    out = {}
    for basin in T.APP_BASINS + ["citywide"]:
        key = BASIN_KEYS.get(basin, "citywide")
        m = models[key]
        sub = T.target_frame(frames[chosen[basin]], basin)
        if basin != "citywide" and not use_archive.get(basin, True):
            sub = sub[sub[f"{basin}_label_source"] != "poobot"]
        elif basin == "citywide" and not all(use_archive.values()):
            sub = sub[~(sub[[f"{b}_label_source" for b in T.APP_BASINS]] == "poobot").any(axis=1)]
        pre = sub[sub["date"] < T.HOLDOUT_START]
        fam = m.get("family") or ("logit" if hasattr(m["model"], "named_steps") else "gb")
        if pre["y"].sum() < 5:
            out[key] = None
        elif fam == "logit":
            import leaderboard as LB
            out[key] = LB.make_model("logit", m.get("C", 0.3)).fit(pre[features], pre["y"])
        else:
            out[key] = T.fit_holdout_model(sub, features)
    return out


def save(stage1: str, variant: str, name: str, note: str = "") -> Path:
    if not candidates.valid_name(name):
        raise SystemExit(f"bad candidate name {name!r}")
    spec = S2.load_variant(variant)
    models, src_sc, family, stage1_name = _load_stage1(stage1)
    features = T.get_feature_columns_v21()
    chosen = {b: models[BASIN_KEYS[b]].get("rain_source", "avg") for b in T.APP_BASINS}
    chosen["citywide"] = models["citywide"].get("rain_source", "avg")
    heads, _ = T.stage2_from_served()
    sources = sorted(set(chosen.values()) | set(T.RAIN_SOURCES) | {h.get("rain_source", "avg") for h in heads.values()})
    frames, notes = T.build_dataset(sources=sources)
    finals = {k: {"model": m["model"], "features": m["features"], "calibration_offset": m["calibration_offset"],
                  **({"C": m["C"]} if "C" in m else {})} for k, m in models.items()}
    holdouts = _refit_holdouts(models, chosen, frames, features)

    # fidelity: refit siblings must reproduce the source artifact's stored holdout probabilities
    by_date = {d["date"]: d for d in src_sc.get("days", [])}
    worst = 0.0
    for basin in T.APP_BASINS:
        key = BASIN_KEYS[basin]
        if holdouts.get(key) is None:
            continue
        X = frames[chosen[basin]]
        ph = T.calibrated({**finals[key], "model": holdouts[key]}, X)
        for i, d in enumerate(X["date"]):
            st = by_date.get(str(d.date()), {}).get("basins", {}).get(key, {}).get("ph")
            if st is not None:
                worst = max(worst, abs(float(ph[i]) - st))
    print(f"holdout siblings refit from {stage1}: reproduce the stored holdout probabilities to within {worst:.4f}")
    if worst > 0.002:
        raise SystemExit("refit holdout models do not match the source artifact — refusing to write")

    per_basin = {}
    if stage1 == "v4":
        ev = json.loads((T.SERVE_DIR / "eval_report.json").read_text())
        for key, t in ev.get("targets", {}).items():
            per_basin[key] = {"source": t.get("rain_source"), "holdout": t.get("holdout"), "n_events": t.get("n_events")}
    else:
        man = json.loads((candidates.candidate_dir(stage1) / "manifest.json").read_text())
        per_basin = man.get("per_basin", {})
    note = note or f"Stage 1 {stage1_name} (from {stage1}) composed with stage 2 {variant} ({spec.get('kind', '')}): {spec.get('definition', '')}"
    d = candidates.save_candidate(name, family, finals, holdouts, chosen, features, per_basin, note=note,
                                  extra={"stage1_source": stage1}, stage2=spec, stage1_from=stage1, stage1_name=stage1_name)
    print(f"candidate → {d.relative_to(REPO)} (pickles copied from {stage1}, stage2.json, manifest, scorecard)")
    return d


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "fit":
        fit(args[args.index("--variant") + 1] if "--variant" in args else "v2")
    elif args[0] == "save":
        get = lambda flag, default=None: args[args.index(flag) + 1] if flag in args else default  # noqa: E731
        save(get("--stage1", "v4"), get("--variant", "v2"), get("--name"), get("--note", ""))
    else:
        raise SystemExit(__doc__)
