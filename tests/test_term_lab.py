"""The term lab (src/models/term_lab.py): it grades a term set exactly as the stages build grades S2, never shows the
confirmation windows, its "choose for me" is nested, its new inputs mean what they say, and nothing served reads it.
Offline. Run: venv/bin/python tests/test_term_lab.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "features/forecast/src/models"), str(ROOT / "features/forecast/src/collectors")):
    if p not in sys.path:
        sys.path.insert(0, p)

import term_lab as TL  # noqa: E402

_LAB = []


def lab() -> TL.Lab:
    if not _LAB:
        _LAB.append(TL.Lab())
    return _LAB[0]


def test_the_live_design_grades_as_its_stage_scores():
    got = TL.check(lab())                    # predictions, skills, ranges and a zero change, against the build
    assert got["prediction_rel_gap"] < 1e-4 and got["grade_gap"] < 1e-5 and got["delta_gap"] < 1e-12, got


def test_the_longer_record_grades_as_its_candidate_does():
    """The live design on the older-reports record is the scored candidate icon-w38r11a-osplit-lt2-bflags: the lab's
    skill and its change against the live model match that candidate's stage scores."""
    sc = json.loads((TL.B.STAGES_DIR / "icon-w38r11a-osplit-lt2-bflags" / "scores.json").read_text())
    g = lab().run(TL.LIVE_TERMS, record="older11")["grade"]
    for tier in TL.TIERS:
        for unit in ["pooled"] + lab().keys:
            assert abs(g[tier][unit]["skill"] - sc["s2"][unit]["oracle"][tier]["bss"]) < 1e-5, (tier, unit)
            assert abs(g[tier][unit]["delta"] - sc["paired"]["vs_served"]["s2"][unit]["oracle"][tier]["delta"]) < 1e-6, (tier, unit)


def test_the_confirmation_windows_never_show():
    assert set(lab().rows["tier"]) == set(TL.TIERS) == {"T2", "T1-holdout"}
    assert lab().rows["date"].max() <= TL.T.TRAIN_END            # nothing after the training end: no post-training day
    g = lab().run(["precip_avg"])["grade"]
    assert set(g) == set(TL.TIERS)


def test_choose_for_me_is_nested():
    pool = ["precip_avg", "rain_2d_cum", "wind_v_rain"]
    r = lab().choose(pool, max_terms=2, jobs=1, n_boot=200)
    folds = [f for _t, f, *_ in lab().plan("served")]
    assert set(r["by_fold"]) == set(folds) and r["n_folds"] == len(folds) == 10
    assert all(1 <= len(ts) <= 2 and set(ts) <= set(pool) for ts in r["by_fold"].values())
    assert 1 <= len(r["terms"]) <= 2 and set(r["terms"]) <= set(pool)
    assert set(r["nested_grade"]) == set(TL.TIERS)
    # each graded season is predicted by its own fold's pick: the nested p is the oof of those picks
    p = lab().oof(None, terms_by_fold=r["by_fold"])
    assert np.isfinite(p).all()


def test_every_size_is_graded_nested():
    """The path never stops early: sizes 1 … max_terms, each fold's first k picks scoring its own season."""
    pool = ["precip_avg", "rain_2d_cum", "wind_v_rain"]
    r = lab().choose(pool, max_terms=3, jobs=1, n_boot=200, path=True)
    assert [s["k"] for s in r["path"]] == [1, 2, 3] and r["terms"] == [s["added"] for s in r["path"]]
    assert all(len(ts) == 3 for ts in r["by_fold"].values()) and sorted(r["terms"]) == sorted(pool)
    first = lab().oof(None, terms_by_fold={f: ts[:1] for f, ts in r["by_fold"].items() if f != "all nine seasons"})
    want = lab().grade(first, 200)["T2"]["pooled"]["skill"]
    assert abs(r["path"][0]["grade"]["T2"]["pooled"]["skill"] - want) < 1e-12


def test_sensible_rules_and_the_wider_search():
    req = TL.requirements(["rain_lag2d", "precip_avg", "rain_lag1d", "precip_avg>0.25", "rain_west", "rain_max3h>0.5"])
    assert req == [(2,), (), (), (1,), (1,), (-1,)]          # lag2 after lag1; a hinge after its base; never without one
    chosen = []
    assert not TL._allowed(0, chosen, req) and TL._allowed(2, chosen, req) and not TL._allowed(5, [0, 1, 2, 3, 4], req)
    pool = ["precip_avg", "precip_avg>0.25", "wind_v_rain"]
    r = lab().choose(pool, max_terms=2, jobs=1, n_boot=200, rules=True, beam=2)
    assert r["beam"] == 2 and [s["k"] for s in r["path"]] == [1, 2] and all(len(s["terms"]) == s["k"] for s in r["path"])
    assert all(("precip_avg>0.25" not in ts) or ("precip_avg" in ts) for ts in r["by_fold"].values())


def test_new_inputs_mean_what_they_say():
    u, v = TL.wind_uv([5.0, 5.0, 5.0], [270.0, 180.0, 90.0])          # west, south, east winds
    assert np.allclose(u, [5.0, 0.0, -5.0]) and np.allclose(v, [0.0, 5.0, 0.0], atol=1e-9)
    x = TL.new_inputs(older=True)
    assert x["date"].min() == pd.Timestamp("2011-01-01") and x["date"].is_unique
    assert x["west_share"].between(0, 1).all()
    h = TL.hourly(older=True)
    day = h.assign(date=h["timestamp"].dt.normalize()).groupby("date")["precip_inches"].sum()
    assert (x.set_index("date")["rain_max24h"] >= day.reindex(x["date"]).to_numpy() - 1e-9).all()   # ≥ the day's own total
    f = lab().entry["avg"]
    assert np.allclose(f["rain_west"], f["precip_avg"] * f["west_share"]) and (f["max3h_after_wet"] <= f["rain_max3h"]).all()
    # every term on the page is a real column, and the live preset is the live 38-weight design
    for t in TL.ALL_TERMS:
        TL.design(f.reset_index(), [t])
    assert TL.PRESETS["Live (38 weights)"] == TL.LIVE_TERMS and len(TL.LIVE_TERMS) == 38


def test_nothing_served_reads_the_lab():
    probe = ("import sys; sys.path[:0] = [{r!r}, {r!r} + '/features/forecast/src/models']\n"
             "from features.forecast import live_dashboard, page\n"
             "import compose_v2, stages_build\n"
             "assert 'term_lab' not in sys.modules\n"
             "print('ok')\n").format(r=str(ROOT))
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and out.stdout.strip().endswith("ok"), out.stderr[-2000:]
    for path in [ROOT / "app/wsgi.py", ROOT / "features/forecast/live_dashboard.py", ROOT / "features/forecast/page.py"]:
        assert "term_lab" not in path.read_text(), path.name


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
