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


# (sender, shadow) pairings per era: era 1 = thread sends / pg shadows;
# era 2 (role reversal) = pg sends / thread shadows.
ERAS = [("watcher", "pg_shadow"), ("pg_live", "thread_shadow")]


def _compare_pair(sender_rows: list[dict], shadow_rows: list[dict], label: str) -> tuple[int, int]:
    unmatched = list(shadow_rows)
    matched = 0
    problems = 0
    for t in sender_rows:
        candidates = [s for s in unmatched
                      if abs(_ts(s) - _ts(t)) <= WINDOW
                      and set(s["station_ids"]) & set(t["station_ids"])]
        if not candidates:
            print(f"❌ {label}: sender-only {t['created_at'][:19]} {t['event_type']} {t['station_ids']}")
            problems += 1
            continue
        s = candidates[0]
        unmatched.remove(s)
        matched += 1
        agree = (set(s["station_ids"]) == set(t["station_ids"])
                 and s["event_type"] == t["event_type"]
                 and s["recipient_count"] == t["recipient_count"])
        mark = "✅" if agree else "⚠️ "
        print(f"{mark} {label}: matched {t['created_at'][:19]} {t['event_type']} {t['station_ids']} "
              f"recipients sender={t['recipient_count']} shadow={s['recipient_count']}")
        if not agree:
            problems += 1
    for s in unmatched:
        print(f"❌ {label}: shadow-only {s['created_at'][:19]} {s['event_type']} {s['station_ids']}")
        problems += 1
    return matched, problems


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = sb.select("alert_log", {
        "select": "id,created_at,source,event_type,station_ids,recipient_count,simulated,channel",
        "created_at": f"gte.{since}",
        "source": "in.(watcher,pg_shadow,pg_live,thread_shadow)",
        "order": "created_at.asc",
    })
    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)
    print(f"last {days}d: " + " · ".join(f"{k}={len(v)}" for k, v in sorted(by_source.items())) + "\n")

    matched = problems = 0
    for sender, shadow in ERAS:
        s_rows, sh_rows = by_source.get(sender, []), by_source.get(shadow, [])
        if s_rows or sh_rows:
            m, p = _compare_pair(s_rows, sh_rows, f"{sender}↔{shadow}")
            matched += m
            problems += p

    config_missing = [r for r in rows if r.get("channel") == "config_missing"]
    for r in config_missing:
        print(f"⚠️  pg_live had NO Brevo config at {r['created_at'][:19]} — event logged, nothing sent")
    problems += len(config_missing)

    print(f"\nmatched={matched} problems={problems}")
    print("VERDICT: clean" if problems == 0 else "VERDICT: investigate the mismatches")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
