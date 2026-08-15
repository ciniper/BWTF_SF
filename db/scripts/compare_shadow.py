#!/usr/bin/env python3
"""Parallel-run comparator: thread (source=watcher) vs pg (source=pg_shadow).

Matches alert_log rows from the two paths within a ±3-minute window on
overlapping station sets and reports agreement/misses. Run any time during the
parallel week; the flip decision wants several days of MATCHED (or all-quiet)
output with zero one-sided rows.

    venv/bin/python db/scripts/compare_shadow.py [days]
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared import supabase as sb

WINDOW = timedelta(minutes=3)


def _ts(row: dict) -> datetime:
    return datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = sb.select("alert_log", {
        "select": "id,created_at,source,event_type,station_ids,recipient_count,simulated",
        "created_at": f"gte.{since}",
        "source": "in.(watcher,pg_shadow)",
        "order": "created_at.asc",
    })
    thread = [r for r in rows if r["source"] == "watcher"]
    shadow = [r for r in rows if r["source"] == "pg_shadow"]
    print(f"last {days}d: watcher rows={len(thread)}  pg_shadow rows={len(shadow)}\n")

    unmatched_shadow = list(shadow)
    matched = 0
    problems = 0
    for t in thread:
        candidates = [s for s in unmatched_shadow
                      if abs(_ts(s) - _ts(t)) <= WINDOW
                      and set(s["station_ids"]) & set(t["station_ids"])]
        if not candidates:
            print(f"❌ watcher-only: {t['created_at'][:19]} {t['event_type']} {t['station_ids']}")
            problems += 1
            continue
        s = candidates[0]
        unmatched_shadow.remove(s)
        matched += 1
        agree = (set(s["station_ids"]) == set(t["station_ids"])
                 and s["event_type"] == t["event_type"]
                 and s["recipient_count"] == t["recipient_count"])
        mark = "✅" if agree else "⚠️ "
        print(f"{mark} matched: {t['created_at'][:19]} {t['event_type']} {t['station_ids']} "
              f"recipients thread={t['recipient_count']} pg={s['recipient_count']}")
        if not agree:
            problems += 1
    for s in unmatched_shadow:
        print(f"❌ pg_shadow-only: {s['created_at'][:19]} {s['event_type']} {s['station_ids']}")
        problems += 1

    print(f"\nmatched={matched} problems={problems}")
    print("VERDICT: clean — ready to consider the flip" if problems == 0
          else "VERDICT: investigate the mismatches before flipping")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
