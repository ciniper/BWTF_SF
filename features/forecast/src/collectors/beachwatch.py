#!/usr/bin/env python3
"""
BeachWatch (California State Water Resources Control Board) → SF beach postings.

County health departments file every beach advisory with the State; the State
publishes them on data.ca.gov as "Beach Advisories (Postings and Closures) and
Beach Water Quality Monitoring". One row per station per advisory:
DateofAdvisory (signs up), DateOpened (signs down), AdvisoryType (Posting /
Rain / Closure), AdvisoryCause, Source, the sample values behind it, and the
SFPUC station name — the same ids as shared/stations.py ("OCEAN#15_SL").

This module keeps the San Francisco rows under data/beachwatch/:
  sf_beach_advisories.csv   one row per station-advisory, trimmed to the useful
                            columns, with registry station id, zone, cause class
  sf_posted_zone_days.csv   one row per (zone, day) on which one of the zone's
                            beaches was posted: cause class, stations, count
  manifest.json             fetched_at, source, counts, span, sha256 of the
                            statewide file — the provenance of the two CSVs

Semantics, checked against the 2016-17 Poo Bot feed archive (data/poobot/
feed_status.csv, SFPUC's own POSTED rows): a station is posted from
DateofAdvisory through DateOpened *inclusive* — 93% of BeachWatch posted
station-days show POSTED in the feed snapshots (98% within ±1 day); reading
DateOpened as already open scores worse both ways. CSO postings last a median
2 days (90th percentile 4); the East's run longer than the west's.

Cause classes:  cso   AdvisoryCause "Combined Sewer Overflow" or Source
                      "Combined Sewer Discharge"
                rain  AdvisoryType or AdvisoryCause "Rain" (the 72-hour rule)
                other Unknown / bacterial standards violation / spills — dry-
                      weather postings a rain model is not expected to see

Two pre-2016 advisories run for years (a filing left open); anything over
MAX_DURATION_DAYS is kept in the CSV but left out of the daily label.

Coverage: 1999 → the county's last filing (Feb 2026 at the 2026-09-26 pull;
SF files with a lag of months). Our own watcher's alert_log carries posting
transitions from Aug 2026 onward for the gap.

Refresh:  venv/bin/python features/forecast/src/collectors/beachwatch.py --refresh
          (downloads the ~30 MB statewide CSV; --local PATH to use a file)
Read:     load_advisories(), load_posted_zone_days(), posted_lookup()

Not yet a scorecard label — the grading change is tracked in TODO.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[4]))            # repo root → shared.*
sys.path.insert(0, str(HERE.parents[1] / "models"))  # groups.py
from shared.stations import STATIONS  # noqa: E402
from groups import SITE_GROUPS, ZONE_GROUPS  # noqa: E402

DATA_DIR = HERE.parents[2] / "data" / "beachwatch"
ADVISORIES_CSV = DATA_DIR / "sf_beach_advisories.csv"
POSTED_DAYS_CSV = DATA_DIR / "sf_posted_zone_days.csv"
MANIFEST = DATA_DIR / "manifest.json"

CKAN_PACKAGE = "https://data.ca.gov/api/3/action/package_show?id=beach-water-quality-postings-and-closures"
RESOURCE_ID = "d5cd6a23-829c-426d-a63e-689a55a3db9c"          # "Beach Posting and Closures- Advisories"
FALLBACK_URL = ("https://data.ca.gov/dataset/b9c8ce91-40ff-4ad3-8164-bc17c46afb44/resource/"
                f"{RESOURCE_ID}/download/beach-advisories.csv")
HEADERS = {"User-Agent": "bwtf-sf (BeachWatch postings ingest)"}

STATION_ZONE = {sid: zk for zk, groups in ZONE_GROUPS.items() for g in groups for sid in SITE_GROUPS[g][1]}
# stations BeachWatch has that the live feed / registry no longer carries
EXTRA_STATION_ZONE = {"BAY#202.2_SL": "north"}   # Crissy Field, Trees (19 advisories, retired)

KEEP = {
    "Advisory id": "advisory_id", "Station_Name": "station_name", "Station_Description": "station_description",
    "Beach_Name": "beach_name", "AdvisoryType": "advisory_type", "AdvisoryCause": "cause", "Source": "source",
    "DateofAdvisory": "posted_on", "TimeofAdvisory": "posted_time", "DateOpened": "reopened_on", "TimeOpened": "reopened_time",
    "Description": "description", "Advisory Comments": "comments",
    "Enterococcus": "enterococcus", "Fecal Coliforms": "fecal_coliforms", "Total Coliforms": "total_coliforms", "E.Coli": "e_coli",
    "Status": "status", "Advisory Record Create Date": "record_created", "Station_UpperLat": "lat", "Station_UpperLon": "lon",
}
CAUSE_CLASSES = ("cso", "rain", "other")
MAX_DURATION_DAYS = 60   # longer = a filing left open (a 2008 Ocean Beach rain advisory "closed" in 2015); kept in the CSV, left out of the daily label


def cause_class(advisory_type: str, cause: str, source: str) -> str:
    if cause == "Combined Sewer Overflow" or source == "Combined Sewer Discharge":
        return "cso"
    if advisory_type == "Rain" or cause == "Rain":
        return "rain"
    return "other"


# ── fetch ───────────────────────────────────────────────────────────────────

def resolve_csv_url() -> str:
    """The advisories resource's current download URL (CKAN), else the known one."""
    try:
        meta = requests.get(CKAN_PACKAGE, headers=HEADERS, timeout=60).json()["result"]
        for r in meta["resources"]:
            if r["id"] == RESOURCE_ID:
                return r["url"]
    except Exception:  # noqa: BLE001  (offline / portal down → fixed URL)
        pass
    return FALLBACK_URL


def fetch_statewide(url: str | None = None, local: Path | None = None) -> tuple[pd.DataFrame, str, str]:
    """(statewide dataframe, source description, sha256)."""
    if local is not None:
        raw = Path(local).read_bytes()
        src = f"local file {Path(local).name}"
    else:
        url = url or resolve_csv_url()
        r = requests.get(url, headers=HEADERS, timeout=600)
        r.raise_for_status()
        raw, src = r.content, url
    df = pd.read_csv(io.BytesIO(raw), low_memory=False, encoding_errors="replace")
    return df, src, hashlib.sha256(raw).hexdigest()


# ── transform ───────────────────────────────────────────────────────────────

def sf_rows(state: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """San Francisco station-advisories, trimmed, dated, mapped to our zones."""
    m = (state["County"].astype(str).str.strip().eq("San Francisco")
         & state["CountyCode"].astype(str).str.strip().eq("SF"))
    df = state.loc[m, list(KEEP)].rename(columns=KEEP).copy()
    for c in ("posted_on", "reopened_on", "record_created"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    bad = df.index[df.posted_on.isna() | df.reopened_on.isna()
                   | (df.posted_on.dt.year < 1990) | (df.reopened_on.dt.year < 1990)
                   | (df.reopened_on < df.posted_on)]
    dropped = df.loc[bad, ["advisory_id", "station_name", "posted_on", "reopened_on"]].astype(str).to_dict("records")
    df = df.drop(bad)
    df["station"] = df.station_name.where(df.station_name.isin(STATIONS.keys()), "")
    df["zone"] = df.station_name.map(STATION_ZONE).fillna(df.station_name.map(EXTRA_STATION_ZONE)).fillna("")
    df["cause_class"] = [cause_class(t, c, s) for t, c, s in zip(df.advisory_type.astype(str), df.cause.astype(str), df.source.astype(str))]
    df["duration_days"] = (df.reopened_on - df.posted_on).dt.days
    df = df.sort_values(["posted_on", "station_name", "advisory_id"]).reset_index(drop=True)
    unmapped = df.loc[df.zone.eq(""), "station_name"].value_counts().to_dict()
    return df, {"dropped_bad_dates": dropped, "unmapped_stations": unmapped}


def posted_zone_days(adv: pd.DataFrame) -> pd.DataFrame:
    """(zone, date) rows for every day a zone had a posted beach — inclusive of
    the reopening day (see module docstring); advisories longer than
    MAX_DURATION_DAYS are left out as filing artifacts."""
    rows: dict[tuple, dict] = {}
    for r in adv.itertuples(index=False):
        if not r.zone or r.duration_days > MAX_DURATION_DAYS:
            continue
        for d in pd.date_range(r.posted_on.normalize(), r.reopened_on.normalize()):
            e = rows.setdefault((r.zone, d), {"classes": set(), "stations": set(), "n": 0})
            e["classes"].add(r.cause_class)
            e["stations"].add(r.station_name)
            e["n"] += 1
    out = pd.DataFrame([{"zone": z, "date": d,
                         "cause_class": next(c for c in CAUSE_CLASSES if c in e["classes"]),
                         "n_advisories": e["n"], "stations": "|".join(sorted(e["stations"]))}
                        for (z, d), e in rows.items()])
    return out.sort_values(["date", "zone"]).reset_index(drop=True)


# ── read ────────────────────────────────────────────────────────────────────

def load_advisories() -> pd.DataFrame:
    df = pd.read_csv(ADVISORIES_CSV, dtype={"station": str, "zone": str}, keep_default_na=False, na_values=[""])
    for c in ("posted_on", "reopened_on", "record_created"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["station"] = df["station"].fillna("")
    df["zone"] = df["zone"].fillna("")
    return df


def load_posted_zone_days() -> pd.DataFrame:
    df = pd.read_csv(POSTED_DAYS_CSV)
    df["date"] = pd.to_datetime(df["date"])
    return df


def posted_lookup() -> dict:
    """{zone: {Timestamp(day): cause_class}} — the daily posting label."""
    out: dict = {z: {} for z in ZONE_GROUPS}
    for r in load_posted_zone_days().itertuples(index=False):
        out.setdefault(r.zone, {})[r.date] = r.cause_class
    return out


# ── refresh ─────────────────────────────────────────────────────────────────

def refresh(local: Path | None = None) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state, src, sha = fetch_statewide(local=local)
    adv, notes = sf_rows(state)
    days = posted_zone_days(adv)
    out = adv.copy()
    for c in ("posted_on", "reopened_on", "record_created"):
        out[c] = out[c].dt.strftime("%Y-%m-%d")
    out.to_csv(ADVISORIES_CSV, index=False)
    d2 = days.copy()
    d2["date"] = d2["date"].dt.strftime("%Y-%m-%d")
    d2.to_csv(POSTED_DAYS_CSV, index=False)
    man = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": src, "dataset": "https://data.ca.gov/dataset/beach-water-quality-postings-and-closures",
        "resource_id": RESOURCE_ID, "statewide_rows": int(len(state)), "statewide_sha256": sha,
        "sf_rows": int(len(adv)), "dropped_bad_dates": notes["dropped_bad_dates"], "unmapped_stations": notes["unmapped_stations"],
        "excluded_long_advisories": adv.loc[adv.duration_days > MAX_DURATION_DAYS, ["advisory_id", "station_name", "advisory_type", "cause", "posted_on", "reopened_on", "duration_days"]].astype(str).to_dict("records"),
        "span": [adv.posted_on.min().strftime("%Y-%m-%d"), adv.reopened_on.max().strftime("%Y-%m-%d")],
        "advisory_types": adv.advisory_type.value_counts().to_dict(),
        "cause_classes": adv.cause_class.value_counts().to_dict(),
        "posted_zone_days": {z: int((days.zone == z).sum()) for z in ZONE_GROUPS},
        "cso_posting_duration_days": {"median": float(adv.loc[adv.cause_class == "cso", "duration_days"].median()),
                                      "p90": float(adv.loc[adv.cause_class == "cso", "duration_days"].quantile(0.9))},
        "convention": "a station is posted from posted_on through reopened_on inclusive (validated vs data/poobot/feed_status.csv)",
    }
    MANIFEST.write_text(json.dumps(man, indent=2))
    return man


def summary(man: dict) -> str:
    return (f"BeachWatch SF postings: {man['sf_rows']} station-advisories {man['span'][0]} → {man['span'][1]} "
            f"({man['cause_classes']}); posted zone-days {man['posted_zone_days']}; "
            f"unmapped {man['unmapped_stations'] or 'none'}; dropped {len(man['dropped_bad_dates'])} bad-date rows; "
            f"fetched {man['fetched_at']} from {man['source']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true", help="download the statewide CSV and rewrite data/beachwatch/")
    ap.add_argument("--local", type=Path, help="use an already-downloaded statewide CSV instead of fetching")
    a = ap.parse_args()
    if a.refresh or a.local:
        print(summary(refresh(a.local)))
    else:
        print(summary(json.loads(MANIFEST.read_text())) if MANIFEST.exists() else "no data yet — run with --refresh")
