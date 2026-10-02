#!/usr/bin/env python3
"""
Ground-truth CSD labels from SFPUC self-monitoring reports.

Builds per-day, per-basin discharge labels from data/csd/sf_csd_events.csv
(per-event records extracted from CIWQS SMR filings — see data/csd/NOTES.md).
These replace the bacteria proxy label ("3+ stations elevated across 2+
basins") that train.py used before ground truth was available.

Basin mapping (CSD report basins → forecast app basins):
    Oceanside                → Westside     (CSD-001..007; Ocean Beach, Mile Rock, Sea Cliff)
    North Shore              → North Shore  (CSD-009..017; Marina/Aquatic Park/piers)
    Central (Islais Creek)   → Southeast    (CSD-029..035; nearest bacteria stations
    Southeast                → Southeast     are BAY#320 Islais, BAY#230 Crane Cove,
                                             and the Candlestick trio BAY#300.1/301.1/301.2)
    Central (Mission Creek)  → Central      (CSD-018..028; BAY#220 Mission Creek —
                                             own stage-1 model since the 2026-09 retrain)

Coverage: Bayside from Oct 2016, Oceanside from Jan 2018, through the last
month in data/csd/sf_csd_monthly_coverage.csv (Jul 2026 after the 2026-09-24
refresh, continuous). Use load_coverage() to distinguish
verified-zero months from no-data months — days outside covered months must
NOT be treated as negatives.
"""

from pathlib import Path

import pandas as pd

CSD_DIR = Path(__file__).parent.parent.parent / "data" / "csd"
EVENTS_CSV = CSD_DIR / "sf_csd_events.csv"
COVERAGE_CSV = CSD_DIR / "sf_csd_monthly_coverage.csv"

# CSD report basin -> forecast app basin. The outfall registry
# (shared/outfalls.py) is the single source of truth; "Central" is the
# Mission Creek basin (CSD-018..028), which has had its own stage-1 model
# since the 2026-09 retrain. Islais Creek outfalls report under "Central (Islais
# Creek)" but post the Southeast beaches, so they are Southeast here.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
from shared.outfalls import APP_BASINS as _APP_BASINS, REPORT_BASIN_TO_APP  # noqa: E402

BASIN_MAP = dict(REPORT_BASIN_TO_APP)

# Basins the beach-risk models serve — all four; every one has at least one
# BWTF bacteria station (Central = BAY#220 Mission Creek).
APP_BASINS = list(_APP_BASINS)

# Coverage grid statuses that mean "we affirmatively know whether events occurred"
_COVERED_STATUSES = {
    "events_parsed",
    "table_present_zero_events",
    "no_table_stated_no_discharge",
}


def load_events() -> pd.DataFrame:
    """Load per-event CSD records with the app-basin column added."""
    df = pd.read_csv(EVENTS_CSV, parse_dates=["event_date"])
    df["app_basin"] = df["basin"].map(BASIN_MAP)
    return df


def load_coverage() -> pd.DataFrame:
    """Monthly coverage grid: which facility-months have trustworthy labels."""
    cov = pd.read_csv(COVERAGE_CSV)
    cov["covered"] = cov["status"].isin(_COVERED_STATUSES)
    return cov


def covered_dates(facility_prefix: str) -> pd.DatetimeIndex:
    """All calendar dates in months where `facility_prefix` ('Oceanside' or
    'Southeast') has trustworthy labels (events or verified zero)."""
    cov = load_coverage()
    cov = cov[cov["facility"].str.startswith(facility_prefix) & cov["covered"]]
    dates = []
    for _, row in cov.iterrows():
        start = pd.Timestamp(int(row["year"]), int(row["month"]), 1)
        dates.append(pd.date_range(start, start + pd.offsets.MonthEnd(0)))
    return pd.DatetimeIndex([]).append(dates) if dates else pd.DatetimeIndex([])


def facility_covered_dates(facility: str) -> pd.DatetimeIndex:
    """covered_dates for a registry facility name (Outfall.facility / geography
    Basin.facility: 'Oceanside' | 'Bayside'). The coverage grid writes the permit
    as 'Southeast/Bayside (CA0037664)', so match on its name parts; exactly one
    grid facility must match."""
    grid = load_coverage()["facility"].unique()
    hits = [f for f in grid if facility in f.split(" (")[0].split("/")]
    if len(hits) != 1:
        raise KeyError(f"facility {facility!r} matches {hits} in {COVERAGE_CSV.name}")
    return covered_dates(hits[0])


def build_daily_labels(geo=None) -> pd.DataFrame:
    """
    One row per calendar day over the covered span, with per-basin ground truth.

    Columns per app basin B in APP_BASINS (Westside, North Shore, Central, Southeast):
        {B}_csd          1 if any discharge event started in basin that day
        {B}_volume_mg    total reported discharge volume that day (MG)
        {B}_outfalls     number of distinct outfalls that discharged
        {B}_covered      1 if that basin's facility has trustworthy labels
                         for that month (0 → treat label as unknown, not negative)
    Plus citywide:
        csd_any, csd_volume_mg, csd_outfalls  (over covered basins that day)

    ``geo`` (a shared.geography.Geography) relabels the same ledger rows: B runs
    over geo.basins' display names, each event takes its basin from its outfall
    through the geography (an outfall the geography lacks raises), and coverage
    follows Basin.facility. None = the served geo_v1 frame, built as v4 was;
    geo=GEO_V1 reproduces it exactly (tests/test_geography.py).
    """
    ev = load_events()
    if geo is None:
        all_basins, ev_basin = list(APP_BASINS), ev["app_basin"]
        ocean_cov = covered_dates("Oceanside")
        bay_cov = covered_dates("Southeast")
        cov_of = {b: ocean_cov if b == "Westside" else bay_cov for b in all_basins}
    else:
        all_basins = [b.name for b in geo.basins]
        ev_basin = ev["outfall_id"].map({o: geo.basin(geo.basin_of_outfall(o)).name for o in ev["outfall_id"].unique()})
        fac = {f: facility_covered_dates(f) for f in dict.fromkeys(b.facility for b in geo.basins)}
        cov_of = {b.name: fac[b.facility] for b in geo.basins}

    start = ev["event_date"].min().replace(day=1)
    end = max(c.max() for c in cov_of.values())  # last month in the CIWQS coverage grid (re-harvested quarterly)
    days = pd.DataFrame({"date": pd.date_range(start, end)})

    for basin in all_basins:
        b = ev[ev_basin == basin]
        daily = b.groupby("event_date").agg(
            volume=("volume_MG", "sum"),
            outfalls=("outfall_id", "nunique"),
        )
        days[f"{basin}_csd"] = days["date"].isin(daily.index).astype(int)
        days[f"{basin}_volume_mg"] = days["date"].map(daily["volume"]).fillna(0.0)
        days[f"{basin}_outfalls"] = days["date"].map(daily["outfalls"]).fillna(0).astype(int)
        days[f"{basin}_covered"] = days["date"].isin(cov_of[basin]).astype(int)

    cov_cols = [f"{b}_covered" for b in all_basins]
    csd_cols = [f"{b}_csd" for b in all_basins]
    vol_cols = [f"{b}_volume_mg" for b in all_basins]
    days["csd_any"] = (days[csd_cols].sum(axis=1) > 0).astype(int)
    days["csd_volume_mg"] = days[vol_cols].sum(axis=1)
    days["csd_outfalls"] = days[[f"{b}_outfalls" for b in all_basins]].sum(axis=1)
    days["fully_covered"] = (days[cov_cols].sum(axis=1) == len(cov_cols)).astype(int)

    return days


if __name__ == "__main__":
    labels = build_daily_labels()
    covered = labels[labels["fully_covered"] == 1]
    print(f"Daily label table: {len(labels)} days "
          f"({labels['date'].min().date()} → {labels['date'].max().date()})")
    print(f"Fully covered days (both facilities): {len(covered)}")
    print(f"Citywide event days (covered span): {covered['csd_any'].sum()}")
    for basin in APP_BASINS:
        sub = labels[labels[f"{basin}_covered"] == 1]
        n = sub[f"{basin}_csd"].sum()
        vol = sub[f"{basin}_volume_mg"].sum()
        print(f"  {basin:14s}: {len(sub)} covered days, {n} event days, {vol:,.0f} MG total")
