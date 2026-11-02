"""Assemble the 2004-2010 discharge records Kyle got from SFPUC (records request, Oct 2026)
from the committed inputs in ``data/csd/pre2011/transcription/``.

Outputs (all in ``data/csd/pre2011/``):
  bayside_daily_2004-07_2006-08.csv   one row per Bayside outfall GROUP per discharge day (hours + count, no MG)
  westside_daily_2008-01_2010-02.csv  one row per Westside outfall per discharge day (hours; MG from Feb 2009)
  monthly_coverage_2004-2011.csv      every month Jul 2004 -> Feb 2011, both plants: report in hand or not,
                                      discharge days found, SFPUC's stated count, whether they agree
  season_check.csv                    each wet season: what the reports in hand show vs SFPUC's own
                                      season-to-date count vs the permits' published season tables

Inputs (NOTES.md in that folder has the full story):
  bayside_parsed_rows_2004-2006.csv   parse_bayside_2004.py output; bayside_column_checks_2004-2006.csv beside it
  westside_hand_read_rows_2008-2010.csv  the five Westside months with discharges, read by eye, file + page
  westside_month_totals_2008-2010.csv    the printed column totals of those tables
  stated_counts_2004-2010.csv          SFPUC's monthly and season-to-date counts (cover letters, summary pages)
  published_season_counts.csv          Table F-3 (2019 Oceanside order) and Table F-4 (2013 Bayside order)

Nothing here is read by the site or the forecast yet.

Run from the repo root:  python features/forecast/src/collectors/csd_pre2011/build_pre2011.py
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "csd_ciwqs"))
from build_pre2018 import GROUPS, WEST_NAMES, group_key  # noqa: E402  same mapping as 2011-2016

ROOT = Path(__file__).resolve().parents[3] / "data" / "csd" / "pre2011"
T = ROOT / "transcription"
BAY, WEST = "Southeast/Bayside (CA0037664)", "Oceanside (CA0037681)"
BASIN = {"North": "North Shore", "Central": "Central", "South": "Southeast"}

# Reports with discharge-summary pages titled for the month before (rows take the filing month).
STALE_TITLE = {"October 2005 Bayside Monthly WW3.pdf": "2005-10"}
# Printed totals with no rows behind them, kept as printed.
TOTAL_NOTES = {
    ("2004-12", "015 Sansome Street"): "printed total 3.7 h, rows add to 3.8 (rounding)",
    ("2006-04", "31, 32, 33, 35, 37, 38 Islais Creek System"): "printed total 3.5 h, rows add to 3.6 (rounding)",
}
NARRATIVE_NOTES = {
    ("2004-10-19", "CSD-029"): "the table puts this Mariposa discharge on 10/19; the report's narrative says "
                               "10/19 had none and Mariposa discharged on 10/26",
    ("2006-04-03", "CSD-031–038"): "no count printed: a continuation of the 4/2 event (a new discharge "
                                   "needs six dry hours)",
}


def read(name: str) -> list[dict]:
    with open(T / name, newline="") as fh:
        return list(csv.DictReader(fh))


def write(name: str, rows: list[dict]) -> None:
    with open(ROOT / name, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def months(first: str, last: str) -> list[str]:
    y, m = map(int, first.split("-"))
    out = []
    while f"{y}-{m:02d}" <= last:
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def season(month: str) -> str:
    y, m = map(int, month.split("-"))
    start = y if m >= 7 else y - 1
    return f"{start}-{str(start + 1)[2:]}"


def bayside() -> list[dict]:
    rows = []
    for r in read("bayside_parsed_rows_2004-2006.csv"):
        group = r["column"].split(" ")
        key = group_key("".join(t for t in group if any(ch.isdigit() for ch in t)))
        oid, oname, basin, water = GROUPS[key]
        date = r["date"]
        if r["source"] in STALE_TITLE:
            date = STALE_TITLE[r["source"]] + date[7:]
        notes = [NARRATIVE_NOTES.get((date, oid), "")]
        rows.append(dict(date=date, facility=BAY, outfall_group=" ".join(t for t in group if any(ch.isdigit() for ch in t)),
                         outfall_id=oid, outfall_name=oname, report_basin=basin, receiving_water=water,
                         discharge_hours=r["discharge_hours"], discharge_count=r["discharge_count"].replace(".0", ""),
                         method="text_layer", source_document=r["source"], page=r["page"],
                         note="; ".join(n for n in notes if n)))
    rows.sort(key=lambda r: (r["date"], r["outfall_id"]))
    return rows


def westside() -> list[dict]:
    rows = []
    for r in read("westside_hand_read_rows_2008-2010.csv"):
        rows.append(dict(event_date=r["date"], facility=WEST, outfall_id=r["outfall_id"],
                         outfall_name=WEST_NAMES[r["outfall_id"]], basin="Oceanside", receiving_water="Pacific Ocean",
                         duration_hours=r["hours_as_printed"], volume_MG=r["volume_MG"],
                         volume_flag="" if r["volume_MG"] else "not_reported",
                         method="hand_read", source_document=r["source_file"], page=r["page"],
                         report_period=r["date"][:7], note=r["note"]))
    return rows


def check_westside_totals(rows: list[dict]) -> list[str]:
    sums = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        s = sums[(r["report_period"], r["outfall_id"])]
        s[0] += float(r["duration_hours"])
        s[1] += float(r["volume_MG"] or 0)
    problems = []
    for t in read("westside_month_totals_2008-2010.csv"):
        h, v = sums[(t["month"], t["outfall_id"])]
        if abs(h - float(t["hours_total_printed"])) > 0.06:
            problems.append(f"{t['month']} {t['outfall_id']} hours {h:.2f} vs printed {t['hours_total_printed']}")
        if t["volume_total_printed_MG"] and abs(v - float(t["volume_total_printed_MG"])) > 0.11:
            problems.append(f"{t['month']} {t['outfall_id']} MG {v:.2f} vs printed {t['volume_total_printed_MG']}")
    return problems


def coverage(bay: list[dict], west: list[dict]) -> list[dict]:
    inv = [r for r in csv.DictReader(open(ROOT / "inventory.csv", newline=""))
           if r["status"] == "new" and r["kind"] in ("monthly wet weather report", "monthly self-monitoring report",
                                                     "cover letter only")]
    have = {(r["plant"], r["report_month"]): r["file"] for r in inv}
    stated = defaultdict(dict)
    for s in read("stated_counts_2004-2010.csv"):
        if s["kind"] in ("month", "derived"):
            ym = f"{s['year']}-{int(s['month']):02d}"
            # the FY 04-05 one-page table and the monthly page can differ; keep both
            stated[(s["plant"], ym, s["basin"])].setdefault(s["source"].split(" (")[0], int(s["stated_count"]))
    days = defaultdict(set)
    for r in bay:
        if r["discharge_count"] not in ("", "0"):
            b = {"North Shore": "North Shore", "Southeast": "Southeast"}.get(r["report_basin"], "Central")
            days[("Southeast/Bayside", r["date"][:7], b)].add(r["date"])
    for r in west:
        days[("Oceanside/Westside", r["event_date"][:7], "Westside")].add(r["event_date"])
    out = []
    for plant, basins in (("Southeast/Bayside", ("North Shore", "Central", "Southeast")),
                          ("Oceanside/Westside", ("Westside",))):
        for ym in months("2004-07", "2011-02"):
            src = have.get((plant, ym), "")
            table_read = bool(src) and not src.startswith("OSP 0412")
            if plant.startswith("Ocean") and src and ym not in {"2008-01", "2009-02", "2009-03", "2010-01", "2010-02"}:
                table_read = False   # zero months rest on the cover letter; their tables were not read
            for b in basins:
                st = stated.get((plant, ym, b), {})
                found = len(days.get((plant, ym, b), ()))
                values = sorted(set(st.values()))
                if not src and not st:
                    status = "no_report"
                elif not src or not table_read:
                    status = "stated_only"
                else:
                    status = "events" if found else "zero"
                agrees = "" if not st or status in ("no_report", "stated_only") else str(found in values)
                out.append(dict(plant=plant, month=ym, basin=b, status=status, discharge_days_found=found if table_read else "",
                                stated_count="/".join(map(str, values)),
                                stated_by="; ".join(f"{k}: {v}" for k, v in st.items()),
                                agrees=agrees, source_document=src))
    return out


def seasons(cov: list[dict]) -> list[dict]:
    pub = defaultdict(dict)
    for p in read("published_season_counts.csv"):
        pub[(p["season"], p["plant"])][p["column"]] = int(p["count"])
    std = {}
    for s in read("stated_counts_2004-2010.csv"):
        if s["kind"] == "season_to_date":
            ym = f"{s['year']}-{int(s['month']):02d}"
            key = (season(ym), s["plant"], s["basin"])
            if key not in std or ym > std[key][0]:
                std[key] = (ym, int(s["stated_count"]))
    out = []
    for se in ("2004-05", "2005-06", "2006-07", "2007-08", "2008-09", "2009-10", "2010-11"):
        for plant, basins in (("Southeast/Bayside", ("North Shore", "Central", "Southeast")),
                              ("Oceanside/Westside", ("Westside",))):
            for b in basins:
                rows = [c for c in cov if c["plant"] == plant and c["basin"] == b and season(c["month"]) == se]
                found = sum(int(c["discharge_days_found"] or 0) for c in rows)
                missing = [c["month"] for c in rows if c["status"] in ("no_report",) or
                           (c["status"] == "stated_only" and c["stated_count"] not in ("", "0"))]
                p = pub.get((se, plant), {})
                published = p.get(b, "") if plant.startswith("Southeast") else \
                    " ".join(f"{k[-3:]}:{v}" for k, v in sorted(p.items()))
                last = std.get((se, plant, b))
                out.append(dict(season=se, plant=plant, basin=b, discharge_days_in_reports_read=found,
                                months_without_a_read_table=" ".join(missing),
                                sfpuc_season_to_date=f"{last[1]} (through {last[0]})" if last else "",
                                published_season_count=published))
    return out


def main() -> None:
    bay, west = bayside(), westside()
    problems = check_westside_totals(west)
    write("bayside_daily_2004-07_2006-08.csv", bay)
    write("westside_daily_2008-01_2010-02.csv", west)
    cov = coverage(bay, west)
    write("monthly_coverage_2004-2011.csv", cov)
    write("season_check.csv", seasons(cov))
    print(f"bayside {len(bay)} rows on {len({r['date'] for r in bay})} days; "
          f"westside {len(west)} rows on {len({r['event_date'] for r in west})} days")
    print("westside column totals:", "all agree" if not problems else problems)
    bad = [c for c in cov if c["agrees"] == "False"]
    print(f"coverage: {len(cov)} plant-month-basin rows, {len(bad)} disagree with SFPUC's count")
    for c in bad:
        print("  ", c["plant"], c["month"], c["basin"], "found", c["discharge_days_found"], "stated", c["stated_by"])


if __name__ == "__main__":
    main()
