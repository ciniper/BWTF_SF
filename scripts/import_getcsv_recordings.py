"""Convert the discharge recorder's getCSV snapshots to append-only observations.

Dry run: python3 scripts/import_getcsv_recordings.py --output /tmp/getcsv-rows.json
Write with configured service credentials: python3 -m scripts.import_getcsv_recordings --apply
Re-running the same recordings is safe: the original read_at is part of the key.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

INDICATORS = ("COLI_TOTAL", "COLI_FECAL", "ENTERO", "COLI_E")
KEY = "source_code,sample_date,analyte,observed_at"


def observations(paths):
    snapshots = []
    for path in paths:
        data = json.loads(Path(path).read_text())
        at = datetime.fromisoformat(data["read_at"])
        if at.tzinfo is None:
            raise ValueError(f"Observation timestamp needs a timezone: {path}")
        snapshots.append((at, data["stations"]))
    previous = {}
    result = {}
    for index, (at, stations) in enumerate(sorted(snapshots, key=lambda x: x[0])):
        current = {}
        for source, rows in stations.items():
            for row in rows:
                day = datetime.strptime(row["sample_date"], "%m/%d/%y").date().isoformat()
                for analyte in INDICATORS:
                    raw = row.get(analyte)
                    current[source, day, analyte] = str(raw).strip() if raw is not None and str(raw).strip() else None
        newest_before = {}
        for source, day, _ in previous:
            newest_before[source] = max(day, newest_before.get(source, day))
        for key, value in current.items():
            old = previous.get(key)
            if value == old:
                continue
            source, day, analyte = key
            if value is None:
                kind = "removed"
            elif index == 0 or (key not in previous and day < newest_before.get(source, day)):
                kind = "baseline"
            else:
                kind = "appeared" if old is None else "changed"
            stamp = at.astimezone(timezone.utc).isoformat()
            event = dict(source_code=source, sample_date=day, analyte=analyte,
                         observed_at=stamp, value_raw=value, observation_kind=kind,
                         recording_source="recorder_import")
            identity = (*key, stamp)
            if identity in result and result[identity] != event:
                raise ValueError(f"Conflicting observations at {identity}")
            result[identity] = event
        # A sample dropping out of the recorder's two-date window is not a removal.
        previous = current
    return list(result.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("docs/discharge-observations/feed"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = list(args.directory.glob("*-getCSV.json"))
    if not paths:
        parser.error("No getCSV recordings found")
    rows = observations(paths)
    if args.output:
        args.output.write_text(json.dumps(rows, indent=2) + "\n")
    if args.apply:
        from shared import supabase as sb
        if not sb.is_configured():
            parser.error("Supabase service credentials are not configured")
        for start in range(0, len(rows), 250):
            sb.upsert("sfpuc_getcsv_observations", rows[start:start + 250],
                      on_conflict=KEY, resolution="ignore-duplicates")
    print(f"{len(paths)} snapshots -> {len(rows)} observations" + (" (submitted idempotently)" if args.apply else " (dry run)"))


if __name__ == "__main__":
    main()
