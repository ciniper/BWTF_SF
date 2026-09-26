#!/usr/bin/env python3
"""Replay — grades live corrections (live_v1) against the plain two-stage model.

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

Variants: plain (models alone) · live_v1 (all rules) · no_downgrade (recall 0)
· no_samples · cso_flags_only. Self-check: the plain recomposition must match
the artifact's stored group risks (≤ 0.001) before any grading counts.

    venv/bin/python features/forecast/src/models/replay_live.py [--no-write]
    → reports/2026-09_live_replay.html + .json
"""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import html
import json
import pickle
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
import live_rules as LR  # noqa: E402
import posting_label as PL  # noqa: E402
import train_v4 as T  # noqa: E402
from groups import BASIN_KEYS, GROUPS_BY_BASIN, ZONE_GROUPS, zone_risks  # noqa: E402
from impact import compose, impact_fraction, smooth_table  # noqa: E402
from scorecard import zone_confusion, zone_confusion_combined, zone_confusion_posted  # noqa: E402
from shared.outfalls import FEED_NAME_TO_OUTFALLS, OUTFALLS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

REPO = HERE.parents[3]
SERVE_DIR = HERE.parents[1] / "data" / "models"
POOBOT = HERE.parents[1] / "data" / "poobot"
OUT_HTML = REPO / "reports" / "2026-09_live_replay.html"
OUT_JSON = REPO / "reports" / "2026-09_live_replay.json"
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
    return {"live_v1": full, "no_downgrade": nodg, "no_samples": full, "cso_flags_only": nodg}


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
    table = smooth_table(json.loads((SERVE_DIR / "impact_table.json").read_text()))
    return sc, sc["days"], table


def predicted_volumes(days: list) -> dict:
    """{date: {basin_key: MG}} — the volume heads on the training frames, as build_scorecard used them."""
    heads = {}
    for basin in T.APP_BASINS:
        path = SERVE_DIR / f"{BASIN_KEYS[basin]}_volume.pkl"
        if path.exists():
            with open(path, "rb") as f:
                heads[basin] = pickle.load(f)
    sources = sorted({h.get("rain_source", "avg") for h in heads.values()} | {"avg"})
    frames = T.build_dataset(end=pd.Timestamp(days[-1]["date"]), sources=sources)[0]
    out: dict = {}
    for basin, h in heads.items():
        X = frames[h.get("rain_source", "avg")]
        v = T.predicted_volume(h, X)
        for d, val in zip(X["date"], v):
            out.setdefault(str(pd.Timestamp(d).date()), {})[BASIN_KEYS[basin]] = float(val)
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
        probs = [{bk: float(by_date[str(d)]["basins"][bk]["p"]) for bk in GROUPS_BY_BASIN} for d in win]
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
    probs = [{bk: float(d["basins"][bk]["p"]) for bk in GROUPS_BY_BASIN} for d in days]
    vols = [{bk: float(vol_pred.get(d["date"], {}).get(bk, 0.0)) for bk in GROUPS_BY_BASIN} for d in days]
    out, worst = {}, 0.0
    for i, d in enumerate(days):
        D = dates_all[i]
        if D < era[0] or D > era[1]:
            continue
        _, groups = compose(table, GROUPS_BY_BASIN, probs, vols, i, dates_all, {})
        out[d["date"]] = groups
        for g, v in groups.items():
            stored = d["groups"][g]["risk"]
            if stored is not None:
                worst = max(worst, abs(float(v) - float(stored)))
    return out, worst


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
    vol_pred = predicted_volumes(days)
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
    zone_rows = ['<table><tr><th>Zone (50%, primary ruler)</th><th>plain</th><th>live_v1</th></tr>']
    for z in ZONE_ORDER:
        a, b = v["plain"]["grades"]["0.5"]["combined"]["zones"][z], v["live_v1"]["grades"]["0.5"]["combined"]["zones"][z]
        f = lambda c: f'{c["tp"]} of {c["tp"]+c["fn"]} caught · {c["fp"]} false (clean-sample {c["fp_sample"]}, quiet {c["fp_quiet"]}) · cost {c["fp"] + WFN * c["fn"]}'  # noqa: E731
        zone_rows.append(f'<tr><td>{esc(ZONES[z].label)}</td><td>{f(a)}</td><td>{f(b)}</td></tr>')
    zone_rows.append('</table>')
    r = res["rules"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live corrections replay — live_v1 vs the model alone</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css} tr.best td{{background:#e0f0ea}}</style></head><body><div class="wrap">
<header><h1>Live corrections replay: live_v1 vs the model alone</h1>
<p class="sub">Every day of the era recomposed with only what was known by the <b>end of the day before</b> — the start-of-day forecast — the served stage-1 probabilities, the feed's CSO onsets and flag days, samples published a day later — through <code>src/models/live_rules.py</code> exactly as serving does, then graded on the Model check's three rulers. A discharge day is never credited for its own flag; the corrections are judged on the tail they shape. Cost = false alarms + {WFN} × misses.</p>
<div class="meta"><span>era {esc(res["era"][0])} → {esc(res["era"][1])} ({v["plain"]["n_days"]} days)</span><span>{res["flag_days"]} flag basin-days · {res["onset_days"]} onset basin-days ({esc(", ".join(res["onset_basins"]))})</span><span>self-check: plain recomposition vs stored risks, worst Δ {res["self_check_worst_delta"]}</span><span>downgrade recall by quiet days {esc(json.dumps(r["downgrade"]["recall_by_quiet_days"]))}</span></div></header>
<section><h2>Read this first</h2>
<p class="lead">The only era with feed-flag data today is the 2016-17 Poo Bot archive. Its stage-1 probabilities are <b>in-sample</b> (the served models trained on these seasons), so every variant is flattered by the same amount — read the difference between rows, not the rows. <b>Read the bayside subtotal as the clean comparison:</b> North Shore and East labels come from CIWQS, independent of the feed. The Westside has no CIWQS per-event records before 2018, so its discharge labels in this era <i>are</i> the archive's feed onsets — the onset rule catches them by construction and that column is marked circular (the sample part of the primary ruler is still independent there). Snapshots came twice a day, so a flag that came and went between them is missed. The watcher era (Aug 2026 →) joins this page once the hindcast artifact is rescored past it.</p>
<p class="lead">Variants: <b>plain</b> = the two-stage model alone · <b>live_v1</b> = every rule · <b>no_downgrade</b> = live_v1 with the no-flag downgrade off · <b>no_samples</b> = live_v1 without the sample floors and caps · <b>cso_flags_only</b> = onset, anchor, large volume and flag hold only.</p></section>
<section>{"".join(sections)}</section>
<section><h2>Per zone, 50% line, primary ruler</h2>{"".join(zone_rows)}</section>
<footer>Generated by <code>features/forecast/src/models/replay_live.py</code>. Design: <code>features/forecast/LIVE_COMPOSITION_DESIGN.md</code>. <a href="/forecast">Back to the forecast</a> · <a href="/reports/2026-09_model_analysis.html">Model analysis</a></footer>
</div></body></html>"""


if __name__ == "__main__":
    main(write="--no-write" not in sys.argv, cutoff="end" if "--cutoff=end" in sys.argv else "start")
