"""How the city's beach testing changed, 2000 → today, computed from the lab data in
the repo — the facts behind the "How testing changed" page (/analysis/testing).

Sources: the SFPUC STARDB export (Jan 2000 – 27 Jul 2020, with method and units) and
the DataSF snapshot the forecast trains on (27 Jul 2020 on; no method column, so its
changes show up as changed indicators and reporting limits). Discharge days come
from the Discharge Ledger's files, for the "sampled more after a discharge" check.

Regenerate after a DataSF snapshot refresh (it rewrites testing_history.json):
    python features/site_analysis/testing_history.py
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "features" / "forecast" / "data"
STARDB = DATA / "sfpuc_stardb_2000_2020" / "sfpuc_beach_bacteria_2000-01_2020-07_normalized.csv"
STARDB_RAW_NAMES = DATA / "sfpuc_stardb_2000_2020" / "station_names.json"
DATASF = DATA / "raw" / "historical_bacteria.csv"
CSD = DATA / "csd"
OUT = Path(__file__).with_name("testing_history.json")
DISCHARGE_ONLY = ("OCEAN#20_SL", "OCEAN#21_SL", "OCEAN#22_SL")   # STARDB page 1: "only sampled following Combined Sewer Discharges"
LABEL = {"ENTERO": "Enterococcus", "COLI_E": "E. coli", "COLI_FECAL": "Fecal coliform", "COLI_TOTAL": "Total coliform"}


def _rows(path: Path) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _span(store: dict, key, day: str) -> None:
    s = store.setdefault(key, {"first": day, "last": day, "n": 0})
    s["first"], s["last"], s["n"] = min(s["first"], day), max(s["last"], day), s["n"] + 1


def build() -> dict:
    from shared.stations import STATIONS
    from shared.zones import ZONES, ZONE_OF_SOURCE

    stardb, datasf = _rows(STARDB), _rows(DATASF)
    days = defaultdict(set)
    for r in stardb + datasf:
        days[r["station"]].add(r["sample_date"][:10])
    through = max(max(v) for v in days.values())
    years = list(range(2000, int(through[:4]) + 1))

    names = json.load(open(STARDB_RAW_NAMES)) if STARDB_RAW_NAMES.exists() else {}

    def station(sid: str) -> dict:
        ds = days[sid]
        c = Counter(d[:4] for d in ds)
        return {"id": sid, "name": STATIONS[sid].name if sid in STATIONS else names.get(sid, sid),
                "first": min(ds), "last": max(ds), "days": [c.get(str(y), 0) for y in years],
                "discharge_only": sid in DISCHARGE_ONLY}
    zones = [{"key": z.key, "label": z.label, "stations": [station(s) for s in z.source_ids if s in days]} for z in ZONES.values()]
    retired = [station(s) for s in sorted(days) if s not in ZONE_OF_SOURCE]

    # what the lab measured: STARDB has method + units; DataSF only the indicator and its printed limits
    methods, limits_stardb, limits_datasf, spans_datasf = {}, {}, {}, {}
    for r in stardb:
        _span(methods, (r["method"], r["units"], r["analyte"]), r["sample_date"][:10])
        if r["value_raw"][:1] in "<>":
            _span(limits_stardb, (r["analyte"], r["value_raw"]), r["sample_date"][:10])
    for r in datasf:
        _span(spans_datasf, r["analyte"], r["sample_date"][:10])
        if r["value_raw"][:1] in "<>":
            _span(limits_datasf, (r["analyte"], r["value_raw"]), r["sample_date"][:10])
    by_method = defaultdict(lambda: {"analytes": [], "first": "9999", "last": "0000", "n": 0})
    for (m, u, a), s in methods.items():
        if s["n"] < 20:          # one stray fecal coliform result in Jan 2000
            continue
        b = by_method[(m, u)]
        b["analytes"].append(LABEL[a]); b["first"] = min(b["first"], s["first"]); b["last"] = max(b["last"], s["last"]); b["n"] += s["n"]
    lab_methods = sorted(({"method": m, "units": u, **v} for (m, u), v in by_method.items()), key=lambda x: x["first"])

    def lims(store):
        out = defaultdict(list)
        for (a, raw), s in sorted(store.items(), key=lambda kv: kv[1]["first"]):
            if s["n"] >= 20:
                out[LABEL[a]].append({"printed": raw, **s})
        return out

    first_indicator = {}
    for r in stardb:
        if r["analyte"] in ("ENTERO", "COLI_E"):
            z = ZONE_OF_SOURCE.get(r["station"])
            k = "ocean" if z in ("ocean", "baker_china") else "bay" if z else None
            if k:
                first_indicator[k] = min(first_indicator.get(k, "9999"), r["sample_date"][:10])

    # E. coli: SFPUC's reports judge it at 400 MPN; the report card uses 235 (shared/standards.py)
    from shared.standards import STANDARDS
    ec = [float(r["value"]) for r in stardb + datasf if r["analyte"] == "COLI_E" and r["value"] not in ("", None)]
    lim = STANDARDS["COLI_E"]["single_sample_max"]
    ecoli = {"results": len(ec), "report_card_limit": lim, "sfpuc_limit": 400,
             "between": sum(lim < v < 400 for v in ec), "at_or_over_400": sum(v >= 400 for v in ec)}

    # sampled more right after a discharge? (discharge days from the ledger's files)
    west, bay = set(), set()
    for r in _rows(CSD / "sf_csd_events.csv"):
        (west if r["facility"].startswith("Oceanside") else bay).add(r["event_date"])
    for r in _rows(CSD / "pre2018" / "westside_daily_2011-03_2017-12.csv"):
        west.add(r["event_date"])
    for r in _rows(CSD / "pre2018" / "bayside_legacy_2011-03_2016-09.csv"):
        bay.add(r["date"])
    lo, hi = date(2011, 3, 1), date.fromisoformat(through)
    alld = [lo + timedelta(i) for i in range((hi - lo).days + 1)]

    def after_set(dis):
        dd = {date.fromisoformat(x) for x in dis}
        return {d for d in alld if any(d - timedelta(k) in dd for k in (1, 2, 3))}
    after = {"west": after_set(west), "bay": after_set(bay)}
    rates = []
    for z in zones:
        sysk = "west" if z["key"] in ("ocean", "baker_china") else "bay"
        for s in z["stations"]:
            start = max(lo, date.fromisoformat(s["first"]))   # count only the days a station existed
            span = {d for d in alld if d >= start}
            sd = {date.fromisoformat(x) for x in days[s["id"]] if start <= date.fromisoformat(x) <= hi}
            a, o = after[sysk] & span, span - after[sysk]
            if len(sd) < 50:
                continue
            rates.append({"id": s["id"], "name": s["name"], "zone": z["label"], "discharge_only": s["discharge_only"],
                          "after_pct": round(100 * len(sd & a) / len(a)), "other_pct": round(100 * len(sd - a) / len(o))})

    routine = [s for z in zones for s in z["stations"] if not s["discharge_only"]]

    def median_days(y0, y1):
        vals = sorted(s["days"][years.index(y)] for s in routine for y in range(y0, y1 + 1) if s["days"][years.index(y)] >= 20)
        return vals[len(vals) // 2] if vals else None

    return {"through": through, "years": years, "zones": zones, "retired": retired, "lab_methods": lab_methods,
            "limits_stardb": lims(limits_stardb), "limits_datasf": lims(limits_datasf),
            "datasf_indicators": {LABEL[a]: s for a, s in spans_datasf.items()},
            "first_enterococcus": first_indicator, "ecoli": ecoli, "after_discharge": rates,
            "after_discharge_window": [lo.isoformat(), through],
            "median_sample_days": {"2000-2002": median_days(2000, 2002), "2004-2019": median_days(2004, 2019),
                                   f"2021-{years[-2]}": median_days(2021, years[-2])}}


def load() -> dict:
    with open(OUT) as fh:
        return json.load(fh)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    data = build()
    with open(OUT, "w") as fo:
        json.dump(data, fo, indent=1)
    print(f"wrote {OUT.name}: through {data['through']}, {sum(len(z['stations']) for z in data['zones'])} stations, "
          f"{len(data['retired'])} retired, medians {data['median_sample_days']}")
