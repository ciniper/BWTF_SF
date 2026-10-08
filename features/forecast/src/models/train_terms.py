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
    stage 2    v2, the served split and linger table (``icon-t8wind-osplit-lt2-bflags`` at 8 terms), and v2_d10,
               the same split with the linger table fit on the stages' S4 truth samples (``…-lt2more-…``;
               stage2_variants.py fit --variant v2_d10): the two lingering tables under the same overflow model.
               Each set is named by its lineup (shared/lineup.py set_id).

    added      the 8-term set plus the day's 3-hour peak (``rain_max3h``), forced: 9 terms (Chase, 2026-10-07:
               "the current 8 term set that you selected PLUS the 3 hour peak"; it adds nothing yet, "it may help
               later on when we get better rain info"). Each graded fold takes its own 8 picks plus the peak; a fold
               that picked the peak itself takes its next pick instead, so every fold has 9 (``ADDED``,
               ``PEAK_SETS``; saved with the linger table on more samples only, ``--peak``).

A fold's refit in the stages build uses that fold's own terms (the manifest's ``fold_terms``, stages_s2.
check_fold_terms), so the T2 and holdout scores grade the procedure, not one term list picked with every season in
view. T2 stays labelled selection-contaminated all the same: the forced terms and the sizes were chosen with the
lab's nine-season grades in view. Post-training and the live season were never shown in the lab.

    venv/bin/python features/forecast/src/models/train_terms.py            # select and print the nested grades
    venv/bin/python features/forecast/src/models/train_terms.py --save
    venv/bin/python features/forecast/src/models/train_terms.py --peak     # the 8 terms + the 3-hour peak, from the
                                                                            # committed selection (no re-selection)
    venv/bin/python features/forecast/src/models/stages_build.py --set icon-t8wind-osplit-lt2-bflags --root candidates --write
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
STAGE2 = {"s2v2": "v2", "s2v2d10": "v2_d10"}         # which linger table → stage 2 id (stage2_variants.load_spec)
ADDED = {"max3h": ["rain_max3h"]}                     # terms forced on top of a size's picks (Chase, 2026-10-07)
PEAK_SETS = ((8, "max3h", "s2v2d10"),)                # (size, addition, linger table): the 8 terms + the 3-hour peak
# protocol §2's post_seen reasons (candidates.tag_candidate): the record, and the linger table on more samples
POST_SEEN = {"record": ("its training record (the older SFPUC reports from 2011, every discharge day) was chosen in "
                        "OLDER_REPORTS.md with the older-report sets' post-training scores in view"),
             "v2_d10": ("its linger table on more samples was built after sfpuc-icon-t8s-osplits-pickout-lzflags's "
                        "post-training scores showed the extra samples helping the public number")}


def stage1_name(k: int, added: str | None = None) -> str:
    return f"logit_wind{k}_{added + '_' if added else ''}{RECORD_KEY}"


def set_name(k: int, suffix: str = "s2v2", added: str | None = None) -> str:
    """The set saved for k terms (plus ADDED[added]) and stage 2 STAGE2[suffix]: its lineup's id (candidates.geo_v1_set)."""
    return CAND.geo_v1_set(stage1_name(k, added), SV.load_spec(STAGE2[suffix]))[0]


def with_added(ts: list, k: int, added: str | None = None) -> list:
    """A pick order's first k terms plus ADDED[added]; an added term the order already holds is taken out of it first,
    so the next pick fills its place and the list always has k + len(ADDED[added]) terms."""
    extra = ADDED[added] if added else []
    return [t for t in ts if t not in extra][:k] + extra


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


def fold_terms(sel: dict, k: int, added: str | None = None) -> dict:
    return {f: with_added(ts, k, added) for f, ts in sel["by_fold"].items()}


def lab_grade(sel: dict, k: int, added: str, lab=None) -> dict:
    """The term lab's nested grade of k picks plus ADDED[added], each graded fold on its own list (the grade the
    selection path records for a plain size): {tier: pooled cell}."""
    import term_lab as TL
    lab = lab or TL.Lab()
    g = lab.grade(lab.oof(None, C=sel["C"], nonneg=sel["nonneg"], record=RECORD_KEY, terms_by_fold=fold_terms(sel, k, added)))
    return {t: v["pooled"] for t, v in g.items()}


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


def fit(sel: dict, k: int, models: dict, added: str | None = None) -> dict:
    """{finals, holdouts, chosen, per_basin, notes, rows, terms, fold_terms} for size k (plus ADDED[added])."""
    basin_of = {v: b for b, v in BASIN_KEYS.items()}
    terms, ft = with_added(sel["terms"], k, added), fold_terms(sel, k, added)
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


def check_pairing(spec: dict) -> None:
    """A spec carrying the served stage 2's id must be the served stage2.json (a stale data/models/stage2/ file
    would pair a candidate with a table the live forecast does not use)."""
    if CAND.stage2_variant_id(spec) == CAND.served_info()["stage2"] and spec != json.loads((T.SERVE_DIR / "stage2.json").read_text()):
        raise SystemExit(f"data/models/stage2/ {CAND.stage2_variant_id(spec)} is not the served stage 2: refusing to pair with it")


def save(sel: dict, k: int, got: dict, suffix: str = "s2v2", added: str | None = None, grade: dict | None = None) -> Path:
    """Save size k (plus ADDED[added], whose nested ``grade`` lab_grade gives) with stage 2 STAGE2[suffix], tagged
    post_seen (POST_SEEN)."""
    variant = STAGE2[suffix]
    spec = SV.load_spec(variant)
    check_pairing(spec)
    name, lineup = CAND.geo_v1_set(stage1_name(k, added), spec)
    table = {"v2": "stage 2 v2", "v2_d10": "stage 2 v2 with its linger table fit on the stages' S4 truth samples (v2_d10)"}[variant]
    n = len(got["terms"])
    how = (f"{n} named terms per basin, the same in every basin: {', '.join(got['terms'])}. The day's rain, the 2- and "
           f"3-day sums and the south wind on the rainy hours are forced; the rest picked by nested forward selection "
           f"(train_terms.py), each fold on its own seasons")
    if added:
        how += (f"; then {', '.join(ADDED[added])} forced on top of the {k} picks (Chase, 2026-10-07), a fold that picked "
                f"it taking its next pick instead")
    note = (f"{how}. The served rain sources and C, the older SFPUC reports from 2011 (every discharge day), {table}. "
            f"Not served.")
    if added:
        if grade is None:
            raise ValueError("an added term's nested grade comes from lab_grade")
    else:
        grade = {t: g["pooled"] for t, g in next(s for s in sel["path"] if s["k"] == k)["grade"].items()}
    extra = {"record": sel["record"], "record_notes": got["notes"]["record"], "record_counts": TOR.record_counts(got["rows"]),
             "stage1_source": "fit", "terms": got["terms"], "fold_terms": got["fold_terms"],
             "term_selection": {"by": "train_terms.py → term_lab.Lab.choose", "must": sel["must"], "rules": sel["rules"],
                                "pool": sel["pool"], "size": k, **({"added": ADDED[added]} if added else {}),
                                "lab_grade": grade}}
    d = CAND.save_candidate(name, "logit", got["finals"], got["holdouts"], got["chosen"], got["features"], got["per_basin"],
                            note=note, extra=extra, stage2=spec, stage1_from="fit", stage1_name=stage1_name(k, added),
                            lineup=lineup)
    (d / SELECTION_FILE).write_text(json.dumps(sel, indent=1, default=float) + "\n")
    CAND.tag_candidate(name, "post_seen", "; ".join([POST_SEEN["record"]] + ([POST_SEEN[variant]] if variant in POST_SEEN else [])))
    CAND.rescore_post(name)                         # post-training days on the served input rules, as every set's
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
    ap.add_argument("--peak", action="store_true", help="save PEAK_SETS from the committed selection (no re-selection)")
    a = ap.parse_args(argv)
    if a.peak:
        sel = json.loads((CAND.candidate_dir(set_name(8, "s2v2d10")) / SELECTION_FILE).read_text())
        models = served_models()
        for k, added, suffix in PEAK_SETS:
            grade = lab_grade(sel, k, added)
            print(f"  {k} terms + {', '.join(ADDED[added])}: " + " · ".join(
                f"{t} {g['skill']:.3f} (live {g['live_skill']:.3f}, {g['verdict']})" for t, g in grade.items()))
            save(sel, k, fit(sel, k, models, added), suffix, added, grade)
        return
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


if __name__ == "__main__":
    main()
