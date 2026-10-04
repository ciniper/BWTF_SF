"""The Model check's stage builder (export_stage_builder.py → data/models/stage_builder.json → /forecast/api/stage-builder;
Chase, 2026-10-04: drop a part into each stage and see how good it is).

Pins: the committed JSON is what the exporter writes today (no clock: the same bytes); every number in it is the
artifact field it names, checked here along paths written out independently of the exporter's tables; every part is
named by shared/lineup.py's WORDS and no name says "today"; every scored set is a chain with its manifest's parts; the
served set is the LIVE one; the route serves the file; the file ships in the app bundle (the stage artifacts do not);
the page reads it and names no set "today's".

    venv/bin/python tests/test_stage_builder.py
"""
from __future__ import annotations

import fnmatch
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_SRC = ROOT / "features" / "forecast" / "src" / "models"
for p in (str(ROOT), str(MODELS_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import export_stage_builder as E  # noqa: E402
from shared import lineup as LU  # noqa: E402

DATA = ROOT / "features" / "forecast" / "data" / "models"
STAGES = DATA / "stages"
JSON_PATH = DATA / "stage_builder.json"
PAGE = ROOT / "app" / "templates" / "forecast" / "page.html"


@lru_cache(maxsize=None)
def built() -> dict:
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def scores(name: str) -> dict:
    return json.loads((STAGES / name / "scores.json").read_text())


@lru_cache(maxsize=None)
def s1_scores() -> dict:
    return json.loads((STAGES / "_s1" / "s1_scores.json").read_text())


def stage(sid: str) -> dict:
    return next(s for s in built()["stages"] if s["id"] == sid)


def scored_sets() -> list:
    return sorted(d.name for d in STAGES.iterdir() if d.is_dir() and not d.name.startswith("_") and (d / "scores.json").exists())


def same_cell(mine, art, where):
    """A stage cell (S2–S4 oracle, the chain's public number) against the artifact's own cell, field by field."""
    has = isinstance(art, dict) and art.get("bss") is not None
    if mine is None:
        assert not has, f"{where}: the artifact has a score the builder drops"
        return 0
    assert has, f"{where}: the builder shows a score the artifact does not have"
    want = {"bss": art["bss"], "lo": art["ci"]["bss"][0], "hi": art["ci"]["bss"][1], "pr": art["pr"], "roc": art["roc"],
            "n": art["n"], "n_pos": art["n_pos"], "storms": art["n_storm_blocks"], "low_power": art["low_power"], "span": art["span"]}
    assert mine == want, (where, mine, want)
    return 1


def at(d, path: str):
    for k in path.split("/"):
        d = d[int(k)] if isinstance(d, list) else d[k]
    return d


# ── the file ─────────────────────────────────────────────────────────────────

def test_the_json_is_current():
    """Regenerating from the committed artifacts gives the committed bytes (rebuild: SWAPS.md)."""
    assert E.OUT == JSON_PATH
    assert E.render() == JSON_PATH.read_text(encoding="utf-8"), \
        "stage_builder.json is stale: run venv/bin/python features/forecast/src/models/export_stage_builder.py and commit it"
    assert "NaN" not in JSON_PATH.read_text() and built()["schema"] == E.SCHEMA
    assert JSON_PATH.stat().st_size < 120_000, "the builder's file should stay small: it is fetched by the page"


def test_sampled_numbers_are_their_fields():
    sv = built()["served"]
    sc, S = scores(sv), s1_scores()
    s2 = next(p for p in stage("s2")["parts"] if p["live"])
    assert s2["from"] == sv and s2["scores"]["T2"]["bss"] == sc["s2"]["pooled"]["oracle"]["T2"]["bss"]
    assert s2["scores"]["T1"]["pr"] == sc["s2"]["pooled"]["oracle"]["T1"]["pr"]
    live = next(c for c in built()["chains"] if c["live"])
    assert live["scores"]["T2"]["L1"]["hi"] == sc["out"]["pooled"]["L1"]["T2"]["ci"]["bss"][1]
    assert live["scores"]["T1-holdout"]["rain"]["n_pos"] == sc["out"]["pooled"]["rain"]["T1-holdout"]["n_pos"]
    icon = next(p for p in stage("s1")["parts"] if p["live"])
    assert icon["score"]["mae"] == S["by_lead"]["1"]["avg"]["models"][icon["id"]]["continuous"]["either_wet"]["mae"]
    floor = stage("s1")["floor"]
    assert floor["mae"] == S["floor"][floor["name"]]["continuous"]["either_wet"]["mae"]
    basin = next(p for p in stage("s5")["parts"] if p["id"] == "basin_swap")["scores"]["geo_v1"]
    assert basin["S5"]["perfect"]["delta"] == sc["s5"]["pooled"]["oracle"]["S5"]["basin_swap"]["delta_vs_plain"]["delta"]
    assert basin["S5"]["realistic"]["mean"]["delta"] == sc["figure"]["m.s5"]["chained"]["v"]
    assert basin["T1"]["realistic"]["mean"]["hi"] == sc["figures"]["T1"]["m.s5"]["chained"]["hi"]


def test_every_number_is_its_artifact_field():
    n = 0
    for sid in ("s2", "s3", "s4"):
        for p in stage(sid)["parts"]:
            if p["scores"] is None:
                assert p["from"] is None
                continue
            for w, cell in p["scores"].items():
                n += same_cell(cell, (((scores(p["from"]).get(sid) or {}).get("pooled") or {}).get("oracle") or {}).get(w), f"{sid} {p['id']} {w}")
    for c in built()["chains"]:
        for w, both in c["scores"].items():
            for e in ("L1", "rain"):
                n += same_cell(both[e], scores(c["set"])["out"]["pooled"][e].get(w), f"chain {c['set']} {e} {w}")
    S = s1_scores()
    for p in stage("s1")["parts"] + [dict(stage("s1")["floor"], id=None)]:
        blk = S["by_lead"]["1"]["avg"]["models"][p["id"]] if p["id"] else S["floor"][p["name"]]
        mine = p["score"] if p["id"] else p
        want = {"mae": blk["continuous"]["either_wet"]["mae"], "lo": blk["ci"]["continuous"]["either_wet"]["mae"][0],
                "hi": blk["ci"]["continuous"]["either_wet"]["mae"][1], "n": blk["n_either_wet"], "span": [blk["first"], blk["last"]]}
        assert {k: mine[k] for k in want} == want, (p["id"], mine, want)
        if p["id"] and not p["live"]:
            assert p["vs_live"] == S["primary"]["vs_served"][p["id"]]["verdict"]
        n += 1
    st5 = stage("s5")
    for p in st5["parts"]:
        for g, per in p["scores"].items():
            pooled = scores(st5["home"][g])["s5"]["pooled"]
            for sw, x in per.items():
                c = pooled["oracle"][sw][p["id"]]
                d = c["delta_vs_plain"]
                assert x["perfect"] == {"delta": d["delta"], "lo": d["lo"], "hi": d["hi"], "verdict": d["verdict"], "n": c["n"],
                                        "low_power": c["low_power"], "span": c["span"]}, (p["id"], g, sw)
                feeds = sorted(f for f in pooled if f.startswith("degraded:") and p["id"] in (pooled[f].get(sw) or {}))
                r = x["realistic"]
                assert sorted(dr["feed"] for dr in r["draws"]) == feeds, (p["id"], g, sw)
                for dr in r["draws"]:
                    dd = pooled[dr["feed"]][sw][p["id"]]["delta_vs_plain"]
                    assert (dr["delta"], dr["verdict"]) == (dd["delta"], dd["verdict"])
                if r["mean"]:
                    pill = at(scores(st5["home"][g]), r["mean"]["src"])
                    assert {k: r["mean"][k] for k in ("delta", "lo", "hi", "n", "seeds", "low_power", "span")} == \
                           {"delta": pill["v"], "lo": pill["lo"], "hi": pill["hi"], "n": pill["n"], "seeds": pill["seeds"],
                            "low_power": pill["low_power"], "span": pill["span"]}
                n += 1
    assert n >= 60, n   # every scored cell of five sets and the S1 models (101 on 2026-10-04)
    print(f"   {n} scores checked against their artifact fields")


def test_the_realistic_mean_is_the_rule_it_is_shown_for():
    """A stored mean is the set's own rule (live_v2 = basin_swap on the served basins) or link/zone beside it."""
    st5 = stage("s5")
    for p in st5["parts"]:
        for g, per in p["scores"].items():
            for sw, x in per.items():
                m = (x["realistic"] or {}).get("mean")
                if not m:
                    continue
                pill = at(scores(st5["home"][g]), m["src"])
                words = pill.get("what") or pill["window"]
                assert E.S5_FIGURE_WORDS[p["id"]] in words, (p["id"], g, sw, words)
    import stages_s5 as S5
    import stages_build as SB
    assert E.S5_PRIMARY == S5.PRIMARY and E.S5_ALIASES["live_v2"] == S5.LIVE_V2[0]
    for rule, words in E.S5_FIGURE_WORDS.items():
        assert words in SB.S5_RULE_WORDS[rule], (rule, words)


# ── names, chains, the live set ──────────────────────────────────────────────

def test_every_name_comes_from_the_lineup_words_and_none_says_today():
    for st in built()["stages"]:
        assert st["title"] == dict(LU.COLUMNS)[st["id"]]
        for p in st["parts"]:
            assert p["name"] == LU.words(st["id"], p["id"]), (st["id"], p)
            assert "today" not in p["name"].lower(), p
            for alias in p.get("same_as", []):
                assert LU.words(st["id"], alias) == p["name"], (alias, p["name"])
        names = [p["name"] for p in st["parts"]]
        for g in built()["basins"]:
            mine = [p["name"] for p in st["parts"] if g["id"] in p["basins"]]
            assert len(set(mine)) == len(mine), (st["id"], g, mine)   # one plain name per part on the same basins
        assert names
    for b in built()["basins"]:
        assert b["name"] == LU.words("geography", b["id"]) and "today" not in b["name"].lower()
    for c in built()["chains"] + built()["lineups"]:
        assert c["name"] == " · ".join(LU.words(k, c["parts"][k]) for k in ("s2", "s3", "s4")) and "today" not in c["name"].lower()
    assert "today" not in json.dumps(built()).lower()


def test_every_scored_set_is_a_chain_with_its_manifest_parts():
    chains = {c["set"]: c for c in built()["chains"]}
    sets = scored_sets()
    assert sorted(chains) == sets and len(sets) >= 2
    for name in sets:
        man = json.loads((STAGES / name / "manifest.json").read_text())
        c = chains[name]
        assert c["basins"] == man["geography"] and c["parts"] == {k: man["components"][k] for k in ("s1", "s2", "s3", "s4", "s5")}, name
        for k in ("s1", "s2", "s3", "s4"):
            part = next(p for p in stage(k)["parts"] if p["id"] == c["parts"][k])
            assert c["basins"] in part["basins"], (name, k)
        rule = built()["aliases"]["s5"].get(c["parts"]["s5"], c["parts"]["s5"])
        assert any(p["id"] == rule for p in stage("s5")["parts"]), (name, rule)


def test_parts_on_disk_are_listed_and_unscored_ones_say_so():
    s2 = {p["id"]: p for p in stage("s2")["parts"]}
    for m in sorted((DATA / "candidates").glob("*/manifest.json")):
        cand = json.loads(m.read_text())
        assert cand["stage1"]["name"] in s2, cand["name"]
    unscored = [p for p in s2.values() if p["scores"] is None]
    assert unscored and all(p["from"] is None for p in unscored)
    assert "logit_v2_shared5" in {p["id"] for p in unscored}   # the 5-term model: on disk, no stage score yet
    lineup_sets = {x["set"] for x in built()["lineups"]}
    assert lineup_sets and not lineup_sets & set(scored_sets())


def test_the_served_set_is_live():
    served = json.loads((DATA / "served.json").read_text())["name"]
    b = built()
    assert b["served"] == served
    live = [c for c in b["chains"] if c["live"]]
    assert [c["set"] for c in live] == [served] and b["chains"][0]["set"] == served
    comps = live[0]["parts"]
    for st in b["stages"]:
        want = b["aliases"]["s5"].get(comps["s5"], comps["s5"]) if st["id"] == "s5" else comps[st["id"]]
        assert [p["id"] for p in st["parts"] if p["live"]] == [want], st["id"]
    assert stage("s5")["home"][live[0]["basins"]] == served


def test_the_live_season_says_whether_it_is_graded():
    t0 = next(w for w in built()["windows"] if w["key"] == "T0")
    cells = [c["scores"]["T0"][e] for c in built()["chains"] for e in ("L1", "rain")]
    assert t0["graded"] == any(cells)
    if not t0["graded"]:
        assert t0["data_end"] < t0["after"], t0
        for sid in ("s2", "s3", "s4"):
            assert all(p["scores"] is None or p["scores"]["T0"] is None for p in stage(sid)["parts"])


# ── serving it ───────────────────────────────────────────────────────────────

def test_the_route_returns_the_json():
    from app.wsgi import app
    with app.test_client() as c:
        r = c.get("/forecast/api/stage-builder")
    assert r.status_code == 200 and r.content_type.startswith("application/json"), (r.status_code, r.content_type)
    assert r.data == JSON_PATH.read_bytes()
    from features.forecast import page
    assert page.GET_ROUTES["/forecast/api/stage-builder"] is page.handle_stage_builder and page.STAGE_BUILDER_FILE == JSON_PATH


def test_the_json_is_bundled_and_the_stage_artifacts_are_not():
    cfg = json.loads((ROOT / "vercel.json").read_text())
    glob = cfg["functions"]["app/wsgi.py"]["excludeFiles"]
    parts = glob[1:-1].split(",")
    rel = JSON_PATH.relative_to(ROOT).as_posix()
    assert not any(fnmatch.fnmatch(rel, g) for g in parts), f"{rel} would be left out of the app"
    assert any(fnmatch.fnmatch("features/forecast/data/models/stages/logit_v1_s2v2/scores.json", g) for g in parts)


def test_the_page_reads_the_builder_and_names_no_set_todays():
    html = PAGE.read_text(encoding="utf-8")
    assert "/forecast/api/stage-builder" in html and 'id="builder"' in html and 'id="dayFoldBtn"' in html
    assert "How to read this" in html and "Day by day" in html and "sb-live" in html
    assert not re.search(r"today['’]s", html, re.I), re.findall(r".{40}today['’]s.{40}", html, re.I)[:3]
    assert "S5 is scored on its own and does not enter this number" in html


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
