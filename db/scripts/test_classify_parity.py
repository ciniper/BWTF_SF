#!/usr/bin/env python3
"""Parity test: the watcher's Postgres classifier (bwtf_classify, migration
002) must agree with the Python one every page renders from
(shared.sfpuc_api → features.alerts.watcher.classify) for every shape an
SFPUC feed row can take.

Why this exists: alerts are DECIDED in Postgres, pages are DRAWN from Python.
If the two drift, a beach can read Safe on every page while subscribers get
an email about it — or the reverse. They were proven equal once (the
shadow-run diff when the watcher moved to Postgres, Aug 2026); this keeps
proving it. Run after touching either copy.

    venv/bin/python db/scripts/test_classify_parity.py
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.alerts.watcher import classify  # noqa: E402
from shared import supabase as sb  # noqa: E402
from shared.sfpuc_api import SFPUCRealTimeAPI  # noqa: E402

# Every value each field has been seen to take (plus a lower-case colour and
# an empty string, which the feed does emit for `cso`).
CSO = [None, "", "Islais Creek CSD"]
S_COLOR = [None, "G", "R", "Y", "W", "g"]
POSTED = [None, "BAY#320_SL"]
P_COLOR = [None, "G", "R", "Y"]


def python_classification(api: SFPUCRealTimeAPI, row: dict) -> str:
    """Exactly what the pages do with a feed row, collapsed the way the old
    Python watcher did (features/alerts/watcher.classify)."""
    station = api._parse_station(row) if hasattr(api, "_parse_station") else None
    if station is None:
        class _S:
            has_cso = api._detect_cso(row)
            status = api._parse_station_status(row)
        station = _S()
    return classify(station)


def main() -> int:
    if not sb.is_configured():
        print("Supabase env missing — aborting."); return 1
    api = SFPUCRealTimeAPI()
    mismatches, n = [], 0
    for cso, s_color, posted, p_color in itertools.product(CSO, S_COLOR, POSTED, P_COLOR):
        row = {"stationid": "4619", "stationname": "Islais Creek", "cso": cso, "s_color": s_color,
               "posted": posted, "p_color": p_color, "sample_date": "08/31/26",
               "lat": "37.747", "lon": "-122.388"}
        pg = sb.rpc("bwtf_classify", {"p_cso": cso, "p_s_color": s_color,
                                      "p_posted": posted, "p_p_color": p_color})
        py = python_classification(api, row)
        n += 1
        if pg != py:
            mismatches.append((row, pg, py))
    for row, pg, py in mismatches:
        print(f"FAIL cso={row['cso']!r} s_color={row['s_color']!r} posted={row['posted']!r} "
              f"p_color={row['p_color']!r}: postgres={pg!r} python={py!r}")
    print(f"{n} feed-row shapes · {len(mismatches)} disagreements · "
          f"{'PARITY OK' if not mismatches else 'CLASSIFIERS HAVE DRIFTED'}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
