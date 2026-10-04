"""The reports index (reports/index.html, served at /reports/) and the archived pages it lists, written by
features/forecast/src/models/export_reports_index.py (STAGES_DESIGN.md A8, 2026-10-03).

Pins what makes it obvious which report to read: the index is current with reports/ and the model sets on
disk (a rebuild gives the same bytes); every page under reports/ is listed exactly once; every archived page
carries one banner, right after <body>, that links its replacement, and no current page carries it; the
current pages name the five stages (no heading starts "Stage 1" or "Stage 2", the old two-stage names) and
call no forecast, set or part "today's" (each part has one fixed name, shared/lineup.py; the served set is
"the live forecast" and wears a LIVE badge; Chase, 2026-10-04); every link in the index lands on a file or a
route; and the index reads plainly and fits a 320 px phone.

    venv/bin/python tests/test_reports_index.py
"""
from __future__ import annotations

import collections
import html
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "features" / "forecast" / "src" / "models"
for _p in (str(ROOT), str(MODELS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import export_reports_index as RI  # noqa: E402

REPORTS = ROOT / "reports"
PHONE = 320                                   # px: the narrowest screen the index must fit without sideways scroll
_CACHE: dict = {}


def index() -> str:
    if "index" not in _CACHE:
        _CACHE["index"] = RI.render()
    return _CACHE["index"]


def page(name: str) -> str:
    return (REPORTS / name).read_text(encoding="utf-8")


def visible(s: str) -> str:
    """What a reader sees: no styles, scripts or tags; entities read as characters."""
    s = re.sub(r"<(style|script)\b.*?</\1>", " ", s, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def headings(text: str) -> list:
    """(tag, id, words) of every h1–h3 in a page's HTML, as written in the file."""
    body = re.sub(r"<(style|script)\b.*?</\1>", " ", text, flags=re.S)
    return [(m.group(1), (re.search(r'\sid="([^"]+)"', m.group(2)) or [None, None])[1], visible(m.group(3)))
            for m in re.finditer(r"<(h[1-3])\b([^>]*)>(.*?)</\1>", body, re.S)]


def script_headings(text: str, ids) -> list:
    """The words a page's script puts into its headings: every string literal assigned to the textContent or
    innerHTML of a heading's id."""
    out = []
    for i in ids:
        for m in re.finditer(r"getElementById\('" + re.escape(i) + r"'\)\.(?:textContent|innerHTML)\s*=\s*([^;]*);", text):
            for quoted, backticked in re.findall(r"""'([^'\\]*(?:\\.[^'\\]*)*)'|`([^`]*)`""", m.group(1)):
                out += [visible(s) for s in (quoted, backticked) if s]
    return out


# ── current ──────────────────────────────────────────────────────────────────

def test_the_index_is_current():
    assert RI.OUT == REPORTS / "index.html"                                   # served at /reports/
    assert RI.OUT.exists(), "no index: run features/forecast/src/models/export_reports_index.py"
    assert RI.OUT.read_text(encoding="utf-8") == index(), \
        "the index is stale: rerun features/forecast/src/models/export_reports_index.py"
    assert RI.render() == index(), "two builds differ: the index reads a clock or an unordered listing"


def test_every_report_is_listed_exactly_once():
    listed = collections.Counter(re.findall(r'\sdata-file="([^"]+)"', index()))
    on_disk = sorted(p.name for p in REPORTS.glob("*.html") if p.name != "index.html")
    assert sorted(listed) == on_disk, (sorted(set(on_disk) - set(listed)), sorted(set(listed) - set(on_disk)))
    assert all(n == 1 for n in listed.values()), [f for f, n in listed.items() if n > 1]
    # each entry links the page it lists
    for f in listed:
        entry = re.search(r'<(a|div) class="[^"]*" data-file="' + re.escape(f) + r'"(.*?)</\1>', index(), re.S)
        assert entry and f'href="/reports/{f}"' in entry.group(0), f


def test_the_reading_order():
    """Start here is the stages report; then how it works; then the live forecast's S2 explorer and the S3–S4 explorer;
    every other set's explorer sits in the fold; the archived pages under Archived."""
    def files(sid):
        sec = re.search(rf'<section id="{sid}">(.*?)</section>', index(), re.S)
        assert sec, sid
        return re.findall(r'\sdata-file="([^"]+)"', sec.group(1))
    sets = RI.model_sets()
    assert sets[0]["served"] and sum(r["served"] for r in sets) == 1
    assert files("start") == [RI.STAGES] and files("works") == [RI.WORKS]
    assert files("deeper") == [sets[0]["file"], RI.STAGE2]
    assert files("sets") == [r["file"] for r in sets[1:] if (REPORTS / r["file"]).exists()]
    assert files("archived") == list(RI.ARCHIVED)
    assert "<details" in re.search(r'<section id="sets">(.*?)</section>', index(), re.S).group(1)
    assert set(RI.ARCHIVED).isdisjoint(RI.current_pages())


# ── archived ─────────────────────────────────────────────────────────────────

def test_each_archived_page_carries_one_banner_linking_its_replacement():
    for f, (day, repl, why) in RI.ARCHIVED.items():
        text = page(f)
        assert text.count(RI.BANNER_START) == 1 and text.count(RI.BANNER_END) == 1, f
        body = re.search(r"<body\b[^>]*>", text)
        assert text.startswith(RI.BANNER_START, body.end()), f"{f}: the banner is not right after <body>"
        banner = text[body.end():text.index(RI.BANNER_END) + len(RI.BANNER_END)]
        assert banner == RI.banner(f), f"{f}: the banner is stale: rerun export_reports_index.py"
        words = visible(banner)
        assert words.startswith(f"Archived {RI.day_words(day)}. {RI.BANNER_TEXT} Read instead:"), words
        assert f'href="/reports/{repl}"' in banner, (f, repl)
        target, _, anchor = repl.partition("#")
        assert (REPORTS / target).is_file() and target in RI.current_pages(), (f, repl)
        assert not anchor or f'id="{anchor}"' in page(target), (f, repl)
        assert RI.stamped(text, f) == text, f"{f}: stamping again would change it"


def test_no_current_page_carries_the_banner():
    for f in RI.current_pages() + ["index.html"]:
        text = index() if f == "index.html" else page(f)
        assert RI.BANNER_START not in text and 'id="archived-banner"' not in text, f


def test_the_stamper_goes_right_after_body_and_once():
    f = next(iter(RI.ARCHIVED))
    raw = '<!doctype html><html><head><style>body{margin:0}</style></head><body class="x">\n<div class="wrap"><h1>Old</h1></div></body></html>'
    once = RI.stamped(raw, f)
    assert once.startswith('<!doctype html><html><head><style>body{margin:0}</style></head><body class="x">' + RI.BANNER_START)
    assert once.replace(RI.banner(f), "") == raw and RI.stamped(once, f) == once
    # a changed table rewrites the banner in place, never adds a second
    stale = once.replace(RI.day_words(RI.ARCHIVED[f][0]), "1 Jan 2020")
    assert RI.stamped(stale, f) == once


def test_a_rebuild_of_the_graded_page_keeps_its_banner():
    """export_how_it_works writes the archived graded page only when asked (--graded), and stamps it straight after."""
    src = (MODELS / "export_how_it_works.py").read_text()
    assert src.count("OUT_GRADED.write_text(") == 1
    block = re.search(r'if "--graded" in argv:\n(.*?)\n    print', src, re.S)
    assert block and "OUT_GRADED.write_text(report_graded(S))" in block.group(1) and "RI.stamp(OUT_GRADED)" in block.group(1)
    assert block.group(1).index("write_text") < block.group(1).index("RI.stamp")


# ── the five stages on the current pages ────────────────────────────────────

OLD = re.compile(r"^\W*stage\s*[12]\b", re.I)


def test_current_pages_name_the_five_stages():
    for f in RI.current_pages():
        text = page(f)
        hs = headings(text)
        old = [w for _, _, w in hs if OLD.search(w)]
        old += [w for w in script_headings(text, [i for _, i, _ in hs if i]) if OLD.search(w)]
        assert not old, (f, old)
    works = [w for t, _, w in headings(page(RI.WORKS)) if t == "h2"]
    for code in ("S1 ·", "S2 ·", "S3 ·", "S4 ·", "S5 ·", "OUT ·"):
        assert sum(w.startswith(code) for w in works) == 1, (code, works)
    s2 = [w for t, _, w in headings(page(RI.STAGE2)) if t in ("h1", "h2")]
    assert s2[0] == RI.TITLES[RI.STAGE2] and any(w.startswith("S3 ·") for w in s2) and any(w.startswith("S4 ·") for w in s2), s2
    stages = [w for t, _, w in headings(page(RI.STAGES)) if t == "h3"]
    assert all(any(w.startswith(c) for w in stages) for c in ("S1", "S2", "S3", "S4", "S5")), stages


def test_each_explorer_is_titled_by_its_set():
    """Every set's S2 page is titled by the set's name, its S2 · S3 · S4 in their fixed words, the live forecast's
    too; the live forecast's heading alone wears the LIVE badge, beside the name."""
    badge = re.compile(r'<span class="live-badge"[^>]*>LIVE</span>')
    for r in RI.model_sets():
        text = page(r["file"])
        want = f'{RI.EXPLORER_TITLE}: {r["words"]}'
        assert RI.explorer_title(r) == want and r["words"] == " · ".join(RI.LU.words(c, r["parts"][c]) for c in RI.SET_COLS)
        title = html.unescape(re.search(r"<title>(.*?)</title>", text).group(1))
        h1 = [w for t, _, w in headings(text) if t == "h1"]
        assert title == want and h1 == [want + (" LIVE" if r["served"] else "")], (r["name"], title, h1)
        h1_html = re.search(r"<h1\b[^>]*>(.*?)</h1>", text, re.S).group(1)
        assert len(badge.findall(h1_html)) == (1 if r["served"] else 0), r["name"]
        assert "How the SF CSO forecast works" not in text, r["name"]       # the old title duplicated how it works
        assert f'"name":"{r["name"]}"' in text                                 # the stored name stays, in small print


# ── names (Chase, 2026-10-04) ────────────────────────────────────────────────

# "today's" as a name: before a forecast, a set or a part ("today's forecast", "today's split", "today's 4 basins",
# "today's overflow model", "today's S2"), standing alone for one ("than today's,", "(differs from today's)"), or
# "differs from today". A day can still be today's: "today's rain", "today's total", "today's hours so far".
TODAYS_NAME = re.compile(
    r"\btoday's\s+(?:\w+\s+)?(?:forecast|split|lingering|table|rule|basins|model|set|chain|design|recipe)s?\b"
    r"|\btoday's\s+S[1-5]\b"
    r"|\btoday's(?=\s*(?:[,.;:)\]<\"'`—–]|$))"
    r"|\bdiffers?\s+from\s+today\b", re.I | re.M)


def as_written(text: str) -> str:
    """A page's whole text (words, attributes, scripts and the data they render) with every apostrophe spelled one
    way: entities read, JavaScript's \\' and JSON's \\u2019 undone."""
    return html.unescape(text).replace("\\'", "'").replace("\\u2019", "'").replace("\u2019", "'")


def artifact_quotes() -> list:
    """Sentences in the stages report's artifacts that call a forecast "today's" (the tooltips quote an artifact's own
    reason verbatim, so its words reach the page): there must be none."""
    import export_stages_report as SR
    out = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and TODAYS_NAME.search(as_written(v)):
            out.add(as_written(v))
    for a in SR.sources(SR.load()).values():
        walk(a.data)
    return sorted(out, key=len, reverse=True)


def test_no_current_page_calls_a_forecast_todays():
    quotes = artifact_quotes()
    assert not quotes, f"an artifact the stages report quotes calls a forecast \"today's\" (fix the stored words, rebuild): {quotes[:3]}"
    hits = {}
    for f in RI.current_pages() + ["index.html"]:
        text = as_written(index() if f == "index.html" else page(f))
        found = [re.sub(r"\s+", " ", text[max(0, m.start() - 50):m.end() + 30]) for m in TODAYS_NAME.finditer(text)]
        if found:
            hits[f] = found[:4]
    assert not hits, f"\"today's\" as a name (say the part's fixed name, or \"the live forecast\"): {hits}"
    # the pattern catches what the pages said before, and leaves a day's words alone
    for old in ("Today's forecast: <b>", "today's 4 basins (Islais in Southeast)", "today's split", "today's lingering table",
                "today's rule (live_v2)", "Today's overflow model (S2)", "fewer errors than today's, basins pooled",
                "(differs from today's)", "'S3–S4 differ from today\\'s'", "Challenger − today's</th>", "tested on today's chain",
                "today's S2 explorer", "Today's set was also picked"):
        assert TODAYS_NAME.search(as_written(old)), old
    for day in ("today's rain total", "Today's peak 1h", "Response to today's rain", "fills today's hours so far",
                "today's total and the last two days", "today's what-if features", "today's rain in 5 bands, yesterday's rain"):
        assert not TODAYS_NAME.search(day), day


# ── links, words, phones ─────────────────────────────────────────────────────

def test_every_link_in_the_index_lands():
    from app.wsgi import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    ids = set(re.findall(r'\sid="([\w-]+)"', index()))
    links = re.findall(r'\s(?:href|src)="([^"]+)"', index())
    assert links
    for url in links:
        if url.startswith("https://"):
            assert url.startswith("https://fonts.googleapis.com/"), url
            continue
        path, _, anchor = url.partition("#")
        if not path:
            assert anchor in ids, url
        elif path == RI.URL:
            assert RI.URL in rules, url
        elif path.startswith("/reports/"):
            target = path[len("/reports/"):]
            assert (REPORTS / target).is_file(), url
            assert not anchor or f'id="{anchor}"' in page(target), url
        elif path.startswith("/static/"):
            assert (ROOT / "app" / path.lstrip("/")).is_file(), url
        else:
            assert path in rules, url


def test_the_index_reads_plainly():
    words = visible(index())
    for w in (r"\bcost", r"\bcheapest\b", r"\bKing\b", r"\balarm[\s_-]*line"):
        assert not re.search(w, words, 0 if w == r"\bKing\b" else re.I), w
    assert not re.search(r"\bstage\s*[12]\b", words, re.I), re.findall(r".{30}\bstage\s*[12]\b.{30}", words, re.I)
    # the sets read by their lineup words; a stored name shows only as small print
    fold = re.search(r'<section id="sets">(.*?)</section>', index(), re.S).group(1)
    for r in RI.model_sets()[1:]:
        row = re.search(r'data-file="' + re.escape(r["file"]) + r'"[^>]*>(.*?)</a>', fold, re.S)
        assert row, r["name"]
        assert visible(re.search(r'<span class="w">(.*?)</span><small>', row.group(1), re.S).group(1)) == r["words"], r["name"]
        assert f"<small>stored as <code>{r['name']}</code>" in row.group(1), r["name"]
        tinted = [visible(t) for t in re.findall(r'<span class="d">(.*?)</span>', row.group(1))]
        assert len(tinted) == sum(r["differs"].values()), (r["name"], tinted)
    stored = set(re.findall(r"<code>([^<]+)</code>", fold))
    assert RI.model_sets()[0]["name"] not in stored                       # the live forecast is under Go deeper …
    live = re.search(r'<a class="item" data-file="' + re.escape(RI.model_sets()[0]["file"]) + r'"[^>]*><b>(.*?)</b>', index(), re.S)
    assert live and live.group(1) == RI.esc(RI.explorer_title(RI.model_sets()[0])) + " " + RI.LIVE_BADGE   # … by its name, with its badge
    assert index().count(RI.LIVE_BADGE) == 1
    for f in RI.ARCHIVED:
        assert RI.heading(f) in words, f


def test_the_index_fits_a_phone():
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in index()
    css = re.sub(r"/\*.*?\*/", "", "".join(re.findall(r"<style>(.*?)</style>", index(), re.S)), flags=re.S)
    flat = re.sub(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", r"\1", css)
    for sels, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", flat):
        for prop, px in re.findall(r"(?<![-\w])(min-width|width)\s*:\s*(\d+(?:\.\d+)?)px", decls):
            assert float(px) <= PHONE / 4, (sels.strip(), prop, px)          # only the logo has a width
        for val in re.findall(r"grid-template-columns\s*:\s*([^;}]+)", decls):
            assert not re.search(r"\d*\.?\d+fr|\bauto\b", re.sub(r"minmax\(0,\s*[\d.]+fr\)", " ", val)), (sels, val)
        assert "nowrap" not in decls and "position:absolute" not in decls.replace(" ", ""), sels
    for style in re.findall(r'style="([^"]*)"', index()):
        assert not re.search(r"(?<![-\w])(min-)?width\s*:\s*\d", style), style
    for f in RI.ARCHIVED:                                                    # the banner wraps on any screen
        banner = RI.banner(f)
        assert not re.search(r"(?<![-\w])(min-)?width\s*:\s*\d", banner) and "nowrap" not in banner, f


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
