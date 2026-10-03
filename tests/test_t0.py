"""T0, the prospective window (STAGES_PROTOCOL.md §2, §4.2, §8; STAGES_DESIGN.md Part B 16, 20 and 28).

Pins: stages_s2 plans T0 as T1's fold, not refit, from the day after the freeze (a gb set too); with the freeze moved
back to 2026-06-30, so that 2026-07-01 → the data end fall in T0, every stage labels those days T0 (X-SEL's
post_selected stops at the freeze; S1, S5 and S5's window spans agree; S5's parameters are T1's) and its rows are
the rows the real freeze labels T1 on the same days; OUT's primary window T1 post ∪ T0 is one cell in vs_served, equal to the one-tier cell
on the same rows; t0_words is 'empty' before T0's first day and 'scored' from it; the served set's T0 as served is
OUT's own scorer on the committed forecast_history snapshot (a tiny synthetic snapshot, checked against
verify.scores_bundle), with its counts; with no snapshot, or nothing yet to grade, it says so. No test reads Supabase.

Fast: one small served slice under each freeze, and synthetic frames.

    venv/bin/python tests/test_t0.py
"""
from __future__ import annotations

import contextlib
import csv
import functools
import json
import sys
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
MODELS = FORECAST / "src" / "models"
for p in (ROOT, MODELS, FORECAST / "src" / "collectors"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import exclusions as X  # noqa: E402
import grade_prospective as GP  # noqa: E402
import stages_build as B  # noqa: E402
import stages_s1 as S1  # noqa: E402
import stages_s2 as S2  # noqa: E402
import stages_s5 as S5  # noqa: E402
import truth as T  # noqa: E402
import verify as V  # noqa: E402
from shared import risk_levels as RL  # noqa: E402
from shared.zones import ZONES  # noqa: E402

AS_OF = "2026-08-17"                          # the committed data's end (Part B 23)
FREEZE = pd.Timestamp("2026-06-30")           # moved back so the committed data hold a T0: 2026-07-01 → 2026-08-17
SLICE = dict(entries=("oracle", "rain", "L1"), steps=("s2", "s3", "s4", "out"), n_boot=50, log=lambda *a: None)
N_BOOT = 50


@contextlib.contextmanager
def frozen(day=FREEZE):
    """exclusions.freeze_date moved to ``day`` (every reader calls it through the module)."""
    real = X.freeze_date
    X.freeze_date = lambda: pd.Timestamp(day)
    try:
        yield
    finally:
        X.freeze_date = real


@functools.lru_cache(maxsize=1)
def _builds():
    """(the served slice under the real freeze, T1 only; the same slice under the moved freeze, T1 and T0)."""
    real = B.build("served", tiers=("T1",), **SLICE)
    with frozen():
        moved = B.build("served", tiers=("T1", "T0"), **SLICE)
    return real, moved


@functools.lru_cache(maxsize=1)
def _grading():
    """(the exclusions context, OUT's reference pool, truth.blocks) the served build grades with."""
    ctx = X.context("geo_v1", end=AS_OF)
    return ctx, B._pool("out", ctx, ctx.geo, "rain"), T.blocks(end=AS_OF).set_index("date")


def _snapshot(path: Path, served: str, issue_days, other: int = 2) -> Path:
    """A forecast_history snapshot as grade_prospective.export writes one: every issue day × lead 0–5 × zone, the
    first ``other`` issue days made by another model."""
    rng = np.random.default_rng(7)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GP.COLUMNS)
        w.writeheader()
        for i, d in enumerate(issue_days):
            for lead in GP.LEADS:
                for z in sorted(ZONES):
                    w.writerow({"issue_date": d.isoformat(), "generated_at": f"{d.isoformat()}T14:05:00+00:00",
                                "model": "gb_v1" if i < other else served, "corrections": "live_v2",
                                "target_date": (d + timedelta(days=lead)).isoformat(), "lead": lead, "zone": z,
                                "p": round(float(rng.uniform(0, 0.6)), 4)})
    return path


def _raises(fn, words: str) -> None:
    try:
        fn()
    except ValueError as e:
        assert words in str(e), (words, str(e))
        return
    raise AssertionError(f"no raise ({words})")


# ── the plan ────────────────────────────────────────────────────────────────

def test_the_plan_holds_t0_as_t1s_fold_after_the_freeze():
    fz = X.freeze_date()
    t1, t0 = S2._plan(("T1", "T0"))
    assert t1 == ("T1", "final", S2.POST_START, fz, None, None)
    assert t0 == ("T0", "final", fz + pd.Timedelta(days=1), pd.Timestamp.max, None, None)   # no refit, no last day
    assert [p[0] for p in S2._plan(S2.TIERS)] == ["T1", "T1-holdout"] + ["T2"] * 9           # the spec fitters': no T0
    assert B.TIERS == ("T1", "T1-holdout", "T2", "T0") and B.fold_window("T0", "final")[0] == fz + pd.Timedelta(days=1)
    gb = SimpleNamespace(name="gb_x", family="gb")                                           # the GBM scores its finals
    assert S2._check_tiers(gb, S2.FINAL_TIERS) == ("T1", "T0")
    _raises(lambda: S2._check_tiers(gb, ("T1", "T1-holdout")), "its finals only")
    _raises(lambda: S2._check_tiers(gb, ("T3",)), "X-ALL-INSAMPLE")
    _raises(lambda: S2._check_tiers(gb, ("T9",)), "unknown tiers")
    with frozen():
        assert S2._plan(("T1",))[0][3] == FREEZE and S2._plan(("T0",))[0][2] == FREEZE + pd.Timedelta(days=1)


# ── the shadow-run ──────────────────────────────────────────────────────────

def test_days_after_the_freeze_are_t0_and_score_as_t1_would():
    """Every stage's rows after the (moved) freeze are T0, T1's fold on later days: the same rows, values and
    exclusions the real freeze scores as T1 there; X-SEL's post_selected stops at the freeze."""
    real, moved = _builds()
    first = FREEZE + pd.Timedelta(days=1)
    for st in ("s2", "s3", "s4", "out"):
        r = moved.rows[st]
        d = pd.DatetimeIndex(r["date"])
        t0 = (r["tier"] == "T0").to_numpy()
        assert t0.any() and set(r["tier"]) == {"T1", "T0"} and d[t0].min() == first and d[~t0].max() <= FREEZE, st
        assert (r.loc[t0, "fold"] == "final").all() and (r.loc[t0, "sel"] == "").all(), st
        assert (r.loc[~t0, "sel"] == "post_selected").all(), st
        key = ["entry", "unit", "date"]
        a = real.rows[st][pd.DatetimeIndex(real.rows[st]["date"]) >= first].set_index(key).sort_index()
        b = r[t0].set_index(key).sort_index()
        assert a.index.equals(b.index) and (a["tier"] == "T1").all(), st
        assert (a["excl"] == b["excl"]).all() and np.array_equal(a["y"], b["y"], equal_nan=True), st
        tol = 0.0 if st in ("s4", "out") else B.EPS            # S2 / S3: one model's p in another batch size
        assert float(np.abs(a["p"].to_numpy(dtype=float) - b["p"].to_numpy(dtype=float)).max()) <= tol, st
    assert moved.spec_info[("T0", "final")]["fit"] == moved.spec_info[("T1", "final")]["fit"]     # T1's specs, not refit
    assert moved.manifest["windows"]["freeze"] == str(FREEZE.date()) and moved.manifest["windows"]["tiers"] == ["T1", "T0"]
    with frozen():                                             # S1, S5 and X-SEL read the same freeze
        dd = pd.date_range("2026-06-29", "2026-07-02")
        assert list(S1._tier(dd)) == ["T1", "T1", "T0", "T0"]
        assert list(X.selection(dd, "geo_v1")) == ["post_selected", "post_selected", "", ""]
        run = pd.date_range("2026-06-01", AS_OF)
        days, fit, _ = S5._window("T0", S2.TRAINED_THROUGH, run)
        assert days.min() == first and fit.equals(S5._window("T1", S2.TRAINED_THROUGH, run)[1])   # T1's S5 parameters
        assert B.tier_span("T1")[1] == FREEZE and B.tier_span("T0") == (first, None) and B.tier_span("T2") is None


def test_outs_window_is_t1_post_and_t0_as_one():
    """vs_served's OUT_WINDOW: once T0 holds rows, candidate − served on T1 post ∪ T0 is one cell, equal to the cell the
    real freeze gives T1 on the same unit-days (the same Δ, CI and MCB); with no T0 row there is no such cell."""
    real, moved = _builds()
    blocks = _grading()[2]
    geo = real.bundle.geo

    def served(rows):                                          # another set's written rows: same truth, other p, 6 digits
        return rows.assign(p=B._as_written(np.clip(0.9 * rows["p"].to_numpy(dtype=float) + 0.01, 0, 1)))
    srv = served(real.rows["out"])
    srv_moved = srv.assign(tier=np.where(pd.DatetimeIndex(srv["date"]) > FREEZE, "T0", "T1"))
    a = B.vs_served({"out": real.rows["out"]}, {"rows": srv, "manifest": {"geography": "geo_v1"}}, blocks, N_BOOT, geo)
    b = B.vs_served({"out": moved.rows["out"]}, {"rows": srv_moved, "manifest": {"geography": "geo_v1"}}, blocks, N_BOOT, geo)
    assert not any(B.OUT_WINDOW in cells for by_e in a["out"].values() for cells in by_e.values())
    for u, by_e in a["out"].items():
        for e, cells in by_e.items():
            assert b["out"][u][e][B.OUT_WINDOW] == cells["T1"], (u, e)
            assert b["out"][u][e]["T1"]["n"] + b["out"][u][e]["T0"]["n"] == cells["T1"]["n"], (u, e)
    pooled = b["out"]["pooled"]["rain"][B.OUT_WINDOW]
    assert pooled["mcb"] == a["out"]["pooled"]["rain"]["T1"]["mcb"] and pooled["n"] > b["out"]["pooled"]["rain"]["T1"]["n"]


def test_t0_words_say_whether_t0_holds_days():
    first = X.freeze_date() + pd.Timedelta(days=1)
    assert B.t0_words(AS_OF)[0] == B.t0_words(first - pd.Timedelta(days=1))[0] == "empty"
    assert "T1 post-training stands alone" in B.t0_words(AS_OF)[1]
    for end in (first, first + pd.Timedelta(days=90)):
        state, words = B.t0_words(end)
        assert state == "scored" and "T1 post ∪ T0" in words and str(end.date()) in words, words
    with frozen():
        assert B.t0_words(AS_OF)[0] == "scored"


# ── the served set's T0, as served ──────────────────────────────────────────

def test_the_served_sets_t0_as_served_is_outs_scorer():
    """grade_prospective.rows → the rows the served set's name made, target day ≤ the data end → OUT by lead, per zone
    and pooled: exclusions.apply's truth and exclusions, the T2 seasons' reference, storm blocks, verify.scores_bundle."""
    served = B.served_name()
    ctx, pool, blocks = _grading()
    days = [date(2026, 7, 1) + timedelta(days=k) for k in range(48)]          # issued 1 Jul → 17 Aug, the data end
    with tempfile.TemporaryDirectory() as tmp, frozen():
        path = _snapshot(Path(tmp) / "t0.csv", served, days)
        got = B.t0_as_served(served, ctx, pool, blocks, N_BOOT, AS_OF, path)
        snap = GP.rows(path)
        mine = snap[snap["model"] == served]
        due = mine[mine["target_date"] <= pd.Timestamp(AS_OF)]
        sks = {}
        for lead in GP.LEADS:                                  # the same days as OUT rows, zone by zone, made here
            g = due[due["lead"] == lead]
            sk = X.skeleton("out", list(ZONES), g["target_date"].min(), AS_OF, entry=f"L{lead}", tier="T0")
            key = pd.MultiIndex.from_arrays([sk["unit"], pd.DatetimeIndex(sk["date"])])
            sk["p"] = g.set_index(["zone", "target_date"])["p"].reindex(key).to_numpy(dtype=float)
            sk["y"] = ctx.frames["zone"]["out_y"].reindex(key).to_numpy(dtype=float)
            assert len(sk) == len(g), lead
            sks[lead] = X.apply(sk, "out", ctx)
    assert got["state"] == "graded" and got["counts"] == {"issue_days": 48, "graded": len(due), "waiting": len(mine) - len(due),
                                                         "other_model": 2 * 6 * 4, "corrections": {"live_v2": len(mine)}}
    assert set(got["out"]["pooled"]) == {f"L{k}" for k in GP.LEADS} and len(due) < len(mine)
    for lead, sk in sks.items():
        sc = sk[sk["excl"] == ""]
        assert sum(c[f"L{lead}"]["T0"]["n_total"] for c in got["partition"].values()) == len(sk)
        ref = B.references(sc, pool)
        for u in ("pooled", "east"):
            m = np.ones(len(sc), bool) if u == "pooled" else (sc["unit"] == u).to_numpy()
            want = V.scores_bundle(sc["y"][m], sc["p"][m], ref[m], blocks["block"].reindex(pd.DatetimeIndex(sc["date"][m])).to_numpy(),
                                   edges=RL.edges(), n=N_BOOT, seed=B.SEED, level=B.LEVEL)
            cell = got["out"][u][f"L{lead}"]["T0"]
            assert {k: cell[k] for k in want} == want, (lead, u)
            assert cell["low_power"] is True and cell["span"][1] == AS_OF, (lead, u)       # one dry summer: no storm


def test_no_snapshot_or_nothing_to_grade_says_so():
    served = B.served_name()
    ctx, pool, blocks = _grading()
    with tempfile.TemporaryDirectory() as tmp:
        none = B.t0_as_served(served, ctx, pool, blocks, N_BOOT, AS_OF, Path(tmp) / "missing.csv")
        path = _snapshot(Path(tmp) / "late.csv", served, [GP.t0_start() + timedelta(days=k) for k in range(3)], other=0)
        late = B.t0_as_served(served, ctx, pool, blocks, N_BOOT, AS_OF, path)                # the real freeze: after the data
    assert none == {"state": "no snapshot", "words": none["words"]} and "No forecast_history snapshot is committed" in none["words"]
    assert late["state"] == "waiting" and "out" not in late and "after the data end" in late["words"]
    assert late["counts"] == {"issue_days": 3, "graded": 0, "waiting": 3 * 6 * 4, "other_model": 0, "corrections": {"live_v2": 72}}
    sc = json.loads((B.STAGES_DIR / served / "scores.json").read_text())                    # the committed build says which
    assert sc["t0_as_served"]["state"] == ("graded" if GP.SNAPSHOT.exists() else "no snapshot"), sc["t0_as_served"]


if __name__ == "__main__":
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
