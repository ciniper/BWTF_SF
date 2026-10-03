"""No cost rankings (STAGES_DESIGN.md A3 and Part B 21; Chase, 2026-10-01: fixed public risk levels,
no cost-chosen line). The cost rule (false alarms + k × misses) ranks nothing and sets nothing.

The model analysis (report_models.py: the King, the cheapest line, the sensitivity grids) is retired;
the five stages report replaces it. How it is graded, the Model check, the live-corrections replays
and the weather report count days (caught, missed, false alarms) and give the standard threshold
scores (POD, FAR, POFD, CSI) at the risk-level edges, never a weighted sum. This keeps it that way:

1. no "cost", "cheapest", "King" (as a word) or "alarm line" in the forecast report pages under
   reports/ or in app/templates/forecast/, except the phrases in ALLOWED, which use a word for
   something that is not a ranking (each with its reason; a stale exception fails too);
2. no miss-weight constant in features/forecast/src/models/*.py: no WFN / MISS_WEIGHT / costRatio
   (or the design's MISS_COST, FALSE_ALARM_COST, P_STAR, cost_basis), no PRIMARY / WEIGHTINGS
   tuple of weightings, no "a miss costs N false alarms" label and no false-alarm + k × miss sum;
3. the model analysis stays retired: no generator, no narrative, no page, no live link.

Out of scope: the running-costs page (/alerts/costs, features/alerts/), which is about what the
site's infrastructure costs, not about ranking forecasts.

Run: venv/bin/python tests/test_no_cost_rankings.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
MODELS = ROOT / "features" / "forecast" / "src" / "models"

# the forecast report pages, and the live forecast page's templates
REPORT_GLOBS = ("2026-09_forecast_*.html", "2026-09_live_replay*.html", "2026-09_weather_models.html",
                "2026-10_forecast_stages.html")   # the stages report, when it exists
TEMPLATES = ROOT / "app" / "templates" / "forecast"

# case-insensitive, anywhere in the file (text, attributes, scripts): costRatio, lineCost and
# cheapestLine are caught along with the words
BANNED = {
    "cost": re.compile(r"cost", re.I),
    "cheapest": re.compile(r"cheapest", re.I),
    "King": re.compile(r"\bking\b", re.I),
    "alarm line": re.compile(r"alarm[\s_-]*line", re.I),
}

# Exact phrases a page may keep: the word means something else there. {file name: [(phrase, reason)]}
ALLOWED = {
    "2026-09_forecast_how_it_works.html": [
        ('Chase, 2026-09-30: "simpler is king"',
         "a quote asking for fewer model terms (Could it be simpler?), not a ranking of sets"),
        ("The 19 inputs are cheap to compute, so the cost of the extra terms is not speed",
         "cost in the everyday sense: what extra terms cost in clarity, not a weighted sum of misses and false alarms"),
    ],
}

# miss weights in the model code
WEIGHT_NAMES = re.compile(r"\b(WFN|MISS_WEIGHT|MISS_COST|FALSE_ALARM_COST|P_STAR|WEIGHTINGS|costRatio|cost_basis)\b")
# a module-level tuple whose label is a weighting: PRIMARY = ("a miss costs 2 false alarms", 1, 2), WEIGHTINGS = [(...), ...]
WEIGHTING_TUPLE = re.compile(r"""^[A-Za-z_][A-Za-z0-9_]*\s*=\s*[\[(]\s*\(?\s*["'][^"'\n]*\b(miss|false alarm)[^"'\n]*["']\s*,\s*\d""", re.M | re.I)
# "a miss costs 2 false alarms", "false alarm costs 2 misses" (verify's elementary score "a miss costs 1 − θ" is no weight)
MISS_PHRASE = re.compile(r"\bmiss(es)?\s+costs?\s+(\d+(\.\d+)?|one|two|three|four|five|six|ten)\s+(false\s+alarms?|FA)\b"
                         r"|\bfalse\s+alarms?\s+costs?\s+(\d+(\.\d+)?|one|two|three)\s+miss", re.I)
# fp + k × fn (or fn × k + fp) on the confusion counts, and the weight variables wfp / wfn
WEIGHTED_SUM = re.compile(r"""\bfp\b["'\]]*\s*\+\s*[\w.]+\s*\*\s*[\w\[\]"'.]*\bfn\b|\bfn\b["'\]]*\s*\*\s*[\w.]+\s*\+\s*[\w\[\]"'.]*\bfp\b|\bw_?f[pn]\b""")


def _pages() -> list[Path]:
    pages = [p for g in REPORT_GLOBS for p in sorted(REPORTS.glob(g))]
    pages += sorted(p for p in TEMPLATES.rglob("*") if p.is_file())
    return pages


def _hits(text: str) -> dict:
    out = {}
    for word, pat in BANNED.items():
        for m in pat.finditer(text):
            out.setdefault(word, []).append(re.sub(r"\s+", " ", text[max(0, m.start() - 60):m.end() + 60]))
    return out


def test_report_pages_and_the_forecast_template_name_no_cost_ranking():
    pages = _pages()
    names = {p.name for p in pages}
    for must in ("2026-09_forecast_how_it_works.html", "2026-09_forecast_how_it_is_graded.html", "2026-09_live_replay.html",
                 "2026-09_live_replay_synthetic.html", "2026-09_weather_models.html", "page.html"):
        assert must in names, f"{must} not scanned: {sorted(names)}"
    bad = {}
    for p in pages:
        text = p.read_text(errors="replace")
        for phrase, reason in ALLOWED.get(p.name, []):
            assert reason and phrase in text, f"{p.name}: stale exception, the page no longer says {phrase!r}; drop it from ALLOWED"
            text = text.replace(phrase, "")
        hits = _hits(text)
        if hits:
            bad[str(p.relative_to(ROOT))] = {w: h[:3] for w, h in hits.items()}
    assert not bad, f"cost-ranking words on forecast pages (STAGES_DESIGN.md A3): {bad}"
    print(f"   {len(pages)} pages clean of cost / cheapest / King / alarm line; {sum(len(v) for v in ALLOWED.values())} listed exceptions")


def test_no_miss_weight_in_the_model_code():
    files = sorted(MODELS.glob("*.py"))
    assert len(files) > 20, files
    bad = []
    for p in files:
        src = p.read_text()
        for pat in (WEIGHT_NAMES, WEIGHTING_TUPLE, MISS_PHRASE, WEIGHTED_SUM):
            for m in pat.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                bad.append(f"{p.name}:{line}: {src.splitlines()[line - 1].strip()[:120]}")
    assert not bad, "miss weights in features/forecast/src/models (A3: nothing weighs a miss against a false alarm):\n  " + "\n  ".join(bad)
    print(f"   {len(files)} model modules: no miss weight, no weighting tuple, no false-alarm + k × miss sum")


def test_the_patterns_catch_what_was_removed():
    """The checks above must fire on the code and copy A3 removed (report_models, export_how_it_works,
    replay_live, the Model check), or a clean pass means nothing."""
    removed_code = ['PRIMARY = ("a miss costs 2 false alarms", 1, 2)',
                    'WEIGHTINGS = [("a miss costs 2 false alarms", 1, 2), ("1 : 1", 1, 1)]',
                    "MISS_WEIGHT = 2", "WFN = 2", "const costRatio = 2;",
                    'return c["fp"] + MISS_WEIGHT * c["fn"], c',
                    'return {**s_, "cost": s_["fp"] + WFN * s_["fn"]}',
                    "c = wfp * fp + wfn * d[\"fn\"]"]
    for src in removed_code:
        assert any(p.search(src) for p in (WEIGHT_NAMES, WEIGHTING_TUPLE, MISS_PHRASE, WEIGHTED_SUM)), src
    removed_copy = ["King under the primary setting", "cheapest line", "Cost = false alarms + 2 × missed",
                    "its alarm line is <b>25%</b>", "cost (miss = 2 FA)", "lineCost(", "cheapestLine"]
    for txt in removed_copy:
        assert _hits(txt), txt
    # and stay quiet on what is not a weighting: scores, the elementary score, the everyday words
    for ok in ("csi = tp / (tp + fn + fp)", "far = fp / (tp + fp)", "pofd = fp / (fp + tn)",
               "At θ an alert is p ≥ θ; a miss costs 1 − θ and a false alarm θ.", "PRIMARY_LEAD = 1",
               'PRIMARY = ("link_zone_swap", "basin_swap")'):
        assert not any(p.search(ok) for p in (WEIGHT_NAMES, WEIGHTING_TUPLE, MISS_PHRASE, WEIGHTED_SUM)), ok
    for ok in ("a ranking of overflow days above quiet ones", "taking a sample", "the 25% line", "Medium and up (21%+)"):
        assert not _hits(ok), ok


def test_the_model_analysis_stays_retired():
    for gone in (MODELS / "report_models.py", MODELS / "report_models_narrative.html", REPORTS / "2026-09_model_analysis.html"):
        assert not gone.exists(), f"{gone.relative_to(ROOT)} is retired (A3): the five stages report replaces it"
    # no live link or call to it: code and pages, and the docs that describe today's pages (dated
    # design and findings docs are records and keep their history)
    roots = [ROOT / "app", ROOT / "features", ROOT / "shared", ROOT / "scripts"]
    files = [p for r in roots for p in r.rglob("*") if p.suffix in (".py", ".html", ".js", ".sh") and p.is_file() and "__pycache__" not in p.parts]
    files += sorted(REPORTS.glob("*.html"))
    files += [ROOT / d for d in ("README.md", "DEPLOY.md", "docs/OVERVIEW.md", "features/forecast/README.md", "features/forecast/SWAPS.md") if (ROOT / d).exists()]
    bad = [str(p.relative_to(ROOT)) for p in files if re.search(r"2026-09_model_analysis|report_models", p.read_text(errors="replace"))]
    assert not bad, f"links to the retired model analysis: {bad}"
    print(f"   {len(files)} files: no link to the model analysis")


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
