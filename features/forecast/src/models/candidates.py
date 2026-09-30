"""Candidate model sets — alternatives to the served gb_v1 models that the Model
check page can score side by side. Nothing here is ever served live: the
forecast's predictions always come from data/models/ (the served gb_v1 bundle). A candidate is a
directory of pickles with the same contract as the served ones plus its own
scorecard artifact, so every window the page can score for the served set it can score
for the candidate too, on identical labels and identical days.

    data/models/candidates/<name>/
        manifest.json      name, family, note, created_at, trained_through,
                           per-basin {rain_source, C, holdout scores}
        {key}_model.pkl    model, features, calibration_offset, rain_source, version, family
        scorecard.json.gz  build_scorecard over the full input reach; days after
                           trained_through carry post_training=true and no
                           holdout probability (same convention as --rescore)

Writers: src/models/leaderboard.py --save <name>. Readers: live_dashboard
(scorecards + manifests only; it never unpickles a candidate).
"""
from __future__ import annotations

import gzip
import json
import pickle
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVE_DIR = HERE.parents[1] / "data" / "models"
CANDIDATES_DIR = SERVE_DIR / "candidates"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")

# The served set, described once, in data/models/served.json — written by
# promote.py when a candidate is promoted. Without the file the served set is
# the original gb_v1 bundle (stage 1 trees whose pickles still say "v4" — a
# file name only; never call the model v4 — composed with stage 2 v1). The live
# snapshot stamps itself with this (live_dashboard.model_stamp) so every
# forecast_history row says which model made it; the analysis report, the
# Model check and the explorers label the served set from it too. ``line`` is
# the operating line the served set is meant to run at (the page's alarm and
# the Model check's default).
SERVED_FILE = SERVE_DIR / "served.json"
SERVED_DEFAULT = {"name": "gb_v1", "stage1": "gb_v1", "stage2": "v1", "artifact": "v4", "family": "gb", "line": 0.5}


def served_info() -> dict:
    """The served set's descriptor: served.json merged over the default."""
    out = dict(SERVED_DEFAULT)
    if SERVED_FILE.exists():
        out.update(json.loads(SERVED_FILE.read_text()))
    return out


SERVED = served_info()


def valid_name(name: str) -> bool:
    return bool(name) and bool(NAME_RE.match(name))


def candidate_dir(name: str) -> Path:
    if not valid_name(name):
        raise ValueError(f"bad candidate name {name!r}")
    return CANDIDATES_DIR / name


def list_candidates() -> list[dict]:
    """Manifests of every candidate on disk, oldest first."""
    out = []
    if CANDIDATES_DIR.exists():
        for d in sorted(CANDIDATES_DIR.iterdir()):
            m = d / "manifest.json"
            if d.is_dir() and m.exists() and valid_name(d.name):
                try:
                    out.append(json.loads(m.read_text()))
                except json.JSONDecodeError:
                    continue
    return sorted(out, key=lambda m: m.get("created_at", ""))


def load_scorecard(name: str) -> dict:
    p = candidate_dir(name) / "scorecard.json.gz"
    if not p.exists():
        return {}
    with gzip.open(p, "rt") as f:
        return json.load(f)


def load_models(name: str) -> dict:
    """Unpickle a candidate's stage-1 models: {key: {"model", "features",
    "calibration_offset", "rain_source", "family", ...}}. Offline tooling only
    (explorer exports, tests) — serving never calls this.

    Logit pipelines reference their hinge transform as ``leaderboard.add_hinges``,
    so the leaderboard module is imported first. Pickles written before the
    leaderboard's entry point dispatched through the module reference
    ``__main__.add_hinges`` instead; the alias below lets those load too."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import leaderboard  # noqa: F401  (defines add_hinges at module level)
    main_mod = sys.modules.get("__main__")
    if main_mod is not None and not hasattr(main_mod, "add_hinges"):
        main_mod.add_hinges = leaderboard.add_hinges
    out = {}
    for p in sorted(candidate_dir(name).glob("*_model.pkl")):
        with open(p, "rb") as f:
            out[p.name[: -len("_model.pkl")]] = pickle.load(f)
    return out


def load_stage2(name: str) -> dict | None:
    """The candidate's stage 2 variant spec (stage2.json), or None = stage 2 v1 (served)."""
    p = candidate_dir(name) / "stage2.json"
    return json.loads(p.read_text()) if p.exists() else None


def build_candidate_scorecard(finals: dict, holdout_models: dict, chosen: dict, features: list,
                              stage2: dict | None = None) -> dict:
    """The trainer's scorecard for a candidate: its stage-1 models and either
    stage 2 v1 (the served volume heads + impact table) or the given stage 2
    variant spec (src/models/stage2.py), over every day the refreshed inputs
    cover. Days after TRAIN_END are post-training: the candidate never saw
    them, so they get no holdout probability and are graded as the served
    artifact grades its own."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import train_v4 as T
    from scorecard import zone_confusion

    end = min(T._inputs_reach().values())
    heads, impact_raw = T.stage2_from_served()
    sources = sorted(set(chosen.values()) | set(T.RAIN_SOURCES) | {h.get("rain_source", "avg") for h in heads.values()})
    frames, notes = T.build_dataset(end=end, sources=sources)
    sc = T.build_scorecard(frames, chosen, finals, holdout_models, heads, impact_raw, T.load_samples(),
                           T.archive_tables(), notes["archive_used"], features, stage2=stage2)
    trained_through = str(T.TRAIN_END.date())
    for d in sc["days"]:
        if d["date"] > trained_through:
            d["post_training"] = True
            for z in d["zones"].values():
                z["risk_h"] = None
            for g in d["groups"].values():
                g["risk_h"] = None
            for b in d["basins"].values():
                b["ph"] = None
    sc["zone_confusion_holdout"] = zone_confusion(sc["days"], list(sc["zone_confusion_holdout"]), holdout_only=True)
    sc["trained_through"] = trained_through
    sc["rescored_at"] = datetime.now().isoformat()
    return sc


def rescore_post(name: str, input_rules: list | None = None) -> dict:
    """Re-score a candidate's POST-TRAINING days with its own pickles and stage 2
    on inputs treated by ``input_rules`` (default: what serving applies,
    rain_features.INPUT_RULES_LIVE — the gauge-outage rule). Pre-training days
    stay exactly as stored, so the holdout comparison across sets remains on
    one input treatment and the post-training comparison on another, each
    apples to apples. Mirrors train_v4.rescore(replace_post=True) for the
    served set."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import train_v4 as T
    from rain_features import INPUT_RULES_LIVE
    from groups import BASIN_KEYS
    rules = list(input_rules or INPUT_RULES_LIVE)
    models = load_models(name)
    sc = load_scorecard(name)
    stage2 = load_stage2(name)
    trained_through = sc.get("trained_through") or str(T.TRAIN_END.date())
    basin_of_key = {v: k for k, v in BASIN_KEYS.items()}
    finals = {k: {"model": m["model"], "features": m["features"], "calibration_offset": m["calibration_offset"]} for k, m in models.items()}
    chosen = {basin_of_key.get(k, "citywide"): m.get("rain_source", "avg") for k, m in models.items()}
    heads, impact_raw = T.stage2_from_served()
    end = min(T._inputs_reach().values())
    sources = sorted(set(chosen.values()) | set(T.RAIN_SOURCES) | {h.get("rain_source", "avg") for h in heads.values()})
    frames, notes = T.build_dataset(end=end, sources=sources, input_rules=rules)
    fresh = T.build_scorecard(frames, chosen, finals, {}, heads, impact_raw, T.load_samples(),
                              T.archive_tables(), notes["archive_used"], finals["citywide"]["features"], stage2=stage2)
    old = {d["date"]: d for d in sc["days"]}
    keep = [d for d in sc["days"] if d["date"] <= trained_through]
    new_days = []
    for d in fresh["days"]:
        if d["date"] <= trained_through:
            continue
        d["post_training"] = True
        for z in d["zones"].values():
            z["risk_h"] = None
        for g in d["groups"].values():
            g["risk_h"] = None
        for b in d["basins"].values():
            b["ph"] = None
        new_days.append(d)
    moved = sum(1 for d in new_days if d["date"] in old for zk in d["zones"]
                if abs(d["zones"][zk]["risk"] - old[d["date"]]["zones"][zk]["risk"]) >= 0.05)
    sc["days"] = keep + new_days
    sc["span"] = [sc["span"][0], sc["days"][-1]["date"]]
    sc["input_rules_post"] = rules
    sc["rescored_at"] = datetime.now().isoformat()
    d = candidate_dir(name)
    with gzip.open(d / "scorecard.json.gz", "wt") as f:
        json.dump(sc, f, separators=(",", ":"), default=str)
    mp = d / "manifest.json"
    man = json.loads(mp.read_text())
    man["input_rules_post"] = rules
    man["span"] = sc["span"]
    mp.write_text(json.dumps(man, indent=1, default=str))
    print(f"{name}: {len(new_days)} post-training days re-scored with input rules {rules}; {moved} zone-days moved by ≥ 0.05")
    return sc


def save_candidate(name: str, family: str, finals: dict, holdout_models: dict, chosen: dict, features: list,
                   per_basin: dict, note: str = "", extra: dict | None = None,
                   stage2: dict | None = None, stage1_from: str = "fit", stage1_name: str | None = None) -> Path:
    """Write pickles + scorecard + manifest. `finals[key]` = {"model", "features",
    "calibration_offset"}; `chosen[basin name]` = rain source; `per_basin[key]`
    = anything worth keeping about how the model was picked (source, C, scores).
    `stage2` = a fitted variant spec to compose with (saved as stage2.json);
    None = stage 2 v1 (served). `stage1_from` names where the stage-1 models
    came from ("fit", "served", or another candidate's name); `stage1_name` is the
    stage 1's own name (a freshly fit set is named after itself, e.g. logit_v1;
    the served bundle's stage 1 is gb_v1)."""
    d = candidate_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from groups import BASIN_KEYS
    basin_of_key = {v: k for k, v in BASIN_KEYS.items()}
    now = datetime.now().isoformat(timespec="seconds")
    for key, fin in finals.items():
        with open(d / f"{key}_model.pkl", "wb") as f:
            pickle.dump({"model": fin["model"], "features": fin["features"], "calibration_offset": fin["calibration_offset"],
                         "label": "csd_event_reported", "rain_source": chosen.get(basin_of_key.get(key, "citywide"), "avg"),
                         "trained_at": now, "version": name, "family": family, **{k: v for k, v in fin.items() if k in ("C", "terms")}}, f)
    if stage2:
        (d / "stage2.json").write_text(json.dumps(stage2, indent=1, default=str))
    elif (d / "stage2.json").exists():
        (d / "stage2.json").unlink()
    sc = build_candidate_scorecard(finals, holdout_models, chosen, features, stage2=stage2)
    sc["candidate"] = name
    with gzip.open(d / "scorecard.json.gz", "wt") as f:
        json.dump(sc, f, separators=(",", ":"), default=str)
    manifest = {"name": name, "family": family, "note": note, "created_at": now,
                "trained_through": sc["trained_through"], "span": sc["span"], "holdout_start": sc["holdout_start"],
                "rain_sources": {BASIN_KEYS.get(b, b): s for b, s in chosen.items()}, "per_basin": per_basin,
                "stage1": {"name": stage1_name or name, "from": stage1_from, "family": family},
                "stage2": {"variant": (stage2 or {}).get("variant", "v1"), "kind": (stage2 or {}).get("kind", "basin composition"), "impact_table_refit": bool(stage2 and stage2.get("impact_table")),
                           "fitted_at": (stage2 or {}).get("fitted_at")},
                "zone_confusion_holdout": sc["zone_confusion_holdout"], **(extra or {})}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    return d
