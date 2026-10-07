#!/usr/bin/env python3
"""set_names — a set's stored name is its lineup's id (shared/lineup.py ``set_id``), and the one-time move of
2026-10-07 that renamed every set to it (Chase: "the full model name should have a combo of each stage at the
time's name"; STAGES_DESIGN.md Part B 36).

Every set records its ``lineup`` (basins and S1 … S5, each part's component id at the time it was made) in its
manifest, and served.json records the live set's. ``check`` holds every set on disk to name == set_id(lineup).

The move (``migrate``), for every name in lineup.RENAMED:
  candidates/<old>/            → candidates/<id>/: manifest name, lineup and ``renamed_from``; a set name its
                                 manifest quotes (stage1.from, stage1_source, and every unambiguous old name in its
                                 text) mapped; the scorecard's ``candidate``
  stages_candidates/<old>/     → stages_candidates/<id>/: every file's ``set`` stamp restamped, every old set name
                                 inside its specs and manifest mapped, the sha256s recomputed (the loader's checks
                                 pass on the result); the bake-off's results.json likewise
  served.json                  name, lineup, renamed_from, from_candidate, replaced; the served scorecard's
                               ``candidate``
  stages/<old>/, reports/…<old>…_model_explorer.html
                               removed: the stages build and the exporters write them again under the new names
Pickles keep their own ``version`` stamps (the artifact as it was fit; served.json's ``artifact`` names it).

An old name that is also an S2 component id (gb_v1, logit_v1, logit_v2_*) is mapped only where a field holds a set
name; inside text only the unambiguous old names are (``rename_text``).

    venv/bin/python features/forecast/src/models/set_names.py            # the plan and the check, writes nothing
    venv/bin/python features/forecast/src/models/set_names.py --migrate
"""
from __future__ import annotations

import argparse
import gzip
import json
import pickle
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import candidates as CAND  # noqa: E402
from shared import lineup as LU  # noqa: E402

REPO = HERE.parents[3]
STAGES_DIR = CAND.SERVE_DIR / "stages"
STAGE_CANDIDATES = CAND.SERVE_DIR / "stages_candidates"
BAKEOFF = STAGE_CANDIDATES / "_bakeoff" / "results.json"
REPORTS = REPO / "reports"
# the weather model and correction rule every set on BWTF basins was scored with on 2026-10-07 (stages_build: the
# served weather model, live_rules' version): the S1 and S5 of its lineup at the time
GEO_V1_S1, GEO_V1_S5 = "icon_seamless", "live_v2"
NOT_SETS = ("fit", "served", "retired-served")          # stage1.from values that name no set

AMBIGUOUS = {old for old in LU.RENAMED if old in LU.CODES["s2"]}     # also an S2 component id
_RX = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(map(re.escape, set(LU.RENAMED) - AMBIGUOUS), key=len, reverse=True))
                 + r")(?![A-Za-z0-9_])")


def rename_text(s: str) -> str:
    """``s`` with every unambiguous old set name replaced by its id."""
    return _RX.sub(lambda m: LU.RENAMED[m.group(1)], s)


def rename_json(o):
    if isinstance(o, dict):
        return {rename_text(k) if isinstance(k, str) else k: rename_json(v) for k, v in o.items()}
    if isinstance(o, list):
        return [rename_json(v) for v in o]
    return rename_text(o) if isinstance(o, str) else o


def _set_name(v):
    """A field that holds a set name (or one of NOT_SETS), mapped."""
    return v if v in NOT_SETS or not isinstance(v, str) else LU.current_name(v)


# ── lineups ────────────────────────────────────────────────────────────────

def candidate_lineup(man: dict) -> dict:
    """A BWTF-basin candidate's lineup: its recorded one, else its overflow model and stage 2 id with GEO_V1_S1/S5."""
    if man.get("lineup"):
        return dict(man["lineup"])
    stage1 = (man.get("stage1") or {}).get("name") or man["name"]
    return LU.geo_v1_lineup(stage1, (man.get("stage2") or {}).get("variant", "v1"), GEO_V1_S1, GEO_V1_S5)


def stage_lineup(man: dict) -> dict:
    return dict(man.get("lineup") or {"geography": man["geography"], **{c: man["components"][c] for c in LU.STAGE_COLS}})


def served_lineup(sv: dict) -> dict:
    return dict(sv.get("lineup") or LU.geo_v1_lineup(sv["stage1"], sv["stage2"], GEO_V1_S1, GEO_V1_S5))


def sets_on_disk() -> list:
    """[(kind, name, lineup)] for the served set and every candidate and stage candidate on disk."""
    out = [("served", CAND.served_info()["name"], served_lineup(CAND.served_info()))]
    out += [("candidate", d.name, candidate_lineup(json.loads((d / "manifest.json").read_text())))
            for d in sorted(CAND.CANDIDATES_DIR.iterdir()) if (d / "manifest.json").exists()]
    out += [("stage", d.name, stage_lineup(json.loads((d / "manifest.json").read_text())))
            for d in sorted(STAGE_CANDIDATES.iterdir()) if not d.name.startswith("_") and (d / "manifest.json").exists()]
    return out


def check() -> list:
    """Every set whose stored name is not its lineup's id, or whose manifest records no lineup: [(kind, name, id)]."""
    bad = []
    for kind, name, lineup in sets_on_disk():
        want = LU.set_id(lineup)
        recorded = (CAND.served_info() if kind == "served" else json.loads(
            ((CAND.CANDIDATES_DIR if kind == "candidate" else STAGE_CANDIDATES) / name / "manifest.json").read_text())).get("lineup")
        if name != want or recorded != lineup:
            bad.append((kind, name, want))
    return bad


def plan() -> list:
    """[(kind, old, new)] for every set on disk whose name RENAMED maps; an old name with no entry, or whose entry is
    not its lineup's id, raises."""
    out = []
    for kind, name, lineup in sets_on_disk():
        if name not in LU.RENAMED:
            continue
        new = LU.RENAMED[name]
        if LU.set_id(lineup) != new:
            raise ValueError(f"{name}: RENAMED says {new!r}, its lineup's id is {LU.set_id(lineup)!r}")
        out.append((kind, name, new))
    return out


# ── the move ───────────────────────────────────────────────────────────────

def migrate_candidate(old: str, new: str) -> None:
    src, dst = CAND.CANDIDATES_DIR / old, CAND.CANDIDATES_DIR / new
    if dst.exists():
        raise FileExistsError(dst)
    man = json.loads((src / "manifest.json").read_text())
    lineup = candidate_lineup(man)
    note = man.get("note")
    man = rename_json(man)
    man["note"] = rename_text(note) if isinstance(note, str) else note
    st1 = man.get("stage1") or {}
    if "from" in st1:
        st1["from"] = _set_name(st1["from"])
    if "stage1_source" in man:
        man["stage1_source"] = _set_name(man["stage1_source"])
    man.update(name=new, lineup=lineup, renamed_from=old)
    (src / "manifest.json").write_text(json.dumps(man, indent=1, default=str))
    sp = src / "scorecard.json.gz"
    if sp.exists():
        with gzip.open(sp, "rt") as f:
            sc = json.load(f)
        if sc.get("candidate"):
            sc["candidate"] = new
            with gzip.open(sp, "wt") as f:
                json.dump(sc, f, separators=(",", ":"), default=str)
    src.rename(dst)


def migrate_stage(old: str, new: str) -> None:
    import stages_candidates as SC
    SC.load_set(old)                                         # it loads as saved before anything moves
    src, dst = STAGE_CANDIDATES / old, STAGE_CANDIDATES / new
    if dst.exists():
        raise FileExistsError(dst)
    man = json.loads((src / "manifest.json").read_text())
    lineup = stage_lineup(man)
    dst.mkdir()
    for fname in man["files"]:
        p = src / fname
        if fname.endswith(".pkl"):
            with open(p, "rb") as f:
                obj = pickle.load(f)
            obj["set"] = new
            SC._write_atomic(dst / fname, pickle.dumps(obj))
        else:
            SC._write_atomic(dst / fname, SC._json_bytes(rename_json(json.loads(p.read_text()))))
        man["files"][fname] = SC._sha(dst / fname)
    man = rename_json(man)
    man.update(name=new, lineup=lineup, renamed_from=old)
    SC._write_atomic(dst / "manifest.json", SC._json_bytes(man))
    shutil.rmtree(src)


def migrate_served(old: str, new: str) -> None:
    sv = json.loads(CAND.SERVED_FILE.read_text())
    lineup = served_lineup(sv)
    note = sv.get("note")
    sv.update(name=new, lineup=lineup, renamed_from=old, from_candidate=_set_name(sv.get("from_candidate")),
              replaced=_set_name(sv.get("replaced")))
    if isinstance(note, str):
        sv["note"] = rename_text(note)
    CAND.SERVED_FILE.write_text(json.dumps(sv, indent=1, default=str) + "\n")
    sp = CAND.SERVE_DIR / "scorecard.json.gz"                 # the served scorecard names its set too
    with gzip.open(sp, "rt") as f:
        sc = json.load(f)
    if sc.get("candidate") == old:
        sc["candidate"] = new
        with gzip.open(sp, "wt") as f:
            json.dump(sc, f, separators=(",", ":"), default=str)


def migrate(log=print) -> list:
    steps = plan()
    for kind, old, new in steps:
        {"served": migrate_served, "candidate": migrate_candidate, "stage": migrate_stage}[kind](old, new)
        log(f"  {kind:9} {old} → {new}")
        for stale in (STAGES_DIR / old, REPORTS / f"2026-09_forecast_{old}_model_explorer.html"):
            if stale.is_dir():
                shutil.rmtree(stale)
            elif stale.exists():
                stale.unlink()
    if BAKEOFF.exists():                                      # the bake-off names the set its winner was saved as
        BAKEOFF.write_text(json.dumps(rename_json(json.loads(BAKEOFF.read_text())), indent=1, allow_nan=False) + "\n")
    return steps


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--migrate", action="store_true")
    a = ap.parse_args(argv)
    if a.migrate:
        migrate()
    else:
        for kind, old, new in plan():
            print(f"  {kind:9} {old:32} → {new}")
    bad = check()
    print("every set's name is its lineup's id" if not bad else f"{len(bad)} sets to rename: {bad}")


if __name__ == "__main__":
    main()
