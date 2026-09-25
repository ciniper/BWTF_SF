"""Step 0 of the CIWQS pipeline: build ``smr_documents.json`` — the monthly
SMR document ids for SFPUC's two plants — from the eSMR analytical datastore
on data.ca.gov (one resource per year, SQL API, no key).

    python list_documents.py 2025 2026        # years to (re)index
    python list_documents.py 2026 --only-new  # skip months already in data/csd/sf_csd_monthly_coverage.csv

Output shape (what harvest_index.py reads):
    {"2026": [{"smr_document_id": "3063421", "facility_place_id": "256499",
               "report_name": "Monthly SMR ( MONNPDES ) report for January 2026"}, ...]}

Caveat learned 2026-09-24: a month is listed only once its analytical rows are
loaded on data.ca.gov. SFPUC's Nov and Dec 2025 SMRs were absent from both the
2025 and 2026 resources even though Jan–Jul 2026 were present — check the
printed month list against what you expect before running the next steps.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import requests

CKAN = "https://data.ca.gov/api/3/action"
PACKAGE = "water-quality-effluent-electronic-self-monitoring-report-esmr-data"
FACILITIES = {"256498": "Oceanside (CA0037681)", "256499": "Southeast/Bayside (CA0037664)"}
COVERAGE = Path(__file__).resolve().parents[3] / "data" / "csd" / "sf_csd_monthly_coverage.csv"
MONTHS = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                            "August", "September", "October", "November", "December"])}


def year_resources() -> dict[str, str]:
    """{"2026": resource_id, ...} for the per-year analytical CSV resources."""
    pkg = requests.get(f"{CKAN}/package_show", params={"id": PACKAGE}, timeout=60).json()["result"]
    out = {}
    for r in pkg["resources"]:
        m = re.match(r"(\d{4}) eSMR Analytical Data", r.get("name") or "")
        if m and r.get("datastore_active"):
            out[m.group(1)] = r["id"]
    return out


def documents(resource_id: str) -> list[dict]:
    sql = (f'SELECT DISTINCT smr_document_id, report_name, facility_place_id FROM "{resource_id}" '
           f"WHERE facility_place_id IN ({', '.join(repr(k) for k in FACILITIES)}) AND report_name LIKE 'Monthly SMR%' "
           f"ORDER BY facility_place_id, smr_document_id")
    r = requests.get(f"{CKAN}/datastore_search_sql", params={"sql": sql}, timeout=120).json()
    if not r.get("success"):
        raise SystemExit(f"datastore_search_sql failed: {str(r)[:300]}")
    return [{"smr_document_id": str(x["smr_document_id"]), "facility_place_id": str(x["facility_place_id"]),
             "report_name": x["report_name"]} for x in r["result"]["records"]]


def covered_months() -> set[tuple[str, int, int]]:
    if not COVERAGE.exists():
        return set()
    with COVERAGE.open() as f:
        return {(row["facility"], int(row["year"]), int(row["month"])) for row in csv.DictReader(f)}


def main(argv: list[str]) -> None:
    only_new = "--only-new" in argv
    years = [a for a in argv if re.fullmatch(r"\d{4}", a)]
    if not years:
        raise SystemExit(__doc__)
    res = year_resources()
    done = covered_months() if only_new else set()
    out: dict[str, list[dict]] = {}
    for y in years:
        if y not in res:
            print(f"{y}: no datastore resource yet")
            continue
        docs = documents(res[y])
        kept = []
        for d in docs:
            m = re.search(r"for (\w+) (\d{4})", d["report_name"])
            key = (FACILITIES[d["facility_place_id"]], int(m.group(2)), MONTHS[m.group(1)]) if m else None
            if only_new and key in done:
                continue
            kept.append(d)
            print(f"  {y}  {FACILITIES[d['facility_place_id']][:9]:9}  {d['smr_document_id']}  {d['report_name']}")
        out[y] = kept
    json.dump(out, open("smr_documents.json", "w"), indent=1)
    print("wrote smr_documents.json:", {y: len(v) for y, v in out.items()})


if __name__ == "__main__":
    main(sys.argv[1:])
