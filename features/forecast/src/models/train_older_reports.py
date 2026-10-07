#!/usr/bin/env python3
"""Candidates trained on the older discharge reports (collectors/csd_pre2018.py): the served
overflow model's design refit on a longer label record. Nothing served moves.

The design is the served set's, unchanged: per basin the same 19 inputs + hinges, standardised
L2 logistic, the same C and rain source (the served pickles, cloned). Only the training rows
grow: the older reports label the days CIWQS is silent on (train_v4.apply_older_labels),
from the record's start. The beach split and the linger table are the served stage 2 v2
spec, and the stages build fits them per fold on the served record, so a candidate differs
from the served set in its S2 weights alone.

    RECORDS       start        older reports used
    older16_*     2016-03-01   Westside Mar 2016 – Dec 2017, Bayside Mar – Sep 2016 (no new rain)
    older11_*     2011-03-01   everything, Mar 2011 on (rain before 2016 from the older files)
    *_first       the first day of each run of discharge days (the modern "an event started")
    *_every       every discharge day

Scores here are the trainer's own (holdout and pre-holdout season CV, as the leaderboard keeps
them); the protocol's scores come from the stages build:

    venv/bin/python features/forecast/src/models/train_older_reports.py            # compare, write nothing
    venv/bin/python features/forecast/src/models/train_older_reports.py --save all
    venv/bin/python features/forecast/src/models/stages_build.py --set logit_v1_older16_first_s2v2 --root candidates --write
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import pandas as pd
from sklearn.base import clone

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import candidates as CAND  # noqa: E402
import leaderboard as L  # noqa: E402
import stage2 as ST2  # noqa: E402
import train_v4 as T  # noqa: E402
from groups import BASIN_KEYS  # noqa: E402
from shared.lineup import WORDS as _WORDS  # noqa: E402

LU_WORDS = _WORDS["s2"]

STAGE1 = "logit_v1"                      # the served overflow model's design, refit here
RECORDS = {
    "logit_v1_older16_first": {"labels": "csd_pre2018", "day_rule": "first", "start": "2016-03-01"},
    "logit_v1_older16_every": {"labels": "csd_pre2018", "day_rule": "every", "start": "2016-03-01"},
    "logit_v1_older11_first": {"labels": "csd_pre2018", "day_rule": "first", "start": "2011-03-01"},
    "logit_v1_older11_every": {"labels": "csd_pre2018", "day_rule": "every", "start": "2011-03-01"},
}
DROUGHT = (pd.Timestamp("2011-10-01"), pd.Timestamp("2016-09-30"))   # water years 2012–2016
KEYS = [BASIN_KEYS[b] for b in T.APP_BASINS] + ["citywide"]


def set_name(stage1: str) -> str:
    return f"{stage1}_s2v2"


def served_models() -> dict:
    sv = CAND.served_info()
    if sv["stage1"] != STAGE1:
        raise SystemExit(f"the served overflow model is {sv['stage1']!r}; this refits {STAGE1}'s design")
    out = {}
    for key in KEYS:
        with open(T.SERVE_DIR / f"{key}_model.pkl", "rb") as f:
            out[key] = pickle.load(f)
        m = out[key]
        if m["calibration_offset"] != 0.0 or m["features"] != L.FEATS or "C" not in m:
            raise SystemExit(f"served {key}: not the 19-input logit design with a zero offset")
    return out


def training_rows(frames: dict, basin: str, source: str, use_archive: dict) -> pd.DataFrame:
    """stages_s2.set_rows' rows: covered days of the basin's rain source, archive days dropped where the trainer
    dropped them, through TRAIN_END. citywide drops a day any basin's archive label decided, as train_v4 does."""
    sub = T.target_frame(frames[source], basin)
    if basin == "citywide":
        if not all(use_archive.values()):
            sub = sub[~(sub[[f"{b}_label_source" for b in T.APP_BASINS]] == "poobot").any(axis=1)]
    elif not use_archive[basin]:
        sub = sub[sub[f"{basin}_label_source"] != "poobot"]
    return sub[sub["date"] <= T.TRAIN_END]


def fit(record: dict | None, models: dict) -> dict:
    """{finals, holdouts, chosen, per_basin, notes, rows} of the served design on the record (None = the served record)."""
    basin_of = {v: k for k, v in BASIN_KEYS.items()}
    chosen = {basin_of.get(k, "citywide"): m["rain_source"] for k, m in models.items()}
    heads, _ = T.stage2_from_served()
    sources = sorted(set(chosen.values()) | set(T.RAIN_SOURCES) | {h.get("rain_source", "avg") for h in heads.values()})
    frames, notes = T.build_dataset(sources=sources, record=record)
    use_archive = json.loads((T.SERVE_DIR / "eval_report.json").read_text())["stage1_archive_labels"]
    finals, holdouts, per_basin, rows = {}, {}, {}, {}
    for key in KEYS:
        basin, m = basin_of.get(key, "citywide"), models[key]
        sub = training_rows(frames, basin, m["rain_source"], use_archive)
        pre, te = sub[sub["date"] < T.HOLDOUT_START], sub[sub["date"] >= T.HOLDOUT_START]
        final = clone(m["model"]).fit(sub[m["features"]], sub["y"])
        hold = clone(m["model"]).fit(pre[m["features"]], pre["y"])
        finals[key] = {"model": final, "features": m["features"], "calibration_offset": 0.0, "C": m["C"]}
        holdouts[key] = hold
        per_basin[key] = {"source": m["rain_source"], "C": m["C"],
                          "holdout": L.scores(te["y"], hold.predict_proba(te[m["features"]])[:, 1]),
                          "season_cv_pre_holdout": L.season_cv(sub, "logit", m["C"]),
                          "n_events": int(sub["y"].sum()), "first_day": str(sub["date"].min().date())}
        rows[key] = sub
    return {"finals": finals, "holdouts": holdouts, "chosen": chosen, "per_basin": per_basin, "notes": notes, "rows": rows}


def record_counts(rows: dict) -> dict:
    """Per basin: training days and discharge days the older reports add, and how many fall in the 2012–2016 drought."""
    out = {}
    for key, sub in rows.items():
        if key == "citywide":
            continue
        basin = {v: k for k, v in BASIN_KEYS.items()}[key]
        old = sub[sub[f"{basin}_label_source"] == "csd_pre2018"]
        dr = old[(old["date"] >= DROUGHT[0]) & (old["date"] <= DROUGHT[1])]
        out[key] = {"days": int(len(old)), "discharge_days": int(old["y"].sum()),
                    "drought_days": int(len(dr)), "drought_discharge_days": int(dr["y"].sum())}
    return out


def _row(key: str, a: dict, b: dict) -> str:
    def f(s, k):
        return f"{s[k]:.4f}" if s.get(k) is not None else "  —   "
    ha, hb, ca, cb = a["holdout"], b["holdout"], a["season_cv_pre_holdout"], b["season_cv_pre_holdout"]
    return (f"  {key:12} events {a['n_events']:3} → {b['n_events']:3} | holdout Brier {f(ha, 'brier')} → {f(hb, 'brier')}"
            f"  PR {f(ha, 'pr_auc')} → {f(hb, 'pr_auc')} | pre-holdout CV Brier {f(ca, 'brier')} → {f(cb, 'brier')}"
            f" (n {ca.get('n')} → {cb.get('n')})")


def save(stage1: str, record: dict, got: dict) -> Path:
    name = set_name(stage1)
    spec = ST2.load_variant("v2")
    if spec != json.loads((T.SERVE_DIR / "stage2.json").read_text()):
        raise SystemExit("data/models/stage2/v2.json is not the served stage 2: refusing to pair with it")
    rule = {"first": "the first day of each run of discharge days", "every": "every discharge day"}[record["day_rule"]]
    note = (f"The served overflow model's design (38 weights: 19 inputs + hinges, L2 logistic, the served C and rain sources) "
            f"refit on a longer label record: the older SFPUC discharge reports from {record['start']} where CIWQS is silent, "
            f"{rule} as a discharge day. Stage 2 v2, the served split and linger table. Not served.")
    extra = {"record": record, "record_notes": got["notes"]["record"], "record_counts": record_counts(got["rows"]),
             "stage1_source": "fit"}
    d = CAND.save_candidate(name, "logit", got["finals"], got["holdouts"], got["chosen"], L.FEATS, got["per_basin"],
                            note=note, extra=extra, stage2=spec, stage1_from="fit", stage1_name=stage1)
    print(f"candidate → {d}")
    return d


# ── the write-up: every number read from the committed stage scores ──────────

REPORT = HERE.parents[1] / "OLDER_REPORTS.md"
STAGES_DIR = T.SERVE_DIR / "stages"
TIERS = (("T2", "nine seasons"), ("T1-holdout", "holdout"), ("T1", "post-training"))
BASIN_ROWS = (("pooled", "All four"), ("westside", "Westside"), ("north_shore", "North Shore"), ("central", "Central"),
              ("southeast", "Southeast"))
ZONE_ROWS = (("pooled", "All zones"), ("ocean", "Ocean Beach"), ("baker_china", "Baker & China"), ("north", "North"),
             ("east", "East"))
WORD = {"better": "better", "worse": "worse", "no clear difference": "no clear diff."}


def _scores(name: str) -> dict:
    p = STAGES_DIR / name / "scores.json"
    if not p.exists():
        raise SystemExit(f"{name} has no stage scores: run stages_build.py --set {name} --root candidates --write")
    return json.loads(p.read_text())


def _skill(cell: dict) -> str:
    lo, hi = cell["ci"]["bss"]
    return f"{cell['bss']:.2f} [{lo:.2f}–{hi:.2f}]"


def _delta(cell: dict) -> str:
    return f"{cell['delta'] * 1000:+.2f} [{cell['lo'] * 1000:+.2f}, {cell['hi'] * 1000:+.2f}] {WORD[cell['verdict']]}"


def _rain_table(frames: dict) -> list:
    """P(discharge day | that day's rain) per basin on its own gauge, older reports vs CIWQS, for one record."""
    bins, labels = [0.25, 0.5, 1.0, 1.5, 99.0], ["¼–½″", "½–1″", "1–1½″", "1½″+"]
    out = []
    for basin in T.APP_BASINS:
        f = frames[CAND.served_info()["rain_sources"][BASIN_KEYS[basin]]]
        f = f[(f[f"{basin}_covered"] == 1) & (f["date"] <= T.TRAIN_END)]
        rain = f["rain_2d_cum"] - f["rain_lag1d"]                       # the day's own total
        cells = []
        for era in ("csd_pre2018", "ciwqs"):
            g = f[f[f"{basin}_label_source"] == era]
            r = rain[g.index]
            cells.append([f"{g.loc[(r >= lo) & (r < hi), f'{basin}_csd'].mean():.0%} of {int(((r >= lo) & (r < hi)).sum())}"
                          for lo, hi in zip(bins, bins[1:])])
        out.append((basin, cells))
    return [f"| {b} | " + " | ".join(f"{o} · {c}" for o, c in zip(*cells)) + " |" for b, cells in out], labels


def _season_rows(frames: dict) -> list:
    rain, _ = T.rain_series("avg", older_rain=True)
    r = pd.DataFrame({"date": rain.index, "r": rain.values})
    r["season"] = T.wet_season(r["date"])
    f = frames["avg"]
    rows = []
    for season in range(2011, 2025):
        g, sub = r[r["season"] == season], f[f["season"] == season]
        old = (sub["Westside_label_source"] == "csd_pre2018").any()
        cells = [f"{int(sub.loc[sub[f'{b}_covered'] == 1, f'{b}_csd'].sum())}" for b in T.APP_BASINS]
        rows.append(f"| {season}-{(season + 1) % 100:02d}{' ·' if old else ''} | {g['r'].sum():.1f} | {int((g['r'] >= 0.5).sum())} | "
                    + " | ".join(cells) + " |")
    return rows


# The reading, dated: hand-written from the tables below (rerun --report after a rebuild and re-read it).
READING = [
    "*Read 2026-10-06, from the tables below.*", "",
    "- **Count every discharge day.** \"All days\" beats \"first days\" in every window, bar one tie: the public number on "
    "the holdout.",
    "  - With all days counted, an older-report day behaves like a CIWQS day. At each rain level the share of days with an "
    "overflow is close (older vs CIWQS): Westside ½–1″ 33% vs 37%, Central 44% vs 43%.",
    "  - On a heavy-rain day the CIWQS ledger almost always records an overflow starting that day. \"First days\" leaves "
    "out the second day of a two-day storm (Central on 1–1½″ days: 50% vs 87%), which teaches the model that heavy rain sometimes "
    "brings none.",
    "- **The 2016–17 reports alone change little.** Basin-pooled and zone-pooled, every window shows no clear difference, "
    "apart from \"first days\" being worse on post-training days. A few single basins move either way: Southeast is "
    "better on the nine seasons with all days, and Central is worse on post-training days with either rule.",
    "- **The 2011–17 reports, all days, make the strongest challenger so far on the nine seasons.**",
    "  - Its overflow model is better basin-pooled, with Central and Southeast better on their own. Westside and North "
    "Shore show no clear difference.",
    "  - The public number is better too, East most.",
    "  - On the holdout and on post-training days, basin-pooled and zone-pooled, there is no clear difference. On the "
    "holdout, Central is better while North Shore and the North zone's public number are slightly worse.",
    "- **It does not pass the pre-registered tests yet.** Two things stop it:",
    "  - The overflow model's nine-season win cannot count: the 38-weight design was picked with those seasons in view "
    "(protocol §2).",
    "  - The public number's one-day-ahead non-inferiority test misses because its range is too wide, not because the "
    "estimate is worse: an upper bound of +1.29 against a +1.09 margin, on 984 post-training zone-days. The live season "
    "(T0) adds days after the December–January ledger refresh.",
    "  - On the promotion checklist it meets 1 of 5, calibration only. Each 2016–17 set meets 2, as "
    "38-weight · No split · Linger table 1 does. Promotion is the owner's call (protocol §9).",
    "- **With all days counted, Westside gains least, though it gets the most new days** (65 discharge days). Its changes "
    "stay inside the noise in every window.",
    "- **The drought years still had storms.** Most of the added days fall in the 2012–2016 drought: 1,826 of Westside's "
    "2,466, and 39 of its 65 discharge days. Those seasons still had 10–13 days of ½″ or more each, about like 2021-22 or "
    "2024-25, so they add storms, not only dry days.",
]


def report() -> str:
    """OLDER_REPORTS.md's text: the reading, what the older reports add and whether they behave like the CIWQS ledger,
    then each candidate against the served set, stage by stage, from stages/<set>/scores.json (paired on the days
    both scored; never recomputed here)."""
    sv = CAND.served_info()["name"]
    srv = _scores(sv)
    lines = ["# Training on the older discharge reports", "",
             "Generated by `src/models/train_older_reports.py --report` from the committed stage scores. Edit the script, "
             "not this file. The method and guardrails are in `SWAPS.md` (\"Training on the older discharge reports\") and "
             "`STAGES_DESIGN.md` Part B 31.", "",
             f"Every candidate is the live overflow model's design ({LU_WORDS['logit_v1']}) refit on a longer label record:",
             "- the same 19 inputs and hinges, the same C, the same rain gauge per basin;",
             "- the live beach split and linger table.", "",
             f"The scoring is the frozen protocol's: the same days, the same truth (the CIWQS ledger only), the same holdout "
             f"from {T.HOLDOUT_START.date()}. An older-report day is a training day, never a scored one.", "",
             "## Result", "", *READING, ""]
    mans = {st1: json.loads((CAND.candidate_dir(set_name(st1)) / "manifest.json").read_text()) for st1 in RECORDS}
    lines += ["## What the older reports add", "",
              "Discharge days added per basin to the training record. In brackets: how many fall in the 2012–2016 drought, "
              f"water years 2012–2016 ({DROUGHT[0].date()} → {DROUGHT[1].date()}).", "",
              "| basin | CIWQS, to the training end | " + " | ".join(LU_WORDS[st1].replace("38-weight + ", "") for st1 in RECORDS)
              + " | days added, 2016–17 / 2011–17 |",
              "|---|" + "---|" * (len(RECORDS) + 2)]
    base = {k: v["n_events"] for k, v in CAND.served_info()["per_basin"].items()}
    for key, label in BASIN_ROWS[1:]:
        cells = [f"{mans[st1]['record_counts'][key]['discharge_days']} ({mans[st1]['record_counts'][key]['drought_discharge_days']})"
                 for st1 in RECORDS]
        days = f"{mans['logit_v1_older16_every']['record_counts'][key]['days']:,} / {mans['logit_v1_older11_every']['record_counts'][key]['days']:,}"
        lines.append(f"| {label} | {base[key]} | " + " | ".join(cells) + f" | {days} |")
    lines += ["", "\"First days\" counts only the first day of a run of discharge days. The CIWQS label means an event "
              "*started* that day; these reports are calendar-day totals, so a discharge across midnight fills two days. "
              "\"All days\" counts every discharge day.", ""]
    for rule in ("every", "first"):
        frames, _ = T.build_dataset(sources=sorted(set(CAND.served_info()["rain_sources"].values())),
                                    record={"labels": "csd_pre2018", "day_rule": rule, "start": "2011-03-01"})
        rows, labels = _rain_table(frames)
        if rule == "every":
            seasons = _season_rows(frames)
        name = "all days" if rule == "every" else "first days"
        lines += [f"**Does an older-report day behave like a CIWQS day? ({name})** The share of days with an overflow, by that "
                  "day's rain on the basin's own gauge. Each cell reads older reports · CIWQS (to the training end).", "",
                  "| basin | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels), *rows, ""]
    lines += ["**Seasons** (July–June): two-gauge rain, days with ½″ or more, and discharge days per basin with all days "
              "counted. A dot marks a season that holds older-report days.", "",
              "| season | rain (in) | days ½″+ | " + " | ".join(T.APP_BASINS) + " |", "|---|" + "---|" * (2 + len(T.APP_BASINS)),
              *seasons, ""]
    lines += ["## How each candidate scores", "",
              "**S2, the overflow model** (rain known): skill against the usual rate for that basin and month, with its 90% "
              "range, then the paired change in Brier score against the live model on the same basin-days (×1000; negative "
              "means the candidate is closer). \"better\" and \"worse\" are used only when the whole range is on one side.", ""]
    for stage1 in RECORDS:
        name = set_name(stage1)
        sc = _scores(name)
        vs = sc["paired"]["vs_served"]
        lines += [f"### {LU_WORDS[stage1]}", "", f"Set `{name}`.", "",
                  "| basin | " + " | ".join(f"{t[1]}: skill (live)" for t in TIERS) + " | "
                  + " | ".join(f"{t[1]}: Δ Brier ×1000" for t in TIERS) + " |",
                  "|---|" + "---|" * (2 * len(TIERS))]
        for key, label in BASIN_ROWS:
            cells = [f"{_skill(sc['s2'][key]['oracle'][t])} ({srv['s2'][key]['oracle'][t]['bss']:.2f})" for t, _ in TIERS]
            cells += [_delta(vs["s2"][key]["oracle"][t]) for t, _ in TIERS]
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        lines += ["", "The public number (OUT), paired against the live set, Δ Brier ×1000:", "",
                  "| zone | " + " | ".join(f"{t[1]}, rain known" for t in TIERS) + " | post-training, 1 day ahead |",
                  "|---|" + "---|" * (len(TIERS) + 1)]
        for key, label in ZONE_ROWS:
            cells = [_delta(vs["out"][key]["rain"][t]) for t, _ in TIERS] + [_delta(vs["out"][key]["L1"]["T1"])]
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        prim = {r["id"]: r for r in sc["primaries"]["rows"]}
        parts = [f"{p['part']} ({p['window']}) {p['status']}" for i in ("S2", "OUT") for p in prim[i].get("parts", [])]
        lines += ["", f"Pre-registered tests (protocol §8): S2 {prim['S2']['status']}, OUT {prim['OUT']['status']}. "
                  f"Their parts: {'; '.join(parts)}.", ""]
    return "\n".join(lines) + "\n"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--save", default=None, help="a stage-1 name from RECORDS, or 'all'")
    ap.add_argument("--only", default=None, help="comma-separated stage-1 names to compare (default: all)")
    ap.add_argument("--report", action="store_true", help=f"write {REPORT.name} from the stage scores")
    a = ap.parse_args(argv)
    if a.report:
        REPORT.write_text(report())
        print(f"→ {REPORT}")
        return
    models = served_models()
    base = fit(None, models)
    for key, rec in base["per_basin"].items():          # the refit reproduces the served set's recorded rows
        sv = CAND.served_info()["per_basin"].get(key)
        if sv and (sv["n_events"], sv["season_cv_pre_holdout"]["n"]) != (rec["n_events"], rec["season_cv_pre_holdout"]["n"]):
            raise SystemExit(f"{key}: the served record refit gives {rec['n_events']} events, the served set recorded {sv['n_events']}")
    names = list(RECORDS) if a.save == "all" or not (a.save or a.only) else (a.save or a.only).split(",")
    for stage1 in names:
        rec = RECORDS[stage1]
        got = fit(rec, models)
        print(f"\n{stage1}  ({rec['day_rule']} days from {rec['start']})")
        for key in KEYS:
            print(_row(key, base["per_basin"][key], got["per_basin"][key]))
        print("  older days used:", json.dumps(record_counts(got["rows"])))
        if a.save:
            save(stage1, rec, got)


if __name__ == "__main__":
    main()
