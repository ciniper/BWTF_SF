#!/usr/bin/env python3
"""Two plain reports about the forecast as it runs today, drawn from the artifacts:

    reports/2026-09_forecast_how_it_works.html     inputs → stage 1 → stage 2 → live corrections → outputs, and how it was trained
    reports/2026-09_forecast_how_it_is_graded.html the rulers, the windows, the lines, the results, the replays, the weather input

Less text, more pictures (Chase, 2026-09-30). Every number is read from the served
bundle (served.json, the pickles, stage2.json, scorecard.json.gz), the rules
module, the candidates' scorecards, the replay and weather JSONs and the sources
registry — nothing is typed by hand that a file already knows. Re-run after a
promotion, a rescore or a rule change:

    venv/bin/python features/forecast/src/models/export_how_it_works.py
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
from src.models import candidates, live_rules as LR, posting_label as PL, stage2 as S2  # noqa: E402
from src.models.groups import BASIN_KEYS, GROUPS_BY_BASIN, SITE_GROUPS, ZONE_GROUPS  # noqa: E402
from src.models.impact import compose, impact_fraction, smooth_table  # noqa: E402
from src.models.rain_features import DAILY_FEATURES, INTENSITY_FEATURES, add_daily_features, hourly_intensity  # noqa: E402
from src.models.scorecard import basin_metrics, zone_confusion, zone_confusion_combined, zone_confusion_posted, zone_fp_tail  # noqa: E402
from shared.sources import registry  # noqa: E402
from shared.zones import ZONES  # noqa: E402

_main = sys.modules.get("__main__")
if _main is not None and not hasattr(_main, "add_hinges"):
    _main.add_hinges = leaderboard.add_hinges

MODEL_DIR = FORECAST / "data" / "models"
RAW_DIR = FORECAST / "data" / "raw"
OUT_WORKS = REPO / "reports" / "2026-09_forecast_how_it_works.html"
OUT_GRADED = REPO / "reports" / "2026-09_forecast_how_it_is_graded.html"
ICONS = REPO / "app" / "templates" / "_icons.html"

BASINS = list(BASIN_KEYS.values())                   # westside, north_shore, central, southeast
BASIN_NAME = {v: k for k, v in BASIN_KEYS.items()}
ZONE_ORDER = ["ocean", "baker_china", "north", "east"]
ZONE_LABEL = {k: z.label for k, z in (ZONES.items() if isinstance(ZONES, dict) else ((z.key, z) for z in ZONES))}
ZONE_ICON = {"ocean": "waves", "baker_china": "umbrella", "north": "anchor", "east": "building-2"}
GROUP_ORDER = ["Ocean Beach", "Baker-China", "Crissy Field", "Aquatic Park", "Mission Creek", "Southeast"]
LINE_GRID = tuple(round(x, 2) for x in np.arange(0.05, 0.80, 0.05))
MISS_WEIGHT = 2                                       # Chase: a miss costs two false alarms
LEVELS = [(0.5, "HIGH", "#b5310a"), (0.25, "MODERATE", "#d4763a"), (0.1, "LOW", "#b97e00"), (0.0, "MINIMAL", "#237059")]
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
    return "—" if v is None else f"{100 * v:.{nd}f}%"


def fmt_month(day: str) -> str:
    return dt.date.fromisoformat(day[:10]).strftime("%b %Y")


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
    L, R, T, B = 40, 12, 22, 30
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


def node(x, y, w, h, icon, title, sub, hub=False):
    tile = logo_tile(x + 10, y + (h - 40) / 2, icon[5:]) if icon.startswith("logo:") else icon_tile(x + 10, y + (h - 40) / 2, icon)
    return (f'<rect class="box{" hub" if hub else ""}" x="{x}" y="{y}" width="{w}" height="{h}" rx="14"/>{tile}'
            f'<text class="t" x="{x + 60}" y="{y + h / 2 - 3}">{esc(title)}</text><text class="s" x="{x + 60}" y="{y + h / 2 + 14}">{esc(sub)}</text>')


def pipeline_svg(sv: dict, wx_label: str) -> str:
    """The whole thing in one picture. Top path: rain → 19 numbers → stage 1 →
    stage 2. Bottom path: the city's live observations → live corrections.
    Both meet in the outputs. Arrows carry what flows along them."""
    a = ['<svg class="pipe" viewBox="0 0 1200 430" role="img"><title>How rain becomes a beach percentage</title>'
         '<defs><marker id="pa" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="#0072BC"/></marker></defs>']
    a.append('<text class="col" x="20" y="30">WHAT COMES IN</text><text class="col" x="310" y="30">THE MODEL (fixed since training)</text><text class="col" x="990" y="30">WHAT GOES OUT</text>')
    # rain, the top path
    a.append(node(20, 56, 240, 54, "logo:logos/noaa.svg", "Two NOAA gauges", "Downtown · Oceanside · daily totals"))
    a.append(node(20, 118, 240, 54, "logo:logos/noaa.svg", "SFO airport gauge", "NWS · today's hours so far"))
    a.append(node(20, 180, 240, 54, "cloud-rain", f"{wx_label} weather model", "Open-Meteo · the hours ahead"))
    a.append(node(310, 118, 180, 54, "gauge", "19 numbers a day", "totals · lags · peaks · dryness", hub=True))
    a.append(node(530, 118, 190, 54, "chart", "Stage 1 · 4 basins", "chance the sewers overflow", hub=True))
    a.append(node(760, 118, 200, 54, "waves", "Stage 2 · 6 beach groups", "how long, and which beaches", hub=True))
    # observations, the bottom path
    a.append(node(20, 296, 240, 54, "logo:logos/sfpuc.png", "SFPUC beach map", "overflow flags · postings · every minute"))
    a.append(node(20, 358, 240, 54, "logo:logos/sf-city-seal.png", "City lab results", "DataSF · published nightly"))
    a.append(node(760, 296, 200, 54, "satellite-dish", f"Live corrections · {LR.VERSION}", "observations override the model", hub=True))
    # outputs
    a.append(node(990, 180, 190, 54, "map-pin", "4 zones × 6 days", "a percentage, every 30 min"))
    a.append(node(990, 242, 190, 54, "bell", f"Alarm line {int(round((sv.get('line') or 0.25) * 100))}%", "the banner words"))
    a.append(node(990, 304, 190, 54, "logo:logos/supabase.svg", "History tables", "every forecast, kept"))

    def arrow(d, label=None, lx=None, ly=None):
        a.append(f'<path class="flow" d="{d}" marker-end="url(#pa)"/>')
        if label:
            a.append(f'<text class="al" x="{lx}" y="{ly}" text-anchor="middle">{esc(label)}</text>')
    def dot(x, y):
        a.append(f'<circle class="dot" cx="{x}" cy="{y}" r="3.5"/>')
    # the three rain sources join into one hourly series, then the daily numbers
    a.append('<path class="flow" d="M260,83 H285 V211 H260"/>'); dot(285, 145)
    arrow("M285,145 H310")
    a.append('<text class="al" x="292" y="228" text-anchor="start">one hourly series</text>')
    arrow("M490,145 H530", "19 numbers", 510, 108)
    arrow("M720,145 H760", "P(overflow)", 740, 108)
    arrow("M860,172 V296", "% per beach group", 860, 240)
    # observations into the live corrections
    a.append('<path class="flow" d="M260,385 H285 V323 H260"/>'); dot(285, 323)
    arrow("M285,323 H760", "flags · postings · sample results", 520, 314)
    # live corrections to each output
    a.append('<path class="flow" d="M960,323 H975 V207"/><path class="flow" d="M975,323 V331"/>'); dot(975, 323)
    arrow("M975,207 H990"); arrow("M975,269 H990"); arrow("M975,331 H990")
    a.append("</svg>")
    return "".join(a)



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


# ── report A: how it works ──────────────────────────────────────────────────

def report_works(S: dict, reg: dict, wx_model: str) -> str:
    sv, models, table, stage2, sc = S["info"], S["models"], S["table"], S["stage2"], S["sc"]
    wx_label = {"icon_seamless": "ICON", "ecmwf_ifs025": "ECMWF", "gfs_seamless": "GFS"}.get(wx_model, wx_model)
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
    inputs = "".join([
        src_card("logo:logos/noaa.svg", "Rain that fell", "two NOAA gauges (daily) and the SFO airport gauge (hourly): the truth the model was trained on",
                 [e for e in weather if "ACIS" in e["name"] or "KSFO" in e["name"]]),
        src_card("cloud-rain", "Rain to come", f"the {wx_label} weather model through Open-Meteo, hourly for six days: the stand-in for the gauges on forecast days",
                 [e for e in weather if "ICON" in e["name"]], "An input, not part of the model: the fitted weights never see which weather model produced the inches, so swapping it needs no retraining. ICON is the closest single model to the gauges (see the weather report)."),
        src_card("logo:logos/sfpuc.png", "SFPUC beach map", "overflow flags and beach postings, read every minute by the watcher", tiles.get("sfpuc", [])),
        src_card("logo:logos/sf-city-seal.png", "City lab results", "bacteria samples: DataSF live, the 2000–2020 lab export, the 2016-17 feed archive", tiles.get("datasf", [])),
        src_card("logo:logos/water-boards.png", "State records", "SFPUC's filed overflow reports (CIWQS) train stage 1 and grade everything; BeachWatch postings are a second ruler", tiles.get("state", [])),
    ])

    # stage 1 cards + response curves
    sweep = np.arange(0.0, 3.01, 0.1)
    xl = [f'{v:.1f}"' if i % 5 == 0 else "" for i, v in enumerate(sweep)]
    dry = sweep_features(sweep, ratios)
    wet = sweep_features(sweep, ratios, prior={3: 1.0, 2: 0.5})
    s1_cards = []
    for key in BASINS:
        md = models[key]
        pb = (sv.get("per_basin") or {}).get(key, {})
        ho = pb.get("holdout", {})
        src = md.get("rain_source", "avg")
        chart = svg_lines([{"label": "after a dry month", "color": BRAND, "values": list(predict(md, dry))},
                           {"label": 'after 1.5" earlier this week', "color": "#d4763a", "values": list(predict(md, wet)), "dash": True}],
                          xl, ymax=1.0, title="P(overflow) vs today's rain", xlab="today's rain total")
        s1_cards.append(f'''<div class="card basin"><div class="bh"><b>{esc(BASIN_NAME[key])}</b><span class="mute">{esc("Downtown + Oceanside mean" if src == "avg" else src)}</span></div>
            <div class="kpis"><div><b>{pb.get("n_events", "—")}</b><span>overflow days in training</span></div><div><b>{ho.get("pr_auc", 0):.2f}</b><span>holdout PR-AUC</span></div><div><b>C = {md.get("C", "—")}</b><span>regularisation</span></div></div>{chart}{weights_chart(md)}</div>''')
    knots = (S["leaderboard"].get("hinges") or {})
    feat_rows = "".join(f'<tr><td class="mono">{esc(f)}</td><td>{esc(FEATURE_MEANING.get(f, ""))}</td><td class="mute">{esc(", ".join(f"{k:g}" for k in knots.get(f, [])) or "")}</td></tr>' for f in DAILY_FEATURES + INTENSITY_FEATURES)

    # stage 2: decay curves per group, shares, worked example
    k_labels = ["0", "1", "2", "3", "4–5", "6–7"]
    k_days = [0, 1, 2, 3, 4, 6]
    decay_cards = []
    for g in GROUP_ORDER:
        med = (table.get(g) or {}).get("median_event_volume_mg", 1.0) or 1.0
        small = [impact_fraction(table, g, k, 0.01) for k in k_days]
        large = [impact_fraction(table, g, k, 1e6) for k in k_days]
        base = ((table.get(g) or {}).get("buckets", {}).get("baseline_no_recent_discharge") or {}).get("p_elevated")
        n = (table.get(g) or {}).get("n_sample_days")
        sh = ((stage2 or {}).get("shares") or {}).get(g, {})
        share_txt = "" if not sh else f' · share of basin overflows that reach it: large {sh.get("large", {}).get("p", 1):.2f}, small {sh.get("small", {}).get("p", 1):.2f}'
        decay_cards.append(f'''<div class="card group"><div class="bh"><b>{esc(g)}</b><span class="mute">{esc(SITE_GROUPS[g][0])} basin · {len(SITE_GROUPS[g][1])} station{"s" if len(SITE_GROUPS[g][1]) != 1 else ""}</span></div>
            {svg_lines([{"label": f"large overflow (≥ {med:g} MG)", "color": "#b5310a", "values": large}, {"label": "small overflow", "color": "#d4763a", "values": small, "dash": True}], k_labels, ymax=1.0, title="chance the beach is still over standard", xlab="days after the overflow", width=300, height=160)}
            <div class="fine">Dry-weather background {pct(base)} on {n} sampled days, removed{esc(share_txt)}.</div></div>''')
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
    level_rows = "".join(f'<div class="lvl"><b style="color:{c}">{name}</b><span>{"≥" if lo else "<"} {int((lo if lo else 0.1) * 100)}%</span></div>' for lo, name, c in LEVELS)
    zone_rows = "".join(f'<tr><td>{ic(ZONE_ICON[z])} <b>{esc(ZONE_LABEL[z])}</b></td><td>{esc(", ".join(ZONE_GROUPS[z]))}</td><td class="num">{sum(len(SITE_GROUPS[g][1]) for g in ZONE_GROUPS[z])}</td></tr>' for z in ZONE_ORDER)

    # training timeline
    def tl_x(day: str, x0=60, x1=980, d0=dt.date(2013, 1, 1), d1=dt.date(2026, 12, 31)):
        d = dt.date.fromisoformat(day[:10])
        return x0 + (x1 - x0) * ((d - d0).days / (d1 - d0).days)
    tt, hs = sv.get("trained_through") or sc.get("trained_through"), sv.get("holdout_start") or sc.get("holdout_start")
    tw = json.load(open(MODEL_DIR / "eval_report.json")).get("train_window", ["2016-03-01", tt]) if (MODEL_DIR / "eval_report.json").exists() else ["2016-03-01", tt]
    years = "".join(f'<line x1="{tl_x(f"{y}-01-01"):.0f}" y1="24" x2="{tl_x(f"{y}-01-01"):.0f}" y2="146" class="grid"/><text x="{tl_x(f"{y}-01-01"):.0f}" y="16" class="ax" text-anchor="middle">{y}</text>' for y in range(2013, 2027))
    bar = lambda y, a, b, color, label: (f'<rect x="{tl_x(a):.0f}" y="{y}" width="{max(tl_x(b) - tl_x(a), 2):.0f}" height="16" rx="5" fill="{color}"/>'  # noqa: E731
                                        f'<text x="{tl_x(a) + 6:.0f}" y="{y + 12}" class="bl" fill="#fff">{esc(label)}</text>')
    timeline = f'''<svg class="tl" viewBox="0 0 1000 176" role="img"><title>The record and the windows</title>{years}
      {bar(30, "2013-01-01", "2016-10-15", "#b8c4cc", "bay overflows, legacy record")}{bar(30, "2016-10-16", str(ev_span[1]), "#54576F", f"SFPUC's filed overflows (CIWQS): {n_events} events on {n_days} days")}
      {bar(52, "2016-03-19", "2017-01-10", "#7b5ea7", "2016-17 feed archive")}{bar(52, "2020-07-27", "2026-08-31", "#7b5ea7", "DataSF samples")}
      {bar(74, "2013-01-01", "2020-07-26", "#c4b5e6", "city lab export 2000–2020 (not yet read by the forecast)")}
      {bar(96, "2016-01-01", "2026-09-30", "#85BFDF", "rain: two NOAA gauges + ERA5 hourly")}
      {bar(122, tw[0], hs, BRAND, "stage 1 trained here")}{bar(122, hs, tt, "#237059", "holdout: never seen while choosing")}{bar(122, str(dt.date.fromisoformat(tt) + dt.timedelta(days=1)), "2026-09-30", "#d4763a", "since training: the forward test")}
      <text x="60" y="162" class="ax">Stage 1 was fit on {esc(fmt_month(tw[0]))} → {esc(fmt_month(hs))}, its settings chosen on the holdout {esc(fmt_month(hs))} → {esc(fmt_month(tt))}; every day after {esc(fmt_month(tt))} is a genuine forward test.</text></svg>'''
    lb = S["leaderboard"]
    n_rows = sum(len(b.get("rows", [])) for b in (lb.get("basins") or {}).values())
    retired = sv.get("replaced")

    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>How the forecast works — {esc(sv["name"])}</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/brand.css"><style>{CSS}</style></head><body>{sprite()}
<div class="wrap">
<header class="rh"><img src="/static/brand/bwtf_144x144.png" alt="" class="mark"><div><h1>How the forecast works</h1>
<p class="sub">The served set is <b>{esc(sv["name"])}</b>: stage 1 <b>{esc(sv["stage1"])}</b> (a weights model on 19 rain numbers) with stage 2 <b>{esc(sv["stage2"])}</b> (the outfall split), live corrections <b>{LR.VERSION}</b>, the <b>{esc(wx_label)}</b> weather model for the days ahead. Promoted {esc((sv.get("promoted_at") or "")[:10])}; replaced {esc(retired or "—")}.</p>
<div class="meta"><span>generated {esc(gen)}</span><span>trained through {esc(tt)}</span><span>holdout from {esc(hs)}</span><span><a href="/reports/2026-09_forecast_how_it_is_graded.html">how it is graded →</a></span></div></div></header>
<nav><a href="#picture">One picture</a><a href="#inputs">Inputs</a><a href="#stage1">Stage 1</a><a href="#stage2">Stage 2</a><a href="#live">Live corrections</a><a href="#outputs">Outputs</a><a href="#training">Training</a><a href="#more">Deeper</a></nav>

<section id="picture"><h2>Rain in, beach risk out</h2>
<div class="panel">{pipeline_svg(sv, wx_label)}</div>
<ul class="caps"><li><b>Do the gauges shape the live numbers?</b> Yes, first of all. Every complete past day is re-based onto the two NOAA gauges before anything runs; the SFO airport gauge fills today's hours so far and the peak-hour numbers of recent days; the weather model only fills the hours that have not happened yet.</li>
<li><b>Does a new weather model mean retraining?</b> No. The model reads inches. Swapping ICON for another model changes an input, never the fitted weights.</li>
<li><b>What if nothing is observed?</b> Then the live corrections are the identity and the page shows the model alone, to the digit.</li></ul>
<p class="lead">Rain that fell and rain to come become 19 numbers a day. Four basin models turn them into the chance the sewers overflow that day. Six beach groups turn overflow chances into days of beach risk. The city's live beach map and lab results then move the numbers where they have something to say. The page shows four zones, today and five days ahead.</p></section>

<section id="inputs"><h2>What comes in</h2><div class="grid3">{inputs}</div></section>

<section id="stage1"><h2>Stage 1 · will the sewers overflow?</h2>
<p class="lead">One model per combined-sewer basin, trained on the days SFPUC filed an overflow. Each is a weights model: the 19 numbers plus bends at a few rain amounts (the knots below), standardised, then an L2 logistic fit. Every basin reads the gauge that predicted it best on the holdout. The curves are the served models answering "how likely is an overflow today if this much falls?", once after a dry month and once after a wet start to the week. The bars are the weights themselves, each term's share of the model's total weight: warm raises the overflow odds, cool lowers them.</p>
<div class="grid2">{"".join(s1_cards)}</div>
<details class="more"><summary>The 19 numbers, and where the bends are</summary><table><tr><th>feature</th><th>plain meaning</th><th>bends at (inches)</th></tr>{feat_rows}</table>
<p class="fine">Peak-hour numbers for the curves above are set at their typical share of a wet day's total in the hourly record ({", ".join(f"{k.replace('rain_max', '')}: {v:.0%}" for k, v in ratios.items())}).</p></details></section>

<section id="stage2"><h2>Stage 2 · which beaches, and for how long?</h2>
<p class="lead">An overflow fouls a beach for days. For each beach group the city's own samples after past overflows give the chance the water is still over standard k days later, large and small overflows apart, with the dry-weather background removed. The outfall split (stage 2 {esc(sv["stage2"])}) first scales the basin's chance by the share of its overflows that reach the group at all: Ocean Beach and Aquatic Park do not see every overflow in their basin. The percentage on the page is the combined risk from the last eight days.</p>
<div class="grid3">{"".join(decay_cards)}</div>
<div class="card wide"><b>Worked example</b><p class="fine">Every basin at a 2% chance, except one day at 90% (a median-sized overflow). The day itself is a stay-out day; the days after decay along each group's curve, scaled by its share.</p>{ex_chart}</div></section>

<section id="live"><h2>Live corrections · {LR.VERSION}</h2>
<p class="lead">Once the forecast is running, what SFPUC's beach map and the city's lab results say can override the model, and only then: with nothing observed the output is the model alone, to the digit. Every change is recorded and shown on the page as a badge.</p>
<div class="grid2 rules">{rule_cards}</div>
<div class="card wide"><b>Where the sample results come from, and when</b>{lag_svg}</div></section>

<section id="outputs"><h2>What goes out</h2>
<div class="grid3">
<div class="card"><b>Four zones, six days</b><table class="plain"><tr><th>zone</th><th>beach groups</th><th class="num">stations</th></tr>{zone_rows}</table><p class="fine">A zone shows the worst of its groups; a basin the worst of its groups; the city the worst basin.</p></div>
<div class="card"><b>One number, four words</b><div class="levels">{level_rows}</div><p class="fine">The alarm line is <b>{int(round((sv.get("line") or 0.25) * 100))}%</b>: the Model check grades at it by default and the banner turns there. Per-zone lines are under review.</p></div>
<div class="card"><b>Every 30 minutes</b><p class="fine">A scheduler recomputes the forecast at :05 and :35, stores it, and the page always serves the stored copy. Each day's first and last forecast go to the history tables with the model's name, so a model swap never breaks the record. The Model check grades every stored day.</p><p class="fine">Since {esc((sv.get("promoted_at") or "")[:10])} the stamp reads {esc(sv["name"])}; before that {esc(retired or "—")}.</p></div>
</div></section>

<section id="training"><h2>How it was trained</h2>
<div class="panel">{timeline}</div>
<div class="grid3">
<div class="card"><b>The labels</b><p class="fine">SFPUC's filed overflow reports, one row per outfall per event: {n_events:,} events on {n_days} days, {esc(str(ev_span[0]))} → {esc(str(ev_span[1]))}, pulled from CIWQS each quarter. Bay-side basins also learn from the 2016-17 feed archive's flags; the Westside does not, because those flags lag the rain.</p></div>
<div class="card"><b>The choice</b><p class="fine">A leaderboard fit {n_rows} combinations of model family, rain gauge and regularisation per basin, judged by leave-one-season-out cross-validation before the holdout and then by holdout ranking. The weights model won every basin on the holdout; the outfall split was added as stage 2 {esc(sv["stage2"])}; the set was promoted on {esc((sv.get("promoted_at") or "")[:10])} on the analysis report's cost ranking.</p></div>
<div class="card"><b>What is kept</b><p class="fine">The retired set ({esc(retired or "—")}, gradient-boosted trees) and two other candidates stay on disk with their own scorecards, so the Model check can grade them on the same days. Nothing is retrained on the fly: the pickles change only at a promotion.</p></div>
</div></section>

<section id="more"><h2>Deeper</h2><p class="lead">
<a href="/reports/2026-09_forecast_how_it_is_graded.html">How it is graded</a> · <a href="/reports/2026-09_forecast_{esc(sv["name"])}_model_explorer.html">The served models opened up (weights, what-if editor)</a> · <a href="/reports/2026-09_forecast_stage2_explorer.html">Stage 2 explorer</a> · <a href="/reports/2026-09_model_analysis.html">Model analysis: which set to run</a> · <a href="/reports/2026-09_live_replay.html">Live corrections replay</a> · <a href="/reports/2026-09_live_replay_synthetic.html">Synthetic replay</a> · <a href="/reports/2026-09_weather_models.html">Which weather model</a> · <a href="/forecast">The forecast</a></p></section>
</div></body></html>'''
    return html


# ── report B: how it is graded ──────────────────────────────────────────────

def grade_set(sc: dict, label, thr_grid=LINE_GRID) -> dict:
    """Windows × rulers for one scorecard, the way the analysis report does it."""
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


def cost_of(conf_zone: dict, thr: float) -> tuple:
    c = conf_zone[str(thr)] if str(thr) in conf_zone else conf_zone[thr]
    if "fp" not in c and "vs_discharge_posting" in c:      # the discharge-only ruler nests its counts
        c = c["vs_discharge_posting"]
    return c["fp"] + MISS_WEIGHT * c["fn"], c


def zone_costs(conf: dict, thr: float) -> dict:
    return {z: cost_of(conf[z], thr)[0] for z in ZONE_ORDER if z in conf}


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
    line = sv.get("line") or 0.25
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

    # served results table (primary ruler) at the served line, oos and post
    def zone_table(w: dict, thr: float, title: str) -> str:
        rows, T = [], {"bad": 0, "tp": 0, "fn": 0, "fp": 0}
        for z in ZONE_ORDER:
            c = w["combined"][z][str(thr)]
            bad = c["tp"] + c["fn"]
            T["bad"] += bad; T["tp"] += c["tp"]; T["fn"] += c["fn"]; T["fp"] += c["fp"]
            best = min(LINE_GRID, key=lambda t: cost_of(w["combined"][z], t)[0])
            rows.append(f'<tr><td>{ic(ZONE_ICON[z])} <b>{esc(ZONE_LABEL[z])}</b></td><td class="num">{bad}</td><td class="num">{c["tp"]} <span class="mute">{pct(c["tp"] / bad) if bad else "—"}</span></td><td class="num">{c["fn"]}</td><td class="num">{c["fp"]} <span class="mute">({c.get("fp_sample", 0)} clean-sample · {c.get("fp_quiet", 0)} quiet)</span></td><td class="num"><b>{c["fp"] + MISS_WEIGHT * c["fn"]}</b></td><td class="num">{int(best * 100)}% <span class="mute">cost {cost_of(w["combined"][z], best)[0]}</span></td></tr>')
        rows.append(f'<tr class="total"><td>All zones</td><td class="num">{T["bad"]}</td><td class="num">{T["tp"]} <span class="mute">{pct(T["tp"] / T["bad"]) if T["bad"] else "—"}</span></td><td class="num">{T["fn"]}</td><td class="num">{T["fp"]}</td><td class="num"><b>{T["fp"] + MISS_WEIGHT * T["fn"]}</b></td><td></td></tr>')
        return f'<h3>{esc(title)}</h3><table><tr><th>zone</th><th class="num">bad days</th><th class="num">caught</th><th class="num">missed</th><th class="num">false alarms</th><th class="num">cost at {int(thr*100)}%</th><th class="num">cheapest line</th></tr>{"".join(rows)}</table>'

    served_tables = zone_table(g["oos"], line, f"Out of sample, {fmt_month(hs)} → {fmt_month(last)}: holdout-fit models through {fmt_month(tt)}, the served models after") + zone_table(g["post"], line, f"Since training only, {fmt_month(g['post']['start'])} → {fmt_month(last)}: the truest test, one wet season")

    # cost vs line per zone (oos, primary), served set
    xl = [f"{int(t*100)}" if i % 2 == 0 else "" for i, t in enumerate(LINE_GRID)]
    cost_charts = []
    for z in ZONE_ORDER:
        vals = [cost_of(g["oos"]["combined"][z], t)[0] for t in LINE_GRID]
        vals_d = [cost_of(g["oos"]["discharge"][z], t)[0] for t in LINE_GRID]
        ymax = max(max(vals), max(vals_d), 1) * 1.1
        cost_charts.append(f'<div class="card"><div class="bh"><b>{ic(ZONE_ICON[z])} {esc(ZONE_LABEL[z])}</b><span class="mute">cheapest {int(min(LINE_GRID, key=lambda t: cost_of(g["oos"]["combined"][z], t)[0]) * 100)}%</span></div>'
                           + svg_lines([{"label": "discharge days + samples", "color": BRAND, "values": vals}, {"label": "discharge days only", "color": "#8a949b", "values": vals_d, "dash": True}], xl, ymax=ymax, yfmt=lambda v: f"{v:.0f}", title="cost of each alarm line", xlab="alarm line, %", width=300, height=170) + "</div>")

    # candidates: total cost at the served line and at 50%, oos primary
    def total_cost(gs, w, thr):
        return sum(cost_of(gs[w]["combined"][z], thr)[0] for z in ZONE_ORDER)
    cand_rows = sorted(sets, key=lambda s: total_cost(G[s["name"]], "oos", line))
    cand_bars = svg_hbars([{"label": s["label"], "value": total_cost(G[s["name"]], "oos", line), "color": (BRAND if s["served"] else "#8a949b")} for s in cand_rows], title=f"cost at the {int(line*100)}% line, out of sample, all zones (lower is better)", width=640, label_w=210)
    cand50 = svg_hbars([{"label": s["label"], "value": total_cost(G[s["name"]], "oos", 0.5), "color": (BRAND if s["served"] else "#8a949b")} for s in sorted(sets, key=lambda s: total_cost(G[s["name"]], "oos", 0.5))], title="the same at a 50% line", width=640, label_w=210)
    best_line = {s["name"]: min(LINE_GRID, key=lambda t: total_cost(G[s["name"]], "oos", t)) for s in sets}
    cand_best = svg_hbars([{"label": f'{s["label"]} @ {int(best_line[s["name"]]*100)}%', "value": total_cost(G[s["name"]], "oos", best_line[s["name"]]), "color": (BRAND if s["served"] else "#8a949b")} for s in sorted(sets, key=lambda s: total_cost(G[s["name"]], "oos", best_line[s["name"]]))], title="each set at its own cheapest line", width=640, label_w=250)

    # stage 1 ranking: PR-AUC per basin, holdout and post, served vs others
    s1_rows = []
    for b in BASINS:
        cells = "".join(f'<td class="num">{(G[s["name"]]["holdout"]["basins"][b]["pr_auc"] or 0):.2f} / {(G[s["name"]]["post"]["basins"][b]["pr_auc"] or 0):.2f}</td>' for s in sets)
        s1_rows.append(f'<tr><td>{esc(BASIN_NAME[b])}</td><td class="num">{g["holdout"]["basins"][b]["n_events"]} / {g["post"]["basins"][b]["n_events"]}</td>{cells}</tr>')
    s1_table = f'<table><tr><th>basin</th><th class="num">overflow days (holdout / since)</th>{"".join(f"<th class=num>{esc(s['label'])}</th>" for s in sets)}</tr>{"".join(s1_rows)}</table><p class="fine">PR-AUC holdout / since training: how well the day\'s probability ranks overflow days above quiet ones (1.0 perfect). Sets sharing a stage 1 share these numbers.</p>'
    cal = calibration(sc, hs, last)
    cal_bars = svg_hbars([{"label": c["band"], "value": (c["rate"] or 0), "color": BRAND, } for c in cal], vmax=1.0, fmt=lambda v: f"{100*v:.0f}%", title="of the basin-days the model put in this band, the share that overflowed (out of sample)", width=560)
    cal_note = " · ".join(f'{c["band"]}: {c["hits"]} of {c["n"]}' for c in cal)

    # replays
    ra = json.load(open(REPO / "reports" / "2026-09_live_replay.json")) if (REPO / "reports" / "2026-09_live_replay.json").exists() else None
    rs = json.load(open(REPO / "reports" / "2026-09_live_replay_synthetic.json")) if (REPO / "reports" / "2026-09_live_replay_synthetic.json").exists() else None
    live_key = LR.VERSION
    replay_html = ""
    if ra and rs:
        a_plain, a_live = ra["variants"]["plain"]["grades"]["0.5"]["combined"]["bayside"], ra["variants"][live_key]["grades"]["0.5"]["combined"]["bayside"]
        s_plain, s_live, s_perf = (rs["variants"][k]["grades"] for k in ("plain", live_key, f"{live_key}_perfect_feed"))
        replay_html = f'''<div class="grid2">
          <div class="card"><b>2016-17 feed archive, bay-side beaches, 50% line</b>{svg_hbars([{"label": "model alone", "value": a_plain["cost"], "color": "#8a949b"}, {"label": f"with {live_key}", "value": a_live["cost"], "color": BRAND}], title="cost (a miss = 2 false alarms)", width=420)}<p class="fine">Bad days caught: {a_plain["tp"]} → {a_live["tp"]} of {a_plain["tp"] + a_plain["fn"]}. Real flags from the 2016-17 archive of SFPUC's feed, replayed one day at a time with only what was known that morning.</p></div>
          <div class="card"><b>Synthetic feed, every out-of-sample day, all zones</b>{svg_hbars([{"label": "model alone · 50%", "value": s_plain["0.5"]["combined"]["cost"], "color": "#8a949b"}, {"label": f"{live_key} · 50%", "value": s_live["0.5"]["combined"]["cost"], "color": BRAND}, {"label": "perfect feed · 50%", "value": s_perf["0.5"]["combined"]["cost"], "color": "#237059"}, {"label": "model alone · 25%", "value": s_plain["0.25"]["combined"]["cost"], "color": "#b8c4cc"}, {"label": f"{live_key} · 25%", "value": s_live["0.25"]["combined"]["cost"], "color": "#85BFDF"}], title="cost, mean of 5 draws", width=420)}<p class="fine">The filed overflows stand in for the feed, degraded with the archive\'s miss and lag rates. The rules help at 50%; at 25% the sample floors below 0.5 hurt, which is why they are off.</p></div></div>'''

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

    n_bad_oos = sum(g["oos"]["combined"][z][str(line)]["tp"] + g["oos"]["combined"][z][str(line)]["fn"] for z in ZONE_ORDER)
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>How the forecast is graded — {esc(served_name)}</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/brand.css"><style>{CSS}</style></head><body>{sprite()}
<div class="wrap">
<header class="rh"><img src="/static/brand/bwtf_144x144.png" alt="" class="mark"><div><h1>How the forecast is graded</h1>
<p class="sub">Every stored day is scored against what the city later filed and measured. The served set is <b>{esc(served_name)}</b>; its alarm line is <b>{int(line*100)}%</b>; a missed bad day costs <b>{MISS_WEIGHT}</b> false alarms. Nothing here uses a day the model saw while it was being chosen.</p>
<div class="meta"><span>generated {esc(gen)}</span><span>holdout {esc(fmt_month(hs))} → {esc(fmt_month(tt))}</span><span>since training {esc(fmt_month(g["post"]["start"]))} → {esc(fmt_month(last))}</span><span><a href="/reports/2026-09_forecast_how_it_works.html">← how it works</a></span></div></div></header>
<nav><a href="#rulers">Rulers</a><a href="#results">Results</a><a href="#lines">Lines</a><a href="#sets">Sets</a><a href="#stage1">Stage 1</a><a href="#live">Live corrections</a><a href="#weather">Weather input</a><a href="#caveats">Caveats</a></nav>

<section id="rulers"><h2>What counts as a bad day</h2>
<p class="lead">Three rulers, one week after an overflow. The primary one grades the percentage on what it claims: a beach fouled by an overflow. The overflow day is bad; a sample over standard in the week after is bad; a clean sample is good; a day nobody sampled is not graded; a dirty sample with no overflow in the prior week is dry-weather dirtiness the rain model does not claim.</p>
<div class="panel strips"><div class="weekhead"><span></span>{"".join(f"<span>{w}</span>" for w in week)}</div>{rulers}</div></section>

<section id="results"><h2>The served set, primary ruler</h2>
<p class="lead">{n_bad_oos} bad days out of sample. Caught means risk at or above the line that morning. False alarms are alarm days known clean: a clean sample, or a quiet day with no recent overflow. Cost = false alarms + {MISS_WEIGHT} × missed.</p>
{served_tables}</section>

<section id="lines"><h2>The line matters as much as the set</h2>
<p class="lead">Cost of every alarm line from 5% to 75%, out of sample, per zone. The East wants a low line, Ocean Beach a higher one. Dashed: the older ruler that charged the model for every day it stayed up after an overflow.</p>
<div class="grid4">{"".join(cost_charts)}</div></section>

<section id="sets"><h2>Every set on the same days</h2>
<p class="lead">The served set against the candidates kept on disk, graded on identical days and labels. At a fixed line the tree sets look cheaper at 50% and the weights sets at 25%: the trees say 60% on days the weights model says 30%, so the fair comparison is each set at its own cheapest line.</p>
<div class="grid2"><div class="card">{cand_bars}</div><div class="card">{cand50}</div><div class="card wide">{cand_best}</div></div></section>

<section id="stage1"><h2>Stage 1 on its own</h2>
<p class="lead">Graded on overflow days alone, where that label is exact.</p>
<div class="card wide">{s1_table}</div>
<div class="card wide"><b>Does 30% mean 30%?</b>{cal_bars}<p class="fine">{esc(cal_note)}. A well-calibrated model sits near the band it names.</p></div></section>

<section id="live"><h2>Live corrections, replayed</h2>
<p class="lead">The rules are graded by replaying past days with only what was known each morning: a day never sees its own flag or sample. The arithmetic is {live_key}'s; live_v1 graded identically, since only the source of the samples changed.</p>{replay_html}</section>

<section id="weather"><h2>The weather model, as an input</h2>{wx_html}</section>

<section id="caveats"><h2>Caveats</h2><ul>
<li><b>Small numbers.</b> {n_bad_oos} bad days out of sample, fourteen overflow days since training. A few storms decide a ranking; read differences of a handful of days as ties.</li>
<li><b>Most tail days are unsampled</b>, so the tail is judged on the days the city happened to sample, which is mostly the East. The 2000–2020 lab export can extend the samples ruler across the whole training span; it is the next grading job.</li>
<li><b>One line grades the whole page</b> while each zone's cheapest line differs. Per-zone lines are the open decision.</li>
<li><b>Holdout numbers flatter the weights model</b>: its settings were chosen there. The since-training table is the fairer one.</li>
<li><b>Postings are precautionary.</b> Against the signs the outfall split scores a small loss on Ocean Beach, where SFPUC posts after Sea Cliff-only overflows whose samples come back clean. The water could not settle it (one sampled case in ten years).</li>
</ul><p class="lead"><a href="/reports/2026-09_model_analysis.html">The full model analysis</a> · <a href="/reports/2026-09_forecast_how_it_works.html">How it works</a> · <a href="/forecast">The Model check, live</a></p></section>
</div></body></html>'''
    return html


CSS = """
.wrap{max-width:1280px;margin:0 auto;padding:24px}
.rh{display:flex;gap:18px;align-items:flex-start;margin-bottom:8px}.rh .mark{width:56px;height:56px;border-radius:14px}
h1{font-family:'Bebas Neue',sans-serif;font-weight:400;font-size:40px;letter-spacing:.04em;margin:0 0 6px;color:#26272a}
h2{font-family:'Bebas Neue',sans-serif;font-weight:400;font-size:28px;letter-spacing:.05em;margin:28px 0 8px;color:#26272a}
h3{font-size:16px;margin:18px 0 6px;color:#26272a}
.sub{font-size:16px;color:#54576F;margin:0 0 8px;max-width:900px}.lead{font-size:15px;color:#54576F;max-width:960px;margin:6px 0 12px}
.meta{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:#8a949b}
nav{display:flex;flex-wrap:wrap;gap:4px 14px;margin:10px 0 6px;font-size:14px;font-weight:600}nav a{color:#0072BC;text-decoration:none}nav a:hover{text-decoration:underline}
.panel{background:#fff;border:1px solid #d9e4e8;border-radius:24px;padding:16px;overflow-x:auto}
svg.pipe{display:block;width:100%;min-width:760px;height:auto;font-family:Roboto,Arial,sans-serif}
.box{fill:#fff;stroke:#d9e4e8;stroke-width:1.5}.box.hub{stroke:#0072BC;stroke-width:2}.tile{fill:#E3EBF2}.tile.logo{fill:#fff;stroke:#e3ebf2;stroke-width:1.5}
.icn{fill:none;stroke:#0072BC;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.t{font-size:15px;font-weight:700;fill:#26272a}.s{font-size:12.5px;fill:#54576F}.col{font-size:11px;font-weight:700;letter-spacing:.14em;fill:#8a949b}
.flow{fill:none;stroke:#0072BC;stroke-width:2}.dot{fill:#0072BC}.al{font-size:11px;font-weight:700;fill:#0072BC}.cap{font-size:12px;fill:#54576F}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
@media(max-width:1000px){.grid4{grid-template-columns:repeat(2,minmax(0,1fr))}.grid3{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:640px){.grid2,.grid3,.grid4{grid-template-columns:1fr}.rh{flex-direction:column}}
.card{background:#fff;border:1px solid #d9e4e8;border-radius:18px;padding:14px 16px;font-size:14px;color:#26272a}.card.wide{grid-column:1/-1}
.card b{display:block;font-size:15px;margin-bottom:4px}.card .bh{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.card .bh b{margin:0}
.card.src{display:flex;gap:12px;align-items:flex-start}.card.src img.lg{width:44px;height:44px;object-fit:contain;border:1px solid #e3ebf2;border-radius:11px;padding:4px;background:#fff;flex:0 0 auto}
.lgi{display:inline-flex;width:44px;height:44px;border-radius:11px;background:#E3EBF2;align-items:center;justify-content:center;flex:0 0 auto}.lgi .ic{width:24px;height:24px;color:#0072BC}
.card.rule{display:flex;gap:12px;align-items:flex-start}.card.rule p{margin:2px 0 6px;color:#54576F}
.srow{display:flex;justify-content:space-between;gap:10px;font-size:13px;padding:3px 0;border-top:1px solid #eef2f5}.srow span:first-child{color:#26272a}
.mute{color:#8a949b}.fine{font-size:12.5px;color:#54576F;margin:6px 0 0}
.kpis{display:flex;gap:14px;margin:8px 0 4px}.kpis div{display:flex;flex-direction:column}.kpis b{font-size:20px;margin:0;color:#0072BC}.kpis span{font-size:11.5px;color:#8a949b}
.chartbox{margin-top:6px}svg.chart{display:block;width:100%;height:auto;font-family:Roboto,Arial,sans-serif}.grid{stroke:#eef2f5}.ax{font-size:10.5px;fill:#8a949b}.ttl{font-size:12px;font-weight:700;fill:#54576F}.bl{font-size:12px;fill:#26272a}.bv{font-size:12px;font-weight:700;fill:#26272a}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:#54576F;margin-top:2px}.legend i{display:inline-block;width:14px;height:4px;border-radius:2px;margin-right:6px;vertical-align:middle}
table{width:100%;border-collapse:collapse;font-size:13.5px;background:#fff;border:1px solid #d9e4e8;border-radius:14px;overflow:hidden}
th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#54576F;padding:8px 10px;border-bottom:1px solid #d9e4e8;background:#f7fafc}td{padding:7px 10px;border-bottom:1px solid #eef2f5;vertical-align:top}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}tr.total td{font-weight:700;border-top:2px solid #d9e4e8}table.plain{border:0}.mono{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
details.more{margin-top:10px}details.more summary{cursor:pointer;font-weight:700;color:#0072BC}
.caps{margin:10px 0 0;padding:0 0 0 18px;color:#54576F;font-size:14px}.caps li{margin:4px 0}.caps b{color:#26272a}
.levels{display:flex;flex-direction:column;gap:6px;margin:8px 0}.lvl{display:flex;justify-content:space-between;font-size:14px;padding:6px 10px;background:#f7fafc;border-radius:10px}.lvl b{margin:0;font-size:14px}
svg.tl,svg.lag{display:block;width:100%;height:auto;font-family:Roboto,Arial,sans-serif}
.strips .weekhead{display:grid;grid-template-columns:220px repeat(8,1fr);gap:4px;font-size:11px;color:#8a949b;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:6px;text-align:center}
.strip{display:grid;grid-template-columns:220px 1fr;gap:4px;align-items:center;margin:4px 0}.strip b{font-size:13.5px}.cells{display:grid;grid-template-columns:repeat(8,1fr);gap:4px}
.cell{font-size:11.5px;text-align:center;padding:8px 4px;border-radius:8px;background:#f3f6f9;color:#54576F}.cell.bad{background:#f8dcd6;color:#b5310a;font-weight:700}.cell.good{background:#e0f0ea;color:#237059}.cell.na{background:#f3f6f9;color:#8a949b;font-style:italic}
.ic{width:1em;height:1em;vertical-align:-.15em;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
"""


def main():
    S = load_served()
    reg = registry(include_supabase=False)
    import importlib
    sys.path.insert(0, str(FORECAST))
    ld = importlib.import_module("live_dashboard") if False else None  # the engine is heavy; read the weather model from the source instead
    wx_model = re.search(r'"models":\s*"([a-z0-9_]+)"', (FORECAST / "live_dashboard.py").read_text()).group(1)
    OUT_WORKS.write_text(report_works(S, reg, wx_model))
    OUT_GRADED.write_text(report_graded(S))
    print(f"wrote {OUT_WORKS.relative_to(REPO)} ({OUT_WORKS.stat().st_size/1e3:.0f} KB) and {OUT_GRADED.relative_to(REPO)} ({OUT_GRADED.stat().st_size/1e3:.0f} KB); weather model {wx_model}; live {LR.VERSION}; served {S['info']['name']}")


if __name__ == "__main__":
    main()
