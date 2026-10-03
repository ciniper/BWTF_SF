"""The stages report: reports/2026-10_forecast_stages.html, written by
features/forecast/src/models/export_stages_report.py (STAGES_DESIGN.md A8, Part C §8 P9).

Pins what the page promises: it is current with the committed artifacts (a rebuild gives
the same bytes); every number on it is the artifact field its data-v names, and a sample
read straight from the files independently; every set with stages scores is in the lineup,
which marks exactly the cells that differ from today's forecast; no retired words, no rule
ids on its face; nothing wider than a phone outside a sideways scroller; the figure's links
land on the page; and it never recommends a promotion.

    venv/bin/python tests/test_stages_report.py
"""
from __future__ import annotations

import html
import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "features" / "forecast" / "src" / "models"
for _p in (str(ROOT), str(MODELS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import export_stages_report as R  # noqa: E402

DATA = ROOT / "features" / "forecast" / "data" / "models"
STAGES = DATA / "stages"
PHONE = 390                                     # px: the narrowest screen the page must fit without sideways scroll
_CACHE: dict = {}


def data() -> dict:
    if "data" not in _CACHE:
        _CACHE["data"] = R.load()
    return _CACHE["data"]


def page() -> str:
    if "page" not in _CACHE:
        _CACHE["page"] = R.render(data())
    return _CACHE["page"]


def read(p: Path):
    return json.loads(p.read_text())


def served() -> str:
    return read(DATA / "served.json")["name"]


def scored_sets() -> list:
    return sorted(d.name for d in STAGES.iterdir() if d.is_dir() and not d.name.startswith("_") and (d / "scores.json").exists())


def section(sid: str) -> str:
    """The HTML of one <section id=...> of the page."""
    m = re.search(rf'<section id="{re.escape(sid)}">(.*?)</section>', page(), re.S)
    assert m, f"no section #{sid}"
    return m.group(1)


def body_html() -> str:
    """The page without the icon sprite, the CSS and the scripts."""
    s = re.sub(r"<style>.*?</style>", " ", page(), flags=re.S)
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S)
    return s.replace(R.sprite(), " ")


def visible(s: str) -> str:
    """What a reader sees: no tooltips (<title> elements, title attributes), no tags; spaces as they read
    ("0.59 [0.51, 0.65]", not the gaps the tags leave)."""
    s = re.sub(r"<title>.*?</title>", " ", s, flags=re.S)
    s = re.sub(r'\stitle="[^"]*"', " ", s)
    s = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())
    return re.sub(r" ([,\]])", r"\1", re.sub(r"\[ ", "[", s))


def m2(v: float, nd: int = 2, signed: bool = False) -> str:
    """A number as the page prints it, independently of the module: a real minus sign."""
    return (f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}").replace("-", "−")


# ── current, and every number is a field ────────────────────────────────────

def test_the_report_exists_and_is_current():
    assert R.OUT.exists(), "no report: run features/forecast/src/models/export_stages_report.py"
    assert R.OUT.read_text(encoding="utf-8") == page(), \
        "the report is stale: rerun features/forecast/src/models/export_stages_report.py"
    assert R.render(R.load()) == page(), "two builds from the same artifacts differ: the page reads a clock or an order"


def test_the_cli_writes_the_page_and_prints_its_path():
    import contextlib
    import io
    assert R.OUT == ROOT / "reports" / "2026-10_forecast_stages.html"          # served at /reports/<name>
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "page.html"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            R.main([str(out)])
        assert out.read_text(encoding="utf-8") == page() and buf.getvalue().strip() == str(out)


def test_every_number_is_the_field_it_names():
    m = re.search(r'<script type="application/json" id="sources">(.*?)</script>', page(), re.S)
    assert m, "no #sources block"
    srcs = json.loads(m.group(1).replace("<\\/", "</"))
    arts = R.sources(data())
    assert set(srcs) == set(arts), sorted(set(srcs) ^ set(arts))
    files = {k: read(ROOT / rel) for k, rel in srcs.items()}          # straight from disk, not the module's copy
    for k, a in arts.items():                                         # (dumps: S1's scores hold NaN, and NaN != NaN)
        assert json.dumps(files[k], sort_keys=True) == json.dumps(a.data, sort_keys=True) and srcs[k] == a.rel, k
    spans = re.findall(r'<span class="n" data-v="([^"]+)">([^<]*)</span>', page())
    assert len(spans) > 800, len(spans)
    for ref, text in spans:
        ref = html.unescape(ref)
        key, rest = ref.split(":", 1)
        path, f = rest.rsplit("|", 1)
        v = files[key]
        for p in path.split("/"):
            v = v[int(p)] if isinstance(v, list) else v[p]
        assert html.unescape(text) == R.FMT[f](v), (ref, text, v)


def test_no_number_is_typed_by_hand():
    """Off the data-v spans (and outside the figure, which stages_flowchart draws from the same scores), a decimal
    must be the spec's own words, and a number from 10 up must be the spec's words, a plain label, a risk band,
    a station's name, a year or a count of an artifact's terms."""
    import stages_spec as SP
    bare = re.sub(r'<span class="n" data-v="[^"]+">[^<]*</span>', " ", body_html())
    bare = re.sub(r'<svg class="(pipe|chart)".*?</svg>', " ", bare, flags=re.S)
    bare = re.sub(r'<ol class="pipe-m">.*?</ol>', " ", bare, flags=re.S)
    words = " ".join(visible(bare).split())
    spec = " ".join([e["plain"] + " " + e["chip"] for e in SP.EXCLUSIONS.values()]
                    + [c["plain"] + " " + c["pill"] for c in SP.CLAIMS.values()])
    for d in set(re.findall(r"(?<![\w.])\d+\.\d+", words)):
        assert d in spec, f"a decimal on the page that is no artifact field and no spec word: {d}"
    allowed = {int(n) for n in re.findall(r"\d+", spec + " " + json.dumps(R.LABELS))}
    from shared.risk_levels import LEVELS
    allowed |= {n for lv in LEVELS for n in (lv.lo, lv.hi)}
    bk = read(DATA / "stages_candidates" / "_bakeoff" / "results.json")
    for c, v in bk["grid"]["contenders"].items():                      # the bake-off's inputs and bends, counted
        terms = v["terms"][bk["development"]["choices"][c]["bends"]]
        allowed |= {len({re.split(r"[:>]", t)[0] for t in terms}), sum(1 for t in terms if ">" in t)}
    sc = read(STAGES / served() / "scores.json")
    allowed |= {int(re.search(r"\d+", k).group(0)) for k in sc["catalog"]["exclusions"]["s4"].get("X-S4-FOLLOWUP", {})}
    allowed |= {int(re.search(r"\d+", o).group(0)) for o in sc["claims"]["C-UNMON"]["outfalls"]}         # outfall names
    odd = sorted({int(n) for n in re.findall(r"(?<![\w.#])\d+(?![\w.])", words) if 10 <= int(n) and not 2000 <= int(n) <= 2099}
                 - allowed)
    assert not odd, f"numbers on the page that no data-v span carries: {odd}"


def test_a_sample_of_numbers_matches_the_artifacts():
    sv = served()
    sc = read(STAGES / sv / "scores.json")
    out = section("out")
    v = sc["out"]["pooled"]["L1"]["T1"]
    assert m2(v["bss"]) in visible(out) and m2(v["ci"]["bss"][0]) in visible(out) and f'{v["n"]:,}' in visible(out)
    v = sc["out"]["pooled"]["oracle"]["T2"]
    assert f'{m2(v["bss"])} [{m2(v["ci"]["bss"][0])}, {m2(v["ci"]["bss"][1])}]' in visible(out)
    st = section("stages")
    v = sc["s2"]["pooled"]["oracle"]["T2"]
    assert f'{m2(v["bss"])} [{m2(v["ci"]["bss"][0])}, {m2(v["ci"]["bss"][1])}]' in visible(st)
    assert f'{v["n"]:,} basin-days · {v["n_pos"]:,} overflow days · {v["n_storm_blocks"]:,} storms' in visible(st)
    s1 = read(STAGES / "_s1" / "s1_scores.json")
    icon = s1["by_lead"]["1"]["avg"]["models"][s1["served_model"]]
    assert f'{m2(icon["continuous"]["either_wet"]["mae"])}″' in visible(st)
    d = sc["s5"]["pooled"]["degraded:1"]["S5"]["basin_swap"]["delta_vs_plain"]
    assert f'{m2(d["delta"], 3, True)} [{m2(d["lo"], 3, True)}, {m2(d["hi"], 3, True)}]' in visible(st)
    lad = sc["paired"]["ladder"]["pooled"]["T1"]
    assert m2(lad["bs"]["L1"], 4) in visible(out) and m2(lad["drops"]["rain − truth_at_s3"]["delta"], 4, True) in visible(out)
    left = section("left-out")
    assert f'{len(sc["catalog"]["ledger_suspect"])} such basin windows' in visible(left)
    assert f'{sc["catalog"]["exclusions"]["s2"]["X-S2-UNCOV"]["westside"]:,}' in visible(left)
    bk = read(DATA / "stages_candidates" / "_bakeoff" / "results.json")
    w = bk["winner"]["contender"]
    nb = bk["nested"]["by_contender"][w]["pooled"]
    ch = section("challengers")
    assert f'{m2(nb["bss"])} [{m2(nb["ci"]["bss"][0])}, {m2(nb["ci"]["bss"][1])}]' in visible(ch)
    for name in scored_sets():
        if name == sv:
            continue
        card = re.search(rf'<div class="card chal" id="c-{re.escape(name)}">(.*?)<div class="check">', ch, re.S)
        assert card, f"no challenger card for {name}"
        p = read(STAGES / name / "scores.json")["primaries"]
        for o in next(r for r in p["rows"] if r["id"] == "OUT")["parts"]:
            if o.get("delta") is None:                                        # e.g. while the served build is rebuilt
                assert o["status"] in visible(card.group(1)), (name, o["part"])
                continue
            nd = 3 if max(abs(o["delta"]), *map(abs, o["ci"])) >= 0.01 else 4  # the page's rule: more places when small
            assert f'{m2(o["delta"], nd, True)} [{m2(o["ci"][0], nd, True)}, {m2(o["ci"][1], nd, True)}]' in visible(card.group(1)), (name, o["part"])


# ── the lineup (A8) ──────────────────────────────────────────────────────────

def lineup_rows() -> dict:
    tab = re.search(r'<table class="lineup">(.*?)</table>', section("lineups"), re.S).group(1)
    rows = {}
    for name, body in re.findall(r'<tr data-set="([^"]+)"[^>]*>(.*?)</tr>', tab, re.S):
        rows[name] = {c: (cls, html.unescape(cid)) for c, cls, cid in re.findall(r'<td data-col="(\w+)" class="(\w+)" title="([^"]*)">', body)}
    return rows


def test_every_scored_set_is_in_the_lineup():
    rows = lineup_rows()
    assert sorted(rows) == scored_sets(), (sorted(rows), scored_sets())
    assert next(iter(rows)) == served(), "today's forecast heads the lineup"


def test_the_lineup_marks_exactly_the_cells_that_differ():
    def lineup(name):
        m = read(STAGES / name / "manifest.json")
        return {"geography": m["geography"], **{k: m["components"][k] for k in ("s1", "s2", "s3", "s4", "s5")}}
    base = lineup(served())
    rows = lineup_rows()
    for name, cells in rows.items():
        want = lineup(name)
        assert set(cells) == set(want), (name, sorted(cells))
        for col, (cls, cid) in cells.items():
            assert cid == want[col], (name, col, cid)
            assert (cls == "diff") == (want[col] != base[col]), (name, col, cls)
    assert all(cls == "same" for cls, _ in rows[served()].values())
    assert any(cls == "diff" for name in rows for cls, _ in rows[name].values())


def test_the_lineup_speaks_plain_words():
    tab = re.search(r'<table class="lineup">(.*?)</table>', section("lineups"), re.S).group(1)
    for col, cid, cell in re.findall(r'<td data-col="(\w+)" class="\w+" title="([^"]*)">(.*?)</td>', tab, re.S):
        words = " ".join(visible(re.sub(r'<span class="vh">.*?</span>', "", cell)).split())
        assert words == R.LABELS[col][html.unescape(cid)], (col, cid, words)
        assert "_" not in words.replace("(live_v2)", ""), words                 # ids only in the tooltip ("live_v2": the owner's words)
    # the numbers in the words are the artifacts' own
    bk = read(DATA / "stages_candidates" / "_bakeoff" / "results.json")
    assert R.LABELS["s2"]["logit_v1"].startswith(f'{bk["grid"]["contenders"]["logit_v1"]["n_terms"]}-weight')
    for name in scored_sets():
        d = DATA / "stages_candidates" / name / "manifest.json"
        if d.exists() and read(d)["components"].get("s2") == "shared8_nonneg_sfpuc4":
            assert R.LABELS["s2"]["shared8_nonneg_sfpuc4"].startswith(f'{read(d)["s2"]["n_terms"]}-term model ({read(d)["s2"]["contender"]})')
    assert served() in visible(tab) and "“s2” is the old two-stage pipeline's stage 2" in visible(section("lineups"))   # shown, and explained


# ── words ────────────────────────────────────────────────────────────────────

def test_no_retired_words():
    whole = html.unescape(body_html())
    for w in (r"\bcost", r"\bKing\b", r"\bcheapest\b", r"\balarm line"):
        assert not re.search(w, whole, re.I if w != r"\bKing\b" else 0), w
    assert not re.search(r"\bgroups?\b", visible(body_html()), re.I), re.findall(r".{30}\bgroups?\b.{30}", visible(body_html()), re.I)


def test_no_rule_ids_or_section_signs_on_the_face():
    face = visible(body_html())
    assert not re.search(r"\b[XC]-[A-Z0-9]{2,}", face), re.findall(r"\b[XC]-[A-Z0-9-]+", face)
    assert "§" not in face
    assert 'title="X-S2-UNCOV"' in page() and 'title="C-DRY"' in page()            # …the ids live in the tooltips
    above_footer = visible(body_html().split('<footer class="srcs">')[0])          # the footer names its files
    assert not re.search(r"\.(py|json|pkl|md)\b|\['|\bT[0-3]\b|\bL[0-5]s?\b", above_footer), \
        re.findall(r".{30}(?:\.(?:py|json|pkl|md)\b|\['|\bT[0-3]\b|\bL[0-5]s?\b).{30}", above_footer)


def test_todays_rule_is_called_worse_when_the_artifact_says_so():
    sc = read(STAGES / served() / "scores.json")
    seeds = [f for f in sc["s5"]["pooled"] if f.startswith("degraded:")]
    worse = [f for f in seeds if sc["s5"]["pooled"][f]["S5"]["basin_swap"]["delta_vs_plain"]["verdict"] == "worse"]
    face = " ".join(visible(section("stages")).split())
    if seeds and len(worse) == len(seeds):
        assert f"On every one of the {len(seeds)} realistic feeds, today's rule makes the days after an observation worse than no correction at all" in face
    else:
        assert "worse than no correction at all" not in face


def test_promotion_is_chases_call_and_nothing_is_recommended():
    face = " ".join(visible(body_html()).split())
    assert "whether to switch is his call" in face and "it recommends nothing" in face
    assert not re.search(r"\bpromot", face, re.I), re.findall(r".{40}promot.{40}", face, re.I)
    assert not re.search(r"\b(we|this page) (recommend|suggest)s?\b|\bshould (be )?(switch|replace|serve)", face, re.I)
    for name in scored_sets():
        if name != served():
            crit = read(STAGES / name / "scores.json")["promotion"]["criteria"]
            met = sum(1 for c in crit if c["status"] == "met")
            assert f"{met} of {len(crit)} conditions met" in face, name


def test_the_words_cover_every_part_and_rules_version():
    for s in data()["sets"]:
        R.lineup_words(s["manifest"])                             # raises on a component with no words
    info = R.protocol_info()
    assert [v["version"] for v in info["versions"]][-1] == info["version"] and set(v["version"] for v in info["versions"]) <= set(R.VERSION_NOTES)
    face = visible(section("left-out"))
    for v in info["versions"]:
        assert v["version"] in face
    for s in data()["sets"]:
        assert s["scores"].get("protocol") == info["stamp"]


# ── phones ───────────────────────────────────────────────────────────────────

def _css() -> str:
    return "".join(re.findall(r"<style>(.*?)</style>", page(), re.S))


def test_nothing_wider_than_a_phone_outside_a_scroller():
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)
    css = re.sub(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", r"\1", css)          # media blocks: their rules count too
    wide = []
    for sels, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for prop, px in re.findall(r"(?<![-\w])(min-width|width)\s*:\s*(\d+(?:\.\d+)?)px", decls):
            if float(px) > PHONE:
                for sel in (s.strip() for s in sels.split(",")):
                    if not (sel.startswith(".tw ") or sel.startswith("svg.pipe")):
                        wide.append((sel, prop, px))
    assert not wide, f"fixed widths above {PHONE}px outside a scroller: {wide}"
    # a grid track whose minimum is its content (a bare 1fr is minmax(auto, 1fr)) lets a wide table inside a
    # scroller widen its column past the phone: every track must be able to shrink to 0
    for sels, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for val in re.findall(r"grid-template-columns\s*:\s*([^;}]+)", decls):
            bare = re.sub(r"minmax\(0,\s*[\d.]+fr\)", " ", val)
            assert not re.search(r"\d*\.?\d+fr|\bauto\b", bare), (sels.strip(), val)
    base = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*\s*\}", " ", re.sub(r"/\*.*?\*/", "", _css(), flags=re.S))
    rules = dict((s.strip(), d) for s, d in re.findall(r"([^{}]+)\{([^{}]*)\}", base))   # outside any media query
    assert "overflow-x:auto" in rules.get(".tw", "") and "overflow-x:auto" in rules.get(".panel", "")
    # an absolutely positioned element (the hidden "differs" words) escapes a scroller that is not its containing
    # block and widens the page: the scroller must be positioned
    assert "position:relative" in rules.get(".tw", ""), ".tw must contain its absolutely positioned descendants"
    for sel, d in rules.items():
        if "position:absolute" in d.replace(" ", ""):
            assert sel.startswith(".vh") or sel.startswith("svg.pipe"), sel
    # on a phone the figure gives way to its stacked list (stages_flowchart's CSS)
    swap = [int(w) for w, body in re.findall(r"@media\s*\(max-width:\s*(\d+)px\)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", _css())
            if re.search(r"svg\.pipe\s*\{[^}]*display:\s*none", body) and re.search(r"\.pipe-m\s*\{[^}]*display:\s*block", body)]
    assert swap and max(swap) >= PHONE, "no media query swaps the figure for its phone list"
    body = body_html()
    for m in re.finditer(r"<table\b", body):
        assert re.search(r'<div class="tw[^"]*">$', body[max(0, m.start() - 40):m.start()]), body[max(0, m.start() - 80):m.start() + 40]
    for m in re.finditer(r'<svg class="pipe"', body):
        assert body[max(0, m.start() - 30):m.start()].endswith('<div class="panel">'), "the figure sits in its scroller"
    for style in re.findall(r'style="([^"]*)"', re.sub(r"<svg class=\"(pipe|chart)\".*?</svg>", " ", body, flags=re.S)):
        for prop, px in re.findall(r"(?<![-\w])(min-width|width)\s*:\s*(\d+(?:\.\d+)?)px", style):
            assert float(px) <= PHONE, style
    for tag in re.findall(r"<svg\b[^>]*>", body):
        w = re.search(r'\swidth="(\d+(?:\.\d+)?)', tag)
        assert not w or float(w.group(1)) <= PHONE, tag
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page()


def test_every_figure_link_lands_on_the_page():
    svg = re.search(r'<svg class="pipe".*?</svg>', page(), re.S).group(0)
    targets = {t for t in re.findall(r'href="#([\w-]+)"', svg) if not t.startswith("i-")}
    ids = set(re.findall(r'\sid="([\w-]+)"', page()))
    assert targets and targets <= ids, sorted(targets - ids)
    nav = re.search(r"<nav>(.*?)</nav>", page(), re.S).group(1)
    assert set(re.findall(r'href="#([\w-]+)"', nav)) <= ids


if __name__ == "__main__":
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
