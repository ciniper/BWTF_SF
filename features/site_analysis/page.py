"""Site Report Card — how often each shoreline site is "bad", per the city's lab data.

Public read-only page answering the questions volunteers and coordinators
actually ask ("which sites are worst?", "is it getting better?", "should we
expand testing to X?") from DataSF's beach lab results (dataset v3fv-x3ux —
the SFPUC shoreline monitoring program's published Enterococcus/coliform
counts, ~weekly per station since Jul 2020).

Standard: California's single-sample maximum for Enterococcus, 104 MPN/100mL
(with 36 as the caution tier) — the same standard BWTF's own grading and the
city's beach postings use, so numbers here line up with the other pages.

Two sampling views (?mode=):
  * ``weekly`` (default) — first sample of each site-week only. The city's
    routine cadence is Mondays (~69% of all samples; holiday weeks shift to
    Tuesday); everything after the week's first sample is follow-up
    resampling that clusters around contamination events and inflates rates
    (Islais Creek: 30% all-samples vs 15.5% weekly). This is the honest
    week-by-week base rate — and the apples-to-apples view vs BWTF's own
    weekly volunteer sampling.
  * ``all`` — every published sample, resamples included. Shows how often
    *tests* failed, not how often *weeks* were bad.
Plus a date range (?start=YYYY-MM-DD&end=YYYY-MM-DD; the dataset's floor is
2020-07-27 — the city publishes nothing earlier).

Other methodology notes baked into the payload (the page shows them):
  * Censored values ("<10", ">24196") keep their magnitude; "<x" is below
    every threshold in play and ">x" is far above, so sign-stripping is safe.
  * Stations sampled only sporadically/reactively (< ROUTINE_MIN samples in
    the full record) are flagged — their rates reflect when the city chose
    to sample, not typical conditions, and must not be ranked against
    weekly sites.

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

from shared.stations import STATIONS as _CANONICAL_STATIONS

from shared.datasf import BEACH_SAMPLES_URL  # noqa: E402
from shared.standards import ENTERO_CAUTION, STANDARDS  # noqa: E402

SOCRATA_URL = BEACH_SAMPLES_URL  # shared/datasf.py
ANALYTE = "ENTERO"
SSM = STANDARDS["ENTERO"]["single_sample_max"]   # CA single-sample maximum, Enterococcus (shared/standards.py)
CAUTION = ENTERO_CAUTION                        # BWTF/state caution tier
ROUTINE_MIN = 300  # fewer full-record samples than this = sporadic/reactive
WET_MONTHS = {11, 12, 1, 2, 3, 4}
DATASET_FLOOR = "2020-07-27"  # the city publishes nothing earlier

# Raw rows are cached (the dataset refreshes daily); stats are recomputed
# per request from them — filtering 7k tuples is microseconds.
_CACHE_TTL_SECONDS = 6 * 3600
_cache: dict = {"at": 0.0, "rows": None}

# station id -> (display name, shoreline group, lat, lon), from the canonical
# registry in shared/stations.py (coordinates are SFPUC's fixed monitoring
# points, embedded there rather than fetched live).
STATIONS = {
    sid: (s.name, s.group, s.lat, s.lon) for sid, s in _CANONICAL_STATIONS.items()
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


def _parse_rows(rows: list[dict]) -> dict[str, list]:
    """rows -> {station id: [(date, value)] sorted by date}."""
    samples = defaultdict(list)
    for r in rows:
        sid, v = r.get("source"), _value(r.get("data"))
        if sid not in STATIONS or v is None or not r.get("sample_date"):
            continue
        samples[sid].append((datetime.fromisoformat(r["sample_date"][:19]), v))
    for pairs in samples.values():
        pairs.sort()
    return samples


def _weekly_only(pairs: list[tuple]) -> list[tuple]:
    """First sample of each ISO site-week — the routine cadence, minus the
    follow-up resamples that cluster around contamination events."""
    seen_weeks, out = set(), []
    for d, v in pairs:  # already date-sorted
        wk = d.isocalendar()[:2]
        if wk not in seen_weeks:
            seen_weeks.add(wk)
            out.append((d, v))
    return out


def _compute(rows: list[dict], start: datetime | None, end: datetime | None,
             weekly: bool) -> dict:
    all_samples = _parse_rows(rows)
    newest = max((p[-1][0] for p in all_samples.values() if p), default=None)

    def rate(pairs, pred):
        hits = [p for p in pairs if pred(p)]
        return {"n": len(hits), "pct": round(100 * sum(v >= SSM for _, v in hits) / len(hits), 1)} if hits else {"n": 0, "pct": None}

    sites = []
    for sid, full_pairs in all_samples.items():
        name, group, lat, lon = STATIONS[sid]
        pairs = _weekly_only(full_pairs) if weekly else full_pairs
        pairs = [(d, v) for d, v in pairs
                 if (start is None or d >= start) and (end is None or d <= end)]
        entry = {
            "id": sid, "name": name, "group": group, "lat": lat, "lon": lon,
            # sporadic flag is judged on the FULL record so short custom
            # ranges don't mark every station sporadic
            "routine": len(full_pairs) >= ROUTINE_MIN,
            "samples": len(pairs),
        }
        if pairs:
            values = [v for _, v in pairs]
            n = len(values)
            by_year = defaultdict(list)
            for d, v in pairs:
                by_year[d.year].append(v)
            entry.update({
                "first": min(d for d, _ in pairs).strftime("%Y-%m-%d"),
                "last": max(d for d, _ in pairs).strftime("%Y-%m-%d"),
                "exceed_pct": round(100 * sum(v >= SSM for v in values) / n, 1),
                "caution_pct": round(100 * sum(v >= CAUTION for v in values) / n, 1),
                "median": sorted(values)[n // 2],
                "max": max(values),
                "wet": rate(pairs, lambda p: p[0].month in WET_MONTHS),
                "dry": rate(pairs, lambda p: p[0].month not in WET_MONTHS),
                "yearly": [{"year": y, "n": len(vs),
                            "pct": round(100 * sum(v >= SSM for v in vs) / len(vs), 1)}
                           for y, vs in sorted(by_year.items())],
            })
        else:
            entry.update({"first": None, "last": None, "exceed_pct": None,
                          "caution_pct": None, "median": None, "max": None,
                          "wet": {"n": 0, "pct": None}, "dry": {"n": 0, "pct": None},
                          "yearly": []})
        sites.append(entry)

    sites.sort(key=lambda s: (not s["routine"], -(s["exceed_pct"] if s["exceed_pct"] is not None else -1)))
    return {
        "analyte": ANALYTE, "ssm": SSM, "caution": CAUTION,
        "routine_min": ROUTINE_MIN,
        "mode": "weekly" if weekly else "all",
        "start": start.strftime("%Y-%m-%d") if start else None,
        "end": end.strftime("%Y-%m-%d") if end else None,
        "dataset_floor": DATASET_FLOOR,
        "newest_sample": newest.strftime("%Y-%m-%d") if newest else None,
        "total_samples": sum(s["samples"] for s in sites),
        "sites": sites,
    }


# ─── response helpers / handlers ─────────────────────────────────────────────

def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


def _parse_date(query, key) -> datetime | None:
    raw = (query.get(key) or [""])[0].strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d") if raw else None
    except ValueError:
        return None


def handle_summary(query, body):
    now = time.monotonic()
    rows, stale_note = _cache["rows"], None
    if rows is None or now - _cache["at"] >= _CACHE_TTL_SECONDS:
        try:
            rows = _fetch_rows()
            _cache.update(at=now, rows=rows)
        except Exception as exc:
            if rows is None:
                return _json({"error": f"DataSF fetch failed: {exc}"}, status=502)
            stale_note = f"refresh failed: {type(exc).__name__}"  # serve stale rows

    start = _parse_date(query, "start")
    end = _parse_date(query, "end")
    if end:  # inclusive through end-of-day
        end = end + timedelta(days=1) - timedelta(seconds=1)
    weekly = (query.get("mode") or ["weekly"])[0] != "all"
    payload = _compute(rows, start, end, weekly)
    if stale_note:
        payload["stale_note"] = stale_note
    return _json(payload)


def handle_page(query, body):
    html = render_template("site_analysis/page.html")
    return 200, "text/html; charset=utf-8", html.encode()


GET_ROUTES = {
    "/analysis": handle_page,
    "/analysis/api/summary": handle_summary,
}
POST_ROUTES = {}
