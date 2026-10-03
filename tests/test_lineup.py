"""The stage lineup (shared/lineup.py, STAGES_DESIGN.md A8): every set anyone can read has plain words for each
part, the Model check marks exactly the parts that differ from the served set, today's-basins parts agree with
what the stage build wrote, and the shared-terms counts are the designs' own.
Run: venv/bin/python tests/test_lineup.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "features" / "forecast" / "src" / "models"
for p in (str(ROOT), str(ROOT / "features" / "forecast"), str(MODELS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared import lineup as LU  # noqa: E402

STAGES = ROOT / "features" / "forecast" / "data" / "models" / "stages"


def _model_check_sets() -> list:
    from features.forecast import live_dashboard as ld
    from features.forecast import page
    eng = ld.LiveData.__new__(ld.LiveData)
    return page.with_lineups(eng.list_models())


def test_every_model_check_set_reads_in_words_and_marks_what_differs():
    sets = _model_check_sets()
    served = [m for m in sets if m["served"]]
    assert len(served) == 1 and not any(x["differs"] for x in served[0]["lineup"])
    for m in sets:
        parts = LU.geo_v1_parts(m["stage1"], m["stage2"])
        assert [x["col"] for x in m["lineup"]] == ["s2", "s3", "s4"], m["key"]
        for x in m["lineup"]:
            assert x["words"] == LU.words(x["col"], parts[x["col"]]), (m["key"], x)   # never the id fallback
        base = LU.geo_v1_parts(served[0]["stage1"], served[0]["stage2"])
        assert [x["differs"] for x in m["lineup"]] == [parts[c] != base[c] for c in ("s2", "s3", "s4")], m["key"]
    names = [" · ".join(x["words"] for x in m["lineup"]) for m in sets]
    assert len(set(names)) == len(names), "two sets read the same"


def test_every_scored_set_has_words_for_every_part():
    built = sorted(d for d in STAGES.iterdir() if (d / "manifest.json").exists())
    assert built, "no stage artifacts"
    for d in built:
        man = json.loads((d / "manifest.json").read_text())
        comps = {"geography": man["geography"], **man["components"]}
        for col, _ in LU.COLUMNS:
            LU.words(col, comps[col])


def test_todays_basin_parts_are_what_the_stage_build_wrote():
    import candidates as C
    sv = C.served_info()
    for d in sorted(STAGES.iterdir()):
        mp = d / "manifest.json"
        if not mp.exists():
            continue
        man = json.loads(mp.read_text())
        if man["geography"] != "geo_v1":
            continue
        if man["set"] == sv["name"]:
            stage1, variant = sv["stage1"], sv["stage2"]
        else:
            cm = json.loads((C.candidate_dir(man["set"]) / "manifest.json").read_text())
            stage1, variant = cm["stage1"]["name"], cm["stage2"]["variant"]
        assert LU.geo_v1_parts(stage1, variant) == {c: man["components"][c] for c in ("s2", "s3", "s4")}, man["set"]


def test_shared_terms_counts_are_the_designs_bands():
    import leaderboard as L
    for key, design in L.SHARED_DESIGNS.items():
        words = LU.WORDS["s2"][f"logit_v2_{key}"]
        assert words.startswith(f"{len(L.band_columns(design))}-term model"), (key, words)


def test_the_report_reads_the_same_map():
    import export_stages_report as R
    assert R.LABELS is LU.WORDS and R.LINEUP_COLS is LU.COLUMNS and R.label is LU.words


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
