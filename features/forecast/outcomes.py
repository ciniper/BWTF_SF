"""Forecast vs reality — the record of what each morning's forecast said, next to what then happened
(migration 022: ``forecast_issued``, ``forecast_outcomes``; the Admin "Forecast check" tab reads it).

The forecast and the truth are written by different hands at different times:

  * ``record`` — a side job of the production refresh (features/forecast/page._compute_and_store, after the snapshot
    is stored; never raises). It adds the day's FIRST forecast of every target day × lead × zone — DO NOTHING on the
    key, so the first refresh of the Pacific day wins and the later refreshes stay what they are: the live rules at
    work (forecast_changes) — and refills the outcome columns for the last BACKFILL_DAYS target days from the fast
    rulers the site already keeps: the SFPUC map's posted / CSO flags (feed_station_days), the city's lab results
    over standard (samples) and the gauges' measured rain (ACIS). It never writes ledger_overflow: that is the gold
    truth, filled from the quarterly CIWQS refresh (TODO).
  * ``report`` — what the Admin tab shows: is the record healthy (an issue day for every calendar day), how each target
    day's forecast developed from five days out to the morning of, and the season's tally by lead at the Medium edge
    on three rulers. The rulers are interim (STAGES_PROTOCOL.md §1: feed flags and postings are a second ruler, never
    the truth); the protocol's own T0 grade stays grade_prospective.py → stages_build.t0_as_served.

Why the first snapshot and not all 48: a visitor who read the page that morning saw it, and the protocol grades it
(§2, T0). Why a Python side job and not pg_cron: the zone registry and the exceedance rules live here, once.

Backfill, from the main checkout (service key in .env; GETs plus the two tables' writes), after applying 022:
    venv/bin/python -m features.forecast.outcomes --backfill [--since YYYY-MM-DD]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

from shared import supabase as sb
from shared.clock import now_utc, today_pacific
from shared.stations import STATION_NAMES
from shared.zones import ZONE_OF_SOURCE, ZONE_OF_STATION, ZONES

ISSUED = "forecast_issued"
OUTCOMES = "forecast_outcomes"
LEADS = range(0, 6)          # today and five days ahead, as the page shows them
BACKFILL_DAYS = 10           # outcome columns refilled this far back on every refresh (truth keeps arriving)
PAGE = 1000                  # PostgREST's default max rows per GET
EDGES = {"medium": 0.205, "high": 0.505, "extreme": 0.805}   # STAGES_PROTOCOL.md §5 (A3): fixed, chosen from no data
RULERS = ("feed", "samples", "either")


def level(p: Optional[float]) -> str:
    """The page's risk level for a probability (protocol §5): Low / Medium / High / Extreme."""
    if p is None:
        return "unknown"
    if p >= EDGES["extreme"]:
        return "extreme"
    if p >= EDGES["high"]:
        return "high"
    if p >= EDGES["medium"]:
        return "medium"
    return "low"


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v) -> Optional[float]:
    try:
        return None if v is None else round(float(v), 4)
    except (TypeError, ValueError):
        return None


# ─── what the forecast said ──────────────────────────────────────────────────

def issued_rows(snap: dict, issue_date: str, generated_at: str) -> list[dict]:
    """One snapshot → its forecast_issued rows: each forecast day (lead 0–5) × zone, with the rain it was fed, the
    uncorrected percentage, the rules that fired and the basin probabilities. Past days a cached snapshot may still
    carry (no day_offset, or one outside 0–5) are skipped; a day whose date disagrees with its lead is skipped with
    a note rather than raised — a side job of the refresh never fails it."""
    stamp = snap.get("model") or {}
    issue = date.fromisoformat(str(issue_date)[:10])
    out = []
    for day in (snap.get("predictions") or {}).values():
        if not isinstance(day, dict) or day.get("day_offset") is None:
            continue
        lead = int(day["day_offset"])
        if lead not in LEADS:
            continue
        target = str(day.get("date"))[:10]
        if target != (issue + timedelta(days=lead)).isoformat():
            print(f"forecast_issued: {issue} lead {lead} is dated {target}; skipped")
            continue
        lc = day.get("live_corrections") or {}
        rules = sorted({str(v.get("rule")) for blk in (lc.get("stage1") or {}, lc.get("groups") or {})
                        for v in blk.values() if isinstance(v, dict) and v.get("rule")})
        plain = (day.get("plain") or {}).get("zones") or {}
        for zone, p in sorted((day.get("zones") or {}).items()):
            if zone not in ZONES:
                continue
            out.append({
                "target_date": target, "lead": lead, "zone": zone, "issue_date": issue.isoformat(),
                "generated_at": generated_at,
                "model": stamp.get("name"), "corrections": stamp.get("live_corrections"),
                "stamp": {k: stamp.get(k) for k in ("name", "stage1", "stage2", "weather_model", "live_corrections")},
                "p": _num(p), "p_plain": _num(plain.get(zone)), "rules": rules,
                "rain_in": _num(day.get("rain_inches")), "rain_2d": _num(day.get("rain_2d_cum")),
                "rain_3d": _num(day.get("rain_3d_cum")), "rain_source": day.get("rain_source"),
                "basins": {k: _num(v) for k, v in (day.get("discharge_probs") or {}).items()} or None,
            })
    return out


# ─── what happened ───────────────────────────────────────────────────────────

def feed_by_zone_day(rows: Iterable[dict]) -> dict:
    """feed_station_days rows (station_id = the feed's numeric id, day, posted, cso) → {(day, zone): {stations,
    posted, cso}}. A zone-day with no row is absent: the watcher saw nothing that day."""
    out: dict = {}
    for r in rows:
        zone = ZONE_OF_STATION.get(str(r.get("station_id")))
        if zone is None:
            continue
        key = (str(r["day"])[:10], zone)
        cur = out.setdefault(key, {"feed_stations": 0, "feed_posted": False, "feed_cso": False})
        cur["feed_stations"] += 1
        cur["feed_posted"] = cur["feed_posted"] or bool(r.get("posted"))
        cur["feed_cso"] = cur["feed_cso"] or bool(r.get("cso"))
    return out


def samples_by_zone_day(rows: Iterable[dict]) -> dict:
    """samples rows (station_id = the registry key, sample_date, exceeds) → {(day, zone): {samples_n,
    samples_exceeded, exceeded_stations}}: station-days sampled, and how many had any analyte over standard."""
    by_station: dict = defaultdict(bool)
    for r in rows:
        zone = ZONE_OF_SOURCE.get(r.get("station_id"))
        if zone is None:
            continue
        k = (str(r["sample_date"])[:10], zone, r["station_id"])
        by_station[k] = by_station[k] or bool(r.get("exceeds"))
    out: dict = {}
    for (day, zone, station), exceeded in by_station.items():
        cur = out.setdefault((day, zone), {"samples_n": 0, "samples_exceeded": 0, "exceeded_stations": []})
        cur["samples_n"] += 1
        if exceeded:
            cur["samples_exceeded"] += 1
            cur["exceeded_stations"].append(STATION_NAMES.get(station, station))
    for cur in out.values():
        cur["exceeded_stations"].sort()
    return out


def rain_by_day(acis) -> dict:
    """The engine's ACIS frame (date + one column per gauge, inches, NaN when missing) → {day: {rain_obs_in,
    rain_obs_by_gauge}}. Empty / None → {}."""
    out: dict = {}
    if acis is None or getattr(acis, "empty", True):
        return out
    gauges = [c for c in acis.columns if c != "date"]
    for _, row in acis.iterrows():
        vals = {}
        for g in gauges:
            v = row[g]
            vals[g] = None if v is None or v != v else round(float(v), 2)   # NaN != NaN
        known = [v for v in vals.values() if v is not None]
        out[str(row["date"])[:10]] = {"rain_obs_in": round(sum(known) / len(known), 2) if known else None,
                                      "rain_obs_by_gauge": vals}
    return out


def outcome_rows(days: Iterable[date], feed: dict, samples: dict, rain: dict, now: datetime) -> list[dict]:
    """One forecast_outcomes row per day × zone from the three fast rulers. Columns a ruler cannot speak to are
    null: no feed row → feed_* null (nobody was watching); no sample → samples_exceeded null. ledger_* are never
    here — the merge upsert leaves them as they are."""
    stamp = _iso(now)
    out = []
    for d in days:
        key_day = d.isoformat()
        for zone in ZONES:
            f = feed.get((key_day, zone))
            s = samples.get((key_day, zone))
            r = rain.get(key_day) or {}
            out.append({
                "target_date": key_day, "zone": zone,
                "rain_obs_in": r.get("rain_obs_in"), "rain_obs_by_gauge": r.get("rain_obs_by_gauge"),
                "feed_stations": f["feed_stations"] if f else None,
                "feed_posted": f["feed_posted"] if f else None,
                "feed_cso": f["feed_cso"] if f else None,
                "samples_n": s["samples_n"] if s else 0,
                "samples_exceeded": s["samples_exceeded"] if s else None,
                "exceeded_stations": s["exceeded_stations"] if s else [],
                "updated_at": stamp,
            })
    return out


def _between(col: str, start: date, end: date) -> dict:
    return {"and": f"({col}.gte.{start.isoformat()},{col}.lte.{end.isoformat()})"}


def fetch_truth(start: date, end: date, rain_fetcher: Optional[Callable] = None) -> tuple[dict, dict, dict]:
    """The three rulers over [start, end]: feed flags and samples from Supabase, rain through ``rain_fetcher``
    (the engine's _fetch_acis_daily; None → no rain)."""
    feed = feed_by_zone_day(sb.select("feed_station_days", {"select": "station_id,day,posted,cso", **_between("day", start, end)}))
    samples = samples_by_zone_day(sb.select("samples", {"select": "station_id,sample_date,exceeds",
                                                         **_between("sample_date", start, end)}))
    rain = rain_by_day(rain_fetcher(start, end)) if rain_fetcher else {}
    return feed, samples, rain


def record(snap: dict, issue_date: str, generated_at: str, rain_fetcher: Optional[Callable] = None,
           now: Optional[datetime] = None) -> dict:
    """The refresh's side job: the day's first forecast rows (DO NOTHING) and the last BACKFILL_DAYS days' outcomes
    (merge). Never raises; -1 in a count means that half failed and was printed."""
    now = now or now_utc()
    out = {"issued": 0, "outcomes": 0}
    try:
        rows = issued_rows(snap, issue_date, generated_at)
        if rows:
            sb.upsert(ISSUED, rows, "target_date,lead,zone", resolution="ignore-duplicates")
        out["issued"] = len(rows)
    except Exception as e:  # noqa: BLE001
        print(f"forecast_issued not recorded: {e}")
        out["issued"] = -1
    try:
        today = date.fromisoformat(str(issue_date)[:10])
        start = today - timedelta(days=BACKFILL_DAYS)
        feed, samples, rain = fetch_truth(start, today, rain_fetcher)
        rows = outcome_rows([start + timedelta(days=i) for i in range((today - start).days + 1)], feed, samples, rain, now)
        sb.upsert(OUTCOMES, rows, "target_date,zone")
        out["outcomes"] = len(rows)
    except Exception as e:  # noqa: BLE001
        print(f"forecast_outcomes not recorded: {e}")
        out["outcomes"] = -1
    return out


# ─── the Admin tab's report ──────────────────────────────────────────────────

def _select_all(table: str, params: dict) -> list[dict]:
    rows, offset = [], 0
    while True:
        batch = sb.select(table, {**params, "limit": str(PAGE), "offset": str(offset)})
        rows += batch
        if len(batch) < PAGE:
            return rows
        offset += PAGE


def load(start: date, end: date) -> tuple[list[dict], list[dict]]:
    """Every forecast_issued row targeting [start, end] and every forecast_outcomes row in it (GETs)."""
    issued = _select_all(ISSUED, {"select": "target_date,lead,zone,issue_date,generated_at,model,corrections,p,p_plain,rules,"
                                            "rain_in,rain_2d,rain_3d,rain_source", **_between("target_date", start, end),
                                  "order": "target_date.asc,lead.asc,zone.asc"})
    outcomes = _select_all(OUTCOMES, {"select": "*", **_between("target_date", start, end), "order": "target_date.asc,zone.asc"})
    return issued, outcomes


def _truth(o: Optional[dict]) -> dict:
    """What an outcome row says on each ruler: True / False / None (unknown)."""
    if not o:
        return {k: None for k in RULERS}
    feed = None if not o.get("feed_stations") else bool(o.get("feed_posted") or o.get("feed_cso"))
    smp = None if not o.get("samples_n") else bool(o.get("samples_exceeded"))
    either = None if feed is None and smp is None else bool(feed or smp)
    return {"feed": feed, "samples": smp, "either": either}


def report(issued: list[dict], outcomes: list[dict], today: Optional[date] = None, edge: float = EDGES["medium"]) -> dict:
    """The Admin tab's data from the two tables' rows.

    health      issue days recorded vs calendar days from the first issue day to today, the missing ones, the last
                refresh that wrote, rows whose truth is still to come (a target after yesterday)
    days        newest first, per target day: observed rain, then per zone the trajectory lead 5 → 0 (p, level, the
                rain it was fed, the rules that fired, the uncorrected p) and the outcome row with its three rulers
    tally       per lead × ruler, target days up to yesterday whose ruler is known: n, caught, missed, false alarms,
                quiet, Brier score (mean squared gap between p and 0/1) — at the Medium edge, as the Model check grades
    """
    today = today or today_pacific()
    yesterday = today - timedelta(days=1)
    by_target: dict = defaultdict(lambda: defaultdict(dict))
    issue_days, last_generated = set(), None
    for r in issued:
        by_target[str(r["target_date"])[:10]][r["zone"]][int(r["lead"])] = r
        issue_days.add(str(r["issue_date"])[:10])
        g = str(r.get("generated_at") or "")
        last_generated = max(last_generated or "", g)
    out_by: dict = {(str(o["target_date"])[:10], o["zone"]): o for o in outcomes}

    health = {"issue_days": len(issue_days), "expected_days": 0, "missing": [], "first_issue": None, "last_issue": None,
              "last_generated_at": last_generated, "rows": len(issued), "waiting": 0}
    if issue_days:
        first, last = min(issue_days), max(issue_days)
        d0, d1 = date.fromisoformat(first), max(yesterday, date.fromisoformat(last))   # today is missing only once it is over
        every = {(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)}
        health.update({"first_issue": first, "last_issue": last, "expected_days": len(every),
                       "missing": sorted(every - issue_days)})
    health["waiting"] = sum(1 for r in issued if str(r["target_date"])[:10] > yesterday.isoformat())

    tally = {lead: {ruler: {"n": 0, "caught": 0, "missed": 0, "false_alarm": 0, "quiet": 0, "sq": 0.0} for ruler in RULERS}
             for lead in LEADS}
    days = []
    for target in sorted(by_target, reverse=True):
        zones = {}
        rain_obs = None
        for zone in ZONES:
            o = out_by.get((target, zone))
            if o and rain_obs is None:
                rain_obs = {"in": o.get("rain_obs_in"), "by_gauge": o.get("rain_obs_by_gauge")}
            truth = _truth(o)
            traj = {}
            for lead in LEADS:
                r = by_target[target][zone].get(lead)
                if r is None:
                    continue
                p = _num(r.get("p"))
                traj[lead] = {"p": p, "level": level(p), "p_plain": _num(r.get("p_plain")), "rules": r.get("rules") or [],
                              "rain_in": _num(r.get("rain_in")), "rain_3d": _num(r.get("rain_3d")),
                              "rain_source": r.get("rain_source"), "model": r.get("model")}
                if target <= yesterday.isoformat() and p is not None:
                    alert = p >= edge
                    for ruler in RULERS:
                        bad = truth[ruler]
                        if bad is None:
                            continue
                        t = tally[lead][ruler]
                        t["n"] += 1
                        t["sq"] += (p - (1.0 if bad else 0.0)) ** 2
                        t["caught" if (alert and bad) else "missed" if bad else "false_alarm" if alert else "quiet"] += 1
            zones[zone] = {"label": ZONES[zone].label, "trajectory": traj, "outcome": o, "truth": truth}
        days.append({"date": target, "pending": target > yesterday.isoformat(), "rain_obs": rain_obs, "zones": zones})

    for lead in tally:
        for ruler, t in tally[lead].items():
            t["brier"] = round(t.pop("sq") / t["n"], 4) if t["n"] else None
    return {"today": today.isoformat(), "edge": edge, "rulers": RULERS, "leads": list(LEADS), "health": health,
            "days": days, "tally": tally, "zones": {k: z.label for k, z in ZONES.items()}}


def build(days_back: int = 60, today: Optional[date] = None) -> dict:
    """The report for the last ``days_back`` target days plus the five ahead, or a `state` saying why not."""
    today = today or today_pacific()
    if not sb.is_configured():
        return {"state": "unconfigured", "words": "Supabase is not configured on this host: the record lives in production.",
                "today": today.isoformat()}
    try:
        issued, outcomes = load(today - timedelta(days=days_back), today + timedelta(days=5))
    except Exception as e:  # noqa: BLE001
        return {"state": "error", "words": f"The record could not be read: {e}", "today": today.isoformat()}
    out = report(issued, outcomes, today)
    out["state"] = "ok" if issued else "empty"
    out["days_back"] = days_back
    return out


# ─── backfill ────────────────────────────────────────────────────────────────

def backfill(since: Optional[str] = None, rain_fetcher: Optional[Callable] = None, now: Optional[datetime] = None) -> dict:
    """Every first snapshot in forecast_history (GETs, paged) → forecast_issued, then the outcomes for every target
    day the record covers. Run once after applying 022, and any time the refresh was down."""
    now = now or now_utc()
    rows, offset, first_day, last_day = [], 0, None, None
    params = {"select": "forecast_date,first_generated_at,first_snapshot", "order": "forecast_date.asc"}
    if since:
        params["forecast_date"] = f"gte.{since}"
    while True:
        batch = sb.select("forecast_history", {**params, "limit": "31", "offset": str(offset)})
        for r in batch:
            rows += issued_rows(r["first_snapshot"] or {}, r["forecast_date"], r["first_generated_at"])
            first_day = first_day or r["forecast_date"]
            last_day = r["forecast_date"]
        if len(batch) < 31:
            break
        offset += 31
    for i in range(0, len(rows), 500):
        sb.upsert(ISSUED, rows[i:i + 500], "target_date,lead,zone", resolution="ignore-duplicates")
    n_out = 0
    if first_day:
        start, end = date.fromisoformat(first_day), min(date.fromisoformat(last_day) + timedelta(days=5), today_pacific())
        feed, samples, rain = fetch_truth(start, end, rain_fetcher)
        out_rows = outcome_rows([start + timedelta(days=i) for i in range((end - start).days + 1)], feed, samples, rain, now)
        for i in range(0, len(out_rows), 500):
            sb.upsert(OUTCOMES, out_rows[i:i + 500], "target_date,zone")
        n_out = len(out_rows)
    return {"issued": len(rows), "outcomes": n_out, "first": first_day, "last": last_day}


if __name__ == "__main__":
    if "--backfill" not in sys.argv[1:]:
        raise SystemExit(__doc__)
    if not sb.is_configured():
        raise SystemExit("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_KEY): run this from the main checkout")
    since = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--since=")), None)
    if since is None and "--since" in sys.argv[1:]:
        since = sys.argv[sys.argv.index("--since") + 1]
    from features.forecast import page as _page   # the engine's ACIS fetch, for the measured rain
    _page._load_engine()
    fetcher = _page._engine.LIVE._fetch_acis_daily if _page._engine is not None else None
    if fetcher is None:
        print("forecast engine unavailable: outcomes will carry no measured rain")
    print(json.dumps(backfill(since, fetcher), indent=1))
