#!/usr/bin/env python3
"""The model catalog: every forecast set on disk in one readable file, data/models/catalog.json (Chase, 2026-10-07:
"there should be a database (Json?) with each model and its description and its weights").

One entry per set, the live one first, then the candidates on BWTF basins and the stage candidates on SFPUC basins,
each by name:

    name, status        its id (shared/lineup.py set_id) and live | candidate | retired (served once)
    words, parts        its five stages in words, and each part's component id (its lineup, as it was made)
    description         its own notes (a manifest's note; a stage candidate's S4 note and S5 choice)
    trained_through, record, tags, files, explorer, stage_scores
    overflow_model      S2 per basin: its rain source, C, intercept and every term's weight. A weight is per
                        standard deviation of its term: p = 1 / (1 + exp(−(intercept + Σ weight × (x − mean) / sd))),
                        where a term "f>k" is max(0, f − k) and a band term is clip(f − lo, 0, hi − lo). Trees list
                        their size and each input's share of the split gain instead (a tree ensemble has no weights).

Everything is read from the files the forecast runs on (the pickles through export_model_explorer.export_stage1, the
manifests, served.json), with no clock, so rebuilding it unchanged gives the same bytes (tests/test_catalog.py).
Re-run after any save, promotion or rebuild:

    venv/bin/python features/forecast/src/models/export_catalog.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (str(REPO), str(HERE), str(HERE.parent / "collectors")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import candidates as CAND  # noqa: E402
import export_model_explorer as EX  # noqa: E402  (export_stage1: a pickle's weights as plain numbers)
import leaderboard  # noqa: E402,F401  (weights pipelines reference leaderboard.add_hinges)
import stages_candidates as SC  # noqa: E402
from shared import lineup as LU  # noqa: E402

OUT = CAND.SERVE_DIR / "catalog.json"
STAGES = CAND.SERVE_DIR / "stages"
REPORTS = REPO / "reports"
HOW_TO_READ = ("A weight is per standard deviation of its term: p = 1 / (1 + exp(-(intercept + sum of weight * (x - mean) / sd))). "
               "A term 'f>k' is max(0, f - k); a band term is clip(f - lo, 0, hi - lo). Trees have no weights: their size "
               "and each input's share of the split gain are listed instead.")


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def basin_model(m: dict) -> dict:
    """One basin's overflow model as numbers: its weights (logistic) or its size and gain shares (trees)."""
    ex = EX.export_stage1(m, m["features"])
    head = {"rain_source": m.get("rain_source", "avg")}
    if ex["family"] != "logit":
        return {**head, "family": "trees", "n_trees": ex["n_estimators"], "max_depth": ex["max_depth"],
                "learning_rate": ex["learning_rate"], "dry_day_offset": float(m.get("calibration_offset", 0.0)),
                "gain_share": ex["importances"]}
    bands = dict(zip(ex["names"], ex["bands"])) if ex.get("bands") else {}
    terms = []
    for n, w, mu, sd in zip(ex["names"], ex["coef"], ex["means"], ex["scales"]):
        t = {"term": n, "weight": w, "mean": mu, "sd": sd}
        if n in bands:
            t["band"] = {"of": bands[n]["f"], "lo": bands[n]["lo"], "hi": bands[n]["hi"]}
        terms.append(t)
    return {**head, "family": "logistic", "C": ex["C"], "intercept": ex["intercept"], "terms": terms,
            "dry_day_offset": float(m.get("calibration_offset", 0.0)),
            **({"every_weight_nonnegative": True} if ex.get("nonneg") else {})}


def _common(name: str, lineup: dict) -> dict:
    explorer = REPORTS / f"2026-09_forecast_{name}_model_explorer.html"
    scores = STAGES / name / "scores.json"
    return {"words": LU.full_words(lineup), "basins": LU.words("geography", lineup["geography"]),
            "parts": {c: {"id": lineup[c], "words": LU.words(c, lineup[c])} for c in LU.STAGE_COLS},
            "explorer": f"/reports/{explorer.name}" if explorer.exists() else None,
            "stage_scores": _rel(scores) if scores.exists() else None}


def served_entry() -> dict:
    sv = CAND.served_info()
    import pickle
    models = {}
    for key in ("westside", "north_shore", "central", "southeast", "citywide"):
        with open(CAND.SERVE_DIR / f"{key}_model.pkl", "rb") as f:
            models[key] = pickle.load(f)
    return {"name": sv["name"], "status": "live", **_common(sv["name"], sv["lineup"]),
            "description": sv.get("note", ""), "promoted_at": sv.get("promoted_at"), "line": sv.get("line"),
            "trained_through": sv.get("trained_through"), "record": sv.get("record"), "tags": sv.get("tags") or {},
            "files": _rel(CAND.SERVE_DIR) + "/ (served.json, *_model.pkl, stage2.json)",
            "overflow_model": {k: basin_model(m) for k, m in models.items()}}


def candidate_entry(man: dict) -> dict:
    name = man["name"]
    models = CAND.load_models(name)
    return {"name": name, "status": "retired" if man.get("retired_at") else "candidate", **_common(name, man["lineup"]),
            "description": man.get("note", ""), "created_at": man.get("created_at"), "retired_at": man.get("retired_at"),
            "trained_through": man.get("trained_through"), "record": man.get("record"), "tags": man.get("tags") or {},
            "files": _rel(CAND.candidate_dir(name)) + "/",
            "overflow_model": {k: basin_model(m) for k, m in sorted(models.items())}}


def stage_entry(man: dict) -> dict:
    st = SC.load_set(man["name"])
    s4 = (st.s4_quality or {}).get("note")
    s5 = (man.get("s5") or {}).get("why")
    models = dict(st.models, **({"citywide": st.citywide} if st.citywide else {}))
    return {"name": st.name, "status": "candidate", **_common(st.name, man["lineup"]),
            "description": " ".join(x for x in (s4 and f"S4: {s4}", s5 and f"S5: {s5}") if x),
            "created_at": man.get("created_at"), "trained_through": (man.get("s2") or {}).get("trained_through"),
            "record": None, "tags": man.get("tags") or {}, "files": _rel(st.path) + "/",
            "overflow_model": {k: basin_model(m) for k, m in sorted(models.items())}}


def build() -> dict:
    sv = CAND.served_info()
    sets = [served_entry()]
    sets += [candidate_entry(m) for m in sorted(CAND.list_candidates(), key=lambda m: m["name"]) if m["name"] != sv["name"]]
    sets += [stage_entry(m) for m in SC.list_sets()]
    return {"about": "Every forecast set on disk, generated by features/forecast/src/models/export_catalog.py; never edit by hand.",
            "how_to_read": HOW_TO_READ, "live": sv["name"], "n_sets": len(sets), "sets": sets}


def write(path: Path = OUT) -> Path:
    path.write_text(json.dumps(build(), indent=1, ensure_ascii=False, allow_nan=False) + "\n")
    return path


if __name__ == "__main__":
    p = write(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(f"wrote {_rel(p) if p.is_relative_to(REPO) else p} ({p.stat().st_size / 1e3:.0f} KB)")
