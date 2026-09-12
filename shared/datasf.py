"""DataSF (SF Open Data) — the beach water-quality lab dataset, in one place.

SFPUC's lab results for the 20 monitoring stations are published as Socrata
dataset v3fv-x3ux. In 2026 the portal moved from data.sfgov.org to
data.sf.gov; the legacy host now redirects plain queries but answers any
query carrying ``$select`` with a bare 403 (hit 2026-09-09: blank comparison
columns, a Forbidden "View results" link). Every consumer imports these
constants so the next move is one edit — `tests/test_single_source.py`
scans the tree for stray copies.
"""
DATASET_ID = "v3fv-x3ux"
BEACH_SAMPLES_URL = f"https://data.sf.gov/resource/{DATASET_ID}.json"
DATASET_PAGE_URL = f"https://data.sf.gov/Energy-and-Environment/Beach-Water-Quality-Monitoring/{DATASET_ID}"
