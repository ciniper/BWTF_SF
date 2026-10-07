#!/usr/bin/env python3
"""Overflow-model candidates on a short named term list with the south wind, the terms picked nested. Nothing
served moves.

Chase, 2026-10-07: "an 8/9/10 term model and including the wind ... a 2 day indicator and possible a 3-4 day
indicator and maybe even 24 hour". The term lab (term_lab.py) graded those asks; this saves them as candidates the
stages build scores:

    forced     the day's rain, the 2-day and 3-day sums, the south wind on the rainy hours (wind_v_rain): every
               model holds them. The 4-day sum tied the 3-day one; the largest 24-hour rain lowered the nine-season
               skill at every size and made the holdout worse at 6 terms (it mostly repeats the day's rain), so it
               is left out.
    the rest   added one at a time by term_lab.Lab.choose: nested forward selection inside every graded fold (the
               nine seasons left out in turn, and the holdout fold), on the terms the page and the stages build can
               compute (``servable``), with the sensible rules (a hinge after its base, lags in order), to 10 terms.
    sizes      8, 9 and 10 terms (SIZES): each graded fold keeps its own first k, the finals the first k picked on
               all nine seasons.
    the rest of the design   per basin the served set's rain source and C, standardised L2 logistic (leaderboard.
               make_terms_model), the older SFPUC reports from 2011 as labels where CIWQS is silent, every discharge
               day (train_older_reports' best record).
    stage 2    v2, the served split and linger table (``<name>_s2v2``), and v2_d10, the same split with the linger
               table fit on the stages' S4 truth samples (``<name>_s2v2d10``; stage2_variants.py fit --variant
               v2_d10): the two lingering tables under the same overflow model.

A fold's refit in the stages build uses that fold's own terms (the manifest's ``fold_terms``, stages_s2.
check_fold_terms), so the T2 and holdout scores grade the procedure, not one term list picked with every season in
view. T2 stays labelled selection-contaminated all the same: the forced terms and the sizes were chosen with the
lab's nine-season grades in view. Post-training and the live season were never shown in the lab.

    venv/bin/python features/forecast/src/models/train_terms.py            # select and print the nested grades
    venv/bin/python features/forecast/src/models/train_terms.py --save
    venv/bin/python features/forecast/src/models/stages_build.py --set logit_wind8_older11_s2v2 --root candidates --write
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import candidates as CAND  # noqa: E402
import leaderboard as L  # noqa: E402
import train_older_reports as TOR  # noqa: E402  (training_rows, record_counts: the older-record rows, as its candidates)
import stage2_variants as SV  # noqa: E402
import train_v4 as T  # noqa: E402
from groups import BASIN_KEYS  # noqa: E402
from rain_features import WIND_FEATURES  # noqa: E402

MUST = ["precip_avg", "rain_2d_cum", "rain_3d_cum", "wind_v_rain"]
SIZES = (8, 9, 10)
RECORD_KEY = "older11"                                # term_lab.RECORDS: the older reports from 2011, every day
SELECTION = {"rules": True, "path": True, "C": "live", "nonneg": False, "max_terms": max(SIZES)}
KEYS = TOR.KEYS                                       # the four basins and citywide
SELECTION_FILE = "selection.json"
STAGE2 = {"s2v2": "v2", "s2v2d10": "v2_d10"}         # set-name suffix → stage 2 id (stage2_variants.load_spec)


def stage1_name(k: int) -> str:
    return f"logit_wind{k}_{RECORD_KEY}"


def set_name(k: int, suffix: str = "s2v2") -> str:
    return f"{stage1_name(k)}_{suffix}"


def servable(terms) -> list:
    """The terms a frame can give: a base input, the wind, a derived input, or a hinge on one of them."""
    ok = set(L.FEATS) | set(WIND_FEATURES) | set(L.TERM_DERIVED)
    return [t for t in terms if t.partition(">")[0] in ok]


def select(lab=None) -> dict:
    """term_lab's nested choice on the servable pool: {terms (all nine seasons, in order), by_fold, path}."""
    import term_lab as TL
    lab = lab or TL.Lab()
    pool = servable(TL.ALL_TERMS)
    r = lab.choose(pool, record=RECORD_KEY, must=MUST, jobs=-1, **SELECTION)
    for f, ts in {**r["by_fold"], "all nine seasons": r["terms"]}.items():
        if ts[:len(MUST)] != MUST or len(ts) < max(SIZES):
            raise AssertionError(f"{f}: the choice does not start with the forced terms or stops short: {ts}")
    return {"pool": pool, "must": MUST, "record": TL.RECORDS[RECORD_KEY], **SELECTION, "terms": r["terms"],
            "by_fold": r["by_fold"], "path": [{"k": s["k"], "terms": s["terms"], "grade": s["grade"]} for s in r["path"]]}


def fold_terms(sel: dict, k: int) -> dict:
    return {f: ts[:k] for f, ts in sel["by_fold"].items()}


def terms_season_cv(sub: pd.DataFrame, terms: list, C: float, features: list) -> dict:
    """leaderboard.season_cv with the term model (the finals' terms; a display number, not nested)."""
    pre = sub[sub["date"] < T.HOLDOUT_START]
    ys, ps = [], []
    for s in sorted(pre["season"].unique()):
        tr, te = pre[pre["season"] != s], pre[pre["season"] == s]
        if tr["y"].sum() < 5 or len(te) == 0:
            continue
        m = L.make_terms_model(terms, C).fit(tr[features], tr["y"])
        ys.append(te["y"].to_numpy())
        ps.append(m.predict_proba(te[features])[:, 1])
    return L.scores(np.concatenate(ys), np.concatenate(ps)) if ys else {}


def fit(sel: dict, k: int, models: dict) -> dict:
    """{finals, holdouts, chosen, per_basin, notes, rows, terms, fold_terms} for size k."""
    basin_of = {v: b for b, v in BASIN_KEYS.items()}
    terms, ft = sel["terms"][:k], fold_terms(sel, k)
    features = L.term_inputs(sorted({t for ts in [terms, *ft.values()] for t in ts}))
    chosen = {basin_of.get(key, "citywide"): m["rain_source"] for key, m in models.items()}
    heads, _ = T.stage2_from_served()
    sources = sorted(set(chosen.values()) | set(T.RAIN_SOURCES) | {h.get("rain_source", "avg") for h in heads.values()})
    frames, notes = T.build_dataset(sources=sources, record=sel["record"], wind=True)
    use_archive = json.loads((T.SERVE_DIR / "eval_report.json").read_text())["stage1_archive_labels"]
    finals, holdouts, per_basin, rows = {}, {}, {}, {}
    for key in KEYS:
        basin, m = basin_of.get(key, "citywide"), models[key]
        sub = TOR.training_rows(frames, basin, m["rain_source"], use_archive)
        if sub[features].isna().any().any():
            raise ValueError(f"{key}: a training row has no {[c for c in features if sub[c].isna().any()]}")
        pre, te = sub[sub["date"] < T.HOLDOUT_START], sub[sub["date"] >= T.HOLDOUT_START]
        final = L.make_terms_model(terms, m["C"]).fit(sub[features], sub["y"])
        hold = L.make_terms_model(ft["pre_holdout"], m["C"]).fit(pre[features], pre["y"])   # the holdout fold's own pick
        finals[key] = {"model": final, "features": features, "calibration_offset": 0.0, "C": m["C"], "terms": terms}
        holdouts[key] = hold
        cv = terms_season_cv(sub, terms, m["C"], features)
        per_basin[key] = {"source": m["rain_source"], "C": m["C"], "terms": terms,
                          "holdout": L.scores(te["y"], hold.predict_proba(te[features])[:, 1]),
                          "season_cv_pre_holdout": {**cv, "n": int(len(pre)), "pos": int(pre["y"].sum())},
                          "n_events": int(sub["y"].sum()), "first_day": str(sub["date"].min().date())}
        rows[key] = sub
    return {"finals": finals, "holdouts": holdouts, "chosen": chosen, "per_basin": per_basin, "notes": notes,
            "rows": rows, "terms": terms, "fold_terms": ft, "features": features}


def _grade_words(g: dict) -> str:
    t2, h = g["T2"]["pooled"], g["T1-holdout"]["pooled"]
    return (f"nine seasons {t2['skill']:.3f} (live {t2['live_skill']:.3f}, {t2['verdict']}) · "
            f"holdout {h['skill']:.3f} (live {h['live_skill']:.3f}, {h['verdict']})")


def save(sel: dict, k: int, got: dict, suffix: str = "s2v2") -> Path:
    name, variant = set_name(k, suffix), STAGE2[suffix]
    spec = SV.load_spec(variant)
    if variant == "v2" and spec != json.loads((T.SERVE_DIR / "stage2.json").read_text()):
        raise SystemExit("data/models/stage2/v2.json is not the served stage 2: refusing to pair with it")
    table = {"v2": "stage 2 v2", "v2_d10": "stage 2 v2 with its linger table fit on the stages' S4 truth samples (v2_d10)"}[variant]
    note = (f"{k} named terms per basin, the same in every basin: {', '.join(got['terms'])}. The day's rain, the 2- and "
            f"3-day sums and the south wind on the rainy hours are forced; the rest picked by nested forward selection "
            f"(train_terms.py), each fold on its own seasons. The served rain sources and C, the older SFPUC reports from "
            f"2011 (every discharge day), {table}. Not served.")
    path = next(s for s in sel["path"] if s["k"] == k)
    extra = {"record": sel["record"], "record_notes": got["notes"]["record"], "record_counts": TOR.record_counts(got["rows"]),
             "stage1_source": "fit", "terms": got["terms"], "fold_terms": got["fold_terms"],
             "term_selection": {"by": "train_terms.py → term_lab.Lab.choose", "must": sel["must"], "rules": sel["rules"],
                                "pool": sel["pool"], "size": k, "lab_grade": {t: g["pooled"] for t, g in path["grade"].items()}}}
    d = CAND.save_candidate(name, "logit", got["finals"], got["holdouts"], got["chosen"], got["features"], got["per_basin"],
                            note=note, extra=extra, stage2=spec, stage1_from="fit", stage1_name=stage1_name(k))
    print(f"candidate → {d}")
    return d


def served_models() -> dict:
    """The served pickles (rain source, C), the design this module replaces with a term list."""
    sv = CAND.served_info()
    out = {}
    for key in KEYS:
        with open(T.SERVE_DIR / f"{key}_model.pkl", "rb") as f:
            out[key] = pickle.load(f)
        if "C" not in out[key] or out[key]["calibration_offset"] != 0.0:
            raise SystemExit(f"served {key}: no C or a dry-day offset; {sv['name']} is not a logit set")
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--save", action="store_true", help=f"save the {', '.join(map(str, SIZES))}-term candidates")
    a = ap.parse_args(argv)
    sel = select()
    for s in sel["path"]:
        print(f"  {s['k']:2} terms  {_grade_words(s['grade'])}")
    print("  all nine seasons:", ", ".join(sel["terms"]))
    for f, ts in sel["by_fold"].items():
        print(f"  {f:12} {', '.join(ts[len(MUST):])}")
    if not a.save:
        return
    models = served_models()
    for k in SIZES:
        got = fit(sel, k, models)
        for suffix in STAGE2:
            save(sel, k, got, suffix)
            (CAND.candidate_dir(set_name(k, suffix)) / SELECTION_FILE).write_text(json.dumps(sel, indent=1, default=float) + "\n")
            CAND.rescore_post(set_name(k, suffix))      # post-training days on the served input rules, as every set's


if __name__ == "__main__":
    main()
