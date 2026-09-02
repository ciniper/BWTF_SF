"""Landing page — current conditions + overall CSO status + links to the feature pages.

Reuses the shared SFPUC client (status summary) and the optional weather/tide
context. Everything is wrapped defensively so a flaky upstream never blanks the
hub page. The markup lives in ``app/templates/landing.html`` (Jinja2).
"""
from datetime import datetime

from flask import render_template

BWTF_LOGO_URL = "https://bwtf.surfrider.org/images/BWTF-Logo_White.png"
SURFRIDER_LOGO_URL = "https://f.hubspotusercontent20.net/hubfs/20811975/SF-Horizontal-Logo_RGB_Black_crop_small.png"

# (href, icon key, title, blurb) for the feature cards. Icon keys map to the
# inline SVG sprite in landing.html — SVGs render identically on every OS,
# unlike emoji (Apple/Segoe/Noto each draw their own artwork).
PAGES = [
    ("/signup", "mail", "Get Beach Alerts",
     "Pick your beach areas and get one email when the water turns bad — a new posting or an active sewage discharge. Nothing else, ever."),
    ("/alerts", "bell", "Sewage Alert System",
     "Live SFPUC status, bacteria readings, and real-time CSO alerts you can subscribe to by text or email."),
    ("/forecast", "cloud-rain", "CSO Forecast",
     "Machine-learning forecast of combined-sewer-overflow risk from rainfall — a warning before discharges happen."),
    ("/compare", "scales", "Source Comparison",
     "Surfrider volunteer-lab results vs. official city data for the same beaches, with full history."),
    ("/bwtf", "flask", "BWTF Sample Log",
     "Every Surfrider volunteer sample — tester, field conditions (weather, tide, waves), and notes."),
    ("/cso-history", "pulse", "CSO Event Timeline",
     "Every posting and sewage-overflow event our real-time watcher has detected, plotted per station."),
    ("/analysis", "chart", "Site Report Card",
     "How often each shoreline site fails the state bacteria standard — rankings, storm-season effect, and year-by-year trends from the city's lab data."),
    ("/discharges", "receipt", "Discharge Ledger",
     "Every combined-sewer discharge SFPUC reported to regulators since 2016 — outfall, duration, and gallons — by location and year."),
]


def _status_banner(summary: dict) -> tuple[str, str]:
    cso = summary.get("cso_active_count", 0)
    posted = summary.get("posted_count", 0)
    safe = summary.get("safe_count", 0)
    cso_locs = summary.get("cso_locations", [])
    if cso:
        sites = ", ".join(cso_locs) if cso_locs else f"{cso} site(s)"
        return ("danger", f"🚨 Active combined-sewer-overflow discharge at: {sites}. Avoid water contact.")
    if posted:
        return ("warn", f"⚠️ {posted} site(s) posted for elevated bacteria. {safe} site(s) currently meeting standards.")
    if safe:
        return ("ok", f"✅ No active CSO discharge. {safe} site(s) meeting California water-quality standards.")
    return ("warn", "Status is loading or temporarily unavailable — open the alert page for details.")


def _conditions_lines(env_context) -> list[str]:
    lines = []
    if not env_context:
        return lines
    try:
        rain = env_context.weather.get_rain_advisory()
        if rain.is_active:
            lines.append(f"🌧️ Rain advisory active — CSO risk {rain.cso_risk.upper()} "
                         f"({rain.total_recent_inches:.2f}\" recent)")
        elif rain.upcoming_rain:
            lines.append("🌦️ Rain in the forecast — watch for elevated CSO risk")
        else:
            lines.append("☀️ No recent or forecast rain — conditions favorable")
    except Exception:
        pass
    try:
        tide = env_context.tides.get_tide_info()
        if tide:
            arrow = "📈 rising" if tide.current_trend == "rising" else "📉 falling"
            nxt = ""
            if tide.next_high:
                nxt = f" · next high {tide.next_high.time.strftime('%-I:%M %p')}"
            lines.append(f"🌊 Tide {arrow}{nxt}")
    except Exception:
        pass
    return lines


def render_landing(sfpuc_api, env_context=None) -> str:
    try:
        summary = sfpuc_api.get_status_summary()
    except Exception:
        summary = {}

    tone, message = _status_banner(summary)
    conditions = _conditions_lines(env_context)
    generated = datetime.now().strftime("%B %-d, %Y at %-I:%M %p")

    return render_template(
        "landing.html",
        bwtf_logo=BWTF_LOGO_URL,
        tone=tone,
        message=message,
        conditions=conditions,
        pages=PAGES,
        generated=generated,
    )
