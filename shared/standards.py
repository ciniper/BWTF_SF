"""California recreational-water bacteria standards (AB 411 / Ocean Plan) — in
one place, with the one helper that decides "over standard".

Five copies of these numbers used to live around the repo and only the alerts
page applied the total-coliform ratio rule, so the same sample could be an
exceedance on one page and clean on another (found 2026-09-12). Everything
now imports from here; tests/test_single_source.py scans for stray literals.

Units: MPN/100 mL. Lab results reported as "<10" count as half the detection
limit (5); ">24196" counts as the limit.
"""
from __future__ import annotations

from typing import Iterable, Optional

STANDARDS = {
    "ENTERO": {
        "single_sample_max": 104,   # Enterococcus single sample max
        "geometric_mean": 35,       # 30-day geometric mean
        "description": "Enterococcus",
    },
    "COLI_E": {
        "single_sample_max": 235,   # E. coli single sample max
        "geometric_mean": 126,
        "description": "E. coli",
    },
    "COLI_FECAL": {
        "single_sample_max": 400,   # Fecal coliform single sample max
        "geometric_mean": 200,
        "description": "Fecal Coliform",
    },
    "COLI_TOTAL": {
        "single_sample_max": 10000,        # Total coliform single sample max (default)
        "single_sample_max_ratio": 1000,   # ...but 1,000 when fecal/total > ratio_threshold
        "ratio_threshold": 0.1,
        "geometric_mean": 1000,
        "description": "Total Coliform",
    },
}

# BWTF's own grading (and the site report card) flags Enterococcus at 36 as a
# caution tier below the 104 single-sample maximum.
ENTERO_CAUTION = 36

# Geometric-mean standards need at least 5 weekly samples over 30 days.
GEOMETRIC_MEAN_WINDOW_DAYS = 30
GEOMETRIC_MEAN_MIN_SAMPLES = 5


def parse_result(raw) -> Optional[float]:
    """Lab result text → number. '<10' → 5.0 (half the detection limit),
    '>24196' → 24196.0, '41' → 41.0, anything else → None."""
    s = str(raw if raw is not None else "").strip()
    if not s:
        return None
    try:
        if s.startswith("<"):
            return float(s[1:]) / 2
        if s.startswith(">"):
            return float(s[1:])
        return float(s)
    except ValueError:
        return None


def single_sample_max(analyte: str, fecal: Optional[float] = None, total: Optional[float] = None) -> Optional[float]:
    """The single-sample limit that applies to one result. For total coliform
    the limit drops to 1,000 when the same sample's fecal share exceeds 10%."""
    std = STANDARDS.get(analyte)
    if not std:
        return None
    limit = std["single_sample_max"]
    if analyte == "COLI_TOTAL" and fecal is not None and total and total > 0 \
            and fecal / total > std["ratio_threshold"]:
        limit = std["single_sample_max_ratio"]
    return limit


def exceeds(analyte: str, value: Optional[float], fecal: Optional[float] = None,
            total: Optional[float] = None) -> bool:
    limit = single_sample_max(analyte, fecal, total)
    return value is not None and limit is not None and value > limit


def flag_exceedances(samples: Iterable[dict], station_key: str = "station", date_key: str = "date",
                     analyte_key: str = "analyte", value_key: str = "value") -> list:
    """Set ``exceeds`` (and ``threshold``) on each sample dict, applying the
    total-coliform ratio rule using the SAME station + date's fecal result.
    Returns the list (mutated in place)."""
    samples = list(samples)
    by_key: dict = {}
    for s in samples:
        by_key.setdefault((s.get(station_key), s.get(date_key)), {})[s.get(analyte_key)] = s.get(value_key)
    for s in samples:
        peers = by_key.get((s.get(station_key), s.get(date_key)), {})
        fecal, total = peers.get("COLI_FECAL"), peers.get("COLI_TOTAL")
        s["threshold"] = single_sample_max(s.get(analyte_key), fecal, total)
        s["exceeds"] = exceeds(s.get(analyte_key), s.get(value_key), fecal, total)
    return samples
