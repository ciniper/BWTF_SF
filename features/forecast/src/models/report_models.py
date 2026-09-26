#!/usr/bin/env python3
"""Model analysis report — every model set (stage 1 × stage 2) ranked from
several angles, with charts, from the scorecard artifacts alone.

    venv/bin/python features/forecast/src/models/report_models.py
    → reports/2026-09_model_analysis.html   (served at /reports/…)

Angles: stage-1 ranking quality and calibration per basin; zone-level
operating performance at fixed lines (caught, false alarms split into the
tail after a real discharge vs clean days, cost under several weightings);
cost-vs-line curves; the isolated effect of stage 2 v2 and of the weights
stage 1; a volume-weighted catch rate (a 48 MG discharge should not count
like a 0.1 MG trickle); the bacteria-sample view. Windows: the holdout
(Jul 2023 → Oct 2025, holdout-fit stage 1) and post-training (Nov 2025 →
Aug 2026, the served/candidate models on days they never saw), scored
separately and pooled ("out of sample"). Re-run after every rescore.
"""
from __future__ import annotations

import datetime as dt
import gzip
import html
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import candidates  # noqa: E402
import stage2 as S2  # noqa: E402
from groups import SITE_GROUPS, ZONE_GROUPS  # noqa: E402
from scorecard import LINE_GRID, basin_metrics, zone_confusion, zone_fp_tail  # noqa: E402
from shared.zones import ZONES  # noqa: E402

REPO = HERE.parents[3]
SERVE_DIR = HERE.parents[1] / "data" / "models"
OUT = REPO / "reports" / "2026-09_model_analysis.html"
ZONE_ORDER = ["ocean", "baker_china", "north", "east"]
BASIN_ORDER = ["westside", "north_shore", "central", "southeast"]
BASIN_NAME = {"westside": "Westside", "north_shore": "North Shore", "central": "Central", "southeast": "Southeast"}
ZONE_BASIN = {"ocean": "westside", "baker_china": "westside", "north": "north_shore", "east": "southeast"}
LINES = [0.25, 0.5]
# cost weightings: (label, weight per false alarm, weight per miss)
WEIGHTINGS = [("a miss costs 2 false alarms", 1, 2), ("1 : 1", 1, 1), ("a miss costs 4 false alarms", 1, 4), ("a miss costs 6 false alarms", 1, 6),
              ("a miss costs 10 false alarms", 1, 10), ("a false alarm costs 2 misses", 2, 1)]
PRIMARY = ("a miss costs 2 false alarms", 1, 2)
COLORS = ["#0072BC", "#b5310a", "#7b4bb5", "#237059", "#b97e00", "#d4763a"]


# ── data ────────────────────────────────────────────────────────────────────

def load_sets() -> list[dict]:
    sc = json.load(gzip.open(SERVE_DIR / "scorecard.json.gz"))
    sets = [{"name": "gb_v1", "label": "gb_v1 (served)", "stage1": "gb_v1", "stage2": "v1", "served": True, "sc": sc}]
    for man in candidates.list_candidates():
        c = candidates.load_scorecard(man["name"])
        if not c:
            continue
        sets.append({"name": man["name"], "label": man["name"], "stage1": (man.get("stage1") or {}).get("name", man["name"]),
                     "stage2": (man.get("stage2") or {}).get("variant", "v1"), "served": False, "sc": c, "note": man.get("note", "")})
    return sets


def zone_volumes(sets_master: dict) -> dict:
    """{zone: {date: MG}} — reported volume that day from outfalls posting the zone's beaches."""
    ev = pd.read_csv(HERE.parents[1] / "data" / "csd" / "sf_csd_events.csv", parse_dates=["event_date"])
    out = {}
    for zk, groups in ZONE_GROUPS.items():
        outs = set()
        for g in groups:
            outs |= set(S2.outfalls_posting(g))
        sub = ev[ev["outfall_id"].astype(str).isin(outs)]
        vol = sub.groupby(sub["event_date"].dt.strftime("%Y-%m-%d"))["volume_MG"].sum()
        out[zk] = {d: float(v) for d, v in vol.items()}
    return out


def windows(sc: dict) -> dict:
    hs, tt, last = sc["holdout_start"], sc["trained_through"], sc["span"][1]
    post0 = str(dt.date.fromisoformat(tt) + dt.timedelta(days=1))
    return {"holdout": (hs, tt, True), "post": (post0, last, False), "oos": (hs, last, False)}


def _risk(z: dict, holdout_only: bool):
    if z.get("risk_h") is not None:
        return z["risk_h"]
    return None if holdout_only else z["risk"]


def evaluate(s: dict, zvol: dict) -> dict:
    sc = s["sc"]
    days = sc["days"]
    res = {"windows": {}}
    for wname, (lo, hi, ho) in windows(sc).items():
        # "oos" pools holdout-fit probabilities (where they exist) with served ones after training
        conf = zone_confusion(days, ZONE_ORDER, lo, hi, holdout_only=ho, thresholds=LINE_GRID)
        tail = zone_fp_tail(days, ZONE_ORDER, lo, hi, holdout_only=ho, thresholds=LINE_GRID)
        bm = basin_metrics(days, BASIN_ORDER, lo, hi, holdout_only=ho)
        # calibration bands, stage 1, pooled over basins
        bands = [(0, .1), (.1, .25), (.25, .5), (.5, .75), (.75, 1.01)]
        cal = []
        for blo, bhi in bands:
            n = k = 0
            psum = 0.0
            for d in days:
                if not (lo <= d["date"] <= hi):
                    continue
                for key in BASIN_ORDER:
                    b = d["basins"][key]
                    p = b["ph"] if b.get("ph") is not None else (None if ho else b["p"])
                    if p is None or b["y"] is None or not (blo <= p < bhi):
                        continue
                    n += 1; k += b["y"]; psum += p
            cal.append({"band": f"{int(blo*100)}–{int(min(bhi,1)*100)}%", "n": n, "hits": k, "observed": (k / n) if n else None, "predicted": (psum / n) if n else None})
        # volume-weighted catch per zone at each line + bacteria view
        volz, bact = {}, {}
        for zk in ZONE_ORDER:
            volz[zk] = {}
            for thr in LINE_GRID:
                caught_mg = total_mg = 0.0
                for d in days:
                    if not (lo <= d["date"] <= hi):
                        continue
                    z = d["zones"][zk]
                    r = _risk(z, ho)
                    if r is None or not z["discharge"]:
                        continue
                    mg = zvol[zk].get(d["date"], 0.0)
                    total_mg += mg
                    if r >= thr:
                        caught_mg += mg
                volz[zk][str(thr)] = {"caught_mg": caught_mg, "total_mg": total_mg}
        res["windows"][wname] = {"start": lo, "end": hi, "confusion": conf, "tail": tail, "basins": bm, "calibration": cal, "volume": volz}
    return res


def cost(conf: dict, tail: dict, thr: float, wfp: float, wfn: float, clean_only: bool = False) -> dict:
    tot = 0.0
    per = {}
    for zk in ZONE_ORDER:
        d = conf[zk][str(thr)]["vs_discharge_posting"]
        fp = d["fp"] - (tail[zk][str(thr)] if clean_only else 0)
        c = wfp * fp + wfn * d["fn"]
        per[zk] = c
        tot += c
    return {"total": tot, "per_zone": per}


# ── SVG helpers ─────────────────────────────────────────────────────────────

def esc(x) -> str:
    return html.escape(str(x))


def svg_lines(series: list[dict], xs: list[float], ylabel: str, xlabel: str, width=900, height=300, xfmt=lambda v: f"{int(v*100)}%", mark_x=None) -> str:
    pl, pr, pt, pb = 52, 16, 14, 40
    ymax = max(max(s["y"]) for s in series) * 1.08 or 1
    X = lambda v: pl + (v - xs[0]) / (xs[-1] - xs[0]) * (width - pl - pr)
    Y = lambda v: pt + (1 - v / ymax) * (height - pt - pb)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="display:block">']
    for i in range(5):
        v = ymax * i / 4
        out.append(f'<line x1="{pl}" y1="{Y(v):.1f}" x2="{width-pr}" y2="{Y(v):.1f}" stroke="#e3ebf2"/><text x="{pl-6}" y="{Y(v)+4:.1f}" text-anchor="end" font-size="11" fill="#54576F">{v:.0f}</text>')
    for x in xs:
        out.append(f'<text x="{X(x):.1f}" y="{height-12}" text-anchor="middle" font-size="11" fill="#54576F">{xfmt(x)}</text>')
    if mark_x is not None:
        out.append(f'<line x1="{X(mark_x):.1f}" y1="{pt}" x2="{X(mark_x):.1f}" y2="{height-pb}" stroke="#26272a" stroke-dasharray="3 3"/>')
    for k, s in enumerate(series):
        pts = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(zip(xs, s["y"])))
        out.append(f'<path d="{pts}" fill="none" stroke="{s.get("color", COLORS[k])}" stroke-width="2.4" stroke-dasharray="{s.get("dash", "")}"/>')
        for x, y in zip(xs, s["y"]):
            out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="3.2" fill="{s.get("color", COLORS[k])}"><title>{esc(s["label"])} · {xfmt(x)}: {y:.0f}</title></circle>')
        out.append(f'<rect x="{pl + 8 + k*200}" y="{pt}" width="12" height="12" fill="{s.get("color", COLORS[k])}"/><text x="{pl + 24 + k*200}" y="{pt+10}" font-size="11.5" fill="#26272a">{esc(s["label"])}</text>')
    out.append(f'<text x="{(pl+width-pr)/2:.0f}" y="{height-26}" text-anchor="middle" font-size="11.5" fill="#26272a">{esc(xlabel)}</text>')
    out.append(f'<text x="14" y="{(pt+height-pb)/2:.0f}" text-anchor="middle" font-size="11.5" fill="#26272a" transform="rotate(-90 14 {(pt+height-pb)/2:.0f})">{esc(ylabel)}</text></svg>')
    return "".join(out)


def svg_grouped_bars(groups: list[str], series: list[dict], ylabel: str, width=900, height=280, yfmt=lambda v: f"{v:.2f}", ymax=None, stacked_key=None) -> str:
    """series[k] = {label, values[len(groups)], color, (stack: values2 drawn on top in lighter tone)}"""
    pl, pr, pt, pb = 52, 16, 36, 46
    vals = [v + (s.get("stack", [0]*len(groups))[i]) for s in series for i, v in enumerate(s["values"])]
    ymax = ymax or (max(vals) * 1.15 if vals and max(vals) > 0 else 1)
    n, m = len(groups), len(series)
    gw = (width - pl - pr) / n
    bw = gw * 0.8 / m
    Y = lambda v: pt + (1 - v / ymax) * (height - pt - pb)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="display:block">']
    for i in range(5):
        v = ymax * i / 4
        out.append(f'<line x1="{pl}" y1="{Y(v):.1f}" x2="{width-pr}" y2="{Y(v):.1f}" stroke="#e3ebf2"/><text x="{pl-6}" y="{Y(v)+4:.1f}" text-anchor="end" font-size="11" fill="#54576F">{yfmt(v)}</text>')
    for i, g in enumerate(groups):
        out.append(f'<text x="{pl + gw*i + gw/2:.1f}" y="{height-14}" text-anchor="middle" font-size="11.5" fill="#26272a">{esc(g)}</text>')
        for k, s in enumerate(series):
            x = pl + gw * i + gw * 0.1 + bw * k
            v = s["values"][i]
            out.append(f'<rect x="{x:.1f}" y="{Y(v):.1f}" width="{bw-2:.1f}" height="{Y(0)-Y(v):.1f}" fill="{s["color"]}"><title>{esc(s["label"])} · {esc(g)}: {yfmt(v)}</title></rect>')
            st = s.get("stack")
            if st and st[i]:
                out.append(f'<rect x="{x:.1f}" y="{Y(v+st[i]):.1f}" width="{bw-2:.1f}" height="{Y(v)-Y(v+st[i]):.1f}" fill="{s["color"]}" fill-opacity=".35"><title>{esc(s["label"])} · {esc(g)} ({esc(stacked_key or "stack")}): {yfmt(st[i])}</title></rect>')
            lab = s.get("labels")
            if lab:
                out.append(f'<text x="{x + (bw-2)/2:.1f}" y="{Y(v + (st[i] if st else 0)) - 4:.1f}" text-anchor="middle" font-size="10" fill="#26272a">{esc(lab[i])}</text>')
    for k, s in enumerate(series):
        out.append(f'<rect x="{pl + 8 + k*190}" y="8" width="12" height="12" fill="{s["color"]}"/><text x="{pl + 24 + k*190}" y="18" font-size="11.5" fill="#26272a">{esc(s["label"])}</text>')
    if stacked_key:
        out.append(f'<rect x="{pl + 8 + m*190}" y="8" width="12" height="12" fill="#54576F" fill-opacity=".35"/><text x="{pl + 24 + m*190}" y="18" font-size="11.5" fill="#26272a">lighter = {esc(stacked_key)}</text>')
    out.append(f'<text x="14" y="{(pt+height-pb)/2:.0f}" text-anchor="middle" font-size="11.5" fill="#26272a" transform="rotate(-90 14 {(pt+height-pb)/2:.0f})">{esc(ylabel)}</text></svg>')
    return "".join(out)


def svg_calibration(cals: list[dict], width=900, height=300) -> str:
    """cals[k] = {label, color, points: [{predicted, observed, n}]}"""
    pl, pr, pt, pb = 52, 16, 14, 40
    X = lambda v: pl + v * (width - pl - pr)
    Y = lambda v: pt + (1 - v) * (height - pt - pb)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="display:block">']
    for v in (0, .25, .5, .75, 1):
        out.append(f'<line x1="{pl}" y1="{Y(v):.1f}" x2="{width-pr}" y2="{Y(v):.1f}" stroke="#e3ebf2"/><text x="{pl-6}" y="{Y(v)+4:.1f}" text-anchor="end" font-size="11" fill="#54576F">{int(v*100)}%</text><text x="{X(v):.1f}" y="{height-12}" text-anchor="middle" font-size="11" fill="#54576F">{int(v*100)}%</text>')
    out.append(f'<line x1="{X(0):.1f}" y1="{Y(0):.1f}" x2="{X(1):.1f}" y2="{Y(1):.1f}" stroke="#54576F" stroke-dasharray="4 3"/>')
    for k, c in enumerate(cals):
        pts = [p for p in c["points"] if p["predicted"] is not None]
        path = " ".join(f"{'M' if i == 0 else 'L'}{X(p['predicted']):.1f},{Y(p['observed']):.1f}" for i, p in enumerate(pts))
        out.append(f'<path d="{path}" fill="none" stroke="{c["color"]}" stroke-width="2"/>')
        for p in pts:
            out.append(f'<circle cx="{X(p["predicted"]):.1f}" cy="{Y(p["observed"]):.1f}" r="{min(9, 3 + np.sqrt(p["n"])/2):.1f}" fill="{c["color"]}" fill-opacity=".75"><title>{esc(c["label"])} · band {esc(p["band"])}: predicted {p["predicted"]:.0%}, observed {p["observed"]:.0%} ({p["hits"]} of {p["n"]} basin-days)</title></circle>')
        out.append(f'<rect x="{pl + 8 + k*200}" y="{pt}" width="12" height="12" fill="{c["color"]}"/><text x="{pl + 24 + k*200}" y="{pt+10}" font-size="11.5" fill="#26272a">{esc(c["label"])}</text>')
    out.append(f'<text x="{(pl+width-pr)/2:.0f}" y="{height-26}" text-anchor="middle" font-size="11.5" fill="#26272a">predicted probability (mean of the band)</text>')
    out.append(f'<text x="14" y="{(pt+height-pb)/2:.0f}" text-anchor="middle" font-size="11.5" fill="#26272a" transform="rotate(-90 14 {(pt+height-pb)/2:.0f})">share of basin-days that discharged</text></svg>')
    return "".join(out)


def svg_scatter_ops(sets_eval: list[tuple[dict, dict]], zk: str, wname: str, width=440, height=260) -> str:
    """caught (y) vs false alarms (x) along the line grid, one path per set; 50% dot larger."""
    pl, pr, pt, pb = 40, 12, 14, 36
    xmax = max(e["windows"][wname]["confusion"][zk][str(t)]["vs_discharge_posting"]["fp"] for _, e in sets_eval for t in LINE_GRID) or 1
    posted = None
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="display:block">']
    X = lambda v: pl + v / (xmax * 1.05) * (width - pl - pr)
    for k, (s, e) in enumerate(sets_eval):
        c = e["windows"][wname]["confusion"][zk]
        posted = c["0.5"]["vs_discharge_posting"]["tp"] + c["0.5"]["vs_discharge_posting"]["fn"]
        Y = lambda v: pt + (1 - (v / posted if posted else 0)) * (height - pt - pb)
        pts = [(c[str(t)]["vs_discharge_posting"]["fp"], c[str(t)]["vs_discharge_posting"]["tp"], t) for t in LINE_GRID]
        path = " ".join(f"{'M' if i == 0 else 'L'}{X(fp):.1f},{Y(tp):.1f}" for i, (fp, tp, _) in enumerate(pts))
        out.append(f'<path d="{path}" fill="none" stroke="{COLORS[k]}" stroke-width="1.8" stroke-dasharray="{"" if k % 2 == 0 else "5 4"}"/>')
        for fp, tp, t in pts:
            out.append(f'<circle cx="{X(fp):.1f}" cy="{Y(tp):.1f}" r="{5 if t == 0.5 else 2.6}" fill="{COLORS[k]}" fill-opacity="{1 if t == 0.5 else .7}"><title>{esc(s["name"])} · {int(t*100)}% line: caught {tp} of {posted}, {fp} false alarms</title></circle>')
    Yl = lambda v: pt + (1 - v) * (height - pt - pb)
    for v in (0, .5, 1):
        out.append(f'<line x1="{pl}" y1="{Yl(v):.1f}" x2="{width-pr}" y2="{Yl(v):.1f}" stroke="#e3ebf2"/><text x="{pl-5}" y="{Yl(v)+4:.1f}" text-anchor="end" font-size="10.5" fill="#54576F">{int(v*100)}%</text>')
    for v in np.linspace(0, xmax, 5):
        out.append(f'<text x="{X(v):.1f}" y="{height-10}" text-anchor="middle" font-size="10.5" fill="#54576F">{v:.0f}</text>')
    out.append(f'<text x="{(pl+width-pr)/2:.0f}" y="{height-22}" text-anchor="middle" font-size="11" fill="#26272a">false alarms (large dot = 50% line)</text>')
    out.append(f'<text x="12" y="{(pt+height-pb)/2:.0f}" text-anchor="middle" font-size="11" fill="#26272a" transform="rotate(-90 12 {(pt+height-pb)/2:.0f})">discharge days caught</text></svg>')
    return "".join(out)


# ── report ──────────────────────────────────────────────────────────────────

def fmt_pct(v):
    return "—" if v is None else f"{v:.0%}"


def build_html(sets: list[dict], evals: list[dict], narrative: str) -> str:
    se = list(zip(sets, evals))
    master = sets[0]["sc"]
    w = windows(master)
    gen = dt.datetime.now().isoformat(timespec="minutes")

    # ranking tables
    def rank_table(wname: str, thr: float, weighting, clean_only: bool) -> tuple[str, list]:
        label, wfp, wfn = weighting
        rows = []
        for s, e in se:
            W = e["windows"][wname]
            c = cost(W["confusion"], W["tail"], thr, wfp, wfn, clean_only)
            tp = sum(W["confusion"][zk][str(thr)]["vs_discharge_posting"]["tp"] for zk in ZONE_ORDER)
            fn = sum(W["confusion"][zk][str(thr)]["vs_discharge_posting"]["fn"] for zk in ZONE_ORDER)
            fp = sum(W["confusion"][zk][str(thr)]["vs_discharge_posting"]["fp"] for zk in ZONE_ORDER)
            tl = sum(W["tail"][zk][str(thr)] for zk in ZONE_ORDER)
            mg_c = sum(W["volume"][zk][str(thr)]["caught_mg"] for zk in ZONE_ORDER)
            mg_t = sum(W["volume"][zk][str(thr)]["total_mg"] for zk in ZONE_ORDER)
            rows.append({"set": s, "cost": c["total"], "per_zone": c["per_zone"], "tp": tp, "fn": fn, "fp": fp, "tail": tl, "clean": fp - tl, "mg_c": mg_c, "mg_t": mg_t})
        rows.sort(key=lambda r: (r["cost"], -r["tp"]))
        h = [f'<table><tr><th>#</th><th>Set</th><th>stage 1 · stage 2</th><th class="num">cost</th><th class="num">caught</th><th class="num">missed</th><th class="num">false alarms</th><th class="num">· after a real discharge</th><th class="num">· clean days</th><th class="num">MG caught</th>' + "".join(f'<th class="num">{esc(ZONES[zk].label.split(" ")[0])}</th>' for zk in ZONE_ORDER) + '</tr>']
        for i, r in enumerate(rows):
            s = r["set"]
            h.append(f'<tr class="{"best" if i == 0 else ""}"><td>{i+1}</td><td><b>{esc(s["label"])}</b>{" <span class=\"badge info\">served</span>" if s["served"] else ""}</td><td class="fine">{esc(s["stage1"])} · s2 {esc(s["stage2"])}</td><td class="num"><b>{r["cost"]:.0f}</b></td><td class="num">{r["tp"]} of {r["tp"]+r["fn"]}</td><td class="num">{r["fn"]}</td><td class="num">{r["fp"]}</td><td class="num">{r["tail"]}</td><td class="num">{r["clean"]}</td><td class="num">{r["mg_c"]:.0f} of {r["mg_t"]:.0f}</td>' + "".join(f'<td class="num">{r["per_zone"][zk]:.0f}</td>' for zk in ZONE_ORDER) + '</tr>')
        h.append('</table>')
        return "".join(h), rows

    primary_html, primary_rows = rank_table("oos", 0.5, PRIMARY, False)
    post_html, post_rows = rank_table("post", 0.5, PRIMARY, False)
    hold_html, _ = rank_table("holdout", 0.5, PRIMARY, False)
    clean_html, clean_rows = rank_table("oos", 0.5, PRIMARY, True)
    line25_html, rows25 = rank_table("oos", 0.25, PRIMARY, False)

    # cheapest line per set, overall and per zone (oos, primary weighting)
    cheap = ['<table><tr><th>Set</th><th class="num">cheapest single line, all zones</th><th class="num">per-zone lines, summed</th>' + "".join(f'<th class="num">{esc(ZONES[zk].label)}</th>' for zk in ZONE_ORDER) + '</tr>']
    for s, e in se:
        W = e["windows"]["oos"]
        tot = {t: cost(W["confusion"], W["tail"], t, PRIMARY[1], PRIMARY[2]) for t in LINE_GRID}
        best_all = min(LINE_GRID, key=lambda t: (tot[t]["total"], -t))
        cells = []
        zone_sum = sum(min(tot[t]["per_zone"][zk] for t in LINE_GRID) for zk in ZONE_ORDER)
        for zk in ZONE_ORDER:
            bz = min(LINE_GRID, key=lambda t: (tot[t]["per_zone"][zk], -t))
            d = W["confusion"][zk][str(bz)]["vs_discharge_posting"]
            cells.append(f'<td class="num"><b>{int(bz*100)}%</b> <span class="fine">cost {tot[bz]["per_zone"][zk]:.0f} · {d["tp"]} of {d["tp"]+d["fn"]} caught, {d["fp"]} false</span></td>')
        cheap.append(f'<tr><td><b>{esc(s["label"])}</b></td><td class="num"><b>{int(best_all*100)}%</b> <span class="fine">cost {tot[best_all]["total"]:.0f} (vs {tot[0.5]["total"]:.0f} at 50%)</span></td><td class="num"><b>{zone_sum:.0f}</b> <span class="fine">each zone at its own cheapest line</span></td>' + "".join(cells) + '</tr>')
    cheap.append('</table>')
    cheap_html = "".join(cheap)

    # sensitivity: rank position of each set under every weighting × line × fp-mode (oos)
    sens = ['<table><tr><th>Setting (out of sample, Jul 2023 → Aug 2026)</th>' + "".join(f'<th class="num">{esc(s["label"])}</th>' for s in sets) + '</tr>']
    for thr in LINES:
        for weighting in WEIGHTINGS:
            for clean_only in (False, True):
                _, rows = rank_table("oos", thr, weighting, clean_only)
                pos = {r["set"]["name"]: (i + 1, r["cost"]) for i, r in enumerate(rows)}
                sens.append(f'<tr><td>{int(thr*100)}% line · {esc(weighting[0])}{" · clean-day false alarms only" if clean_only else ""}</td>' + "".join(f'<td class="num">{"<b>" if pos[s["name"]][0] == 1 else ""}#{pos[s["name"]][0]} <span class="fine">({pos[s["name"]][1]:.0f})</span>{"</b>" if pos[s["name"]][0] == 1 else ""}</td>' for s in sets) + '</tr>')
    sens.append('</table>')

    # cost vs line chart (oos, primary weighting), all zones
    xs = list(LINE_GRID)
    series = [{"label": s["label"], "y": [cost(e["windows"]["oos"]["confusion"], e["windows"]["oos"]["tail"], t, PRIMARY[1], PRIMARY[2])["total"] for t in xs], "color": COLORS[k], "dash": "" if k % 2 == 0 else "6 4"} for k, (s, e) in enumerate(se)]
    cost_chart = svg_lines(series, xs, f"cost, all zones ({PRIMARY[0]})", "alarm line", mark_x=0.5)
    series_c = [{"label": s["label"], "y": [cost(e["windows"]["oos"]["confusion"], e["windows"]["oos"]["tail"], t, PRIMARY[1], PRIMARY[2], True)["total"] for t in xs], "color": COLORS[k], "dash": "" if k % 2 == 0 else "6 4"} for k, (s, e) in enumerate(se)]
    cost_chart_clean = svg_lines(series_c, xs, "cost counting clean-day false alarms only", "alarm line", mark_x=0.5)

    # stage 1: PR-AUC per basin, holdout and post, for each distinct stage 1
    stage1_sets = {}
    for s, e in se:
        stage1_sets.setdefault(s["stage1"], (s, e))
    s1_rows = ['<table><tr><th>Basin</th>' + "".join(f'<th class="num">{esc(n)} · holdout PR-AUC</th><th class="num">{esc(n)} · since training PR-AUC</th><th class="num">{esc(n)} · holdout Brier</th><th class="num">{esc(n)} · since training Brier</th>' for n in stage1_sets) + '</tr>']
    for key in BASIN_ORDER:
        s1_rows.append(f'<tr><td><b>{BASIN_NAME[key]}</b></td>' + "".join(
            f'<td class="num">{e["windows"]["holdout"]["basins"][key]["pr_auc"]:.3f}</td><td class="num">{e["windows"]["post"]["basins"][key]["pr_auc"]:.3f}</td><td class="num">{e["windows"]["holdout"]["basins"][key]["brier"]:.4f}</td><td class="num">{e["windows"]["post"]["basins"][key]["brier"]:.4f}</td>'
            for n, (s, e) in stage1_sets.items()) + '</tr>')
    s1_rows.append('</table>')
    pr_bars = svg_grouped_bars([BASIN_NAME[k] for k in BASIN_ORDER],
                               [{"label": f"{n} · holdout", "values": [e["windows"]["holdout"]["basins"][k]["pr_auc"] for k in BASIN_ORDER], "color": COLORS[i*2]} for i, (n, (s, e)) in enumerate(stage1_sets.items())] +
                               [{"label": f"{n} · since training", "values": [e["windows"]["post"]["basins"][k]["pr_auc"] for k in BASIN_ORDER], "color": COLORS[i*2+1]} for i, (n, (s, e)) in enumerate(stage1_sets.items())],
                               "stage 1 PR-AUC (discharge days vs quiet days)", ymax=1.0)
    cal_chart = svg_calibration([{"label": n, "color": COLORS[i*2], "points": e["windows"]["oos"]["calibration"]} for i, (n, (s, e)) in enumerate(stage1_sets.items())])
    cal_table = ['<table><tr><th>Predicted band</th>' + "".join(f'<th class="num">{esc(n)}: discharged / basin-days</th>' for n in stage1_sets) + '</tr>']
    for bi in range(5):
        cal_table.append('<tr><td>' + esc(next(iter(stage1_sets.values()))[1]["windows"]["oos"]["calibration"][bi]["band"]) + '</td>' + "".join(
            f'<td class="num">{c["hits"]} / {c["n"]}{" = " + fmt_pct(c["observed"]) if c["n"] else ""}</td>' for n, (s, e) in stage1_sets.items() for c in [e["windows"]["oos"]["calibration"][bi]]) + '</tr>')
    cal_table.append('</table>')

    # zone false alarms at 50% (oos): stacked clean + tail, with caught labels
    fa_bars = svg_grouped_bars([ZONES[zk].label for zk in ZONE_ORDER],
                               [{"label": s["label"], "color": COLORS[k],
                                 "values": [e["windows"]["oos"]["confusion"][zk]["0.5"]["vs_discharge_posting"]["fp"] - e["windows"]["oos"]["tail"][zk]["0.5"] for zk in ZONE_ORDER],
                                 "stack": [e["windows"]["oos"]["tail"][zk]["0.5"] for zk in ZONE_ORDER],
                                 "labels": [f'{e["windows"]["oos"]["confusion"][zk]["0.5"]["vs_discharge_posting"]["tp"]}/{e["windows"]["oos"]["confusion"][zk]["0.5"]["vs_discharge_posting"]["tp"] + e["windows"]["oos"]["confusion"][zk]["0.5"]["vs_discharge_posting"]["fn"]}' for zk in ZONE_ORDER]}
                                for k, (s, e) in enumerate(se)],
                               "false alarms at the 50% line (label = discharge days caught)", yfmt=lambda v: f"{v:.0f}", stacked_key="within 7 days after a real discharge")
    vol_bars = svg_grouped_bars([ZONES[zk].label for zk in ZONE_ORDER],
                                [{"label": s["label"], "color": COLORS[k],
                                  "values": [(e["windows"]["oos"]["volume"][zk]["0.5"]["caught_mg"] / e["windows"]["oos"]["volume"][zk]["0.5"]["total_mg"]) if e["windows"]["oos"]["volume"][zk]["0.5"]["total_mg"] else 0 for zk in ZONE_ORDER]}
                                 for k, (s, e) in enumerate(se)],
                                "share of discharged volume (MG) on caught days, 50% line", yfmt=lambda v: f"{v:.0%}", ymax=1.0)
    ops = "".join(f'<div class="card"><b class="t">{esc(ZONES[zk].label)} — caught vs false alarms along the line grid (out of sample)</b>{svg_scatter_ops(se, zk, "oos")}</div>' for zk in ZONE_ORDER)

    # isolated effects (oos, 50%): stage 2 v2 vs v1 with the same stage 1; logit vs gb with the same stage 2
    by = {(s["stage1"], s["stage2"]): (s, e) for s, e in se}
    def delta_rows(pairs, title):
        h = [f'<table><tr><th>{esc(title)}</th>' + "".join(f'<th class="num">{esc(ZONES[zk].label)}</th>' for zk in ZONE_ORDER) + '<th class="num">all zones</th></tr>']
        for lab, a, b in pairs:
            if a not in by or b not in by:
                continue
            (sa, ea), (sb, eb) = by[a], by[b]
            cells, tot_fp, tot_tp = [], 0, 0
            for zk in ZONE_ORDER:
                da = ea["windows"]["oos"]["confusion"][zk]["0.5"]["vs_discharge_posting"]; db = eb["windows"]["oos"]["confusion"][zk]["0.5"]["vs_discharge_posting"]
                dfp, dtp = db["fp"] - da["fp"], db["tp"] - da["tp"]; tot_fp += dfp; tot_tp += dtp
                cells.append(f'<td class="num">{dtp:+d} caught · {dfp:+d} false alarms</td>')
            h.append(f'<tr><td>{esc(lab)}</td>' + "".join(cells) + f'<td class="num"><b>{tot_tp:+d} caught · {tot_fp:+d} false alarms</b></td></tr>')
        h.append('</table>')
        return "".join(h)
    effect_s2 = delta_rows([("gb_v1: stage 2 v1 → v2", ("gb_v1", "v1"), ("gb_v1", "v2")), ("logit_v1: stage 2 v1 → v2", ("logit_v1", "v1"), ("logit_v1", "v2"))], "Stage 2 v2 vs v1, same stage 1 (50% line, out of sample)")
    effect_s1 = delta_rows([("stage 2 v1: gb_v1 → logit_v1", ("gb_v1", "v1"), ("logit_v1", "v1")), ("stage 2 v2: gb_v1 → logit_v1", ("gb_v1", "v2"), ("logit_v1", "v2"))], "Weights stage 1 vs trees, same stage 2 (50% line, out of sample)")

    # bacteria view at 50% (oos)
    bact = ['<table><tr><th>Set</th>' + "".join(f'<th class="num">{esc(ZONES[zk].label)}: elevated-sample days caught</th><th class="num">alarms on clean-sample days</th>' for zk in ZONE_ORDER) + '</tr>']
    for s, e in se:
        cells = []
        for zk in ZONE_ORDER:
            b = e["windows"]["oos"]["confusion"][zk]["0.5"]["vs_bacteria_elevated"]
            cells.append(f'<td class="num">{b["tp"]} of {b["tp"]+b["fn"]} ({fmt_pct(b["tp"]/(b["tp"]+b["fn"]) if b["tp"]+b["fn"] else None)})</td><td class="num">{b["fp"]} of {b["fp"]+b["tn"]} ({fmt_pct(b["fp"]/(b["fp"]+b["tn"]) if b["fp"]+b["tn"] else None)})</td>')
        bact.append(f'<tr><td><b>{esc(s["label"])}</b></td>' + "".join(cells) + '</tr>')
    bact.append('</table>')

    king = primary_rows[0]["set"]
    served_row = next(r for r in primary_rows if r["set"]["served"])
    css = (REPO / "features/forecast/src/models/stage2_explorer_template.html").read_text().split("<style>")[1].split("</style>")[0]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model analysis — which stage 1 and stage 2 to run</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css}
tr.best td{{background:#e0f0ea}} .verdict{{background:#0072BC;color:#fff;border-radius:16px;padding:16px 20px;font-size:16px}} .verdict b{{font-size:20px}}
.narr p{{margin:8px 0}} .narr h3{{margin-top:18px}}
</style></head><body><div class="wrap">
<header><h1>Model analysis: which stage 1 and stage 2 to run</h1>
<p class="sub">Every model set on disk, scored on identical days and labels from the stored scorecard artifacts, ranked by cost. Primary setting per Chase (2026-09-26): <b>{esc(PRIMARY[0])}</b>, at the <b>50% line</b>, over every out-of-sample day (the Jul 2023 → Oct 2025 holdout with holdout-fit stage 1, plus Nov 2025 → Aug 2026 with the served/candidate models on days they never saw). Other lines and weightings follow, so the verdict can be read for its sensitivity.</p>
<div class="meta"><span>{len(sets)} sets: {esc(", ".join(f"{s['name']} = {s['stage1']} + s2 {s['stage2']}" for s in sets))}</span><span>holdout {esc(w["holdout"][0])} → {esc(w["holdout"][1])}</span><span>since training {esc(w["post"][0])} → {esc(w["post"][1])}</span><span>post-training rain: {esc(", ".join(master.get("input_rules_post") or ["raw record"]))}</span><span>generated {esc(gen)}</span></div></header>
<nav><a href="#verdict">Verdict</a><a href="#rank">Ranking</a><a href="#sens">Sensitivity</a><a href="#cost">Cost vs line</a><a href="#s1">Stage 1</a><a href="#cal">Calibration</a><a href="#zones">Zones</a><a href="#effects">Isolated effects</a><a href="#volume">Volume</a><a href="#bact">Bacteria</a><a href="#recs">Recommendations</a></nav>

<section id="verdict"><div class="verdict">King under the primary setting: <b>{esc(king["label"])}</b> — stage 1 {esc(king["stage1"])} with stage 2 {esc(king["stage2"])}: cost {primary_rows[0]["cost"]:.0f} vs {primary_rows[1]["cost"]:.0f} for the runner-up ({esc(primary_rows[1]["set"]["label"])}) and {next(r["cost"] for r in primary_rows if r["set"]["served"]):.0f} for the served {esc(served_row["set"]["stage1"])}. {primary_rows[0]["tp"]} of {primary_rows[0]["tp"]+primary_rows[0]["fn"]} discharge days caught, {primary_rows[0]["fp"]} false alarms of which {primary_rows[0]["clean"]} on clean days.</div>
<div class="narr">{narrative}</div></section>

<section id="rank"><h2>Ranking — 50% line, {esc(PRIMARY[0])}</h2>
<p class="lead">Cost = {PRIMARY[1]:g} × false alarms + {PRIMARY[2]:g} × missed discharge days, summed over the four zones, against the discharge label (a CIWQS-reported discharge that day from an outfall posting one of the zone's beaches). "After a real discharge" = false alarms within 7 days of a real posting, the composition holding risk up while the beach is likely still dirty; "clean days" is the model's own cost. MG caught = reported discharge volume on caught days.</p>
<h3>Out of sample — holdout + since training (primary)</h3>{primary_html}
<h3>Since training only — Nov 2025 → Aug 2026, the truest test</h3>{post_html}
<h3>Holdout only — Jul 2023 → Oct 2025</h3>{hold_html}
<h3>Same as primary, counting clean-day false alarms only</h3>{clean_html}
<h3>Primary weighting at the 25% line instead</h3>{line25_html}
<h3>Cheapest line per set — overall and per zone (out of sample, primary weighting)</h3>
<p class="fine">Where each set's cost bottoms out on the 5–75% grid. A zone whose cheapest line differs from the others is a case for per-zone alarm lines.</p>{cheap_html}</section>

<section id="sens"><h2>Sensitivity — does the winner depend on the weighting or the line?</h2>
<p class="lead">Rank (and cost) of every set under each combination, out of sample. A verdict that holds across this table is robust; one that flips is a judgment call.</p>{"".join(sens)}</section>

<section id="cost"><h2>Cost against the alarm line</h2>
<p class="lead">Total cost over all zones as the line moves from 5% to 75% (out of sample). The dashed vertical is the 50% line. Left: all false alarms count. Right: only clean-day false alarms count.</p>
<div class="grid g2"><div class="card">{cost_chart}</div><div class="card">{cost_chart_clean}</div></div></section>

<section id="s1"><h2>Stage 1 — ranking quality per basin</h2>
<p class="lead">PR-AUC is threshold-free: how well each basin model puts discharge days above quiet days. Holdout-fit models on the holdout; the served/candidate models since training. Brier grades the percentages themselves.</p>
{pr_bars}{"".join(s1_rows)}</section>

<section id="cal"><h2>Calibration — does 50% mean 50%?</h2>
<p class="lead">Stage 1 probability bands pooled over the four basins, out of sample: the share of basin-days in each band that actually discharged, against the band's mean prediction. The dashed diagonal is perfect calibration; dot size = basin-days.</p>
{cal_chart}{"".join(cal_table)}</section>

<section id="zones"><h2>Zones — what subscribers would have experienced at 50%</h2>
<p class="lead">False alarms per zone and set, split into the tail after a real discharge (lighter) and clean days (solid); the label on each bar is discharge days caught. Below, each zone's full operating curve along the line grid.</p>
{fa_bars}<div class="grid g2" style="margin-top:14px">{ops}</div></section>

<section id="effects"><h2>Isolated effects</h2>
<p class="lead">Change one layer, hold the other: what stage 2 v2 does to each stage 1, and what the weights stage 1 does under each stage 2. Positive caught is good; positive false alarms is bad.</p>{effect_s2}<br>{effect_s1}</section>

<section id="volume"><h2>Volume-weighted catch</h2>
<p class="lead">A 48 MG discharge and a 0.1 MG trickle count the same in "caught". Weighting by reported volume asks the question subscribers care about: how much of the sewage that reached the water fell on days we called at or above 50%.</p>{vol_bars}</section>

<section id="bact"><h2>The bacteria view</h2>
<p class="lead">Against sampled days only (roughly weekly): elevated-sample days with risk at or above 50%, and alarms on days the samples came back clean. Dry-weather dirtiness (the East's 35% baseline) shows up here as misses no rain model can reach.</p>{"".join(bact)}</section>

<section id="recs"><h2>Recommendations</h2><div class="narr" id="recs-body">__RECS__</div></section>
<footer>Generated {esc(gen)} by <code>features/forecast/src/models/report_models.py</code> from the scorecard artifacts of every model set. <a href="/forecast">Back to the forecast</a> · <a href="/reports/2026-09_forecast_stage2_explorer.html">Stage 2 explorer</a></footer>
</div></body></html>"""


def summary_tables(sets, evals) -> None:
    """Console dump used to write the narrative."""
    se = list(zip(sets, evals))
    for wname in ("holdout", "post", "oos"):
        print(f"\n=== {wname} ===")
        for thr in (0.25, 0.5):
            print(f"-- {int(thr*100)}% line: set: caught/posted, FP (tail/clean), MG caught/total | cost (FA:miss) 1:2, 1:1, 2:1 | clean-only 1:2")
            for s, e in se:
                W = e["windows"][wname]
                tp = sum(W["confusion"][z][str(thr)]["vs_discharge_posting"]["tp"] for z in ZONE_ORDER); fn = sum(W["confusion"][z][str(thr)]["vs_discharge_posting"]["fn"] for z in ZONE_ORDER)
                fp = sum(W["confusion"][z][str(thr)]["vs_discharge_posting"]["fp"] for z in ZONE_ORDER); tl = sum(W["tail"][z][str(thr)] for z in ZONE_ORDER)
                mgc = sum(W["volume"][z][str(thr)]["caught_mg"] for z in ZONE_ORDER); mgt = sum(W["volume"][z][str(thr)]["total_mg"] for z in ZONE_ORDER)
                c21 = cost(W["confusion"], W["tail"], thr, 2, 1)["total"]; c11 = cost(W["confusion"], W["tail"], thr, 1, 1)["total"]; c12 = cost(W["confusion"], W["tail"], thr, 1, 2)["total"]; cc = cost(W["confusion"], W["tail"], thr, 1, 2, True)["total"]
                per = "  ".join(f"{z[:5]} {W['confusion'][z][str(thr)]['vs_discharge_posting']['tp']}/{W['confusion'][z][str(thr)]['vs_discharge_posting']['tp']+W['confusion'][z][str(thr)]['vs_discharge_posting']['fn']}+{W['confusion'][z][str(thr)]['vs_discharge_posting']['fp']}" for z in ZONE_ORDER)
                print(f"   {s['name']:14} {tp}/{tp+fn}, FP {fp} ({tl}/{fp-tl}), MG {mgc:.0f}/{mgt:.0f} | {c12:.0f}, {c11:.0f}, {c21:.0f} | {cc:.0f}   [{per}]")
        print("-- stage 1 PR-AUC / Brier per basin:")
        for s, e in se:
            if s["stage2"] != "v1":
                continue
            print(f"   {s['stage1']:9} " + "  ".join(f"{k[:5]} {e['windows'][wname]['basins'][k]['pr_auc']:.3f}/{e['windows'][wname]['basins'][k]['brier']:.4f}" for k in BASIN_ORDER))
        print("-- calibration (pooled basins):", [(c['band'], f"{c['hits']}/{c['n']}") for c in evals[0]['windows'][wname]['calibration']], " logit:", [(c['band'], f"{c['hits']}/{c['n']}") for c in next(e for s, e in se if s['stage1'] == 'logit_v1' and s['stage2'] == 'v1')['windows'][wname]['calibration']])


def main(write: bool = True, narrative_file: Path | None = None) -> None:
    sets = load_sets()
    zvol = zone_volumes(sets[0]["sc"])
    evals = [evaluate(s, zvol) for s in sets]
    summary_tables(sets, evals)
    if write:
        narr = (narrative_file or (HERE / "report_models_narrative.html")).read_text() if (narrative_file or (HERE / "report_models_narrative.html")).exists() else "<p class='fine'>Narrative not written yet.</p>"
        parts = narr.split("<!-- RECOMMENDATIONS -->")
        html_out = build_html(sets, evals, parts[0]).replace("__RECS__", parts[1] if len(parts) > 1 else "")
        OUT.write_text(html_out)
        print(f"\nwrote {OUT.relative_to(REPO)}: {OUT.stat().st_size/1e6:.2f} MB")


if __name__ == "__main__":
    main(write="--no-write" not in sys.argv)
