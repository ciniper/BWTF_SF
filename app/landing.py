"""Landing page — current conditions + overall CSO status + links to the feature pages.

Reuses the shared SFPUC client (status summary) and the optional weather/tide
context. Everything is wrapped defensively so a flaky upstream never blanks the
hub page. The markup lives in ``app/templates/landing.html`` (Jinja2).
"""
from datetime import datetime

from html import escape as _esc
from flask import render_template

BWTF_LOGO_URL = "https://bwtf.surfrider.org/images/BWTF-Logo_White.png"
SURFRIDER_LOGO_URL = "https://f.hubspotusercontent20.net/hubfs/20811975/SF-Horizontal-Logo_RGB_Black_crop_small.png"

# The nine feature pages, grouped into three hubs by the question a visitor is
# asking (Chase, 2026-09-28): is it safe today · what did the labs find · what
# did the city report. Each row is (href, title, blurb, static fact); the fact
# is replaced by a live one from _live_facts when a source answers in time.
# Icon keys map to the inline SVG sprite in _icons.html.
HUBS = [
    {
        "key": "today", "icon": "bell", "title": "Today & alerts",
        "sub": "Is the water safe, and tell me when it isn't",
        "primary": ("/signup", "Get beach alerts",
                    "Pick your beach areas and get one email when the water turns bad — a new posting or an active sewage discharge. Nothing else, ever."),
        "rows": [
            ("/forecast", "CSO Forecast",
             "Machine-learning forecast of combined-sewer-overflow risk from rainfall — a warning before discharges happen.",
             "risk from rain · next 3 days"),
            ("/alerts", "Sewage Alert System",
             "Live SFPUC status, bacteria readings, and real-time CSO alerts you can subscribe to by text or email.",
             "coordinators · live board"),
        ],
    },
    {
        "key": "water", "icon": "flask", "title": "The water record",
        "sub": "What the labs found",
        "primary": None,
        "rows": [
            ("/analysis", "Site Report Card",
             "How often each shoreline site fails the state bacteria standard — rankings, storm-season effect, and year-by-year trends from the city's lab data.",
             "rankings · trends"),
            ("/compare", "Source Comparison",
             "Surfrider volunteer-lab results vs. official city data for the same beaches, with full history.",
             "city vs Surfrider · every sample"),
            ("/bwtf", "BWTF Sample Log",
             "Every Surfrider volunteer sample — tester, field conditions (weather, tide, waves), and notes.",
             "volunteer samples · field notes"),
        ],
    },
    {
        "key": "record", "icon": "clipboard", "title": "Postings & discharges",
        "sub": "What the city reported, and when",
        "primary": None,
        "rows": [
            ("/discharges", "Discharge Ledger",
             "Every combined-sewer discharge SFPUC reported to regulators since 2016 — outfall, duration, and gallons — by location and year.",
             "filed with regulators · since 2016"),
            ("/postings", "Beach Postings",
             "Every beach advisory San Francisco filed with the State since 1999 — when each beach was posted, for how long, and why — from the State Water Board's BeachWatch record.",
             "filed with the State · since 1999"),
            ("/cso-history", "Online Postings Timeline",
             "Every posting and sewage-overflow flag the city's online map showed, as our real-time watcher saw it, plotted per station.",
             "as the feed showed them · since Aug 2026"),
        ],
    },
]

# Model pages and reports — for the curious, out of the public cards.
UNDER_THE_HOOD = [
    ("/forecast#check", "Model check"),
    ("/reports/2026-09_model_analysis.html", "Model analysis"),
    ("/reports/2026-09_live_replay.html", "Live corrections replay"),
    ("/reports/2026-09_live_replay_synthetic.html", "Synthetic replay"),
]

# Flat (href, icon, title, blurb) view of the same pages, kept for anything
# that wants the list (tests, the sitemap of this repo's docs).
PAGES = [(h["primary"][0], "mail", h["primary"][1], h["primary"][2]) for h in HUBS if h["primary"]] + [
    (href, {"today": "cloud-rain", "water": "chart", "record": "receipt"}[h["key"]], title, blurb)
    for h in HUBS for href, title, blurb, _fact in h["rows"]
]

FACTS_BUDGET_SECONDS = 2.5   # the landing page must stay quick; a slow source just keeps its static fact


def _fact_forecast() -> str:
    from features.forecast import page as fp
    row = fp._read_row()
    snap = (row or {}).get("snapshot") or {}
    days = [d for d in snap.get("predictions", {}).values() if isinstance(d, dict)]
    today = next((d for d in days if d.get("is_today")), None)
    if not today:
        return ""
    risk = max((float(v) for v in (today.get("zones") or {}).values()), default=None)
    if risk is None:
        return ""
    ahead = [max((float(v) for v in (d.get("zones") or {}).values()), default=0.0)
             for d in days if (d.get("day_offset") or 0) > 0]
    tail = " · rising" if ahead and max(ahead) >= max(risk + 0.10, 0.25) else ""
    return f"today {round(risk * 100)}% risk{tail}"


def _fact_samples() -> str:
    from shared import supabase as sb
    if not sb.is_configured():
        return ""
    rows = sb.select("samples", {"select": "sample_date,station_id,exceeds", "order": "sample_date.desc", "limit": 200}) or []
    if not rows:
        return ""
    newest = rows[0]["sample_date"]
    day = [r for r in rows if r["sample_date"] == newest]
    over = len({r["station_id"] for r in day if r.get("exceeds")})
    d = datetime.strptime(newest[:10], "%Y-%m-%d")
    return f"city samples {d:%-m/%-d}" + (f" · {over} site{'s' if over != 1 else ''} over" if over else " · all under standard")


def _fact_bwtf() -> str:
    from features.comparison.bwtf_api import SFBWTFClient
    lab = SFBWTFClient(timeout=3).fetch_lab()
    times = [s.latest_time for s in (lab.sites if lab else []) if s.latest_time]
    return f"last volunteer sample {max(times):%-m/%-d}" if times else ""


def _fact_discharges() -> str:
    import csv
    from features.discharges.page import _CSV
    with open(_CSV, newline="") as fh:
        last = max((r["event_date"] for r in csv.DictReader(fh) if r.get("event_date")), default="")
    if not last:
        return ""
    d = datetime.strptime(last[:10], "%Y-%m-%d")
    return f"filed with regulators · last {d:%b %Y}"


def _fact_timeline() -> str:
    from shared import supabase as sb
    if not sb.is_configured():
        return ""
    rows = sb.select("alert_log", {"select": "created_at,event_type", "event_type": "in.(posted,cso)",
                                   "simulated": "eq.false", "order": "created_at.desc", "limit": 1}) or []
    if not rows:
        return ""
    d = datetime.fromisoformat(str(rows[0]["created_at"]).replace("Z", "+00:00"))
    return f"last posting seen {d:%b %-d}"


_FACT_SOURCES = {
    "/forecast": _fact_forecast,
    "/compare": _fact_samples,
    "/bwtf": _fact_bwtf,
    "/discharges": _fact_discharges,
    "/cso-history": _fact_timeline,
}


def _live_facts(budget: float = FACTS_BUDGET_SECONDS, sources: dict | None = None) -> dict:
    """{href: fact} for every source that answered within the budget. Each
    source runs in its own thread and any failure or timeout just means the
    row keeps its static fact — the hub page never waits on a slow upstream."""
    from concurrent.futures import ThreadPoolExecutor, wait
    sources = _FACT_SOURCES if sources is None else sources
    if not sources:
        return {}
    out: dict = {}
    pool = ThreadPoolExecutor(max_workers=len(sources), thread_name_prefix="landing-fact")
    futures = {pool.submit(fn): href for href, fn in sources.items()}
    done, _pending = wait(futures, timeout=budget)
    for fut in done:
        try:
            text = fut.result()
            if text:
                out[futures[fut]] = text
        except Exception:
            pass
    pool.shutdown(wait=False, cancel_futures=True)
    return out


def hubs_with_facts(facts: dict | None) -> list[dict]:
    """HUBS with each row as a dict, the live fact substituted where one came back."""
    facts = facts or {}
    rendered = []
    for h in HUBS:
        rows = [{"href": href, "title": title, "blurb": blurb,
                 "fact": facts.get(href, static), "live": href in facts}
                for href, title, blurb, static in h["rows"]]
        rendered.append({**h, "rows": rows})
    return rendered


def _status_banner(summary: dict) -> tuple[str, str]:
    cso = summary.get("cso_active_count", 0)
    posted = summary.get("posted_count", 0)
    safe = summary.get("safe_count", 0)
    cso_locs = summary.get("cso_locations", [])
    if cso:
        sites = _esc(", ".join(cso_locs)) if cso_locs else f"{cso} site(s)"
        return ("danger", f'<svg class="ic"><use href="#i-octagon-alert"/></svg> Active combined-sewer-overflow discharge at: {sites}. Avoid water contact.')
    if posted:
        return ("warn", f'<svg class="ic"><use href="#i-triangle-alert"/></svg> {posted} site(s) posted for elevated bacteria. {safe} site(s) currently meeting standards.')
    if safe:
        return ("ok", f'<svg class="ic"><use href="#i-circle-check"/></svg> No active CSO discharge. {safe} site(s) meeting California water-quality standards.')
    return ("warn", "Status is loading or temporarily unavailable — open the alert page for details.")


def _conditions_lines(env_context) -> list[str]:
    lines = []
    if not env_context:
        return lines
    try:
        rain = env_context.weather.get_rain_advisory()
        if rain.is_active:
            lines.append(f'<svg class="ic"><use href="#i-cloud-rain"/></svg> Rain advisory active — CSO risk {rain.cso_risk.upper()} '
                         f"({rain.total_recent_inches:.2f}\" recent)")
        elif rain.upcoming_rain:
            lines.append('<svg class="ic"><use href="#i-cloud-sun-rain"/></svg> Rain in the forecast — watch for elevated CSO risk')
        else:
            lines.append('<svg class="ic"><use href="#i-circle-check"/></svg> No recent or forecast rain — conditions favorable')
    except Exception:
        pass
    try:
        tide = env_context.tides.get_tide_info()
        if tide:
            arrow = '<svg class="ic"><use href="#i-trending-up"/></svg> rising' if tide.current_trend == "rising" else '<svg class="ic"><use href="#i-trending-down"/></svg> falling'
            nxt = ""
            if tide.next_high:
                nxt = f" · next high {_esc(tide.next_high.time.strftime('%-I:%M %p'))}"
            lines.append(f'<svg class="ic"><use href="#i-waves"/></svg> Tide {arrow}{nxt}')
    except Exception:
        pass
    return lines


def render_landing(sfpuc_api, env_context=None, live_facts: bool = True) -> str:
    try:
        summary = sfpuc_api.get_status_summary()
    except Exception:
        summary = {}

    tone, message = _status_banner(summary)
    conditions = _conditions_lines(env_context)
    generated = datetime.now().strftime("%B %-d, %Y at %-I:%M %p")
    facts = _live_facts() if live_facts else {}

    return render_template(
        "landing.html",
        bwtf_logo=BWTF_LOGO_URL,
        tone=tone,
        message=message,
        conditions=conditions,
        hubs=hubs_with_facts(facts),
        hood=UNDER_THE_HOOD,
        pages=PAGES,
        generated=generated,
    )
