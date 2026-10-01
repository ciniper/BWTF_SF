"""Running costs by subscriber count — the admin dashboard's Running costs tab (/alerts/costs).

One model, three kinds of input, each named where it comes from:

  PRICES    published prices, each with its source page and the day it was checked
  rates()   alerts per subscriber a year, counted from the records: one alert per station
            posting (State Water Board BeachWatch) and one per station discharge flag (SFPUC's
            CIWQS reports, through the outfall registry), per zone, over the complete years
            both records cover. The site merges changes it sees in the same minute into one
            message, so this is the high end; a count of alert DAYS is the low end.
  MEASURED  what the site uses per message and per visit (2026-10-01), plus the planning
            assumptions that are guesses and are labelled as such.

Text-message segments and stored email sizes are measured from the live renderer
(features/alerts/render._fallback), so the model follows the alert wording when it changes.

Plan decisions are Chase's (2026-10-01): Brevo goes paid from 50 subscribers; Vercel Pro,
Supabase Pro and a domain from 100. Everything is free below that.
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Optional

from shared import supabase as sb
from shared.clock import today_pacific
from shared.outfalls import OUTFALLS
from shared.zones import ZONE_OF_STATION, ZONES

PRICES_CHECKED = "2026-10-01"
BREVO_PAID_FROM = 50      # subscribers (Chase, 2026-10-01)
PRO_FROM = 100            # subscribers: Vercel Pro, Supabase Pro and a domain (Chase, 2026-10-01)
STEPS = [10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000]

PRICES = {
    "brevo": {"tiers": [(5_000, 9), (10_000, 17), (20_000, 32), (50_000, 56), (100_000, 82), (250_000, 249), (500_000, 429)],
              "starter_max": 100_000, "nonprofit_discount": 0.20, "free_per_day": 300,
              "source": "https://www.brevo.com/pricing/", "note": "monthly price by monthly email volume; Starter to 100k, Standard above; 20% off for nonprofits"},
    "ses": {"per_1000": 0.10, "source": "https://aws.amazon.com/ses/pricing/", "note": "à la carte plan; new accounts default to Essentials at $0.16"},
    "twilio": {"per_segment": 0.0083, "carrier_fee": statistics.mean([0.0035, 0.0045, 0.0050]), "toll_free_number": 2.15,
               "source": "https://www.twilio.com/en-us/sms/pricing/us", "note": "toll-free number; carrier fees AT&T $0.0035, T-Mobile $0.0045, Verizon $0.005 a segment"},
    "vercel": {"pro": 20.0, "credit": 20.0, "per_m_invocations": 0.60, "cpu_hour": 0.128, "gb_hour": 0.0106, "per_gb_transfer": 0.15,
               "source": "https://vercel.com/docs/plans/pro-plan", "note": "$20 a month with $20 of usage included"},
    "supabase": {"pro": 25.0, "included_gb": 8, "per_gb_month": 0.125, "included_egress_gb": 250, "per_gb_egress": 0.09,
                 "source": "https://supabase.com/pricing", "note": "Pro includes 8 GB of disk and 250 GB of egress"},
    "arcgis": {"free_tiles": 2_000_000, "per_1000_tiles": 0.15, "source": "https://location.arcgis.com/pricing/",
               "note": "ArcGIS Location Platform; needs a free API key"},
    "domain": {"per_year": 19.18, "source": "https://www.namecheap.com/domains/registration/gtld/org/", "note": ".org renewal plus the ICANN fee"},
}

MEASURED = {   # 2026-10-01; the guesses say so
    "home_db_read_kb": 51,             # forecast row (read twice) + samples rows, per home page view
    "forecast_refreshes_month": 1_440,  # pg_cron, every 30 minutes
    "refresh_seconds": 19,             # wall time of one refresh (generated_at → last_refresh)
    "refresh_cpu_seconds": 5,          # guess: most of a refresh waits on weather and city APIs
    "view_cpu_seconds": 0.1,           # guess: rendering, not waiting
    "view_wall_seconds": 1.5,
    "function_gb": 2,
    "invocations_per_visit": 2,        # the page, plus the odd asset now that static files cache for a year
    "transfer_mb_per_visit": 0.2,
    "tiles_per_visit": 20,             # guess: a map screen plus a zone tap or two
    "visits_per_subscriber_month": 3,  # guess
    "public_visits_month": 2_000,      # guess
    "database_base_gb": 0.05,
}


# ── what a message costs to send and to keep ────────────────────────────────

_GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
_GSM7_EXT = set("^{}\\[~]|€")   # two septets each


def sms_segments(text: str) -> tuple[str, int]:
    """(encoding, billable segments) for one text. GSM-7: 160 alone, 153 a part. Any character
    outside it — an em dash, a curly quote — switches the whole text to UCS-2: 70 alone, 67 a part."""
    if all(c in _GSM7 or c in _GSM7_EXT for c in text):
        n = len(text) + sum(1 for c in text if c in _GSM7_EXT)
        return "GSM-7", 1 if n <= 160 else -(-n // 153)
    return "UCS-2", 1 if len(text) <= 70 else -(-len(text) // 67)


# What a typical year's alerts look like: most name one or two beaches, a storm names six.
_SAMPLES = [
    (0.50, [("4602", "Ocean Beach at Sloat Boulevard", "posted")]),
    (0.35, [("4602", "Ocean Beach at Sloat Boulevard", "posted"), ("4619", "Islais Creek", "cso")]),
    (0.15, [("4602", "Ocean Beach at Sloat Boulevard", "posted"), ("4619", "Islais Creek", "cso"), ("4618", "Mission Creek", "cso"),
            ("4620", "Crane Cove Park", "cso"), ("4615", "Jackrabbit Beach", "cso"), ("4616", "Windsurfer Circle", "cso")]),
]


@lru_cache(maxsize=1)
def message_profile() -> dict:
    """Segments per text (as worded now, and with plain hyphens) and KB stored per email,
    weighted over _SAMPLES, from the renderer the dispatcher's Python port uses."""
    from features.alerts import render
    unsub = "https://bwtf-sf.vercel.app/unsubscribe?t=00000000-0000-0000-0000-000000000000"
    seg_now = seg_plain = kb = 0.0
    examples = []
    for weight, stations in _SAMPLES:
        r = render._fallback([{"station_id": s, "station_name": n, "to": to} for s, n, to in stations], False, "Ocean Beach", unsub, "Wed Oct 1, 7:12 AM PDT")
        enc, now = sms_segments(r["sms_text"])
        _, plain = sms_segments(r["sms_text"].replace("—", "-").replace("–", "-"))
        seg_now += weight * now; seg_plain += weight * plain
        kb += weight * (len(r["html_body"].encode()) + len(r["text_body"].encode())) / 1024
        examples.append({"beaches": len(stations), "chars": len(r["sms_text"]), "encoding": enc, "segments": now, "segments_plain": plain})
    return {"segments_now": round(seg_now, 2), "segments_plain": round(seg_plain, 2), "email_kb": round(kb, 1), "examples": examples}


# ── how often alerts go out, from the records ───────────────────────────────

@lru_cache(maxsize=1)
def rates() -> dict:
    """Alerts a year per zone: every station posting in the State record plus every station a
    reported discharge flags, averaged over the complete years both records cover."""
    from features.discharges.page import _CSV as DISCHARGES_CSV
    from features.postings.page import _load as load_postings
    p = load_postings()
    col = {c: i for i, c in enumerate(p["columns"])}
    events = defaultdict(Counter)          # year -> zone -> events
    by_month = Counter()                   # month of year -> events
    by_day = defaultdict(Counter)          # zone -> day -> events
    for r in p["advisories"]:
        day, zone = r[col["posted_on"]], r[col["zone"]]
        if zone in ZONES:
            events[int(day[:4])][zone] += 1; by_month[int(day[5:7])] += 1; by_day[zone][day] += 1
    discharge_last = ""
    seen = set()
    with open(DISCHARGES_CSV, newline="") as fh:
        for r in csv.DictReader(fh):
            o = OUTFALLS.get(r["outfall_id"]); day = r["event_date"]
            discharge_last = max(discharge_last, day)
            for s in (o.stations if o else ()):
                if (day, s) in seen:
                    continue
                seen.add((day, s)); z = ZONE_OF_STATION[s]
                events[int(day[:4])][z] += 1; by_month[int(day[5:7])] += 1; by_day[z][day] += 1
    postings_last = p["known_through"] or ""
    first = 2017                                                                # the discharge record starts Oct 2016
    last = min(int(postings_last[:4]) - (postings_last[5:7] != "12"), int(discharge_last[:4]) - (discharge_last[5:7] != "12"))
    years = list(range(first, last + 1))
    per_zone = {z: round(statistics.mean(events[y][z] for y in years)) for z in ZONES}
    all_zones = [sum(events[y].values()) for y in years]
    month_avg = sum(by_month.values()) / 12
    return {
        "years": [years[0], years[-1]],
        "per_zone": per_zone,
        "zone_labels": {z: ZONES[z].label for z in ZONES},
        "all_zones": round(statistics.mean(all_zones)),
        "busiest_year": {"year": years[all_zones.index(max(all_zones))], "alerts": max(all_zones)},
        "busiest_month_factor": round(max(by_month.values()) / month_avg, 1),
        "worst_day_per_zone": max(max(d.values(), default=0) for d in by_day.values()),
        "one_zone": round(statistics.mean(per_zone.values())),
    }


def subscriber_mix(r: Optional[dict] = None) -> dict:
    """Today's subscribers: how many, how many take texts, and their alerts a year from the zones
    they chose (the sum of each zone's rate). Empty when the database is not reachable."""
    r = r or rates()
    if not sb.is_configured():
        return {}
    try:
        subs = sb.select("subscribers", {"select": "email,phone_number,station_ids,region_zone", "active": "eq.true"})
    except Exception:  # noqa: BLE001 — the page still works on the planning numbers
        return {}
    by_label = {ZONES[z].label: z for z in ZONES}
    yearly = []
    for s in subs:
        zones = {by_label[lbl] for lbl in by_label if lbl in (s.get("region_zone") or "")}
        zones |= {ZONE_OF_STATION[x] for x in (s.get("station_ids") or []) if x in ZONE_OF_STATION}
        yearly.append(sum(r["per_zone"][z] for z in zones) if zones else r["all_zones"])
    return {"subscribers": len(subs), "texting": sum(1 for s in subs if s.get("phone_number")),
            "alerts_per_year": round(statistics.mean(yearly)) if yearly else None}


# ── the model ───────────────────────────────────────────────────────────────

def monthly_cost(subscribers: int, sms_share: float = 0.0, alerts_per_year: float = 115, *, plain_hyphen: bool = False,
                 email: str = "brevo", prune: bool = False, busiest_month_factor: Optional[float] = None) -> dict:
    """Pure: the month's bill, averaged over the year, line by line. Email plans are sized for the
    busiest month (Brevo bills a plan, not per message); texts are billed per segment as sent."""
    prof = message_profile()
    busy = busiest_month_factor or rates()["busiest_month_factor"]
    S = max(0, int(subscribers))
    texting = round(S * sms_share); mailing = S - texting
    avg_month = mailing * alerts_per_year / 12; peak_month = avg_month * busy
    lines = []

    B = PRICES["brevo"]
    if email == "ses":
        lines.append(("email", "Email", PRICES["ses"]["per_1000"] * avg_month / 1000, "Amazon SES, $0.10 per 1,000", PRICES["ses"]["source"]))
    elif S < BREVO_PAID_FROM:
        lines.append(("email", "Email", 0.0, f"Brevo free, below {BREVO_PAID_FROM} subscribers", B["source"]))
    else:
        tier = next(((v, p) for v, p in B["tiers"] if v >= peak_month), None)
        if tier:
            name = "Starter" if tier[0] <= B["starter_max"] else "Standard"
            lines.append(("email", "Email", tier[1] * (1 - B["nonprofit_discount"]), f"Brevo {name}, {tier[0]:,} a month for the busiest month, 20% nonprofit discount", B["source"]))
        else:
            v, p = B["tiers"][-1]
            lines.append(("email", "Email", p * peak_month / v * (1 - B["nonprofit_discount"]), "Brevo past its largest published plan, extrapolated", B["source"]))

    T = PRICES["twilio"]
    texts = texting * alerts_per_year / 12
    seg = prof["segments_plain"] if plain_hyphen else prof["segments_now"]
    lines.append(("sms", "Texts", (T["toll_free_number"] + texts * seg * (T["per_segment"] + T["carrier_fee"])) if texting else 0.0,
                  f"Twilio toll-free, {texts:,.0f} texts a month at {seg:.2f} segments each" if texting else "None", T["source"]))

    M = MEASURED
    visits = M["public_visits_month"] + M["visits_per_subscriber_month"] * S
    pro = S >= PRO_FROM
    V = PRICES["vercel"]
    usage = (visits * M["invocations_per_visit"] / 1e6 * V["per_m_invocations"]
             + (M["forecast_refreshes_month"] * M["refresh_cpu_seconds"] + visits * M["view_cpu_seconds"]) / 3600 * V["cpu_hour"]
             + (M["forecast_refreshes_month"] * M["refresh_seconds"] + visits * M["view_wall_seconds"]) * M["function_gb"] / 3600 * V["gb_hour"]
             + visits * M["transfer_mb_per_visit"] / 1024 * V["per_gb_transfer"])
    lines.append(("vercel", "Web app, Vercel", V["pro"] + max(0.0, usage - V["credit"]) if pro else 0.0,
                  f"Pro, from {PRO_FROM} subscribers" + (", plus usage past its credit" if pro and usage > V["credit"] else "") if pro else "Hobby", V["source"]))

    D = PRICES["supabase"]
    kept_months = 3 if prune else 12
    storage_gb = M["database_base_gb"] + S * alerts_per_year * kept_months / 12 * prof["email_kb"] / 1024 / 1024
    egress_gb = visits * M["home_db_read_kb"] / 1024 / 1024
    lines.append(("supabase", "Database, Supabase",
                  D["pro"] + max(0.0, storage_gb - D["included_gb"]) * D["per_gb_month"] + max(0.0, egress_gb - D["included_egress_gb"]) * D["per_gb_egress"] if pro else 0.0,
                  (f"Pro, from {PRO_FROM} subscribers" + (", plus storage past 8 GB" if storage_gb > D["included_gb"] else "")) if pro else "Free", D["source"]))

    A = PRICES["arcgis"]
    tiles = visits * M["tiles_per_visit"]
    lines.append(("map", "Map tiles, Esri", max(0.0, tiles - A["free_tiles"]) * A["per_1000_tiles"] / 1000,
                  "ArcGIS free tier" if tiles <= A["free_tiles"] else f"ArcGIS, {tiles / 1e6:.1f}M tiles a month", A["source"]))

    N = PRICES["domain"]
    lines.append(("domain", "Domain", N["per_year"] / 12 if pro else 0.0, "One .org domain, about $19 a year" if pro else "None yet", N["source"]))

    total = sum(x[2] for x in lines)
    return {"subscribers": S, "total": total, "per_subscriber_year": total * 12 / S if S else 0.0,
            "busiest_month_messages": round((mailing + texting) * alerts_per_year / 12 * busy), "texts_month": round(texts),
            "storage_gb": round(storage_gb, 2), "visits_month": visits,
            "lines": [{"key": k, "service": n, "monthly": round(v, 2), "plan": p, "source": src} for k, n, v, p, src in lines]}


def free_until(key: str, **params) -> Optional[int]:
    """The largest subscriber count at which this line still costs nothing (None if never free)."""
    last = None
    for S in list(range(1, 151)) + [int(150 * 1.03 ** i) for i in range(1, 260)]:
        line = next(x for x in monthly_cost(S, **params)["lines"] if x["key"] == key)
        if line["monthly"] >= 0.005:    # a cent or more on the bill
            return last
        last = S
    return last


def report(subscribers: int = 1_000, sms_share: float = 0.25, alerts: str = "mix", plain_hyphen: bool = False,
           email: str = "brevo", prune: bool = False) -> dict:
    """Everything the Running costs page draws: the chosen month, the curve over STEPS for email
    only and with texts, the inputs and where each came from."""
    r = rates(); mix = subscriber_mix(r)
    choices = {"one": r["one_zone"], "mix": (mix.get("alerts_per_year") or round(statistics.mean([r["one_zone"], r["all_zones"]]))),
               "busiest": r["busiest_year"]["alerts"]}
    a = choices.get(alerts, choices["mix"])
    kw = {"alerts_per_year": a, "plain_hyphen": plain_hyphen, "email": email, "prune": prune}
    month = monthly_cost(subscribers, sms_share, **kw)
    for line in month["lines"]:
        line["free_until"] = free_until(line["key"], sms_share=sms_share, **kw)
    share_for_curve = sms_share or 0.25
    return {
        "ok": True, "month": month, "params": {"subscribers": subscribers, "sms_share": sms_share, "alerts": alerts, "alerts_per_year": a,
                                                "plain_hyphen": plain_hyphen, "email": email, "prune": prune},
        "curve": {"steps": STEPS, "texts_share": share_for_curve,
                  "email_only": [round(monthly_cost(s, 0.0, **kw)["total"], 2) for s in STEPS],
                  "with_texts": [round(monthly_cost(s, share_for_curve, **kw)["total"], 2) for s in STEPS]},
        "alert_choices": choices, "rates": r, "today": mix, "messages": message_profile(),
        "measured": MEASURED, "prices": {k: {"source": v["source"], "note": v["note"]} for k, v in PRICES.items()},
        "prices_checked": PRICES_CHECKED, "thresholds": {"brevo_paid_from": BREVO_PAID_FROM, "pro_from": PRO_FROM},
        "as_of": today_pacific().isoformat(),
    }


# ── routes (gated in app/wsgi.py: the page behind the unlock page, the API behind a 401) ──

def _params(query: dict) -> dict:
    one = lambda k, d="": (query.get(k) or [d])[0]  # noqa: E731
    def num(k, d, lo, hi, cast=float):
        try:
            return min(hi, max(lo, cast(one(k, str(d)))))
        except ValueError:
            return d
    return {"subscribers": num("subscribers", 1_000, 1, 1_000_000, int), "sms_share": num("sms_share", 0.25, 0.0, 1.0),
            "alerts": one("alerts", "mix") if one("alerts", "mix") in ("one", "mix", "busiest") else "mix",
            "plain_hyphen": one("hyphen") == "1", "email": "ses" if one("email") == "ses" else "brevo", "prune": one("prune") == "1"}


def handle_api(query, body):
    try:
        return 200, "application/json", json.dumps(report(**_params(query))).encode()
    except Exception as e:  # noqa: BLE001
        return 500, "application/json", json.dumps({"ok": False, "error": str(e)}).encode()


def handle_page(query, body):
    from flask import render_template
    data = report(**_params(query))
    return 200, "text/html; charset=utf-8", render_template("alerts/costs.html", data=data).encode()


GET_ROUTES = {
    "/alerts/costs": handle_page,
    "/alerts/api/costs": handle_api,
}
