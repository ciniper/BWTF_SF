"""The five-stage spec and figure 1 (STAGES_DESIGN.md §1, P1).

Pins what the spec promises: every stage has a box, a truth, a metric and a
list of what its score leaves out; every rule id the spec names is in the
catalog; the drawing is clean for both geographies (no box overlaps, no edge
crosses an edge or runs through a box, no label sits on a box, an edge or
another label); the words follow the geography; the public copy carries no
rule ids outside tooltips and never the word "cost".

    venv/bin/python tests/test_stages_spec.py
"""
from __future__ import annotations

import builtins
import importlib
import io
import json
import re
import sys
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "features" / "forecast" / "src" / "models"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(MODELS))

import stages_flowchart as F  # noqa: E402
import stages_spec as SP  # noqa: E402
from shared import risk_levels as RL  # noqa: E402
from shared.zones import ZONES  # noqa: E402

SCORES = {"figure": {
    "m.s1": {"oracle": {"v": 0.184}, "chained": {"v": 0.2149, "lo": 0.19, "hi": 0.24, "n": 100}},
    "m.s2": {"oracle": {"v": 0.5432, "lo": -0.07, "hi": 0.76, "n": 854, "pos": 12, "low_power": True}, "chained": {"v": -0.123}},
    "m.out": {"lead": [{"v": 0.7}, {"v": 0.6}, None, {"v": -0.2}]},
}}
COUNTS = {"exclusions": {"X-S2-UNCOV": 374, "X-E2E-UNK": 1172}, "claims": {"C-DRY": 214}, "n_city_days": 112}


def visible(svg: str) -> str:
    """The text a reader sees: tooltips (<title>) and tags removed."""
    return re.sub(r"<[^>]+>", " ", re.sub(r"<title>.*?</title>", " ", svg, flags=re.S))


def test_every_stage_has_a_box_a_truth_a_metric_and_exclusions():
    assert [s["id"] for s in SP.STAGES] == ["s1", "s2", "s3", "s4", "s5", "out"]
    for s in SP.STAGES:
        for f in ("name", "question", "unit", "input", "output", "truth", "oracle", "chained", "metric", "anchor"):
            assert isinstance(s[f], str) and s[f].strip(), (s["id"], f)
        assert s["benchmarks"] and s["exclusions"], s["id"]
        n = SP.NODE[s["node"]]
        assert n["kind"] in ("stage", "output") and n["stage"] == s["code"], s["id"]
        assert n.get("metric"), s["id"]
        if s["id"] != "s5":                      # S5 is graded on the later stages' truths
            assert SP.NODE[s["truth_node"]]["kind"] == "truth" and SP.NODE[s["truth_node"]]["stage"] == s["code"]
            assert SP.NODE[s["chip"]]["kind"] == "exclusion" and SP.NODE[s["chip"]]["stage"] == s["code"]
        assert len(set(s["exclusions"])) == len(s["exclusions"]), s["id"]


def test_every_rule_id_is_in_the_catalog():
    used = set()
    for s in SP.STAGES:
        for x in s["exclusions"]:
            assert x in SP.EXCLUSIONS, (s["id"], x)
            used.add(x)
    for n in SP.NODES:
        for line in n.get("items", ()):
            for _, x in line:
                assert x in SP.EXCLUSIONS, (n["id"], x)
                assert x in SP.STAGE_OF_CODE[n["stage"]]["exclusions"], (n["id"], x)
        for c in n.get("claims", ()):
            assert c in SP.CLAIMS, (n["id"], c)
    assert used == set(SP.EXCLUSIONS), set(SP.EXCLUSIONS) - used          # no orphan rules
    for x, r in SP.EXCLUSIONS.items():
        assert re.fullmatch(r"X-[A-Z0-9]+(-[A-Z0-9]+)*", x), x
        assert r["kind"] in ("exclude", "tag", "stratum", "fit") and r["chip"] and r["plain"], x
        assert r["group"] in ("ALL", "S1", "S2", "S3", "S4", "S5", "OUT"), x
    # Part B: exceedances with no overflow are negatives of the claim, never excluded; new rules in place
    assert "X-E2E-SCOPE" not in SP.EXCLUSIONS
    assert "X-S4-RESAMPLE" in SP.STAGE["s4"]["exclusions"]
    assert "X-S5-PERFECT-SIBLING" in SP.STAGE["s5"]["exclusions"]                # Part B 9
    assert "X-LEDGER-SUSPECT" in SP.STAGE["s2"]["exclusions"] and "X-LEDGER-SUSPECT" in SP.STAGE["s3"]["exclusions"]
    assert {"C-DRY", "C-RUNOFF", "C-OTHER", "C-UNMON", "C-POSTING", "C-TIME"} == set(SP.CLAIMS)
    assert SP.CLAIMS["C-DRY"]["counted"] and SP.CLAIMS["C-RUNOFF"]["counted"]
    assert SP.NODE["x.claim"]["claims"] == tuple(SP.CLAIMS)


def _protocol_s7() -> str:
    return (ROOT / "features" / "forecast" / "STAGES_PROTOCOL.md").read_text().split("## 7.", 1)[1].split("\n## ", 1)[0]


def _protocol_first_match() -> dict:
    """STAGES_PROTOCOL.md §7's first-match table: stage code → (ids in order, the 'also' column's ids)."""
    ids = lambda t: re.findall(r"X-[A-Z0-9]+(?:-[A-Z0-9]+)*", t)  # noqa: E731
    return {m.group(1): (ids(m.group(2)), ids(m.group(3)))
            for m in re.finditer(r"^\| (S[1-5]|OUT) \| ([^|]*)\| ([^|]*)\|", _protocol_s7(), re.M)}


def test_the_exclusions_module_names_every_catalog_rule():
    """src/models/exclusions.py (P4b) implements every rule the figure names and none the catalog lacks
    (a dropped rule such as X-E2E-SCOPE coming back), in both directions, with the protocol's order."""
    import exclusions as X
    assert set(X.RULES) == set(SP.EXCLUSIONS), set(X.RULES) ^ set(SP.EXCLUSIONS)
    assert len(X.RULES) == 36
    for x, r in X.RULES.items():
        assert r.kind == {"fit": "fit_only"}.get(SP.EXCLUSIONS[x]["kind"], SP.EXCLUSIONS[x]["kind"]), x
        assert set(r.stages) == {c for c, ids in SP.EXCLUSIONS_BY_STAGE.items() if x in ids}, x
        assert r.chip == SP.EXCLUSIONS[x]["chip"] and r.plain == SP.EXCLUSIONS[x]["plain"], x   # words read, never copied
    protocol = _protocol_first_match()
    assert {c: tuple(first) for c, (first, _) in protocol.items()} == X.STAGE_ORDER
    for code, (first, also) in protocol.items():
        mine = list(SP.STAGE_OF_CODE[code]["exclusions"])
        assert mine[:len(first)] == first, (code, mine, first)
    src = (MODELS / "exclusions.py").read_text()
    missing = [x for x in SP.EXCLUSIONS if x not in src]
    assert not missing, missing
    extra = set(re.findall(r"""["'](X-[A-Z0-9]+(?:-[A-Z0-9]+)*)["']""", src)) - set(SP.EXCLUSIONS)
    assert not extra, extra
    for x in SP.EXCLUSIONS:                                   # the plain words live in stages_spec only
        assert SP.EXCLUSIONS[x]["plain"] not in src, x


def test_the_stage_lists_follow_the_frozen_protocol():
    """STAGES_PROTOCOL.md §7 (frozen at P0) is the first-match order: each stage's list starts with
    it, holds the row's other rules, and the catalog is exactly the protocol's rule table."""
    rows = _protocol_first_match()
    assert set(rows) == {s["code"] for s in SP.STAGES}, set(rows)
    for code, (first, also) in rows.items():
        mine = list(SP.STAGE_OF_CODE[code]["exclusions"])
        assert mine[:len(first)] == first, (code, mine, first)
        assert set(also) <= set(mine), (code, set(also) - set(mine))
    table = {m.group(1) for m in re.finditer(r"^\| (X-[A-Z0-9-]+)", _protocol_s7(), re.M)}
    assert table == set(SP.EXCLUSIONS), (table ^ set(SP.EXCLUSIONS))


def test_nodes_and_edges_are_well_formed():
    assert len(SP.NODE) == len(SP.NODES)
    anchors = {s["anchor"] for s in SP.STAGES} | {"levels", "claims"}
    for n in SP.NODES:
        assert n["kind"] in F.BOX_CLASS and n["anchor"] in anchors, n["id"]
        assert n.get("logo") or n.get("icon") or n["kind"] in ("exclusion", "claim"), n["id"]
    for geo in SP.GEOS:
        ids = [e["id"] for e in SP.edges(geo)]
        assert len(set(ids)) == len(ids), geo
        for e in SP.edges(geo):
            assert e["frm"] in SP.NODE and e["to"] in SP.NODE and e["style"] in SP.STYLES, (geo, e["id"])
            assert len(e["pts"]) >= 2 and (e["label"] is None) == (e["lab"] is None), (geo, e["id"])
    sprite = (ROOT / "app" / "templates" / "_icons.html").read_text()
    for n in SP.NODES:
        if n.get("icon"):
            assert f'id="i-{n["icon"]}"' in sprite, n["icon"]
        if n.get("logo"):
            assert (ROOT / "app" / "static" / "logos" / n["logo"]).exists(), n["logo"]


def test_the_drawing_is_clean_for_both_geographies():
    for geo in SP.GEOS:
        assert F.check(geo) == [], (geo, F.check(geo))


def test_the_check_catches_a_bad_drawing():
    saved = SP.EDGES
    try:   # down the S2|S3 gutter across the chain and an oracle arrow, then into S3, labelled inside S2
        SP.EDGES = saved + (dict(id="bad", frm="t.s2", to="m.s4", style="data", pts=((530, 150), (530, 400), (700, 300)),
                                 label="x", lab=(400, 300, "start"), tip=""),)
        kinds = {p[0] for p in F.check("sfpuc4_v1")}
    finally:
        SP.EDGES = saved
    assert {"cross", "through", "label-in-box"} <= kinds, kinds
    saved_n = SP.NODES
    try:   # a second box on S1's truth cell
        SP.NODES = saved_n + (dict(id="bad.n", kind="truth", stage="S1", col=0, row=0, icon="cloud-rain", title="x", lines=()),)
        assert ("overlap", "t.s1", "bad.n") in F.check("geo_v1")
    finally:
        SP.NODES = saved_n
    assert F.check("sfpuc4_v1") == []


def test_no_cost_anywhere():
    """Cost sets nothing and ranks nothing (Chase, 2026-10-01, A3): not even a tooltip says it."""
    assert "cost" not in json.dumps(SP.spec_dict(), default=list, ensure_ascii=False).lower()
    for geo in SP.GEOS:
        for sc, cn in ((None, None), (SCORES, COUNTS)):
            svg, ph = F.render(geo, sc, cn)
            assert "cost" not in svg.lower() and "cost" not in ph.lower(), geo


def test_rule_ids_and_artifacts_only_in_tooltips():
    for geo in SP.GEOS:
        svg, ph = F.render(geo, SCORES, COUNTS)
        for text in (visible(svg), re.sub(r"<[^>]+>", " ", ph)):
            assert not re.search(r"\b[XC]-[A-Z0-9]{2,}", text), re.findall(r"\b[XC]-[A-Z0-9-]+", text)
            assert not any(re.search(rf"(?<![\w.]){re.escape(i)}(?![\w.])", text) for i in SP.NODE), geo
            assert not re.search(r"\.(json|pkl|py)\b|_v\d|geo_v1|sfpuc4", text), geo
        assert "X-S1-OUTAGE" in svg and "m.s2" in svg                        # …and they are in the tooltips


def test_the_svg_is_well_formed():
    for geo in SP.GEOS:
        for sc, cn in ((None, None), (SCORES, COUNTS)):
            root = ET.fromstring(F.render(geo, sc, cn)[0])
            assert root.tag == "svg" and root.get("viewBox") == f"0 0 {SP.VIEW[0]} {SP.VIEW[1]}"


def test_dashes_until_scored():
    svg, ph = F.render("sfpuc4_v1")
    for s in ("floor —", "lead 1 —", "oracle —", "chained —", "no filing — · feed archive —", "— overflow days",
              "days nobody sampled — · resamples —",
              "dry-weather exceedances —", "unsampled days after one —"):
        assert s in svg, s
    assert svg.count('class="lead na"') == 6
    assert "identity links: checked only" in svg and "peak hours" in svg and "SFPUC's posting decisions" in svg
    assert not re.search(r"\d\.\d\d", visible(svg)), re.findall(r"\d\.\d\d", visible(svg))   # no placeholder numbers


def test_the_figure_css_stays_inside_the_figure():
    """brand.css and the reports style .lead / .pill / .chip on HTML: the figure's own rules may
    only reach elements inside svg.pipe (an unscoped .lead{opacity:.85} dimmed the page's lead text)."""
    own = F.CSS[len(F.K.PIPE_CSS):]
    sels = [s.strip() for m in re.finditer(r"([^{}]+)\{", own) for s in m.group(1).split(",")]
    assert sels and all(s.startswith("svg.pipe ") for s in sels), [s for s in sels if not s.startswith("svg.pipe ")]
    assert "svg.pipe .lead{" in own and not re.search(r"(?<!svg\.pipe )\.lead[.{]", F.CSS)


def test_counts_must_be_whole_numbers():
    import numpy as np
    assert F.fmt_count({"a": np.int64(1172)}, "a") == "1,172" and F.fmt_count({"a": 3.0}, "a") == "3"
    for bad in ({"ocean": 3}, "12", 2.5, float("nan"), True):
        try:
            F.render("geo_v1", None, {"X-S2-UNCOV": bad})
        except TypeError:
            continue
        raise AssertionError(f"count {bad!r} printed instead of raising")
    svg = F.render("geo_v1", {"m.s2": {"oracle": {"v": float("nan")}, "chained": float("inf")}})[0]
    assert "nan" not in visible(svg).lower() and "inf" not in visible(svg).lower() and "oracle —" in svg


def test_scores_and_counts_bind():
    for sc, cn in ((SCORES, COUNTS), (SCORES["figure"], {**COUNTS["exclusions"], **COUNTS["claims"], "n_city_days": 112})):
        svg, ph = F.render("geo_v1", sc, cn)
        for s in ("floor 0.18″", "lead 1 0.21″", "oracle 0.54", "chained −0.12", "no filing 374 · feed archive —",
                  "dry-weather exceedances 214", "112 overflow days", "unsampled days after one 1,172", "oracle —"):
            assert s in svg, s
        assert 'opacity=".55"' in svg and "BSS 0.54 [−0.07, 0.76], n = 854, 12 positives; too few to decide" in svg
        assert svg.count('class="lead na"') == 3                       # +2, +4, +5 unscored; −0.2 draws a stub
        assert "Skill vs climatology (BSS): oracle 0.54 · chained −0.12." in ph and "today 0.70 · +1 0.60 · +2 —" in ph
    whole = {**SCORES, "exclusions": COUNTS["exclusions"], "claims": COUNTS["claims"]}   # a whole scores.json, counts inside
    svg = F.render("sfpuc4_v1", whole)[0]
    assert "no filing 374" in svg and "dry-weather exceedances 214" in svg and "oracle 0.54" in svg


def test_the_words_follow_the_geography():
    """Part B 15: the served GEO_V1 set says what it does; SFPUC4 says what it would do."""
    sf, v1 = (visible(F.render(g)[0]) for g in SP.GEOS)
    assert "SFPUC's four basins" in sf and "our four basins" not in sf
    assert "our four basins" in v1 and "SFPUC's four basins" not in v1
    assert "after the split" in sf and "named outfall" in sf and "before the split" not in sf
    assert "its basin, before the split" in v1 and "a flag → its basin" in v1 and "named outfall" not in v1
    assert "overflow history + rain" in sf and "overflow history only" in v1
    assert {e["id"]: e["to"] for e in SP.edges("sfpuc4_v1")}["l4"] == "m.s3"
    assert {e["id"]: e["to"] for e in SP.edges("geo_v1")}["l4"] == "m.s2"
    assert F.inset_data("sfpuc4_v1")["basins"][-1] == "South" and F.inset_data("geo_v1")["basins"][-1] == "Southeast"
    assert "Southeast" not in sf and not re.search(r"\bgroups?\b|\bKing\b|cheapest|alarm|alert line", sf + v1, re.I)
    wx = F.weather_model()
    assert wx != F.WEATHER_UNKNOWN, "METEO_PARAMS['models'] no longer parses from live_dashboard.py"
    assert wx == wx.upper() and f"{wx} via Open-Meteo, one point" in sf


def test_the_inset_draws_the_geography():
    zones = [F._short_zone(z.label) for z in ZONES.values()]
    for geo in SP.GEOS:
        ins = F.inset_data(geo)
        assert ins["zones"] == zones and len(ins["basins"]) == 4, geo
        assert len(ins["links"]) == 5, (geo, ins["links"])           # the five unique (basin, zone) links
        split = [(ins["basins"][b], ins["zones"][z]) for b, z, s in ins["links"] if s]
        assert split == [("Westside", "Ocean Beach"), ("Westside", "Baker & China")], (geo, split)
        assert sorted({z for _, z, _ in ins["links"]}) == [0, 1, 2, 3], geo   # every zone is reached
        assert ins["source"] == geo, (geo, ins["source"])                 # the geography itself: there is no stand-in
    assert not hasattr(SP, "FALLBACK_INSET") and "fallback_inset" not in SP.spec_dict()


def test_risk_levels_box_shows_the_shared_bands():
    words = " · ".join(f"{lv.label} {lv.lo}–{lv.hi}" for lv in RL.LEVELS)
    assert words == "Low 0–20 · Medium 21–50 · High 51–80 · Extreme 81–100"
    assert SP.LEVEL_EDGES == RL.edges() == (0.205, 0.505, 0.805)
    for geo in SP.GEOS:
        svg, ph = F.render(geo)
        v = visible(svg)
        assert "Risk levels" in v and "the same bands on every page" in v
        for lv in RL.LEVELS:
            assert f'fill="{lv.color}">{lv.label}</tspan>' in svg and f"{lv.label} {lv.lo}–{lv.hi}" in ph
        assert not re.search(r"calibrated|1 in 3|false alarm|\bmiss(es)?\b", v, re.I)


def test_s4_chip_lists_the_first_look_rule():
    """X-S4-RESAMPLE is the rule behind S4's first-look primary (Part B 4): the chip names it, with a count."""
    items = [x for line in SP.NODE["x.s4"]["items"] for _, x in line]
    assert items == ["X-S4-UNSAMPLED", "X-S4-RESAMPLE", "X-S4-HISTUNK"], items
    assert SP.EXCLUSIONS["X-S4-RESAMPLE"]["counted"]
    svg = F.render("sfpuc4_v1", None, {"X-S4-RESAMPLE": 628})[0]
    assert "resamples 628" in svg and "resamples 628" in F.render("sfpuc4_v1", None, {"X-S4-RESAMPLE": 628})[1]


def test_out_chip_lists_only_what_out_leaves_out():
    assert [w for line in SP.NODE["x.out"]["items"] for w, _ in line] == ["unsampled days after one", "overflow history unknown"]
    assert {x for line in SP.NODE["x.out"]["items"] for _, x in line} == {"X-E2E-UNK", "X-E2E-UNCOV"}


def test_the_phone_list_follows_the_spec():
    for geo in SP.GEOS:
        ph = F.render(geo)[1]
        assert ph.startswith('<ol class="pipe-m">') and ph.count("<li ") == len(SP.PHONE_ORDER) == 8
        assert re.findall(r'<li id="m-([a-z0-9]+)"', ph) == list(SP.PHONE_ORDER)
        for s in SP.STAGES:
            assert F._cap(s["question"]) in ph, (geo, s["id"])
        assert ph.count("Not scored:") == 6 and ph.count('class="lgi"') == 8


def test_spec_version_and_unknown_geography():
    assert re.fullmatch(r"[0-9a-f]{10}", SP.SPEC_VERSION)
    for bad in ("sfpuc4", "GEO_V1", ""):
        try:
            F.render(bad)
        except KeyError:
            continue
        raise AssertionError(f"render({bad!r}) did not raise")


def test_importing_reads_no_files():
    """Imported by pages and reports: building the spec and the renderer touches no file."""
    calls, real_b, real_io = [], builtins.open, io.open
    def spy(*a, **k):  # noqa: E306
        calls.append(a[0] if a else k)
        return real_b(*a, **k)
    builtins.open = io.open = spy
    try:
        importlib.reload(F.K)
        importlib.reload(SP)
        importlib.reload(F)
    finally:
        builtins.open, io.open = real_b, real_io
    assert not calls, calls


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
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
