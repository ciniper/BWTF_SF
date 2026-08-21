"""Site Report Card — how often each shoreline site is "bad", per the city's lab data.

Public read-only page answering the questions volunteers and coordinators
actually ask ("which sites are worst?", "is it getting better?", "should we
expand testing to X?") from DataSF's beach lab results (dataset v3fv-x3ux —
the SFPUC shoreline monitoring program's published Enterococcus/coliform
counts, ~weekly per station since Jul 2020).

Standard: California's single-sample maximum for Enterococcus, 104 MPN/100mL
(with 36 as the caution tier) — the same standard BWTF's own grading and the
city's beach postings use, so numbers here line up with the other pages.

Methodology notes baked into the API payload (the page shows them):
  * Censored values ("<10", ">24196") keep their magnitude; "<x" is below
    every threshold in play and ">x" is far above, so sign-stripping is safe.
  * The city resamples after exceedances, which inflates absolute rates a
    little; it affects all sites similarly, so RANKINGS are robust.
  * Stations sampled only sporadically/reactively (< ROUTINE_MIN samples)
    are flagged — their raw rates reflect when the city chose to sample,
    not typical conditions, and must not be ranked against weekly sites.

Stateless-survivor pattern (like /compare and /bwtf): fetch upstream on
demand, hold a small in-process TTL cache (the dataset refreshes daily), no
background threads, no Supabase — serverless-safe. Each route handler
returns ``(status, content_type, body_bytes)`` — the contract
``app/wsgi.py`` dispatches.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta

import requests
from flask import render_template

SOCRATA_URL = "https://data.sfgov.org/resource/v3fv-x3ux.json"
ANALYTE = "ENTERO"
SSM = 104          # CA single-sample maximum, Enterococcus (MPN/100mL)
CAUTION = 36       # BWTF/state caution tier
ROUTINE_MIN = 300  # fewer samples than this = sporadic/reactive sampling
WET_MONTHS = {11, 12, 1, 2, 3, 4}
RECENT_DAYS = 730  # "recent" window, relative to the newest sample in the data

_CACHE_TTL_SECONDS = 6 * 3600
_cache: dict = {"at": 0.0, "payload": None}

# station id -> (display name, shoreline group)
STATIONS = {
    "BAY#202.4_SL": ("Crissy Field East", "North Shore"),
    "BAY#202.5_SL": ("Crissy Field West", "North Shore"),
    "BAY#210.1_SL": ("Hyde Street Pier", "North Shore"),
    "BAY#211_SL": ("Aquatic Park", "North Shore"),
    "BAY#220_SL": ("Mission Creek", "East Bayshore"),
    "BAY#230_SL": ("Crane Cove Park", "East Bayshore"),
    "BAY#300.1_SL": ("Sunnydale Cove", "East Bayshore"),
    "BAY#301.1_SL": ("Windsurfer Circle", "East Bayshore"),
    "BAY#301.2_SL": ("Jackrabbit Beach", "East Bayshore"),
    "BAY#320_SL": ("Islais Creek", "East Bayshore"),
    "OCEAN#15_SL": ("Baker Beach at Lobos Creek", "Ocean"),
    "OCEAN#15EAST_SL": ("Baker Beach East", "Ocean"),
    "OCEAN#16_SL": ("Baker Beach West", "Ocean"),
    "OCEAN#17_SL": ("China Beach", "Ocean"),
    "OCEAN#18_SL": ("Ocean Beach at Balboa", "Ocean"),
    "OCEAN#19_SL": ("Ocean Beach at Lincoln", "Ocean"),
    "OCEAN#20_SL": ("Ocean Beach at Pacheco", "Ocean"),
    "OCEAN#21_SL": ("Ocean Beach at Vicente", "Ocean"),
    "OCEAN#21.1_SL": ("Ocean Beach at Sloat", "Ocean"),
    "OCEAN#22_SL": ("Fort Funston", "Ocean"),
}


def _fetch_rows() -> list[dict]:
    """All ENTERO rows from DataSF (paged; currently ~5k of 21k total rows)."""
    rows, offset = [], 0
    while True:
        batch = requests.get(SOCRATA_URL, params={
            "$limit": 50000, "$offset": offset, "$order": ":id",
            "analyte": ANALYTE,
        }, timeout=30).json()
        rows.extend(batch)
        if len(batch) < 50000:
            return rows
        offset += 50000


def _value(raw) -> float | None:
    try:
        return float(str(raw).lstrip("<>").strip())
    except (TypeError, ValueError):
        return None


def _compute(rows: list[dict]) -> dict:
    samples = defaultdict(list)  # station id -> [(date, value)]
    newest = None
    for r in rows:
        sid, v = r.get("source"), _value(r.get("data"))
        if sid not in STATIONS or v is None or not r.get("sample_date"):
            continue
        d = datetime.fromisoformat(r["sample_date"][:19])
        samples[sid].append((d, v))
        newest = d if newest is None or d > newest else newest

    recent_cutoff = (newest - timedelta(days=RECENT_DAYS)) if newest else None

    def rate(pairs, pred):
        hits = [p for p in pairs if pred(p)]
        return {"n": len(hits), "pct": round(100 * sum(v >= SSM for _, v in hits) / len(hits), 1)} if hits else {"n": 0, "pct": None}

    sites = []
    for sid, pairs in samples.items():
        name, group = STATIONS[sid]
        values = [v for _, v in pairs]
        n = len(pairs)
        yearly = []
        by_year = defaultdict(list)
        for d, v in pairs:
            by_year[d.year].append(v)
        for y in sorted(by_year):
            ys = by_year[y]
            yearly.append({"year": y, "n": len(ys),
                           "pct": round(100 * sum(v >= SSM for v in ys) / len(ys), 1)})
        mid = sorted(values)[n // 2]
        sites.append({
            "id": sid, "name": name, "group": group,
            "samples": n, "routine": n >= ROUTINE_MIN,
            "first": pairs and min(d for d, _ in pairs).strftime("%Y-%m-%d"),
            "last": pairs and max(d for d, _ in pairs).strftime("%Y-%m-%d"),
            "exceed_pct": round(100 * sum(v >= SSM for v in values) / n, 1),
            "caution_pct": round(100 * sum(v >= CAUTION for v in values) / n, 1),
            "median": mid,
            "max": max(values),
            "recent": rate(pairs, lambda p: recent_cutoff and p[0] >= recent_cutoff),
            "wet": rate(pairs, lambda p: p[0].month in WET_MONTHS),
            "dry": rate(pairs, lambda p: p[0].month not in WET_MONTHS),
            "yearly": yearly,
        })

    sites.sort(key=lambda s: (not s["routine"], -(s["recent"]["pct"] or 0)))
    return {
        "analyte": ANALYTE, "ssm": SSM, "caution": CAUTION,
        "routine_min": ROUTINE_MIN,
        "recent_days": RECENT_DAYS,
        "newest_sample": newest.strftime("%Y-%m-%d") if newest else None,
        "total_samples": sum(s["samples"] for s in sites),
        "sites": sites,
    }


# ─── response helpers / handlers ─────────────────────────────────────────────

def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


def handle_summary(query, body):
    now = time.monotonic()
    if _cache["payload"] is not None and now - _cache["at"] < _CACHE_TTL_SECONDS:
        return _json(_cache["payload"])
    try:
        payload = _compute(_fetch_rows())
        _cache.update(at=now, payload=payload)
        return _json(payload)
    except Exception as exc:
        if _cache["payload"] is not None:  # upstream hiccup: serve stale
            stale = dict(_cache["payload"], stale_note=f"refresh failed: {type(exc).__name__}")
            return _json(stale)
        return _json({"error": f"DataSF fetch failed: {exc}"}, status=502)


def handle_page(query, body):
    html = render_template("site_analysis/page.html")
    return 200, "text/html; charset=utf-8", html.encode()


GET_ROUTES = {
    "/analysis": handle_page,
    "/analysis/api/summary": handle_summary,
}
POST_ROUTES = {}
