#!/usr/bin/env python3
"""T0, the prospective window: what the served forecast actually said (STAGES_PROTOCOL.md §2;
STAGES_DESIGN.md P10, Part B 20 and 28).

Every production refresh records the day's first snapshot in Supabase's ``forecast_history`` (migration
012): the start-of-day forecast for today and five days ahead, stamped with the model that made it. Only
the service role can read that table, so grading never reads Supabase. The owner exports it once a season,
read-only (GETs), into a committed snapshot:

    venv/bin/python features/forecast/src/models/grade_prospective.py --export

which writes data/forecast_history/t0_first_snapshots.csv, one row per issue day × lead × zone:

    issue_date, generated_at, model, corrections, target_date, lead, zone, p

for every issue day after the protocol's freeze date (T0 starts the next day). ``model`` is the set that
served, named by the lineup its stamp records (``stamp_set``: shared/lineup.py's id of its overflow model,
stage 2, weather model and correction rule) and ``corrections`` its live-correction rule, both from the
snapshot's model stamp. Commit the
file with the data refresh that covers its target days (the CIWQS ledger arrives quarterly), then rebuild
the stage scores. ``rows()`` reads and checks the committed file; stages_build.t0_as_served grades the
served set's rows with OUT's own scorer (scores["t0_as_served"]; features/forecast/SWAPS.md, "Grading the
live season").
"""
from __future__ import annotations

import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import exclusions as X  # noqa: E402
from shared import lineup as LU  # noqa: E402

SNAPSHOT = HERE.parents[1] / "data" / "forecast_history" / "t0_first_snapshots.csv"
COLUMNS = ("issue_date", "generated_at", "model", "corrections", "target_date", "lead", "zone", "p")
LEADS = range(0, 6)        # today and five days ahead, as the page shows them
PAGE = 31                  # snapshots per GET: each holds a whole payload


def t0_start() -> date:
    return (X.freeze_date() + pd.Timedelta(days=1)).date()


def stamp_set(stamp: dict) -> str | None:
    """The set a snapshot's model stamp names: the id of the lineup it records (its overflow model and stage 2 id,
    and the weather model and correction rule it ran with), so a row made before a rename or a switch names the
    lineup that made it; a stamp missing one of those, its set's name today (lineup.current_name). An unknown
    component raises (add it to shared/lineup.py)."""
    if all(stamp.get(k) for k in ("stage1", "stage2", "weather_model", "live_corrections")):
        return LU.set_id(LU.geo_v1_lineup(stamp["stage1"], stamp["stage2"], stamp["weather_model"], stamp["live_corrections"]))
    return LU.current_name(stamp["name"]) if stamp.get("name") else None


def snapshot_rows(issue_date: str, snapshot: dict, generated_at: str) -> list[dict]:
    """One first snapshot → its rows: each forecast day (lead 0–5) × zone. Past days a cached snapshot may
    still carry are skipped; a day whose date disagrees with its lead is a malformed snapshot and raises."""
    stamp = snapshot.get("model") or {}
    issue = date.fromisoformat(str(issue_date)[:10])
    out = []
    for day in (snapshot.get("predictions") or {}).values():
        if not isinstance(day, dict) or day.get("day_offset") is None:
            continue
        lead = int(day["day_offset"])
        if lead not in LEADS:
            continue
        target = str(day.get("date"))[:10]
        if target != (issue + timedelta(days=lead)).isoformat():
            raise ValueError(f"forecast_history {issue}: the lead-{lead} day is dated {target}")
        for zone, p in sorted((day.get("zones") or {}).items()):
            out.append({"issue_date": issue.isoformat(), "generated_at": generated_at, "model": stamp_set(stamp),
                        "corrections": stamp.get("live_corrections"), "target_date": target, "lead": lead,
                        "zone": zone, "p": float(p)})
    return out


def export(path: Path = SNAPSHOT) -> Path:
    """Read every first snapshot issued in T0 from forecast_history (GET only) and write the committed file."""
    from shared import supabase as sb
    if not sb.is_configured():
        raise SystemExit("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_KEY): run this from the main checkout")
    rows, offset = [], 0
    while True:
        batch = sb.select("forecast_history", {"select": "forecast_date,first_generated_at,first_snapshot",
                                               "forecast_date": f"gte.{t0_start().isoformat()}",
                                               "order": "forecast_date.asc", "limit": str(PAGE), "offset": str(offset)})
        for r in batch:
            rows += snapshot_rows(r["forecast_date"], r["first_snapshot"] or {}, r["first_generated_at"])
        if len(batch) < PAGE:
            break
        offset += PAGE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["issue_date"], r["lead"], r["zone"])))
    issued = sorted({r["issue_date"] for r in rows})
    print(f"wrote {path} ({len(rows)} rows, {len(issued)} issue days"
          + (f", {issued[0]} → {issued[-1]})" if issued else ")"))
    return path


def rows(path: Path = SNAPSHOT) -> pd.DataFrame | None:
    """The committed snapshot, checked: T0 issue days only, leads 0–5, target = issue + lead, p a probability (read
    exactly as written), one row per issue day × lead × zone. None when nothing has been exported yet.
    stages_build.t0_as_served grades it."""
    if not Path(path).exists():
        return None
    df = pd.read_csv(path, dtype={"model": str, "corrections": str, "zone": str}, float_precision="round_trip")
    if tuple(df.columns) != COLUMNS:
        raise ValueError(f"{Path(path).name}: columns {tuple(df.columns)}, expected {COLUMNS}")
    for c in ("issue_date", "target_date"):
        df[c] = pd.to_datetime(df[c])
    bad = [
        ("an issue day on or before the freeze", df["issue_date"].dt.date < t0_start()),
        ("a lead outside 0–5", ~df["lead"].isin(list(LEADS))),
        ("a target that is not issue + lead", df["target_date"] != df["issue_date"] + pd.to_timedelta(df["lead"], unit="D")),
        ("a p outside [0, 1]", ~df["p"].between(0, 1)),
        ("a repeated issue day × lead × zone", df.duplicated(["issue_date", "lead", "zone"])),
    ]
    for words, mask in bad:
        if mask.any():
            raise ValueError(f"{Path(path).name}: {int(mask.sum())} rows with {words}")
    return df


if __name__ == "__main__":
    if "--export" in sys.argv[1:]:
        export()
    else:
        raise SystemExit(__doc__)
