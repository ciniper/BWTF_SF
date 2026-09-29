"""SFPUC's own lab record for the years before DataSF — one place, one shape.

DataSF's Beach Water Quality Monitoring dataset (shared/datasf.py) starts
2020-07-27. SFPUC's lab database (STARDB) holds the same results back to
2000-01-03; Chase obtained an export of it in September 2026 (a 374-page PDF)
and ``features/forecast/src/collectors/sfpuc_stardb_pdf.py`` parsed it to
``features/forecast/data/sfpuc_stardb_2000_2020/…_normalized.csv`` — 60,574
results. The 45 results both records hold for 2020-07-27 are identical, so
the two join without a seam (``NOTES.md`` next to the CSV has the details).

Every page that shows city results reads DataSF live. For a window that
starts before DataSF's floor, :func:`records` supplies the earlier rows in the
same Socrata shape (``source, sample_date, analyte, data``), so the pages'
code paths do not change: ``features/comparison/comparison.fetch_city_records``
and ``features/site_analysis/page._fetch_rows`` splice them in ahead of the
API rows. History rows carry ``"history": True`` so a page can label them.

What a page should know about the older years: from July 2002 to July 2020
the city ran **E. coli** (``COLI_E``) where it now runs fecal coliform;
Enterococcus and E. coli begin 2002-07-01 (only total coliform before that);
the first years are membrane filtration (CFU/100 mL) rather than Quantitray
(MPN/100 mL) — both are counts per 100 mL and graded by the same limits.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from shared.datasf import DATASET_FLOOR

REPO = Path(__file__).resolve().parents[1]
HISTORY_CSV = REPO / "features" / "forecast" / "data" / "sfpuc_stardb_2000_2020" / \
    "sfpuc_beach_bacteria_2000-01_2020-07_normalized.csv"
HISTORY_FLOOR = "2000-01-03"   # first sample day in the export
HISTORY_END = "2020-07-26"     # the export also holds 2020-07-27; DataSF owns that day on
ENTERO_FROM = "2002-07-01"     # Enterococcus and E. coli start here; only total coliform before
SOURCE_LABEL = "SFPUC lab export (STARDB), Jan 2000 – Jul 2020"


def available() -> bool:
    return HISTORY_CSV.exists()


def city_record_floor() -> str:
    """First day a city result exists for: the export's when it is on disk,
    DataSF's otherwise. Pages use this as the range picker's minimum."""
    return HISTORY_FLOOR if available() else DATASET_FLOOR


def _iso(d) -> str:
    if isinstance(d, (datetime, date)):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


@lru_cache(maxsize=1)
def _rows() -> tuple:
    """(sample_date, station, analyte, raw value) for every result, date-sorted."""
    if not available():
        return ()
    out = []
    with HISTORY_CSV.open(newline="") as f:
        for r in csv.DictReader(f):
            raw = (r.get("value_raw") or "").strip()
            if not raw or not r.get("sample_date") or not r.get("station") or not r.get("analyte"):
                continue
            out.append((r["sample_date"][:10], r["station"], r["analyte"], raw))
    out.sort()
    return tuple(out)


def covers(start) -> bool:
    """Does a window starting at ``start`` reach into the years only the export holds?"""
    return available() and _iso(start) < DATASET_FLOOR


def records(sources: Optional[Iterable[str]] = None, start=None, end=None,
            analytes: Optional[Iterable[str]] = None) -> list[dict]:
    """City results from the export for [start, end] (dates or YYYY-MM-DD strings),
    optionally only these stations / analyte codes, as Socrata-shaped dicts:
    ``{"source", "sample_date" ("YYYY-MM-DDT00:00:00.000"), "analyte", "data", "history": True}``.
    Never returns 2020-07-27 or later — those days belong to DataSF."""
    if not available():
        return []
    s = _iso(start) if start else HISTORY_FLOOR
    e = min(_iso(end), HISTORY_END) if end else HISTORY_END
    if s > e:
        return []
    src = set(sources) if sources else None
    codes = set(analytes) if analytes else None
    out = []
    for d, station, analyte, raw in _rows():
        if d < s:
            continue
        if d > e:
            break
        if (src is not None and station not in src) or (codes is not None and analyte not in codes):
            continue
        out.append({"source": station, "sample_date": f"{d}T00:00:00.000", "analyte": analyte, "data": raw, "history": True})
    return out


def provenance() -> dict:
    """What a page prints about the older years."""
    return {"label": SOURCE_LABEL, "floor": HISTORY_FLOOR, "end": HISTORY_END, "datasf_from": DATASET_FLOOR,
            "entero_from": ENTERO_FROM, "rows": len(_rows()), "available": available()}
