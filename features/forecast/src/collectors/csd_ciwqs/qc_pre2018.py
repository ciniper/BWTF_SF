"""Quality checks for the pre-modern discharge record (data/csd/pre2018/), written to
``qc_summary.json`` there; the ledger's "How reporting changed" page reads it.

Each check compares the transcribed record with something produced independently:
  1. totals   every Westside month's rows add up to the total SFPUC printed for it
  2. counts   the number of discharges SFPUC itself stated per month (cover letters,
              annual reports) against discharge days in the tables
  3. rain     every discharge day against the two NOAA gauges (ACIS 047772 Downtown,
              047767 Oceanside): a discharge with no rain the day before, of, or after is flagged
  4. postings Westside discharges against the State's BeachWatch sewage (CSO) postings
  5. sampling Westside Ocean Beach discharges against SFPUC's follow-up sampling at
              Pacheco, Vicente and Fort Funston (sampled only after discharges from 2004)
  6. poobot   the 2016-17 volunteer archive of SFPUC's live feed
  7. esmr     the state's tabular eSMR flow series: same days? same volumes?

Run from the repo root after build_pre2018.py:
  python features/forecast/src/collectors/csd_ciwqs/qc_pre2018.py
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parents[3] / "data"
P = DATA / "csd" / "pre2018"
DRY_IN = 0.05            # a discharge day is "dry" when no gauge saw this much on the day before, of, or after
OB_OUTFALLS = {"CSD-001", "CSD-002", "CSD-003"}   # Lake Merced, Vicente, Lincoln: the Ocean Beach outfalls


def rd(path: Path) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def d(s: str) -> date:
    return date.fromisoformat(s[:10])


def main() -> dict:
    west = rd(P / "westside_daily_2011-03_2017-12.csv")
    wcov = rd(P / "westside_monthly_coverage_2011-03_2017-12.csv")
    bay = rd(P / "bayside_legacy_2011-03_2016-09.csv")
    bcov = rd(P / "bayside_legacy_monthly_coverage_2011-03_2016-09.csv")
    stated = rd(P / "stated_monthly_counts_2011-2017.csv")

    def wet(r):   # a row that discharged something measurable, or an unmeasured discharge
        return r["volume_flag"] == "not_measured" or float(r["volume_MG"] or 0) > 0
    wdays = sorted({r["event_date"] for r in west})
    bdays = sorted({r["date"] for r in bay})
    out: dict = {"generated_from": "data/csd/pre2018 + raw/beachwatch/poobot/stardb files in the repo"}

    # 1. totals
    mism = [dict(month=f"{c['year']}-{int(c['month']):02d}", printed=float(c["month_total_MG"]), rows=float(c["rows_volume_sum_MG"]))
            for c in wcov if c["month_total_MG"] not in ("", None) and c["status"] != "basin_days_esmr"
            and abs(float(c["month_total_MG"]) - float(c["rows_volume_sum_MG"])) > 0.02]
    out["totals"] = dict(months_checked=sum(c["month_total_MG"] not in ("", None) for c in wcov), mismatches=mism,
                         explained={"2014-12": "the printed month total leaves out 11 Dec, when three outfalls were printed NA; "
                                               "without that day the rows sum to the printed 96.58"})

    # 2. stated counts (Westside: days in the table vs SFPUC's monthly count)
    by_m = defaultdict(set)
    for r in west:
        by_m[r["event_date"][:7]].add(r["event_date"])
    agree, near, off, diffs = 0, 0, 0, []
    for s in stated:
        if not s["plant"].startswith("Oceanside"):
            continue
        ym = f"{s['year']}-{int(s['month']):02d}"
        if ym < "2011-03":
            continue
        n, days = int(s["stated_count"]), sorted(by_m.get(ym, ()))
        runs = sum(1 for i, x in enumerate(days) if i == 0 or (d(x) - d(days[i - 1])).days > 1)
        vol_days = len({r["event_date"] for r in west if r["event_date"][:7] == ym and wet(r)})
        if n in (len(days), runs, vol_days):
            agree += 1
        else:
            (near := near + 1) if abs(n - len(days)) <= 1 else (off := off + 1)
            diffs.append(dict(month=ym, stated=n, table_days=len(days), source=s["source"]))
    b_disagree = [dict(month=f"{c['year']}-{int(c['month']):02d}", parsed=c["counts_parsed"], stated=c["counts_stated"])
                  for c in bcov if c["counts_agree"] != "True"]
    out["counts"] = dict(westside=dict(months=agree + near + off, agree=agree, within_one=near, off_by_more=off, differences=diffs),
                         bayside=dict(months=len(bcov), agree=len(bcov) - len(b_disagree), differences=b_disagree))

    # 3. rain
    rain = defaultdict(float)
    for r in rd(P / "acis_daily_rain_2011-2017.csv"):
        try:
            rain[d(r["date"])] = max(rain[d(r["date"])], float(r["pcpn_in"]))
        except ValueError:            # "M" (missing) or "T" (trace)
            pass

    def window(x: str) -> float:
        return max(rain[d(x) + timedelta(k)] for k in (-1, 0, 1))
    rain_out = {}
    for name, days, rows, dkey in (("westside", wdays, west, "event_date"), ("bayside", bdays, bay, "date")):
        dry = []
        for x in days:
            if window(x) < DRY_IN:
                what = sorted({f"{r['outfall_id']} {r.get('volume_MG') or (r.get('discharge_hours', '') + ' h')}"
                               for r in rows if r[dkey] == x})
                dry.append(dict(date=x, rain_window_in=window(x), rows=what))
        rain_out[name] = dict(discharge_days=len(days), with_rain=len(days) - len(dry), dry=dry)
    out["rain"] = dict(threshold_in=DRY_IN, gauges="ACIS 047772 SF Downtown + 047767 SF Oceanside, wetter of the two", **rain_out)

    # 4. BeachWatch sewage postings, Westside
    posted = defaultdict(set)
    for r in rd(DATA / "beachwatch" / "sf_posted_zone_days.csv"):
        if r["cause_class"] == "cso":
            posted[r["zone"]].add(d(r["date"]))
    zone_of = lambda oid: "ocean" if oid in OB_OUTFALLS else "baker_china" if oid in {"CSD-005", "CSD-006", "CSD-007", "CSD-004"} else None
    checked = followed = 0
    unposted = []
    for x in wdays:
        zones = {zone_of(r["outfall_id"]) for r in west if r["event_date"] == x and wet(r)} - {None}
        if not zones:
            continue
        checked += 1
        if any(d(x) + timedelta(k) in posted[z] for z in zones for k in (0, 1, 2)):
            followed += 1
        else:
            unposted.append(dict(date=x, zones=sorted(zones),
                                 MG=round(sum(float(r["volume_MG"] or 0) for r in west if r["event_date"] == x), 3)))
    any_post = defaultdict(set)   # a posting for any cause, to tell "posted for another reason" from "never posted"
    for r in rd(DATA / "beachwatch" / "sf_posted_zone_days.csv"):
        any_post[r["zone"]].add(d(r["date"]))
    for u in unposted:
        u["posted_for_another_cause"] = any(d(u["date"]) + timedelta(k) in any_post[z] for z in u["zones"] for k in (0, 1, 2))
    by_year = defaultdict(lambda: [0, 0])
    for x in wdays:
        if {zone_of(r["outfall_id"]) for r in west if r["event_date"] == x and wet(r)} - {None}:
            by_year[x[:4]][0] += 1
            by_year[x[:4]][1] += x not in {u["date"] for u in unposted}
    starts = sorted(x for z in ("ocean", "baker_china") for x in posted[z]
                    if date(2011, 3, 1) <= x <= date(2017, 12, 31) and x - timedelta(1) not in posted[z])
    no_discharge = [x.isoformat() for x in starts if not any(0 <= (x - d(w)).days <= 2 for w in wdays)]
    out["postings"] = dict(discharge_days_checked=checked, posted_within_2_days=followed, not_posted=unposted,
                           by_year={y: dict(discharge_days=v[0], posted=v[1]) for y, v in sorted(by_year.items())},
                           posting_runs_started=len(starts), runs_without_a_discharge_in_the_2_days_before=no_discharge)

    # 5. follow-up sampling at the discharge-only stations
    ob = sorted({r["event_date"] for r in west if r["outfall_id"] in OB_OUTFALLS and wet(r)} |
                {r["event_date"] for r in west if r["outfall_id"] == "" and float(r["volume_MG"] or 0) > 0})
    eps = [e for e in rd(DATA / "sfpuc_stardb_2000_2020" / "westside_csd_followup_sampling_episodes.csv") if "2011-03-01" <= e["first"] <= "2017-12-31"]
    explained = [e for e in eps if any(0 <= (d(e["first"]) - d(x)).days <= 3 for x in ob)]
    out["sampling"] = dict(episodes=len(eps), started_within_3_days_after_an_ocean_beach_discharge=len(explained),
                           unexplained=[dict(first=e["first"], last=e["last"], stations=e["stations"]) for e in eps if e not in explained],
                           ocean_beach_discharge_days=len(ob),
                           followed_by_sampling_within_3_days=sum(any(0 <= (d(e["first"]) - d(x)).days <= 3 for e in eps) for x in ob))

    # 6. Poo Bot (Oct 2016 - Jan 2017)
    pb = sorted({r["date"] for r in rd(DATA / "poobot" / "discharge_onsets.csv") if r["basin"] == "Westside"})
    out["poobot"] = dict(onsets=len(pb), within_2_days_of_a_table_day=sum(any(abs((d(p) - d(x)).days) <= 2 for x in wdays) for p in pb),
                         span=[pb[0], pb[-1]] if pb else None)

    # 7. the state's tabular eSMR flow series
    es = {r["sampling_date"][:10]: float(r["result"]) for r in rd(P / "transcription" / "esmr_westside_eff_csd_flow_2011-2020.csv")
          if r["calculated_method"] == "NA" and float(r["result"]) >= 0}
    day_mg = defaultdict(float)
    for r in west:
        day_mg[r["event_date"]] += float(r["volume_MG"] or 0)
    yrs = {}
    for y in range(2011, 2018):
        pairs = [(x, es[x], round(day_mg[x], 3)) for x in es if x.startswith(str(y)) and x in day_mg and x[:7] != "2012-12"]
        close = sum(abs(a - b) <= max(0.05, 0.1 * b) for _, a, b in pairs)
        yrs[str(y)] = dict(days_in_both=len(pairs), volumes_within_10pct=close)
    out["esmr"] = dict(days_in_esmr=sum(1 for x in es if x < "2018"), days_on_a_table_discharge_day=sum(1 for x in es if x < "2018" and x in day_mg),
                       by_year=yrs, fill_values_dropped=sum(1 for r in rd(P / "transcription" / "esmr_westside_eff_csd_flow_2011-2020.csv")
                                                             if r["calculated_method"] == "NA" and float(r["result"]) < 0))
    out["not_yet_done"] = [
        "A second, independent reading of every hand-read page (26 Westside months, 3 Bayside months), compared row by row.",
        "Spot checks of the parsed Bayside 2013-2016 rows against their pages: monthly counts match, but a shifted row would not show in a count.",
    ]
    with open(P / "qc_summary.json", "w") as fo:
        json.dump(out, fo, indent=1, default=str)
    return out


if __name__ == "__main__":
    o = main()
    print(json.dumps({k: (v if k in ("totals", "poobot") else {kk: vv for kk, vv in v.items() if not isinstance(vv, list)} if isinstance(v, dict) else v)
                      for k, v in o.items()}, indent=1, default=str)[:4000])
