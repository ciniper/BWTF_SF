"""S1 rows and scores (P7b, 2026-10-01): the rain forecast vs the gauges, the same for every set.

What is pinned, and why:
- the rows: one per (day, series, lead) for each weather model, in the agreed columns, every row either
  scored or carrying the first S1 rule that leaves it out (protocol §7 order), and the counts partition;
- the written files are what the code builds today, and no scored row holds a NaN (a short model-day is
  X-S1-NWPGAP, never a dry day);
- the S1 primary reproduces weather_models_eval's lead-1 either-wet MAE for ICON on that report's own rows,
  and the scores say which days make S1's own number differ; its verdicts are Holm-adjusted across the
  two candidate models (protocol §6), the unadjusted CI verdict beside them;
- the floor (one gauge as a perfect forecast of the other) is the report's masked floor;
- the benchmarks are what the words say: monthly climatology from 2016-01 to the day before the window,
  persistence from the gauge on D − L − 1 as the issue day read it, each paired with the served model on
  identical rows; models stand side by side only on the days all three scored (by lead and by season);
- s1_scores.json carries the manifest (schema, protocol stamp, built_at, input hashes that match the
  files), the full suite by lead × series × model with CIs, the benchmarks, the paired primary, and no
  ranking word the protocol retired; peak hours are never scored (X-S1-PEAK);
- the exclusion counts, as of the committed data's end (2026-08-17).

Run: venv/bin/python tests/test_stages_s1.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
for p in (str(ROOT), str(FORECAST), str(FORECAST / "src" / "models")):
    if p not in sys.path:
        sys.path.insert(0, p)

import exclusions as X  # noqa: E402
import stages_entries as E  # noqa: E402
import stages_s1 as S  # noqa: E402
from src.models import weather_models_eval as W  # noqa: E402

AS_OF = pd.Timestamp("2026-08-17")
WINDOW = (S.PR_START, AS_OF)
_cache = {}


def _rows(model):
    if model not in _cache:
        _cache[model] = S.load_rows(model)                 # committed with the scores: absent is a failure
    return _cache[model]


def _scores():
    if "scores" not in _cache:
        _cache["scores"] = json.loads(S.SCORES_JSON.read_text())
    return _cache["scores"]


def _win(r, lo=WINDOW[0], hi=WINDOW[1]):
    return r[(r["date"] >= lo) & (r["date"] <= hi)]


# ── rows ────────────────────────────────────────────────────────────────────

def test_rows_are_one_per_day_series_and_lead_and_partition():
    sc = _scores()
    days = pd.date_range(sc["rows"][0], sc["rows"][1])
    assert days[0] == S.S1_START
    order = set(X.order("S1"))
    for m in S.MODELS:
        r = _rows(m)
        assert list(r.columns) == list(S.ROW_COLUMNS), (m, list(r.columns))
        assert len(r) == len(days) * len(S.SERIES) * len(S.LEADS), (m, len(r))
        assert not r.duplicated(["date", "series", "lead"]).any() and set(r["model"]) == {m}
        assert set(r["series"]) == set(S.SERIES) and set(r["lead"]) == set(S.LEADS)
        assert set(r["excl"]) - {""} <= order, (m, set(r["excl"]))
        assert set(r["tags"]) <= {"", "X-POWER"}, (m, set(r["tags"]))
        part = X.counts(S._as_x(r))["partition"]["s1"]                 # asserts n_total = n_scored + Σ n_rule
        assert set(part) == set(S.SERIES) and all(set(e) == {f"L{L}" for L in S.LEADS} for e in part.values())
        assert all(set(t) == {"T1"} for e in part.values() for t in e.values()), "S1 has no weights: T1 before the freeze, never T3"


def test_written_rows_are_what_the_code_builds():
    fresh = S.rows("icon_seamless")
    old = _rows("icon_seamless")
    assert len(fresh) == len(old)
    assert (pd.DatetimeIndex(fresh["date"]) == pd.DatetimeIndex(old["date"])).all()
    for c in ("series", "lead", "excl", "tags"):
        assert (fresh[c].astype(str).to_numpy() == old[c].astype(str).to_numpy()).all(), c
    for c in ("fc_in", "obs_in", "fc_max1h", "fc_max3h"):
        assert np.allclose(fresh[c].to_numpy(float), old[c].to_numpy(float), rtol=1e-5, atol=1e-9, equal_nan=True), c


def test_scored_rows_hold_no_nan_and_a_short_model_day_is_never_dry():
    for m in S.MODELS:
        r = _rows(m)
        scored = r["excl"] == ""
        assert np.isfinite(r.loc[scored, ["fc_in", "obs_in"]].to_numpy(float)).all(), m
        assert (r.loc[r["fc_in"].isna(), "excl"] != "").all(), (m, "a row with no forecast must carry a rule")
        for L in S.LEADS:
            d = E.model_days(m, L).set_index("date")
            sub = r[(r["lead"] == L) & (r["series"] == "avg")].set_index("date")
            comp = d["complete"].reindex(sub.index).fillna(False).astype(bool)
            assert (sub["fc_in"].notna() == comp).all(), (m, L, "fc_in exists exactly where all 24 hours are archived")
            short = sub.index[d["complete"].reindex(sub.index).eq(False)]          # inside the span, < 24 hours
            assert (sub.loc[short, "excl"] == "X-S1-NWPGAP").all(), (m, L)
    ec = _rows("ecmwf_ifs025")
    feb = ec[(ec["lead"] == 0) & (ec["series"] == "avg") & (ec["date"] >= "2024-02-19") & (ec["date"] <= "2024-02-29")]
    assert len(feb) == 11 and (feb["excl"] == "X-S1-NWPGAP").all() and feb["fc_in"].isna().all()   # the Feb 2024 gap


# ── the primary and the report it must agree with ───────────────────────────

def test_s1_primary_reproduces_weather_models_eval():
    """ICON's lead-1 either-wet MAE (two-gauge mean) on weather_models_eval's rows — its window, the days all
    three models hold — is that report's number. S1's own number scores ICON on every day ICON holds."""
    ev = json.loads(W.OUT_JSON.read_text())
    ref = ev["by_lead"]["1"]["avg"]["inputs"]["icon_seamless"]
    lo, hi = (pd.Timestamp(d) for d in ev["window"])
    held = {m: _rows(m).pipe(lambda r: r[(r["lead"] == 1) & (r["series"] == "avg") & (r["excl"] == "")]).set_index("date") for m in S.MODELS}
    common = held["icon_seamless"].index
    for m in S.MODELS:
        common = common.intersection(held[m].index)
    common = common[(common >= lo) & (common <= hi)]
    icon = held["icon_seamless"].loc[common]
    s = W.suite(icon["fc_in"], icon["obs_in"])
    # weather_models_eval.json as committed with the 2026-08-17 data (its rows run to 2026-08-27)
    assert (s["n"], s["n_either_wet"]) == (ref["n"], ref["n_either_wet"]) == (932, 114), (s["n"], s["n_either_wet"])
    assert abs(s["continuous"]["either_wet"]["mae"] - ref["continuous"]["either_wet"]["mae"]) < 5e-6, (s["continuous"]["either_wet"], ref)
    rp = _scores()["reproduction"]
    assert rp["matches"] and rp["eval"]["mae"] == ref["continuous"]["either_wet"]["mae"]
    own = _scores()["by_lead"]["1"]["avg"]["models"]["icon_seamless"]
    assert rp["on_own_rows"]["n"] == own["n"] and abs(rp["on_own_rows"]["mae"] - own["continuous"]["either_wet"]["mae"]) < 1e-9
    why = rp["why_own_differs"]          # own = the report's rows − those after the data end + S1's extra days
    assert own["n"] == ref["n"] - why["only_report_after_the_data_end"] + why["only_s1_before_the_report_window"] \
        + sum(why["only_s1_another_model_lacks"].values()), why
    assert why["only_s1_before_the_report_window"] == 14                 # 2024-01-20 … 02-02: ECMWF's archive starts later


def test_primary_is_the_paired_lead_1_mean_comparison():
    sc = _scores()
    assert sc["primary"]["lead"] == 1 and sc["primary"]["series"] == "avg" and sc["primary"]["subset"] == "either_wet"
    assert sc["served_model"] == S.served_model() == W.SERVED_WX
    vs = sc["primary"]["vs_served"]
    assert set(vs) == set(S.MODELS) - {sc["served_model"]}
    for m, d in vs.items():
        dm = d["mae_either_wet"]
        assert dm["metric"] == "mae" and dm["n_blocks"] >= 40 and dm["level"] == 0.9, (m, dm)
        assert dm["verdict"] == ("better" if dm["hi"] < 0 else "worse" if dm["lo"] > 0 else "no clear difference"), (m, dm)
        assert d["verdict_unadjusted"] == dm["verdict"] and d["ets_0.5"]["verdict"] in ("better", "worse", "no clear difference")
        assert dm == sc["paired"]["1"]["avg"][m]["mae_either_wet"], m          # the primary is the paired table's cell
    # Holm across the candidates (protocol §6): the i-th smallest two-sided p must be ≤ 0.10 / (m − i)
    p2 = {m: min(1.0, 2 * min(d["mae_either_wet"]["p_neg"], 1 - d["mae_either_wet"]["p_neg"])) for m, d in vs.items()}
    held, ok = {}, True
    for i, m in enumerate(sorted(p2, key=p2.get)):
        ok = ok and p2[m] <= 0.10 / (len(p2) - i)
        held[m] = ok
    for m, d in vs.items():
        assert abs(d["p_two_sided"] - p2[m]) < 1e-9, m
        assert d["verdict"] == (d["verdict_unadjusted"] if held[m] else "no clear difference"), (m, d["verdict"], p2)
    # identical rows in both arms: the paired days are the days both models scored, recomputed from the rows
    served = _win(_rows(sc["served_model"]), hi=E.data_end())
    for m in vs:
        mine = _win(_rows(m), hi=E.data_end())
        a = served[(served["lead"] == 1) & (served["series"] == "avg") & (served["excl"] == "")].set_index("date")
        b = mine[(mine["lead"] == 1) & (mine["series"] == "avg") & (mine["excl"] == "")].set_index("date")
        both = a.index.intersection(b.index)
        wet = (a.loc[both, "obs_in"] >= 0.1) | (a.loc[both, "fc_in"] >= 0.1) | (b.loc[both, "fc_in"] >= 0.1)
        dm = vs[m]["mae_either_wet"]
        assert dm["n"] == int(wet.sum()), (m, dm["n"], int(wet.sum()))
        delta = (b.loc[both[wet], "fc_in"] - b.loc[both[wet], "obs_in"]).abs().mean() - (a.loc[both[wet], "fc_in"] - a.loc[both[wet], "obs_in"]).abs().mean()
        assert abs(delta - dm["delta"]) < 1e-5, (m, delta, dm["delta"])


def test_floor_is_the_reports_masked_floor():
    fl = S.floor_rows()
    g = W.load_gauges()
    ref = W.floor_table(g, WINDOW[0], E.data_end(), ci=False)["masked"]
    for truth_g, fc_g in ((W.DT, W.OC), (W.OC, W.DT)):
        sub = _win(fl[(fl["series"] == truth_g) & (fl["excl"] == "")], hi=E.data_end())
        s = W.suite(sub["fc_in"].to_numpy(), sub["obs_in"].to_numpy())
        want = ref[f"{fc_g} as {truth_g}"]
        assert s["n"] == want["n"], (truth_g, s["n"], want["n"])
        for sub_ in W.SUBSETS:
            for k in W.CONT:
                a, b = s["continuous"][sub_][k], want["continuous"][sub_][k]
                assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-12, (truth_g, sub_, k)
        got = _scores()["floor"][f"{fc_g} as {truth_g}"]
        assert got["n"] == s["n"] and abs(got["continuous"]["either_wet"]["mae"] - s["continuous"]["either_wet"]["mae"]) < 1e-5


def test_benchmarks_are_what_the_words_say():
    """Recomputed from weather_models_eval's gauge truth: monthly climatology over 2016-01 → 2024-01-19, and
    persistence = the gauge on D − L − 1, where a 0.00 the record through D − L − 1 could not yet call an
    outage reads 0.00 (stages_entries.issue_time_unmasked); each scored on the served model's rows, and its
    vs_served paired on exactly those rows."""
    sc = _scores()
    served = _rows(sc["served_model"])
    g = W.load_gauges()["truth"]
    late = E.issue_time_unmasked()
    late = set(zip(late["issue"], late["date"], late["gauge"]))
    one = pd.Timedelta(days=1)
    n_issue_time = 0
    for L in S.LEADS:
        for s in S.SERIES:
            b = sc["by_lead"][str(L)][s]["benchmarks"]
            assert b["climatology_months"] == ["2016-01-01", "2024-01-19"], b["climatology_months"]
            sub = _win(served[(served["lead"] == L) & (served["series"] == s) & (served["excl"] == "")], hi=E.data_end()).set_index("date")
            src = g[s][(g[s].index >= "2016-01-01") & (g[s].index < S.PR_START)].dropna()
            clim = src.groupby(src.index.month).mean().reindex(sub.index.month).to_numpy()
            per = {}
            for gauge in S.GAUGES:
                v = g[gauge].reindex(sub.index - (L + 1) * one).to_numpy(copy=True)
                for i, D in enumerate(sub.index):
                    if (D - L * one, D - (L + 1) * one, gauge) in late:
                        v[i] = 0.0                                       # the dead gauge's own reading
                        n_issue_time += 1
                per[gauge] = v
            pers = per[s] if s in per else np.nanmean(np.column_stack([per[x] for x in S.GAUGES]), axis=1)
            for name, fc in (("persistence", pers), ("climatology", clim)):
                keep = np.isfinite(fc)
                want = W.suite(fc[keep], sub["obs_in"].to_numpy()[keep])
                got = b[name]
                assert got["n"] == want["n"] and abs(got["continuous"]["all"]["mae"] - want["continuous"]["all"]["mae"]) < 1e-5, (L, s, name)
                vs = got["vs_served"]["mae_either_wet"]
                wet = (sub["obs_in"].to_numpy()[keep] >= 0.1) | (fc[keep] >= 0.1) | (sub["fc_in"].to_numpy()[keep] >= 0.1)
                assert vs["n"] == int(wet.sum()), (L, s, name)
                o, a, c = sub["obs_in"].to_numpy()[keep][wet], fc[keep][wet], sub["fc_in"].to_numpy()[keep][wet]
                assert abs(vs["delta"] - (np.abs(a - o).mean() - np.abs(c - o).mean())) < 1e-5, (L, s, name)
    assert n_issue_time > 10, "persistence days the issue day read differently from the whole record are exercised"


def test_models_side_by_side_share_their_days():
    """by_lead[...]['common'] and by_season score every model on the same days: the days all three scored."""
    sc = _scores()
    rows = {m: _win(_rows(m), hi=E.data_end()) for m in S.MODELS}
    for L in S.LEADS:
        for s in S.SERIES:
            held = [set(r.loc[(r["lead"] == L) & (r["series"] == s) & (r["excl"] == ""), "date"]) for r in rows.values()]
            both = sorted(set.intersection(*held))
            c = sc["by_lead"][str(L)][s]["common"]
            assert c["n"] == len(both) and c["days"] == [str(both[0].date()), str(both[-1].date())], (L, s)
            ns = {m: (st["n"], st["n_obs_wet"]) for m, st in c["models"].items()}
            assert len(set(ns.values())) == 1 and ns[sc["served_model"]][0] == len(both), (L, s, ns)
            own = sc["by_lead"][str(L)][s]["models"]
            assert all(own[m]["n"] >= c["n"] for m in S.MODELS), (L, s)        # common days are a subset of each model's own
    own1 = sc["by_lead"]["1"]["avg"]["models"]
    assert own1["icon_seamless"]["n"] > sc["by_lead"]["1"]["avg"]["common"]["n"] > 0, "ICON holds days ECMWF lacks (Jan–Feb 2024)"
    for season, blk in sc["by_season"].items():
        for L, cell in blk.items():
            assert set(cell) == set(S.MODELS) and len({(st["n"], st["first"], st["last"]) for st in cell.values()}) == 1, (season, L)
    # the lead-0 ranking the own rows would show is not the paired one: the reason for the common block
    own0, com0 = sc["by_lead"]["0"]["avg"]["models"], sc["by_lead"]["0"]["avg"]["common"]["models"]
    mae = lambda st: st["continuous"]["either_wet"]["mae"]  # noqa: E731
    assert mae(own0["ecmwf_ifs025"]) < mae(own0["icon_seamless"]) and mae(com0["ecmwf_ifs025"]) > mae(com0["icon_seamless"])
    assert sc["paired"]["0"]["avg"]["ecmwf_ifs025"]["mae_either_wet"]["delta"] > 0


# ── the scores file ─────────────────────────────────────────────────────────

def test_scores_carry_the_manifest_and_are_current():
    sc = _scores()
    assert sc["schema"] == S.SCHEMA == "bwtf.stages.s1/1"
    assert sc["protocol"] == S.protocol_stamp() and sc["protocol"].startswith("stages_v2@")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", sc["built_at"]), sc["built_at"]
    want = {str(p.relative_to(ROOT)) for p in S.input_files()}
    assert set(sc["inputs"]) == want, sorted(set(sc["inputs"]) ^ want)
    for rel, digest in sc["inputs"].items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, f"{rel} changed since the scores were built: rerun stages_s1.py --write"
    assert sc["data_end"] == str(E.data_end().date()) and sc["windows"]["previous_runs"] == ["2024-01-20", sc["data_end"]]
    assert sc["tier"]["code"] == "T1" and sc["leads"]["0"]["kind"] == "short_lead_optimistic"


def test_scores_hold_the_full_suite_by_lead_series_and_model():
    sc = _scores()
    assert set(sc["by_lead"]) == {str(L) for L in S.LEADS}
    for L, blk in sc["by_lead"].items():
        assert set(blk) == set(S.SERIES), L
        for s, cell in blk.items():
            assert set(cell["models"]) == set(S.MODELS)
            for m, st in cell["models"].items():
                assert set(st["continuous"]) == set(W.SUBSETS) and all(set(v) >= {"me", "mae", "rmse", "r", "mbias"} for v in st["continuous"].values())
                assert set(st["thresholds"]) == {"0.1", "0.25", "0.5", "1"}, (L, s, m)
                assert all(("sedi" in v) == (t == "1") for t, v in st["thresholds"].items()), "SEDI at 1.0 only"
                assert st["ci"]["level"] == 0.9 and st["ci"]["n_blocks"] >= 100 and st["first"] >= "2024-01-20" and st["last"] <= sc["data_end"]
                assert st["n"] == sc["exclusions"][m]["previous_runs"][s][f"L{L}"]["T1"]["n_scored"], (L, s, m)
            assert set(cell["benchmarks"]) >= {"persistence", "climatology"}
            for b in ("persistence", "climatology"):
                vs = cell["benchmarks"][b]["vs_served"]
                assert vs["mae_either_wet"]["n_blocks"] >= 40 and vs["mae_either_wet"]["mde"] > 0 and vs["ets_0.5"]["mde"] > 0, (L, s, b)
            assert set(cell["common"]["models"]) == set(S.MODELS)
            assert set(sc["paired"][L][s]) == set(S.MODELS) - {sc["served_model"]}
            for m, d in sc["paired"][L][s].items():                     # an MDE beside every comparison (protocol §6)
                assert d["mae_either_wet"]["mde"] > 0 and d["ets_0.5"]["mde"] > 0 and d["ets_0.5"]["se"] > 0, (L, s, m)
    icon = {L: sc["by_lead"][str(L)]["avg"]["models"]["icon_seamless"]["continuous"]["either_wet"]["mae"] for L in S.LEADS}
    assert icon[5] > icon[1] + 0.1, icon                                  # skill falls with lead
    assert sc["lead0_extension"]["avg"]["first"] == "2022-11-17" and sc["lead0_extension"]["avg"]["kind"] == "short_lead_optimistic"
    assert len(sc["by_season"]) >= 3
    text = S.SCORES_JSON.read_text()
    retired = re.findall(r"\b(?:[Cc]ost\w*|King|cheapest|alarm line|[Gg]roups?)\b", text)
    assert not retired, retired


def test_peak_hours_are_never_scored():
    for m, part in _scores()["peak"].items():
        for s, ents in part.items():
            for e, tiers in ents.items():
                cell = tiers["T1"]
                assert cell["n_scored"] == 0 and cell["excluded"].get("X-S1-PEAK", 0) > 800, (m, s, e, cell)
                score = _scores()["exclusions"][m]["previous_runs"][s][e]["T1"]
                assert cell["excluded"]["X-S1-PEAK"] == score["n_scored"], (m, s, e)


# ── counts, as of the committed data's end ──────────────────────────────────

GAUGE_COUNTS = {"SF Downtown": {"X-S1-MISSING": 36, "X-S1-OUTAGE": 12}, "SF Oceanside": {"X-S1-MISSING": 16, "X-S1-OUTAGE": 36},
                "avg": {"X-S1-MISSING": 0, "X-S1-OUTAGE": 0}}
MODEL_COUNTS = {   # two-gauge mean, 2024-01-20 → 2026-08-17: lead → (X-S1-NWPGAP, X-S1-NOLEAD)
    "icon_seamless": {0: (0, 0), 1: (4, 0), 2: (5, 0), 3: (1, 1), 4: (1, 2), 5: (1, 3)},
    "ecmwf_ifs025": {0: (12, 13), 1: (1, 14), 2: (1, 15), 3: (1, 16), 4: (1, 17), 5: (7, 18)},
    "gfs_seamless": {0: (0, 11), 1: (0, 0), 2: (1, 0), 3: (1, 1), 4: (1, 2), 5: (1, 3)},
}


def test_exclusion_counts_as_of_2026_08_17():
    days = len(pd.date_range(*WINDOW))
    assert days == 941
    for m, by_lead in MODEL_COUNTS.items():
        w = _win(_rows(m))
        for L, (gap, nolead) in by_lead.items():
            for s, gc in GAUGE_COUNTS.items():
                x = w[(w["lead"] == L) & (w["series"] == s)]["excl"].value_counts().to_dict()
                assert len(w[(w["lead"] == L) & (w["series"] == s)]) == days
                for rule, n in gc.items():
                    assert x.get(rule, 0) == n, (m, L, s, rule, x)
                if s == "avg":
                    assert (x.get("X-S1-NWPGAP", 0), x.get("X-S1-NOLEAD", 0)) == (gap, nolead), (m, L, x)


def test_the_entries_default_to_the_served_weather_model():
    """frames / drops / spans default to the model the live page reads: a switch there fails here first."""
    import inspect
    served = S.served_model()
    assert served == E.served_weather_model() == _scores()["served_model"]
    for fn in (E.frames, E.drops, E.spans):
        assert inspect.signature(fn).parameters["model"].default == served, fn.__name__


def test_unknown_ids_raise():
    for fn in (S.rows, S.load_rows):
        try:
            fn("best_match")
        except KeyError:
            continue
        raise AssertionError(f"{fn.__name__} took an unknown model")


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
