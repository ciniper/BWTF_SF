"""T0 grader input (grade_prospective.py, STAGES_DESIGN.md P10 / Part B 28): a forecast_history first snapshot
becomes one row per lead × zone, the committed file is checked on read, and the export never runs unconfigured.
No test reads Supabase: scripts/check.sh blanks the keys and everything here uses synthetic snapshots.
Run: venv/bin/python tests/test_grade_prospective.py
"""
from __future__ import annotations

import csv
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "features" / "forecast" / "src" / "models"
for p in (str(ROOT), str(MODELS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import grade_prospective as GP  # noqa: E402

ZONES = ("baker_china", "east", "north", "ocean")


def _snapshot(issue: date, past_day: bool = True) -> dict:
    preds = {}
    for k in range(-1 if past_day else 0, 6):
        d = (issue + timedelta(days=k)).isoformat()
        preds[d] = {"date": d, "day_offset": k, "is_today": k == 0, "zones": {z: round(0.1 * (i + 1) + 0.01 * k, 3) for i, z in enumerate(ZONES)}}
    return {"predictions": preds, "model": {"name": "logit_v1_s2v2", "live_corrections": "live_v2"}, "last_refresh": "x"}


def _write(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GP.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def raises(fn, words: str) -> None:
    try:
        fn()
    except (ValueError, SystemExit) as e:
        assert words in str(e), f"raised {e!r}, expected to mention {words!r}"
        return
    raise AssertionError(f"did not raise ({words})")


def test_t0_starts_the_day_after_the_freeze():
    assert GP.t0_start() == (GP.X.freeze_date() + GP.pd.Timedelta(days=1)).date() == date(2026, 10, 3)


def test_a_first_snapshot_becomes_lead_by_zone_rows():
    issue = GP.t0_start() + timedelta(days=4)
    rows = GP.snapshot_rows(issue.isoformat(), _snapshot(issue), "2026-10-07T07:01:00+00:00")
    assert len(rows) == 6 * len(ZONES)                       # the cached past day is dropped
    assert {r["lead"] for r in rows} == set(range(6)) and {r["zone"] for r in rows} == set(ZONES)
    for r in rows:
        assert r["target_date"] == (issue + timedelta(days=r["lead"])).isoformat()
        assert r["model"] == "logit_v1_s2v2" and r["corrections"] == "live_v2" and r["issue_date"] == issue.isoformat()
    east_l2 = next(r for r in rows if r["zone"] == "east" and r["lead"] == 2)
    assert abs(east_l2["p"] - 0.22) < 1e-12


def test_a_day_dated_off_its_lead_raises():
    issue = GP.t0_start()
    snap = _snapshot(issue, past_day=False)
    d1 = (issue + timedelta(days=1)).isoformat()
    snap["predictions"][d1]["date"] = (issue + timedelta(days=2)).isoformat()
    raises(lambda: GP.snapshot_rows(issue.isoformat(), snap, "t"), "lead-1 day is dated")


def test_the_committed_file_is_checked_on_read():
    issue = GP.t0_start() + timedelta(days=1)
    good = GP.snapshot_rows(issue.isoformat(), _snapshot(issue), "t")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        assert GP.rows(tmp / "missing.csv") is None
        df = GP.rows(_write(tmp / "ok.csv", good))
        assert len(df) == len(good) and df["p"].between(0, 1).all()
        early = [dict(r, issue_date=(GP.t0_start() - timedelta(days=1)).isoformat(),
                      target_date=(GP.t0_start() - timedelta(days=1) + timedelta(days=r["lead"])).isoformat()) for r in good]
        raises(lambda: GP.rows(_write(tmp / "early.csv", early)), "on or before the freeze")
        raises(lambda: GP.rows(_write(tmp / "dup.csv", good + good[:1])), "repeated issue day")
        raises(lambda: GP.rows(_write(tmp / "p.csv", [dict(good[0], p=1.5)] + good[1:])), "p outside")
        raises(lambda: GP.rows(_write(tmp / "lead.csv", [dict(good[0], lead=6)] + good[1:])), "lead outside")


def test_the_export_refuses_without_supabase():
    from shared import supabase as sb
    assert not sb.is_configured(), "run through scripts/check.sh, which blanks the Supabase keys"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "t0.csv"
        raises(lambda: GP.export(out), "not configured")
        assert not out.exists()


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
