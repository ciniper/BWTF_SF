"""Landing page — current conditions + overall CSO status + links to the 3 pages.

Reuses the shared SFPUC client (status summary) and the optional weather/tide
context. Everything is wrapped defensively so a flaky upstream never blanks the
hub page.
"""
from datetime import datetime

BWTF_LOGO_URL = "https://bwtf.surfrider.org/images/BWTF-Logo_White.png"
SURFRIDER_LOGO_URL = "https://f.hubspotusercontent20.net/hubfs/20811975/SF-Horizontal-Logo_RGB_Black_crop_small.png"

# (href, icon, title, blurb) for the three feature cards
PAGES = [
    ("/alerts", "🚨", "Sewage Alert System",
     "Live SFPUC status, bacteria readings, and real-time CSO alerts you can subscribe to by text or email."),
    ("/forecast", "🔮", "CSO Forecast",
     "Machine-learning forecast of combined-sewer-overflow risk from rainfall — a warning before discharges happen."),
    ("/compare", "⚖️", "Source Comparison",
     "Surfrider volunteer-lab results vs. official city data for the same beaches, with full history."),
]


def _status_banner(summary: dict) -> str:
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
    cond_html = (
        "<div class='conditions'>" + "".join(f"<span>{c}</span>" for c in conditions) + "</div>"
        if conditions else ""
    )

    cards = ""
    for href, icon, title, blurb in PAGES:
        cards += f"""
        <a class="card" href="{href}">
          <div class="card-icon">{icon}</div>
          <div class="card-title">{title}</div>
          <div class="card-blurb">{blurb}</div>
          <div class="card-go">Open →</div>
        </a>"""

    generated = datetime.now().strftime("%B %-d, %Y at %-I:%M %p")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SF Beach Water Quality — Surfrider SF Blue Water Task Force</title>
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;background:#f5f6f7;color:#26272a;font-family:'Avenir Next','Trebuchet MS','Segoe UI',sans-serif}}
  .wrap{{max-width:1040px;margin:0 auto;padding:24px}}
  .hero{{background:linear-gradient(135deg,#26272a 0%,#317fb2 100%);color:#fff;border-radius:24px;padding:30px 28px}}
  .kicker{{display:inline-block;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);border-radius:999px;padding:7px 12px;font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}}
  .hero h1{{margin:14px 0 6px;font-size:30px;letter-spacing:.02em}}
  .hero .sub{{margin:0;color:rgba(255,255,255,.85);font-size:15px}}
  .hero-logos{{float:right;display:flex;gap:12px;align-items:center}}
  .hero-logos img{{height:40px;width:auto;opacity:.95}}
  .status{{margin:18px 0 0;border-radius:16px;padding:16px 18px;font-weight:600;font-size:15px;line-height:1.45}}
  .status.ok{{background:rgba(37,214,112,.16);color:#0c5b2e}}
  .status.warn{{background:rgba(251,192,45,.20);color:#7a5a00}}
  .status.danger{{background:rgba(255,65,0,.16);color:#fff;background:#b5310a}}
  .conditions{{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 0}}
  .conditions span{{background:rgba(255,255,255,.14);border-radius:999px;padding:7px 13px;font-size:13px;color:#fff}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-top:22px}}
  .card{{display:block;background:#fff;border:1px solid #d9e4e8;border-radius:20px;padding:22px;text-decoration:none;color:#26272a;transition:transform .12s ease,box-shadow .12s ease}}
  .card:hover{{transform:translateY(-3px);box-shadow:0 14px 30px rgba(38,39,42,.12);border-color:#317fb2}}
  .card-icon{{font-size:34px}}
  .card-title{{font-size:19px;font-weight:800;margin:10px 0 6px}}
  .card-blurb{{color:#5e6a71;font-size:14px;line-height:1.5}}
  .card-go{{margin-top:14px;color:#317fb2;font-weight:700;font-size:14px}}
  .foot{{color:#8a949b;font-size:12px;margin-top:22px;line-height:1.6}}
  .foot a{{color:#317fb2}}
</style></head>
<body><div class="wrap">
  <div class="hero">
    <div class="hero-logos"><img src="{BWTF_LOGO_URL}" alt="Blue Water Task Force"></div>
    <span class="kicker">Surfrider Foundation • Blue Water Task Force</span>
    <h1>San Francisco Beach Water Quality</h1>
    <p class="sub">Real-time sewage-overflow status, forecasting, and monitoring for SF beaches.</p>
    <div class="status {tone}">{message}</div>
    {cond_html}
  </div>

  <div class="cards">{cards}
  </div>

  <p class="foot">
    Updated {generated}. Beach hotline: 1-877-SFBEACH (1-877-732-3224).<br>
    <a href="https://webapps.sfpuc.org/sapps/beachesandbay.html" target="_blank" rel="noopener">SFPUC Beach Map</a> ·
    <a href="https://sf.surfrider.org/programs/blue-water-task-force" target="_blank" rel="noopener">Surfrider SF BWTF</a>
  </p>
</div></body></html>"""
