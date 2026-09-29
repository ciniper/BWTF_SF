"""Landing page — current conditions + overall CSO status + links to the feature pages.

Reuses the shared SFPUC client (status summary) and the optional weather/tide
context. Everything is wrapped defensively so a flaky upstream never blanks the
hub page. The markup lives in ``app/templates/landing.html`` (Jinja2).
"""
from datetime import datetime

from html import escape as _esc
from flask import render_template

from shared.zones import ZONES, ZONE_OF_STATION

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
                    "Pick your beach areas and get an email when the city posts one for bacteria or a sewage discharge. No digest, no marketing."),
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
            ("/samples", "Samples",
             "Every published result from the city's lab and Surfrider's volunteers, one row per sample, with the volunteers' field notes.",
             "every result · both programs"),
            ("/graphs", "Graphs",
             "Any site over time: all three indicators on one plot with the state limits drawn in, or the city against Surfrider head to head.",
             "any site · all indicators", ("/compare", "Source Comparison: the six dual beaches, head to head")),
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

def nav_model() -> list[dict]:
    """The three hubs for the shared two-tier tab bar (app/templates/_frame.html):
    row one is Main + the hubs, row two the active hub's pages as tabs (Chase,
    2026-09-29: "almost like they are within the same main page"). Each hub opens
    on its first page; a row's subpage (Source Comparison) gets its own tab; the
    Today hub's signup CTA closes its row. ``paths`` light the hub. Set as the
    Jinja global ``NAV`` in app/wsgi.py."""
    out = []
    for h in HUBS:
        rows = []
        for r in h["rows"]:
            rows.append({"href": r[0], "title": r[1], "fact": r[3]})
            if len(r) > 4:
                rows.append({"href": r[4][0], "title": r[4][1].split(":")[0], "fact": ""})
        if h["primary"]:
            rows.append({"href": h["primary"][0], "title": h["primary"][1], "fact": ""})
        out.append({"key": h["key"], "title": h["title"], "href": rows[0]["href"], "rows": rows, "paths": [r["href"] for r in rows]})
    return out


# Model pages and reports — for the curious, out of the public cards.
UNDER_THE_HOOD = [
    ("/architecture", "How it's built"),
    ("/records", "How we get the records"),
    ("/forecast#check", "Model check"),
    ("/reports/2026-09_model_analysis.html", "Model analysis"),
    ("/reports/2026-09_live_replay.html", "Live corrections replay"),
    ("/reports/2026-09_live_replay_synthetic.html", "Synthetic replay"),
]

# Flat (href, icon, title, blurb) view of the same pages, kept for anything
# that wants the list (tests, the sitemap of this repo's docs).
PAGES = [(h["primary"][0], "mail", h["primary"][1], h["primary"][2]) for h in HUBS if h["primary"]] + [
    (r[0], {"today": "cloud-rain", "water": "chart", "record": "receipt"}[h["key"]], r[1], r[2])
    for h in HUBS for r in h["rows"]
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
    """Newest city sample day (and sites over) from the mirror, newest Surfrider sample from BWTF."""
    parts = []
    try:
        from shared import supabase as sb
        rows = sb.select("samples", {"select": "sample_date,station_id,exceeds", "order": "sample_date.desc", "limit": 200}) if sb.is_configured() else []
        if rows:
            newest = rows[0]["sample_date"]
            over = len({r["station_id"] for r in rows if r["sample_date"] == newest and r.get("exceeds")})
            d = datetime.strptime(newest[:10], "%Y-%m-%d")
            parts.append(f"city {d:%-m/%-d}" + (f" · {over} over" if over else ""))
    except Exception:
        pass
    try:
        from features.comparison.bwtf_api import SFBWTFClient
        lab = SFBWTFClient(timeout=3).fetch_lab()
        times = [s.latest_time for s in (lab.sites if lab else []) if s.latest_time]
        if times:
            parts.append(f"Surfrider {max(times):%-m/%-d}")
    except Exception:
        pass
    return " · ".join(parts)


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


def _forecast_risks() -> dict:
    """Today's overflow risk per zone (0–100) and the days ahead, from the forecast cache row —
    what the Today board's zone tiles show. Empty dict when there is no snapshot."""
    from features.forecast import page as fp
    row = fp._read_row()
    snap = (row or {}).get("snapshot") or {}
    days = sorted((d for d in snap.get("predictions", {}).values() if isinstance(d, dict)), key=lambda d: d.get("day_offset") or 0)
    today = next((d for d in days if d.get("is_today")), None)
    if not today or not isinstance(today.get("zones"), dict):
        return {}
    zones = {k: round(float(v) * 100) for k, v in today["zones"].items() if isinstance(v, (int, float))}
    ahead = [(d.get("label") or d.get("date") or "", round(max((float(v) for v in (d.get("zones") or {}).values()), default=0.0) * 100))
             for d in days if (d.get("day_offset") or 0) > 0]
    return {"zones": zones, "ahead": ahead}


_FACT_SOURCES = {
    "_forecast": _forecast_risks,
    "/forecast": _fact_forecast,
    "/samples": _fact_samples,
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
        rows = [{"href": r[0], "title": r[1], "blurb": r[2],
                 "fact": facts.get(r[0], r[3]), "live": r[0] in facts,
                 "sub": r[4] if len(r) > 4 else None}   # optional (href, label): a subpage under this row
                for r in h["rows"]]
        rendered.append({**h, "rows": rows})
    return rendered


_SHORT = (("Ocean Beach at ", "OB "), ("Baker Beach at Lobos Creek", "Baker Lobos"), ("Baker Beach ", "Baker "), ("Crissy Field ", "Crissy "), (" Street Pier", " St Pier"))


def _short(name: str) -> str:
    for a, b in _SHORT:
        name = name.replace(a, b)
    return name


def _station_status(st) -> str:
    """safe / posted / discharge / unknown from an SFPUC station object (status enum or string)."""
    if getattr(st, "has_cso", False):
        return "discharge"
    v = getattr(getattr(st, "status", None), "value", getattr(st, "status", None))
    return {"safe": "safe", "posted": "posted"}.get(str(v), "unknown")


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def today_board(stations: list, risks: dict | None = None, samples_fact: str = "", now: datetime | None = None) -> dict:
    """Pure: the landing page's first screen. ``stations`` are SFPUC station
    objects (station_id = SFPUC id, station_name, status, has_cso, sample_date);
    ``risks`` is _forecast_risks(); ``samples_fact`` the samples row's live fact.
    Returns the headline (plain + HTML), the lead sentence, a tone, and one tile
    per zone with its stations' statuses (Chase, 2026-09-29: lead with the answer)."""
    now = now or datetime.now()
    risks = risks or {}
    by_zone: dict[str, list] = {k: [] for k in ZONES}
    for st in stations:
        zk = ZONE_OF_STATION.get(str(getattr(st, "station_id", "")))
        if zk:
            by_zone[zk].append(st)
    status_text = {"safe": "safe", "posted": "posted for bacteria", "discharge": "sewage discharge", "unknown": "not sampled"}
    tiles, posted, discharging, n_safe, n_graded = [], [], [], 0, 0
    for zk, z in ZONES.items():
        sts = [{"name": _short(st.station_name), "status": _station_status(st), "status_text": status_text[_station_status(st)]} for st in by_zone[zk]]
        z_posted = [x["name"] for x in sts if x["status"] == "posted"]
        z_cso = [x["name"] for x in sts if x["status"] == "discharge"]
        z_safe = sum(1 for x in sts if x["status"] == "safe")
        posted += z_posted; discharging += z_cso; n_safe += z_safe; n_graded += z_safe + len(z_posted) + len(z_cso)
        status = "discharge" if z_cso else "posted" if z_posted else "safe" if z_safe else "unknown"
        text = ("Sewage discharge" if status == "discharge" else f"{len(z_posted)} beach{'es' if len(z_posted) > 1 else ''} posted" if status == "posted"
                else "All clear" if status == "safe" else "Not sampled")
        dates = [st.sample_date for st in by_zone[zk] if getattr(st, "sample_date", None)]
        sampled = f"sampled {max(dates):%b %-d}" if dates else "no sample date"
        meta = (" · ".join(z_cso + z_posted) + f" · {sampled}") if (z_cso or z_posted) else f"{len(sts)} stations · {sampled}"
        tiles.append({"key": zk, "label": z.label, "status": status, "status_text": text, "risk": (risks.get("zones") or {}).get(zk),
                      "stations": sts, "meta": meta})
    if discharging:
        tone, headline = "danger", f"Sewage discharge at {_join(discharging)}."
        headline_html = f"<em>{_esc(headline)}</em>"
    elif n_graded == 0:
        tone, headline = "warn", "Beach status is loading or unavailable right now."
        headline_html = _esc(headline)
    elif posted:
        tone = "warn"
        tail = f"{_join(posted)} {'is' if len(posted) == 1 else 'are'} posted." if len(posted) <= 2 else f"{len(posted)} beaches are posted."
        headline = f"Water's fine at {n_safe} of {n_graded} beaches. {tail}"
        headline_html = f"Water's fine at {n_safe} of {n_graded} beaches. <em>{_esc(tail)}</em>"
    else:
        tone, headline = "ok", f"Water's fine at all {n_graded} beaches."
        headline_html = _esc(headline)
    lead = []
    if discharging:
        lead.append("Avoid water contact there, and for 72 hours after it ends.")
    elif n_graded:
        lead.append("No sewage discharge anywhere.")
    zr = risks.get("zones") or {}
    if zr:
        worst = max(zr.values())
        where = "in every zone" if len(set(zr.values())) == 1 else f"at most, in {ZONES[max(zr, key=zr.get)].label}"
        ahead = risks.get("ahead") or []
        if ahead and max(p for _, p in ahead) < 10:
            trend = f", and it stays low through {ahead[-1][0]}"
        elif ahead and max(p for _, p in ahead) >= worst + 10:
            lbl, pk = max(ahead, key=lambda t: t[1]); trend = f", rising to {pk}% by {lbl}"
        else:
            trend = ""
        lead.append(f"Overflow risk today is {worst}% {where}{trend}.")
    if samples_fact:
        lead.append("Latest samples: " + samples_fact.replace("city", "city lab").replace("Surfrider", "Surfrider volunteers") + ".")
    return {"tone": tone, "headline": headline, "headline_html": headline_html, "lead": " ".join(lead), "zones": tiles,
            "posted": posted, "discharging": discharging, "n_safe": n_safe, "n_graded": n_graded,
            "date": now.strftime("%a %b %-d"), "checked": now.strftime("%-I:%M %p")}


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
        stations = list(sfpuc_api.fetch_stations()) if hasattr(sfpuc_api, "fetch_stations") else []
    except Exception:
        stations = []
    try:
        summary = sfpuc_api.get_status_summary() if not stations else {}
    except Exception:
        summary = {}

    conditions = _conditions_lines(env_context)
    generated = datetime.now().strftime("%B %-d, %Y at %-I:%M %p")
    facts = _live_facts() if live_facts else {}
    board = today_board(stations, facts.get("_forecast"), facts.get("/samples", ""))
    if not stations and summary:   # a client that only knows the summary: keep the old banner sentence as the headline
        board["tone"], message = _status_banner(summary)
        board["headline"] = board["headline_html"] = message.split("</svg> ", 1)[-1]

    return render_template(
        "landing.html",
        bwtf_logo=BWTF_LOGO_URL,
        board=board,
        conditions=conditions,
        hubs=hubs_with_facts(facts),
        hood=UNDER_THE_HOOD,
        pages=PAGES,
        generated=generated,
    )
