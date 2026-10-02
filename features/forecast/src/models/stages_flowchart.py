#!/usr/bin/env python3
"""Figure 1 of the stages report, drawn from stages_spec: five stages from rain
to the beach percentage, each graded on its own truth and scored twice
(oracle: fed the true input; chained: fed the stage before), what each score
leaves out, and what the forecast does not claim (STAGES_DESIGN.md §1).

    svg, phone_html = render("sfpuc4_v1")                      # the spec: "—" everywhere
    svg, phone_html = render("geo_v1", scores=fig, counts=n)    # a scored set

`scores` is scores.json["figure"] ({node id: {"oracle": {"v", "lo", "hi", "n",
"pos", "low_power"}, "chained": {...}, "lead": [{"v"} × 6]}, "caption": str});
a pill may also carry "window" (the words for where its number comes from),
"post" (the post-training pill) and "also" ([pill with "what"]), which its
tooltip prints beside its own CI. `counts` maps a rule id (X-… / C-…) or a
count slot (n_city_days) to a number, flat or as {"exclusions": {…},
"claims": {…}, "stages": {stage: {…}}} (a chip reads its own stage's count
first). Missing values print "—", never a placeholder; a pill fades only when
its own cell is X-POWER. ``caption(scores)`` is the one line the page prints
under the figure title. The page that embeds the figure includes
svgkit.FIT_SCRIPT once and the icon sprite (svgkit.sprite()); logos are
/static/ URLs.

The S3 inset draws the geography's own links from shared/geography.py. Node
text follows the geography it is drawn for (Part B 15): the served GEO_V1 set
says "our four basins" and its flags enter at the basin; SFPUC4 says "SFPUC's
four basins" and observations enter after the split.

Preview (standalone HTML, logos as file:// paths, for visual QC):

    venv/bin/python features/forecast/src/models/stages_flowchart.py OUT_DIR
"""
from __future__ import annotations

import itertools
import math
import numbers
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
try:
    from . import stages_spec as SP, svgkit as K
except ImportError:                                   # run as a script, or imported from src/models on sys.path
    import stages_spec as SP  # noqa: E402
    import svgkit as K  # noqa: E402
from shared import geography  # noqa: E402  (imports only the registries; reads no file)

D = "—"
STYLE = {"data": ("flow", "pa"), "fit": ("fit", "pg"), "score": ("grade", "pk"), "oracle": ("orc", "po"),
         "live": ("live", "pl"), "decision": ("dec", "pd")}
MARKERS = (("pa", "#0072BC"), ("pg", "#8a949b"), ("pk", "#8a949b"), ("po", "#237059"), ("pl", "#d4763a"), ("pd", "#26272a"))
BOX_CLASS = {"truth": "box truth", "stage": "box hub", "output": "box hub", "live": "box live", "input": "box oracle",
             "decision": "box dec", "exclusion": "xbox", "claim": "xbox"}
# Average glyph width per character as a share of the font size, by text class (measured on
# this figure's labels in Chrome, 2026-10-01): the server's first guess, so the figure reads
# right without script; FIT_SCRIPT measures the real text in the browser and shrinks to fit.
PC = {"t": 0.52, "t big": 0.49, "s": 0.48, "q": 0.48, "m": 0.46, "ct": 0.55, "mini": 0.51, "xs": 0.48, "pt": 0.47,
      "xh": 0.79, "col up": 0.74}

FIG_CSS = """
.band{fill:#f7fafc;stroke:none}.rowl{font-size:11px;font-weight:700;letter-spacing:.14em;fill:#8a949b}
.q{font-size:12px;fill:#54576F;font-style:italic}.t.big{font-size:17px}.m{font-size:11px;fill:#8a949b;letter-spacing:.02em}
.box.truth{stroke:#b9c7cf;stroke-width:1.5}.box.live{fill:#fff;stroke:#efc9ad;stroke-width:1.5}.box.oracle{stroke:#237059;stroke-width:1.5;stroke-dasharray:3 3}
.box.dec{stroke:#26272a;stroke-width:2}.xbox{fill:#f3f6f9;stroke:none}.xh{font-size:10.5px;font-weight:700;letter-spacing:.12em;fill:#8a949b;text-transform:uppercase}
.col.up{text-transform:uppercase}.xs{font-size:11.5px;fill:#54576F}.pill{fill:#f3f6f9;stroke:#d9e4e8}.pt{font-size:11.5px;fill:#54576F}
.chip.or{fill:#fff;stroke:#237059;stroke-width:1.5}.chip.ch{fill:#0072BC}.ct{font-size:11px;font-weight:700}.ct.or{fill:#237059}.ct.ch{fill:#fff}
.grade{fill:none;stroke:#8a949b;stroke-width:1.5}.orc{fill:none;stroke:#237059;stroke-width:2;stroke-dasharray:2 4;stroke-linecap:round}.live{fill:none;stroke:#d4763a;stroke-width:2}
.dec{fill:none;stroke:#26272a;stroke-width:2}
.el{font-size:11px;font-weight:700}.flowl{fill:#0072BC}.fitl,.gradel{fill:#8a949b}.orcl{fill:#237059;font-style:italic}.livel{fill:#d4763a}.decl{fill:#26272a}
.lead{fill:#0072BC;opacity:.85}.lead.na{fill:#d9e4e8;opacity:1}.ax{font-size:10.5px;fill:#8a949b}
.lk{stroke:#b9c7cf;stroke-width:1.5}.lk.s{stroke:#0072BC;stroke-width:2}.nd{fill:#54576F}.nd.z{fill:#0072BC}.mini{font-size:10px;fill:#54576F}
.lvw{font-weight:700}svg.pipe a{cursor:pointer}svg.pipe a:hover .box,svg.pipe a:hover .xbox{filter:brightness(.98)}
"""
CSS = K.PIPE_CSS + K.scoped(FIG_CSS)       # the house rules as they are; this figure's own only inside svg.pipe


# ── values ──────────────────────────────────────────────────────────────────

def flat_counts(counts) -> dict:
    out = {}
    for k, v in (counts or {}).items():
        if isinstance(v, dict) and k in ("exclusions", "claims", "slots"):
            out.update(v)
        else:
            out[k] = v
    return out


def _whole(v) -> str:
    """A count as the figure prints it (1,172). Anything but a whole number is a build bug, so it
    raises rather than print a dict, a NaN or a fraction into a public figure; numpy ints are fine."""
    if isinstance(v, numbers.Integral) and not isinstance(v, bool):
        return f"{int(v):,}"
    if isinstance(v, numbers.Real) and not isinstance(v, bool) and math.isfinite(v) and float(v).is_integer():
        return f"{int(v):,}"
    raise TypeError(f"a count must be a whole number, not {v!r}")


def fmt_count(counts: dict, key: str) -> str:
    v = counts.get(key)
    return D if v is None else _whole(v)


def _num(x):
    """A finite score as a float; None, NaN and ±inf are 'not scored' (a NaN would print "nan")."""
    if isinstance(x, numbers.Real) and not isinstance(x, bool) and math.isfinite(x):
        return float(x)
    return None


def _val(entry):
    return _num(entry["v"] if isinstance(entry, dict) and "v" in entry else entry)


UNIT_FMTS = ("bss", "inches", "delta")
METRIC_WORD = {"bss": "BSS", "inches": "wet-day error", "delta": "change in Brier"}   # never BSS or skill for a change


def fmt_score(v, unit_fmt: str) -> str:
    """BSS to 2 dp; S1's wet-day error in inches (0.21″); S5's change in Brier signed to 3 dp (−0.035, +0.080).
    A real minus sign."""
    if unit_fmt not in UNIT_FMTS:
        raise KeyError(f"unknown unit_fmt {unit_fmt!r}; known: {UNIT_FMTS}")
    if v is None:
        return D
    s = f"{v:.2f}″" if unit_fmt == "inches" else (f"{v:+.3f}" if unit_fmt == "delta" else f"{v:.2f}")
    return s.replace("-", "−")


def _cell_words(entry, unit_fmt: str) -> str:
    """'BSS 0.54 [−0.07, 0.76], n = 854, 12 positives; too few to decide' for one cell."""
    v = _val(entry)
    if v is None:
        return "not scored yet"
    s = f"{METRIC_WORD[unit_fmt]} {fmt_score(v, unit_fmt)}"
    if isinstance(entry, dict):
        if _num(entry.get("lo")) is not None and _num(entry.get("hi")) is not None:
            s += f" [{fmt_score(_num(entry['lo']), unit_fmt)}, {fmt_score(_num(entry['hi']), unit_fmt)}]"
        if entry.get("n") is not None:
            s += f", n = {_whole(entry['n'])}"
        if entry.get("pos") is not None:
            s += f", {_whole(entry['pos'])} positives"
        if entry.get("low_power"):
            s += "; too few to decide"
    return s


def _score_tip(label: str, entry, unit_fmt: str) -> str:
    """A pill's tooltip: its number with CI, n and positives; where it comes from; any other numbers the build
    gave beside it ("also"); and the post-training number with its own CI ("post")."""
    if _val(entry) is None:
        return f"{label}: not scored yet"
    s = f"{label}: {_cell_words(entry, unit_fmt)}"
    if isinstance(entry, dict):
        if isinstance(entry.get("window"), str) and entry["window"]:
            s += f" · {entry['window']}"
        for a in entry.get("also") or ():
            if isinstance(a, dict) and _val(a) is not None:
                s += f" · {a.get('what', 'also')}: {_cell_words(a, unit_fmt)}"
        post = entry.get("post")
        if isinstance(post, dict):
            s += f" · {post.get('window') or 'post-training'}: {_cell_words(post, unit_fmt)}"
    return s


WEATHER_UNKNOWN = "the weather model"


def weather_model() -> str:
    """The weather model the live forecast reads ('icon_seamless' → 'ICON'), from METEO_PARAMS in
    live_dashboard's source as export_how_it_works does: importing the engine would load the whole
    model stack. Unreadable, it says "the weather model", never a name typed here that a model
    switch would make wrong; tests/test_stages_spec.py fails if the parse ever stops finding it."""
    try:
        src = (REPO / "features" / "forecast" / "live_dashboard.py").read_text()
    except OSError:
        return WEATHER_UNKNOWN
    m = re.search(r'^METEO_PARAMS\s*=\s*\{[^}]*?"models":\s*"([a-z0-9_]+)"', src, re.M | re.S)
    return m.group(1).split("_")[0].upper() if m else WEATHER_UNKNOWN


def _counts_of(scores, counts):
    """counts as given, else the exclusion and claim counts a whole scores.json carries (§1.5)."""
    if counts is None and isinstance(scores, dict) and "figure" in scores:
        return {k: scores[k] for k in ("exclusions", "claims", "slots") if k in scores}
    return counts


def _slots(counts) -> dict:
    return {"weather_model": weather_model(), **flat_counts(counts)}


WORD_SLOTS = ("weather_model",)       # slots filled with words; every other {slot} is a count


def _count_of(counts: dict, stage: str, rule: str):
    """A rule's count as a stage's chip reads it: the stage's own total when the counts carry one (``stages``,
    exclusions.figure_counts: X-LEDGER-SUSPECT is basin-days in S2's chip, zone-days in S3's), else the flat one."""
    own = (counts.get("stages") or {}).get(str(stage).lower())
    if isinstance(own, dict) and rule in own:
        return own[rule]
    return counts.get(rule)


def fmt_rule(counts: dict, stage: str, rule: str) -> str:
    v = _count_of(counts, stage, rule)
    return D if v is None else _whole(v)


def _fill(line: str, counts: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(counts[m.group(1)]) if m.group(1) in WORD_SLOTS else fmt_count(counts, m.group(1)), line)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# ── the S3 inset: the geography's basins → zones ────────────────────────────

def _short_zone(label: str) -> str:
    """'Baker & China Beach' → 'Baker & China': two places already say which beach (the inset is 10 px)."""
    return label[:-6] if " & " in label and label.endswith(" Beach") else label


def inset_data(geo: str) -> dict:
    """Basin names, zone labels and the unique (basin, zone) links, ordered so no two links cross."""
    from shared.zones import ZONES
    zones = [(k, _short_zone(z.label)) for k, z in ZONES.items()]
    zi = {k: i for i, (k, _) in enumerate(zones)}
    g = geography.get(geo)                 # KeyError on an unknown geography; there is no stand-in
    names = [b.name for b in g.basins]
    bi = {b.key: i for i, b in enumerate(g.basins)}
    pairs = [(bi[lk.basin], lk.zone) for lk in g.links]
    source = g.version
    links = sorted({(b, zi[z]) for b, z in pairs})
    fan = {b: sum(1 for bb, _ in links if bb == b) for b, _ in links}
    return dict(basins=[re.sub(r"\s*\(.*\)$", "", n) for n in names], basin_full=list(names), zones=[z for _, z in zones],
                links=[(b, z, fan[b] > 1) for b, z in links], source=source)


def inset_svg(x, y, ins: dict) -> str:
    """Basins (left) → zones (right); a basin that reaches two zones is the split, drawn in blue."""
    ys = lambda i: y + 6 + 13 * i  # noqa: E731
    xb, xz, o = x + 84, x + 128, []
    for b, z, split in ins["links"]:
        o.append(f'<line class="{"lk s" if split else "lk"}" x1="{xb}" y1="{ys(b)}" x2="{xz}" y2="{ys(z)}">'
                 f'<title>{K.esc(ins["basin_full"][b])} → {K.esc(ins["zones"][z])}{" (the split)" if split else ""}</title></line>')
    for i, t in enumerate(ins["basins"]):
        o.append(f'<circle class="nd" cx="{xb}" cy="{ys(i)}" r="3"/>' + K.text("mini", t, xb - 7, ys(i) + 3.5, 68, 10, PC["mini"], 8, "end"))
    for i, t in enumerate(ins["zones"]):
        o.append(f'<circle class="nd z" cx="{xz}" cy="{ys(i)}" r="3"/>' + K.text("mini", t, xz + 7, ys(i) + 3.5, SP.CW - 135 - 6, 10, PC["mini"], 8))
    return "".join(o)


def inset_text(ins: dict) -> str:
    by = {}
    for b, z, _ in ins["links"]:
        by.setdefault(b, []).append(ins["zones"][z])
    return "; ".join(f"{ins['basins'][b]} → {' and '.join(zs)}" for b, zs in sorted(by.items()))


# ── nodes ───────────────────────────────────────────────────────────────────

def _tile(x, y, n: dict, static: str) -> str:
    return K.logo_tile(x, y, n["logo"], static=static) if n.get("logo") else K.icon_tile(x, y, n["icon"])


def _node_tip(n: dict, counts: dict | None = None) -> str:
    s = SP.STAGE_OF_CODE.get(n["stage"])
    if n["kind"] in ("stage", "output") and s:
        return f"{n['id']} · truth: {s['truth']}; oracle: {s['oracle']}; chained: {s['chained']}"
    if n["kind"] == "exclusion" and s:
        rules = []
        for x in s["exclusions"]:
            e = SP.EXCLUSIONS[x]
            words = f"{x} {e['chip']}: {e['plain']}"
            v = _count_of(counts or {}, s["id"], x)
            if e.get("zero") and v is not None and _whole(v) == "0":
                words += f" (0: {e['zero']})"
            rules.append(words)
        return f"{n['id']} · " + " | ".join(rules) + (f" | {n['tip']}" if n.get("tip") else "")
    if n["kind"] == "claim":
        return f"{n['id']} · " + " | ".join(f"{c}: {SP.CLAIMS[c]['plain']}" for c in n["claims"])
    return f"{n['id']}" + (f" · {n['tip']}" if n.get("tip") else "")


def _level_words(line: str) -> str:
    """The level words in their page colours (shared/risk_levels.py), the numbers plain."""
    out = K.esc(line)
    for word, _, _, color in SP.RISK_LEVELS:
        out = re.sub(rf"\b{word}\b", f'<tspan class="lvw" fill="{color}">{word}</tspan>', out)
    return out


def _pill(cls: str, label: str, entry, unit_fmt: str, x, y, w) -> str:
    v = _val(entry)
    words = f"{label} {fmt_score(v, unit_fmt)}"
    op = ' opacity=".55"' if isinstance(entry, dict) and entry.get("low_power") else ""
    return (f'<g{op}><title>{K.esc(_score_tip(label, entry, unit_fmt))}</title>'
            f'<rect class="chip {cls}" x="{x:g}" y="{y:g}" width="{w:g}" height="20" rx="10"/>'
            + K.text(f"ct {cls}", words, x + w / 2, y + 14, w - 12, 11, PC["ct"], 8, "middle") + "</g>")


def render_node(n: dict, ins: dict, scores: dict, counts: dict, static: str) -> str:
    x, y, w, h = n["box"]
    k = n["kind"]
    o = [f'<a href="#{n["anchor"]}"><title>{K.esc(_node_tip(n, counts))}</title>'
         f'<rect class="{BOX_CLASS[k]}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{12 if k in ("exclusion", "claim") else 14}"/>']
    if k == "claim":
        o.append(K.text("col up", n["title"], x + 16, y + 19, w - 32, 11.5, PC["col up"], 9))
        pw = (w - 32 - 5 * 8) / 6
        for i, c in enumerate(n["claims"]):
            px, cl = x + 16 + i * (pw + 8), SP.CLAIMS[c]
            words = f"{cl['pill']} {fmt_count(counts, c)}" if cl["counted"] else cl["pill"]
            o.append(f'<g><title>{K.esc(c)}: {K.esc(cl["plain"])}</title><rect class="pill" x="{px:.1f}" y="{y + 28}" width="{pw:.1f}" height="24" rx="12"/>'
                     + K.text("pt", words, px + pw / 2, y + 44, pw - 14, 11.5, PC["pt"], 8.5, "middle") + "</g>")
    elif k == "exclusion":
        o.append(K.text("xh", n["title"], x + 14, y + 17, w - 28, 10.5, PC["xh"], 8))
        for i, line in enumerate(n["items"]):
            words = " · ".join(f"{t} {fmt_rule(counts, n['stage'], rid)}" if SP.EXCLUSIONS[rid]["counted"] else t for t, rid in line)
            o.append(K.text("xs", words, x + 14, y + SP.CHIP_TOP + i * SP.CHIP_LINE, w - 28, 11.5, PC["xs"], 8.5))
    elif k in ("stage", "output"):
        s = SP.STAGE_OF_CODE[n["stage"]]
        sc = scores.get(n["id"]) or {}
        wide = n.get("wide")
        o.append(_tile(x + 12, y + 12, n, static))
        title = f"{s['code']} · {n['title']}" if s["code"].startswith("S") else n["title"]
        o.append(K.text("t big", title, x + 62, y + 30, w - 62 - 8, 17, PC["t big"], 12))
        o.append(K.text("q", n["q"], x + 62, y + 47, w - 62 - 8, 12, PC["q"], 9))
        if n.get("inset"):
            o.append(inset_svg(x, y + 54, ins))
        both = bool(n.get("pills")) and bool(n.get("lead"))     # OUT: the lead strip, then the pills like every card
        for i, line in enumerate(n.get("lines", ())):
            o.append(K.text("s", _fill(line, counts), x + 14, y + (66 if wide or both else 72) + i * 16, w - 28, 13, PC["s"], 10))
        if n.get("pills"):
            cy = y + h - (26 if wide else 30)
            pw = 118 if wide else (w - 28 - 8) / 2
            px = x + w - 14 - 2 * pw - 8 if wide else x + 14
            if not both:                                          # OUT's pills read the strip's label above them
                o.append(K.text("m", n["metric"], x + 14, cy + 14 if wide else cy - 6, (px - x - 22) if wide else w - 28, 11, PC["m"], 8.5))
            (a, b), keys = n["pills"], ("oracle", "chained")
            o.append(_pill("or", a, sc.get(keys[0]), s["unit_fmt"], px, cy, pw))
            o.append(_pill("ch", b, sc.get(keys[1]), s["unit_fmt"], px + pw + 8, cy, pw))
        if n.get("lead"):
            # label, bar baseline, day words and the tallest bar: above the pills on OUT, at the card's foot alone
            label_y, base, day_y, tall = (y + 79, y + 104, y + 115, 20) if both else (y + h - 54, y + h - 22, y + h - 10, 26)
            o.append(K.text("m", n["metric"], x + 14, label_y, w - 28, 11, PC["m"], 8.5))
            lead = list(sc.get("lead") or [])[:6]
            lead += [None] * (6 - len(lead))
            for i, entry in enumerate(lead):
                v, bx = _val(entry), x + 14 + i * 33
                bh = 4 if v is None else max(2.0, tall * min(max(v, 0.0), 1.0))
                op = ' opacity=".55"' if isinstance(entry, dict) and entry.get("low_power") else ""
                o.append(f'<g{op}><title>{K.esc(_score_tip(SP.LEAD_TIPS[i], entry, "bss"))}</title><rect class="lead{" na" if v is None else ""}" '
                         f'x="{bx}" y="{base - bh:.1f}" width="24" height="{bh:.1f}" rx="3"/>'
                         f'<text class="ax" x="{bx + 12}" y="{day_y}" text-anchor="middle">{SP.LEAD_DAYS[i]}</text></g>')
    else:
        o.append(_tile(x + 10, y + (min(h, 56) - 40) / 2 + (6 if h > 60 else 0), n, static))
        lines = [_fill(line, counts) for line in n["lines"]]
        if k == "decision":
            o.append(K.text("t", n["title"], x + 60, y + 38, w - 70, 16, PC["t"], 12))
            for i, line in enumerate(lines):
                o.append(K.text("s", line, x + 14, y + 72 + i * 17, w - 28, 13, PC["s"], 10, markup=_level_words(line)))
        elif h > 60:
            o.append(K.text("t", n["title"], x + 60, y + 28, w - 70, 16, PC["t"], 12))
            for i, line in enumerate(lines):
                o.append(K.text("s", line, x + 60, y + 46 + i * 15, w - 70, 13, PC["s"], 10))
        else:
            dy = -7 if len(lines) > 1 else 0
            o.append(K.text("t", n["title"], x + 60, y + h / 2 - 3 + dy, w - 70, 16, PC["t"], 12))
            for i, line in enumerate(lines):
                o.append(K.text("s", line, x + 60, y + h / 2 + 14 + i * 15 + dy, w - 70, 13, PC["s"], 10))
    o.append("</a>")
    return "".join(o)


def render_edges(edges) -> str:
    o = []
    for e in edges:
        cls, mk = STYLE[e["style"]]
        d = "M" + " L".join(f"{a:g},{b:g}" for a, b in e["pts"])
        tip = e["id"] + (f" · {e['tip']}" if e.get("tip") else "")
        o.append(f'<path class="{cls}" d="{d}" marker-end="url(#{mk})"><title>{K.esc(tip)}</title></path>')
        if e["label"]:
            lx, ly, an = e["lab"]
            o.append(f'<text class="el {cls}l" x="{lx:g}" y="{ly:g}" text-anchor="{an}">{K.esc(e["label"])}</text>')
    return "".join(o)


def legend_svg() -> str:
    ya, yc = SP.LEGEND_Y[1], SP.LEGEND_Y[0]
    o = []
    for (style, words), x in zip(SP.LEGEND_ARROWS, SP.legend_layout()):
        cls, mk = STYLE[style]
        o.append(f'<path class="{cls}" d="M{x:g},{ya} H{x + 34:g}" marker-end="url(#{mk})"/><text class="lg" x="{x + 42:g}" y="{ya + 4}">{K.esc(words)}</text>')
    o.append(f'<rect class="chip or" x="44" y="{yc}" width="86" height="20" rx="10"/><text class="ct or" x="87" y="{yc + 14}" text-anchor="middle">oracle</text>'
             f'<rect class="chip ch" x="138" y="{yc}" width="86" height="20" rx="10"/><text class="ct ch" x="181" y="{yc + 14}" text-anchor="middle">chained</text>'
             f'<text class="lg" x="234" y="{yc + 14}">{K.esc(SP.LEGEND_CHAINED)}</text>'
             f'<rect class="xbox" x="860" y="{yc}" width="86" height="20" rx="10"/><text class="xh" x="903" y="{yc + 14}" text-anchor="middle">not scored</text>'
             f'<text class="lg" x="956" y="{yc + 14}">{K.esc(SP.LEGEND_NOT_SCORED)}</text>')
    return "".join(o)


def caption(scores=None) -> str:
    """The one line the page prints under the figure's title: which window each stage's pills show and where the
    post-training scores are, as stages_build wrote it into the figure ('' unscored)."""
    fig = (scores or {}).get("figure", scores or {})
    c = fig.get("caption") if isinstance(fig, dict) else None
    return c if isinstance(c, str) else ""


def svg(geo: str, scores=None, counts=None, static: str = K.STATIC) -> str:
    fig = (scores or {}).get("figure", scores or {})
    cnt = _slots(_counts_of(scores, counts))
    ns, es, ins = SP.nodes(geo), SP.edges(geo), inset_data(geo)
    vw, vh = SP.VIEW
    cap = caption(scores)
    s = [f'<svg class="pipe" viewBox="0 0 {vw} {vh}" role="img" data-geo="{geo}" data-spec="{SP.SPEC_VERSION}">'
         f'<title>{K.esc(SP.TITLE)}{" · " + K.esc(cap) if cap else ""} · geography {geo}, inset from {K.esc(ins["source"])}, '
         f'spec {SP.SPEC_VERSION}</title><defs>']
    for mid, col in MARKERS:
        s.append(f'<marker id="{mid}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="{col}"/></marker>')
    s.append("</defs>")
    for y, h, lab in SP.BANDS:
        s.append(f'<rect class="band" x="8" y="{y}" width="{vw - 16}" height="{h}" rx="18"/>'
                 f'<text class="rowl" transform="translate(26,{y + h / 2:g}) rotate(-90)" text-anchor="middle">{lab}</text>')
    for i, (a, b) in enumerate(SP.HEAD):
        s.append(f'<text class="col" x="{SP.COLX[i]}" y="18">{K.esc(a)}</text><text class="q" x="{SP.COLX[i]}" y="33">{K.esc(b)}</text>')
    s.append(render_edges(es))
    s.extend(render_node(n, ins, fig, cnt, static) for n in ns)
    s.append(legend_svg())
    s.append("</svg>")
    return "".join(s)


# ── the phone list: the same spec, stacked (the .pipe-m pattern) ────────────

def _li(n: dict, title: str, blocks, static: str) -> str:
    tile = f'<img src="{static}logos/{n["logo"]}" alt="">' if n.get("logo") else K.ic(n["icon"])
    words = "<br>".join(K.esc(b) for b in blocks)
    return f'<li id="m-{n["anchor"]}"><span class="lgi">{tile}</span><div><b>{K.esc(title)}</b><span>{words}</span></div></li>'


def _not_scored(stage: dict, chip, counts: dict) -> str:
    if chip:
        items = [it for line in chip["items"] for it in line]
    else:
        items = [(SP.EXCLUSIONS[x]["chip"], x) for x in stage["exclusions"] if SP.EXCLUSIONS[x]["kind"] == "exclude"]
    return ", ".join(f"{t} {fmt_rule(counts, stage['id'], rid)}" if SP.EXCLUSIONS[rid]["counted"] else t for t, rid in items)


def phone(geo: str, scores=None, counts=None, static: str = K.STATIC) -> str:
    fig = (scores or {}).get("figure", scores or {})
    cnt = _slots(_counts_of(scores, counts))
    N = {n["id"]: n for n in SP.nodes(geo)}
    m = ['<ol class="pipe-m">']
    for key in SP.PHONE_ORDER:
        if key == "levels":
            n = N["o.levels"]
            m.append(_li(n, n["title"], [f"{n['lines'][0]} · {n['lines'][1]}, on the whole % shown: {n['lines'][2]}."], static))
            continue
        if key == "claims":
            n = N["x.claim"]
            words = " · ".join(f"{SP.CLAIMS[c]['pill']} {fmt_count(cnt, c)}" if SP.CLAIMS[c]["counted"] else SP.CLAIMS[c]["pill"] for c in n["claims"])
            m.append(_li(dict(n, icon="circle-x"), _cap(n["title"]), [_cap(words) + "."], static))
            continue
        st = SP.STAGE[key]
        n, sc = N[st["node"]], fig.get(st["node"]) or {}
        title = f"{st['code']} · {n['title']}" if st["code"].startswith("S") else n["title"]
        q = n["q"]
        what = inset_text(inset_data(geo)) if n.get("inset") else ("; " if n.get("wide") else ", ").join(_fill(line, cnt) for line in n["lines"])
        parts = [f"{_cap(q)} {_cap(what)}." if q.endswith("?") else f"{_cap(q)}: {what}."]
        if key == "s1":
            r = N["l.rain"]
            parts.append(f"{r['title']}: {r['lines'][0]}.")
        if key == "s5":
            parts.append(f"From SFPUC's beach map ({N['l.map']['lines'][0]}) and new lab results ({N['l.lab']['lines'][0]}); the oracle is a perfect feed.")
        parts.append(f"Graded on {st['truth']}.")
        if n.get("pills"):
            (a, b), u = n["pills"], st["unit_fmt"]
            parts.append(f"{_cap(n.get('pill_metric') or n['metric'])}: {a} {fmt_score(_val(sc.get('oracle')), u)} · "
                         f"{b} {fmt_score(_val(sc.get('chained')), u)}.")
        if n.get("lead"):
            lead = list(sc.get("lead") or [])[:6]
            lead += [None] * (6 - len(lead))
            parts.append(f"{_cap(n['metric'])}: " + " · ".join(f"{SP.LEAD_DAYS[i]} {fmt_score(_val(e), 'bss')}" for i, e in enumerate(lead)) + ".")
        chip = N.get(st["chip"]) if st["chip"] else None
        m.append(_li(n, title, [" ".join(parts), f"Not scored: {_not_scored(st, chip, cnt)}."], static))
    m.append("</ol>")
    return "".join(m)


def render(geo: str = "sfpuc4_v1", scores=None, counts=None, static: str = K.STATIC) -> tuple:
    """(svg, phone_html) for one geography; the page includes svgkit.FIT_SCRIPT and the sprite."""
    return svg(geo, scores, counts, static), phone(geo, scores, counts, static)


# ── the geometry check (a port of the prototype's check(); run by tests/test_stages_spec.py) ──

def check(geo: str) -> list:
    """Every way the drawing can go wrong, as (problem, a, b); [] when clean."""
    ns, es = SP.nodes(geo), SP.edges(geo)
    B = {n["id"]: n["box"] for n in ns}
    vw, vh = SP.VIEW
    bad = []
    for i, r in B.items():
        if r[0] < 0 or r[1] < 0 or r[0] + r[2] > vw or r[1] + r[3] > vh:
            bad.append(("off-canvas", i, ""))
    for a, b in itertools.combinations(B, 2):
        if K.rects_overlap(B[a], B[b]):
            bad.append(("overlap", a, b))
    for e in es:
        for end, nid in ((e["pts"][0], e["frm"]), (e["pts"][-1], e["to"])):
            x, y, w, h = B[nid]
            inside = x - .5 <= end[0] <= x + w + .5 and y - .5 <= end[1] <= y + h + .5
            on_edge = min(abs(end[0] - x), abs(end[0] - x - w), abs(end[1] - y), abs(end[1] - y - h)) <= .5
            if not (inside and on_edge):
                bad.append(("loose-end", e["id"], nid))
    for e, f in itertools.combinations(es, 2):
        for s in zip(e["pts"], e["pts"][1:]):
            for t in zip(f["pts"], f["pts"][1:]):
                if K.segments_cross(*s, *t):
                    bad.append(("cross", e["id"], f["id"]))
    for e in es:
        for s in zip(e["pts"], e["pts"][1:]):
            for nid, r in B.items():
                if nid not in (e["frm"], e["to"]) and K.segment_hits_rect(*s, r):
                    bad.append(("through", e["id"], nid))
    labels = {e["id"]: K.label_rect(e["label"], *e["lab"]) for e in es if e["label"]}
    for eid, r in labels.items():
        for nid, b in B.items():
            if K.rects_overlap(r, b):
                bad.append(("label-in-box", eid, nid))
        for f in es:
            if f["id"] != eid and any(K.segment_hits_rect(*s, r, shrink=0) for s in zip(f["pts"], f["pts"][1:])):
                bad.append(("label-on-edge", eid, f["id"]))
    for a, b in itertools.combinations(labels, 2):
        if K.rects_overlap(labels[a], labels[b]):
            bad.append(("label-on-label", a, b))
    for n in ns:                                   # a chip's lines fit its box, even at the smallest type with 6-digit counts
        if n["kind"] == "exclusion":
            x, y, w, h = n["box"]
            if SP.CHIP_TOP + SP.CHIP_LINE * (len(n["items"]) - 1) + 4 > h:
                bad.append(("chip-overflow", n["id"], f"{len(n['items'])} lines in {h} px"))
            for line in n["items"]:
                words = " · ".join(f"{t} 00,000" if SP.EXCLUSIONS[rid]["counted"] else t for t, rid in line)
                if len(words) * PC["xs"] * 8.5 > w - 28:
                    bad.append(("chip-text", n["id"], words))
    ins = inset_data(geo)
    for (b1, z1, _), (b2, z2, _) in itertools.combinations(ins["links"], 2):
        if (b1 - b2) * (z1 - z2) < 0:
            bad.append(("inset-cross", f"{b1}>{z1}", f"{b2}>{z2}"))
    xs = SP.legend_layout()
    ends = [x + 42 + len(w) * 5.9 for (_, w), x in zip(SP.LEGEND_ARROWS, xs)]
    if ends[-1] > vw - 16 or any(ends[i] >= xs[i + 1] for i in range(len(xs) - 1)):
        bad.append(("legend", "arrows", f"ends at {ends[-1]:.0f}"))
    return bad


# ── standalone preview ──────────────────────────────────────────────────────

def page(geo: str, scores=None, counts=None, static: str = K.STATIC) -> str:
    fig, ph = render(geo, scores, counts, static)
    cap = caption(scores)
    cap = f'<p class="figcap">{K.esc(cap)}</p>' if cap else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Stages figure · {geo}</title><style>body{{margin:0;background:#fff;font-family:Roboto,Arial,sans-serif;color:#26272a}}'
            f'.wrap{{max-width:1440px;margin:0 auto;padding:24px}}h3{{font-size:15px;margin:18px 0 8px}}'
            f'.figcap{{font-size:13px;color:#54576F;margin:-2px 0 10px}}'
            f'.phone{{max-width:380px;border:1px solid #d9e4e8;border-radius:24px;padding:12px 16px}}.phone .pipe-m{{display:block}}{CSS}</style></head>'
            f'<body>{K.sprite()}<div class="wrap"><h3>Figure 1 · {geo} · spec {SP.SPEC_VERSION}</h3>'
            f'{cap}<div class="panel">{fig}{ph}</div>'
            f'<h3>Phone list (what a ≤ 700 px screen shows instead)</h3><div class="phone">{ph}</div></div>{K.FIT_SCRIPT}</body></html>')


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0] if argv else ".")
    static = (REPO / "app" / "static").as_uri() + "/"
    for geo, name in (("sfpuc4_v1", "stages_fig_sfpuc4.html"), ("geo_v1", "stages_fig_geo_v1.html")):
        probs = check(geo)
        (out / name).write_text(page(geo, static=static))
        print(f"wrote {out / name} · geometry {'clean' if not probs else probs}")


if __name__ == "__main__":
    main()
