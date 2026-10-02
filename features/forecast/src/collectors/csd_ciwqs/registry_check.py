"""Stop a CIWQS refresh on an outfall the registry does not know (STAGES_DESIGN.md §8, P2).

Every reader of data/csd/sf_csd_events.csv places an event through
shared/outfalls.py: csd_labels maps the row's report basin to an app basin,
shared/geography.py maps its outfall to a basin, link and zone, and the
scorecard looks up the stations it posts. An id the registry lacks is skipped
silently by some of them (train_v4.posted_stations_by_day keeps registry ids
only) and raises in others, so aggregate.py calls ``assert_registered`` before
it writes anything: add the structure to shared/outfalls.py first (stations,
evidence, report basin), then re-run. A row whose report basin disagrees with
the registry's is refused too: the served labels read the row's basin, the
geography reads the registry's, and the two would part without a word.

Run from a scratch directory like the rest of this pipeline (README.md); this
module finds the repo from its own path.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from shared.outfalls import OUTFALLS  # noqa: E402


def problems(rows) -> list[str]:
    """One line per offending row: an outfall id not in shared/outfalls.py, or a report basin that differs from it."""
    out = []
    for r in rows:
        oid, basin = r["outfall_id"], r.get("basin")
        o = OUTFALLS.get(oid)
        if o is None:
            out.append(f"{r.get('event_date', '?')} {oid}: not in shared/outfalls.py")
        elif basin is not None and basin != o.report_basin:
            out.append(f"{r.get('event_date', '?')} {oid}: report basin {basin!r}, the registry says {o.report_basin!r}")
    return out


def assert_registered(rows) -> None:
    """Raise ValueError naming every row ``problems`` finds; return quietly when there are none."""
    bad = problems(rows)
    if bad:
        raise ValueError(f"{len(bad)} CIWQS row(s) the outfall registry does not cover; add the outfall to "
                         "shared/outfalls.py (or fix the parse) and re-run aggregate.py:\n  " + "\n  ".join(bad[:20]))
