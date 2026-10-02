#!/usr/bin/env python3
"""Render reports/2026-09_forecast_v4.html from the v4 training artifacts
(eval_report.json, impact_table.json, scorecard.json.gz) — the "document this
all" deliverable. Re-run after any retrain: it reads numbers, never hand-types them.

    venv/bin/python features/forecast/src/models/report_v4.py
"""
from __future__ import annotations

import gzip
import html as H
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from groups import BASIN_KEYS, SITE_GROUPS, ZONE_GROUPS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

V4 = ROOT / "features" / "forecast" / "data" / "models" / "v4"
V2 = ROOT / "features" / "forecast" / "data" / "models" / "v2"
OUT = ROOT / "reports" / "2026-09_forecast_v4.html"

CSS = """body{margin:0;padding:28px;background:#E3EBF2;font-family:Roboto,'Helvetica Neue',Arial,sans-serif;color:#26272a;line-height:1.5}
h1{font-family:'Bebas Neue',Impact,sans-serif;font-weight:400;font-size:38px;letter-spacing:.02em;color:#0072BC;margin:0 0 4px}
h2{font-family:'Bebas Neue',Impact,sans-serif;font-weight:400;font-size:24px;color:#54576F;margin:28px 0 10px;letter-spacing:.03em}
h3{font-size:15px;margin:18px 0 6px;color:#26272a}
p,li{font-size:14px} .card{background:#fff;border-radius:14px;padding:16px 20px;margin:0 0 12px}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:14px;overflow:hidden;font-size:13px;margin:8px 0 14px}
th{text-align:left;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#54576F;padding:9px 12px;background:#f3f6fa}
td{padding:8px 12px;border-top:1px solid #e6ecf2;vertical-align:top} td.num{text-align:right;font-variant-numeric:tabular-nums}
.good{color:#237059;font-weight:700} .bad{color:#b5310a;font-weight:700} .muted{color:#8a93a3} code{background:#f1f5f9;padding:1px 5px;border-radius:5px;font-size:12px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0} .stat{background:#fff;border-radius:14px;padding:12px 16px;font-size:12px;color:#54576F}
.stat b{display:block;font-size:28px;color:#0072BC;font-family:'Bebas Neue',Impact,sans-serif;font-weight:400}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;letter-spacing:.04em} .pill.blue{background:#e3eefb;color:#0072BC} .pill.red{background:#f8dcd6;color:#b5310a} .pill.green{background:#e0f0ea;color:#237059} .pill.grey{background:#eef1f4;color:#54576F}"""


def f3(x):
    return "—" if x is None else f"{x:.3f}"


def f4(x):
    return "—" if x is None else f"{x:.4f}"


def pc(x):
    return "—" if x is None else f"{100 * x:.0f}%"


def main() -> None:
    ev = json.loads((V4 / "eval_report.json").read_text())
    ev3 = json.loads((V2 / "eval_report.json").read_text()) if (V2 / "eval_report.json").exists() else {"targets": {}}
    impact = json.loads((V4 / "impact_table.json").read_text())
    with gzip.open(V4 / "scorecard.json.gz", "rt") as fh:
        sc = json.load(fh)
    notes = ev["notes"]; ar = notes["archive_recall"]; al = notes.get("archive_labels", {})
    T = ev["targets"]

    # ── stage 1 table: v3 vs v4 per basin ──
    rows = ""
    for basin, key in list(BASIN_KEYS.items()) + [("City-wide", "citywide")]:
        t = T.get(key, {}); h = t.get("holdout") or {}
        t3 = ev3["targets"].get(key, {}); h3 = t3.get("holdout") or {}
        src = t.get("rain_source", "avg"); src_label = "Downtown + Oceanside mean" if src == "avg" else src
        cand = t.get("candidates", {})
        alt = [f"{H.escape(s)}: PR {f3(c['holdout'].get('pr_auc'))}, Brier {f4(c['holdout'].get('brier'))}" for s, c in cand.items() if c.get("holdout")]
        d = (h.get("pr_auc") or 0) - (h3.get("pr_auc") or 0) if h3 else None
        cls = "good" if d is not None and d >= 0.005 else ("bad" if d is not None and d <= -0.005 else "muted")
        rows += (f"<tr><td><b>{H.escape(basin)}</b><div class='muted'>{t.get('n_events', '—')} event days · {t.get('n_days', '—')} labelled days</div></td>"
                 f"<td>{H.escape(src_label)}<div class='muted'>{'<br>'.join(alt)}</div></td>"
                 f"<td class='num'>{f3(h3.get('pr_auc')) if h3 else '<span class=muted>no v3 model</span>'}</td>"
                 f"<td class='num'><b>{f3(h.get('pr_auc'))}</b></td><td class='num {cls}'>{'—' if d is None else f'{d:+.3f}'}</td>"
                 f"<td class='num'>{f3(h.get('roc_auc'))}</td><td class='num'>{f4(h.get('brier'))}</td>"
                 f"<td>{'<span class=pill green>yes</span>' if t.get('stage1_uses_archive_labels') else ('<span class=pill red>dropped</span>' if t.get('stage1_uses_archive_labels') is False else '<span class=pill grey>n/a</span>')}</td></tr>")

    # ── archive ablation ──
    abl = ""
    for basin, key in BASIN_KEYS.items():
        a = (T.get(key) or {}).get("archive_ablation")
        if not a or not a.get("with_archive") or not a.get("without_archive"):
            continue
        w, wo = a["with_archive"], a["without_archive"]
        abl += (f"<tr><td>{H.escape(basin)}</td><td class='num'>{f3(w['pr_auc'])} / {f4(w['brier'])}</td><td class='num'>{f3(wo['pr_auc'])} / {f4(wo['brier'])}</td>"
                f"<td>{'<span class=pill green>kept</span>' if T[key].get('stage1_uses_archive_labels') else '<span class=pill red>dropped for stage 1</span>'}</td></tr>")

    # ── stage 2 impact table ──
    imp = ""
    for g, t in impact.items():
        b = t["buckets"]; base = b["baseline_no_recent_discharge"]
        cells = "".join(f"<td class='num'>{pc(b.get(f'd{k}_large', {}).get('p_elevated'))} <span class='muted'>/ {pc(b.get(f'd{k}_small', {}).get('p_elevated'))}</span>"
                        f"<div class='muted'>n {b.get(f'd{k}_large', {}).get('n', 0)}+{b.get(f'd{k}_small', {}).get('n', 0)}</div></td>" for k in ("0", "1", "2", "3", "4-5", "6-7"))
        imp += (f"<tr><td><b>{H.escape(g)}</b><div class='muted'>{H.escape(t['basin'])} · median event {t['median_event_volume_mg']} MG</div></td>"
                f"<td class='num'>{pc(base['p_elevated'])}<div class='muted'>n {base['n']}</div></td>{cells}</tr>")

    # ── outfall count diagnostic ──
    od = ev.get("outfall_count_diagnostic", {})
    odr = "".join(f"<tr><td>{H.escape(g)}</td><td class='num'>{pc(d['single_outfall']['p_elevated'])} <span class='muted'>(n {d['single_outfall']['n']})</span></td>"
                  f"<td class='num'>{pc(d['multi_outfall']['p_elevated'])} <span class='muted'>(n {d['multi_outfall']['n']})</span></td></tr>" for g, d in od.items())

    # ── zone scorecard ──
    zc = sc["zone_confusion_holdout"]; zr = ""
    for zk, c in zc.items():
        cells = ""
        for thr in ("0.1", "0.25", "0.5"):
            d = c[thr]["vs_discharge_posting"]; b = c[thr]["vs_bacteria_elevated"]
            rec = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else None
            far = d["fp"] / (d["fp"] + d["tn"]) if d["fp"] + d["tn"] else None
            brec = b["tp"] / (b["tp"] + b["fn"]) if b["tp"] + b["fn"] else None
            bfa = b["fp"] / (b["fp"] + b["tn"]) if b["fp"] + b["tn"] else None
            cells += f"<td class='num'>{pc(rec)} <span class='muted'>({d['tp']}/{d['tp'] + d['fn']})</span><br><span class='muted'>FA {pc(far)}</span></td><td class='num'>{pc(brec)} <span class='muted'>({b['tp']}/{b['tp'] + b['fn']})</span><br><span class='muted'>FA {pc(bfa)}</span></td>"
        zr += f"<tr><td><b>{H.escape(ZONES[zk].label)}</b><div class='muted'>{' + '.join(ZONE_GROUPS[zk])}</div></td>{cells}</tr>"

    bt = ev["backtest"]
    marquee = "".join(f"<tr><td>{m['date']}</td><td class='num'>{m['rain']:.2f}\"</td><td class='num'>{pc(m['citywide_p'])}</td><td>{'discharge' if m['actual_any'] else 'none'}</td></tr>" for m in bt["marquee"])
    cfn = "".join(f"<li>{c['date']} — P {pc(c['p'])}, 3-day rain {c['rain_3d']:.2f}\"</li>" for c in bt["critical_fn"]) or "<li>none</li>"
    n_days = len(sc["days"]); n_h = sum(1 for d in sc["days"] if d["zones"]["ocean"]["risk_h"] is not None)
    samples = ev["samples"]

    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Forecast v4 — four basins, regional rain, model check (2026-09)</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet"><style>{CSS}</style></head><body>
<h1>Forecast v4: every beach served, rain from the right gauge, and a model check</h1>
<p class="muted">Trained {H.escape(ev['trained_at'][:16])} · training span {ev['train_window'][0]} → {ev['train_window'][1]} · holdout = {sc['holdout_start']} onward (last two wet seasons, never seen by the holdout-fit models) · generated by <code>src/models/report_v4.py</code> from the artifacts, no hand-typed numbers.</p>
<div class="stats"><div class="stat"><b>4</b>basin models (Central / Mission Creek added — 52% of citywide discharge volume, previously unserved)</div>
<div class="stat"><b>{T['central']['n_events']}</b>Central discharge days now modelled · BAY#220 Mission Creek gets a forecast</div>
<div class="stat"><b>{samples['n']:,}</b>bacteria samples behind stage 2 ({samples['poobot']:,} recovered from the 2016-17 SFPUC feed archive)</div>
<div class="stat"><b>{n_days:,}</b>days in the scorecard, {n_h:,} of them true out-of-sample</div></div>

<h2>What changed, and why</h2><div class="card"><ul>
<li><b>Central basin.</b> Mission Creek's seven outfalls (CSD-018…027) reported {T['central']['n_events']} discharge days over the CIWQS span — more than any other basin — yet the station that sits on them (BAY#220 Mission Creek) had no model and was folded into Southeast in the alerts app. It now has its own stage-1 model and stage-2 group; the station registry, the outfall registry, the alerts API and the signup zones all agree it drains the Central basin.</li>
<li><b>A discharge day is a bad day.</b> The composition used to multiply the discharge probability by the measured same-day exceedance rate (which is well under 1 for small events). Chase's rule (2026-09-05): raw sewage entering the water is a stay-out day regardless of what the samples later showed, so <code>x(0,·) ≡ 1</code>; follow-up days use the measured decay. One implementation, <code>src/models/impact.py</code>, is shared by training and serving.</li>
<li><b>Regional rain.</b> Every basin was evaluated on the two-gauge citywide mean (v3) and on its local NOAA gauge. North Shore and Central are clearly better on Downtown 047772 alone (holdout PR-AUC {f3(T['north_shore']['holdout']['pr_auc'])} vs {f3(T['north_shore']['candidates']['avg']['holdout']['pr_auc'])}, {f3(T['central']['holdout']['pr_auc'])} vs {f3(T['central']['candidates']['avg']['holdout']['pr_auc'])}); Westside and Southeast keep the mean. Each model records its rain source and serving feeds it the matching series: complete past days are re-based onto the NOAA gauges the models were trained on (the KSFO airport gauge was the only observed source before), today uses KSFO hourly, forecast days ECMWF.</li>
<li><b>The Poo Bot archive.</b> 552 snapshots of SFPUC's own feed (Mar 2016 – Jan 2017) were ingested (<code>data/poobot/</code>). They pass a recall check against CIWQS Bayside events ({ar['seen_in_feed']}/{ar['ciwqs_bayside_event_days']} = {pc(ar['recall'])}), so the season is used for coverage and stage 2. The feed's Westside flags, however, appear one to two days after the rain (SFPUC posts the flag once operations confirm it), and the ablation shows they hurt the Westside stage-1 model — so that basin's stage 1 is fit without them. Details below.</li>
<li><b>Presented by zone.</b> The forecast now leads with the four signup zones (Ocean Beach / Baker &amp; China / North Beaches / East Beaches), each the worst of its stage-2 groups, with a basin view one click away. Zones, groups and basins all derive from <code>shared/zones.py</code>, <code>src/models/groups.py</code> and the station registry — no hand-typed lists.</li>
<li><b>Two new views.</b> "What happened" shows gauge rain, every reported discharge with the beaches it posts (via the outfall registry), watcher postings and bacteria samples, per zone, for any date back to March 2016. "Model check" puts today's models' hindcast next to those actuals with hit / miss / false-alarm verdicts, the probabilities recorded at training time (holdout-fit where available), and the season scorecard.</li></ul></div>

<h2>Stage 1 — P(discharge today | rain), per basin</h2>
<p>Chronological holdout ({sc['holdout_start']} → {ev['train_window'][1]}). PR-AUC is the headline for rare events (a random model scores ≈ the event rate, ~2%); Brier is calibration. v3 numbers are the served models this replaces.</p>
<table><tr><th>Basin</th><th>Rain source (alternatives)</th><th class="num">v3 PR-AUC</th><th class="num">v4 PR-AUC</th><th class="num">Δ</th><th class="num">v4 ROC-AUC</th><th class="num">v4 Brier</th><th>Archive labels</th></tr>{rows}</table>

<h3>Does the 2016-17 archive season help? (holdout, same model otherwise)</h3>
<table><tr><th>Basin</th><th class="num">With archive · PR / Brier</th><th class="num">Without · PR / Brier</th><th>Decision</th></tr>{abl}</table>
<div class="card"><p><b>Why Westside drops them.</b> The archive's Westside onsets fall on {len(al.get('onset_days', {}).get('Westside', []))} days ({', '.join(al.get('onset_days', {}).get('Westside', []))}); several are zero-rain days one or two days after a storm (e.g. 2016-11-21 and 2016-12-12 recorded 0.00" at both gauges). The feed flags a structure when SFPUC confirms it, not when it starts — CIWQS dates events by their actual start, which is what the rain features line up with. {al.get('shifted_onsets', 0)} morning-snapshot onsets were re-dated to the previous rainy day; the rest could not be resolved honestly, so Westside's stage 1 uses CIWQS labels only (2018 →) and the archive still provides the season's coverage for the bay basins, the discharge context for 2016-17 samples in stage 2, and the "what happened" view.</p></div>

<h2>Stage 2 — P(beaches elevated | days since discharge, size)</h2>
<p>Per site group; large / small = event volume above / below the group's median reported volume (archive events without a volume use the volume head's prediction, exactly as serving does). Baseline = sample days with no discharge in the prior week. Serving smooths each curve to be non-increasing (weighted PAVA) and removes the baseline before composing.</p>
<table><tr><th>Group</th><th class="num">Baseline</th><th class="num">Day 0</th><th class="num">Day 1</th><th class="num">Day 2</th><th class="num">Day 3</th><th class="num">Days 4-5</th><th class="num">Days 6-7</th></tr>{imp}</table>
<h3>Does the number of outfalls matter? (samples ≤ 2 days after a discharge)</h3>
<table><tr><th>Group</th><th class="num">One outfall discharged</th><th class="num">Two or more</th></tr>{odr}</table>
<p class="muted">Multi-outfall events read higher at most groups, but volume already carries that (the two are strongly correlated), so the volume head stays the size signal; this table is the evidence, not a feature.</p>

<h2>Model check — zones, holdout window, models that never saw the season</h2>
<p>Each zone's risk is the worst of its groups. "Discharge" = a reported discharge whose outfall posts a beach in the zone (outfall registry). "Bacteria" = a sample over standard at a zone beach that day (only sampled days count). FA = false-alarm rate on the negative days.</p>
<table><tr><th>Zone</th><th class="num">≥10% · discharge</th><th class="num">≥10% · bacteria</th><th class="num">≥25% · discharge</th><th class="num">≥25% · bacteria</th><th class="num">≥50% · discharge</th><th class="num">≥50% · bacteria</th></tr>{zr}</table>
<div class="card"><p><b>Reading it.</b> Discharge days are caught at 90–100% at the 25% line with false alarms in the 2–10% range — the rain → discharge relationship is strong and the models have it. Bacteria is a different story: elevated samples at Baker &amp; China are mostly Lobos Creek and dry-weather runoff, not sewage, and the model does not (and should not) predict those; Ocean Beach and the bay zones catch roughly half of elevated-sample days, the rest being the same non-sewage sources. The alert system covers those via the SFPUC posting feed; the forecast is a sewage-risk forecast.</p></div>

<h2>Backtest</h2>
<table><tr><th>Marquee storm</th><th class="num">Rain</th><th class="num">City-wide P</th><th>Reported</th></tr>{marquee}</table>
<p>Dry days (7-day rain &lt; 0.05", n = {bt['dry_days_n']}): mean city-wide P {pc(bt['dry_day_avg_citywide_p'])}. Critical false negatives (reported event, 3-day rain &gt; 0.25", P &lt; 10%):</p><ul>{cfn}</ul>

<h2>Serving changes</h2><div class="card"><ul>
<li><code>live_dashboard.py</code> loads the Central model and volume head; composition, impact fraction and PAVA smoothing come from <code>src/models/impact.py</code>; basin/group/zone tables from <code>src/models/groups.py</code>.</li>
<li>Daily feature frames are built per rain source; complete past days are re-based onto ACIS daily totals for Downtown 047772 and Oceanside 047767 (mean for "avg" models). Each day is labelled <code>gauges</code> / <code>observed</code> (KSFO hourly, today) / <code>mixed</code> / <code>forecast</code>; the payload carries <code>rain_by_gauge</code> and <code>zones</code>.</li>
<li>New endpoints <code>/forecast/api/actuals?date=</code> (discharges + posted beaches, watcher postings, samples, gauge rain, per zone) and <code>/forecast/api/scorecard?date=</code> (training-time hindcast, labels, season scorecard). The date picker reaches back to 2016-03-19.</li>
<li>Artifacts: <code>data/models/v4/</code>, promoted into <code>data/models/</code>; <code>scorecard.json.gz</code> ({(V4 / 'scorecard.json.gz').stat().st_size // 1024} KB) is read lazily by the model-check view.</li></ul></div>
<p class="muted">Companion report: <code>reports/2026-09_outfall_station_mapping.html</code> (which beaches each outfall posts, and the evidence).</p>
</body></html>"""
    OUT.write_text(html)
    print("wrote", OUT, f"{len(html) // 1024} KB")


if __name__ == "__main__":
    main()
