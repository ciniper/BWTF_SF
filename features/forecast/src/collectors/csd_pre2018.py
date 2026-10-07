#!/usr/bin/env python3
"""
The older discharge reports (data/csd/pre2018/, Mar 2011 → the per-event format)
as OPT-IN training labels. Nothing served reads this: csd_labels.py, the ledger
and the frozen protocol's truth read only sf_csd_events.csv + its coverage grid.
A model set trained with these labels is a new candidate that names its record
(train_v4.build_dataset(record=...); the candidate manifest's ``record``).

What the files hold (their NOTES.md):
    Westside   one row per outfall per discharge DAY (calendar-day totals), Mar 2011 – Dec 2017
    Bayside    one row per outfall GROUP per discharge day, no volumes, Mar 2011 – Sep 2016

Labels per app basin and day (basins through shared/outfalls.REPORT_BASIN_TO_APP,
the map csd_labels uses):
    positive   any row in the basin that day: tiny volumes, Dec 11 2014's unmeasured
               rows and Dec 2012's basin-day rows (no outfall) included
    covered    the month's status is events / zero / stated_zero (Westside also
               basin_days_esmr: Dec 2012 holds basin daily totals only)
    left out   2015-06-10 Westside: a dry-weather equipment failure at Sea Cliff 1
               (~1,000 gal), not a rain overflow; neither positive nor negative

The modern label means an event STARTED on D; these rows are calendar-day totals,
so a discharge across midnight marks two days. Two readings (DAY_RULES):
    first   the first day of each run of consecutive discharge days in the basin
            (closest to the modern meaning; the run's other days are negatives,
            as a continuing day with no new start is in the modern frames)
    every   every discharge day

No volumes: Bayside has none before Oct 2016 and Westside's are day totals, so a
row here never feeds a volume head or a volume score (volume_known = 0).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from shared.outfalls import APP_BASINS, REPORT_BASIN_TO_APP  # noqa: E402

PRE_DIR = Path(__file__).resolve().parents[2] / "data" / "csd" / "pre2018"
WESTSIDE_CSV = PRE_DIR / "westside_daily_2011-03_2017-12.csv"
WESTSIDE_COVERAGE_CSV = PRE_DIR / "westside_monthly_coverage_2011-03_2017-12.csv"
BAYSIDE_CSV = PRE_DIR / "bayside_legacy_2011-03_2016-09.csv"
BAYSIDE_COVERAGE_CSV = PRE_DIR / "bayside_legacy_monthly_coverage_2011-03_2016-09.csv"

SOURCE = "csd_pre2018"                       # the frames' {basin}_label_source on these days
DAY_RULES = ("first", "every")
FIRST_DAY = pd.Timestamp("2011-03-01")
COVERED = {"Westside": ("events", "zero", "stated_zero", "basin_days_esmr"),
           "Bayside": ("events", "zero", "stated_zero")}
# (basin, day) rows that are not rain overflows: left out of the labels, never a positive
NOT_RAIN = {("Westside", pd.Timestamp("2015-06-10")): "dry-weather equipment failure at Sea Cliff 1 (~1,000 gal)"}


def _app_basin(report_basin: str) -> str:
    if report_basin not in REPORT_BASIN_TO_APP:
        raise KeyError(f"report basin {report_basin!r} is not in shared/outfalls.REPORT_BASIN_TO_APP")
    return REPORT_BASIN_TO_APP[report_basin]


def load_rows() -> pd.DataFrame:
    """Every discharge row of both files: date, app_basin, outfall (id or group id; '' for Dec 2012), facility."""
    w = pd.read_csv(WESTSIDE_CSV, dtype=str, keep_default_na=False)
    b = pd.read_csv(BAYSIDE_CSV, dtype=str, keep_default_na=False)
    rows = pd.concat([
        pd.DataFrame({"date": pd.to_datetime(w["event_date"]), "app_basin": w["basin"].map(_app_basin),
                      "outfall": w["outfall_id"], "facility": "Oceanside"}),
        pd.DataFrame({"date": pd.to_datetime(b["date"]), "app_basin": b["report_basin"].map(_app_basin),
                      "outfall": b["outfall_id"], "facility": "Bayside"}),
    ], ignore_index=True)
    return rows.sort_values(["date", "app_basin"]).reset_index(drop=True)


def coverage() -> pd.DataFrame:
    """One row per facility-month: facility ('Oceanside' | 'Bayside'), month (Period), status, covered."""
    out = []
    for fac, path in (("Oceanside", WESTSIDE_COVERAGE_CSV), ("Bayside", BAYSIDE_COVERAGE_CSV)):
        c = pd.read_csv(path, dtype=str, keep_default_na=False)
        ok = COVERED["Westside" if fac == "Oceanside" else "Bayside"]
        out.append(pd.DataFrame({"facility": fac,
                                 "month": pd.PeriodIndex.from_fields(year=c["year"].astype(int), month=c["month"].astype(int), freq="M"),
                                 "status": c["status"], "covered": c["status"].isin(ok)}))
    return pd.concat(out, ignore_index=True)


def _facility(basin: str) -> str:
    return "Oceanside" if basin == "Westside" else "Bayside"


def daily_labels(day_rule: str) -> pd.DataFrame:
    """One row per day from 2011-03-01 to the last covered month: per app basin B,
    {B}_csd (the day rule's positive), {B}_covered (1 = a known day), {B}_outfalls (rows
    that day: outfalls, or outfall groups on Bayside), {B}_status (the month's status;
    'not_rain' on a left-out day). Days outside a basin's months are covered 0."""
    if day_rule not in DAY_RULES:
        raise ValueError(f"day rule {day_rule!r}; known: {DAY_RULES}")
    rows, cov = load_rows(), coverage()
    last = cov["month"].max().to_timestamp(how="end").normalize()
    days = pd.DataFrame({"date": pd.date_range(FIRST_DAY, last)})
    month = days["date"].dt.to_period("M")
    for basin in APP_BASINS:
        fac = cov[cov["facility"] == _facility(basin)].set_index("month")
        status = month.map(fac["status"]).fillna("")
        covered = month.map(fac["covered"]).fillna(False).astype(bool)
        mine = rows[rows["app_basin"] == basin]
        n = mine.groupby("date").size()
        wet = days["date"].isin(n.index)
        if day_rule == "first":
            prev = days["date"] - pd.Timedelta(days=1)
            pos = wet & ~prev.isin(n.index)
        else:
            pos = wet
        stray = wet & ~covered
        if stray.any():
            raise ValueError(f"{basin}: a discharge row on {days['date'][stray].iloc[0].date()}, in a month not covered")
        for (b, d), why in NOT_RAIN.items():
            if b == basin:
                hit = days["date"] == d
                if not (hit & wet).any():
                    raise ValueError(f"{basin} {d.date()} ({why}) has no row: the left-out day moved")
                covered &= ~hit
                status = status.where(~hit, "not_rain")
        days[f"{basin}_csd"] = (pos & covered).astype(int)
        days[f"{basin}_covered"] = covered.astype(int)
        days[f"{basin}_outfalls"] = days["date"].map(n).fillna(0).astype(int)
        days[f"{basin}_status"] = status.values
    return days


def summary() -> dict:
    """Discharge days per basin and day rule, all and from 2016-03-01 (the served record's first day)."""
    out = {}
    for rule in DAY_RULES:
        d = daily_labels(rule)
        late = d["date"] >= pd.Timestamp("2016-03-01")
        out[rule] = {b: {"all": int(d[f"{b}_csd"].sum()), "from_2016_03": int(d.loc[late, f"{b}_csd"].sum()),
                         "covered_days": int(d[f"{b}_covered"].sum())} for b in APP_BASINS}
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=1))
