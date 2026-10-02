#!/usr/bin/env python3
"""Replay — grades the live corrections (live_rules.VERSION) against the plain two-stage model.

For every day of an era with feed-flag data, recompose the beach risk using
only what was known by the end of that day — the served stage-1 probabilities
from the hindcast artifact, the feed's CSO onsets and flag days, the samples
published a day later — through src/models/live_rules exactly as serving does,
then grade plain vs corrected on the three rulers of the Model check
(discharge days + samples, discharge days only, beach postings).

Eras with flag data:
  * 2016-03-19 → 2017-01-10 — John Brandon's Poo Bot snapshots of SFPUC's feed
    (data/poobot/feed_status.csv, discharge_onsets.csv). Stage 1 is IN-SAMPLE
    here (the served models trained on these seasons), so absolute numbers
    flatter every variant equally; the plain-vs-corrected difference is the
    fair reading. CAUTION on the Westside: CIWQS has no Westside per-event
    records before 2018, so the Westside discharge labels of Oct 2016 → Jan
    2017 ARE these feed onsets — the onset rule catches them by construction.
    The bayside zones (North Shore, East), whose labels come from CIWQS, are
    the clean comparison; the report shows both subtotals.
  * 2026-08-20 → … — our watcher's alert_log. Joins the replay once the hindcast
    artifact is rescored past that date (it ends 2026-08-17 at this writing).

Variants: plain (models alone) · <VERSION> (all rules) · no_downgrade (recall 0)
· no_samples · cso_flags_only. Self-check: the plain recomposition must match
the artifact's stored group risks (≤ 0.001) before any grading counts.

Synthetic feed (``--synthetic``): the filed CIWQS discharge days stand in for
the feed's onsets over the OUT-OF-SAMPLE years (holdout-fit probabilities from
Jul 2023, served ones after training), degraded with the archive's measured
imperfections — 13% of onsets never flagged, 60% of the rest appearing a day
late — and BeachWatch's CSO postings as the flag-up windows; several random
draws, mean and range reported. Silence in a synthetic feed is "truly no
discharge" except for the dropped onsets, so the degradation is what makes the
no-flag downgrade's cost visible at all. A perfect-feed run is the upper bound.

    venv/bin/python features/forecast/src/models/replay_live.py [--no-write] [--cutoff=end]
    → reports/2026-09_live_replay.html + .json
    venv/bin/python features/forecast/src/models/replay_live.py --synthetic [--no-write]
    → reports/2026-09_live_replay_synthetic.html + .json
"""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import html
import json
import pickle
import random
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
import live_rules as LR  # noqa: E402
LIVE = LR.VERSION   # the variant named after the rule set's version (live_v2 since 2026-09-29; same arithmetic as live_v1)
import posting_label as PL  # noqa: E402
import train_v4 as T  # noqa: E402
from groups import BASIN_KEYS, GROUPS_BY_BASIN, ZONE_GROUPS, zone_risks  # noqa: E402
from impact import compose as _compose, impact_fraction, smooth_table  # noqa: E402

SPLIT = [None]   # the served stage 2 split, set by load_artifact; every composition here goes through it


def compose(*args, **kwargs):
    kwargs.setdefault("split", SPLIT[0])
    return _compose(*args, **kwargs)
from scorecard import zone_confusion, zone_confusion_combined, zone_confusion_posted  # noqa: E402
from shared.outfalls import FEED_NAME_TO_OUTFALLS, OUTFALLS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

REPO = HERE.parents[3]
SERVE_DIR = HERE.parents[1] / "data" / "models"
POOBOT = HERE.parents[1] / "data" / "poobot"
OUT_HTML = REPO / "reports" / "2026-09_live_replay.html"
OUT_JSON = REPO / "reports" / "2026-09_live_replay.json"
OUT_SYN_HTML = REPO / "reports" / "2026-09_live_replay_synthetic.html"
OUT_SYN_JSON = REPO / "reports" / "2026-09_live_replay_synthetic.json"
SYN_MISS_RATE = 0.13      # 2 of 15 archive discharge days never flagged
SYN_LAG_RATE = 0.60       # 10 of 16 matching archive onsets appeared the day after the filed date
SYN_FALLBACK_FLAG_DAYS = 2  # flag-up days after an onset when BeachWatch has no posting record (median CSO posting)
SYN_SEEDS = (1, 2, 3, 4, 5)
ZONE_ORDER = ["ocean", "baker_china", "north", "east"]
BAYSIDE = ("north", "east")          # labels from CIWQS in every era — the clean comparison
WESTSIDE = ("ocean", "baker_china")  # in the archive era the discharge labels ARE the feed onsets → circular for the onset rule
LINES = (0.25, 0.5)
WFN = 2   # a miss costs two false alarms (Chase)
esc = html.escape
_ZONE_OF_GROUP = {g: zk for zk, gs in ZONE_GROUPS.items() for g in gs}
_BASIN_OF_GROUP = {g: bk for bk, gs in GROUPS_BY_BASIN.items() for g in gs}


def variants() -> dict:
    """{name: rules} — the rule sets to replay."""
    full = copy.deepcopy(LR.RULES)
    nodg = copy.deepcopy(full)
    nodg["downgrade"]["recall_by_quiet_days"] = {b: [0.0] * 3 for b in nodg["downgrade"]["recall_by_quiet_days"]}
    allf = copy.deepcopy(full)   # the counterfactual: every empirical floor applied, including those under 0.5 (live_v1 before 2026-09-27)
    for key in ("floor_elevated_tail", "floor_elevated_dry"):
        allf["samples"][key] = dict(LR.SAMPLE_RATES[key])
    return {LIVE: full, "no_downgrade": nodg, "no_samples": full, "cso_flags_only": nodg, "all_floors": allf}


NO_SAMPLES = {"no_samples", "cso_flags_only"}


# ── inputs ─────────────────────────────────────────────────────────────────

def archive_flags() -> tuple[dict, dict, tuple]:
    """(onsets {date: {basin_key}}, flags {date: {basin_key}}, (era_start, era_end)) from the Poo Bot archive."""
    fs = pd.read_csv(POOBOT / "feed_status.csv")
    fs["day"] = pd.to_datetime(fs["snapshot"]).dt.normalize()
    flags: dict = {}
    for _, r in fs[fs["cso_structures"].notna()].iterrows():
        for nm in str(r["cso_structures"]).split("|"):
            key = nm.strip()
            for oid in FEED_NAME_TO_OUTFALLS.get(key.upper(), FEED_NAME_TO_OUTFALLS.get(key, [])):
                o = OUTFALLS.get(oid)
                if o:
                    flags.setdefault(r["day"].date(), set()).add(BASIN_KEYS[o.basin])
    on = pd.read_csv(POOBOT / "discharge_onsets.csv")
    onsets: dict = {}
    for _, r in on.iterrows():
        if r.get("basin") in BASIN_KEYS:
            onsets.setdefault(pd.Timestamp(r["date"]).date(), set()).add(BASIN_KEYS[r["basin"]])
    era = (fs["day"].min().date(), fs["day"].max().date())
    return onsets, flags, era


def load_artifact() -> tuple[dict, list, dict]:
    with gzip.open(SERVE_DIR / "scorecard.json.gz", "rt") as f:
        sc = json.load(f)
    s2_path = SERVE_DIR / "stage2.json"                      # the served stage 2 spec (promote.py); None = v1
    spec = json.loads(s2_path.read_text()) if s2_path.exists() else None
    table = smooth_table(spec["impact_table"]) if spec and spec.get("impact_table") else smooth_table(json.loads((SERVE_DIR / "impact_table.json").read_text()))
    import stage2 as _s2
    SPLIT[0] = _s2.make_split(spec)
    return sc, sc["days"], table


def predicted_volumes(days: list, sc_meta: dict | None = None) -> dict:
    """{date: {basin_key: MG}} — the volume heads on the training frames, as build_scorecard used them
    (rule-treated frames for post-training days, as the rescoring did)."""
    heads = {}
    for basin in T.APP_BASINS:
        path = SERVE_DIR / f"{BASIN_KEYS[basin]}_volume.pkl"
        if path.exists():
            with open(path, "rb") as f:
                heads[basin] = pickle.load(f)
    sources = sorted({h.get("rain_source", "avg") for h in heads.values()} | {"avg"})
    end = pd.Timestamp(days[-1]["date"])
    frames = T.build_dataset(end=end, sources=sources)[0]
    rules_post = sc_meta.get("input_rules_post") if sc_meta else None
    trained_through = (sc_meta or {}).get("trained_through")
    frames_post = T.build_dataset(end=end, sources=sources, input_rules=rules_post)[0] if rules_post else frames
    out: dict = {}
    for basin, h in heads.items():
        src = h.get("rain_source", "avg")
        for X in (frames[src],) if frames_post is frames else (frames[src], frames_post[src]):
            pass
        v_plain = T.predicted_volume(h, frames[src])
        v_post = T.predicted_volume(h, frames_post[src]) if frames_post is not frames else v_plain
        for (d, a), b in zip(zip(frames[src]["date"], v_plain), v_post):
            ds = str(pd.Timestamp(d).date())
            use_post = trained_through is not None and ds > trained_through
            out.setdefault(ds, {})[BASIN_KEYS[basin]] = float(b if use_post else a)
    return out


# ── the replay ─────────────────────────────────────────────────────────────

def replay(days: list, table: dict, vol_pred: dict, onsets: dict, flags: dict, era: tuple, rules: dict,
           use_samples: bool, watcher_ok: bool = True, cutoff: str = "start") -> dict:
    """{date: {group: risk}} for every era day, composed day by day with what was
    known then. ``cutoff="start"`` (the graded forecast) uses only observations
    through the END OF THE DAY BEFORE — the forecast a visitor saw that morning,
    so a discharge day is never credited for its own flag and the corrections
    are judged on the tail they shape; ``cutoff="end"`` includes the day's own
    flags and samples (what the page shows by evening)."""
    by_date = {d["date"]: d for d in days}
    dates_all = [dt.date.fromisoformat(d["date"]) for d in days]
    idx_of = {d: i for i, d in enumerate(dates_all)}
    samples_all = {}
    if use_samples:
        for d in days:
            dd = dt.date.fromisoformat(d["date"])
            for g, gv in d["groups"].items():
                if gv.get("elevated") is not None:
                    samples_all[(g, dd)] = bool(gv["elevated"])
    large = lambda g, k: impact_fraction(table, g, k, rules["cso"]["large_volume_mg"])  # noqa: E731
    out: dict = {}
    era_start, era_end = era
    for D in dates_all:
        if D < era_start or D > era_end:
            continue
        i = idx_of[D]
        lo = max(0, i - 8)
        win = dates_all[lo:i + 1]
        probs = [{bk: _p_used(by_date[str(d)]["basins"][bk]) for bk in GROUPS_BY_BASIN} for d in win]
        vols = [{bk: float(vol_pred.get(str(d), {}).get(bk, 0.0)) for bk in GROUPS_BY_BASIN} for d in win]
        rain = {d: float(by_date[str(d)].get("rain", 0.0) or 0.0) for d in win}
        known = D if cutoff == "end" else D - dt.timedelta(days=1)
        on_k = {d: s for d, s in onsets.items() if d <= known}
        fl_k = {d: s for d, s in flags.items() if d <= known}
        p2, v2, _ = LR.adjust_stage1(probs, vols, win, on_k, fl_k, rain, D, rules=rules, watcher_from=era_start, watcher_ok=watcher_ok)
        j = len(win) - 1
        _, groups = compose(table, GROUPS_BY_BASIN, p2, v2, j, win, {})
        p_only = [dict(p) for p in p2]
        p_only[j] = {b: 0.0 for b in p_only[j]}
        _, persist = compose(table, GROUPS_BY_BASIN, p_only, v2, j, win, {})
        smp = {k: v for k, v in samples_all.items() if k[1] <= known} if use_samples else {}
        probs_by_date = {d: p2[k] for k, d in enumerate(win)}
        adjusted, _ = LR.adjust_groups(groups, persist, p2[j], D, smp, probs_by_date, on_k, fl_k, _ZONE_OF_GROUP, _BASIN_OF_GROUP, large, rules=rules)
        out[str(D)] = adjusted
    return out


def plain_recomposition(days: list, table: dict, vol_pred: dict, era: tuple) -> tuple[dict, float]:
    """The models alone, recomposed from the artifact's p and the heads' volumes; returns ({date: {group: risk}}, worst |Δ| vs the stored risks)."""
    dates_all = [dt.date.fromisoformat(d["date"]) for d in days]
    probs = [{bk: _p_used(d["basins"][bk]) for bk in GROUPS_BY_BASIN} for d in days]
    vols = [{bk: float(vol_pred.get(d["date"], {}).get(bk, 0.0)) for bk in GROUPS_BY_BASIN} for d in days]
    out, worst = {}, 0.0
    for i, d in enumerate(days):
        D = dates_all[i]
        if D < era[0] or D > era[1]:
            continue
        _, groups = compose(table, GROUPS_BY_BASIN, probs, vols, i, dates_all, {})
        out[d["date"]] = groups
        for g, v in groups.items():
            gv = d["groups"][g]
            stored = gv["risk_h"] if gv.get("risk_h") is not None else gv["risk"]   # holdout-fit where the artifact has it
            if stored is not None:
                worst = max(worst, abs(float(v) - float(stored)))
    return out, worst


def _p_used(b: dict) -> float:
    """The probability the graders score: holdout-fit where it exists, else the served model's."""
    return float(b["ph"] if b.get("ph") is not None else b["p"])


def per_day_risks(onsets: dict, flags: dict, era: tuple, variant: str = LIVE, cutoff: str = "start") -> dict:
    """{date: {"groups": {group: risk}, "zones": {zone: risk}}} — the per-day risks
    the replay already computes for one variant on one feed, returned instead of
    graded: ``replay`` for a rule set of ``variants()``, ``plain_recomposition``
    for 'plain'. Same artifact, inputs and arguments as ``main`` and
    ``run_synthetic`` (``onsets`` / ``flags`` / ``era`` as ``archive_flags`` or
    ``synthetic_feed`` return them). Additive (STAGES_DESIGN.md §7 step 6):
    stages_s5 checks its basin_swap against these day by day; main(),
    run_synthetic() and their JSON are unchanged."""
    sc, days, table = load_artifact()
    vol_pred = predicted_volumes(days, sc)
    if variant == "plain":
        risks = plain_recomposition(days, table, vol_pred, era)[0]
    else:
        rule_sets = variants()
        if variant not in rule_sets:
            raise KeyError(f"unknown replay variant {variant!r}; known: plain, {', '.join(rule_sets)}")
        risks = replay(days, table, vol_pred, onsets, flags, era, rule_sets[variant],
                       use_samples=variant not in NO_SAMPLES, cutoff=cutoff)
    return {d: {"groups": dict(g), "zones": zone_risks(g)} for d, g in risks.items()}


def graded_days(days: list, risks: dict) -> list:
    """The artifact's era days with each zone's risk replaced by the variant's composition (labels untouched)."""
    out = []
    for d in days:
        if d["date"] not in risks:
            continue
        dd = copy.deepcopy(d)
        zr = zone_risks(risks[d["date"]])
        for zk in dd["zones"]:
            dd["zones"][zk]["risk"] = zr.get(zk, 0.0)
            dd["zones"][zk]["risk_h"] = None
        out.append(dd)
    return out


def grade(days_v: list, label) -> dict:
    conf_c = zone_confusion_combined(days_v, ZONE_ORDER, holdout_only=False, thresholds=LINES)
    conf_d = zone_confusion(days_v, ZONE_ORDER, holdout_only=False, thresholds=LINES)
    conf_p = zone_confusion_posted(days_v, ZONE_ORDER, label, holdout_only=False, thresholds=LINES) if label is not None else None
    out = {}
    for thr in LINES:
        t = str(thr)
        c = {k: sum(conf_c[z][t][k] for z in ZONE_ORDER) for k in ("tp", "fn", "fp")}
        d = {k: sum(conf_d[z][t]["vs_discharge_posting"][k] for z in ZONE_ORDER) for k in ("tp", "fn", "fp")}
        def sub(zones_c: dict, keys) -> dict:
            s_ = {k: sum(zones_c[z][k] for z in keys) for k in ("tp", "fn", "fp")}
            return {**s_, "cost": s_["fp"] + WFN * s_["fn"]}
        zc = {z: conf_c[z][t] for z in ZONE_ORDER}
        zd = {z: conf_d[z][t]["vs_discharge_posting"] for z in ZONE_ORDER}
        row = {"combined": {**c, "cost": c["fp"] + WFN * c["fn"], "per_zone": {z: zc[z]["fp"] + WFN * zc[z]["fn"] for z in ZONE_ORDER},
                            "zones": zc, "bayside": sub(zc, BAYSIDE), "westside": sub(zc, WESTSIDE)},
               "discharge": {**d, "cost": d["fp"] + WFN * d["fn"], "zones": zd, "bayside": sub(zd, BAYSIDE), "westside": sub(zd, WESTSIDE)}}
        if conf_p is not None:
            zp = {z: conf_p[z][t] for z in ZONE_ORDER}
            p = {k: sum(zp[z][k] for z in ZONE_ORDER) for k in ("tp", "fn", "fp")}
            row["posted"] = {**p, "cost": p["fp"] + WFN * p["fn"], "zones": zp, "bayside": sub(zp, BAYSIDE), "westside": sub(zp, WESTSIDE)}
        out[t] = row
    return out


def main(write: bool = True, cutoff: str = "start") -> dict:
    sc, days, table = load_artifact()
    onsets, flags, era = archive_flags()
    label = PL.from_beachwatch()
    vol_pred = predicted_volumes(days, sc)
    plain, worst = plain_recomposition(days, table, vol_pred, era)
    results = {"era": [str(era[0]), str(era[1])], "cutoff": cutoff, "self_check_worst_delta": round(worst, 4),
               "flag_days": sum(len(v) for v in flags.values()), "onset_days": sum(len(v) for v in onsets.values()),
               "onset_basins": sorted({b for v in onsets.values() for b in v}), "rules": LR.RULES, "variants": {}}
    risks = {"plain": plain}
    for name, rules in variants().items():
        risks[name] = replay(days, table, vol_pred, onsets, flags, era, rules, use_samples=name not in NO_SAMPLES, cutoff=cutoff)
    for name, r in risks.items():
        dv = graded_days(days, r)
        g = grade(dv, label)
        changed = {z: sum(1 for d in dv if abs(zone_risks(r[d["date"]]).get(z, 0) - zone_risks(plain[d["date"]]).get(z, 0)) >= 0.05) for z in ZONE_ORDER} if name != "plain" else {z: 0 for z in ZONE_ORDER}
        results["variants"][name] = {"grades": g, "days_changed": changed, "n_days": len(dv)}
    print(f"cutoff = {cutoff} of day ({'observations through the day before — the start-of-day forecast' if cutoff == 'start' else 'the day’s own flags and samples included'})")
    print(f"era {results['era'][0]} → {results['era'][1]}: {results['variants']['plain']['n_days']} days, {results['flag_days']} flag basin-days, {results['onset_days']} onset basin-days ({', '.join(results['onset_basins'])}); self-check worst Δ {worst:.4f}")
    for thr in LINES:
        t = str(thr)
        print(f"-- {int(thr*100)}% line: variant | discharge+samples caught/bad, FP, cost | discharge days caught, FP, cost | postings caught, FP, cost | days changed per zone")
        for name, v in results["variants"].items():
            g = v["grades"][t]
            c, d, p = g["combined"], g["discharge"], g.get("posted")
            b = c["bayside"]
            print(f"   {name:15} {c['tp']}/{c['tp']+c['fn']}, FP {c['fp']}, cost {c['cost']} (bayside only: {b['tp']}/{b['tp']+b['fn']}, FP {b['fp']}, cost {b['cost']}) | {d['tp']}/{d['tp']+d['fn']}, FP {d['fp']}, cost {d['cost']} | " + (f"{p['tp']}/{p['tp']+p['fn']}, FP {p['fp']}, cost {p['cost']}" if p else "—") + f" | {v['days_changed']}")
    if write:
        OUT_JSON.write_text(json.dumps(results, indent=1, default=str))
        OUT_HTML.write_text(render_html(results))
        print(f"wrote {OUT_HTML.relative_to(REPO)} and {OUT_JSON.relative_to(REPO)}")
    return results


def render_html(res: dict) -> str:
    css = (REPO / "features/forecast/src/models/stage2_explorer_template.html").read_text().split("<style>")[1].split("</style>")[0]
    v = res["variants"]
    names = list(v)

    def table(ruler: str, thr: str) -> str:
        h = [f'<table><tr><th>Variant</th><th class="num">caught</th><th class="num">missed</th><th class="num">false alarms</th><th class="num">cost (miss = {WFN} FA)</th><th class="num">bayside only: caught · FP · cost</th><th class="num">Westside only (circular): caught · FP · cost</th>' + ("".join(f'<th class="num">{esc(ZONES[z].label.split(" ")[0])} cost</th>' for z in ZONE_ORDER) if ruler == "combined" else "") + '<th class="num">days changed vs plain</th></tr>']
        rows = sorted(names, key=lambda n: v[n]["grades"][thr][ruler]["cost"] if ruler in v[n]["grades"][thr] else 1e9)
        for n in rows:
            g = v[n]["grades"][thr].get(ruler)
            if not g:
                continue
            bs, ws = g["bayside"], g["westside"]
            h.append(f'<tr class="{"best" if n == rows[0] else ""}"><td><b>{esc(n)}</b></td><td class="num">{g["tp"]} of {g["tp"]+g["fn"]}</td><td class="num">{g["fn"]}</td><td class="num">{g["fp"]}</td><td class="num"><b>{g["cost"]}</b></td><td class="num">{bs["tp"]} of {bs["tp"]+bs["fn"]} · {bs["fp"]} · <b>{bs["cost"]}</b></td><td class="num fine">{ws["tp"]} of {ws["tp"]+ws["fn"]} · {ws["fp"]} · {ws["cost"]}</td>'
                     + ("".join(f'<td class="num">{g["per_zone"][z]}</td>' for z in ZONE_ORDER) if ruler == "combined" else "")
                     + f'<td class="num">{sum(v[n]["days_changed"].values())}</td></tr>')
        h.append('</table>')
        return "".join(h)

    sections = []
    for thr in ("0.5", "0.25"):
        sections.append(f'<h2>{int(float(thr)*100)}% line</h2><h3>Discharge days + samples (primary ruler)</h3>{table("combined", thr)}<h3>Discharge days only</h3>{table("discharge", thr)}<h3>Beach postings</h3>{table("posted", thr)}')
    zone_rows = [f'<table><tr><th>Zone (50%, primary ruler)</th><th>plain</th><th>{LIVE}</th></tr>']
    for z in ZONE_ORDER:
        a, b = v["plain"]["grades"]["0.5"]["combined"]["zones"][z], v[LIVE]["grades"]["0.5"]["combined"]["zones"][z]
        f = lambda c: f'{c["tp"]} of {c["tp"]+c["fn"]} caught · {c["fp"]} false (clean-sample {c["fp_sample"]}, quiet {c["fp_quiet"]}) · cost {c["fp"] + WFN * c["fn"]}'  # noqa: E731
        zone_rows.append(f'<tr><td>{esc(ZONES[z].label)}</td><td>{f(a)}</td><td>{f(b)}</td></tr>')
    zone_rows.append('</table>')
    r = res["rules"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live corrections replay — {LIVE} vs the model alone</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css} tr.best td{{background:#e0f0ea}}</style></head><body><div class="wrap">
<header><h1>Live corrections replay: {LIVE} vs the model alone</h1>
<p class="sub">Every day of the era recomposed with only what was known by the <b>end of the day before</b> — the start-of-day forecast — the served stage-1 probabilities, the feed's CSO onsets and flag days, samples published a day later — through <code>src/models/live_rules.py</code> exactly as serving does, then graded on the Model check's three rulers. A discharge day is never credited for its own flag; the corrections are judged on the tail they shape. Cost = false alarms + {WFN} × misses.</p>
<div class="meta"><span>era {esc(res["era"][0])} → {esc(res["era"][1])} ({v["plain"]["n_days"]} days)</span><span>{res["flag_days"]} flag basin-days · {res["onset_days"]} onset basin-days ({esc(", ".join(res["onset_basins"]))})</span><span>self-check: plain recomposition vs stored risks, worst Δ {res["self_check_worst_delta"]}</span><span>downgrade recall by quiet days {esc(json.dumps(r["downgrade"]["recall_by_quiet_days"]))}</span></div></header>
<section><h2>Read this first</h2>
<p class="lead">The only era with feed-flag data today is the 2016-17 Poo Bot archive. Its stage-1 probabilities are <b>in-sample</b> (the served models trained on these seasons), so every variant is flattered by the same amount — read the difference between rows, not the rows. <b>Read the bayside subtotal as the clean comparison:</b> North Shore and East labels come from CIWQS, independent of the feed. The Westside has no CIWQS per-event records before 2018, so its discharge labels in this era <i>are</i> the archive's feed onsets — the onset rule catches them by construction and that column is marked circular (the sample part of the primary ruler is still independent there). Snapshots came twice a day, so a flag that came and went between them is missed. The watcher era (Aug 2026 →) joins this page once the hindcast artifact is rescored past it.</p>
<p class="lead">Variants: <b>plain</b> = the two-stage model alone · <b>{LIVE}</b> = every rule · <b>no_downgrade</b> = {LIVE} with the no-flag downgrade off · <b>no_samples</b> = {LIVE} without the sample floors and caps · <b>cso_flags_only</b> = onset, anchor, large volume and flag hold only.</p></section>
<section>{"".join(sections)}</section>
<section><h2>Per zone, 50% line, primary ruler</h2>{"".join(zone_rows)}</section>
<footer>Generated by <code>features/forecast/src/models/replay_live.py</code>. Design: <code>features/forecast/LIVE_COMPOSITION_DESIGN.md</code>. <a href="/forecast">Back to the forecast</a> · <a href="/reports/2026-09_model_analysis.html">Model analysis</a></footer>
</div></body></html>"""


# ── synthetic feed over the out-of-sample years ─────────────────────────────

_ZONES_OF_BASIN = {bk: sorted({_ZONE_OF_GROUP[g] for g in gs}) for bk, gs in GROUPS_BY_BASIN.items()}


def synthetic_feed(days: list, label, era: tuple, seed: int | None, miss_rate: float = SYN_MISS_RATE,
                   lag_rate: float = SYN_LAG_RATE, fallback_days: int = SYN_FALLBACK_FLAG_DAYS) -> tuple[dict, dict, dict]:
    """(onsets, flags, stats) — a feed built from the filed record: every basin
    discharge day (CIWQS) becomes an onset, dropped with ``miss_rate`` and shown
    a day late with ``lag_rate`` (seed None = the perfect feed); the flag stays
    up on the days BeachWatch shows a CSO-cause posting for one of the basin's
    zones, else for ``fallback_days`` after the onset."""
    rng = random.Random(seed) if seed is not None else None
    onsets: dict = {}
    flags: dict = {}
    stats = {"true_onset_days": 0, "dropped": 0, "lagged": 0}
    for d in days:
        D = dt.date.fromisoformat(d["date"])
        if D < era[0] or D > era[1]:
            continue
        for bk, b in d["basins"].items():
            if not b.get("y"):
                continue
            stats["true_onset_days"] += 1
            if rng is not None and rng.random() < miss_rate:
                stats["dropped"] += 1
                continue
            shown = D
            if rng is not None and rng.random() < lag_rate:
                shown = D + dt.timedelta(days=1)
                stats["lagged"] += 1
            onsets.setdefault(shown, set()).add(bk)
            posted_days = []
            if label is not None:
                for k in range(0, 10):
                    dk = D + dt.timedelta(days=k)
                    cls = [label.cls(z, str(dk)) for z in _ZONES_OF_BASIN.get(bk, [])]
                    if any(c == "cso" for c in cls):
                        posted_days.append(dk)
                    elif k > 0 and all(c == PL.UNKNOWN for c in cls):
                        posted_days = None
                        break
            if posted_days is None or not posted_days:   # record unknown or no posting found → the median CSO posting
                posted_days = [D + dt.timedelta(days=k) for k in range(0, fallback_days + 1)]
            for dk in posted_days:
                if dk >= shown:
                    flags.setdefault(dk, set()).add(bk)
    return onsets, flags, stats


def _mean_grade(grades: list) -> dict:
    """Mean (and min–max range) of per-seed grade dicts, same shape as one grade."""
    import statistics
    out: dict = {}
    for t in grades[0]:
        out[t] = {}
        for ruler in grades[0][t]:
            keys = [k for k, v in grades[0][t][ruler].items() if isinstance(v, (int, float))]
            agg = {k: statistics.mean(g[t][ruler][k] for g in grades) for k in keys}
            agg["range"] = {k: [min(g[t][ruler][k] for g in grades), max(g[t][ruler][k] for g in grades)] for k in ("tp", "fp", "cost")}
            for sub in ("bayside", "westside"):
                if sub in grades[0][t][ruler]:
                    agg[sub] = {k: statistics.mean(g[t][ruler][sub][k] for g in grades) for k in ("tp", "fn", "fp", "cost")}
            if "zones" in grades[0][t][ruler]:
                agg["zones"] = {z: {k: statistics.mean(g[t][ruler]["zones"][z][k] for g in grades) for k in grades[0][t][ruler]["zones"][z]} for z in ZONE_ORDER}
            out[t][ruler] = agg
    return out


def run_synthetic(write: bool = True, seeds=SYN_SEEDS) -> dict:
    sc, days, table = load_artifact()
    label = PL.from_beachwatch()
    era = (dt.date.fromisoformat(sc["holdout_start"]), dt.date.fromisoformat(sc["span"][1]))
    vol_pred = predicted_volumes(days, sc)
    plain, worst = plain_recomposition(days, table, vol_pred, era)
    res = {"era": [str(era[0]), str(era[1])], "cutoff": "start", "self_check_worst_delta": round(worst, 4),
           "feed": {"miss_rate": SYN_MISS_RATE, "lag_rate": SYN_LAG_RATE, "fallback_flag_days": SYN_FALLBACK_FLAG_DAYS, "seeds": list(seeds)},
           "rules": LR.RULES, "variants": {}}
    dv_plain = graded_days(days, plain)
    res["variants"]["plain"] = {"grades": grade(dv_plain, label), "n_days": len(dv_plain), "days_changed": {z: 0 for z in ZONE_ORDER}}
    # the perfect feed: an upper bound
    on_p, fl_p, st_p = synthetic_feed(days, label, era, None)
    res["feed"]["true_onset_days"] = st_p["true_onset_days"]
    r_perf = replay(days, table, vol_pred, on_p, fl_p, era, LR.RULES, use_samples=True, cutoff="start")
    dv = graded_days(days, r_perf)
    res["variants"][f"{LIVE}_perfect_feed"] = {"grades": grade(dv, label), "n_days": len(dv),
                                                "days_changed": {z: sum(1 for d in dv if abs(zone_risks(r_perf[d["date"]]).get(z, 0) - zone_risks(plain[d["date"]]).get(z, 0)) >= 0.05) for z in ZONE_ORDER}}
    # the degraded feed: several draws
    per_seed: dict = {name: [] for name in variants()}
    changed: dict = {name: [] for name in variants()}
    feed_stats = []
    for seed in seeds:
        on_s, fl_s, st = synthetic_feed(days, label, era, seed)
        feed_stats.append(st)
        for name, rules in variants().items():
            r = replay(days, table, vol_pred, on_s, fl_s, era, rules, use_samples=name not in NO_SAMPLES, cutoff="start")
            dv = graded_days(days, r)
            per_seed[name].append(grade(dv, label))
            changed[name].append({z: sum(1 for d in dv if abs(zone_risks(r[d["date"]]).get(z, 0) - zone_risks(plain[d["date"]]).get(z, 0)) >= 0.05) for z in ZONE_ORDER})
    res["feed"]["draws"] = feed_stats
    for name in variants():
        res["variants"][name] = {"grades": _mean_grade(per_seed[name]), "n_days": len(dv_plain), "seeds": len(seeds),
                                 "days_changed": {z: round(sum(c[z] for c in changed[name]) / len(seeds), 1) for z in ZONE_ORDER}}
    print(f"synthetic feed, era {res['era'][0]} → {res['era'][1]} ({len(dv_plain)} days, {st_p['true_onset_days']} true onset basin-days; per draw ≈ {sum(s['dropped'] for s in feed_stats)/len(seeds):.0f} dropped, {sum(s['lagged'] for s in feed_stats)/len(seeds):.0f} lagged); self-check worst Δ {worst:.4f}")
    for thr in LINES:
        t = str(thr)
        print(f"-- {int(thr*100)}% line: variant | discharge+samples caught/bad, FP, cost (bayside) | discharge days | postings")
        for name, v in res["variants"].items():
            g = v["grades"][t]
            c, d, p = g["combined"], g["discharge"], g.get("posted")
            b = c["bayside"]
            print(f"   {name:21} {c['tp']:.1f}/{c['tp']+c['fn']:.0f}, FP {c['fp']:.1f}, cost {c['cost']:.1f} (bayside {b['tp']:.1f}/{b['tp']+b['fn']:.0f}, FP {b['fp']:.1f}, cost {b['cost']:.1f}) | {d['tp']:.1f}/{d['tp']+d['fn']:.0f}, FP {d['fp']:.1f}, cost {d['cost']:.1f} | " + (f"{p['tp']:.1f}/{p['tp']+p['fn']:.0f}, FP {p['fp']:.1f}, cost {p['cost']:.1f}" if p else "—"))
    if write:
        OUT_SYN_JSON.write_text(json.dumps(res, indent=1, default=str))
        OUT_SYN_HTML.write_text(render_synthetic_html(res))
        print(f"wrote {OUT_SYN_HTML.relative_to(REPO)} and {OUT_SYN_JSON.relative_to(REPO)}")
    return res


def render_synthetic_html(res: dict) -> str:
    css = (REPO / "features/forecast/src/models/stage2_explorer_template.html").read_text().split("<style>")[1].split("</style>")[0]
    v = res["variants"]
    names = list(v)
    f1 = lambda x: f"{x:.0f}" if abs(x - round(x)) < 1e-9 else f"{x:.1f}"  # noqa: E731

    def rng(g, k):
        r = g.get("range", {}).get(k)
        return f' <span class="fine">({f1(r[0])}–{f1(r[1])})</span>' if r and r[0] != r[1] else ""

    def table(ruler: str, thr: str) -> str:
        h = [f'<table><tr><th>Variant</th><th class="num">caught</th><th class="num">missed</th><th class="num">false alarms</th><th class="num">cost (miss = {WFN} FA)</th><th class="num">bayside: caught · FP · cost</th><th class="num">Westside: caught · FP · cost</th><th class="num">days changed vs plain</th></tr>']
        rows = sorted(names, key=lambda n: v[n]["grades"][thr][ruler]["cost"] if ruler in v[n]["grades"][thr] else 1e9)
        for n in rows:
            g = v[n]["grades"][thr].get(ruler)
            if not g:
                continue
            bs, ws = g["bayside"], g["westside"]
            h.append(f'<tr class="{"best" if n == rows[0] else ""}"><td><b>{esc(n)}</b>{" <span class=\"fine\">mean of " + str(v[n].get("seeds")) + " draws</span>" if v[n].get("seeds") else ""}</td><td class="num">{f1(g["tp"])} of {f1(g["tp"]+g["fn"])}{rng(g, "tp")}</td><td class="num">{f1(g["fn"])}</td><td class="num">{f1(g["fp"])}{rng(g, "fp")}</td><td class="num"><b>{f1(g["cost"])}</b>{rng(g, "cost")}</td><td class="num">{f1(bs["tp"])} of {f1(bs["tp"]+bs["fn"])} · {f1(bs["fp"])} · <b>{f1(bs["cost"])}</b></td><td class="num">{f1(ws["tp"])} of {f1(ws["tp"]+ws["fn"])} · {f1(ws["fp"])} · {f1(ws["cost"])}</td><td class="num">{f1(sum(v[n]["days_changed"].values()))}</td></tr>')
        h.append('</table>')
        return "".join(h)

    sections = []
    for thr in ("0.5", "0.25"):
        sections.append(f'<h2>{int(float(thr)*100)}% line</h2><h3>Discharge days + samples (primary ruler)</h3>{table("combined", thr)}<h3>Discharge days only</h3>{table("discharge", thr)}<h3>Beach postings (through Feb 2026)</h3>{table("posted", thr)}')
    zone_rows = [f'<table><tr><th>Zone (50%, primary ruler)</th><th>plain</th><th>{LIVE} (mean of draws)</th><th>no_downgrade (mean)</th></tr>']
    ff = lambda c: f'{f1(c["tp"])} of {f1(c["tp"]+c["fn"])} caught · {f1(c["fp"])} false (clean-sample {f1(c["fp_sample"])}, quiet {f1(c["fp_quiet"])}) · cost {f1(c["fp"] + WFN * c["fn"])}'  # noqa: E731
    for z in ZONE_ORDER:
        zone_rows.append(f'<tr><td>{esc(ZONES[z].label)}</td><td>{ff(v["plain"]["grades"]["0.5"]["combined"]["zones"][z])}</td><td>{ff(v[LIVE]["grades"]["0.5"]["combined"]["zones"][z])}</td><td>{ff(v["no_downgrade"]["grades"]["0.5"]["combined"]["zones"][z])}</td></tr>')
    zone_rows.append('</table>')
    fd = res["feed"]
    draws = fd.get("draws", [])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live corrections replay — synthetic feed, out of sample</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css} tr.best td{{background:#e0f0ea}}</style></head><body><div class="wrap">
<header><h1>Live corrections replay: synthetic feed, out of sample</h1>
<p class="sub">The filed record standing in for the feed over every out-of-sample day: each CIWQS discharge day becomes an onset, <b>{fd["miss_rate"]:.0%} are never flagged and {fd["lag_rate"]:.0%} of the rest appear a day late</b> (the 2016-17 archive's measured imperfections), and the flag stays up on the days BeachWatch shows a CSO posting for the basin's beaches (else {fd["fallback_flag_days"]} days). Every day is recomposed with what was known by the <b>end of the day before</b> — the start-of-day forecast — through <code>src/models/live_rules.py</code>, then graded on the three rulers. Degraded variants are the mean of {len(fd["seeds"])} random draws (range in grey). Cost = false alarms + {WFN} × misses.</p>
<div class="meta"><span>era {esc(res["era"][0])} → {esc(res["era"][1])} ({v["plain"]["n_days"]} days; holdout-fit probabilities to Oct 2025, served ones after)</span><span>{fd.get("true_onset_days")} true onset basin-days; per draw ≈ {sum(s["dropped"] for s in draws)/max(len(draws),1):.0f} dropped, {sum(s["lagged"] for s in draws)/max(len(draws),1):.0f} lagged</span><span>self-check: plain recomposition vs stored risks, worst Δ {res["self_check_worst_delta"]}</span><span>downgrade R by quiet days {esc(json.dumps(res["rules"]["downgrade"]["recall_by_quiet_days"]["southeast"]))} (bayside), Westside off</span></div></header>
<section><h2>Read this first</h2>
<p class="lead">This is the test the 2016-17 archive is too small for: {v["plain"]["n_days"]} out-of-sample days with an honest stage 1. Its price is that the feed is synthetic. Silence here means "truly no discharge" except for the dropped onsets, so the <b>degradation is the only thing that makes the no-flag downgrade's cost visible</b>; read <i>{LIVE}_perfect_feed</i> as an upper bound and the degraded mean as the estimate. A discharge day is never credited for its own flag (start-of-day grading), so the discharge-day ruler is fair here, but multi-day events do let a flagged first day lift the second — as a real feed would.</p>
<p class="lead">Variants: <b>plain</b> = the two-stage model alone · <b>{LIVE}</b> = every rule, degraded feed · <b>{LIVE}_perfect_feed</b> = every rule, nothing dropped or late · <b>no_downgrade</b> · <b>no_samples</b> · <b>cso_flags_only</b> · <b>all_floors</b> = the counterfactual with every empirical sample floor applied, including those under 50% (what {LIVE} did before 2026-09-27; today only the East's 0.80 tail floor is a floor, and the clean-sample caps stay).</p></section>
<section>{"".join(sections)}</section>
<section><h2>Per zone, 50% line, primary ruler</h2>{"".join(zone_rows)}</section>
<footer>Generated by <code>features/forecast/src/models/replay_live.py --synthetic</code>. The real-feed replay over 2016-17: <a href="/reports/2026-09_live_replay.html">live replay</a>. Design: <code>features/forecast/LIVE_COMPOSITION_DESIGN.md</code>. <a href="/forecast">Back to the forecast</a></footer>
</div></body></html>"""


if __name__ == "__main__":
    if "--synthetic" in sys.argv:
        run_synthetic(write="--no-write" not in sys.argv)
    else:
        main(write="--no-write" not in sys.argv, cutoff="end" if "--cutoff=end" in sys.argv else "start")
