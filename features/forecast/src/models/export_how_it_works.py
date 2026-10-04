#!/usr/bin/env python3
"""How the forecast works, drawn from the artifacts, in the five stages (STAGES_DESIGN.md A8):

    reports/2026-09_forecast_how_it_works.html     S1 rain → S2 overflow → S3 which beaches → S4 how long →
                                                   S5 live corrections → OUT, and how it was trained

It shows what each stage does; how good each one is lives in the stages report
(export_stages_report.py, reports/2026-10_forecast_stages.html), which this page points to first.
The live forecast reads as its lineup, each part by its fixed name (shared/lineup.py); a set goes
by its S2 · S3 · S4, the live forecast's wearing the LIVE badge; stored set names stay identifiers,
in small print.

    reports/2026-09_forecast_how_it_is_graded.html the rulers, the windows, the results at each risk level, the replays, the weather input

is archived (2026-10-03, export_reports_index.ARCHIVED): kept as it was graded before the five-stage
rebuild and not regenerated. ``report_graded`` stays so the page can be rebuilt on purpose
(``--graded``), and the rebuild puts its archived banner straight back. It reports counts (caught,
missed, false alarms) and the standard threshold scores (POD, FAR, POFD, CSI, from
verify.contingency_table) at the public risk-level edges; nothing weighs a miss against a false
alarm and nothing ranks the sets by cost (STAGES_DESIGN.md A3).

Less text, more pictures (Chase, 2026-09-30). Every number is read from the served
bundle (served.json, the pickles, stage2.json, scorecard.json.gz), the rules
module, the candidates' manifests and the sources registry — nothing is typed by hand
that a file already knows. Re-run after a promotion, a rescore or a rule change:

    venv/bin/python features/forecast/src/models/export_how_it_works.py            # how it works
    venv/bin/python features/forecast/src/models/export_how_it_works.py --graded   # also the archived graded page, re-stamped
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import pickle
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for p in (str(REPO), str(FORECAST), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import leaderboard  # noqa: E402,F401  (weights pipelines reference leaderboard.add_hinges)
from src.models import candidates, live_rules as LR, posting_label as PL, stage2 as S2, verify as V  # noqa: E402
from src.models.groups import BASIN_KEYS, GROUPS_BY_BASIN, SITE_GROUPS, ZONE_GROUPS  # noqa: E402
from src.models.impact import compose, impact_fraction, smooth_table  # noqa: E402
from src.models.rain_features import DAILY_FEATURES, INTENSITY_FEATURES, add_daily_features, hourly_intensity  # noqa: E402
from src.models.scorecard import basin_metrics, zone_confusion, zone_confusion_combined, zone_confusion_posted, zone_fp_tail  # noqa: E402
import export_reports_index as RI  # noqa: E402  (the reports index: the archived banner, the sets' lineup words)
from shared import lineup as LU  # noqa: E402  (a stage part's plain words, A8)
from shared.sources import registry  # noqa: E402
from shared.risk_levels import LEVELS, edges, level_of, whole_percent  # noqa: E402  (the public levels, Chase, 2026-10-01)
from shared.zones import ZONES  # noqa: E402

_main = sys.modules.get("__main__")
if _main is not None and not hasattr(_main, "add_hinges"):
    _main.add_hinges = leaderboard.add_hinges

MODEL_DIR = FORECAST / "data" / "models"
RAW_DIR = FORECAST / "data" / "raw"
OUT_WORKS = REPO / "reports" / "2026-09_forecast_how_it_works.html"
OUT_GRADED = REPO / "reports" / "2026-09_forecast_how_it_is_graded.html"     # archived 2026-10-03: written only with --graded
STAGES_URL, INDEX_URL = "/reports/" + RI.STAGES, RI.URL
ICONS = REPO / "app" / "templates" / "_icons.html"

BASINS = list(BASIN_KEYS.values())                   # westside, north_shore, central, southeast
BASIN_NAME = {v: k for k, v in BASIN_KEYS.items()}
ZONE_ORDER = ["ocean", "baker_china", "north", "east"]
ZONE_LABEL = {k: z.label for k, z in (ZONES.items() if isinstance(ZONES, dict) else ((z.key, z) for z in ZONES))}
ZONE_ICON = {"ocean": "waves", "baker_china": "umbrella", "north": "anchor", "east": "building-2"}
GROUP_ORDER = ["Ocean Beach", "Baker-China", "Crissy Field", "Aquatic Park", "Mission Creek", "Southeast"]
LEVEL_EDGES = edges()                                 # where Medium, High and Extreme begin: 0.205, 0.505, 0.805 (shared/risk_levels.py)
BRAND, INK, MUTED, LINE, SAND = "#0072BC", "#26272a", "#54576F", "#d9e4e8", "#E3EBF2"
PALETTE = ["#0072BC", "#d4763a", "#237059", "#b5310a", "#7b5ea7", "#b97e00"]

FEATURE_MEANING = {
    "precip_avg": "today's rain total", "rain_2d_cum": "last 2 days", "rain_3d_cum": "last 3 days", "rain_5d_cum": "last 5 days",
    "rain_7d_cum": "last 7 days", "rain_14d_cum": "last 14 days", "rain_30d_cum": "last 30 days",
    "rain_lag1d": "yesterday's total", "rain_lag2d": "2 days ago", "rain_lag3d": "3 days ago", "rain_lag5d": "5 days ago", "rain_lag7d": "7 days ago",
    "antecedent_moisture": "how wet the ground already is (14 days, 3-day half-life)", "wet_prior_3d": "was it wet in the 3 days before today (yes/no)",
    "peak_3d": "wettest single day of the last 3", "dry_spell_days": "days since it last rained",
    "rain_max1h": "wettest hour today", "rain_max3h": "wettest 3 hours", "rain_max6h": "wettest 6 hours",
}


# ── helpers ─────────────────────────────────────────────────────────────────

def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ic(name: str, cls: str = "ic") -> str:
    return f'<svg class="{cls}"><use href="#i-{name}"/></svg>'


def pct(v, nd=0) -> str:
    return "—" if v is None or v != v else f"{100 * v:.{nd}f}%"   # v != v: a NaN score (zero denominator)


def score2(v) -> str:
    """A 0–1 score (CSI) to two decimals; '—' when its denominator was zero."""
    return "—" if v is None or v != v else f"{v:.2f}"


def level_name(t: float) -> str:
    """A risk-level edge the way the Model check names it: 'Medium and up (21%+)', 'Extreme (81%+)'."""
    lv = level_of(t)
    return f"{lv.label}{'' if lv is LEVELS[-1] else ' and up'} ({whole_percent(t)}%+)"


def fmt_month(day: str) -> str:
    return dt.date.fromisoformat(day[:10]).strftime("%b %Y")


def plain(col: str, cid: str) -> str:
    """A stage part's plain words (shared/lineup.py), or its id while it has none."""
    try:
        return LU.words(col, cid)
    except KeyError:
        return cid


def set_words(name: str) -> str:
    """A stored set's lineup words, S2 · S3 · S4 (the reports index reads them the same way), or its name
    when the set is not on disk."""
    try:
        return RI.model_set(name)["words"]
    except StopIteration:
        return name


def sprite() -> str:
    s = ICONS.read_text()
    return re.sub(r"\{#-.*?-#\}", "", s, flags=re.S)


def load_served() -> dict:
    sv = candidates.served_info()
    models = {}
    for key in BASINS:
        with open(MODEL_DIR / f"{key}_model.pkl", "rb") as f:
            models[key] = pickle.load(f)
    vols = {}
    for key in BASINS:
        p = MODEL_DIR / f"{key}_volume.pkl"
        if p.exists():
            with open(p, "rb") as f:
                vols[key] = pickle.load(f)
    stage2 = json.load(open(MODEL_DIR / "stage2.json")) if (MODEL_DIR / "stage2.json").exists() else None
    table = smooth_table(stage2["impact_table"] if stage2 and stage2.get("impact_table") else json.load(open(MODEL_DIR / "impact_table.json")))
    sc = json.load(gzip.open(MODEL_DIR / "scorecard.json.gz", "rt"))
    lb = json.load(open(MODEL_DIR / "leaderboard.json")) if (MODEL_DIR / "leaderboard.json").exists() else {}
    return {"info": sv, "models": models, "volumes": vols, "stage2": stage2, "table": table, "sc": sc, "leaderboard": lb}


def predict(md: dict, feats: pd.DataFrame) -> np.ndarray:
    raw = md["model"].predict_proba(feats[md["features"]])[:, 1]
    factor = np.maximum(0.0, 1.0 - feats["rain_3d_cum"].values * 2.0)
    return np.clip(raw - md.get("calibration_offset", 0.0) * factor, 0.0, 1.0)


def intensity_ratios() -> dict:
    """Typical peak-hour shares of a day's total on wet days, from the ERA5 hourly record."""
    h = pd.read_csv(RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"])
    h["date"] = h["timestamp"].dt.normalize()
    daily = h.groupby("date")["precip_inches"].sum()
    inten = hourly_intensity(h).set_index("date")
    wet = daily[daily >= 0.25]
    return {k: float((inten.loc[wet.index, k] / wet).median()) for k in INTENSITY_FEATURES}


def sweep_features(rain_today: np.ndarray, ratios: dict, prior: dict | None = None) -> pd.DataFrame:
    """Features for a series of 'today' totals with 31 dry days before (or `prior`
    = {days_ago: inches} laid in), intensity at the typical shares of the total."""
    rows = []
    for r in rain_today:
        s = np.zeros(32)
        for k, v in (prior or {}).items():
            s[-1 - k] = v
        s[-1] = r
        f = add_daily_features(pd.DataFrame({"precip_inches": s})).iloc[-1]
        row = {k: float(f[k]) for k in DAILY_FEATURES}
        for k in INTENSITY_FEATURES:
            row[k] = r * ratios[k]
        rows.append(row)
    return pd.DataFrame(rows)


# ── SVG ─────────────────────────────────────────────────────────────────────

def svg_lines(series: list, xlabels: list, width=320, height=170, ymax=1.0, yfmt=lambda v: f"{int(v*100)}%", title="", xlab="") -> str:
    """series = [{"label", "color", "values", "dash"?}] over the same x positions."""
    L, R, T, B = 40, 30, 22, 30
    W, H = width - L - R, height - T - B
    n = len(xlabels)
    x = lambda i: L + (W * i / max(n - 1, 1))  # noqa: E731
    y = lambda v: T + H - H * (v / ymax)  # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img"><title>{esc(title)}</title>']
    for k in range(5):
        v = ymax * k / 4
        out.append(f'<line x1="{L}" y1="{y(v):.1f}" x2="{L + W}" y2="{y(v):.1f}" class="grid"/><text x="{L - 6}" y="{y(v) + 4:.1f}" class="ax" text-anchor="end">{esc(yfmt(v))}</text>')
    for i, lab in enumerate(xlabels):
        out.append(f'<text x="{x(i):.1f}" y="{T + H + 16}" class="ax" text-anchor="middle">{esc(lab)}</text>')
    if xlab:
        out.append(f'<text x="{L + W / 2:.1f}" y="{height - 2}" class="ax" text-anchor="middle">{esc(xlab)}</text>')
    for s in series:
        pts = " ".join(f"{x(i):.1f},{y(min(max(v, 0), ymax)):.1f}" for i, v in enumerate(s["values"]) if v is not None)
        dash = ' stroke-dasharray="6 4"' if s.get("dash") else ""
        out.append(f'<polyline points="{pts}" fill="none" stroke="{s["color"]}" stroke-width="2.5"{dash}/>')
        for i, v in enumerate(s["values"]):
            if v is not None and s.get("dots", True):
                out.append(f'<circle cx="{x(i):.1f}" cy="{y(min(max(v, 0), ymax)):.1f}" r="3" fill="{s["color"]}"/>')
    if title:
        out.append(f'<text x="{L}" y="14" class="ttl">{esc(title)}</text>')
    out.append("</svg>")
    legend = "".join(f'<span class="lg"><i style="background:{s["color"]}{";opacity:.55" if s.get("dash") else ""}"></i>{esc(s["label"])}</span>' for s in series if s.get("label"))
    return f'<div class="chartbox">{"".join(out)}<div class="legend">{legend}</div></div>'


def svg_hbars(rows: list, width=420, vmax=None, fmt=lambda v: f"{v:.0f}", title="", unit="", label_w=150) -> str:
    """rows = [{"label", "value", "color", "note"?}] horizontal bars, label left, value right."""
    vmax = vmax or max((r["value"] for r in rows), default=1) or 1
    rowh, L, R = 26, label_w, 60
    W = width - L - R
    height = 8 + rowh * len(rows) + (18 if title else 0)
    top = 18 if title else 4
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img"><title>{esc(title)}</title>']
    if title:
        out.append(f'<text x="0" y="12" class="ttl">{esc(title)}</text>')
    for i, r in enumerate(rows):
        yy = top + i * rowh
        w = W * min(r["value"], vmax) / vmax
        out.append(f'<text x="{L - 8}" y="{yy + 17}" class="bl" text-anchor="end">{esc(r["label"])}</text>'
                   f'<rect x="{L}" y="{yy + 4}" width="{W:.1f}" height="18" rx="6" fill="#f3f6f9"/>'
                   f'<rect x="{L}" y="{yy + 4}" width="{max(w, 0):.1f}" height="18" rx="6" fill="{r.get("color", BRAND)}"/>'
                   f'<text x="{L + W + 6}" y="{yy + 17}" class="bv">{esc(fmt(r["value"]))}{esc(unit)}</text>')
    out.append("</svg>")
    return f'<div class="chartbox">{"".join(out)}</div>'


def logo_tile(x, y, logo, size=40):
    return (f'<rect class="tile logo" x="{x}" y="{y}" width="{size}" height="{size}" rx="11"/>'
            f'<image href="/static/{logo}" x="{x + 6}" y="{y + 6}" width="{size - 12}" height="{size - 12}" preserveAspectRatio="xMidYMid meet"/>')


def icon_tile(x, y, name, size=40):
    return (f'<rect class="tile" x="{x}" y="{y}" width="{size}" height="{size}" rx="11"/>'
            f'<use class="icn" href="#i-{name}" x="{x + 8}" y="{y + 8}" width="{size - 16}" height="{size - 16}"/>')


FIT_SCRIPT = """<script>
/* fit every box label to its box in this browser's font: start at full size, shrink only as far as it must */
(function () {
  function fit() {
    document.querySelectorAll('svg text[data-maxw]').forEach(function (t) {
      var max = +t.dataset.maxw, full = +t.dataset.fs || parseFloat(getComputedStyle(t).fontSize);
      t.style.fontSize = full + 'px';
      var len = t.getComputedTextLength();
      if (len > max) t.style.fontSize = (full * max / len * 0.98).toFixed(2) + 'px';
    });
  }
  fit();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
})();
</script>"""


def _fit(text: str, avail: float, size: float, per_char: float, floor: float) -> float:
    """A font size at which `text` fits in `avail` px (rough width = chars × per_char × size), never below `floor`."""
    need = len(text) * per_char * size
    return size if need <= avail else max(floor, size * avail / need)


def node(x, y, w, h, icon, title, sub, hub=False):
    tile = logo_tile(x + 10, y + (h - 40) / 2, icon[5:]) if icon.startswith("logo:") else icon_tile(x + 10, y + (h - 40) / 2, icon)
    avail = w - 60 - 10
    ts, ss = _fit(title, avail, 16, 0.58, 12), _fit(sub, avail, 13, 0.52, 10.5)
    return (f'<rect class="box{" hub" if hub else ""}" x="{x}" y="{y}" width="{w}" height="{h}" rx="14"/>{tile}'
            f'<text class="t" data-maxw="{avail:.0f}" data-fs="16" style="font-size:{ts:.1f}px" x="{x + 60}" y="{y + h / 2 - 3}">{esc(title)}</text>'
            f'<text class="s" data-maxw="{avail:.0f}" data-fs="13" style="font-size:{ss:.1f}px" x="{x + 60}" y="{y + h / 2 + 14}">{esc(sub)}</text>')


def pipeline_svg(sv: dict, wx_label: str, n_events: int = 0, n_sample_days: int = 0) -> str:
    """One source column on the left in three groups (the record, fitted on
    once; S1's rain, live; observations, live, for S5), the model row in the
    middle (S2, then S3–S4 in one box), the outputs (OUT) on the right. Dashed =
    used once to fit; solid = flows every 30 minutes. No two arrows cross."""
    a = ['<svg class="pipe" viewBox="0 0 1290 620" role="img"><title>How rain becomes a beach percentage</title>'
         '<defs><marker id="pa" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="#0072BC"/></marker>'
         '<marker id="pg" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="#8a949b"/></marker></defs>']
    a.append('<text class="col" x="20" y="40">THE RECORD · used once, to fit the model</text>')
    a.append('<text class="col" x="20" y="222">S1 · RAIN, LIVE · every 30 minutes</text>')
    a.append('<text class="col" x="320" y="240">THE MODEL · fixed since training</text>')
    a.append('<text class="col" x="20" y="442">OBSERVED, LIVE · read by S5</text>')
    a.append('<text class="col" x="1075" y="350">OUT · WHAT GOES OUT</text>')
    # the record (fitted on, once)
    a.append(node(20, 52, 250, 56, "logo:logos/sf-city-seal.png", "City lab results, past", f"{n_sample_days:,} sampled days"))
    a.append(node(20, 116, 250, 56, "logo:logos/water-boards.png", "Filed overflows (CIWQS)", f"{n_events:,} events: the labels"))
    # rain, live
    a.append(node(20, 234, 250, 56, "logo:logos/noaa.svg", "Two NOAA gauges", "Downtown · Oceanside, daily"))
    a.append(node(20, 298, 250, 56, "logo:logos/noaa.svg", "SFO airport gauge", "NWS · today's hours so far"))
    a.append(node(20, 362, 250, 56, "cloud-rain", f"{wx_label} weather model", "Open-Meteo · the hours ahead"))
    # observations, live
    a.append(node(20, 452, 250, 56, "logo:logos/sfpuc.png", "SFPUC beach map", "flags · postings, every minute"))
    a.append(node(20, 516, 250, 56, "logo:logos/sf-city-seal.png", "City lab results, new", "this week's samples (DataSF)"))
    # the model row
    a.append(node(320, 298, 205, 56, "gauge", "19 rain inputs a day", "totals · lags · peaks", hub=True))
    a.append(node(555, 298, 205, 56, "chart", "S2 · 4 basins", "chance of an overflow", hub=True))
    a.append(node(790, 298, 255, 56, "waves", "S3–S4 · 6 beach groups", "which beaches, and how long", hub=True))
    a.append(node(790, 452, 255, 56, "satellite-dish", "S5 · live corrections", "observations override", hub=True))
    # outputs
    a.append(node(1075, 362, 205, 56, "map-pin", "4 zones × 6 days", "percent, every 30 min"))
    a.append(node(1075, 426, 205, 56, "bell", "Risk levels", f"{LEVELS[0].label} to {LEVELS[-1].label}"))   # the banner's word is a fixed level, not the served line (Chase, 2026-10-01)
    a.append(node(1075, 490, 205, 56, "logo:logos/supabase.svg", "History tables", "every forecast, kept"))

    def arrow(d, label=None, lx=None, ly=None, anchor="middle", cls="flow", marker="pa"):
        a.append(f'<path class="{cls}" d="{d}" marker-end="url(#{marker})"/>')
        if label:
            a.append(f'<text class="{"al" if cls == "flow" else "fl"}" x="{lx}" y="{ly}" text-anchor="{anchor}">{esc(label)}</text>')

    def dot(x, y):
        a.append(f'<circle class="dot" cx="{x}" cy="{y}" r="3.5"/>')

    # fitted on: the past samples teach S4 how long beaches stay dirty; the filed overflows teach S2 which rain overflows
    arrow("M270,80 H917 V298", "teaches S4 how long a beach stays dirty", 590, 72, cls="fit", marker="pg")
    arrow("M270,144 H657 V298", "teaches S2 which rain overflows", 460, 136, cls="fit", marker="pg")
    # rain: three sources become one hourly series, then the day's numbers
    a.append('<path class="flow" d="M270,262 H295 V390 H270"/>'); dot(295, 326)
    arrow("M295,326 H320"); a.append('<text class="al" x="302" y="412" text-anchor="start">one hourly rain series</text>')
    arrow("M525,326 H555")
    arrow("M760,326 H790")
    arrow("M917,354 V452", "% per beach group", 909, 408, "end")
    # observations into the live corrections
    a.append('<path class="flow" d="M270,544 H295 V480 H270"/>'); dot(295, 480)
    arrow("M295,480 H790", "flags · postings · sample results", 540, 471)
    # live corrections to each output
    a.append('<path class="flow" d="M1045,480 H1060 V390"/><path class="flow" d="M1060,480 V518"/>'); dot(1060, 480)
    arrow("M1060,390 H1075"); arrow("M1060,454 H1075"); arrow("M1060,518 H1075")
    # legend
    a.append('<path class="flow" d="M320,600 H360"/><text class="lg" x="368" y="604">flows every 30 minutes</text>'
             '<path class="fit" d="M560,600 H600"/><text class="lg" x="608" y="604">used once, to fit the model; the weights have not changed since</text>')
    a.append("</svg>")
    # the same story stacked, for phones
    steps = [
        ("logo:logos/water-boards.png", "Fitted once", f"{n_events:,} filed overflows taught S2 which rain overflows; {n_sample_days:,} sampled days taught S4 how long a beach stays dirty."),
        ("logo:logos/noaa.svg", "S1 · rain in, live", f"Two NOAA gauges for every past day, the SFO gauge for today's hours, the {wx_label} model for the hours ahead: one hourly series."),
        ("gauge", "19 rain inputs a day", "Totals, lags, peaks, dryness."),
        ("chart", "S2 · 4 basins", "The chance the sewers overflow today."),
        ("waves", "S3–S4 · 6 beach groups", "Which beaches, and how long: a percentage from the last eight days."),
        ("satellite-dish", "S5 · live corrections", "The beach map's flags and postings and this week's samples override the model where they have something to say."),
        ("map-pin", "OUT", f"Four zones, six days, every 30 minutes; four fixed risk levels, {LEVELS[0].label} to {LEVELS[-1].label}; every forecast kept."),
    ]
    m = ['<ol class="pipe-m">']
    for icon, title, text in steps:
        tile = f'<img src="/static/{icon[5:]}" alt="">' if icon.startswith("logo:") else ic(icon)
        m.append(f'<li><span class="lgi">{tile}</span><div><b>{esc(title)}</b><span>{esc(text)}</span></div></li>')
    m.append("</ol>")
    return "".join(a) + "".join(m)


def weights_chart(md: dict, top: int = 8, width=320) -> str:
    """The served weights model's terms as shares of its total absolute weight
    (standardised inputs, so the coefficients are comparable), signed: a term
    that raises the overflow odds is drawn warm, one that lowers them cool."""
    pipe = md["model"]
    lr = pipe.named_steps.get("lr") if hasattr(pipe, "named_steps") else None
    if lr is None or not hasattr(lr, "coef_"):
        return ""
    names = list(leaderboard.FEATS) + [f"{f}>{k:g}" for f, ks in leaderboard.HINGES.items() for k in ks]
    coef = lr.coef_[0]
    if len(coef) != len(names):
        return ""
    total = float(np.abs(coef).sum()) or 1.0
    order = np.argsort(-np.abs(coef))
    rows = []
    for i in order[:top]:
        n = names[i]
        if ">" in n:
            f, k = n.split(">")
            label = f'{FEATURE_MEANING.get(f, f)} above {k}"'
        else:
            label = FEATURE_MEANING.get(n, n)
        rows.append({"label": label[:34], "value": abs(coef[i]) / total, "color": ("#b5310a" if coef[i] > 0 else "#237059")})
    shown = sum(r["value"] for r in rows)
    return svg_hbars(rows, width=width, vmax=max(r["value"] for r in rows), fmt=lambda v: f"{100*v:.0f}%",
                     title=f"the weights · top {top} of {len(names)} terms = {100*shown:.0f}% of the total", label_w=190)



def math_section(models: dict, ratios: dict) -> str:
    """The weights model written out, the fitted numbers per basin, and one day worked through."""
    names = list(leaderboard.FEATS) + [f"{f}>{k:g}" for f, ks in leaderboard.HINGES.items() for k in ks]

    def term_label(n):
        if ">" in n:
            f, k = n.split(">")
            return f'{FEATURE_MEANING.get(f, f)} above {k}"'
        return FEATURE_MEANING.get(n, n)

    tables = []
    for b in BASINS:
        pipe = models[b]["model"]
        lr, sc = pipe.named_steps["lr"], pipe.named_steps["scale"]
        coef, mu, sd = lr.coef_[0], sc.mean_, sc.scale_
        total = float(np.abs(coef).sum()) or 1.0
        order = np.argsort(-np.abs(coef))
        rows = "".join(
            f'<tr><td class="mono">{esc(names[i])}</td><td>{esc(term_label(names[i]))}</td><td class="num">{coef[i]:+.3f}</td>'
            f'<td class="num">{np.exp(coef[i]):.2f}×</td><td class="num">{100 * abs(coef[i]) / total:.1f}%</td><td class="num mute">{mu[i]:.3f}</td><td class="num mute">{sd[i]:.3f}</td></tr>'
            for i in order)
        tables.append(f'<details class="more"><summary>{esc(BASIN_NAME[b])}: all {len(names)} weights, intercept β₀ = {lr.intercept_[0]:+.3f} (C = {models[b].get("C", "—")})</summary>'
                      f'<table><tr><th>term</th><th>plain meaning</th><th class="num">weight β per SD</th><th class="num">odds × per +1 SD</th><th class="num">share</th><th class="num">mean μ</th><th class="num">SD σ</th></tr>{rows}</table></details>')

    # one day worked through, Westside: an inch today after a dry month
    b = BASINS[0]
    pipe = models[b]["model"]
    lr, sc = pipe.named_steps["lr"], pipe.named_steps["scale"]
    feats = sweep_features(np.array([1.0]), ratios)
    t = leaderboard.add_hinges(feats[leaderboard.FEATS])[0]
    z = (t - sc.mean_) / sc.scale_
    contrib = lr.coef_[0] * z
    logit = float(lr.intercept_[0] + contrib.sum())
    prob = 1 / (1 + np.exp(-logit))
    order = np.argsort(-np.abs(contrib))
    ex_rows = "".join(f'<tr><td class="mono">{esc(names[i])}</td><td class="num">{t[i]:.3f}</td><td class="num">{z[i]:+.2f}</td><td class="num">{lr.coef_[0][i]:+.3f}</td><td class="num"><b>{contrib[i]:+.3f}</b></td></tr>' for i in order[:8])
    rest = float(contrib.sum() - contrib[order[:8]].sum())
    example = (f'<table><tr><th>term</th><th class="num">value</th><th class="num">standardised z</th><th class="num">weight β</th><th class="num">β · z</th></tr>{ex_rows}'
               f'<tr><td colspan="4">the other {len(names) - 8} terms together</td><td class="num">{rest:+.3f}</td></tr>'
               f'<tr class="total"><td colspan="4">intercept β₀ {lr.intercept_[0]:+.3f} + all terms = log-odds</td><td class="num">{logit:+.3f}</td></tr>'
               f'<tr class="total"><td colspan="4">P(overflow) = 1 / (1 + e<sup>−{logit:.3f}</sup>)</td><td class="num">{100 * prob:.0f}%</td></tr></table>')

    return f"""<details class="more math"><summary>The math behind the weights</summary>
<div class="card wide">
<p><b>The model.</b> For a basin and a day, take the 19 inputs x<sub>1</sub> … x<sub>19</sub> (inches, days, or a yes/no). Add {len(names) - 19} <em>bends</em>: for an input f and a knot k, h<sub>f,k</sub> = max(0, x<sub>f</sub> − k), which is zero until the input passes the knot and then rises with it. The knots k are chosen by hand, the same for all four basins; the fit learns only the weights. That is how "the first quarter inch barely matters, the next inch matters a lot" becomes a straight-line model. Together these are the {len(names)} terms t<sub>1</sub> … t<sub>{len(names)}</sub>.</p>
<p><b>Standardise.</b> Each term is centred and scaled by its training mean and standard deviation: z<sub>j</sub> = (t<sub>j</sub> − μ<sub>j</sub>) / σ<sub>j</sub>. So every weight below is "per one standard deviation of that term", and weights are comparable across terms.</p>
<p><b>Combine.</b> The log-odds of an overflow is a weighted sum: &nbsp;<span class="eq">log(p / (1 − p)) = β<sub>0</sub> + Σ<sub>j</sub> β<sub>j</sub> z<sub>j</sub></span>, &nbsp;and the probability is &nbsp;<span class="eq">p = 1 / (1 + e<sup>−(β<sub>0</sub> + Σ β<sub>j</sub> z<sub>j</sub>)</sup>)</span>. A weight of +0.5 multiplies the odds by e<sup>0.5</sup> ≈ 1.65 for each standard deviation of its term. The "share" in the table above is |β<sub>j</sub>| / Σ|β|.</p>
<p><b>Fit.</b> The weights minimise the log-loss over the training days plus a penalty (1 / 2C) Σ β<sub>j</sub>², the L2 ridge: a smaller C shrinks all weights toward zero and spreads credit across correlated terms (today's total and the last two days move together, so they share weight). C is chosen per basin by leave-one-season-out cross-validation on the seasons before the holdout. Serving adds a calibration offset that fades to zero by half an inch of three-day rain; for this family the offset is 0, so p is used as fitted.</p>
<p><b>One day, worked through.</b> {esc(BASIN_NAME[b])}, one inch today after a dry month, peak hours at their typical shares of the day:</p>{example}
{"".join(tables)}
</div></details>"""


def simpler_section() -> str:
    """Could it be simpler? The refits from sparse_logit_eval.py, if they exist."""
    path = MODEL_DIR / "sparse_logit_eval.json"
    if not path.exists():
        return ""
    d = json.load(open(path))
    label = {"full38": f"all {d['n_terms_full']} terms (served design)", "raw19": "the 19 inputs, no bends", "top12": "12 terms the served model weighs most", "top8": "8 terms", "top5": "5 terms", "top3": "3 terms",
             "tiny4": "4 hand-picked: today, today above ½\", last 2 days, wettest hour", "l1": "an L1 fit that picks its own terms"}
    f3 = lambda s, k, nd=3: (f"{s[k]:.{nd}f}" if s and k in s else "—")  # noqa: E731
    blocks = []
    for key, b in d["basins"].items():
        rows = []
        for r in b["rows"]:
            cls = " class='best'" if r["name"] == "full38" else ""
            terms = ""
            if r["name"] in ("l1", "top5", "top3"):
                terms = f'<div class="fine">{esc(", ".join(r["terms"]))}</div>'
            rows.append(f'<tr{cls}><td>{esc(label.get(r["name"], r["name"]))}{terms}</td><td class="num">{r["n_terms"]}</td><td class="num">{f3(r["cv"], "pr_auc")}</td><td class="num">{f3(r["holdout"], "pr_auc")}</td><td class="num">{f3(r["holdout"], "brier", 4)}</td><td class="num">{f3(r["post"], "pr_auc")}</td><td class="num">{f3(r["post"], "brier", 4)}</td></tr>')
        blocks.append(f'<h4>{esc(b["name"])} · {b["n_holdout_events"]} overflow days in the holdout, {b["n_post_events"]} since training</h4>'
                      f'<table><tr><th>design</th><th class="num">terms</th><th class="num">season CV PR-AUC</th><th class="num">holdout PR-AUC</th><th class="num">holdout Brier</th><th class="num">since-training PR-AUC</th><th class="num">since Brier</th></tr>{"".join(rows)}</table>')
    return f"""<h3>Could it be simpler?</h3>
<div class="card wide"><p class="fine">Chase, 2026-09-30: "simpler is king". The same design refit with fewer terms, on the same days and labels (<code>src/models/sparse_logit_eval.py</code>, {esc(d["generated"])}). PR-AUC ranks overflow days above quiet ones (1.0 perfect); Brier is the squared error of the probability (lower is better). The holdout exam fits on the seasons before {esc(fmt_month(d["holdout_start"]))} and scores {esc(fmt_month(d["holdout_start"]))} → {esc(fmt_month(d["trained_through"]))}; the since-training exam fits through {esc(fmt_month(d["trained_through"]))} and scores the days after. The "top K" rows rank terms by the served model, which itself saw the holdout, so read them as optimistic; the L1 and hand-picked rows carry no such advantage. Shaded: the served design.</p>{"".join(blocks)}
<p class="fine"><b>Reading.</b> Eight terms match or beat all {d["n_terms_full"]} in every basin, on the holdout and since training, and the season cross-validation is higher for the smaller designs in three basins of four. Even three to five terms hold within a few points. The 19 inputs are cheap to compute, so the cost of the extra terms is not speed; it is that the model is harder to explain and that correlated terms share credit in ways that shift between fits. A candidate set with about five terms per basin, chosen by cross-validation on the seasons before the holdout, would be the honest test; it would go through the Model check and the five stages report like every candidate, and serving already handles the family.</p></div>"""


# ── report A: how it works ──────────────────────────────────────────────────

def report_works(S: dict, reg: dict, wx_model: str) -> str:
    sv, models, table, stage2, sc = S["info"], S["models"], S["table"], S["stage2"], S["sc"]
    # the live forecast by its lineup (A8): each stage's part by its fixed name; the stored name stays an identifier
    parts = {"geography": "geo_v1", "s1": wx_model, **LU.geo_v1_parts(sv["stage1"], sv["stage2"]), "s5": LR.VERSION}
    W = {c: plain(c, v) for c, v in parts.items()}
    wx_label = W["s1"]
    retired = sv.get("replaced")
    retired_words = set_words(retired) if retired else None
    n_candidates = len(candidates.list_candidates())
    ratios = intensity_ratios()
    gen = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    ev = pd.read_csv(FORECAST / "data" / "csd" / "sf_csd_events.csv", parse_dates=["event_date"])
    n_events, n_days, ev_span = len(ev), ev["event_date"].nunique(), (ev["event_date"].min().date(), ev["event_date"].max().date())
    tiles = reg.get("tiles", {})

    def src_card(logo, name, role, entries, note=""):
        rows = "".join(f'<div class="srow"><span>{esc(e["name"])}</span><span class="mute">{esc(e.get("first") or "")} → {esc(e.get("last") or "")}</span></div>' for e in entries)
        tile = f'<img class="lg" src="/static/{logo[5:]}" alt="">' if logo.startswith("logo:") else f'<span class="lgi">{ic(logo)}</span>'
        return f'<div class="card src">{tile}<div><b>{esc(name)}</b><div class="mute">{esc(role)}</div>{rows}{f"<div class=fine>{esc(note)}</div>" if note else ""}</div></div>'

    weather = [e for e in tiles.get("weather", []) if any(k in e["name"] for k in ("ACIS", "ICON", "KSFO"))]
    rain_in = "".join([
        src_card("logo:logos/noaa.svg", "Rain that fell", "two NOAA gauges (daily) and the SFO airport gauge (hourly): the truth S2 was trained on",
                 [e for e in weather if "ACIS" in e["name"] or "KSFO" in e["name"]]),
        src_card("cloud-rain", "Rain to come", f"the {wx_label} weather model through Open-Meteo, hourly for six days: the stand-in for the gauges on forecast days",
                 [e for e in weather if "ICON" in e["name"]], "An input, not part of the model: the fitted weights never see which weather model produced the inches, so swapping it needs no retraining. ICON is the closest single model to the gauges (S1 in the stages report)."),
    ])
    also_in = "".join([
        src_card("logo:logos/sfpuc.png", "SFPUC beach map", "overflow flags and beach postings, read every minute by the watcher: what S5 corrects with", tiles.get("sfpuc", [])),
        src_card("logo:logos/sf-city-seal.png", "City lab results", "bacteria samples: past ones built S4's lingering table and grade it; new ones feed S5", tiles.get("datasf", [])),
        src_card("logo:logos/water-boards.png", "State records", "SFPUC's filed overflow reports (CIWQS) train S2, give S3 its shares and grade everything; BeachWatch postings are a second ruler", tiles.get("state", [])),
    ])

    # S2: the four basins on one chart, dry and wet antecedents; facts; the weights as percentages in the feature table
    sweep = np.arange(0.0, 3.01, 0.1)
    xl = [f'{v:.1f}"' if i % 5 == 0 else "" for i, v in enumerate(sweep)]
    dry = sweep_features(sweep, ratios)
    wet = sweep_features(sweep, ratios, prior={1: 1.0, 2: 1.0, 3: 0.5})
    curves_dry = svg_lines([{"label": BASIN_NAME[b], "color": PALETTE[i], "values": list(predict(models[b], dry)), "dots": False} for i, b in enumerate(BASINS)],
                           xl, ymax=1.0, title="after a dry month", xlab="today's rain total", width=440, height=230)
    curves_wet = svg_lines([{"label": BASIN_NAME[b], "color": PALETTE[i], "values": list(predict(models[b], wet)), "dots": False} for i, b in enumerate(BASINS)],
                           xl, ymax=1.0, title='after 2.5" over the three days before', xlab="today's rain total", width=440, height=230)
    fact_rows = []
    for key in BASINS:
        md, pb = models[key], (sv.get("per_basin") or {}).get(key, {})
        ho = pb.get("holdout", {})
        src = md.get("rain_source", "avg")
        fact_rows.append(f'<tr><td><b>{esc(BASIN_NAME[key])}</b></td><td>{esc("Downtown + Oceanside mean" if src == "avg" else src)}</td><td class="num">{pb.get("n_events", "—")}</td><td class="num">{md.get("C", "—")}</td><td class="num">{ho.get("pr_auc", 0):.2f}</td><td class="num">{ho.get("roc_auc", 0):.3f}</td></tr>')
    facts = f'<table><tr><th>basin</th><th>rain gauge it reads</th><th class="num">overflow days in training</th><th class="num">C</th><th class="num">holdout PR-AUC</th><th class="num">holdout ROC-AUC</th></tr>{"".join(fact_rows)}</table>'
    knots = (S["leaderboard"].get("hinges") or {})
    names = list(leaderboard.FEATS) + [f"{f}>{k:g}" for f, ks in leaderboard.HINGES.items() for k in ks]
    shares, signs = {}, {}
    for b in BASINS:
        coef = models[b]["model"].named_steps["lr"].coef_[0]
        total = float(np.abs(coef).sum()) or 1.0
        shares[b] = {f: sum(abs(coef[i]) for i, n in enumerate(names) if n == f or n.startswith(f + ">")) / total for f in leaderboard.FEATS}
        signs[b] = {f: sum(coef[i] for i, n in enumerate(names) if n == f or n.startswith(f + ">")) for f in leaderboard.FEATS}
    n_terms = len(names)

    def cell(b, f):
        v, sgn = shares[b][f], signs[b][f]
        alpha = min(0.85, v * 4)
        direction = "▲" if sgn > 0.05 else ("▼" if sgn < -0.05 else "")
        return f'<td class="num heat" style="background:rgba(0,114,188,{alpha:.2f});color:{"#fff" if alpha > 0.45 else "#26272a"}">{100 * v:.0f}% <span class="dir">{direction}</span></td>'

    feat_rows = "".join(f'<tr><td class="mono">{esc(f)}</td><td>{esc(FEATURE_MEANING.get(f, ""))}</td><td class="mute">{esc(", ".join(f"{k:g}" for k in knots.get(f, [])) or "—")}</td>' + "".join(cell(b, f) for b in BASINS) + "</tr>"
                        for f in sorted(leaderboard.FEATS, key=lambda f: -sum(shares[b][f] for b in BASINS)))
    ratio_txt = ", ".join(f"{k.replace('rain_max', '')}: {v:.0%}" for k, v in ratios.items())
    weights_table = (f'<table class="wt"><tr><th>input</th><th>plain meaning</th><th>bends at (inches)</th>{"".join(f"<th class=num>{esc(BASIN_NAME[b])}</th>" for b in BASINS)}</tr>{feat_rows}</table>'
                     f'<p class="fine">Each cell: the share of that basin&#39;s total weight carried by the input and its bends together. {n_terms} weighted terms per basin (the 19 inputs plus {n_terms - 19} bends) and one intercept. '
                     f'▲ raises the overflow odds on balance, ▼ lowers them. Peak-hour inputs for the curves above sit at their typical share of a wet day&#39;s total ({ratio_txt}).</p>')
    # S3: the share of each basin's overflows that reach a beach group, by size (Outfall split; none = No split)
    shares, outs = (stage2 or {}).get("shares") or {}, (stage2 or {}).get("group_outfalls") or {}
    split_rows = [{"label": f"{g} · {size}", "value": shares[g][size]["p"], "color": color}
                  for g in GROUP_ORDER if g in shares for size, color in (("large", "#b5310a"), ("small", "#d4763a"))
                  if (shares[g].get(size) or {}).get("p") is not None]
    split_chart = svg_hbars(split_rows, width=420, vmax=1.0, fmt=lambda v: f"{100 * v:.0f}%", label_w=150,
                            title="share of the basin's overflow days that reach the group, by size") if split_rows else ""
    split_table = "".join(f'<tr><td><b>{esc(g)}</b></td><td>{esc(SITE_GROUPS[g][0])}</td><td class="mono">{esc(" ".join(outs.get(g, [])))}</td>'
                          f'<td class="num">{(shares.get(g) or {}).get("large", {}).get("n", "—")} / {(shares.get(g) or {}).get("small", {}).get("n", "—")}</td></tr>'
                          for g in GROUP_ORDER if g in shares)
    # S4: decay curves per group; then S3 and S4 together, worked through
    k_labels = ["0", "1", "2", "3", "4–5", "6–7"]
    k_days = [0, 1, 2, 3, 4, 6]
    decay_cards = []
    for g in GROUP_ORDER:
        med = (table.get(g) or {}).get("median_event_volume_mg", 1.0) or 1.0
        small = [impact_fraction(table, g, k, 0.01) for k in k_days]
        large = [impact_fraction(table, g, k, 1e6) for k in k_days]
        base = ((table.get(g) or {}).get("buckets", {}).get("baseline_no_recent_discharge") or {}).get("p_elevated")
        n = (table.get(g) or {}).get("n_sample_days")
        decay_cards.append(f'''<div class="card group"><div class="bh"><b>{esc(g)}</b><span class="mute">{esc(SITE_GROUPS[g][0])} basin · {len(SITE_GROUPS[g][1])} station{"s" if len(SITE_GROUPS[g][1]) != 1 else ""}</span></div>
            {svg_lines([{"label": f"large overflow (≥ {med:g} MG)", "color": "#b5310a", "values": large}, {"label": "small overflow", "color": "#d4763a", "values": small, "dash": True}], k_labels, ymax=1.0, title="chance the beach is still over standard", xlab="days after the overflow", width=300, height=160)}
            <div class="fine">Curves from the city&#39;s bacteria samples on {n} sampled days after overflows, the dry-weather background of {pct(base)} removed.</div></div>''')
    # worked example: a 90% day two days ago in every basin, dry otherwise, median-ish volume
    ex_dates = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(8)]
    probs = [{b: 0.02 for b in BASINS} for _ in ex_dates]
    probs[5] = {b: 0.9 for b in BASINS}
    vols = [{b: (table.get(GROUPS_BY_BASIN[b][0]) or {}).get("median_event_volume_mg", 5.0) for b in BASINS} for _ in ex_dates]
    split = S2.make_split(stage2) if stage2 else None
    risk_by_day = []
    for i in range(8):
        _, groups = compose(table, GROUPS_BY_BASIN, probs, vols, i, ex_dates, {}, split=split)
        risk_by_day.append(groups)
    ex_series = [{"label": g, "color": PALETTE[j % len(PALETTE)], "values": [risk_by_day[i].get(g) for i in range(8)]} for j, g in enumerate(GROUP_ORDER)]
    ex_chart = svg_lines([{"label": "P(overflow) that day", "color": "#8a949b", "values": [probs[i]["westside"] for i in range(8)], "dash": True}] + ex_series,
                         [f"day {i - 5:+d}" if i != 5 else "overflow day" for i in range(8)], ymax=1.0, width=720, height=230, title="one 90% overflow day, dry around it: what each beach group shows")

    # live corrections
    R = LR.RULES
    dg = R["downgrade"]["recall_by_quiet_days"]
    dg_bars = svg_hbars([{"label": f"{'morning after' if k == 0 else f'{k} quiet day' + ('s' if k > 1 else '')}{' +' if k == 2 else ''}", "value": dg["southeast"][k], "color": BRAND} for k in range(3)], vmax=1.0, fmt=lambda v: f"{v:.2f}", title="how sure the feed would have flagged a real overflow (bay basins)")
    sr = LR.SAMPLE_RATES
    zl = lambda z: ZONE_LABEL.get(z, z)  # noqa: E731
    floor_bars = svg_hbars([{"label": zl(z), "value": sr["floor_elevated_tail"][z], "color": (BRAND if R["samples"]["floor_elevated_tail"][z] > 0 else "#b8c4cc")} for z in ZONE_ORDER], vmax=1.0, fmt=lambda v: f"{v:.2f}", title="elevated again next time · floor applied only where ≥ 0.5")
    cap_bars = svg_hbars([{"label": zl(z), "value": sr["cap_clean_tail"][z], "color": BRAND} for z in ZONE_ORDER], vmax=1.0, fmt=lambda v: f"{v:.2f}", title="elevated after a clean bottle · the cap on the tail")
    s_rules = R["samples"]
    lag_svg = f'''<svg class="lag" viewBox="0 0 720 120" role="img"><title>When a sample result reaches the forecast</title>
      {"".join(f'<line x1="{60 + i * 100}" y1="20" x2="{60 + i * 100}" y2="70" class="grid"/><text x="{60 + i * 100}" y="88" class="ax" text-anchor="middle">{"sample day" if i == 0 else f"+{i}"}</text>' for i in range(7))}
      <rect x="{60 + 1 * 100 - 14}" y="30" width="{2 * 100 + 28}" height="30" rx="8" fill="#e0f0ea"/><text x="{60 + 2 * 100}" y="50" class="bl" text-anchor="middle">the rules act here (day +{s_rules["known_lag_days"]} to +{s_rules["horizon_days"]})</text>
      <circle cx="{60 + 1.5 * 100}" cy="20" r="6" fill="{BRAND}"/><text x="{60 + 1.5 * 100}" y="12" class="bv" text-anchor="middle">SFPUC map shows it</text>
      <circle cx="{60 + 5 * 100}" cy="20" r="6" fill="#b5310a"/><text x="{60 + 5 * 100}" y="12" class="bv" text-anchor="middle">DataSF publishes it</text>
      <text x="360" y="112" class="ax" text-anchor="middle">measured Sep 2026: the map 1–2 days after sampling, DataSF about 5 · live_v1 read DataSF alone and arrived too late</text></svg>'''
    rule_cards = f'''
      <div class="card rule"><span class="lgi">{ic("octagon-alert")}</span><div><b>An overflow flag appears</b><p>That basin's day becomes 100% and the beach tail follows. If the day before had rain of {R["cso"]["anchor_prev_day"]["min_rain_in"]:g}" or a model chance of {pct(R["cso"]["anchor_prev_day"]["min_p"])}, it is anchored too, because the feed's flag usually lands a day late.</p></div></div>
      <div class="card rule"><span class="lgi">{ic("hourglass")}</span><div><b>The flag stays up</b><p>Up for {R["cso"]["large_when_flag_persists_days"]} days means a large overflow: the day moves to the large-event curve, and the beaches hold there while the flag is up (at most {R["cso"]["hold_max_days"]} days).</p></div></div>
      <div class="card rule"><span class="lgi">{ic("trending-down")}</span><div><b>No flag when one was expected</b><p>A bay-basin day the model put at {pct(R["downgrade"]["min_p"])} or more that the feed never flagged is downgraded, more with each quiet day. Never on the Westside: the feed's record there is unmeasured. Only while the watcher is live.</p>{dg_bars}</div></div>
      <div class="card rule"><span class="lgi">{ic("microscope")}</span><div><b>A sample comes back over standard</b><p>Within a week of an overflow it floors the beach at how often the next bottle is also bad, but only where that is 50% or more: today the East, at {pct(sr["floor_elevated_tail"]["east"])}. In dry weather it changes nothing.</p>{floor_bars}</div></div>
      <div class="card rule"><span class="lgi">{ic("circle-check")}</span><div><b>A sample comes back clean</b><p>Within a week of an overflow it caps the lingering part of the risk at how often a clean bottle is followed by a bad one. Never zero.</p>{cap_bars}</div></div>'''

    # outputs
    level_rows = "".join(f'<div class="lvl"><b style="color:{lv.color}">{lv.label}</b><span>{lv.lo}–{lv.hi}%</span></div>' for lv in reversed(LEVELS))
    zone_rows = "".join(f'<tr><td>{ic(ZONE_ICON[z])} <b>{esc(ZONE_LABEL[z])}</b></td><td>{esc(", ".join(ZONE_GROUPS[z]))}</td><td class="num">{sum(len(SITE_GROUPS[g][1]) for g in ZONE_GROUPS[z])}</td></tr>' for z in ZONE_ORDER)

    # training timeline
    def tl_x(day: str, x0=60, x1=980, d0=dt.date(2013, 1, 1), d1=dt.date(2026, 12, 31)):
        d = dt.date.fromisoformat(day[:10])
        return x0 + (x1 - x0) * ((d - d0).days / (d1 - d0).days)
    tt, hs = sv.get("trained_through") or sc.get("trained_through"), sv.get("holdout_start") or sc.get("holdout_start")
    tw = json.load(open(MODEL_DIR / "eval_report.json")).get("train_window", ["2016-03-01", tt]) if (MODEL_DIR / "eval_report.json").exists() else ["2016-03-01", tt]
    years = "".join(f'<line x1="{tl_x(f"{y}-01-01"):.0f}" y1="24" x2="{tl_x(f"{y}-01-01"):.0f}" y2="146" class="grid"/><text x="{tl_x(f"{y}-01-01"):.0f}" y="16" class="ax" text-anchor="middle">{y}</text>' for y in range(2013, 2027))
    def bar(y, a, b, color, label, dark=False):
        x0, x1 = tl_x(a), min(tl_x(b), 980)
        return (f'<rect x="{x0:.0f}" y="{y}" width="{max(x1 - x0, 2):.0f}" height="16" rx="5" fill="{color}"/>'
                f'<text x="{x0 + 6:.0f}" y="{y + 12}" class="bl" fill="{"#26272a" if dark else "#fff"}">{esc(label)}</text>')
    timeline = f'''<svg class="tl" viewBox="0 0 1000 176" role="img"><title>The record and the windows</title>{years}
      {bar(30, "2013-01-01", "2016-10-15", "#b8c4cc", "bay overflows, legacy list", dark=True)}{bar(30, "2016-10-16", str(ev_span[1]), "#54576F", f"SFPUC's filed overflows (CIWQS): {n_events} events on {n_days} days")}
      {bar(52, "2016-03-19", "2017-01-10", "#7b5ea7", "feed archive")}{bar(52, "2020-07-27", "2026-08-31", "#7b5ea7", "DataSF samples")}
      {bar(74, "2013-01-01", "2020-07-26", "#c4b5e6", "city lab export 2000–2020 (not yet read by the forecast)", dark=True)}
      {bar(96, "2016-01-01", "2026-09-30", "#85BFDF", "rain: two NOAA gauges + ERA5 hourly", dark=True)}
      {bar(122, tw[0], hs, BRAND, "S2 fitted here")}{bar(122, hs, tt, "#237059", "holdout")}{bar(122, str(dt.date.fromisoformat(tt) + dt.timedelta(days=1)), "2026-09-30", "#d4763a", "since training")}
      <text x="60" y="162" class="ax">S2 was fitted on {esc(fmt_month(tw[0]))} → {esc(fmt_month(hs))}; its settings were chosen on the holdout, {esc(fmt_month(hs))} → {esc(fmt_month(tt))}, days it never saw while being chosen; every day after {esc(fmt_month(tt))} is a genuine forward test.</text></svg>'''
    lb = S["leaderboard"]
    n_rows = sum(len(b.get("rows", [])) for b in (lb.get("basins") or {}).values())
    promoted = (sv.get("promoted_at") or "")[:10]
    cap = lambda t: t[:1].upper() + t[1:]  # noqa: E731
    lineup_html = " · ".join(f"<b>{code}</b> {esc(W[c])}" for code, c in (("S1", "s1"), ("S2", "s2"), ("S3", "s3"), ("S4", "s4"), ("S5", "s5")))
    stored_s34 = f'<span class="mute">(stored as stage 2 {esc(sv["stage2"])})</span>'     # the old two-stage pipeline's name for S3 + S4
    others = n_candidates - (1 if retired in {m["name"] for m in candidates.list_candidates()} else 0)
    was = f'{esc(retired)} ({esc(retired_words)})' if retired else "—"
    if shares:
        s3_lead = (f"{esc(cap(W['s3']))} {stored_s34} gives each beach group its basin&#39;s chance times the share of the basin&#39;s overflow days, "
                   "by size, on which the group&#39;s own outfalls spilled: Ocean Beach and Aquatic Park do not see every overflow in their basin. "
                   "Where a group&#39;s outfalls are the whole basin, the share is 1. The day&#39;s size comes from S2&#39;s volume heads; between small and large the share is blended.")
        s3_body = (f'<div class="grid2"><div class="card">{split_chart}</div>'
                   f'<div class="card"><b>Which outfalls post each group</b><div class="tw"><table><tr><th>beach group</th><th>basin</th><th>outfalls</th><th class="num">overflow days, large / small</th></tr>{split_table}</table></div>'
                   '<p class="fine">From the city&#39;s filed overflow reports (CIWQS), which name the outfalls that spilled; the outfall registry says which beaches each one posts.</p></div></div>')
    else:
        s3_lead, s3_body = f"{esc(cap(W['s3']))} {stored_s34}: every beach group takes its basin&#39;s whole chance.", ""

    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>How the forecast works</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/brand.css"><style>{CSS}</style></head><body>{sprite()}
<div class="wrap">
<header class="rh"><img src="/static/brand/bwtf_144x144.png" alt="" class="mark"><div><h1>How the forecast works</h1>
<p class="sub">What each of the five stages does, from the rain to the percentage on the forecast page. The live forecast, on {esc(W["geography"])}: {lineup_html}.</p>
<p class="pointer">How well each stage works is its own report: <a href="{STAGES_URL}"><b>The forecast, stage by stage</b></a>, every stage scored on days its fit never saw. Start there; this page shows what each stage does.</p>
<div class="meta"><span>generated {esc(gen)}</span><span>stored as {esc(sv["name"])}, promoted {esc(promoted)}</span><span>trained through {esc(tt)}</span><span>holdout from {esc(hs)}</span><span><a href="{STAGES_URL}">how good each stage is →</a></span><span><a href="{INDEX_URL}">every report →</a></span></div></div></header>
<nav><a href="#picture">One picture</a><a href="#s1">S1 Rain</a><a href="#s2">S2 Overflow</a><a href="#s3">S3 Which beaches</a><a href="#s4">S4 How long</a><a href="#s5">S5 Live corrections</a><a href="#out">OUT</a><a href="#training">Training</a><a href="#more">Deeper</a></nav>

<section id="picture"><h2>Rain in, beach risk out</h2>
<div class="panel">{pipeline_svg(sv, wx_label, n_events, sum((table.get(g) or {}).get("n_sample_days", 0) for g in GROUP_ORDER))}</div>
<ul class="caps"><li><b>Do the gauges shape the live numbers?</b> Yes, first of all. Every complete past day is re-based onto the two NOAA gauges before anything runs; the SFO airport gauge fills today's hours so far and the peak-hour numbers of recent days; the weather model only fills the hours that have not happened yet.</li>
<li><b>Does a new weather model mean retraining?</b> No. S2 reads inches. Swapping ICON for another model changes S1, never the fitted weights.</li>
<li><b>What if nothing is observed?</b> Then S5 changes nothing and the page shows the model alone, to the digit.</li>
<li><b>Where do the city&#39;s bacteria samples come in?</b> Twice. Past samples taught S4 how long a beach stays dirty after an overflow (the dashed arrow). New samples, as the beach map and DataSF publish them, correct the live numbers through S5 (the solid one).</li></ul>
<p class="lead">Rain that fell and rain to come (S1) become 19 numbers a day. Four basin models turn them into the chance the sewers overflow that day (S2). Each of six beach groups takes the share of its basin's overflows that reach it (S3) and how long they linger there (S4), so overflow chances become days of beach risk. The city's live beach map and lab results then move the numbers where they have something to say (S5). The page shows four zones, today and five days ahead (OUT).</p></section>

<section id="s1"><h2>S1 · How much rain?</h2>
<p class="lead">Rain that fell comes from two NOAA gauges, Downtown and Oceanside; today&#39;s hours so far from the SFO airport gauge; the hours ahead from the {esc(wx_label)} weather model. On every complete past day the gauges replace everything else. S1 hands S2 inches, so another weather model changes the rain, never S2&#39;s weights.</p>
<div class="grid2">{rain_in}</div>
<h3>What else comes in, and which stage reads it</h3>
<div class="grid3">{also_in}</div></section>

<section id="s2"><h2>S2 · Will the sewers overflow?</h2>
<p class="lead"><b>Inputs, weights, output.</b> The 19 numbers are the <b>inputs</b>: what goes in each day, computed from the rain. The <b>weights</b> are what the fit learned, 38 per basin, fixed since training: one for each input and one for each bend. Where the bends sit (the knots) is set by hand and is the same in every basin; the fit learns how much each bend matters. Every station in a basin shares that basin&#39;s S2; the beaches differ in S3 and S4. The <b>output</b> is one number per basin per day: the chance the sewers overflow. One model per combined-sewer basin, trained on the days SFPUC filed an overflow. Each is a weights model: the 19 inputs, plus bends at a few rain amounts so the response can steepen, standardised, then an L2 logistic fit. Every basin reads the gauge that predicted it best on the holdout. The two charts are the served models answering "how likely is an overflow today if this much falls?", once after a dry month and once after a wet start to the week.</p>
<div class="grid2"><div class="card">{curves_dry}</div><div class="card">{curves_wet}</div></div>
<div class="card wide">{facts}</div>
<h3>What each basin weighs</h3>
<div class="card wide">{weights_table}</div>
{math_section(models, ratios)}
{simpler_section()}</section>

<section id="s3"><h2>S3 · Which beaches?</h2>
<p class="lead">An overflow reaches the beaches its outfalls post, and not every outfall spills when its basin does. {s3_lead}</p>
{s3_body}</section>

<section id="s4"><h2>S4 · For how long?</h2>
<p class="lead">An overflow fouls a beach for days. {esc(cap(W["s4"]))} is built from the city&#39;s bacteria samples: for each beach group, the lab results after past overflows give the chance the water is still over standard k days later, large and small overflows apart, with the dry-weather background removed. The percentage on the page is the combined risk from the last eight days.</p>
<div class="grid3">{"".join(decay_cards)}</div>
<div class="card wide"><b>S3 and S4 together, worked through</b><p class="fine">Every basin at a 2% chance, except one day at 90% (a median-sized overflow). The day itself is a stay-out day; the days after decay along each group&#39;s curve (S4), scaled by its share (S3).</p>{ex_chart}</div></section>

<section id="s5"><h2>S5 · Live corrections: {esc(W["s5"])}</h2>
<p class="lead">Once the forecast is running, what SFPUC's beach map and the city's lab results say can override the model, and only then: with nothing observed the output is the model alone, to the digit. Every change is recorded and shown on the page as a badge.</p>
<div class="grid2 rules">{rule_cards}</div>
<div class="card wide"><b>Where the sample results come from, and when</b>{lag_svg}</div></section>

<section id="out"><h2>OUT · What goes out</h2>
<div class="grid3">
<div class="card"><b>Four zones, six days</b><table class="plain"><tr><th>zone</th><th>beach groups</th><th class="num">stations</th></tr>{zone_rows}</table><p class="fine">A zone shows the worst of its groups; a basin the worst of its groups; the city the worst basin.</p></div>
<div class="card"><b>One number, four words</b><div class="levels">{level_rows}</div><p class="fine">Fixed levels on the whole percent shown, the same on every page.</p></div>
<div class="card"><b>Every 30 minutes</b><p class="fine">A scheduler recomputes the forecast at :05 and :35, stores it, and the page always serves the stored copy. Each day's first and last forecast go to the history tables with the model's name, so a model swap never breaks the record. The Model check grades every stored day.</p><p class="fine">Since {esc(promoted)} the stamp reads {esc(sv["name"])}, the live forecast; before that {was}.</p></div>
</div></section>

<section id="training"><h2>How it was trained</h2>
<div class="panel">{timeline}</div>
<div class="grid3">
<div class="card"><b>The labels</b><p class="fine">SFPUC's filed overflow reports, one row per outfall per event: {n_events:,} events on {n_days} days, {esc(str(ev_span[0]))} → {esc(str(ev_span[1]))}, pulled from CIWQS each quarter. Bay-side basins also learn from the 2016-17 feed archive's flags; the Westside does not, because those flags lag the rain.</p></div>
<div class="card"><b>The choice</b><p class="fine">A leaderboard fit {n_rows} combinations of model family, rain gauge and regularisation per basin, judged by leave-one-season-out cross-validation before the holdout and then by holdout ranking. The weights model won every basin on the holdout and became S2 ({esc(W["s2"])}); {esc(W["s3"])} (S3) and {esc(W["s4"])} (S4) were added; the set, {esc(set_words(sv["name"]))}, was promoted on {esc(promoted)}.</p></div>
<div class="card"><b>What is kept</b><p class="fine">The retired set, {esc(retired_words or "—")} <span class="mute">(stored as {esc(retired or "—")})</span>, and {others} other candidate sets stay on disk with their own scorecards, so the Model check can grade them on the same days; each has its own S2 page, listed with <a href="{INDEX_URL}">every report</a>. Nothing is retrained on the fly: the pickles change only at a promotion.</p></div>
</div></section>

<section id="more"><h2>Deeper</h2><p class="lead">
<a href="{STAGES_URL}">The forecast, stage by stage</a> (how good each stage is) · <a href="/reports/{RI.explorer_file(sv["name"])}">{esc(RI.explorer_title(RI.model_set(sv["name"])))}</a> {RI.LIVE_BADGE} (the live forecast's weights, a what-if editor) · <a href="/reports/{RI.STAGE2}">{esc(RI.TITLES[RI.STAGE2])}</a> · <a href="{INDEX_URL}">Every report</a>, current and archived · <a href="/forecast">The forecast</a></p></section>
</div>{FIT_SCRIPT}</body></html>'''
    return html


# ── report B: how it is graded ──────────────────────────────────────────────

def grade_set(sc: dict, label, thr_grid=LEVEL_EDGES) -> dict:
    """Windows × rulers for one scorecard, graded at the risk-level edges."""
    days = sc["days"]
    hs, tt, last = sc["holdout_start"], sc["trained_through"], sc["span"][1]
    post0 = str(dt.date.fromisoformat(tt) + dt.timedelta(days=1))
    out = {}
    for wname, (lo, hi, ho) in {"holdout": (hs, tt, True), "post": (post0, last, False), "oos": (hs, last, False)}.items():
        out[wname] = {
            "start": lo, "end": hi,
            "combined": zone_confusion_combined(days, ZONE_ORDER, lo, hi, holdout_only=ho, thresholds=thr_grid),
            "discharge": zone_confusion(days, ZONE_ORDER, lo, hi, holdout_only=ho, thresholds=thr_grid),
            "tail": zone_fp_tail(days, ZONE_ORDER, lo, hi, holdout_only=ho, thresholds=thr_grid),
            "posted": zone_confusion_posted(days, ZONE_ORDER, label, lo, hi, holdout_only=ho, thresholds=thr_grid) if label is not None else None,
            "basins": basin_metrics(days, BASINS, lo, hi, holdout_only=ho),
        }
    return out


def scores(c: dict) -> dict:
    """POD, FAR = fp/(tp+fp), POFD = fp/(fp+tn), CSI … from one confusion's counts (verify's JWGFVR names)."""
    return V.contingency_table(c["tp"], c["fp"], c["fn"], c["tn"])


def pooled(conf: dict, thr: float, zones=ZONE_ORDER) -> dict:
    """One ruler's counts at one line, summed over zones."""
    return {k: sum(conf[z][str(thr)][k] for z in zones) for k in ("tp", "fn", "fp", "tn")}


def calibration(sc: dict, lo: str, hi: str) -> list:
    bands = [(0, .1), (.1, .25), (.25, .5), (.5, .75), (.75, 1.01)]
    out = []
    for blo, bhi in bands:
        n = k = 0
        for d in sc["days"]:
            if not (lo <= d["date"] <= hi):
                continue
            for key in BASINS:
                b = d["basins"][key]
                p = b["ph"] if b.get("ph") is not None else b["p"]
                if p is None or b["y"] is None or not (blo <= p < bhi):
                    continue
                n += 1; k += b["y"]
        out.append({"band": f"{int(blo*100)}–{int(min(bhi, 1)*100)}%", "n": n, "hits": k, "rate": (k / n) if n else None})
    return out


def report_graded(S: dict) -> str:
    sv, sc = S["info"], S["sc"]
    gen = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    label = PL.from_beachwatch()
    served_name = sv["name"]
    medium = LEVEL_EDGES[0]                               # the Model check's default grading threshold (A3)
    sets = [{"name": served_name, "label": f"{served_name} (served)", "sc": sc, "served": True}]
    for man in candidates.list_candidates():
        c = candidates.load_scorecard(man["name"])
        if c:
            sets.append({"name": man["name"], "label": man["name"], "sc": c, "served": False})
    G = {s["name"]: grade_set(s["sc"], label) for s in sets}
    g = G[served_name]
    hs, tt, last = sc["holdout_start"], sc["trained_through"], sc["span"][1]

    # rulers strip
    def strip(title, cells):
        return (f'<div class="strip"><b>{esc(title)}</b><div class="cells">' + "".join(f'<span class="cell {k}" title="{esc(t)}">{esc(lab)}</span>' for lab, k, t in cells) + "</div></div>")
    week = ["overflow", "+1", "+2", "+3", "+4", "+5", "+6", "+7"]
    rulers = "".join([
        strip("Discharge days + samples (primary)", [("overflow day", "bad", "bad"), ("sampled: over", "bad", "elevated sample in the week after: bad"), ("not sampled", "na", "not graded"), ("sampled: clean", "good", "good"), ("not sampled", "na", ""), ("sampled: over", "bad", ""), ("not sampled", "na", ""), ("quiet", "good", "")]),
        strip("Discharge days only", [("overflow day", "bad", ""), *[("quiet", "good", "every day after counts as quiet") for _ in range(7)]]),
        strip("Beach postings (BeachWatch)", [("posted", "bad", ""), ("posted", "bad", ""), ("posted", "bad", ""), ("sign down", "good", ""), ("quiet", "good", ""), ("quiet", "good", ""), ("quiet", "good", ""), ("quiet", "good", "")]),
    ])

    # one row of counts and threshold scores (primary ruler); the column heads are SCORE_HEAD's
    SCORE_HEAD = ('<th class="num">bad days</th><th class="num" title="POD: caught ÷ bad days">caught</th><th class="num">missed</th><th class="num">false alarms</th>'
                  '<th class="num" title="POFD: false alarms ÷ every known-clean day">clean days alerted</th>'
                  '<th class="num" title="FAR: false alarms ÷ every day alerted">alerts that were false</th>'
                  '<th class="num" title="CSI: caught ÷ (caught + missed + false alarms)">CSI</th>')

    def score_cells(c: dict, split: bool = False) -> str:
        s = scores(c)
        why = f' <span class="mute">({c["fp_sample"]} clean-sample · {c["fp_quiet"]} quiet)</span>' if split else ""
        return (f'<td class="num">{c["tp"] + c["fn"]}</td><td class="num">{c["tp"]} <span class="mute">{pct(s["pod"])}</span></td><td class="num">{c["fn"]}</td>'
                f'<td class="num">{c["fp"]}{why}</td><td class="num">{pct(s["pofd"], 1)}</td><td class="num">{pct(s["far"])}</td><td class="num">{score2(s["csi"])}</td>')

    # served results (primary ruler) at the Medium edge, oos and post
    def zone_table(w: dict, thr: float, title: str) -> str:
        rows = [f'<tr><td>{ic(ZONE_ICON[z])} <b>{esc(ZONE_LABEL[z])}</b></td>{score_cells(w["combined"][z][str(thr)], split=True)}</tr>' for z in ZONE_ORDER]
        rows.append(f'<tr class="total"><td>All zones</td>{score_cells(pooled(w["combined"], thr))}</tr>')
        return f'<h3>{esc(title)}</h3><div class="tw"><table><tr><th>zone</th>{SCORE_HEAD}</tr>{"".join(rows)}</table></div>'

    served_tables = zone_table(g["oos"], medium, f"Out of sample, {fmt_month(hs)} → {fmt_month(last)}: holdout-fit models through {fmt_month(tt)}, the served models after") + zone_table(g["post"], medium, f"Since training only, {fmt_month(g['post']['start'])} → {fmt_month(last)}: the truest test, one wet season")

    # what each risk level catches: the served set, out of sample, every zone at every level edge
    lvl_rows = []
    for z in ZONE_ORDER:
        for i, t in enumerate(LEVEL_EDGES):
            head = f'<td rowspan="{len(LEVEL_EDGES)}">{ic(ZONE_ICON[z])} <b>{esc(ZONE_LABEL[z])}</b></td>' if i == 0 else ""
            lvl_rows.append(f'<tr>{head}<td>{esc(level_name(t))}</td>{score_cells(g["oos"]["combined"][z][str(t)])}</tr>')
    for i, t in enumerate(LEVEL_EDGES):
        head = f'<td rowspan="{len(LEVEL_EDGES)}">All zones</td>' if i == 0 else ""
        lvl_rows.append(f'<tr class="{"total" if i == 0 else "sum"}">{head}<td>{esc(level_name(t))}</td>{score_cells(pooled(g["oos"]["combined"], t))}</tr>')
    levels_table = f'<div class="tw"><table class="lv"><tr><th>zone</th><th>alerted at</th>{SCORE_HEAD}</tr>{"".join(lvl_rows)}</table></div>'

    # every set on the same days, all zones, out of sample: listed in a fixed order (served first), never ranked
    def sets_table(t: float) -> str:
        rows = "".join(f'<tr><td>{"<b>" + esc(s["label"]) + "</b>" if s["served"] else esc(s["label"])}</td>{score_cells(pooled(G[s["name"]]["oos"]["combined"], t))}</tr>' for s in sets)
        return f'<div class="tw"><table><tr><th>set</th>{SCORE_HEAD}</tr>{rows}</table></div>'
    sets_html = (f'<div class="card wide"><b>Alerts at {esc(level_name(medium))}</b>{sets_table(medium)}</div>'
                 + "".join(f'<details class="more"><summary>Alerts at {esc(level_name(t))}</summary><div class="card wide">{sets_table(t)}</div></details>' for t in LEVEL_EDGES[1:]))

    # stage 1 ranking: PR-AUC per basin, holdout and post, served vs others
    s1_rows = []
    for b in BASINS:
        cells = "".join(f'<td class="num">{(G[s["name"]]["holdout"]["basins"][b]["pr_auc"] or 0):.2f} / {(G[s["name"]]["post"]["basins"][b]["pr_auc"] or 0):.2f}</td>' for s in sets)
        s1_rows.append(f'<tr><td>{esc(BASIN_NAME[b])}</td><td class="num">{g["holdout"]["basins"][b]["n_events"]} / {g["post"]["basins"][b]["n_events"]}</td>{cells}</tr>')
    s1_table = f'<div class="tw"><table><tr><th>basin</th><th class="num">overflow days (holdout / since)</th>{"".join(f"<th class=num>{esc(s['label'])}</th>" for s in sets)}</tr>{"".join(s1_rows)}</table></div><p class="fine">PR-AUC holdout / since training: how well the day\'s probability ranks overflow days above quiet ones (1.0 perfect). Sets sharing a stage 1 share these numbers.</p>'
    cal = calibration(sc, hs, last)
    cal_bars = svg_hbars([{"label": c["band"], "value": (c["rate"] or 0), "color": BRAND, } for c in cal], vmax=1.0, fmt=lambda v: f"{100*v:.0f}%", title="of the basin-days the model put in this band, the share that overflowed (out of sample)", width=560)
    cal_note = " · ".join(f'{c["band"]}: {c["hits"]} of {c["n"]}' for c in cal)

    # replays
    ra = json.load(open(REPO / "reports" / "2026-09_live_replay.json")) if (REPO / "reports" / "2026-09_live_replay.json").exists() else None
    rs = json.load(open(REPO / "reports" / "2026-09_live_replay_synthetic.json")) if (REPO / "reports" / "2026-09_live_replay_synthetic.json").exists() else None
    live_key = LR.VERSION
    replay_html = ""
    if ra and rs:
        num = lambda x: f"{x:.0f}" if abs(x - round(x)) < 1e-9 else f"{x:.1f}"  # noqa: E731  (synthetic counts are means over draws)
        a_plain, a_live = (ra["variants"][k]["grades"]["0.5"]["combined"]["bayside"] for k in ("plain", live_key))
        s_plain, s_live, s_perf, s_all = (rs["variants"][k]["grades"] for k in ("plain", live_key, f"{live_key}_perfect_feed", "all_floors"))
        n_draws = len(rs["feed"]["seeds"])
        a_bad = a_plain["tp"] + a_plain["fn"]
        s_bad = s_plain["0.5"]["combined"]["tp"] + s_plain["0.5"]["combined"]["fn"]
        syn = [("model alone · 50%", s_plain["0.5"], "#8a949b"), (f"{live_key} · 50%", s_live["0.5"], BRAND), ("perfect feed · 50%", s_perf["0.5"], "#237059"),
               ("model alone · 25%", s_plain["0.25"], "#b8c4cc"), (f"{live_key} · 25%", s_live["0.25"], "#85BFDF")]
        moved = lambda a, b, k: f'{num(a["combined"][k])} → {num(b["combined"][k])}'  # noqa: E731
        replay_html = f'''<div class="grid2">
          <div class="card"><b>2016-17 feed archive, bay-side beaches, 50% line</b>{svg_hbars([{"label": "model alone · caught", "value": a_plain["tp"], "color": "#8a949b"}, {"label": f"{live_key} · caught", "value": a_live["tp"], "color": BRAND}, {"label": "model alone · false alarms", "value": a_plain["fp"], "color": "#b8c4cc"}, {"label": f"{live_key} · false alarms", "value": a_live["fp"], "color": "#85BFDF"}], vmax=max(a_bad, a_plain["fp"], a_live["fp"]), title=f"bad days caught (of {a_bad}) and false alarms", width=420, label_w=190)}<p class="fine">Bad days caught: {a_plain["tp"]} → {a_live["tp"]} of {a_bad}; false alarms: {a_plain["fp"]} → {a_live["fp"]}. Real flags from the 2016-17 archive of SFPUC's feed, replayed one day at a time with only what was known that morning.</p></div>
          <div class="card"><b>Synthetic feed, every out-of-sample day, all zones</b>{svg_hbars([{"label": lab, "value": gr["combined"]["tp"], "color": col} for lab, gr, col in syn], vmax=s_bad, fmt=num, title=f"bad days caught, of {s_bad}, mean of {n_draws} draws", width=420, label_w=190)}{svg_hbars([{"label": lab, "value": gr["combined"]["fp"], "color": col} for lab, gr, col in syn], fmt=num, title=f"false alarms, mean of {n_draws} draws", width=420, label_w=190)}<p class="fine">The filed overflows stand in for the feed, degraded with the archive\'s miss and lag rates. With the rules, bad days caught go {moved(s_plain["0.5"], s_live["0.5"], "tp")} and false alarms {moved(s_plain["0.5"], s_live["0.5"], "fp")} at 50%; at 25%, {moved(s_plain["0.25"], s_live["0.25"], "tp")} and {moved(s_plain["0.25"], s_live["0.25"], "fp")}. Every sample floor on, those under 0.5 included, would take the 25% line to {num(s_all["0.25"]["combined"]["tp"])} caught and {num(s_all["0.25"]["combined"]["fp"])} false alarms; {live_key} applies only the floors of 0.5 or more.</p></div></div>'''

    # weather input check
    wx = json.load(open(MODEL_DIR / "weather_models_eval.json")) if (MODEL_DIR / "weather_models_eval.json").exists() else None
    wx_html = ""
    if wx:
        ver = wx["verification"]["avg"]
        names = {"ecmwf_ifs025": "ECMWF", "gfs_seamless": "GFS", "icon_seamless": "ICON", "mean3": "mean of three"}
        served_wx = wx.get("served", {}).get("weather_model")
        mae = svg_hbars([{"label": names[k], "value": ver[k]["wet_mae_in"], "color": (BRAND if k == served_wx else "#8a949b")} for k in sorted(names, key=lambda k: ver[k]["wet_mae_in"])], fmt=lambda v: f'{v:.2f}"', title="wet-day error against the two-gauge mean (lower is better)", width=420)
        csi = svg_hbars([{"label": names[k], "value": ver[k]["thresholds"]["0.5"]["csi"], "color": (BRAND if k == served_wx else "#8a949b")} for k in sorted(names, key=lambda k: -(ver[k]["thresholds"]["0.5"]["csi"] or 0))], vmax=1.0, fmt=lambda v: f"{v:.2f}", title='skill at calling a half-inch day (higher is better)', width=420)
        wx_html = f'<div class="grid2"><div class="card">{mae}</div><div class="card">{csi}</div></div><p class="fine">{esc(wx["window"][0])} → {esc(wx["window"][1])}, {wx["gauge_days"]["avg"]["wet_days"]} wet days. The weather model is an input, so it is graded as one: against the gauges, not on what the discharge model does with it. Details: <a href="/reports/2026-09_weather_models.html">the weather report</a>.</p>'

    n_bad_oos = sum(g["oos"]["combined"][z][str(medium)]["tp"] + g["oos"]["combined"][z][str(medium)]["fn"] for z in ZONE_ORDER)   # bad days: the same at every line
    level_list = ", ".join(level_name(t) for t in LEVEL_EDGES)
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>How the forecast is graded — {esc(served_name)}</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/brand.css"><style>{CSS}.tw{{overflow-x:auto}}table.lv td[rowspan]{{background:#f7fafc}}table.lv tr.sum td{{font-weight:700}}</style></head><body>{sprite()}
<div class="wrap">
<header class="rh"><img src="/static/brand/bwtf_144x144.png" alt="" class="mark"><div><h1>How the forecast is graded</h1>
<p class="sub">Every stored day is scored against what the city later filed and measured. The served set is <b>{esc(served_name)}</b>. A day is graded as alerted at the risk levels the page shows, the same in every zone: <b>{esc(level_list)}</b>. The tables count days and give the standard scores; nothing weighs a miss against a false alarm. Nothing here uses a day the model saw while it was being chosen.</p>
<div class="meta"><span>generated {esc(gen)}</span><span>holdout {esc(fmt_month(hs))} → {esc(fmt_month(tt))}</span><span>since training {esc(fmt_month(g["post"]["start"]))} → {esc(fmt_month(last))}</span><span><a href="/reports/2026-09_forecast_how_it_works.html">← how it works</a></span></div></div></header>
<nav><a href="#rulers">Rulers</a><a href="#results">Results</a><a href="#levels">Risk levels</a><a href="#sets">Sets</a><a href="#stage1">Stage 1</a><a href="#live">Live corrections</a><a href="#weather">Weather input</a><a href="#caveats">Caveats</a></nav>

<section id="rulers"><h2>What counts as a bad day</h2>
<p class="lead">Three rulers, one week after an overflow. The primary one grades the percentage on what it claims: a beach fouled by an overflow. The overflow day is bad; a sample over standard in the week after is bad; a clean sample is good; a day nobody sampled is not graded; a dirty sample with no overflow in the prior week is dry-weather dirtiness the rain model does not claim.</p>
<div class="panel strips"><div class="weekhead"><span></span>{"".join(f"<span>{w}</span>" for w in week)}</div>{rulers}</div></section>

<section id="results"><h2>The served set, primary ruler</h2>
<p class="lead">{n_bad_oos} bad days out of sample. Caught means the risk that morning was at {esc(level_name(medium))}, the Model check&#39;s default; the share beside it is POD, the share of bad days caught. False alarms are alerted days known clean: a clean sample, or a quiet day with no recent overflow. Clean days alerted (POFD) = false alarms ÷ every known-clean day; alerts that were false (FAR) = false alarms ÷ every day alerted; CSI = caught ÷ (caught + missed + false alarms), 1 is perfect.</p>
{served_tables}</section>

<section id="levels"><h2>What each risk level catches</h2>
<p class="lead">The served set out of sample, each zone graded at the lower edge of each level: a day counts as alerted when its risk reached that level. A higher level calls fewer days, so it catches fewer bad days and raises fewer false alarms. The levels are fixed and the same in every zone ({esc(" · ".join(f"{lv.label} {lv.lo}–{lv.hi}" for lv in LEVELS))}); no line is picked by weighing misses against false alarms.</p>
<div class="card wide">{levels_table}</div></section>

<section id="sets"><h2>Every set on the same days</h2>
<p class="lead">The served set against the candidates kept on disk, graded on identical days and labels, out of sample, all zones. Listed in a fixed order, the served set first, not ranked: <a href="/reports/2026-10_forecast_stages.html">the five stages report</a> compares the sets, with the Brier score, which needs no line.</p>
{sets_html}</section>

<section id="stage1"><h2>Stage 1 on its own</h2>
<p class="lead">Graded on overflow days alone, where that label is exact.</p>
<div class="card wide">{s1_table}</div>
<div class="card wide"><b>Does 30% mean 30%?</b>{cal_bars}<p class="fine">{esc(cal_note)}. A well-calibrated model sits near the band it names.</p></div></section>

<section id="live"><h2>Live corrections, replayed</h2>
<p class="lead">The rules are graded by replaying past days with only what was known each morning: a day never sees its own flag or sample. The arithmetic is {live_key}'s; live_v1 graded identically, since only the source of the samples changed.</p>{replay_html}</section>

<section id="weather"><h2>The weather model, as an input</h2>{wx_html}</section>

<section id="caveats"><h2>Caveats</h2><ul>
<li><b>Small numbers.</b> {n_bad_oos} bad days out of sample, fourteen overflow days since training. A few storms decide any comparison; read differences of a handful of days as ties.</li>
<li><b>Most tail days are unsampled</b>, so the tail is judged on the days the city happened to sample, which is mostly the East. The 2000–2020 lab export can extend the samples ruler across the whole training span; it is the next grading job.</li>
<li><b>One set of levels for every zone.</b> The same percent means the same word everywhere, so the zones trade catches for false alarms differently at the same level (the risk-level table above).</li>
<li><b>Holdout numbers flatter the weights model</b>: its settings were chosen there. The since-training table is the fairer one.</li>
<li><b>Postings are precautionary.</b> Against the signs the outfall split catches fewer posted days on Ocean Beach, with fewer false alarms there: SFPUC posts after Sea Cliff-only overflows whose samples come back clean. The water could not settle it (one sampled case in ten years).</li>
</ul><p class="lead"><a href="/reports/2026-10_forecast_stages.html">The five stages, scored one by one</a> · <a href="/reports/2026-09_forecast_how_it_works.html">How it works</a> · <a href="/forecast">The Model check, live</a></p></section>
</div>{FIT_SCRIPT}</body></html>'''
    return html


CSS = """
.wrap{max-width:1440px;margin:0 auto;padding:24px}
.rh{display:flex;gap:18px;align-items:flex-start;margin-bottom:8px}.rh .mark{width:56px;height:56px;border-radius:14px}
h1{font-family:'Bebas Neue',sans-serif;font-weight:400;font-size:40px;letter-spacing:.04em;margin:0 0 6px;color:#26272a}
h2{font-family:'Bebas Neue',sans-serif;font-weight:400;font-size:28px;letter-spacing:.05em;margin:28px 0 8px;color:#26272a}
h3{font-size:16px;margin:18px 0 6px;color:#26272a}
.sub{font-size:16px;color:#54576F;margin:0 0 8px;max-width:900px}.lead{font-size:15px;color:#54576F;max-width:960px;margin:6px 0 12px}
.meta{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:#8a949b}
nav{display:flex;flex-wrap:wrap;gap:4px 14px;margin:10px 0 6px;font-size:14px;font-weight:600}nav a{color:#0072BC;text-decoration:none}nav a:hover{text-decoration:underline}
.panel{background:#fff;border:1px solid #d9e4e8;border-radius:24px;padding:16px;overflow-x:auto}
svg.pipe{display:block;width:100%;min-width:760px;height:auto;font-family:Roboto,Arial,sans-serif}
.pipe-m{display:none;list-style:none;margin:0;padding:0}.pipe-m li{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-top:1px solid #eef2f5}.pipe-m li:first-child{border-top:0}.pipe-m b{display:block;font-size:15px}.pipe-m span:last-child{font-size:13.5px;color:#54576F}.pipe-m .lgi img{width:26px;height:26px;object-fit:contain}
@media(max-width:700px){svg.pipe{display:none}.pipe-m{display:block}.panel{overflow:visible}}
.box{fill:#fff;stroke:#d9e4e8;stroke-width:1.5}.box.hub{stroke:#0072BC;stroke-width:2}.tile{fill:#E3EBF2}.tile.logo{fill:#fff;stroke:#e3ebf2;stroke-width:1.5}
.icn{fill:none;stroke:#0072BC;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.t{font-size:16px;font-weight:700;fill:#26272a}.s{font-size:13px;fill:#54576F}.col{font-size:11.5px;font-weight:700;letter-spacing:.12em;fill:#8a949b}
.flow{fill:none;stroke:#0072BC;stroke-width:2}.fit{fill:none;stroke:#8a949b;stroke-width:2;stroke-dasharray:6 5}.dot{fill:#0072BC}.al{font-size:12px;font-weight:700;fill:#0072BC}.fl{font-size:12px;font-weight:700;fill:#8a949b}.lg{font-size:12px;fill:#54576F}.cap{font-size:12px;fill:#54576F}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
@media(max-width:1000px){.grid4{grid-template-columns:repeat(2,minmax(0,1fr))}.grid3{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:640px){.grid2,.grid3,.grid4{grid-template-columns:minmax(0,1fr)}.rh{flex-direction:column}.wrap{padding:16px 14px}}
.card{background:#fff;border:1px solid #d9e4e8;border-radius:18px;padding:14px 16px;font-size:14px;color:#26272a;min-width:0;overflow-x:auto}.card.wide{grid-column:1/-1}
.card > b, .card > div > b:first-child{display:block;font-size:15px;margin-bottom:4px}.card p b{display:inline;font-size:inherit;margin:0}.card .bh{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.card .bh b{margin:0}
.card.src{display:flex;gap:12px;align-items:flex-start}.card.src img.lg{width:44px;height:44px;object-fit:contain;border:1px solid #e3ebf2;border-radius:11px;padding:4px;background:#fff;flex:0 0 auto}
.lgi{display:inline-flex;width:44px;height:44px;border-radius:11px;background:#E3EBF2;align-items:center;justify-content:center;flex:0 0 auto}.lgi .ic{width:24px;height:24px;color:#0072BC}
.card.rule{display:flex;gap:12px;align-items:flex-start}.card.rule p{margin:2px 0 6px;color:#54576F}
.srow{display:flex;justify-content:space-between;gap:10px;font-size:13px;padding:3px 0;border-top:1px solid #eef2f5}.srow span:first-child{color:#26272a}
.mute{color:#8a949b}.fine{font-size:12.5px;color:#54576F;margin:6px 0 0}
.kpis{display:flex;gap:14px;margin:8px 0 4px}.kpis div{display:flex;flex-direction:column}.kpis b{font-size:20px;margin:0;color:#0072BC}.kpis span{font-size:11.5px;color:#8a949b}
.chartbox{margin-top:6px}svg.chart{display:block;width:100%;height:auto;font-family:Roboto,Arial,sans-serif}.card.wide .chartbox svg,.card.wide svg.lag{max-width:760px}.grid{stroke:#eef2f5}.ax{font-size:10.5px;fill:#8a949b}.ttl{font-size:12px;font-weight:700;fill:#54576F}.bl{font-size:12px;fill:#26272a}.bv{font-size:12px;font-weight:700;fill:#26272a}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:#54576F;margin-top:2px}.legend i{display:inline-block;width:14px;height:4px;border-radius:2px;margin-right:6px;vertical-align:middle}
table{width:100%;border-collapse:collapse;font-size:13.5px;background:#fff;border:1px solid #d9e4e8;border-radius:14px;overflow:hidden}
th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#54576F;padding:8px 10px;border-bottom:1px solid #d9e4e8;background:#f7fafc}td{padding:7px 10px;border-bottom:1px solid #eef2f5;vertical-align:top}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}tr.total td{font-weight:700;border-top:2px solid #d9e4e8}table.plain{border:0}.mono{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
table.wt td.heat{font-variant-numeric:tabular-nums;font-weight:600}table.wt .dir{font-size:10px;opacity:.8}
details.more{margin-top:10px}details.more summary{cursor:pointer;font-weight:700;color:#0072BC}details.math>.card{margin-top:8px}details.math p{margin:6px 0;color:#26272a;font-size:14px}.eq{font-family:'Times New Roman',Georgia,serif;font-size:16px;white-space:nowrap}details.math table{margin:8px 0 12px}h4{margin:14px 0 6px;font-size:14px;color:#26272a}
.caps{margin:10px 0 0;padding:0 0 0 18px;color:#54576F;font-size:14px}.caps li{margin:4px 0}.caps b{color:#26272a}
.levels{display:flex;flex-direction:column;gap:6px;margin:8px 0}.lvl{display:flex;justify-content:space-between;font-size:14px;padding:6px 10px;background:#f7fafc;border-radius:10px}.lvl b{margin:0;font-size:14px}
svg.tl,svg.lag{display:block;width:100%;height:auto;font-family:Roboto,Arial,sans-serif}
.strips .weekhead{display:grid;grid-template-columns:220px repeat(8,1fr);gap:4px;font-size:11px;color:#8a949b;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:6px;text-align:center}
.strip{display:grid;grid-template-columns:220px 1fr;gap:4px;align-items:center;margin:4px 0}.strip b{font-size:13.5px}.cells{display:grid;grid-template-columns:repeat(8,1fr);gap:4px}
.cell{font-size:11.5px;text-align:center;padding:8px 4px;border-radius:8px;background:#f3f6f9;color:#54576F}.cell.bad{background:#f8dcd6;color:#b5310a;font-weight:700}.cell.good{background:#e0f0ea;color:#237059}.cell.na{background:#f3f6f9;color:#8a949b;font-style:italic}
.ic{width:1em;height:1em;vertical-align:-.15em;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.pointer{font-size:15px;color:#26272a;background:#e8f2fa;border:1px solid #c9dcea;border-radius:12px;padding:10px 14px;margin:8px 0 10px;max-width:900px}.pointer a{color:#0072BC}
.tw{overflow-x:auto}
""" + RI.LIVE_CSS


def main(argv=None):
    """``export_how_it_works.py [--graded]``: write how it works. The graded page is archived and kept as it
    was; ``--graded`` rebuilds it on purpose and stamps its archived banner straight back on."""
    argv = sys.argv[1:] if argv is None else argv
    S = load_served()
    reg = registry(include_supabase=False)
    # the engine is heavy: read the weather model from live_dashboard's source instead of importing it
    wx_model = re.search(r'"models":\s*"([a-z0-9_]+)"', (FORECAST / "live_dashboard.py").read_text()).group(1)
    OUT_WORKS.write_text(report_works(S, reg, wx_model))
    done = [f"{OUT_WORKS.relative_to(REPO)} ({OUT_WORKS.stat().st_size / 1e3:.0f} KB)"]
    if "--graded" in argv:
        OUT_GRADED.write_text(report_graded(S))
        RI.stamp(OUT_GRADED)                     # archived: a rebuild never drops the banner
        done.append(f"{OUT_GRADED.relative_to(REPO)} ({OUT_GRADED.stat().st_size / 1e3:.0f} KB, archived banner on)")
    print(f"wrote {' and '.join(done)}; weather model {wx_model}; live {LR.VERSION}; served {S['info']['name']}")


if __name__ == "__main__":
    main()
