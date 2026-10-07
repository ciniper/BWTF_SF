#!/usr/bin/env python3
"""
FORECAST v4 TRAINING — four basins, regional rain, the Poo Bot archive, and a
scorecard for "model vs what happened".

What changed from v3 (train_v2.py, promoted 2026-09-02):

  * Central basin. Mission Creek's outfalls (CSD-018..028, 52% of citywide
    discharge volume) get their own stage-1 model; BAY#220 Mission Creek gets
    its own stage-2 group. Every registry station is now served.
  * Regional rain. Each basin is evaluated on the citywide two-gauge average
    (v3) AND on its local gauge — Oceanside 047767 for Westside, Downtown
    047772 for the bay basins — and keeps whichever scores better on the
    chronological holdout. The chosen source is stored with the model so
    serving feeds the same gauge.
  * Poo Bot archive (data/poobot/). SFPUC's own feed, polled twice daily
    Mar 2016 – Jan 2017: Westside discharge onsets for a season CIWQS does not
    cover (Oceanside reports start Jan 2018) and 1,097 ENTERO samples from
    3.5 years before DataSF's floor for the stage-2 impact table. Gated by a
    recall check against CIWQS Bayside events.
  * Volume-unknown events. Archive events carry no volume; the volume head's
    prediction stands in (exactly what serving does), and they are excluded
    from fitting the volume heads themselves.
  * Scorecard artifact. For every day of the training span: rain, per-basin
    P(discharge) from the final model and from the holdout-fit model, the
    composed per-group and per-zone risk, and what actually happened —
    discharges (with the beaches they post, via shared/outfalls.py) and
    bacteria exceedances. Feeds /forecast's "model check" view.

Run:  venv/bin/python features/forecast/src/models/train_v4.py
Artifacts → data/models/v4/ (promote by copying into data/models/).

A longer label record (build_dataset(record=...), opt-in; train_older_reports.py):
the older SFPUC discharge reports (data/csd/pre2018/, Mar 2011 on) label the days
CIWQS is silent on, for candidates only. Every served path reads the record above.

Rescore (no retraining):  train_v4.py --rescore [--promote]
Appends POST-TRAINING days to the served scorecard — the live models scored on
every day after the artifact's end that the refreshed rain + CIWQS + bacteria
inputs now cover. Run it after each quarterly CIWQS refresh so the Model check
scorecard includes the latest winter.
"""
from __future__ import annotations

import json
import pickle
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from train_v2 import (  # noqa: E402  — shared formulas, kept in lockstep
    HOLDOUT_START, INTENSITY_FEATURES, MARQUEE_STORMS, MODEL_PARAMS, TRAIN_END,
    DATA_DIR, RAW_DIR, build_hourly_features, fit_final, get_feature_columns,
    get_feature_columns_v21, persistence_baseline, season_cv_scores, wet_season,
)
from csd_labels import APP_BASINS, build_daily_labels, load_events  # noqa: E402
from impact import compose, smooth_table  # noqa: E402
from rain_features import GAUGE_OUTAGE_RULE, INPUT_RULES_LIVE, mask_gauge_outages  # noqa: E402
from shared.outfalls import FEED_NAME_TO_OUTFALLS, OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

V4_DIR = DATA_DIR / "models" / "v4"
SERVE_DIR = DATA_DIR / "models"
POOBOT_DIR = DATA_DIR / "poobot"
TRAIN_START = pd.Timestamp("2016-03-01")
ARCHIVE_START, ARCHIVE_END = pd.Timestamp("2016-03-19"), pd.Timestamp("2017-01-10")
ARCHIVE_MIN_RECALL = 0.75   # feed must have shown ≥75% of CIWQS Bayside event-days

from groups import BASIN_KEYS, GROUPS_BY_BASIN, SITE_GROUPS, ZONE_GROUPS  # noqa: E402  (shared with serving)
from scorecard import zone_confusion  # noqa: E402  (shared with serving)

# Which ACIS gauge sees each basin's rain (both are in data/raw/historical_rain.csv)
LOCAL_GAUGE = {"Westside": "SF Oceanside", "North Shore": "SF Downtown",
               "Central": "SF Downtown", "Southeast": "SF Downtown"}
RAIN_SOURCES = ["avg", "SF Oceanside", "SF Downtown"]
# adopt the local gauge only if it clearly wins on the holdout
ADOPT_PR_GAIN, ADOPT_BRIER_SLACK = 0.01, 0.0005

from shared.standards import STANDARDS, flag_exceedances, parse_result  # noqa: E402  (one source for 'over standard')


# ── Rain features, per source ───────────────────────────────────────────────

COCORAHS_CSV = RAW_DIR / "historical_rain_cocorahs.csv"   # src/collectors/cocorahs.py
# A longer record's rain before historical_rain.csv / hourly_rain_openmeteo.csv start (2016-01-01), read only
# when build_dataset is given a record starting before TRAIN_START (collectors/historical.py --older).
OLDER_RAIN_CSV = RAW_DIR / "historical_rain_2011-2015.csv"
OLDER_HOURLY_CSV = RAW_DIR / "hourly_rain_openmeteo_2011-2015.csv"
# ERA5's hourly 10 m wind at the hourly rain's point and clock, 2011 on (collectors/historical.py --wind): the south
# wind on the rainy hours (rain_features.WIND_FEATURES), an extra frame column only a model naming it reads.
HOURLY_WIND_CSV = RAW_DIR / "openmeteo_wind_hourly.csv"


def rain_series(source: str, input_rules: list | None = None, older_rain: bool = False) -> tuple:
    """Daily rain (inches) for `source` + fill note. ``input_rules`` may name
    "gauge_outage_v1" (rain_features.GAUGE_OUTAGE_RULE): a dead gauge's
    0.00 run becomes missing before any averaging or filling, so the other
    gauge stands in. None = the raw record, as v4 was trained.

    `source` is 'avg' (mean of the two NOAA gauges), a NOAA gauge name
    ('SF Downtown' / 'SF Oceanside'; a missing day takes the other gauge),
    an extra gauge id from data/raw/historical_rain_cocorahs.csv (e.g.
    'US1CASF0017'; a missing day takes the two-gauge mean), or that id with
    '@-1' appended, which moves each value one day earlier — CoCoRaHS
    observers read the gauge at ~07:00, so the value filed for D is mostly
    D−1's rain. Then everything is filled to 0.

    ``older_rain`` (a longer record's opt-in, build_dataset(record=...)): the two
    gauges before historical_rain.csv's first day, from OLDER_RAIN_CSV, go in front,
    with the gauge-outage rule always applied to them (the Oceanside gauge reads
    0.00 through dead spells in 2011–2015 too). The file's own days are untouched:
    False (every served path) reads exactly the record the served sets saw."""
    rain_df = pd.read_csv(RAW_DIR / "historical_rain.csv", parse_dates=["date"])
    rp = rain_df.pivot_table(index="date", columns="rain_station_name",
                             values="precip_inches", aggfunc="first").sort_index()
    cols = list(rp.columns)
    masked = []
    if input_rules and GAUGE_OUTAGE_RULE["name"] in input_rules:
        tmp, masked = mask_gauge_outages(rp.reset_index(), gauges=tuple(cols))
        rp = tmp.set_index("date")[cols]
    extra = {"outage_runs_masked": len(masked), "outage_days_masked": int(sum(r["days"] for r in masked))}
    if older_rain:
        rp, extra["older_rain"] = _with_older_rain(rp, cols)
    avg = rp[cols].mean(axis=1)
    if source == "avg":
        return avg.fillna(0.0), {"filled_from_other_gauge": 0, **extra}
    if source == "avg3":   # NOAA pair + CoCoRaHS Potrero where it reported, else the pair alone
        cc = pd.read_csv(COCORAHS_CSV, parse_dates=["date"])
        pot = cc[cc["station_id"] == "US1CASF0017"].set_index("date")["precip_inches"].reindex(rp.index)
        three = pd.concat([rp[cols], pot.rename("Potrero")], axis=1).mean(axis=1)
        return three.fillna(avg).fillna(0.0), {"potrero_days": int(pot.notna().sum())}
    if source in cols:
        other = [c for c in cols if c != source][0]
        s = rp[source]
        filled = int(s.isna().sum() - (s.isna() & rp[other].isna()).sum())
        return s.fillna(rp[other]).fillna(0.0), {"filled_from_other_gauge": filled, **extra}
    sid, _, shift = source.partition("@")
    cc = pd.read_csv(COCORAHS_CSV, parse_dates=["date"])
    s = cc[cc["station_id"] == sid].set_index("date")["precip_inches"].reindex(rp.index)
    if s.isna().all():
        raise KeyError(f"unknown rain source {source!r}")
    if shift:
        s = s.shift(int(shift))
    filled = int(s.isna().sum() - (s.isna() & avg.isna()).sum())
    return s.fillna(avg).fillna(0.0), {"filled_from_two_gauge_mean": filled, "shift_days": int(shift or 0)}


def _with_older_rain(rp: pd.DataFrame, cols: list) -> tuple:
    """(rp with OLDER_RAIN_CSV's gauge-days before rp's first day in front, outage-masked; a note)."""
    old = pd.read_csv(OLDER_RAIN_CSV, parse_dates=["date"])
    op = old.pivot_table(index="date", columns="rain_station_name", values="precip_inches", aggfunc="first").sort_index()
    if sorted(op.columns) != sorted(cols):
        raise ValueError(f"{OLDER_RAIN_CSV.name} names gauges {sorted(op.columns)}, historical_rain.csv {sorted(cols)}")
    op = op[op.index < rp.index.min()].reindex(columns=cols)
    if not len(op) or (op.index[-1] + pd.Timedelta(days=1)) != rp.index.min():
        raise ValueError(f"{OLDER_RAIN_CSV.name} does not end the day before historical_rain.csv starts ({rp.index.min().date()})")
    tmp, runs = mask_gauge_outages(op.reset_index(), gauges=tuple(cols))
    op = tmp.set_index("date")[cols]
    note = {"first": str(op.index.min().date()), "days": int(len(op)), "missing_gauge_days": {c: int(op[c].isna().sum()) for c in cols},
            "outage_runs_masked": len(runs), "outage_days_masked": int(sum(r["days"] for r in runs))}
    return pd.concat([op, rp]), note


def rain_features(source: str, input_rules: list | None = None, older_rain: bool = False) -> pd.DataFrame:
    """Daily features over an arbitrary daily series — the shared formula
    (src/models/rain_features.py), the same one serving applies."""
    from rain_features import add_daily_features
    s, _ = rain_series(source, input_rules, older_rain=older_rain)
    rp = add_daily_features(pd.DataFrame({"date": s.index, "precip_inches": s.values}))
    return rp[["date"] + get_feature_columns()]


def hourly_features(older_rain: bool = False) -> pd.DataFrame:
    """train_v2.build_hourly_features, with OLDER_HOURLY_CSV's hours in front for a longer record."""
    if not older_rain:
        return build_hourly_features()
    from rain_features import hourly_intensity
    h = pd.read_csv(RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"])
    old = pd.read_csv(OLDER_HOURLY_CSV, parse_dates=["timestamp"])
    old = old[old["timestamp"] < h["timestamp"].min()]
    if not len(old) or old["timestamp"].max() + pd.Timedelta(hours=1) != h["timestamp"].min():
        raise ValueError(f"{OLDER_HOURLY_CSV.name} does not end the hour before hourly_rain_openmeteo.csv starts")
    return hourly_intensity(pd.concat([old, h], ignore_index=True))


def wind_features(older_rain: bool = False) -> pd.DataFrame:
    """[date, wind_v_rain]: ERA5's hourly rain weighing ERA5's hourly wind (rain_features.wind_rain_features), the
    older hours in front for a longer record. A wet day whose rainy hours the wind file does not reach is NaN, never
    calm: a model that reads the wind raises on it (refetch with historical.py --wind)."""
    from rain_features import wind_rain_features
    h = pd.read_csv(RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"])[["timestamp", "precip_inches"]]
    if older_rain:
        old = pd.read_csv(OLDER_HOURLY_CSV, parse_dates=["timestamp"])[["timestamp", "precip_inches"]]
        h = pd.concat([old[old["timestamp"] < h["timestamp"].min()], h], ignore_index=True)
    w = pd.read_csv(HOURLY_WIND_CSV, parse_dates=["timestamp"])
    return wind_rain_features(h.merge(w, on="timestamp", how="left"))


# ── Poo Bot archive → labels ────────────────────────────────────────────────

def archive_tables() -> dict:
    status = pd.read_csv(POOBOT_DIR / "feed_status.csv", parse_dates=["snapshot"])
    onsets = pd.read_csv(POOBOT_DIR / "discharge_onsets.csv", parse_dates=["date", "snapshot"])
    status["date"] = status["snapshot"].dt.normalize()
    covered = sorted(status["date"].unique())
    # active basins per snapshot date (a continuing discharge is "active", not an onset)
    active = {}
    for _, r in status.iterrows():
        for name in str(r["cso_structures"] or "").split("|"):
            if name and name != "nan":
                for oid in FEED_NAME_TO_OUTFALLS.get(name, []):
                    active.setdefault(r["date"], set()).add(OUTFALLS[oid].basin)
    onsets = onsets[onsets["mapped"] == True].copy()  # noqa: E712
    onsets["basin"] = onsets["basin"].str.split("|").str[0]
    return {"covered_dates": pd.DatetimeIndex(covered), "active": active, "onsets": onsets}


def archive_recall(arch: dict) -> dict:
    """Did the feed show the CIWQS-reported Bayside discharges during the
    archive window? (same day or the next snapshot day)"""
    ev = load_events()
    ev = ev[(ev["event_date"] >= ARCHIVE_START) & (ev["event_date"] <= ARCHIVE_END)
            & (ev["app_basin"] != "Westside")]
    days = ev.groupby(["app_basin", "event_date"]).size().index
    hit = 0
    for basin, d in days:
        if basin in arch["active"].get(d, ()) or basin in arch["active"].get(d + pd.Timedelta(days=1), ()):
            hit += 1
    flagged = {(b, d) for d, bs in arch["active"].items() for b in bs if b != "Westside"}
    ciwqs = {(b, d) for b, d in days}
    ciwqs_pm1 = ciwqs | {(b, d - pd.Timedelta(days=1)) for b, d in ciwqs} | {(b, d + pd.Timedelta(days=1)) for b, d in ciwqs}
    precision = sum(1 for f in flagged if f in ciwqs_pm1) / len(flagged) if flagged else None
    return {"ciwqs_bayside_event_days": len(days), "seen_in_feed": hit,
            "recall": round(hit / len(days), 3) if len(days) else None,
            "feed_flag_days_bayside": len(flagged),
            "precision_vs_ciwqs_pm1d": round(precision, 3) if precision is not None else None}


def geo_onsets(onsets: pd.DataFrame, geo) -> pd.DataFrame:
    """Archive onsets relabelled through a geography (Part B 22): each row's basin
    comes from its outfall_ids, and a structure string whose outfalls span basins
    becomes one row per basin carrying only that basin's ids — split, never
    truncated to the first basin or dropped. `basin` holds the display name."""
    rows = []
    for _, r in onsets.iterrows():
        ids = str(r["outfall_ids"]).split("|")
        for key in geo.basins_of(ids):
            mine = [o for o in ids if geo.basin_of_outfall(o) == key]
            rows.append({**r.to_dict(), "basin": geo.basin(key).name, "outfall_ids": "|".join(mine)})
    return pd.DataFrame(rows, columns=onsets.columns)


def apply_archive_labels(df: pd.DataFrame, arch: dict, rain_avg: pd.Series, geo=None) -> dict:
    """Mark archive dates covered where CIWQS is not, and stamp onset days.

    Onset shift: the feed was polled ~07:00 and ~15:00, so a discharge that
    began the previous evening first shows in the morning snapshot. When the
    morning snapshot shows a new structure and yesterday was the rainy day
    (≥0.2", more than today), the event is dated yesterday — the convention
    CIWQS event_date uses.

    ``geo``: basins and onset basins follow that geography (geo_onsets);
    None = the served reading (the feed's first basin, every listed outfall).
    """
    notes = {"shifted_onsets": 0, "onset_days": {}, "new_covered_days": {}}
    covered = df["date"].isin(arch["covered_dates"])
    rain = rain_avg.reindex(df["date"]).fillna(0).values
    date_pos = {d: i for i, d in enumerate(df["date"])}
    onsets = arch["onsets"] if geo is None else geo_onsets(arch["onsets"], geo)
    for basin in (APP_BASINS if geo is None else [b.name for b in geo.basins]):
        newly = covered & (df[f"{basin}_covered"] == 0)
        df.loc[newly, f"{basin}_covered"] = 1
        df.loc[newly, f"{basin}_volume_known"] = 0
        df.loc[newly, f"{basin}_label_source"] = "poobot"
        notes["new_covered_days"][basin] = int(newly.sum())
        ons = onsets[onsets["basin"] == basin]
        stamped = set()
        for _, r in ons.iterrows():
            d = r["date"].normalize()
            i = date_pos.get(d)
            if i is None:
                continue
            if r["snapshot"].hour < 12 and i > 0 and rain[i - 1] >= 0.2 and rain[i - 1] > rain[i]:
                d, i = df["date"].iloc[i - 1], i - 1
                notes["shifted_onsets"] += 1
            if df.at[i, f"{basin}_label_source"] != "poobot":
                continue  # CIWQS already covers this day
            df.at[i, f"{basin}_csd"] = 1
            df.at[i, f"{basin}_outfalls"] = max(int(df.at[i, f"{basin}_outfalls"]), len(str(r["outfall_ids"]).split("|")))
            stamped.add(d)
        notes["onset_days"][basin] = sorted(str(d.date()) for d in stamped)
    return notes


# ── Dataset ─────────────────────────────────────────────────────────────────

def check_record(record: dict) -> dict:
    """A longer label record (build_dataset's ``record``): {"labels": "csd_pre2018", "day_rule": "first" | "every",
    "start": "YYYY-MM-DD"}, the older discharge reports read through collectors/csd_pre2018.py. Raises on anything else."""
    import csd_pre2018 as P
    if not isinstance(record, dict) or set(record) != {"labels", "day_rule", "start"}:
        raise ValueError(f"a record is {{labels, day_rule, start}}, not {record!r}")
    if record["labels"] != P.SOURCE or record["day_rule"] not in P.DAY_RULES:
        raise ValueError(f"record labels {record['labels']!r} / day rule {record['day_rule']!r}; known: {P.SOURCE}, {P.DAY_RULES}")
    start = pd.Timestamp(record["start"])
    if not (P.FIRST_DAY <= start <= TRAIN_START) or start.day != 1:
        raise ValueError(f"record start {record['start']} must be a month's first day in {P.FIRST_DAY.date()} … {TRAIN_START.date()}")
    return {**record, "start": str(start.date())}


def apply_older_labels(df: pd.DataFrame, record: dict) -> dict:
    """Stamp the older reports' labels (csd_pre2018.daily_labels) on every day the CIWQS ledger does not cover:
    a covered older day replaces an uncovered day and a Poo Bot archive day alike (an SFPUC filing outranks its
    feed), never a CIWQS day. Their volumes are unknown (volume_known 0: no volume head reads them)."""
    import csd_pre2018 as P
    lab = P.daily_labels(record["day_rule"]).set_index("date")
    notes = {"labels": P.SOURCE, "day_rule": record["day_rule"], "start": record["start"], "basins": {}}
    for basin in APP_BASINS:
        src = df[f"{basin}_label_source"]
        known = df["date"].map(lab[f"{basin}_covered"]).fillna(0).astype(int) == 1
        take = known & src.isin(["", "poobot"])
        y = df["date"].map(lab[f"{basin}_csd"]).fillna(0).astype(int)
        n_out = df["date"].map(lab[f"{basin}_outfalls"]).fillna(0).astype(int)
        notes["basins"][basin] = {"days": int(take.sum()), "discharge_days": int((take & (y == 1)).sum()),
                                  "replaced_archive_days": int((take & (src == "poobot")).sum()),
                                  "from_2016_03": int((take & (y == 1) & (df["date"] >= TRAIN_START)).sum())}
        df.loc[take, f"{basin}_csd"] = y[take]
        df.loc[take, f"{basin}_outfalls"] = n_out[take]
        df.loc[take, f"{basin}_volume_mg"] = 0.0
        df.loc[take, f"{basin}_volume_known"] = 0
        df.loc[take, f"{basin}_covered"] = 1
        df.loc[take, f"{basin}_label_source"] = P.SOURCE
    return notes


def build_dataset(end: pd.Timestamp = TRAIN_END, sources: list = None, input_rules: list | None = None,
                  geo=None, record: dict | None = None, wind: bool = False) -> tuple:
    """Returns ({rain_source: feature+label frame}, notes). Label columns are
    identical across sources; only the rain features differ. `end` is the
    training window's last day (TRAIN_END) or, for --rescore, the last day the
    refreshed inputs cover. `sources` defaults to RAIN_SOURCES; the leaderboard
    passes extra gauges (see rain_series). ``input_rules``: see rain_series —
    None reproduces the record the served models were trained on. ``geo`` (a
    shared.geography.Geography): per-basin label columns, coverage and archive
    onsets follow it, named by its basins' display names (csd_labels.
    build_daily_labels); None = the served geo_v1 frames, as v4 was trained.
    ``record`` (check_record; opt-in, GEO_V1 only): the older discharge reports as
    labels where CIWQS is silent (apply_older_labels), from the record's start,
    with the older rain in front when it starts before TRAIN_START. None = the
    served record: nothing reads collectors/csd_pre2018.py. ``wind``: add the south wind on the rainy hours
    (wind_features, rain_features.WIND_FEATURES) after the 19, for a model that reads it; False keeps every frame
    exactly as the served sets were fit on (tests/test_served_golden.py pins it)."""
    basins = APP_BASINS if geo is None else [b.name for b in geo.basins]
    if record is not None:
        if geo is not None:
            raise ValueError("the older reports' Bayside rows are outfall groups: they map to BWTF basins only (geo=None)")
        record = check_record(record)
    start = pd.Timestamp(record["start"]) if record else TRAIN_START
    older_rain = start < TRAIN_START
    labels = build_daily_labels(geo=geo)
    days = pd.DataFrame({"date": pd.date_range(start, end)})
    df = days.merge(labels, on="date", how="left")
    for basin in basins:
        for col, fill in ((f"{basin}_csd", 0), (f"{basin}_volume_mg", 0.0), (f"{basin}_outfalls", 0), (f"{basin}_covered", 0)):
            df[col] = df[col].fillna(fill)
        df[f"{basin}_volume_known"] = df[f"{basin}_covered"].astype(int)
        df[f"{basin}_label_source"] = np.where(df[f"{basin}_covered"] == 1, "ciwqs", "")

    rain_avg, _ = rain_series("avg", input_rules)
    arch = archive_tables()
    recall = archive_recall(arch)
    notes = {"archive_recall": recall, "archive_used": False}
    if geo is not None:
        notes["geography"] = geo.version   # the recall gate stays the feed's own check, in geo_v1 terms
    if recall["recall"] is not None and recall["recall"] >= ARCHIVE_MIN_RECALL:
        notes["archive_used"] = True
        notes["archive_labels"] = apply_archive_labels(df, arch, rain_avg, geo=geo)
    if record is not None:
        notes["record"] = apply_older_labels(df, record)

    cov_cols = [f"{b}_covered" for b in basins]
    df["csd_any"] = (df[[f"{b}_csd" for b in basins]].sum(axis=1) > 0).astype(int)
    df["csd_volume_mg"] = df[[f"{b}_volume_mg" for b in basins]].sum(axis=1)
    df["csd_outfalls"] = df[[f"{b}_outfalls" for b in basins]].sum(axis=1)
    df["fully_covered"] = (df[cov_cols].sum(axis=1) == len(cov_cols)).astype(int)
    df["season"] = wet_season(df["date"])

    hourly = hourly_features(older_rain)
    wind_df = wind_features(older_rain) if wind else None
    out = {}
    for src in (sources or RAIN_SOURCES):
        f = df.merge(rain_features(src, input_rules, older_rain), on="date", how="left").merge(hourly, on="date", how="left")
        f[INTENSITY_FEATURES] = f[INTENSITY_FEATURES].fillna(0)
        f[get_feature_columns()] = f[get_feature_columns()].fillna(0)
        if wind_df is not None:
            # after the 19: a day the hourly rain does not reach has no rainy hour, 0, as its peaks are; a wet day the
            # wind file does not reach stays NaN
            f = f.merge(wind_df, on="date", how="left")
            f["wind_v_rain"] = f["wind_v_rain"].where(f["date"].isin(wind_df["date"]), 0.0)
        out[src] = f.reset_index(drop=True)
        notes[f"rain_{src}"] = rain_series(src, input_rules, older_rain)[1]
    notes["input_rules"] = list(input_rules or [])
    return out, notes


def target_frame(df: pd.DataFrame, basin: str, geo=None) -> pd.DataFrame:
    """Covered days of one basin (or 'citywide') with y = its discharge label.
    With ``geo``, `basin` may be the geography's key or display name ('south')."""
    if geo is not None and basin != "citywide":
        basin = geo.basin(basin).name
    if basin == "citywide":
        sub = df[df["fully_covered"] == 1].copy()
        sub["y"] = sub["csd_any"]
    else:
        sub = df[df[f"{basin}_covered"] == 1].copy()
        sub["y"] = sub[f"{basin}_csd"]
    return sub


# ── Stage 1 ─────────────────────────────────────────────────────────────────

def fit_holdout_model(sub: pd.DataFrame, features: list):
    tr = sub[sub["date"] < HOLDOUT_START]
    if tr["y"].sum() < 5:
        return None
    m = GradientBoostingClassifier(**MODEL_PARAMS)
    m.fit(tr[features], tr["y"])
    return m


def holdout_scores(sub: pd.DataFrame, features: list, model=None) -> dict:
    te = sub[sub["date"] >= HOLDOUT_START]
    model = model or fit_holdout_model(sub, features)
    if model is None or te["y"].sum() == 0:
        return {}
    p = model.predict_proba(te[features])[:, 1]
    return {"n_test": len(te), "pos_test": int(te["y"].sum()),
            "roc_auc": roc_auc_score(te["y"], p), "pr_auc": average_precision_score(te["y"], p),
            "brier": brier_score_loss(te["y"], p)}


def calibrated(model_data: dict, X: pd.DataFrame) -> np.ndarray:
    """The dashboard's rain-scaled dry-day offset (live_dashboard._predict_calibrated)."""
    raw = model_data["model"].predict_proba(X[model_data["features"]])[:, 1]
    rain_factor = np.clip(1.0 - X["rain_3d_cum"].values * 2.0, 0.0, 1.0)
    return np.clip(raw - model_data["calibration_offset"] * rain_factor, 0.0, 1.0)


def fit_volume_heads(frames: dict, chosen: dict, features: list) -> dict:
    heads = {}
    for basin in APP_BASINS:
        sub = target_frame(frames[chosen[basin]], basin)
        ev = sub[(sub["y"] == 1) & (sub[f"{basin}_volume_known"] == 1)]
        if len(ev) < 20:
            continue
        m = GradientBoostingRegressor(n_estimators=150, max_depth=2, learning_rate=0.05,
                                      min_samples_leaf=8, random_state=42)
        yv = np.log1p(ev[f"{basin}_volume_mg"])
        m.fit(ev[features], yv)
        resid = yv - m.predict(ev[features])
        heads[basin] = {"model": m, "features": features, "target": "log1p_volume_mg",
                        "rain_source": chosen[basin], "n_events": len(ev),
                        "resid_std": round(float(resid.std()), 3)}
    return heads


def predicted_volume(head: dict, X: pd.DataFrame) -> np.ndarray:
    return np.maximum(0.0, np.expm1(head["model"].predict(X[head["features"]])))


def empirical_thresholds(frames: dict, chosen: dict) -> dict:
    bins = [(0.0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 1.5), (1.5, 99.0)]
    out = {}
    for basin in APP_BASINS + ["citywide"]:
        sub = target_frame(frames[chosen.get(basin, "avg")], basin)
        rows = []
        for lo, hi in bins:
            b = sub[(sub["precip_avg"] >= lo) & (sub["precip_avg"] < hi)]
            if len(b) >= 5:
                rows.append({"rain_lo": lo, "rain_hi": hi, "n_days": len(b),
                             "p_discharge": round(float(b["y"].mean()), 3)})
        out[basin] = rows
    return out


# ── Stage 2 ─────────────────────────────────────────────────────────────────

def _parse_value(raw) -> float | None:
    s = str(raw).strip()
    try:
        if s.startswith("<"):
            return float(s[1:]) / 2
        if s.startswith(">"):
            return float(s[1:])
        return float(s)
    except ValueError:
        return None


def load_samples() -> pd.DataFrame:
    """Per-sample exceedances: DataSF (2020-07 →) plus the Poo Bot archive
    (2015-12 → 2017-01), deduplicated on (station, date, analyte). "Over
    standard" is recomputed here from the raw values with the shared rule
    (shared/standards.py, including the total-coliform ratio rule) rather
    than trusted from the collector's stored column."""
    b = pd.read_csv(RAW_DIR / "historical_bacteria.csv", parse_dates=["sample_date"])
    b = b[["station", "sample_date", "analyte", "value_raw"]].rename(columns={"value_raw": "data"}).assign(source="datasf")
    p = pd.read_csv(POOBOT_DIR / "samples.csv", parse_dates=["sample_date"])
    p = p[p["source"].isin(STATIONS) & p["analyte"].isin(STANDARDS)].rename(columns={"source": "station"})
    p = p[["station", "sample_date", "analyte", "data"]].assign(source="poobot")
    allrows = pd.concat([b, p])
    allrows = allrows[allrows["analyte"].isin(STANDARDS)].copy()
    allrows["value"] = allrows["data"].map(parse_result)
    # Same-day resamples exist (e.g. two fecal results for one station and
    # day). Keep the HIGHEST per (station, date, analyte): stage 2 asks "was
    # the beach elevated that day", so any exceedance must survive the dedupe.
    # DataSF wins ties over the archive.
    allrows = allrows.sort_values(["value", "source"], ascending=[False, True], na_position="last")
    allrows = allrows.drop_duplicates(["station", "sample_date", "analyte"], keep="first").reset_index(drop=True)
    recs = allrows.rename(columns={"sample_date": "date"}).to_dict("records")
    flag_exceedances(recs)
    allrows["exceeds_standard"] = [r["exceeds"] for r in recs]
    return allrows[["station", "sample_date", "analyte", "exceeds_standard", "source", "value"]]


def fit_impact_table(frames: dict, chosen: dict, heads: dict, samples: pd.DataFrame,
                     event_days: dict | None = None) -> tuple:
    """P(group's beaches elevated | days since the basin's last discharge, size).
    Unknown volumes (archive events) use the volume head's prediction — the
    same thing serving does. Also returns the outfall-count diagnostic.
    ``event_days`` ({group: set of dates}) restricts a group's "discharge
    days" to those attributed to it — the outfall-split stage 2 variant fits
    its table that way (src/models/stage2.group_event_days); None = the
    basin's every discharge day, as v4 was fit."""
    table, outfall_diag = {}, {}
    for group, (basin, stations) in SITE_GROUPS.items():
        df = frames[chosen[basin]]
        sub = df[df[f"{basin}_covered"] == 1]
        ev = sub[sub[f"{basin}_csd"] == 1].copy()
        if event_days is not None and group in event_days:
            ev = ev[ev["date"].isin(event_days[group])]
        if basin in heads:
            pred = predicted_volume(heads[basin], ev)
            ev["vol"] = np.where(ev[f"{basin}_volume_known"] == 1, ev[f"{basin}_volume_mg"], pred)
        else:
            ev["vol"] = ev[f"{basin}_volume_mg"]
        events = ev.set_index("date")["vol"]
        n_out = ev.set_index("date")[f"{basin}_outfalls"]
        known = ev[ev[f"{basin}_volume_known"] == 1][f"{basin}_volume_mg"]
        med = float(known.median()) if len(known) else 0.0

        smp = samples[samples["station"].isin(stations)]
        daily = smp.groupby("sample_date").agg(elevated=("exceeds_standard", "max")).reset_index()
        daily = daily[(daily["sample_date"] >= sub["date"].min()) & (daily["sample_date"] <= sub["date"].max())]
        # only sample days where the basin's label is known
        daily = daily[daily["sample_date"].isin(sub["date"])]

        rows = []
        for _, r in daily.iterrows():
            d = r["sample_date"]
            past = events[(events.index <= d) & (events.index >= d - pd.Timedelta(days=7))]
            if past.empty:
                rows.append({"elevated": bool(r["elevated"]), "days_since": None, "large": False, "multi": False})
            else:
                last = past.index.max()
                rows.append({"elevated": bool(r["elevated"]), "days_since": (d - last).days,
                             "large": float(past.loc[last]) >= med, "multi": int(n_out.loc[last]) >= 2})
        dd = pd.DataFrame(rows)

        def bucket(d):
            if d is None or (isinstance(d, float) and np.isnan(d)):
                return "none_7d"
            d = int(d)
            return str(d) if d <= 3 else ("4-5" if d <= 5 else "6-7")

        dd["bucket"] = dd["days_since"].map(bucket)
        out = {}
        base = dd[dd["bucket"] == "none_7d"]
        out["baseline_no_recent_discharge"] = {"p_elevated": round(float(base["elevated"].mean()), 3) if len(base) else 0.0, "n": len(base)}
        for buck in ["0", "1", "2", "3", "4-5", "6-7"]:
            for size, mask in [("large", dd["large"]), ("small", ~dd["large"])]:
                g = dd[(dd["bucket"] == buck) & mask]
                if len(g) >= 3:
                    out[f"d{buck}_{size}"] = {"p_elevated": round(float(g["elevated"].mean()), 3), "n": len(g)}
        table[group] = {"basin": basin, "stations": stations, "median_event_volume_mg": round(med, 2),
                        "n_sample_days": int(len(dd)), "buckets": out}
        near = dd[dd["days_since"].notna() & (dd["days_since"] <= 2)]
        outfall_diag[group] = {
            "single_outfall": {"p_elevated": round(float(near[~near["multi"]]["elevated"].mean()), 3) if (~near["multi"]).sum() else None, "n": int((~near["multi"]).sum())},
            "multi_outfall": {"p_elevated": round(float(near[near["multi"]]["elevated"].mean()), 3) if near["multi"].sum() else None, "n": int(near["multi"].sum())},
        }
    return table, outfall_diag


# ── Scorecard: model vs what happened, every day ───────────────────────────

def posted_stations_by_day(frames: dict, arch: dict, archive_used: bool) -> dict:
    """date → {registry station id} that a discharge that day would post
    (CIWQS events via the outfall registry; archive onsets via feed names)."""
    ev = load_events()
    out = {}
    for _, r in ev.iterrows():
        o = OUTFALLS.get(r["outfall_id"])
        if o:
            out.setdefault(r["event_date"].normalize(), set()).update(o.stations)
    if archive_used:
        for _, r in arch["onsets"].iterrows():
            for oid in str(r["outfall_ids"]).split("|"):
                if oid in OUTFALLS:
                    out.setdefault(r["date"].normalize(), set()).update(OUTFALLS[oid].stations)
    by_sfpuc = {s.sfpuc_id: sid for sid, s in STATIONS.items()}
    return {d: {by_sfpuc[x] for x in sids if x in by_sfpuc} for d, sids in out.items()}


def build_scorecard(frames: dict, chosen: dict, finals: dict, holdout_models: dict, heads: dict,
                    impact_raw: dict, samples: pd.DataFrame, arch: dict, archive_used: bool, features: list,
                    stage2: dict | None = None) -> dict:
    """``stage2`` = a fitted variant spec (src/models/stage2.py) with an optional
    refit ``impact_table``; None = stage 2 v1, the served composition."""
    import stage2 as _s2
    table = smooth_table(stage2["impact_table"]) if stage2 and stage2.get("impact_table") else smooth_table(impact_raw)
    split = _s2.make_split(stage2)
    base = frames["avg"]
    n = len(base)
    dates = list(base["date"])
    day_probs, day_probs_h, day_vols = [dict() for _ in range(n)], [dict() for _ in range(n)], [dict() for _ in range(n)]
    for basin in APP_BASINS:
        key = BASIN_KEYS[basin]
        X = frames[chosen[basin]]
        p = calibrated(finals[key], X)
        ph = calibrated({**finals[key], "model": holdout_models[key]}, X) if holdout_models.get(key) is not None else None
        # stage 2 is shared by every model set, inputs included: a volume head
        # reads the rain source it was trained on, not the stage-1 model's
        # (they differ for a candidate whose basin picked another gauge)
        Xv = frames.get(heads[basin].get("rain_source", chosen[basin]), X) if basin in heads else X
        v = predicted_volume(heads[basin], Xv) if basin in heads else np.zeros(n)
        for i in range(n):
            day_probs[i][key] = float(p[i])
            day_vols[i][key] = float(v[i])
            if ph is not None and dates[i] >= HOLDOUT_START:
                day_probs_h[i][key] = float(ph[i])
    cw = calibrated(finals["citywide"], base)

    posted = posted_stations_by_day(frames, arch, archive_used)
    smp = samples.copy()
    smp["group"] = smp["station"].map({sid: g for g, (_, sids) in SITE_GROUPS.items() for sid in sids})
    elevated = smp.groupby(["sample_date", "group"]).agg(elevated=("exceeds_standard", "max"), n=("exceeds_standard", "size"))
    elevated_idx = {(d, g): (bool(e), int(k)) for (d, g), (e, k) in elevated.iterrows()}
    station_zone = {sid: zk for zk, groups in ZONE_GROUPS.items() for g in groups for sid in SITE_GROUPS[g][1]}

    days = []
    for i, d in enumerate(dates):
        row = base.iloc[i]
        per_basin, per_group = compose(table, GROUPS_BY_BASIN, day_probs, day_vols, i, split=split)
        if day_probs_h[i]:
            probs_h = [day_probs_h[j] if day_probs_h[j] else day_probs[j] for j in range(n)]
            _, per_group_h = compose(table, GROUPS_BY_BASIN, probs_h, day_vols, i, split=split)
        else:
            per_group_h = None
        basins = {}
        for basin in APP_BASINS:
            key = BASIN_KEYS[basin]
            covered = int(row[f"{basin}_covered"]) == 1
            basins[key] = {"p": round(day_probs[i][key], 3),
                           "ph": round(day_probs_h[i][key], 3) if key in day_probs_h[i] else None,
                           "y": int(row[f"{basin}_csd"]) if covered else None,
                           "vol": round(float(row[f"{basin}_volume_mg"]), 2) if covered and row[f"{basin}_volume_known"] else None,
                           "outfalls": int(row[f"{basin}_outfalls"]) if covered else None,
                           "src": row[f"{basin}_label_source"] or None}
        groups = {}
        for g in SITE_GROUPS:
            e = elevated_idx.get((d, g))
            groups[g] = {"risk": per_group[g], "risk_h": per_group_h[g] if per_group_h else None,
                         "elevated": e[0] if e else None, "n_samples": e[1] if e else 0}
        posted_today = posted.get(d, set())
        zones = {}
        for zk, zgroups in ZONE_GROUPS.items():
            zb = {SITE_GROUPS[g][0] for g in zgroups}
            covered = all(int(row[f"{b}_covered"]) == 1 for b in zb)
            posts = any(station_zone.get(s) == zk for s in posted_today)
            el = [groups[g]["elevated"] for g in zgroups if groups[g]["elevated"] is not None]
            zones[zk] = {"risk": max(per_group[g] for g in zgroups),
                         "risk_h": max(per_group_h[g] for g in zgroups) if per_group_h else None,
                         "discharge": (posts if covered else None),
                         "elevated": (any(el) if el else None)}
        days.append({"date": str(d.date()), "season": int(row["season"]), "rain": round(float(row["precip_avg"]), 3),
                     "rain_by_gauge": {src: round(float(frames[src].iloc[i]["precip_avg"]), 3) for src in RAIN_SOURCES if src != "avg"},
                     "citywide_p": round(float(cw[i]), 3), "basins": basins, "groups": groups, "zones": zones})

    # zone confusion on the holdout window, model = holdout-fit — the same
    # function serving uses to time-box the scorecard (src/models/scorecard.py)
    confusion = zone_confusion(days, list(ZONE_GROUPS), holdout_only=True)

    return {"trained_at": datetime.now().isoformat(), "holdout_start": str(HOLDOUT_START.date()),
            "span": [days[0]["date"], days[-1]["date"]],
            "basins": {BASIN_KEYS[b]: {"name": b, "rain_source": chosen[b]} for b in APP_BASINS},
            "groups": {g: {"basin": BASIN_KEYS[b], "stations": sids} for g, (b, sids) in SITE_GROUPS.items()},
            "zones": {zk: {"label": ZONES[zk].label, "groups": gs} for zk, gs in ZONE_GROUPS.items()},
            "zone_confusion_holdout": confusion, "days": days,
            "stage2": {"variant": (stage2 or {}).get("variant", "v1"), "impact_table_refit": bool(stage2 and stage2.get("impact_table"))}}


# ── Backtest ────────────────────────────────────────────────────────────────

def backtest(base: pd.DataFrame, finals: dict, exclude: pd.Series = None) -> dict:
    """Marquee storms must score high, verified-dry stretches low; count real
    event days with meaningful rain that score <10%. `exclude` masks days
    whose labels stage 1 chose not to trust (archive-only basins)."""
    fully = base[(base["fully_covered"] == 1) & ~(exclude if exclude is not None else False)].reset_index(drop=True)
    p = calibrated(finals["citywide"], fully)
    out = {"marquee": [], "critical_fn": []}
    for d in MARQUEE_STORMS:
        m = fully.index[fully["date"] == d]
        if len(m):
            i = m[0]
            out["marquee"].append({"date": d, "rain": round(float(fully.at[i, "precip_avg"]), 2),
                                   "citywide_p": round(float(p[i]), 3), "actual_any": int(fully.at[i, "csd_any"])})
    dry = fully["rain_7d_cum"] < 0.05
    out["dry_days_n"] = int(dry.sum())
    out["dry_day_avg_citywide_p"] = round(float(p[dry.values].mean()), 4) if dry.sum() else None
    for i, r in fully.iterrows():
        if r["csd_any"] == 1 and r["rain_3d_cum"] > 0.25 and p[i] < 0.10:
            out["critical_fn"].append({"date": str(r["date"].date()), "p": round(float(p[i]), 3), "rain_3d": round(float(r["rain_3d_cum"]), 2)})
    return out


# ── Main ────────────────────────────────────────────────────────────────────

def main(promote: bool = False) -> dict:
    print("=" * 64)
    print("FORECAST v4 TRAINING — four basins · regional rain · Poo Bot archive")
    print("=" * 64)
    frames, notes = build_dataset()
    features = get_feature_columns_v21()
    base = frames["avg"]
    print(f"dataset: {len(base)} days {base['date'].min().date()} → {base['date'].max().date()}")
    ar = notes["archive_recall"]
    print(f"archive recall vs CIWQS Bayside: {ar['seen_in_feed']}/{ar['ciwqs_bayside_event_days']} = {ar['recall']}"
          f"  (precision ±1d {ar['precision_vs_ciwqs_pm1d']}) → archive labels {'USED' if notes['archive_used'] else 'SKIPPED'}")
    if notes["archive_used"]:
        al = notes["archive_labels"]
        print(f"   new covered days {al['new_covered_days']}; onset days {{{', '.join(f'{b}: {len(v)}' for b, v in al['onset_days'].items())}}}; shifted {al['shifted_onsets']}")

    report = {"trained_at": datetime.now().isoformat(), "version": "v4", "feature_set": "v21",
              "train_window": [str(base["date"].min().date()), str(base["date"].max().date())],
              "notes": notes, "targets": {}, "rain_source_chosen": {}}
    finals, holdout_models, chosen, stage1_archive = {}, {}, {}, {}

    for basin in APP_BASINS:
        key = BASIN_KEYS[basin]
        cands = {}
        for src in ("avg", LOCAL_GAUGE[basin]):
            sub = target_frame(frames[src], basin)
            hm = fit_holdout_model(sub, features)
            cands[src] = {"season_cv": season_cv_scores(sub, features), "holdout": holdout_scores(sub, features, hm), "_model": hm, "_sub": sub}
        a, l = cands["avg"]["holdout"], cands[LOCAL_GAUGE[basin]]["holdout"]
        use_local = bool(a and l and l["pr_auc"] >= a["pr_auc"] + ADOPT_PR_GAIN and l["brier"] <= a["brier"] + ADOPT_BRIER_SLACK)
        src = LOCAL_GAUGE[basin] if use_local else "avg"
        chosen[basin] = src
        sub = cands[src]["_sub"]
        n_arch = int((sub[f"{basin}_label_source"] == "poobot").sum())
        print(f"\n── {basin}: {len(sub)} covered days, {int(sub['y'].sum())} event days ({n_arch} archive-covered days)")
        for s, c in cands.items():
            cv, ho = c["season_cv"], c["holdout"]
            print(f"   [{s:>12}] season-CV: ROC {cv['roc_auc']:.3f}  PR {cv['pr_auc']:.3f}  Brier {cv['brier']:.4f}"
                  + (f" | holdout: ROC {ho['roc_auc']:.3f}  PR {ho['pr_auc']:.3f}  Brier {ho['brier']:.4f}" if ho else ""))
        print(f"   → rain source: {src}")
        # Archive ablation. The holdout is 2023+, so this measures whether the
        # extra 2016-17 season helps or hurts generalization; a basin whose
        # holdout gets clearly WORSE with the archive labels is fit without
        # them (stage 1 only — stage 2 still uses the feed's posting dates).
        ablation, use_archive = None, True
        if notes["archive_used"] and n_arch:
            no_arch = sub[sub[f"{basin}_label_source"] != "poobot"]
            ablation = {"with_archive": cands[src]["holdout"], "without_archive": holdout_scores(no_arch, features)}
            w, wo = ablation["with_archive"], ablation["without_archive"]
            if w and wo:
                use_archive = not (wo["pr_auc"] >= w["pr_auc"] + ADOPT_PR_GAIN)
                print(f"   archive ablation (holdout): with PR {w['pr_auc']:.3f} Brier {w['brier']:.4f} | "
                      f"without PR {wo['pr_auc']:.3f} Brier {wo['brier']:.4f} → stage 1 {'uses' if use_archive else 'DROPS'} archive labels")
            if not use_archive:
                sub = no_arch
                cands[src]["season_cv"] = season_cv_scores(sub, features)
                cands[src]["_model"] = fit_holdout_model(sub, features)
                cands[src]["holdout"] = holdout_scores(sub, features, cands[src]["_model"])
        pers = persistence_baseline(sub)
        final = fit_final(sub, features)
        finals[key] = {**final, "features": features}
        holdout_models[key] = cands[src]["_model"]
        stage1_archive[basin] = use_archive
        print(f"   final fit : dry-day offset {final['calibration_offset']:.4f}; top: {', '.join(f for f, _ in final['importances'][:4])}")
        report["targets"][key] = {"basin": basin, "n_days": len(sub), "n_events": int(sub["y"].sum()),
                                  "rain_source": src, "candidates": {s: {"season_cv": c["season_cv"], "holdout": c["holdout"]} for s, c in cands.items()},
                                  "season_cv": cands[src]["season_cv"], "holdout": cands[src]["holdout"],
                                  "archive_ablation": ablation, "stage1_uses_archive_labels": use_archive,
                                  "persistence_baseline_on_holdout": pers,
                                  "calibration_offset": final["calibration_offset"], "top_features": final["importances"][:8]}
        report["rain_source_chosen"][key] = src

    # citywide (avg rain); archive-covered days only if every basin kept them
    sub = target_frame(base, "citywide")
    if not all(stage1_archive.values()):
        sub = sub[~(sub[[f"{b}_label_source" for b in APP_BASINS]] == "poobot").any(axis=1)]
    report["stage1_archive_labels"] = stage1_archive
    cv, hm = season_cv_scores(sub, features), fit_holdout_model(sub, features)
    ho = holdout_scores(sub, features, hm)
    final = fit_final(sub, features)
    finals["citywide"] = {**final, "features": features}
    holdout_models["citywide"] = hm
    chosen["citywide"] = "avg"
    print(f"\n── citywide: {len(sub)} fully-covered days, {int(sub['y'].sum())} event days")
    print(f"   season-CV: ROC {cv['roc_auc']:.3f}  PR {cv['pr_auc']:.3f}  Brier {cv['brier']:.4f} | holdout: ROC {ho['roc_auc']:.3f}  PR {ho['pr_auc']:.3f}  Brier {ho['brier']:.4f}")
    report["targets"]["citywide"] = {"n_days": len(sub), "n_events": int(sub["y"].sum()), "rain_source": "avg",
                                     "season_cv": cv, "holdout": ho, "calibration_offset": final["calibration_offset"],
                                     "top_features": final["importances"][:8]}

    print("\n── volume heads / impact table / thresholds / scorecard")
    heads = fit_volume_heads(frames, chosen, features)
    samples = load_samples()
    impact_raw, outfall_diag = fit_impact_table(frames, chosen, heads, samples)
    thresholds = empirical_thresholds(frames, chosen)
    untrusted = pd.Series(False, index=base.index)
    for b, keep in stage1_archive.items():
        if not keep:
            untrusted |= base[f"{b}_label_source"] == "poobot"
    bt = backtest(base, finals, untrusted)
    report["backtest"] = bt
    report["outfall_count_diagnostic"] = outfall_diag
    report["samples"] = {"n": len(samples), "datasf": int((samples["source"] == "datasf").sum()),
                         "poobot": int((samples["source"] == "poobot").sum()),
                         "span": [str(samples["sample_date"].min().date()), str(samples["sample_date"].max().date())]}
    for g, t in impact_raw.items():
        b = t["buckets"]
        print(f"   {g:14} median event {t['median_event_volume_mg']:>7.2f} MG · {t['n_sample_days']} sample days · baseline {b['baseline_no_recent_discharge']['p_elevated']:.2f} (n={b['baseline_no_recent_discharge']['n']})"
              + "".join(f" · d{k} {b[f'd{k}_large']['p_elevated']:.2f}/{b[f'd{k}_small']['p_elevated']:.2f}" for k in ("0", "1", "2") if f"d{k}_large" in b and f"d{k}_small" in b))
    for m in bt["marquee"]:
        print(f"   {m['date']}  rain {m['rain']:.2f}\"  citywide P {m['citywide_p']:.0%}  actual={'CSD' if m['actual_any'] else 'none'}")
    print(f"   dry days (7d rain <0.05\", n={bt['dry_days_n']}): avg citywide P {bt['dry_day_avg_citywide_p']:.2%}")
    print(f"   critical FN (event, 3d rain >0.25\", P<10%): {len(bt['critical_fn'])}")

    scorecard = build_scorecard(frames, chosen, finals, holdout_models, heads, impact_raw, samples,
                                archive_tables(), notes["archive_used"], features)
    for zk, c in scorecard["zone_confusion_holdout"].items():
        d = c["0.25"]["vs_discharge_posting"]; b = c["0.25"]["vs_bacteria_elevated"]
        rec = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else float("nan")
        far = d["fp"] / (d["fp"] + d["tn"]) if d["fp"] + d["tn"] else float("nan")
        brec = b["tp"] / (b["tp"] + b["fn"]) if b["tp"] + b["fn"] else float("nan")
        print(f"   zone {zk:12} @25%: discharge recall {rec:.2f}, false-alarm rate {far:.3f} | bacteria recall {brec:.2f} (n={sum(b.values())})")
    report["zone_confusion_holdout"] = scorecard["zone_confusion_holdout"]

    # ── save ──
    V4_DIR.mkdir(parents=True, exist_ok=True)
    for key, final in finals.items():
        with open(V4_DIR / f"{key}_model.pkl", "wb") as f:
            pickle.dump({"model": final["model"], "features": features, "calibration_offset": final["calibration_offset"],
                         "label": "csd_event_reported", "rain_source": chosen.get(next((b for b, k in BASIN_KEYS.items() if k == key), "citywide"), "avg"),
                         "auc": report["targets"][key]["season_cv"]["roc_auc"], "trained_at": report["trained_at"], "version": "v4"}, f)
    for basin, head in heads.items():
        with open(V4_DIR / f"{BASIN_KEYS[basin]}_volume.pkl", "wb") as f:
            pickle.dump(head, f)
    (V4_DIR / "impact_table.json").write_text(json.dumps(impact_raw, indent=2))
    (V4_DIR / "thresholds.json").write_text(json.dumps(thresholds, indent=2))
    (V4_DIR / "eval_report.json").write_text(json.dumps(report, indent=2, default=str))
    import gzip
    with gzip.open(V4_DIR / "scorecard.json.gz", "wt") as f:
        json.dump(scorecard, f, separators=(",", ":"), default=str)
    for stale in (V4_DIR / "scorecard.json", SERVE_DIR / "scorecard.json"):
        if stale.exists():
            stale.unlink()
    print(f"\nartifacts → {V4_DIR}/")
    if promote:
        for p in V4_DIR.iterdir():
            if p.suffix in (".pkl", ".json", ".gz"):
                shutil.copy2(p, SERVE_DIR / p.name)
        print(f"promoted → {SERVE_DIR}/")
    return report


# ── Rescore: extend the scorecard with post-training days ───────────────────

def _inputs_reach() -> dict:
    """Last complete day of each refreshed input the frame builder reads."""
    hourly = pd.read_csv(RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"])
    hmax = hourly["timestamp"].max()
    hourly_end = hmax.normalize() if hmax.hour == 23 else hmax.normalize() - pd.Timedelta(days=1)
    daily = pd.read_csv(RAW_DIR / "historical_rain.csv", parse_dates=["date"]).dropna(subset=["precip_inches"])
    both = daily.groupby("date")["rain_station_name"].nunique()
    daily_end = both[both >= 2].index.max()
    labels_end = build_daily_labels()["date"].max()
    return {"hourly_rain": hourly_end, "daily_rain": daily_end, "labels": labels_end}


def stage2_from_served() -> tuple:
    """(volume heads by basin name, raw impact table) from the served set —
    the stage 2 every candidate shares."""
    heads = {}
    for basin in APP_BASINS:
        vp = SERVE_DIR / f"{BASIN_KEYS[basin]}_volume.pkl"
        if vp.exists():
            with open(vp, "rb") as f:
                heads[basin] = pickle.load(f)
    return heads, json.loads((SERVE_DIR / "impact_table.json").read_text())


def rescore(promote: bool = False, tolerance: float = 0.02, replace_post: bool = False,
            input_rules: list | None = None) -> dict:
    """Extend the served scorecard with POST-TRAINING days.

    The served models, exactly as deployed (data/models/*.pkl, the impact
    table and, since a promotion, stage2.json's split), are scored on every day after the artifact's last day for which
    rain features and labels now exist — data/raw and data/csd are refreshed
    between trainings, the artifact is not. No model is refit. Existing days
    are kept byte-identical; new days carry ``post_training: true`` and no
    holdout probability (the holdout-fit siblings were never persisted, and a
    day after training needs none: the live models never saw it, which is the
    truest test there is).

    Fidelity gate: the served models must reproduce the stored zone risks on
    the artifact's own recent days to within `tolerance`; if they don't, some
    input changed retroactively and we refuse to append.

    ``replace_post``: re-score EVERY post-training day (not just append) with
    the served models on inputs treated by ``input_rules`` (default: the rules
    serving applies, rain_features.INPUT_RULES_LIVE — today the gauge-outage
    rule). Pre-training days stay exactly as trained; the fidelity gate still
    runs on the raw record. The artifact records ``input_rules_post``.

    Run:  venv/bin/python features/forecast/src/models/train_v4.py --rescore [--promote] [--replace-post]
    """
    import gzip
    served = SERVE_DIR / "scorecard.json.gz"
    with gzip.open(served, "rt") as f:
        sc = json.load(f)
    last = pd.Timestamp(sc["span"][1])
    trained_through = sc.get("trained_through") or sc["span"][1]
    reach = _inputs_reach()
    end = min(reach.values())
    print(f"rescore: artifact ends {last.date()} (models trained through {trained_through}); inputs reach "
          + ", ".join(f"{k} {v.date()}" for k, v in reach.items()) + f" → scoring through {end.date()}")
    if end <= last and not replace_post:
        print("nothing to add")
        return sc

    frames, notes = build_dataset(end=end)
    try:
        import leaderboard  # noqa: F401  (weights pipelines reference leaderboard.add_hinges)
    except Exception:  # noqa: BLE001
        pass
    s2_path = SERVE_DIR / "stage2.json"                     # the served stage 2 spec (promote.py); None = v1
    served_stage2 = json.loads(s2_path.read_text()) if s2_path.exists() else None
    finals, chosen, heads = {}, {}, {}
    for basin in APP_BASINS:
        key = BASIN_KEYS[basin]
        with open(SERVE_DIR / f"{key}_model.pkl", "rb") as f:
            m = pickle.load(f)
        finals[key] = {"model": m["model"], "features": m["features"], "calibration_offset": m["calibration_offset"]}
        chosen[basin] = m.get("rain_source", "avg")
        vp = SERVE_DIR / f"{key}_volume.pkl"
        if vp.exists():
            with open(vp, "rb") as f:
                heads[basin] = pickle.load(f)
    with open(SERVE_DIR / "citywide_model.pkl", "rb") as f:
        m = pickle.load(f)
    finals["citywide"] = {"model": m["model"], "features": m["features"], "calibration_offset": m["calibration_offset"]}
    chosen["citywide"] = "avg"
    impact_raw = json.loads((SERVE_DIR / "impact_table.json").read_text())
    fresh = build_scorecard(frames, chosen, finals, {}, heads, impact_raw, load_samples(),
                            archive_tables(), notes["archive_used"], finals["citywide"]["features"], stage2=served_stage2)
    by_date = {d["date"]: d for d in fresh["days"]}

    # Fidelity gate. The artifact's post-training days were scored on inputs treated by its
    # input_rules_post (the gauge-outage rule since 2026-09-25); comparing them with days rebuilt
    # from the raw record would flag the rule itself as drift. So when the last-120-day tail is
    # post-training, rebuild it with the same rules the artifact records.
    tail_dates = [d["date"] for d in sc["days"] if pd.Timestamp(d["date"]) > last - pd.Timedelta(days=120)]
    gate_rules = list(sc.get("input_rules_post") or []) if tail_dates and all(d > trained_through for d in tail_dates) else []
    if gate_rules:
        frames_g, notes_g = build_dataset(end=end, input_rules=gate_rules)
        fresh_g = build_scorecard(frames_g, chosen, finals, {}, heads, impact_raw, load_samples(),
                                  archive_tables(), notes_g["archive_used"], finals["citywide"]["features"], stage2=served_stage2)
        gate_by_date = {d["date"]: d for d in fresh_g["days"]}
        print(f"fidelity gate rebuilt with the artifact's input rules {gate_rules}")
    else:
        gate_by_date = by_date

    tail = [d for d in sc["days"] if pd.Timestamp(d["date"]) > last - pd.Timedelta(days=120) and d["date"] in gate_by_date]
    worst = max((abs(gate_by_date[d["date"]]["zones"][zk]["risk"] - d["zones"][zk]["risk"]) for d in tail for zk in d["zones"]), default=0.0)
    print(f"fidelity: served models reproduce the stored zone risks on the artifact's last {len(tail)} days to within {worst:.4f}")
    if worst > tolerance:
        raise SystemExit(f"served models do not reproduce the artifact (worst {worst:.4f} > {tolerance}) — an input changed retroactively; refusing to append")

    if replace_post:
        rules = list(input_rules or INPUT_RULES_LIVE)
        frames_r, notes_r = build_dataset(end=end, input_rules=rules)
        fresh_r = build_scorecard(frames_r, chosen, finals, {}, heads, impact_raw, load_samples(),
                                  archive_tables(), notes_r["archive_used"], finals["citywide"]["features"], stage2=served_stage2)
        old_by_date = {d["date"]: d for d in sc["days"]}
        keep = [d for d in sc["days"] if d["date"] <= trained_through]
        new_days = [{**d, "post_training": True} for d in fresh_r["days"] if d["date"] > trained_through]
        moved = [(d["date"], zk, old_by_date[d["date"]]["zones"][zk]["risk"], d["zones"][zk]["risk"]) for d in new_days if d["date"] in old_by_date
                 for zk in d["zones"] if abs(d["zones"][zk]["risk"] - old_by_date[d["date"]]["zones"][zk]["risk"]) >= 0.05]
        print(f"replace-post with input rules {rules}: {len(new_days)} post-training days re-scored; "
              f"{len(moved)} zone-days moved by ≥ 0.05" + (" — e.g. " + "; ".join(f"{d} {zk} {a:.2f}→{b:.2f}" for d, zk, a, b in moved[:6]) if moved else ""))
        sc["days"] = keep + new_days
        sc["input_rules_post"] = rules
        sc["rain_notes_post"] = {k: v for k, v in notes_r.items() if k.startswith("rain_")}
    else:
        new_days = [{**d, "post_training": True} for d in fresh["days"] if d["date"] > sc["span"][1]]
        sc["days"] = sc["days"] + new_days
    sc["span"] = [sc["span"][0], sc["days"][-1]["date"]]
    sc["trained_through"] = trained_through
    sc["rescored_at"] = datetime.now().isoformat()
    sc["rescore_inputs"] = {k: str(v.date()) for k, v in reach.items()}
    n_dis = sum(1 for d in new_days if any(z["discharge"] for z in d["zones"].values()))
    n_smp = sum(1 for d in new_days if any(z["elevated"] is not None for z in d["zones"].values()))
    print(f"appended {len(new_days)} post-training days {new_days[0]['date']} → {new_days[-1]['date']}: "
          f"{n_dis} with a discharge posting a beach, {n_smp} with samples")
    staged = SERVE_DIR / "scorecard.rescored.json.gz"     # staging beside the served artifact (V4_DIR is gb_v1's training record)
    with gzip.open(staged, "wt") as f:
        json.dump(sc, f, separators=(",", ":"), default=str)
    print(f"artifact → {staged}")
    if promote:
        shutil.copy2(staged, served)
        staged.unlink()
        print(f"promoted → {served}")
    return sc


if __name__ == "__main__":
    if "--rescore" in sys.argv:
        rescore(promote="--promote" in sys.argv, replace_post="--replace-post" in sys.argv)
    else:
        main(promote="--promote" in sys.argv)
