"""Assemble the pre-modern discharge record (Mar 2011 → the modern per-event format)
from the committed transcription inputs in ``data/csd/pre2018/transcription/``.

Outputs (all in ``data/csd/pre2018/``):
  westside_daily_2011-03_2017-12.csv           one row per Westside outfall per discharge day (hours + MG)
  westside_monthly_coverage_2011-03_2017-12.csv every month: status, printed total, row sum, source page
  bayside_legacy_2011-03_2016-09.csv            one row per Bayside outfall GROUP per discharge day (hours + count, no MG)
  bayside_legacy_monthly_coverage_2011-03_2016-09.csv

Inputs and how they were made (NOTES.md in that folder has the full story):
  westside_text_layer_rows.csv  2011-03 → 2012-10 tables with a text layer, parsed by column position
  westside_hand_read_rows.csv   2012-11 and 2013-01 → 2017-12 scanned tables, read by eye, each row with file + page
  westside_months.csv           one row per month: status, printed month total, method, source
  esmr_westside_eff_csd_flow_2011-2020.csv  the state's eSMR analytical rows (Dec 2012 fallback only)
  bayside_parsed_rows_2011_2012.csv / _2013_2016.csv  parse_old_sep.py output (filing month wins over a stale page header)
  bayside_hand_read_rows.csv    Bayside months whose scans parse badly, read by eye

These files are NOT read by the forecast: training labels and the Model check use
sf_csd_events.csv + sf_csd_monthly_coverage.csv only, which this script never touches.

Run from the repo root:  python features/forecast/src/collectors/csd_ciwqs/build_pre2018.py
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "data" / "csd" / "pre2018"
T = ROOT / "transcription"

WEST_NAMES = {"CSD-001": "Lake Merced", "CSD-002": "Vicente", "CSD-003": "Lincoln", "CSD-004": "Mile Rock",
              "CSD-005": "Sea Cliff #1", "CSD-006": "Sea Cliff Brick Sewer", "CSD-007": "Sea Cliff #2"}
NORTH, MISSION, ISLAIS = "SF Bay - North Shore waterfront", "Mission Creek / China Basin", "Islais Creek"
SOUTH = "SF Bay - Southeast (Yosemite Slough / Candlestick)"
# Legacy Bayside reports group outfalls by structure. Single-outfall groups keep the
# registry id (shared/outfalls.py); multi-outfall groups get a range id that names them.
# Receiving waters match the labels sf_csd_events.csv uses, so the ledger's water filter
# treats old and new rows alike.
GROUPS = {
    "009": ("CSD-009", "Baker Street", "North Shore", NORTH),
    "010": ("CSD-010", "Pierce Street", "North Shore", NORTH),
    "011": ("CSD-011", "Laguna Street", "North Shore", NORTH),
    "013": ("CSD-013", "Beach Street", "North Shore", NORTH),
    "015": ("CSD-015", "Sansome Street", "North Shore", NORTH),
    "017": ("CSD-017", "Jackson Street", "North Shore", NORTH),
    "18,19,22,23,24,25,26,27,28": ("CSD-018–028", "Mission Creek System (9 outfalls, CSD-018 to 028)", "Central (Mission Creek)", MISSION),
    "29": ("CSD-029", "Mariposa Street", "Central (Islais Creek)", ISLAIS),
    "30,30a": ("CSD-030/030A", "Twentieth Street (20th St and 22nd St)", "Central (Islais Creek)", ISLAIS),
    "31,32,33,35": ("CSD-031–035", "Islais Creek System (CSD-031, 031A, 032, 033, 035)", "Central (Islais Creek)", ISLAIS),
    "31,31A,32,33,35": ("CSD-031–035", "Islais Creek System (CSD-031, 031A, 032, 033, 035)", "Central (Islais Creek)", ISLAIS),
    "31,32,33,35,37,38": ("CSD-031–038", "Islais Creek System with Evans and Hudson (2011 grouping)", "Central (Islais Creek)", ISLAIS),
    "37,38": ("CSD-037/038", "Evans and Hudson", "Southeast", SOUTH),
    "40,41,42": ("CSD-040–042", "Griffith Street System (CSD-040, 041, 042)", "Southeast", SOUTH),
    "43": ("CSD-043", "Sunnydale Avenue", "Southeast", SOUTH),
    "SoutheastBasin(outfallnotstated)": ("Southeast (not stated)", "Southeast Basin, outfall not stated", "Southeast", SOUTH),
}
FEB_2014_NOTE = ("added 2026-10-06: this month's attachment is named 'WW Report', which the scraper's "
                 "'wet weather' filter skipped")


def group_key(g: str) -> str:
    return re.sub(r"[#\s]", "", g)


def doc_id(fname: str) -> str:
    m = re.match(r"(?:OSP|SEP)_\d{4}-\d{2}_(\d+)_\d+_", fname or "")
    return m.group(1) if m else ""


def hours(raw: str):
    if raw in ("", "NA", None):
        return None
    return int(raw.split(":")[0]) + int(raw.split(":")[1]) / 60 if ":" in raw else float(raw)


def read(name: str) -> list[dict]:
    with open(T / name, newline="") as fh:
        return list(csv.DictReader(fh))


def westside() -> tuple[list[dict], list[dict]]:
    rows = []
    for r in read("westside_text_layer_rows.csv"):
        rows.append(dict(event_date=r["date"], outfall_id=r["outfall_id"], hours_as_printed=r["hours_raw"],
                         duration_hours=round(hours(r["hours_raw"]), 3), volume_MG=r["volume_MG"], volume_flag="",
                         method="text_layer", source_document=r["source_file"], page="", note=""))
    for r in read("westside_hand_read_rows.csv"):
        h = hours(r["hours_raw"])
        na = r["volume_MG"] == "NA"
        rows.append(dict(event_date=r["date"], outfall_id=r["outfall_id"], hours_as_printed=r["hours_raw"],
                         duration_hours=round(h, 3) if h is not None else "", volume_MG="" if na else r["volume_MG"],
                         volume_flag="not_measured" if na else "", method="hand_read",
                         source_document=r["source_file"], page=r["page"], note=r["note"]))
    for r in read("esmr_westside_eff_csd_flow_2011-2020.csv"):   # Dec 2012 only: no per-outfall table was filed
        if r["calculated_method"] == "NA" and r["sampling_date"][:7] == "2012-12":
            rows.append(dict(event_date=r["sampling_date"][:10], outfall_id="", hours_as_printed="", duration_hours="",
                             volume_MG=r["result"], volume_flag="basin_total_from_eSMR_analytical", method="eSMR_analytical",
                             source_document="data.ca.gov eSMR Analytical Data 2012 (location EFF-CSD, parameter Flow)", page="",
                             note="no per-outfall table online for Dec 2012; basin-day volume; cover letter: 5 discharges this month"))
    rows.sort(key=lambda r: (r["event_date"], r["outfall_id"]))
    for r in rows:
        r.update(facility="Oceanside (CA0037681)", outfall_name=WEST_NAMES.get(r["outfall_id"], "Westside, outfall not stated"),
                 basin="Oceanside", receiving_water="Pacific Ocean", report_period=r["event_date"][:7],
                 ciwqs_document_id=doc_id(r["source_document"]) or ("781911" if r["event_date"].startswith("2012-12") else ""))
    months = []
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["event_date"][:7]].append(r)
    for m in read("westside_months.csv"):
        ym = f"{int(m['year'])}-{int(m['month']):02d}"
        mr = by_month.get(ym, [])
        months.append(dict(facility="Oceanside (CA0037681)", year=int(m["year"]), month=int(m["month"]), status=m["status"],
                           month_total_MG=m["month_total_MG"],
                           rows_volume_sum_MG=round(sum(float(r["volume_MG"]) for r in mr if r["volume_MG"] not in ("", None)), 4),
                           n_rows=len(mr), n_discharge_days=len({r["event_date"] for r in mr}), method=m["method"],
                           source_document=m["source_file"], ciwqs_document_id=doc_id(m["source_file"]), page=m["page"], note=m["note"]))
    months.sort(key=lambda m: (m["year"], m["month"]))
    return rows, months


def bayside() -> tuple[list[dict], list[dict]]:
    rows = []
    for name, method in (("bayside_parsed_rows_2011_2012.csv", "parsed"), ("bayside_parsed_rows_2013_2016.csv", "parsed"),
                         ("bayside_hand_read_rows.csv", "hand_read")):
        for r in read(name):
            src = r.get("source", "")
            note = r.get("note", "") or (FEB_2014_NOTE if "February_2014_Bayside_WW_Report" in src else "")
            oid, oname, basin, water = GROUPS[group_key(r["outfall_group"])]
            rows.append(dict(date=r["date"], facility="Southeast/Bayside (CA0037664)", outfall_group=r["outfall_group"],
                             outfall_id=oid, outfall_name=oname, report_basin=basin, receiving_water=water,
                             discharge_hours=r["discharge_hours"], discharge_count=r["discharge_count"], method=method,
                             source_document=src, ciwqs_document_id=doc_id(src), page=r.get("page", ""), note=note))
    rows.sort(key=lambda r: (r["date"], r["outfall_id"]))
    stated = defaultdict(dict)
    with open(ROOT / "stated_monthly_counts_2011-2017.csv", newline="") as fh:
        for s in csv.DictReader(fh):
            if s["plant"].startswith("Southeast"):
                stated[(int(s["year"]), int(s["month"]))][s["basin"]] = int(s["stated_count"])
    no_report = {(2012, 2): "filed as a spreadsheet and a Word narrative, not a PDF report",
                 (2015, 8): "no wet weather report attached", (2016, 8): "no wet weather report attached",
                 (2016, 9): "no wet weather report attached"}
    counts = Counter()
    for r in rows:
        b = {"North Shore": "North Shore", "Southeast": "Southeast"}.get(r["report_basin"], "Central")
        counts[(int(r["date"][:4]), int(r["date"][5:7]), b)] += float(r["discharge_count"] or 0)
    months = []
    y, m = 2011, 3
    while (y, m) <= (2016, 9):
        mr = [r for r in rows if r["date"][:7] == f"{y}-{m:02d}"]
        st = stated.get((y, m), {})
        parsed = {b: int(counts[(y, m, b)]) for b in ("North Shore", "Central", "Southeast")}
        if (y, m) in no_report:
            status, note = ("stated_zero" if not any(st.values()) else "no_report"), no_report[(y, m)] + "; the annual report lists " + (
                "no discharges" if not any(st.values()) else str(st))
        else:
            status, note = ("events" if mr else "zero"), ""
        months.append(dict(facility="Southeast/Bayside (CA0037664)", year=y, month=m, status=status, n_rows=len(mr),
                           n_discharge_days=len({r["date"] for r in mr}),
                           counts_parsed=" / ".join(f"{b} {parsed[b]}" for b in parsed),
                           counts_stated=" / ".join(f"{b} {st.get(b, '')}" for b in ("North Shore", "Central", "Southeast")),
                           counts_agree=all(parsed[b] == st.get(b, 0) for b in parsed), note=note))
        y, m = (y + (m == 12), m % 12 + 1)
    return rows, months


def write(path: Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", newline="") as fo:
        w = csv.DictWriter(fo, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def main() -> None:
    wr, wm = westside()
    write(ROOT / "westside_daily_2011-03_2017-12.csv", wr,
          ["event_date", "facility", "outfall_id", "outfall_name", "basin", "receiving_water", "duration_hours", "hours_as_printed",
           "volume_MG", "volume_flag", "method", "source_document", "ciwqs_document_id", "page", "report_period", "note"])
    write(ROOT / "westside_monthly_coverage_2011-03_2017-12.csv", wm,
          ["facility", "year", "month", "status", "month_total_MG", "rows_volume_sum_MG", "n_rows", "n_discharge_days", "method",
           "source_document", "ciwqs_document_id", "page", "note"])
    br, bm = bayside()
    write(ROOT / "bayside_legacy_2011-03_2016-09.csv", br,
          ["date", "facility", "outfall_group", "outfall_id", "outfall_name", "report_basin", "receiving_water", "discharge_hours",
           "discharge_count", "method", "source_document", "ciwqs_document_id", "page", "note"])
    write(ROOT / "bayside_legacy_monthly_coverage_2011-03_2016-09.csv", bm,
          ["facility", "year", "month", "status", "n_rows", "n_discharge_days", "counts_parsed", "counts_stated", "counts_agree", "note"])
    print(f"westside: {len(wr)} rows, {len({r['event_date'] for r in wr})} discharge days, {len(wm)} months")
    print(f"bayside:  {len(br)} rows, {len({r['date'] for r in br})} discharge days, {len(bm)} months, "
          f"{sum(not m['counts_agree'] for m in bm)} months whose counts differ from the annual report")


if __name__ == "__main__":
    main()
