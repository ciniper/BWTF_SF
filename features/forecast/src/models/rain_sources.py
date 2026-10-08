#!/usr/bin/env python3
"""Which rain should the forecast read? The NOAA gauges against three gridded sources over San Francisco (Chase,
2026-10-08: "do a big investigation with your recs on a branch and give a report"). Research: nothing served moves.

    venv/bin/python features/forecast/src/models/rain_sources.py              # the grades, printed
    venv/bin/python features/forecast/src/models/rain_sources.py --report     # + RAIN_SOURCES.md, rain_sources.json
    venv/bin/python features/forecast/src/models/rain_sources.py --report --s2   # + the overflow-model test (~5 min)

The inputs are collectors/rain_grids.py's city cut-outs (AORC, MRMS, AQPI; data/raw/rain_grids/), the two NOAA
gauges (historical_rain*.csv) and ERA5's hourly rain at the city point (the training peaks' source,
hourly_rain_openmeteo*.csv). Four questions, each answered from those files with no clock:

1. **Against the gauges** (``grade``): each source read at a gauge's own grid cell, against that gauge, day by Pacific
   day: the season-total ratio, the correlation, the mean miss on wet days, the critical success index (hits / (hits +
   misses + false alarms)) at 0.1, 0.5 and 1 inch, and the mean miss on two-day totals (a gauge sometimes books a
   storm a day off). A dead gauge's days (rain_features.find_gauge_outages) are left out. As a weather model is
   graded (SWAPS.md), never on overflow catch rate. Neither side is truth: a gauge is one point and can die.
2. **February 2026**, the dead Oceanside gauge: what each source saw at Ocean Beach.
3. **Storm peaks** (``peaks``): the wettest hour and three hours of each storm day, per source, on the city
   rectangle; the training peaks today come from ERA5 at one point.
4. **The overflow model** (``s2_test``, ``--s2``): the live design (its terms and each fold's own pick, its record,
   its C) refit on another source's rain, scored by the term lab on the same basin-days as the live model (S2 oracle:
   rain known; nine seasons and the holdout), paired against it. AORC is the only gridded source long enough
   (MRMS starts Oct 2020, AQPI Oct 2025).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (str(REPO), str(HERE), str(HERE.parent / "collectors")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rain_grids as RG  # noqa: E402
from rain_features import (DAILY_FEATURES, INTENSITY_FEATURES, add_daily_features, find_gauge_outages,  # noqa: E402
                           hourly_intensity, wind_rain_features)

RAW = FORECAST / "data" / "raw"
REPORT = FORECAST / "RAIN_SOURCES.md"
OUT_JSON = FORECAST / "data" / "models" / "rain_sources.json"
TZ = RG.TZ
GAUGES = {"SF Downtown": "downtown_gauge", "SF Oceanside": "oceanside_gauge"}
WINDOWS = {   # name → (start, end, words): Pacific days
    "aqpi_season": ("2025-10-01", "2026-08-30", "AQPI's season, Oct 2025 – Aug 2026"),
    "oct_dec_2025": ("2025-10-01", "2025-12-31", "Oct – Dec 2025, when all four overlap"),
    "mrms_record": ("2020-10-15", "2025-06-30", "five seasons, Oct 2020 – Jun 2025"),
    "nine_seasons": ("2016-10-01", "2025-06-30", "the nine scoring seasons, Oct 2016 – Jun 2025"),
}
WINDOW_SOURCES = {"aqpi_season": ("aqpi", "mrms"), "oct_dec_2025": ("aqpi", "aorc", "mrms"),
                  "mrms_record": ("aorc", "mrms"), "nine_seasons": ("aorc",)}   # the sources that span each window
THRESH = (0.1, 0.5, 1.0)
STORM_IN = 0.5                     # a storm day: either gauge (or, when one is dead, AQPI's city mean) at 0.5″ or more


# ── loading ─────────────────────────────────────────────────────────────────

def gauges() -> tuple:
    """(daily [SF Downtown, SF Oceanside] from 2011, the same with each dead gauge's run masked, the runs)."""
    parts = []
    for name in ("historical_rain_2011-2015.csv", "historical_rain.csv"):
        g = pd.read_csv(RAW / name, dtype={"rain_station": str}, parse_dates=["date"])
        parts.append(g.pivot_table(index="date", columns="rain_station_name", values="precip_inches", aggfunc="first"))
    d = pd.concat(parts)
    d = d[~d.index.duplicated(keep="last")].sort_index()[list(GAUGES)]
    runs = find_gauge_outages(d.reset_index().rename(columns={"index": "date"}))
    masked = d.copy()
    for r in runs:
        masked.loc[r["start"]:r["end"], r["gauge"]] = np.nan
    return d, masked, runs


def hourly_local(source: str) -> pd.DataFrame:
    """A source's hours on the Pacific clock, stamped at the hour's START (so a stamp's date is the hour's day)."""
    h = RG.read(source)
    h.index = (h.index - pd.Timedelta(hours=1)).tz_convert(TZ).tz_localize(None)
    return h[~h.index.duplicated(keep="first")]


def era5_hourly() -> pd.Series:
    """ERA5's hourly rain at the city point (Open-Meteo, Pacific clock, stamped at the hour's end), 2011 on, as the
    training peaks read it; re-stamped at the hour's start."""
    parts = [pd.read_csv(RAW / n, parse_dates=["timestamp"]) for n in ("hourly_rain_openmeteo_2011-2015.csv", "hourly_rain_openmeteo.csv")]
    h = pd.concat(parts).drop_duplicates("timestamp", keep="last").set_index("timestamp")["precip_inches"].sort_index()
    h.index = h.index - pd.Timedelta(hours=1)
    return h


def daily_from_hourly(h: pd.DataFrame) -> pd.DataFrame:
    """Pacific-day totals; a day missing any hour (or holding a missing one) is missing."""
    day = h.index.normalize()
    g = h.groupby(day)
    n = g.count()
    want = pd.Series([len(pd.date_range(pd.Timestamp(d).tz_localize(TZ), pd.Timestamp(d + pd.Timedelta(days=1)).tz_localize(TZ),
                                        freq="h", inclusive="left")) for d in n.index], index=n.index)
    tot = g.sum(min_count=1)
    return tot.where(n.ge(want, axis=0))


def sources_daily() -> dict:
    """{source: daily frame of regions}: AQPI and AORC from their hours, MRMS from its 24-hour product."""
    out = {"aqpi": daily_from_hourly(hourly_local("aqpi")), "aorc": daily_from_hourly(hourly_local("aorc"))}
    if (RG.OUT_DIR / "mrms_daily.csv.gz").exists():
        out["mrms"] = RG.read("mrms", "daily")
    return out


# ── 1 · against the gauges ──────────────────────────────────────────────────

def grade(x: pd.Series, g: pd.Series) -> dict | None:
    j = pd.concat([x.rename("x"), g.rename("g")], axis=1).dropna()
    if len(j) < 20 or j["g"].sum() <= 0:
        return None
    wet = (j["x"] >= 0.01) | (j["g"] >= 0.01)
    cell = {"days": int(len(j)), "wet_days": int(wet.sum()), "total": round(float(j["x"].sum()), 2),
            "gauge_total": round(float(j["g"].sum()), 2), "ratio": round(float(j["x"].sum() / j["g"].sum()), 3),
            "r": round(float(j["x"].corr(j["g"])), 3), "mae_wet": round(float((j["x"] - j["g"])[wet].abs().mean()), 3)}
    for t in THRESH:
        hit = int(((j["x"] >= t) & (j["g"] >= t)).sum())
        miss = int(((j["x"] < t) & (j["g"] >= t)).sum())
        fa = int(((j["x"] >= t) & (j["g"] < t)).sum())
        cell[f"csi_{t}"] = round(hit / (hit + miss + fa), 3) if hit + miss + fa else None
        cell[f"n_{t}"] = int((j["g"] >= t).sum())
    # two-day totals on consecutive days both sides hold
    full = j.reindex(pd.date_range(j.index.min(), j.index.max(), freq="D"))
    two = full.rolling(2).sum().dropna()
    two = two[(two["x"] >= 0.05) | (two["g"] >= 0.05)]
    cell["mae_2day"] = round(float((two["x"] - two["g"]).abs().mean()), 3) if len(two) else None
    cell["gauge_wet_source_dry"] = int(((j["g"] >= 0.1) & (j["x"] < 0.02)).sum())
    cell["source_wet_gauge_dry"] = int(((j["x"] >= 0.1) & (j["g"] < 0.02)).sum())
    return cell


def grades(daily: dict, masked: pd.DataFrame) -> dict:
    """{window: {gauge: {source: cell}}}: each source at the gauge's own cell; ``aqpi``'s and ``mrms``' basin
    rectangles beside (westside for Oceanside, central for Downtown)."""
    out = {}
    for w, (a, b, _) in WINDOWS.items():
        out[w] = {}
        for gname, point in GAUGES.items():
            g = masked[gname].loc[a:b]
            cells = {}
            for src, d in daily.items():
                if point in d and src in WINDOW_SOURCES[w]:
                    c = grade(d[point].loc[a:b], g)
                    if c:
                        cells[src] = c
            out[w][gname] = cells
    return out


def february(daily: dict, raw: pd.DataFrame) -> dict:
    """February 2026, the Oceanside gauge dead all month: each source's month (where it holds every day), and every
    source on the days all of them hold whole (AQPI misses some hours of four storm days)."""
    a, b = "2026-02-01", "2026-02-28"
    keys = ("oceanside_gauge", "westside", "downtown_gauge", "city")
    subs = {src: d.loc[a:b] for src, d in daily.items() if len(d.loc[a:b])}
    common = raw.loc[a:b].index
    for d in subs.values():
        common = common.intersection(d.index[d["city"].notna()])
    out = {"common_days": int(len(common)), "gauges_month": {g: round(float(raw[g].loc[a:b].sum()), 2) for g in GAUGES},
           "gauges_common": {g: round(float(raw[g].reindex(common).sum()), 2) for g in GAUGES}, "sources": {}}
    for src, sub in subs.items():
        whole = int(sub["city"].notna().sum())
        out["sources"][src] = {"whole_days": whole, "common": {k: round(float(sub[k].reindex(common).sum()), 2) for k in keys},
                               "month": {k: round(float(sub[k].sum()), 2) for k in keys} if whole == len(raw.loc[a:b]) else None}
    return out


# ── 2 · storm peaks and AORC's clock ────────────────────────────────────────

def peaks(raw: pd.DataFrame) -> dict:
    """Per storm day, each source's wettest hour and three hours on the city rectangle (ERA5 at its one point)."""
    hours = {s: hourly_local(s)["city"] for s in ("aqpi", "aorc") if (RG.OUT_DIR / f"{s}_hourly.csv.gz").exists()}
    if (RG.OUT_DIR / "mrms_hourly.csv.gz").exists():
        hours["mrms"] = hourly_local("mrms")["city"]
    hours["era5"] = era5_hourly()
    aq = daily_from_hourly(hourly_local("aqpi"))["city"]
    storm = raw.max(axis=1).ge(STORM_IN) | aq.reindex(raw.index).ge(STORM_IN)
    days = raw.index[storm]
    rows = []
    for d in days:
        r = {"date": str(d.date())}
        for s, h in hours.items():
            seg = h.loc[d:d + pd.Timedelta(hours=23)]
            if len(seg) >= 23 and seg.notna().all():
                r[f"{s}_1h"] = round(float(seg.max()), 3)
                r[f"{s}_3h"] = round(float(seg.rolling(3, min_periods=1).sum().max()), 3)
        rows.append(r)
    f = pd.DataFrame(rows).set_index("date")

    def ratio(a: str, b: str, k: str) -> dict | None:
        if not {f"{a}_{k}", f"{b}_{k}"} <= set(f):
            return None
        j = f[[f"{a}_{k}", f"{b}_{k}"]].dropna()
        j = j[j[f"{b}_{k}"] > 0]
        if len(j) < 3:
            return None
        return {"storms": int(len(j)), "median_ratio": round(float((j[f"{a}_{k}"] / j[f"{b}_{k}"]).median()), 2),
                "median": round(float(j[f"{a}_{k}"].median()), 3), "median_ref": round(float(j[f"{b}_{k}"].median()), 3)}
    out = {"storm_days": len(f), "table": f.reset_index().to_dict("records")}
    for ref in ("aqpi", "mrms"):
        if ref in hours:
            out[f"vs_{ref}"] = {f"{s}_{k}": ratio(s, ref, k) for s in hours if s != ref for k in ("1h", "3h")}
    return out


def clock() -> dict:
    """Do the hourly stamps agree? Each pair's correlation on the city rectangle with the second series moved k hours
    (k > 0: its value from k hours earlier). MRMS's hourly product is stamped at its accumulation's end by definition,
    so it is the reference; a best k of 0 means the stamps agree. Over MRMS's wet days (AQPI's season for AQPI)."""
    out = {}
    s = {n: RG.read(n)["city"] for n in ("mrms", "aorc", "aqpi") if (RG.OUT_DIR / f"{n}_hourly.csv.gz").exists()}
    for a, b in (("mrms", "aorc"), ("mrms", "aqpi"), ("aqpi", "aorc")):
        if a in s and b in s:
            j = pd.concat([s[a].rename("a"), s[b].rename("b")], axis=1).dropna()
            r = {k: round(float(j["a"].corr(j["b"].shift(k))), 3) for k in range(-3, 4)}
            out[f"{a}~{b}"] = {"hours": int(len(j)), "r_by_shift": r, "best": max(r, key=r.get)}
    return out


def aorc_hole(daily: dict, raw: pd.DataFrame, runs: list) -> list:
    """Each Oceanside outage inside AORC's record: rain at the Downtown gauge, and AORC at the Oceanside and Downtown
    cells. AORC is adjusted to daily gauges; through the 2018–2020 outages its Oceanside cell reads a fraction of the
    rain around it (a dry hole on the dead gauge), from 2023 it does not."""
    a = daily["aorc"]
    out = []
    for r in runs:
        if r["gauge"] != "SF Oceanside" or r["start"] < "2016-10-01" or pd.Timestamp(r["end"]) > a.index.max():
            continue
        w = slice(r["start"], r["end"])
        dt = float(raw["SF Downtown"].loc[w].sum())
        out.append({"start": r["start"], "end": r["end"], "days": r["days"], "downtown_gauge": round(dt, 2),
                    "aorc_oceanside_cell": round(float(a["oceanside_gauge"].loc[w].sum()), 2),
                    "aorc_downtown_cell": round(float(a["downtown_gauge"].loc[w].sum()), 2)})
    return out


def scale(daily: dict) -> dict:
    """AORC against MRMS, day by day per region (Oct 2020 – Dec 2025): could a model trained on AORC read MRMS live?"""
    if "mrms" not in daily:
        return {}
    a, m = daily["aorc"].loc["2020-10-15":"2025-12-31"], daily["mrms"].loc["2020-10-15":"2025-12-31"]
    out = {}
    for k in RG.REGIONS:
        c = grade(m[k], a[k])           # MRMS graded with AORC in the gauge's place
        if c:
            out[k] = {"ratio_mrms_to_aorc": c["ratio"], "r": c["r"], "mae_wet": c["mae_wet"], "days": c["days"]}
    return out


# ── 3 · the overflow model on another source's rain ─────────────────────────

S2_VARIANTS = {
    # name → (words, {basin key: (daily, peaks) where each is a gauge recipe or an AORC region / point recipe})
    "aorc_points": "AORC at the gauges' own cells (the same recipe: the mean of both cells where the live model reads "
                   "the two-gauge mean, the Downtown cell where it reads Downtown): the instrument changes, not the place",
    "aorc_basins": "AORC averaged over each basin's rectangle: the place changes too",
    "gauges_aorc_peaks": "the gauges' daily rain as live, with the peak hours (and the wind's rain weights) from AORC "
                         "over the basin's rectangle instead of ERA5 at one point",
    "outage_masked": "the gauges as live, trained with the dead-gauge rule on (gauge_outage_v1: a dead gauge's 0.00 run "
                     "read from the other gauge). The live model trains on the raw record, where the Westside's two-gauge "
                     "mean is halved through every Oceanside outage; its scoring rain is already masked",
    "aorc_fill": "the gauges as live, with every dead or missing gauge day read from AORC at that gauge's cell instead "
                 "of from the other gauge, in training and in scoring",
    "fill_and_peaks": "both: the gauges' daily rain with dead days from AORC, and the peak hours (and the wind's weights) "
                      "from AORC over the basin's rectangle",
    "mrms_fill": "the gauges as live, with every dead or missing gauge day read from MRMS at that gauge's cell where MRMS "
                 "has it (Oct 2020 on), else from the other gauge as live, in training and in scoring",
}


def _features(daily: pd.Series, hourly: pd.Series, wind: pd.DataFrame) -> pd.DataFrame:
    """[date, the 16 daily features, the 3 peak features, wind_v_rain] from one daily and one hourly series (hours
    stamped at their start, Pacific clock), by the shared formulas (rain_features)."""
    d = add_daily_features(pd.DataFrame({"date": daily.index, "precip_inches": daily.fillna(0.0).to_numpy()}))
    h = pd.DataFrame({"timestamp": hourly.index, "precip_inches": hourly.fillna(0.0).to_numpy()})
    peak = hourly_intensity(h)
    # ERA5's wind is stamped at the hour's end: the hour that starts at t reads the wind at t + 1h
    hw = h.assign(end=h["timestamp"] + pd.Timedelta(hours=1)).merge(wind, left_on="end", right_on="timestamp",
                                                                     how="left", suffixes=("", "_w"))
    wv = wind_rain_features(hw[["timestamp", "precip_inches", "wind_speed_ms", "wind_dir_deg"]])
    return d[["date"] + DAILY_FEATURES].merge(peak, on="date", how="left").merge(wv, on="date", how="left")


def _swap(frame: pd.DataFrame, feats: pd.DataFrame, cols: list) -> pd.DataFrame:
    f = frame.copy()
    m = feats.set_index("date")
    for c in cols:
        f[c] = f["date"].map(m[c])
    if "rain_west" in f:
        f["rain_west"] = f["precip_avg"] * f["west_share"]
    if "max3h_after_wet" in f:
        f["max3h_after_wet"] = f["rain_max3h"] * f["wet_prior_3d"]
    if "rain_4d_cum" in f:
        f["rain_4d_cum"] = f["rain_3d_cum"] + f["rain_lag3d"]
    return f


def s2_test(n_boot: int | None = None, only: list | None = None) -> dict:
    """The live design on each S2_VARIANTS' rain, graded by the term lab against the live model on identical rows."""
    import term_lab as TL
    lab = TL.Lab()
    terms, by_fold, rec = lab.live_design()
    n_boot = n_boot or TL.N_BOOT
    wind = pd.read_csv(TL.WIND_CSV, parse_dates=["timestamp"])
    raw, _masked, _runs = gauges()
    a_h, a_d = hourly_local("aorc"), daily_from_hourly(hourly_local("aorc"))
    region = {"westside": "westside", "north_shore": "north_shore", "central": "central", "southeast": "southeast", "citywide": "city"}

    def gauge_daily(src: str) -> pd.Series:
        return raw.mean(axis=1) if src == "avg" else raw[src]

    def point_series(src: str, frame: pd.DataFrame) -> pd.Series:
        return frame[["downtown_gauge", "oceanside_gauge"]].mean(axis=1) if src == "avg" else frame[GAUGES[src]]

    base_live = lab.grade(lab.oof(terms, record=rec, terms_by_fold=by_fold), n_boot)
    out = {"design": {"terms": terms, "record": rec, "fold_terms": bool(by_fold)}, "live": _pooled(base_live), "variants": {}}
    sources0 = dict(lab.source)
    train = lab.train(rec)
    import train_v4 as T
    dead = raw.copy()
    for r in _runs:
        dead.loc[r["start"]:r["end"], r["gauge"]] = np.nan
    filled = dead.copy()
    for gname, point in GAUGES.items():
        filled[gname] = filled[gname].fillna(a_d[point].reindex(filled.index))
    m_d = RG.read("mrms", "daily")
    mfill = dead.copy()
    for gname, point in GAUGES.items():
        mfill[gname] = mfill[gname].fillna(m_d[point].reindex(mfill.index))
    other = {g: [o for o in GAUGES if o != g][0] for g in GAUGES}
    every = DAILY_FEATURES + INTENSITY_FEATURES + ["wind_v_rain"]

    def daily_only(series: pd.Series) -> pd.DataFrame:
        return add_daily_features(pd.DataFrame({"date": series.index, "precip_inches": series.fillna(0.0).to_numpy()}))

    for name, words in S2_VARIANTS.items():
        if only and name not in only:
            continue
        for key in lab.keys:
            src = sources0[key]
            if name == "aorc_points":
                feats, cols = _features(point_series(src, a_d), point_series(src, a_h), wind), every
            elif name == "aorc_basins":
                feats, cols = _features(a_d[region[key]], a_h[region[key]], wind), every
            elif name == "gauges_aorc_peaks":
                feats, cols = _features(gauge_daily(src), a_h[region[key]], wind), INTENSITY_FEATURES + ["wind_v_rain"]
            elif name == "outage_masked":
                feats, cols = daily_only(T.rain_series(src, [T.GAUGE_OUTAGE_RULE["name"]], older_rain=True)[0]), DAILY_FEATURES
            elif name == "aorc_fill":
                one = filled.mean(axis=1) if src == "avg" else filled[src]
                feats, cols = daily_only(one), DAILY_FEATURES
            elif name == "fill_and_peaks":
                one = filled.mean(axis=1) if src == "avg" else filled[src]
                feats, cols = _features(one, a_h[region[key]], wind), every
            else:                                                          # mrms_fill: before MRMS, the live rule
                one = mfill.mean(axis=1) if src == "avg" else mfill[src].fillna(mfill[other[src]])
                feats, cols = daily_only(one), DAILY_FEATURES
            pseudo = f"{name}:{key}"
            train[pseudo] = _swap(train[src], feats, cols)
            lab.entry[pseudo] = _swap(lab.entry[src].reset_index(), feats, cols).set_index("date")
            lab.source[key] = pseudo
        try:
            g = lab.grade(lab.oof(terms, record=rec, terms_by_fold=by_fold), n_boot)
        finally:
            lab.source = dict(sources0)
        out["variants"][name] = {"words": words, "grade": _pooled(g)}
        print(f"  s2 {name}: " + " · ".join(f"{t} {c['pooled']['skill']:.3f} (Δ {c['pooled']['delta'] * 1000:+.2f}, {c['pooled']['verdict']})"
                                            for t, c in out["variants"][name]["grade"].items()))
    return out


def _pooled(g: dict) -> dict:
    keep = ("skill", "skill_lo", "skill_hi", "live_skill", "delta", "lo", "hi", "verdict", "n", "pos", "pr", "live_pr")
    return {t: {u: {k: c.get(k) for k in keep if k in c} for u, c in units.items()} for t, units in g.items()}


# ── the report ──────────────────────────────────────────────────────────────

def build(with_s2: bool = False, only: list | None = None) -> dict:
    raw, masked, runs = gauges()
    daily = sources_daily()
    res = {"outages": [r for r in runs if r["start"] >= "2016-10-01"], "grades": grades(daily, masked),
           "february_2026": february(daily, raw), "peaks": peaks(raw), "clock": clock(), "aorc_vs_mrms": scale(daily),
           "aorc_hole": aorc_hole(daily, raw, runs)}
    if with_s2:
        res["s2"] = s2_test(only=only)
    return res


SOURCE_ROWS = [  # name, what, grid, step, record, live, where it is
    ("NOAA gauges", "SF Downtown (USW00023272) and SF Oceanside (USC00047767), ACIS daily", "2 points", "daily",
     "2011 →", "a day late", "data/raw/historical_rain*.csv"),
    ("ERA5", "the reanalysis behind today's training peaks (Open-Meteo)", "~25 km, one point", "hourly", "2011 →",
     "~5 days late", "data/raw/hourly_rain_openmeteo*.csv"),
    ("AORC v1.1", "NOAA's Analysis of Record for Calibration (AWS noaa-nws-aorc-v1-1-1km)", "~800 m", "hourly",
     "1979 → Dec 2025 on AWS", "no (yearly stores)", "rain_grids/aorc_hourly.csv.gz; cut-outs 07_rain_grids/aorc/ (16 years)"),
    ("MRMS Pass2", "NOAA's radar + gauge multisensor QPE (AWS noaa-mrms-pds)", "1 km", "hourly; 24 h at midnight",
     "Oct 2020 →", "~1 hour", "rain_grids/mrms_daily.csv.gz (every day), mrms_hourly.csv.gz (wet days)"),
    ("AQPI", "the Bay Area radar network, Surfrider's feed (CW3E)", "250 m", "15 minutes", "Oct 2025 → Aug 2026",
     "yes (Kyle's endpoint)", "rain_grids/aqpi_hourly.csv.gz; 15-minute means in 07_rain_grids/aqpi/"),
]
NAMES = {"aqpi": "AQPI", "aorc": "AORC", "mrms": "MRMS", "era5": "ERA5"}


def _f(v, d=2, sign=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def _facts(res: dict) -> dict:
    """The numbers the short version quotes, from the result itself."""
    a, b = (pd.Timestamp(x) for x in WINDOWS["nine_seasons"][:2])
    dead = sum(max(0, (min(pd.Timestamp(r["end"]), b) - max(pd.Timestamp(r["start"]), a)).days + 1)
               for r in res["outages"] if r["gauge"] == "SF Oceanside")
    nine = (b - a).days + 1
    return {"oceanside_dead": dead, "nine_days": nine, "dead_share": dead / nine}


def render(res: dict) -> str:
    g, s2, fb, pk, ck, sc = res["grades"], res.get("s2"), res["february_2026"], res["peaks"], res["clock"], res["aorc_vs_mrms"]
    L = ["# Rain sources: what the forecast could read", "",
         "Generated by `src/models/rain_sources.py --report` from `data/models/rain_sources.json` (the collectors: "
         "`src/collectors/rain_grids.py`). Edit the script, not this file. Research only: nothing served moves. "
         "Chase, 2026-10-08: \"do a big investigation with your recs on a branch and give a report. Pull stuff if you need to.\"", "",
         "## The short version", "", *[line.format(**_facts(res)) for line in READING], "",
         "## What was pulled", "",
         "| source | what | grid | step | record | live | here |", "|---|---|---|---|---|---|---|"]
    L += [f"| {a} | {b} | {c} | {d} | {e} | {f} | `{h}` |" for a, b, c, d, e, f, h in SOURCE_ROWS]
    L += ["", "Every grid is cut to the city and read at seven places (`rain_grids.REGIONS`): the cell nearest each NOAA "
          "gauge, and five rough rectangles, the four BWTF basins and the whole city. The rectangles are not sewershed "
          "outlines (the repo has none; AQPI's `name` column is a federal HUC12 watershed). CNRFC's 6-hour, 4 km QPE was "
          "not pulled: one of its cells is about a whole basin and six hours blur the peak, so it can offer nothing MRMS "
          "and AORC do not.", "",
          "## 1 · Against the gauges", "",
          "Each source at the gauge's own cell, day by Pacific day (midnight to midnight is the gauges' day: it lines "
          "them up with AQPI best). A dead gauge's days are left out (`rain_features.find_gauge_outages`: "
          f"{len(res['outages'])} runs since Oct 2016, {sum(r['days'] for r in res['outages']):,} days, "
          f"{sum(r['days'] for r in res['outages'] if r['gauge'] == 'SF Oceanside'):,} of them Oceanside's). "
          "**ratio**: the source's total over the gauge's. **r**: the daily correlation. **miss**: the mean miss on wet "
          "days, inches. **CSI ≥ x**: hits / (hits + misses + false alarms) at x inches, then how many gauge days reached "
          "it. **2-day miss**: the mean miss on two-day totals (a gauge sometimes books a storm a day off).", "",
          "Neither side is truth. MRMS corrects its radar with gauges, and both NOAA gauges report into the NWS network "
          "it draws on (ACIS lists them as SFOC1 and SRSC1), so MRMS's agreement with them is partly built in. AORC is "
          "built from gauge-adjusted analyses too. AQPI is the radar alone, as far as the feed says.", ""]
    for w, (a, b, words) in WINDOWS.items():
        L += [f"**{words}**", "", "| gauge | source | days | ratio | r | miss | CSI ≥ 0.1 | CSI ≥ 0.5 | CSI ≥ 1 | 2-day miss |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for gname, cells in g[w].items():
            for src, c in cells.items():
                L.append(f"| {gname} | {NAMES[src]} | {c['days']:,} | {_f(c['ratio'])} | {_f(c['r'])} | {_f(c['mae_wet'])} | "
                         f"{_f(c['csi_0.1'])} ({c['n_0.1']}) | {_f(c['csi_0.5'])} ({c['n_0.5']}) | {_f(c['csi_1.0'])} ({c['n_1.0']}) | {_f(c['mae_2day'])} |")
        L.append("")
    L += ["## 2 · February 2026, the dead Oceanside gauge", "",
          f"The Oceanside gauge read 0.00 all month; Downtown read {fb['gauges_month']['SF Downtown']:.2f}″. On the "
          f"{fb['common_days']} days every source holds whole (AQPI misses hours of four storm days), in inches:", "",
          "| | Ocean Beach cell (the Oceanside gauge's) | Westside | Downtown cell | city |", "|---|---|---|---|---|",
          f"| NOAA gauges | {fb['gauges_common']['SF Oceanside']:.2f} (dead) | – | {fb['gauges_common']['SF Downtown']:.2f} | – |"]
    for src, c in fb["sources"].items():
        cc = c["common"]
        L.append(f"| {NAMES[src]} | {cc['oceanside_gauge']:.2f} | {cc['westside']:.2f} | {cc['downtown_gauge']:.2f} | {cc['city']:.2f} |")
    m = fb["sources"].get("mrms", {}).get("month")
    if m:
        L.append(f"| MRMS, the whole month | {m['oceanside_gauge']:.2f} | {m['westside']:.2f} | {m['downtown_gauge']:.2f} | {m['city']:.2f} |")
    if res.get("aorc_hole"):
        L += ["", "**AORC's dry hole.** AORC is adjusted to daily gauges. Through the Oceanside outages of 2018–2020 its "
              "Oceanside cell reads a fraction of the rain around it, a dry hole on the dead gauge (its January–June "
              "2018 and 2019 maps show it: 1–3″ there, 13–15″ across the rest of the city). From 2023 it does not. A "
              "fill from AORC is wrong on exactly the days it is needed most:", "",
              "| Oceanside outage | days | Downtown gauge | AORC, Oceanside cell | AORC, Downtown cell |", "|---|---|---|---|---|"]
        L += [f"| {h['start']} → {h['end']} | {h['days']} | {h['downtown_gauge']:.2f} | {h['aorc_oceanside_cell']:.2f} | "
              f"{h['aorc_downtown_cell']:.2f} |" for h in res["aorc_hole"]]
    L += ["", "## 3 · Storm peaks", "",
          f"Each storm day (either gauge, or AQPI's city mean, at {STORM_IN}″ or more: {pk['storm_days']} days since 2011), each "
          "source's wettest hour and three hours on the city rectangle (ERA5 at its one point). The median of the "
          "source's peak over the reference's, on the storms both hold:", "",
          "| reference | source | storms | wettest hour (ratio) | wettest 3 hours (ratio) | median wettest hour, source / reference |",
          "|---|---|---|---|---|---|"]
    for ref in ("mrms", "aqpi"):
        cells = pk.get(f"vs_{ref}") or {}
        for src in ("aqpi", "aorc", "era5", "mrms"):
            h1, h3 = cells.get(f"{src}_1h"), cells.get(f"{src}_3h")
            if h1:
                L.append(f"| {NAMES[ref]} | {NAMES[src]} | {h1['storms']} | {h1['median_ratio']:.2f} | {h3['median_ratio']:.2f} | "
                         f"{h1['median']:.2f}″ / {h1['median_ref']:.2f}″ |")
    L += ["", "**The clocks.** The correlation of two hourly series on the city rectangle with the second moved k hours "
          "(MRMS's hourly product is stamped at its accumulation's end by definition):", "",
          "| pair | hours | k = −1 | k = 0 | k = +1 | best k |", "|---|---|---|---|---|---|"]
    for pair, c in ck.items():
        a, b = pair.split("~")
        L.append(f"| {NAMES[a]} ~ {NAMES[b]} | {c['hours']:,} | {c['r_by_shift'][-1 if -1 in c['r_by_shift'] else '-1']} | "
                 f"{c['r_by_shift'][0 if 0 in c['r_by_shift'] else '0']} | {c['r_by_shift'][1 if 1 in c['r_by_shift'] else '1']} | {c['best']} |")
    L += ["", "AQPI's and AORC's stamps both mark the hour's end (best k = 0 against MRMS). AORC's hours follow MRMS's "
          "loosely (r 0.70 at best, against AQPI's 0.83): within a storm its timing is approximate, as its hourly "
          "pattern in California is partly spread out of longer totals.", "",
          "## 4 · The overflow model on another source's rain", ""]
    if s2:
        L += [f"The live design ({len(s2['design']['terms'])} terms, each fold's own pick, the {s2['design']['record']} record, "
              "the live C) refit on each variant's rain and scored by the term lab on the live model's own basin-days: S2 "
              "with the rain known, nine seasons (T2) and the holdout, paired against the live model. Δ is the change in "
              "Brier score ×1000 (negative: the variant is closer), with its 90% range; \"better\" and \"worse\" only "
              "when the whole range is on one side. The terms were picked on the gauges' rain, which favours the live "
              "rain a little. One day ahead is not tested here: forecast days read ICON's rain whatever trains the model.", "",
              f"Live: skill {s2['live']['T2']['pooled']['skill']:.3f} (nine seasons), {s2['live']['T1-holdout']['pooled']['skill']:.3f} (holdout).", "",
              "| variant | nine seasons: skill | Δ ×1000 [range] | holdout: skill | Δ ×1000 [range] | clear by basin |",
              "|---|---|---|---|---|---|"]
        for name, v in s2["variants"].items():
            t2, th = v["grade"]["T2"], v["grade"]["T1-holdout"]
            clear = []
            for t, lab in (("T2", "nine"), ("T1-holdout", "holdout")):
                for u, c in v["grade"][t].items():
                    if u != "pooled" and c["verdict"] in ("better", "worse"):
                        clear.append(f"{u.replace('_', ' ')} {c['verdict']} ({lab} {c['delta'] * 1000:+.2f})")
            L.append(f"| `{name}` | {t2['pooled']['skill']:.3f} | {t2['pooled']['delta'] * 1000:+.2f} [{t2['pooled']['lo'] * 1000:+.2f}, "
                     f"{t2['pooled']['hi'] * 1000:+.2f}] | {th['pooled']['skill']:.3f} | {th['pooled']['delta'] * 1000:+.2f} "
                     f"[{th['pooled']['lo'] * 1000:+.2f}, {th['pooled']['hi'] * 1000:+.2f}] | {'; '.join(clear) or '–'} |")
        L += [""] + [f"- `{name}`: {v['words']}." for name, v in s2["variants"].items()]
    L += ["", "## 5 · Could a model trained on AORC read MRMS live?", "",
          "AORC ends in December 2025 on AWS, so a model trained on it would need another source for the days since. MRMS "
          "against AORC, Oct 2020 – Dec 2025, day by day:", "",
          "| region | MRMS / AORC | r | mean miss, wet days |", "|---|---|---|---|"]
    L += [f"| {k.replace('_', ' ')} | {_f(c['ratio_mrms_to_aorc'])} | {_f(c['r'])} | {_f(c['mae_wet'])} |" for k, c in sc.items()]
    L += ["", "MRMS reads 7–17% drier than AORC: a model trained on AORC would read MRMS low unless MRMS were scaled.", "",
          "## Recommendations", "", *RECOMMENDATIONS, ""]
    return "\n".join(L) + "\n"


READING = [
    "- **Keep the NOAA gauges as the forecast's daily rain.** No gridded source predicted overflows better as the "
    "day's rain. AORC in the gauges' place was worse in both windows, clearly so in Central and Southeast, whether read "
    "at the gauges' own cells or averaged over the basins.",
    "- **Fill dead-gauge days from MRMS, not from the other gauge.** The Oceanside gauge was dead on {oceanside_dead} "
    "of the nine scored seasons' {nine_days:,} days ({dead_share:.0%}); today the Westside then reads Downtown's rain. "
    "Reading MRMS at the dead gauge's cell instead made the Westside clearly better on the holdout (−0.67 ×1000), whose "
    "outages fall inside MRMS's record (2023–24). Pooled it is −0.26 on the holdout and −0.05 on the nine seasons, "
    "where most outages come before MRMS starts (Oct 2020): not clear, and pointing one way.",
    "- **AORC copies the dead gauge.** It is adjusted to daily gauges, and through the 2018–2020 Oceanside outages "
    "its Oceanside cell reads 27–64% of the rain Downtown got: a dry hole on the dead gauge. So AORC cannot fill those "
    "days, and its Westside rain is wrong in those seasons. MRMS passed the same test in February 2026: 4.8″ at the "
    "dead gauge's cell, in line with Downtown's 4.4″.",
    "- **MRMS is the gridded source to use live.** It is the closest to both gauges (partly by construction: it "
    "corrects with them, but it threw the dead one out), about an hour behind, every hour since Oct 2020.",
    "- **AQPI times storms well but reads wet.** Its hours line up with MRMS's (r 0.83), but it reads 15–50% above the "
    "gauges and MRMS, about 5% of its 15-minute periods are blank (some in storms), and the network changed during "
    "the season. Good for \"is it raining now\", not for amounts, until it is calibrated.",
    "- **Sharper peak hours did not clearly help.** AORC's peaks made the Westside clearly better on the nine seasons "
    "but not on the holdout, and over the city MRMS's wettest hours are only about 10% above ERA5's (today's training "
    "peaks). Forecast days read ICON's peaks anyway.",
]
RECOMMENDATIONS = [
    "1. **A dead-gauge fill from MRMS** (`gauge_outage_v2`, a candidate input rule): a dead gauge's run read from MRMS "
    "at its cell, the other gauge before Oct 2020 as today, in training, scoring and on the live page. Build it as an "
    "opt-in input rule, save a candidate whose id names the change, and let the stages build score it on every entry, "
    "one day ahead included. Promotion is Chase's call.",
    "2. **MRMS on the live board** as that backup, and as rain so far today. Decoding GRIB2 needs eccodes, which may "
    "not fit the app's function bundle (its dependencies are near the limit already), so the live path needs its own "
    "small job that writes the city's numbers where the app reads them.",
    "3. **AQPI for \"raining now\"** on the Today board, once Kyle confirms the endpoint and that we may poll it. "
    "Scale it to MRMS before showing inches.",
    "4. **Not now:** AORC or MRMS as the daily rain (worse or no better); AORC as a fill (its dry holes); sewershed "
    "outlines for rain (the basin rectangles did no better than the gauges' cells); CNRFC (too coarse).",
    "5. **Revisit peaks after the live season (T0)**, with MRMS's hourly peaks if a longer window opens up, and grade "
    "the peak terms one day ahead, where ICON supplies them.",
    "6. **Ask Kyle:** the live endpoint and permission to poll it; how the six beach points were picked; whether CW3E "
    "keeps a permanent archive (the folder is `tmp_archive`); and whether AQPI's QPE is adjusted to gauges.",
]



def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--s2", action="store_true")
    ap.add_argument("--only", default=None, help="--s2: comma-separated S2_VARIANTS to run (merged into the saved result)")
    a = ap.parse_args(argv)
    only = a.only.split(",") if a.only else None
    res = build(a.s2, only)
    if a.report:
        prev = json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}
        if not a.s2 and "s2" in prev:
            res["s2"] = prev["s2"]                        # keep the slow test's result when only the grades rerun
        elif only and "s2" in prev:                        # a partial run adds its variants to the saved ones
            res["s2"]["variants"] = {**prev["s2"]["variants"], **res["s2"]["variants"]}
            res["s2"]["variants"] = {k: res["s2"]["variants"][k] for k in S2_VARIANTS if k in res["s2"]["variants"]}
        OUT_JSON.write_text(json.dumps(res, indent=1, default=str) + "\n")
        REPORT.write_text(render(json.loads(OUT_JSON.read_text())))
        print(f"→ {OUT_JSON.relative_to(REPO)}, {REPORT.relative_to(REPO)}")
    else:
        print(json.dumps({k: v for k, v in res.items() if k != "peaks"}, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
