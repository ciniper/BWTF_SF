"""Candidate model sets — alternatives to the served v4 models that the Model
check page can score side by side. Nothing here is ever served live: the
forecast's predictions always come from data/models/ (v4). A candidate is a
directory of pickles with the same contract as the served ones plus its own
scorecard artifact, so every window the page can score for v4 it can score
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


def build_candidate_scorecard(finals: dict, holdout_models: dict, chosen: dict, features: list) -> dict:
    """The trainer's scorecard for a candidate: its stage-1 models, the served
    stage-2 (volume heads + impact table — stage 2 is not what candidates
    vary), over every day the refreshed inputs cover. Days after TRAIN_END are
    post-training: the candidate never saw them, so they get no holdout
    probability and are graded as the served artifact grades its own."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import train_v4 as T
    from scorecard import zone_confusion

    end = min(T._inputs_reach().values())
    sources = sorted(set(chosen.values()) | set(T.RAIN_SOURCES))
    frames, notes = T.build_dataset(end=end, sources=sources)
    heads, impact_raw = T.stage2_from_served()
    sc = T.build_scorecard(frames, chosen, finals, holdout_models, heads, impact_raw, T.load_samples(),
                           T.archive_tables(), notes["archive_used"], features)
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


def save_candidate(name: str, family: str, finals: dict, holdout_models: dict, chosen: dict, features: list,
                   per_basin: dict, note: str = "", extra: dict | None = None) -> Path:
    """Write pickles + scorecard + manifest. `finals[key]` = {"model", "features",
    "calibration_offset"}; `chosen[basin name]` = rain source; `per_basin[key]`
    = anything worth keeping about how the model was picked (source, C, scores)."""
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
                         "trained_at": now, "version": name, "family": family, **{k: v for k, v in fin.items() if k in ("C",)}}, f)
    sc = build_candidate_scorecard(finals, holdout_models, chosen, features)
    sc["candidate"] = name
    with gzip.open(d / "scorecard.json.gz", "wt") as f:
        json.dump(sc, f, separators=(",", ":"), default=str)
    manifest = {"name": name, "family": family, "note": note, "created_at": now,
                "trained_through": sc["trained_through"], "span": sc["span"], "holdout_start": sc["holdout_start"],
                "rain_sources": {BASIN_KEYS.get(b, b): s for b, s in chosen.items()}, "per_basin": per_basin,
                "zone_confusion_holdout": sc["zone_confusion_holdout"], **(extra or {})}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    return d
