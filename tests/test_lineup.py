"""The stage lineup (shared/lineup.py, STAGES_DESIGN.md A8 and Part B 36): every set anyone can read has plain words
for each part, the Model check marks exactly the parts that differ from the served set, today's-basins parts agree
with what the stage build wrote, the shared-terms counts are the designs' own, and every set's stored name is its
recorded lineup's id (the codes one per part, the old names mapped once).
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
    """The Model check names a set by its five parts as it was made (its recorded lineup), the parts agree with its
    two stored halves, and the parts it does not share with the served set are marked."""
    from features.forecast import live_dashboard as ld
    raw = {m["key"]: m for m in ld.LiveData.__new__(ld.LiveData).list_models()}
    sets = _model_check_sets()
    served = [m for m in sets if m["served"]]
    assert len(served) == 1 and not any(x["differs"] for x in served[0]["lineup"])
    base = raw[served[0]["key"]]["lineup"]
    for m in sets:
        lineup = raw[m["key"]]["lineup"]
        assert {c: lineup[c] for c in ("s2", "s3", "s4")} == LU.geo_v1_parts(m["stage1"], m["stage2"]), m["key"]
        assert [x["col"] for x in m["lineup"]] == list(LU.STAGE_COLS), m["key"]
        for x in m["lineup"]:
            assert x["words"] == LU.words(x["col"], lineup[x["col"]]), (m["key"], x)   # never the id fallback
        assert [x["differs"] for x in m["lineup"]] == [lineup[c] != base[c] for c in LU.STAGE_COLS], m["key"]
        assert (m["key"] or served[0]["label"].removesuffix(" (served)")) == LU.set_id(lineup), m["key"]
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


def test_every_build_is_stored_under_its_lineups_id():
    for d in sorted(STAGES.iterdir()):
        mp = d / "manifest.json"
        if mp.exists():
            man = json.loads(mp.read_text())
            assert d.name == man["set"] == LU.set_id({"geography": man["geography"], **man["components"]}), d.name


# ── names (Part B 36) ───────────────────────────────────────────────────────

def test_every_set_on_disk_is_named_by_its_recorded_lineup():
    import set_names as SN
    assert SN.check() == []
    assert SN.plan() == [], "an old name is still on disk"


def test_the_served_lineup_is_what_serves():
    """served.json's lineup is the page's: its weather model (S1) and its correction rule (S5) as they run now, so a
    switch of either renames the served set in the same commit (promote.py --corrections / --rename-served)."""
    import candidates as C
    import promote
    sv = C.served_info()
    assert sv["lineup"] == promote.served_lineup_now(sv) and sv["name"] == LU.set_id(sv["lineup"])


def test_every_part_has_one_code_of_its_own():
    """A part with words has a code; a code is letters and digits only (an id splits on "-"); two parts share a
    code only when they share their words (the same rule under two ids), and only the BWTF basins have none."""
    assert set(LU.CODES) == set(LU.WORDS) == {c for c, _ in LU.COLUMNS} == set(LU.NAME_COLS)
    for col, codes in LU.CODES.items():
        assert set(codes) == set(LU.WORDS[col]), col
        for cid, c in codes.items():
            assert c.isalnum() and c == c.lower() or (col, cid) == ("geography", "geo_v1") and c == "", (col, cid, c)
            same = {k for k, v in codes.items() if v == c}
            assert len({LU.WORDS[col][k] for k in same}) == 1, (col, c, sorted(same))


def test_the_old_names_map_once():
    """RENAMED maps each name a set carried before 2026-10-07 to a distinct id; no id is itself an old name, so
    current_name is the id after one step, and every id parses back into one code per part."""
    ids = list(LU.RENAMED.values())
    assert len(set(ids)) == len(ids) and not set(ids) & set(LU.RENAMED)
    assert all(LU.current_name(LU.current_name(o)) == LU.current_name(o) == n for o, n in LU.RENAMED.items())
    for n in ids:
        parts = n.split("-")
        geo = "sfpuc4_v1" if parts[0] == "sfpuc" else "geo_v1"
        parts = parts[1:] if geo != "geo_v1" else parts
        assert len(parts) == len(LU.STAGE_COLS), n
        for col, c in zip(LU.STAGE_COLS, parts):
            assert c in LU.CODES[col].values(), (n, col, c)


def test_the_saver_refuses_a_hand_picked_name():
    """candidates.save_candidate stores a set under its lineup's id only, and its overflow model must be the
    lineup's S2; it refuses before anything is written."""
    import candidates as C
    lineup = C.geo_v1_lineup("logit_v1", None)
    assert C.set_name(lineup) == "icon-w38-nosplit-lt1-bflags" == C.geo_v1_set("logit_v1", None)[0]
    for name, kw in (("logit_v1_d10", {"lineup": lineup}), (C.set_name(lineup), {}),
                     (C.set_name(lineup), {"lineup": lineup, "stage1_name": "gb_v1"})):
        try:
            C.save_candidate(name, "logit", {}, {}, {}, [], {}, **kw)
            raise AssertionError(f"{name} {kw} was saved")
        except ValueError:
            pass
    assert not C.candidate_dir("logit_v1_d10").exists()


def test_a_set_id_reads_its_parts_in_order():
    lu = {"geography": "geo_v1", "s1": "icon_seamless", "s2": "logit_v1", "s3": "split_v2", "s4": "impact_v2_d10", "s5": "live_v2"}
    assert LU.set_id(lu) == "icon-w38-osplit-lt2more-bflags"
    assert LU.full_words(lu) == "ICON · 38-weight · Outfall split · Linger table 2, more samples · Basin flags"
    assert LU.set_id({**lu, "geography": "sfpuc4_v1", "s5": "link_zone_v1"}) == "sfpuc-icon-w38-osplit-lt2more-lzflags"
    assert LU.geo_v1_lineup("logit_v1", "v2_d10", "icon_seamless", "live_v2") == lu
    for bad in ({k: v for k, v in lu.items() if k != "s5"}, {**lu, "s1": "nam"}):
        try:
            LU.set_id(bad)
            raise AssertionError(f"{bad} has no id")
        except KeyError:
            pass


def test_shared_terms_counts_are_the_designs_bands():
    import leaderboard as L
    for key, design in L.SHARED_DESIGNS.items():
        words = LU.WORDS["s2"][f"logit_v2_{key}"]
        if key == "four":   # named by its inputs, not its terms
            assert words == "Four-input" and len(design) == 4, (key, words)
        else:
            assert words == f"{len(L.band_columns(design))}-term", (key, words)


def test_names_are_distinct_and_never_relative():
    """Every part has one name of its own within its stage (two ids may share one only when they are the same
    rule under two names: the replay's and the served one), and no name says "today's" (Chase, 2026-10-04)."""
    same = {frozenset({"live_v2", "basin_swap"}), frozenset({"link_zone_swap", "link_zone_v1"})}
    for col, words in LU.WORDS.items():
        for name in set(words.values()):
            ids = frozenset(k for k, v in words.items() if v == name)
            assert len(ids) == 1 or ids in same, (col, name, sorted(ids))
            assert "today" not in name.lower(), (col, name)


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
