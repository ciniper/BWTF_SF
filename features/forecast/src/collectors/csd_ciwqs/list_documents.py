"""Step 0 of the CIWQS pipeline: build ``smr_documents.json`` — the monthly
SMR document ids for SFPUC's two plants.

Primary source (2026-09-24 on): CIWQS's own eSMR At-A-Glance search, filtered
by party name. It is the authoritative index — every document SFPUC has filed,
with reporting period and date received, no lag. The data.ca.gov analytical
datastore (``--datastore``) is kept as a cross-check; it lags and can miss
months entirely (SFPUC's Nov–Dec 2025 never appeared there even though CIWQS
had both plants' filings within a month of the due date).

    python list_documents.py                 # every Monthly SMR for both plants
    python list_documents.py --only-new      # skip months already in data/csd/sf_csd_monthly_coverage.csv
    python list_documents.py --datastore 2025 2026   # old route: per-year eSMR datastore resources

Output shape (what harvest_index.py reads):
    {"2026": [{"smr_document_id": "3063421", "facility_place_id": "256499",
               "report_name": "Monthly SMR ( MONNPDES ) report for January 2026",
               "received": "02/27/2026"}, ...]}
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import requests

CIWQS = "https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/PublicReportEsmrAtGlanceServlet"
PARTY = "San Francisco Public Utilities Commission"
# CIWQS facility place ids (what harvest_index.py keys on) ← facility names in the listing
FACILITIES = {"256498": "Oceanside (CA0037681)", "256499": "Southeast/Bayside (CA0037664)"}
COVERAGE = Path(__file__).resolve().parents[3] / "data" / "csd" / "sf_csd_monthly_coverage.csv"
MONTHS = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                            "August", "September", "October", "November", "December"])}
UA = {"User-Agent": "Mozilla/5.0 (research; CSD records compilation)"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def _place_id(facility: str) -> str | None:
    f = facility.upper()
    if "OCEANSIDE" in f:
        return "256498"
    if "SF-SE" in f or "SOUTHEAST" in f or "BAYSIDE" in f:
        return "256499"
    return None


def ciwqs_documents() -> list[dict]:
    """Every 'Monthly SMR ( MONNPDES )' document for SFPUC's two plants, from
    the eSMR At-A-Glance party search (one session: reset → search → big page)."""
    s = requests.Session()
    s.headers.update(UA)
    s.get(CIWQS, params={"inCommand": "reset", "reportID": "2"}, timeout=90)
    s.get(CIWQS, params={"reportID": "1", "firstRun": "Y", "partyName": PARTY, "facilityName": "", "orderNo": "",
                         "wdid": "", "npdesPermit": "", "ciNo": "", "runReport": "Run Report"}, timeout=180)
    html = s.get(CIWQS, params={"reportID": "1", "newPageNumber": "0", "newPageSize": "5000"}, timeout=300).text
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        ids = re.findall(r"documentID=(\d+)", row)
        if not ids:
            continue
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 8 or not cells[4].startswith("Monthly SMR ( MONNPDES )"):
            continue
        pid = _place_id(cells[1])
        if pid is None:
            continue
        out.append({"smr_document_id": ids[0], "facility_place_id": pid, "report_name": cells[4],
                    "period": cells[5], "due": cells[6], "received": cells[7]})
    if not out:
        raise SystemExit("CIWQS party search returned no SFPUC monthly SMR rows — the servlet's form may have changed; "
                         "see the docstring and NOTES.md 'Method' for the request sequence")
    return out


def datastore_documents(years: list[str]) -> list[dict]:
    """Fallback: distinct smr_document_id per SFPUC facility from the per-year
    eSMR analytical CSV resources on data.ca.gov (lags CIWQS; may miss months)."""
    ckan = "https://data.ca.gov/api/3/action"
    pkg = requests.get(f"{ckan}/package_show",
                       params={"id": "water-quality-effluent-electronic-self-monitoring-report-esmr-data"}, timeout=60).json()["result"]
    res = {}
    for r in pkg["resources"]:
        m = re.match(r"(\d{4}) eSMR Analytical Data", r.get("name") or "")
        if m and r.get("datastore_active"):
            res[m.group(1)] = r["id"]
    out = []
    for y in years:
        if y not in res:
            print(f"{y}: no datastore resource yet")
            continue
        sql = (f'SELECT DISTINCT smr_document_id, report_name, facility_place_id FROM "{res[y]}" '
               f"WHERE facility_place_id IN ({', '.join(repr(k) for k in FACILITIES)}) AND report_name LIKE 'Monthly SMR%' "
               f"ORDER BY facility_place_id, smr_document_id")
        r = requests.get(f"{ckan}/datastore_search_sql", params={"sql": sql}, timeout=120).json()
        if not r.get("success"):
            raise SystemExit(f"datastore_search_sql failed: {str(r)[:300]}")
        out += [{"smr_document_id": str(x["smr_document_id"]), "facility_place_id": str(x["facility_place_id"]),
                 "report_name": x["report_name"]} for x in r["result"]["records"]]
    return out


def month_key(d: dict) -> tuple[str, int, int] | None:
    m = re.search(r"for (\w+) (\d{4})", d["report_name"])
    return (FACILITIES[d["facility_place_id"]], int(m.group(2)), MONTHS[m.group(1)]) if m and m.group(1) in MONTHS else None


def covered_months() -> set[tuple[str, int, int]]:
    if not COVERAGE.exists():
        return set()
    with COVERAGE.open() as f:
        return {(row["facility"], int(row["year"]), int(row["month"])) for row in csv.DictReader(f)}


def main(argv: list[str]) -> None:
    only_new = "--only-new" in argv
    years = [a for a in argv if re.fullmatch(r"\d{4}", a)]
    docs = datastore_documents(years) if "--datastore" in argv else ciwqs_documents()
    done = covered_months() if only_new else set()
    out: dict[str, list[dict]] = {}
    for d in sorted(docs, key=lambda d: (month_key(d) or ("", 0, 0))):
        key = month_key(d)
        if key is None or (years and str(key[1]) not in years) or (only_new and key in done):
            continue
        out.setdefault(str(key[1]), []).append(d)
        print(f"  {key[1]}-{key[2]:02d}  {key[0][:9]:9}  doc {d['smr_document_id']}  received {d.get('received', '?')}")
    json.dump(out, open("smr_documents.json", "w"), indent=1)
    print("wrote smr_documents.json:", {y: len(v) for y, v in out.items()} or "nothing new")


if __name__ == "__main__":
    main(sys.argv[1:])
