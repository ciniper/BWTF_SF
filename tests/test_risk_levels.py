"""Public risk levels (shared/risk_levels.py): Low 0–20, Medium 21–50, High 51–80, Extreme 81–100 on the
whole percent the page shows (Chase, 2026-10-01, STAGES_DESIGN.md A3), one table behind every public display.

Pins the edges and the page rounding, renders the Forecast page and the Today board (offline: Flask test
client, stubbed forecast row) and checks they show the levels, and guards the app against hard-coded
cutoffs creeping back. When node is installed, the pages' own JS is run against the Python table.

    venv/bin/python tests/test_risk_levels.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import risk_levels as R  # noqa: E402

KEYS = lambda ps: [R.level_of(p).key for p in ps]  # noqa: E731


def test_levels_cover_every_whole_percent_once_in_order():
    assert [(lv.label, lv.lo, lv.hi) for lv in R.LEVELS] == [("Low", 0, 20), ("Medium", 21, 50), ("High", 51, 80), ("Extreme", 81, 100)]
    assert R.LEVELS[0].lo == 0 and R.LEVELS[-1].hi == 100 and all(a.hi + 1 == b.lo for a, b in zip(R.LEVELS, R.LEVELS[1:]))
    assert [R.level_of_percent(w).key for w in (0, 20, 21, 50, 51, 80, 81, 100)] == ["low", "low", "medium", "medium", "high", "high", "extreme", "extreme"]
    assert R.level_of_percent(-3).key == "low" and R.level_of_percent(140).key == "extreme"
    assert len({lv.key for lv in R.LEVELS}) == len({lv.color for lv in R.LEVELS}) == 4


def test_edges_and_the_page_rounding():
    assert R.edges() == (0.205, 0.505, 0.805)
    assert KEYS([0.2049, 0.205, 0.5049, 0.505, 0.8049, 0.805]) == ["low", "medium", "medium", "high", "high", "extreme"]
    assert KEYS([0, 0.0, 1, 1.0, None]) == ["low", "low", "extreme", "extreme", "low"]
    # JavaScript's Math.round(p * 100), measured in node: halves go up, unlike Python's round()
    js = {0.205: 21, 0.505: 51, 0.805: 81, 0.125: 13, 0.215: 22, 0.285: 28, 0.565: 56, 0.575: 57, 0.995: 100, 0.2049: 20, 0.0: 0, 1.0: 100}
    assert {p: R.whole_percent(p) for p in js} == js
    assert round(0.205 * 100) == 20 and R.whole_percent(0.205) == 21          # why the module does not use round()
    assert R.whole_percent(None) == 0 and R.whole_percent(float("nan")) == 0 and R.whole_percent(0.49999999999999994 / 100) == 0
    for e in R.edges():                                                        # the edge is where the shown percent turns
        assert R.level_of(e) != R.level_of(e - 1e-9)


def test_export_is_json_and_carries_each_levels_edge():
    ex = R.export()
    assert json.loads(json.dumps(ex)) == ex
    assert [lv["key"] for lv in ex["levels"]] == ["low", "medium", "high", "extreme"] and ex["edges"] == [0.205, 0.505, 0.805]
    assert [lv["edge"] for lv in ex["levels"]] == [0.0, 0.205, 0.505, 0.805]
    assert set(ex["levels"][0]) == {"key", "label", "lo", "hi", "color", "tint", "edge"}


def test_colours_come_from_the_palette_and_read_on_white():
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    palette = {"#237059", "#c99a12", "#d4763a", "#b5310a", "#8f2508"}            # the forecast page's riskInfo colours before 2026-10-01
    assert R.LEVELS[0].color == "#237059" and {lv.color for lv in R.LEVELS} <= palette   # Low is green
    for lv in R.LEVELS:
        assert 1.05 / (lum(lv.color) + 0.05) >= 3.0, lv                         # AA for large text, bars and borders
        assert lum(lv.tint) > 0.6, lv                                           # tints stay light
    above = [lum(lv.color) for lv in R.LEVELS[1:]]
    assert above == sorted(above, reverse=True)                                # past Low, each level darker than the last


# ── the pages ────────────────────────────────────────────────────────────────

def _forecast_html() -> str:
    from app.wsgi import app
    with app.test_client() as c:
        r = c.get("/forecast")
    assert r.status_code == 200
    return r.data.decode()


def _today_html(zones: dict) -> str:
    from app.wsgi import app
    from app import landing as L
    from features.today import page as T
    S = lambda sid, name, status: types.SimpleNamespace(station_id=sid, station_name=name, status=types.SimpleNamespace(value=status), has_cso=False, sample_date=datetime(2026, 9, 23))  # noqa: E731
    board = L.today_board([S("4601", "Fort Funston", "safe"), S("4612", "Crissy Field East", "posted")], {"zones": zones, "ahead": [("Tomorrow", 12)]}, "", now=datetime(2026, 10, 1, 9, 0))
    with app.test_request_context("/today"):
        return T.render_page({"board": board, "conditions": [], "facts": {}, "generated": "October 1, 2026 at 9:00 AM PDT"})


def _injected(html: str) -> dict:
    assert html.count("const RISK_LEVELS = ") == 1                              # one injected constant per page
    return json.loads(re.search(r"const RISK_LEVELS = (\{.*?\});\n", html).group(1))


def test_forecast_page_draws_the_levels_from_the_one_table():
    h = _forecast_html()
    assert _injected(h) == R.export()
    assert "Low 0–20%, Medium 21–50%, High 51–80%, Extreme 81–100%." in h      # the "Reading the number" copy, from the table
    assert "alarm line the alerts use" not in h and "10% low, 25% moderate, 50% high" not in h
    for lv in R.LEVELS:
        assert f".risk-{lv.key} {{ border-color: {lv.color}; }}" in h and f".day-card.g-{lv.key} {{ border-color: {lv.color}; }}" in h
    assert 'class="risk-banner risk-low" id="riskBanner"' in h and "st-discharge" in h   # "What happened" grades by outcome, not by level
    assert "{{" not in h and "{%" not in h


def test_today_board_tiles_and_risk_view_use_the_levels():
    h = _today_html({"ocean": 20, "baker_china": 21, "north": 51, "east": 81})
    assert _injected(h) == R.export()
    for key, pct in (("low", 20), ("medium", 21), ("high", 51), ("extreme", 81)):
        assert f'<b class="lvl-{key}">{pct}%</b> overflow risk today' in h, key
    for lv in R.LEVELS:
        assert f".zone.lvl-{lv.key}{{border-color:{lv.color}}}" in h and f".status-dot.lvl-{lv.key}{{background:{lv.color}}}" in h
    assert 'class="hi"' not in h and "under 10%" not in h and "50% or more" not in h
    assert "{{" not in h and "{%" not in h


def test_home_page_rising_means_the_level_goes_up():
    from app import landing as L
    from features.forecast import page as fp
    def snap(today, *ahead):
        days = {"d0": {"is_today": True, "day_offset": 0, "zones": {"ocean": today, "east": 0.01}}}
        days.update({f"d{i}": {"day_offset": i, "label": f"Day {i}", "zones": {"ocean": p}} for i, p in enumerate(ahead, 1)})
        return {"snapshot": {"predictions": days}}
    saved = fp._read_row
    try:
        cases = {(0.15, 0.22): "today 15% risk · rising",     # Low → Medium
                 (0.25, 0.45): "today 25% risk",              # Medium → Medium: +20 points, the old rule said rising
                 (0.05, 0.14): "today 5% risk",               # still Low
                 (0.205, 0.30): "today 21% risk",             # 0.205 shows 21%, Medium, as on the Forecast page
                 (0.48, 0.505): "today 48% risk · rising",    # Medium → High at the edge
                 (0.60, 0.20): "today 60% risk"}
        for (t, a), want in cases.items():
            fp._read_row = lambda t=t, a=a: snap(t, a)
            assert L._fact_forecast() == want, (t, a, L._fact_forecast())
        fp._read_row = lambda: snap(0.125, 0.205, 0.5049)
        assert L._forecast_risks() == {"zones": {"ocean": 13, "east": 1}, "ahead": [("Day 1", 21), ("Day 2", 50)]}   # halves up, as the page rounds
    finally:
        fp._read_row = saved
    lead = lambda worst, *ahead: L.today_board([], {"zones": {"ocean": worst, "east": 0}, "ahead": [(f"Day {i}", p) for i, p in enumerate(ahead, 1)]}, "")["lead"]  # noqa: E731
    assert lead(15, 18, 20).endswith("at most (Ocean Beach), staying low through Day 2.")
    assert lead(15, 30).endswith("at most (Ocean Beach), rising to 30% by Day 1.")
    assert lead(25, 45).endswith("at most (Ocean Beach).")                    # same level: no trend
    assert lead(30, 5).endswith("at most (Ocean Beach).")                     # Medium today is not "staying low"
    tiles = {t["key"]: t for t in L.today_board([], {"zones": {"ocean": 20, "east": 81}}, "")["zones"]}
    assert (tiles["ocean"]["level"], tiles["east"]["level"], tiles["north"]["level"]) == ("low", "extreme", None)


def test_the_pages_js_matches_the_python_table():
    """The forecast page's riskInfo and the board's band, run in node on every whole percent and the edges."""
    node = shutil.which("node")
    if not node:
        print("  (node not installed: JS parity skipped)"); return
    h, b = _forecast_html(), _today_html({"ocean": 1, "baker_china": 2, "north": 3, "east": 4})
    lv = re.search(r"const RISK_LEVELS = \{.*?\};\n", h).group(0)
    pct = re.search(r"const pct = p => .*?;\n", h).group(0)
    risk_info = re.search(r"function riskInfo\(prob\) \{.*?\n\}\n", h, re.S).group(0)
    band = re.search(r"const band = pct => .*?;\n", b).group(0)
    probs = [i / 1000 for i in range(1001)] + [0.2049, 0.5049, 0.8049, None]
    js = (lv + pct + risk_info + "const COL = {};\n" + re.search(r"for \(const l of RISK_LEVELS\.levels\) COL.*?;\n", b).group(0) + band +
          f"console.log(JSON.stringify({{info: {json.dumps(probs)}.map(p => riskInfo(p).cls), shown: {json.dumps(probs)}.map(p => pct(p)), "
          f"band: [...Array(101).keys()].map(w => band(w)), cols: COL}}));")
    out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out["info"] == [R.level_of(p).key for p in probs]
    assert out["shown"] == [R.whole_percent(p) for p in probs]
    assert out["band"] == ["lvl-" + R.level_of_percent(w).key for w in range(101)]
    assert out["cols"] == {f"lvl-{x.key}": x.color for x in R.LEVELS}
    for html in (h, b):                                                        # every inline script on both pages parses
        for i, src in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
            r = subprocess.run([node, "--check", "-"], input=src, capture_output=True, text=True)
            assert r.returncode == 0, (i, r.stderr[:400])


# ── the guard: no public display hard-codes a cutoff ─────────────────────────

# Comparisons against a literal that are not risk cutoffs, in the files that show risk.
NOT_CUTOFFS = ("spread < 0.02",                         # zones within 2 points read "in every zone"
               "Math.abs(ro - rd) >= 0.05")             # the two rain gauges disagree by 0.05"
OLD_COPY = ("under 10%", "10–25%", "25–50%", "50% or more", "10% low", "25% moderate", "50% high",
            "alarm line the alerts use", "(LOW)", "(MODERATE)", "(HIGH)", 'class="hi"', "b.hi{")


def _app_sources():
    for p in sorted((ROOT / "app").rglob("*")):
        if p.suffix in (".html", ".js", ".py") and p.is_file():
            yield p, p.read_text(encoding="utf-8", errors="replace")


def test_no_template_or_app_js_hard_codes_the_old_cutoffs():
    bad = []
    risk_files = []
    for p, text in _app_sources():
        rel = p.relative_to(ROOT).as_posix()
        if re.search(r"\b(MINIMAL|MODERATE|EXTREME)\b", text):
            bad.append(f"{rel}: an old band word in capitals")
        if not re.search(r"overflow risk|riskInfo|RISK_LEVELS", text):
            continue
        risk_files.append(rel)
        for snip in OLD_COPY:
            if snip in text:
                bad.append(f"{rel}: old band copy {snip!r}")
        for line in text.splitlines():
            if any(ok in line for ok in NOT_CUTOFFS):
                continue
            for m in re.finditer(r"[\w.)\]]+\s*(?:>=|<=|>|<)\s*(?:0?\.\d+|\d{2,3})\b(?![\w.%-])", line):
                bad.append(f"{rel}: comparison against a literal {m.group(0)!r}")
        for m in re.finditer(r"\b(?:risk|worst|prob|pk|m)\s*[+-]\s*(?:0?\.\d+|\d{1,2})\b", text):
            bad.append(f"{rel}: a fixed step {m.group(0)!r}")
        for m in re.finditer(r"""label:\s*['"](?:Low|Medium|High|Extreme|LOW|HIGH)['"]""", text):
            bad.append(f"{rel}: a level label typed by hand {m.group(0)!r}")
    assert {"app/templates/forecast/page.html", "app/templates/_today_board.html", "app/landing.py"} <= set(risk_files), risk_files
    assert not bad, "\n".join(bad)


def test_the_how_it_works_report_shows_the_levels_not_a_line():
    """The public how-it-works page (and its exporter) lists the four levels from the table; nothing in it
    says the banner words follow the served line, or names the old bands."""
    src = (ROOT / "features/forecast/src/models/export_how_it_works.py").read_text()
    assert "from shared.risk_levels import LEVELS" in src and not re.search(r"^LEVELS\s*=", src, re.M)
    rep = (ROOT / "reports/2026-09_forecast_how_it_works.html").read_text()
    for lv in R.LEVELS:
        assert f'<b style="color:{lv.color}">{lv.label}</b><span>{lv.lo}–{lv.hi}%</span>' in rep, lv.label
    for text in (src, rep):
        assert not re.search(r"\b(?:MINIMAL|MODERATE)\b|the banner words|the banner turns|Alarm line \d", text)


def test_risk_levels_is_wired_where_risk_is_shown():
    page = (ROOT / "app/templates/forecast/page.html").read_text()
    board = (ROOT / "app/templates/_today_board.html").read_text()
    landing = (ROOT / "app/landing.py").read_text()
    assert "RISK_LEVELS | tojson" in page and "RISK_LEVELS|tojson" in board and "from shared.risk_levels import" in landing
    assert 'globals["RISK_LEVELS"] = risk_levels.export()' in (ROOT / "app/wsgi.py").read_text()
    assert "round(float(v) * 100)" not in landing and "max(risk + 0.10, 0.25)" not in landing
    src = (ROOT / "shared/risk_levels.py").read_text()
    assert not re.search(r"^(import|from) (?!__future__|math|dataclasses)", src, re.M)   # no IO, nothing heavy: safe on the served path


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
