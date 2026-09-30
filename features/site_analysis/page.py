"""Site Report Card — how often each shoreline site is "bad", per the city's lab data.

Public read-only page answering the questions volunteers and coordinators
actually ask ("which sites are worst?", "is it getting better?", "should we
expand testing to X?") from DataSF's beach lab results (dataset v3fv-x3ux —
the SFPUC shoreline monitoring program's published Enterococcus/coliform
counts, ~weekly per station since Jul 2020).

Standard: by default California's single-sample maximum for Enterococcus,
104 MPN/100mL (with 36 as the caution tier) — what BWTF's own grading uses,
so numbers here line up with the other pages. A "Standard" toggle
(?indicator=) regrades the same samples on fecal coliform, total coliform, or
ANY — the state posting rule (Title 17 §7958): bad when any of the three is
at or over its limit, the total-coliform limit falling to 1,000 when fecal is
over 10% of total. The city posts on any of the three (Chase, 2026-09-27).

Two sampling views (?mode=):
  * ``weekly`` (default) — first sample of each site-week only. The city's
    routine cadence is Mondays (~69% of all samples; holiday weeks shift to
    Tuesday); everything after the week's first sample is follow-up
    resampling that clusters around contamination events and inflates rates
    (Islais Creek: 30% all-samples vs 15.5% weekly). This is the honest
    week-by-week base rate — and the apples-to-apples view vs BWTF's own
    weekly volunteer sampling.
  * ``all`` — every published sample, resamples on later days included.
    Shows how often *tests* failed, not how often *weeks* were bad.
  In both views one station + sample time is ONE sample holding every
  indicator measured then, and a repeated (station, time, analyte) keeps the
  higher value (Chase, 2026-09-27): DataSF truncates sample_date to midnight,
  so a routine sample and a storm resample on the same day collide, and 406 of
  the 503 collisions are the Aug–Oct 2020 batches loaded twice.
Plus a date range (?start=YYYY-MM-DD&end=YYYY-MM-DD; the dataset's floor is
2020-07-27 — the city publishes nothing earlier).

Other methodology notes baked into the payload (the page shows them):
  * Censored values ("<10", ">24196") keep their magnitude; "<x" is below
    every threshold in play and ">x" is far above, so sign-stripping is safe.
  * Stations sampled only sporadically/reactively (under ROUTINE_PER_YEAR samples
    in a typical year of the window, or of the full record — see _routine) are flagged — their rates reflect when the city chose
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
from collections import defaultdict, Counter
from datetime import datetime, timedelta

import requests
from flask import render_template

from shared.stations import STATIONS as _CANONICAL_STATIONS

from shared import city_history  # noqa: E402
from shared.datasf import BEACH_SAMPLES_URL, DATASET_FLOOR  # noqa: E402
from shared.standards import (  # noqa: E402
    ENTERO_CAUTION, GEOMETRIC_MEAN_MIN_SAMPLES, GEOMETRIC_MEAN_WINDOW_DAYS, STANDARDS, parse_result,
    single_sample_max,
)

SOCRATA_URL = BEACH_SAMPLES_URL  # shared/datasf.py
ANALYTE = "ENTERO"
SSM = STANDARDS["ENTERO"]["single_sample_max"]   # CA single-sample maximum, Enterococcus (shared/standards.py)
CAUTION = ENTERO_CAUTION                        # BWTF/state caution tier
# The standards the page can grade on (the "Standard" toggle): the three
# indicators the state posting rule lists for marine water, plus E. coli — the
# indicator the city ran in place of fecal coliform from Jul 2002 to Jul 2020
# (shared/city_history.py), so the 26-year record is graded on what was
# measured. "ANY" = over on any indicator measured in the sample.
POSTING_INDICATORS = ("ENTERO", "COLI_FECAL", "COLI_TOTAL")
INDICATOR_CODES = ("ENTERO", "COLI_FECAL", "COLI_E", "COLI_TOTAL")
INDICATORS = {code: STANDARDS[code]["description"] for code in INDICATOR_CODES}
INDICATORS["ANY"] = "All (any over)"
DEFAULT_INDICATOR = ANALYTE
ROUTINE_PER_YEAR = 20  # a station sampled fewer times than this in a typical year of the window is sporadic/reactive
WET_MONTHS = {11, 12, 1, 2, 3, 4}

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
    """Every city row for the four indicators: SFPUC's lab export for 2000 →
    Jul 2020 (shared/city_history.py) ahead of DataSF from its floor (paged)."""
    rows, offset = list(city_history.records(analytes=INDICATOR_CODES)), 0
    where = "analyte in (" + ",".join(f"'{a}'" for a in INDICATOR_CODES) + ")"
    while True:
        batch = requests.get(SOCRATA_URL, params={
            "$limit": 50000, "$offset": offset, "$order": ":id",
            "$where": where,
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
    """rows -> {station id: [(sample time, {analyte: value})] sorted by time}:
    one entry per station and sample time holding every indicator measured
    then, so the total-coliform ratio rule can see the same sample's fecal
    result. A repeated (station, time, analyte) keeps the higher value — an
    exceedance must survive the merge."""
    by_key: dict = defaultdict(dict)
    for r in rows:
        sid, a, v = r.get("source"), r.get("analyte"), _value(r.get("data"))
        if sid not in STATIONS or a not in INDICATOR_CODES or v is None or not r.get("sample_date"):
            continue
        d = datetime.fromisoformat(r["sample_date"][:19])
        by_key[(sid, d)][a] = max(v, by_key[(sid, d)].get(a, v))
    samples = defaultdict(list)
    for (sid, d), vals in by_key.items():
        samples[sid].append((d, vals))
    for pairs in samples.values():
        pairs.sort(key=lambda p: p[0])
    return samples


def _over(indicator: str, vals: dict) -> bool | None:
    """Is one sample "bad" under ``indicator``? None = that indicator was not
    measured in the sample (it then counts for nothing). Single indicators:
    at or over their single-sample limit, total coliform's falling to the
    ratio limit when the same sample's fecal share is over the threshold.
    ANY: bad when any measured indicator is."""
    if indicator == "ANY":
        flags = [f for f in (_over(a, vals) for a in INDICATOR_CODES) if f is not None]
        return any(flags) if flags else None
    v = vals.get(indicator)
    if v is None:
        return None
    return v >= single_sample_max(indicator, vals.get("COLI_FECAL"), vals.get("COLI_TOTAL"))


def bad_text(indicator: str) -> str:
    """How the page words "bad" under ``indicator``, from shared/standards.py."""
    total = STANDARDS["COLI_TOTAL"]
    ratio = f"{total['single_sample_max_ratio']:,} when fecal coliform is over {total['ratio_threshold']:.0%} of total"
    if indicator == "ANY":
        return f"any indicator measured in the sample at or over its state single-sample limit (total coliform's falls to {ratio})"
    s = STANDARDS[indicator]
    txt = f"{s['description']} at or over the state single-sample limit, {s['single_sample_max']:,} MPN/100mL"
    return txt + (f" ({ratio})" if indicator == "COLI_TOTAL" else "")


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


def _routine(full_pairs: list[tuple], start: datetime | None, end: datetime | None) -> bool:
    """Is this a weekly-programme station? Judged on the samples per calendar
    year inside the window (the full record when there is none): the median
    year must reach ROUTINE_PER_YEAR, so a station SFPUC visits only after
    discharges (Ocean Beach at Pacheco, Vicente and Fort Funston since 2004)
    reads sporadic even though 26 years of visits add up. Windows under a year
    are judged on the station's last year of record instead, so a short custom
    range does not mark every station sporadic."""
    dates = [d for d, _ in full_pairs]
    if not dates:
        return False
    lo, hi = start or dates[0], end or dates[-1]
    if (hi - lo).days < 365:
        hi = dates[-1]
        lo = hi - timedelta(days=365)
        return sum(1 for d in dates if lo <= d <= hi) >= ROUTINE_PER_YEAR
    counts = Counter(d.year for d in dates if lo <= d <= hi)
    if not counts:
        return False
    full_years = [y for y in counts if lo.year < y < hi.year] or list(counts)   # partial edge years drop out when a full one exists
    per_year = sorted(counts[y] for y in full_years)
    return per_year[len(per_year) // 2] >= ROUTINE_PER_YEAR


def _compute(rows: list[dict], start: datetime | None, end: datetime | None,
             weekly: bool, indicator: str = DEFAULT_INDICATOR) -> dict:
    all_samples = _parse_rows(rows)
    newest = max((p[-1][0] for p in all_samples.values() if p), default=None)

    def pct(flags):
        return round(100 * sum(flags) / len(flags), 1)

    def rate(flagged, pred):
        hits = [p for p in flagged if pred(p)]
        return {"n": len(hits), "pct": pct([f for _, f in hits])} if hits else {"n": 0, "pct": None}

    sites = []
    for sid, full_pairs in all_samples.items():
        name, group, lat, lon = STATIONS[sid]
        # only samples that measured the chosen indicator take part (the
        # weekly regime then picks the first such sample of each site-week)
        measured = [(d, vals) for d, vals in full_pairs if _over(indicator, vals) is not None]
        pairs = _weekly_only(measured) if weekly else measured
        pairs = [(d, vals) for d, vals in pairs
                 if (start is None or d >= start) and (end is None or d <= end)]
        flagged = [(d, _over(indicator, vals)) for d, vals in pairs]
        entry = {
            "id": sid, "name": name, "group": group, "lat": lat, "lon": lon,
            "routine": _routine(full_pairs, start, end),
            "samples": len(pairs),
        }
        if pairs:
            n = len(pairs)
            by_year = defaultdict(list)
            for d, f in flagged:
                by_year[d.year].append(f)
            # the value columns (median, worst reading) only mean something for one indicator
            values = [vals[indicator] for _, vals in pairs] if indicator != "ANY" else []
            entero = [vals["ENTERO"] for _, vals in pairs] if indicator == "ENTERO" else []
            entry.update({
                "first": min(d for d, _ in pairs).strftime("%Y-%m-%d"),
                "last": max(d for d, _ in pairs).strftime("%Y-%m-%d"),
                "exceed_pct": pct([f for _, f in flagged]),
                "caution_pct": pct([v >= CAUTION for v in entero]) if entero else None,
                "median": sorted(values)[n // 2] if values else None,
                "max": max(values) if values else None,
                "wet": rate(flagged, lambda p: p[0].month in WET_MONTHS),
                "dry": rate(flagged, lambda p: p[0].month not in WET_MONTHS),
                "yearly": [{"year": y, "n": len(fs), "pct": pct(fs)}
                           for y, fs in sorted(by_year.items())],
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
        "indicator": indicator, "indicator_label": INDICATORS[indicator], "bad_text": bad_text(indicator),
        "routine_per_year": ROUTINE_PER_YEAR,
        "mode": "weekly" if weekly else "all",
        "start": start.strftime("%Y-%m-%d") if start else None,
        "end": end.strftime("%Y-%m-%d") if end else None,
        "dataset_floor": city_history.city_record_floor(), "datasf_from": DATASET_FLOOR, "history": city_history.provenance(),
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
    indicator = (query.get("indicator") or [DEFAULT_INDICATOR])[0].strip().upper()
    if indicator not in INDICATORS:
        indicator = DEFAULT_INDICATOR
    payload = _compute(rows, start, end, weekly, indicator)
    if stale_note:
        payload["stale_note"] = stale_note
    return _json(payload)


# The analytes the city's shoreline program reports, in the order the standards
# panel lists them. E. coli is the freshwater indicator and appears in only a
# small share of the city's rows (848 of ~21k in the training copy).
STANDARDS_ORDER = ("ENTERO", "COLI_FECAL", "COLI_TOTAL", "COLI_E")


def standards_context() -> dict:
    """Everything the page's "bacteria standards" panel shows, read from
    shared/standards.py so the panel cannot drift from the alerts page or the
    forecast's sample labels (tests/test_single_source.py guards the numbers)."""
    rows = []
    for code in STANDARDS_ORDER:
        s = STANDARDS[code]
        rows.append({
            "code": code, "name": s["description"],
            "ssm": s["single_sample_max"], "gm": s["geometric_mean"],
            "ratio_ssm": s.get("single_sample_max_ratio"),
            "ratio_pct": round(100 * s["ratio_threshold"]) if "ratio_threshold" in s else None,
        })
    return {
        "standards": rows, "graded_on": DEFAULT_INDICATOR, "graded_on_name": STANDARDS[DEFAULT_INDICATOR]["description"],
        "indicators": [{"code": c, "label": l, "title": "bad = " + bad_text(c)} for c, l in INDICATORS.items()],
        "ssm": SSM, "caution": CAUTION, "floor": city_history.city_record_floor(), "datasf_from": DATASET_FLOOR,
        "history": city_history.provenance(),
        "gm_window_days": GEOMETRIC_MEAN_WINDOW_DAYS, "gm_min_samples": GEOMETRIC_MEAN_MIN_SAMPLES,
        "below_detection": int(parse_result("<10")), "over_range": int(parse_result(">24196")),
    }


def handle_page(query, body):
    html = render_template("site_analysis/page.html", **standards_context())
    return 200, "text/html; charset=utf-8", html.encode()


GET_ROUTES = {
    "/analysis": handle_page,
    "/analysis/api/summary": handle_summary,
}
POST_ROUTES = {}
