#!/usr/bin/env python3
"""Promote a candidate set to the served bundle — and retire the served set to a candidate.

    venv/bin/python features/forecast/src/models/promote.py logit_v1_s2v2 --line 0.25 [--dry-run]

What moves (data/models/):
  * the served stage-1 pickles, scorecard and stage2.json (if any) are copied
    into data/models/candidates/<old served name>/ with a synthesized manifest,
    so the retired set stays gradable next to everything else;
  * the candidate's pickles, scorecard.json.gz and stage2.json (or none for a
    v1 set) replace the served ones;
  * data/models/served.json describes the new served set (name, stage 1,
    stage 2, family, operating line, provenance) — candidates.SERVED reads it;
  * the candidate's directory is removed (a set is served or a candidate,
    never both);
  * other candidates whose manifest says stage 1 came "from served" are
    re-pointed at the retired set by name.

Untouched: the volume heads, impact_table.json (the v1 table the variant
fitters still read), eval_report.json and the other gb_v1 training artifacts
(they describe the original training run and stay as its record).

After promoting: re-export the explorers (served + the retired candidate),
export_stage2_explorer, report_models; run `train_v4.py --rescore --replace-post`
as the fidelity check (the served models must reproduce the stored zone risks);
run the tests; deploy. Nothing here touches Supabase.
"""
from __future__ import annotations

import gzip
import json
import pickle
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import candidates as C  # noqa: E402

SERVE_DIR = C.SERVE_DIR
KEYS = ("citywide", "westside", "north_shore", "central", "southeast")


def _load_pickles(directory: Path) -> dict:
    import leaderboard  # noqa: F401  (logit pipelines reference leaderboard.add_hinges)
    out = {}
    for k in KEYS:
        with open(directory / f"{k}_model.pkl", "rb") as f:
            out[k] = pickle.load(f)
    return out


def _scorecard(path: Path) -> dict:
    with gzip.open(path, "rt") as f:
        return json.load(f)


def retire_served(now: str, dry_run: bool) -> str:
    """Copy the served set into candidates/<its name>/ with a manifest. Returns its name."""
    served = C.served_info()
    name = served["name"]
    dest = C.candidate_dir(name)
    if dest.exists():
        raise SystemExit(f"candidates/{name}/ already exists — refusing to overwrite the retired set's record")
    models = _load_pickles(SERVE_DIR)
    sc = _scorecard(SERVE_DIR / "scorecard.json.gz")
    s2_path = SERVE_DIR / "stage2.json"
    s2 = json.loads(s2_path.read_text()) if s2_path.exists() else None
    family = served.get("family") or models["westside"].get("family") or "gb"
    per_basin = served.get("per_basin")
    if per_basin is None:   # the original gb_v1 bundle: its training report carries the per-basin picks
        ev_path = SERVE_DIR / "eval_report.json"
        ev = json.loads(ev_path.read_text()) if ev_path.exists() else {}
        per_basin = {k: {"source": t.get("rain_source"), "holdout": t.get("holdout"), "n_events": t.get("n_events")}
                     for k, t in ev.get("targets", {}).items()}
    manifest = {
        "name": name, "family": family,
        "note": f"Retired from serving on {now[:10]} (served {served.get('promoted_at', 'from the 2026-09-12 launch')[:10]} → {now[:10]}); "
                f"stage 1 {served['stage1']} with stage 2 {served['stage2']}. Kept as a candidate so it stays gradable.",
        "created_at": models["westside"].get("trained_at") or now, "retired_at": now,
        "trained_through": sc.get("trained_through") or sc["span"][1], "span": sc["span"], "holdout_start": sc.get("holdout_start"),
        "rain_sources": {k: m.get("rain_source", "avg") for k, m in models.items()},
        "per_basin": per_basin,
        "stage1": {"name": served["stage1"], "from": "retired-served", "family": family},
        "stage2": {"variant": served["stage2"], "kind": (s2 or {}).get("kind", "basin composition"),
                   "impact_table_refit": bool(s2 and s2.get("impact_table")), "fitted_at": (s2 or {}).get("fitted_at")},
        "zone_confusion_holdout": sc.get("zone_confusion_holdout"), "input_rules_post": sc.get("input_rules_post") or [],
        "stage1_source": "retired-served",
    }
    print(f"retire {name}: {len(models)} pickles, scorecard ({len(sc['days'])} days), stage2 {served['stage2']} → candidates/{name}/")
    if not dry_run:
        dest.mkdir(parents=True)
        for k in KEYS:
            shutil.copy2(SERVE_DIR / f"{k}_model.pkl", dest / f"{k}_model.pkl")
        shutil.copy2(SERVE_DIR / "scorecard.json.gz", dest / "scorecard.json.gz")
        if s2 is not None:
            shutil.copy2(s2_path, dest / "stage2.json")
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    return name


def promote(candidate: str, line: float, dry_run: bool = False) -> dict:
    if not C.valid_name(candidate):
        raise SystemExit(f"bad candidate name {candidate!r}")
    src = C.candidate_dir(candidate)
    if not src.exists():
        raise SystemExit(f"no candidate {candidate}")
    if candidate == C.served_info()["name"]:
        raise SystemExit(f"{candidate} is already the served set")
    now = datetime.now().isoformat(timespec="seconds")
    man = json.loads((src / "manifest.json").read_text())
    s2 = C.load_stage2(candidate)
    models = _load_pickles(src)
    sc = _scorecard(src / "scorecard.json.gz")
    if (sc.get("stage2") or {}).get("variant", "v1") != (s2 or {}).get("variant", "v1"):
        raise SystemExit("candidate scorecard and stage2.json disagree on the variant")

    retired = retire_served(now, dry_run)

    served = {
        "name": candidate, "stage1": (man.get("stage1") or {}).get("name", candidate), "stage2": (s2 or {}).get("variant", "v1"),
        "artifact": models["westside"].get("version", candidate), "family": man.get("family") or models["westside"].get("family", "gb"),
        "line": float(line), "promoted_at": now, "from_candidate": candidate, "replaced": retired,
        "created_at": man.get("created_at"), "trained_through": man.get("trained_through"), "holdout_start": man.get("holdout_start"),
        "rain_sources": man.get("rain_sources"), "per_basin": man.get("per_basin"), "input_rules_post": man.get("input_rules_post"),
        "stage2_kind": (s2 or {}).get("kind", "basin composition"), "note": man.get("note", ""),
    }
    print(f"promote {candidate}: stage 1 {served['stage1']} ({served['family']}), stage 2 {served['stage2']}, line {line:.2f}; replaces {retired}")
    if not dry_run:
        for k in KEYS:
            shutil.copy2(src / f"{k}_model.pkl", SERVE_DIR / f"{k}_model.pkl")
        shutil.copy2(src / "scorecard.json.gz", SERVE_DIR / "scorecard.json.gz")
        if s2 is not None:
            shutil.copy2(src / "stage2.json", SERVE_DIR / "stage2.json")
        elif (SERVE_DIR / "stage2.json").exists():
            (SERVE_DIR / "stage2.json").unlink()
        C.SERVED_FILE.write_text(json.dumps(served, indent=1, default=str) + "\n")
        shutil.rmtree(src)
        # candidates built on the retired set's stage 1 said "from served"; name the set now
        for m in C.list_candidates():
            mp = C.candidate_dir(m["name"]) / "manifest.json"
            mm = json.loads(mp.read_text())
            st1 = mm.get("stage1") or {}
            if st1.get("from") in ("served", "v4") or mm.get("stage1_source") in ("served", "v4"):
                st1["from"] = retired
                mm["stage1"] = st1
                mm["stage1_source"] = retired
                mp.write_text(json.dumps(mm, indent=1, default=str))
                print(f"   {m['name']}: stage 1 provenance 'served' → {retired}")
    return served


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        raise SystemExit(__doc__)
    line = float(args[args.index("--line") + 1]) if "--line" in args else 0.5
    promote(args[0], line, dry_run="--dry-run" in args)
