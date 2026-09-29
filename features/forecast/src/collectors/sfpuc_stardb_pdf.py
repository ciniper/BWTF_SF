"""Parse an SFPUC STARDB beach-bacteria export PDF into CSVs.

    python sfpuc_stardb_pdf.py "SFPUC Beach Water Quality_1Jan2000_27Jul2020.pdf" [--out DIR]

SFPUC's lab database (STARDB) exports one text row per sample result:

    Station Number | Station Name | Sample_Date | Sample_Time | Analysis_Name | Analyte | Qualifier | Value | Final | Units

The first parse (2026-09-29) covered 1 Jan 2000 – 27 Jul 2020: 374 pages,
60,574 results, the day before DataSF's record begins (2020-07-27; the 45
results both hold are identical). Writes, in --out (default: next to the PDF):

- ``<stem>_rows.csv``        every result as printed (plus pdf_page)
- ``<stem>_normalized.csv``  the training-file schema used by
  ``data/raw/historical_bacteria.csv``: sample_date, station, analyte code
  (COLI_TOTAL / ENTERO / COLI_E / COLI_FECAL), value (``<10`` → 5, ``>x`` → x
  via shared.standards.parse_result), value_raw, exceeds_standard + threshold
  (shared.standards.flag_exceedances, incl. the total-coliform ratio rule),
  basin, zone, units, method, sample_time, in_registry
- ``<stem>_summary.json``    counts per year / analyte / method / station,
  registry overlap, unparsed lines
- ``<stem>_unparsed.txt``    lines that matched no row (page footers,
  the page-1 metadata block, and results printed without a value)

Needs ``pdfplumber`` (dev-only, like the CIWQS scraper). Text-based PDFs only.
"""
from __future__ import annotations

import collections
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pdfplumber

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from shared.standards import flag_exceedances, parse_result  # noqa: E402
from shared.stations import STATION_BASINS  # noqa: E402
from shared.zones import ZONE_OF_SOURCE  # noqa: E402

ANALYTES = ["Total Coliform", "Fecal Coliform", "Escherichia coli", "E. coli", "Enterococcus", "Enterococci",
            "Fecal Streptococcus"]
CODE = {"Total Coliform": "COLI_TOTAL", "Fecal Coliform": "COLI_FECAL", "Escherichia coli": "COLI_E", "E. coli": "COLI_E",
        "Enterococcus": "ENTERO", "Enterococci": "ENTERO", "Fecal Streptococcus": "FECAL_STREP"}
ROW = re.compile(r"^(?P<station>\S+)\s+(?P<name>.*?)\s*(?P<date>\d{1,2}-[A-Z][a-z]{2}-\d{2})\s+(?P<time>\d{1,2}:\d{2})\s+"
                 r"(?P<mid>.+?)\s+(?P<qual>[<>]=?)?\s*(?P<value>[\d.,]+|ND|NA|N/A)\s+(?P<final>\S+)\s+"
                 r"(?P<units>(?:CFU|MPN|cfu|mpn)/100\s?mL)(?P<trail>.*)$")
SKIP = ("Station Number", "Metadata", "Column definitions")


def parse(pdf_path: Path, out_dir: Path) -> dict:
    rows, bad, per_page = [], [], []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages):
            n0 = len(rows)
            for line in (page.extract_text() or "").split("\n"):
                s = line.strip()
                if not s or s.startswith(SKIP):
                    continue
                m = ROW.match(s)
                if not m:
                    bad.append((i + 1, s))
                    continue
                mid = m.group("mid").strip()
                analysis, analyte = mid, None
                for a in ANALYTES:
                    if mid.endswith(a):
                        analysis, analyte = mid[: -len(a)].strip(), a
                        break
                try:
                    d = datetime.strptime(m.group("date"), "%d-%b-%y").date().isoformat()
                except ValueError:
                    bad.append((i + 1, "DATE " + s))
                    continue
                rows.append({"station_id": m.group("station"), "station_name": m.group("name").strip(), "sample_date": d,
                             "sample_time": m.group("time"), "analysis_name": analysis, "analyte": analyte or mid,
                             "qualifier": m.group("qual") or "", "value": m.group("value").replace(",", ""),
                             "value_final": m.group("final"), "units": m.group("units").replace(" ", ""),
                             "trailing_text": m.group("trail").strip(), "pdf_page": i + 1})
            per_page.append(len(rows) - n0)
            if (i + 1) % 50 == 0:
                print(f"{i + 1} pages, {len(rows)} rows", flush=True)
    if not rows:
        raise SystemExit("no rows parsed — is this a text-based STARDB export?")

    stem = out_dir / re.sub(r"[^A-Za-z0-9._-]+", "_", pdf_path.stem)
    with open(f"{stem}_rows.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(f"{stem}_unparsed.txt", "w") as f:
        for p, s in bad:
            f.write(f"p{p}\t{s}\n")

    norm = []
    for r in rows:
        code = CODE.get(r["analyte"], r["analyte"])
        norm.append({"sample_date": r["sample_date"], "station": r["station_id"], "analyte": code,
                     "value": parse_result(r["value_final"]), "value_raw": r["value_final"],
                     "basin": STATION_BASINS.get(r["station_id"], "Unknown"), "zone": ZONE_OF_SOURCE.get(r["station_id"], ""),
                     "units": r["units"], "method": r["analysis_name"], "sample_time": r["sample_time"],
                     "in_registry": int(r["station_id"] in STATION_BASINS)})
    flag_exceedances(norm, station_key="station", date_key="sample_date")
    cols = ["sample_date", "station", "analyte", "value", "value_raw", "exceeds", "threshold", "basin", "zone", "units",
            "method", "sample_time", "in_registry"]
    with open(f"{stem}_normalized.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[("exceeds_standard" if c == "exceeds" else c) for c in cols])
        w.writeheader()
        for r in norm:
            w.writerow({("exceeds_standard" if c == "exceeds" else c): r.get(c) for c in cols})

    years = collections.Counter(r["sample_date"][:4] for r in rows)
    summary = {
        "source_pdf": pdf_path.name, "pages": len(per_page), "rows": len(rows), "unparsed_lines": len(bad),
        "rows_per_page_min_max": [min(per_page), max(per_page)],
        "date_min": min(r["sample_date"] for r in rows), "date_max": max(r["sample_date"] for r in rows),
        "rows_per_year": dict(sorted(years.items())),
        "exceedances": sum(1 for r in norm if r["exceeds"]),
        "analytes": dict(collections.Counter(r["analyte"] for r in rows).most_common()),
        "analysis_names": dict(collections.Counter(r["analysis_name"] for r in rows).most_common()),
        "units": dict(collections.Counter(r["units"] for r in rows)),
        "qualifiers": dict(collections.Counter(r["qualifier"] for r in rows)),
        "stations": dict(sorted(collections.Counter(r["station_id"] for r in rows).items())),
        "stations_in_registry": sorted({r["station_id"] for r in rows} & set(STATION_BASINS)),
        "stations_not_in_registry": sorted({r["station_id"] for r in rows} - set(STATION_BASINS)),
        "distinct_sample_days": len({r["sample_date"] for r in rows}),
        "distinct_station_days": len({(r["station_id"], r["sample_date"]) for r in rows}),
    }
    json.dump(summary, open(f"{stem}_summary.json", "w"), indent=1)
    print(json.dumps({k: summary[k] for k in ("rows", "unparsed_lines", "date_min", "date_max", "exceedances")}))
    return summary


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit(__doc__)
    pdf_path = Path(argv[0]).expanduser()
    out_dir = Path(argv[argv.index("--out") + 1]).expanduser() if "--out" in argv else pdf_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    parse(pdf_path, out_dir)


if __name__ == "__main__":
    main(sys.argv[1:])
