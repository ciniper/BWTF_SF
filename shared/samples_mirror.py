"""Mirror of the city's lab results in Supabase (`samples`, migration 012).

DataSF (dataset v3fv-x3ux) is the source of truth and the pages keep reading
it live; this table adds the one thing DataSF cannot give — WHEN we first saw
each result (the live rules assume results arrive a day after sampling) — and
a backstop if the API is down. Rows are keyed by station, date, analyte and
raw value and inserted with DO NOTHING, so first_seen_at never moves.

Two writers:
  * the production forecast refresh (features/forecast/page.py) mirrors the
    samples the engine just fetched for its window;
  * the backfill, once after the migration and any time the API was down:
        venv/bin/python -m shared.samples_mirror --backfill [--since YYYY-MM-DD]
"""
from __future__ import annotations

import sys
from typing import Iterable

import requests

from shared import supabase as sb
from shared.datasf import BEACH_SAMPLES_URL
from shared.standards import STANDARDS, flag_exceedances, parse_result
from shared.stations import STATIONS

TABLE = "samples"
ON_CONFLICT = "station_id,sample_date,analyte,value_raw"
DATASF_FLOOR = "2020-07-27"   # the city publishes nothing earlier
BATCH = 500


def to_rows(samples: Iterable[dict]) -> list[dict]:
    """Engine-shaped samples ({date, station, analyte, value, value_raw, exceeds})
    → table rows. Unknown stations/analytes and blank raw values are dropped;
    duplicates within the batch collapse (PostgREST rejects a batch that hits
    the same key twice)."""
    out, seen = [], set()
    for s in samples:
        st, d, an, raw = s.get("station"), str(s.get("date") or "")[:10], s.get("analyte"), str(s.get("value_raw") or "").strip()
        if st not in STATIONS or an not in STANDARDS or len(d) != 10 or not raw:
            continue
        key = (st, d, an, raw)
        if key in seen:
            continue
        seen.add(key)
        out.append({"station_id": st, "sample_date": d, "analyte": an, "value_raw": raw,
                    "value": s.get("value"), "exceeds": s.get("exceeds")})
    return out


def mirror(samples: Iterable[dict]) -> int:
    """Insert what is new (DO NOTHING on conflict). Returns rows sent. Raises
    SupabaseError on a failed request — callers on the serving path catch it."""
    rows = to_rows(samples)
    for i in range(0, len(rows), BATCH):
        sb.upsert(TABLE, rows[i:i + BATCH], on_conflict=ON_CONFLICT, resolution="ignore-duplicates")
    return len(rows)


def fetch_datasf(since: str = DATASF_FLOOR, until: str | None = None) -> list[dict]:
    """Every DataSF row from ``since`` (inclusive), engine-shaped and flagged
    with the shared exceedance rule. Pages through Socrata 20k at a time."""
    out, offset, page = [], 0, 20000
    where = f"sample_date >= '{since}T00:00:00' AND analyte IS NOT NULL"
    if until:
        where += f" AND sample_date <= '{until}T23:59:59'"
    while True:
        r = requests.get(BEACH_SAMPLES_URL, params={"$select": "source,sample_date,analyte,data", "$where": where,
                                                    "$order": "sample_date ASC, source ASC, analyte ASC",
                                                    "$limit": page, "$offset": offset}, timeout=120)
        r.raise_for_status()
        recs = r.json()
        for rec in recs:
            st, d, an, raw = rec.get("source", ""), str(rec.get("sample_date", ""))[:10], rec.get("analyte", ""), str(rec.get("data", "") or "")
            if st in STATIONS and an in STANDARDS and raw:
                out.append({"date": d, "station": st, "analyte": an, "value": parse_result(raw), "value_raw": raw})
        if len(recs) < page:
            break
        offset += page
    return flag_exceedances(out)


def backfill(since: str = DATASF_FLOOR) -> int:
    samples = fetch_datasf(since)
    n = mirror(samples)
    print(f"samples mirror: {n} rows sent from {since} (DO NOTHING on the ones already there)")
    return n


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--backfill" in args:
        since = args[args.index("--since") + 1] if "--since" in args else DATASF_FLOOR
        if not sb.is_configured():
            sys.exit("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_KEY)")
        backfill(since)
    else:
        print(__doc__)
