"""Lab samples for the stage builders: one row per (station, day, analyte), from up to three sources.

S4 (zone overflow → water quality) is scored against the city's lab results
(STAGES_DESIGN.md §3.4), and its truth needs more years than DataSF holds.
Three records exist, and they overlap:

  datasf   DataSF v3fv-x3ux, mirrored in data/raw/historical_bacteria.csv, 2020-07-27 →
  stardb   SFPUC's own STARDB export (data/sfpuc_stardb_2000_2020/), 2000-01 → 2020-07-27.
           The same database as DataSF (their 45 results on 2020-07-27 are identical)
  poobot   SFPUC's feed as the 2016-17 Poo Bot archived it (data/poobot/samples.csv),
           2015-12 → 2017-01. Every one of its 3,291 results is also in STARDB, value
           for value (checked 2026-10-01)

``load_samples`` merges them with one rule: on the same station, day and
analyte **DataSF wins over STARDB, which wins over Poo Bot** (the city's
published record first, then its lab export, then a third party's copy of
its feed). Within one source a same-day repeat keeps the highest
value, as ``train_v4.load_samples`` does, so an exceedance survives the merge.
How many rows each step dropped is in ``df.attrs["report"]``.

Stations are mapped through shared/stations.py ids only. STARDB carries
``basin`` / ``zone`` columns copied from the registry when it was parsed
(2026-09-29); a copy goes stale the day the geography moves (SFPUC4 puts
Islais Creek in Central), and its nine retired points carry "Unknown". So
they are never read: a row's zone comes from shared/zones.py, a basin from
the geography the builder uses, and a row whose station is not in the
registry is dropped and counted. "Over standard" is recomputed from the raw
lab text with shared/standards.py (AB 411 single sample, total-coliform ratio
rule included), never trusted from a stored column.

The default sources are what the served models were fit on (DataSF + Poo
Bot), so ``load_samples()`` reproduces ``train_v4.load_samples``' station-day
exceedances exactly (tests/test_samples.py); ``train_v4`` is on the served
path and is not edited. STARDB is opt-in. A source may come with a window,
``(name, first day, last day or None)``, and is clipped to it before the
merge (``attrs["report"]["dropped"]["outside_window"]`` counts what fell
outside). ``D10_SOURCES`` is the stages' S4 truth (design §3.4, owner
decision D10): DataSF 2020-07 →, Poo Bot 2015-12 → 2017-01 and STARDB
2016-10 → 2020-07, de-duplicated by the precedence above. truth.py and
exclusions.py read it; nothing on the served path does. Before 2002-07 at
the bay stations and 2003-10 on the ocean beaches STARDB has total coliform
only, so exceedance rates there are not comparable with later years.

``zone_sample_days`` is the S4 truth frame: per zone × day, how many stations
were sampled, whether any result was over standard, and the *first-look* flag.
SFPUC resamples within two days after 93–100% of exceedances, so a sample
taken because of a recent exceedance is not a random look at the water. A
zone sample-day is a first look when that zone had no exceedance on D−1 or
D−2; S4's primary score uses first looks only, resamples are their own
stratum (Part B 4, X-S4-RESAMPLE).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from shared.standards import STANDARDS, flag_exceedances, parse_result  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONE_OF_SOURCE, ZONES  # noqa: E402

DATA_DIR = FORECAST / "data"
SOURCE_FILES = {
    "datasf": DATA_DIR / "raw" / "historical_bacteria.csv",
    "stardb": DATA_DIR / "sfpuc_stardb_2000_2020" / "sfpuc_beach_bacteria_2000-01_2020-07_normalized.csv",
    "poobot": DATA_DIR / "poobot" / "samples.csv",
}
PRECEDENCE = ("datasf", "stardb", "poobot")         # earlier wins on the same station-day-analyte
DEFAULT_SOURCES = ("datasf", "poobot")              # what the served models were fit on
# The stages' S4 truth (design §3.4, owner decision D10): each source on its own window (first day, last day;
# None = the record's end), then the precedence merge. DataSF's mirror starts 2020-07-27, where STARDB ends
# (their shared results go to DataSF); Poo Bot's 2016-10 → 2017-01 results are all in STARDB too.
D10_SOURCES = (("datasf", "2020-07-01", None), ("stardb", "2016-10-01", "2020-07-31"), ("poobot", "2015-12-01", "2017-01-31"))
RESAMPLE_DAYS = (1, 2)                              # an exceedance on D−1 or D−2 makes D a resample
KEY = ["station", "date", "analyte"]
COLUMNS = ["station", "date", "analyte", "value", "value_raw", "standard", "exceeds", "source", "zone"]


def _read(source: str) -> pd.DataFrame:
    """One source in the common shape (station, date, analyte, value_raw), lab text kept as text."""
    path = SOURCE_FILES[source]
    if source == "poobot":
        df = pd.read_csv(path, dtype={"data": str, "source": str}).rename(columns={"source": "station", "data": "value_raw"})
    else:
        df = pd.read_csv(path, dtype={"value_raw": str, "station": str})
    df = df[["station", "sample_date", "analyte", "value_raw"]].rename(columns={"sample_date": "date"})
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d").dt.normalize()
    df["value_raw"] = df["value_raw"].astype(object).where(df["value_raw"].notna(), None)
    return df.assign(source=source)


def windows(sources) -> dict:
    """{source: (first, last)} for ``sources``: names, or (name, first day, last day) with None for an open end.
    An unknown name, a name given twice or a window that ends before it starts raises."""
    out = {}
    for s in sources:
        name, lo, hi = (s, None, None) if isinstance(s, str) else tuple(s)
        if name not in PRECEDENCE:
            raise ValueError(f"unknown sample source {name!r}; known: {PRECEDENCE}")
        if name in out:
            raise ValueError(f"sample source {name!r} is named twice")
        lo, hi = (None if d is None else pd.Timestamp(d).normalize() for d in (lo, hi))
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"sample source {name!r}: its window ends {hi.date()}, before it starts {lo.date()}")
        out[name] = (lo, hi)
    return out


def load_samples(sources=DEFAULT_SOURCES) -> pd.DataFrame:
    """Every lab result from ``sources``, one row per (station, date, analyte).

    ``sources``: names, or (name, first day, last day or None), which clips that source to the window before
    the merge (``D10_SOURCES``). Columns: station (registry id), date, analyte, value (MPN/100 mL, '<10' → 5),
    value_raw, standard (the single-sample limit that applied), exceeds, source,
    zone (shared/zones.py). ``attrs["report"]`` counts what was dropped and why.
    """
    win = windows(tuple(sources))
    sources = tuple(win)
    rep = {"sources": list(sources), "windows": {s: [None if d is None else str(d.date()) for d in w] for s, w in win.items()},
           "rows_read": {}, "dropped": {"outside_window": {}, "not_in_registry": {}, "analyte_without_standard": {},
                                        "same_source_repeat": {}, "superseded": {}}}
    frames = []
    for src in sources:
        df = _read(src)
        rep["rows_read"][src] = int(len(df))
        lo, hi = win[src]
        inside = pd.Series(True, index=df.index)
        if lo is not None:
            inside &= df["date"] >= lo
        if hi is not None:
            inside &= df["date"] <= hi
        rep["dropped"]["outside_window"][src] = int((~inside).sum())
        df = df[inside]
        in_reg = df["station"].isin(STATIONS)
        rep["dropped"]["not_in_registry"][src] = int((~in_reg).sum())
        df = df[in_reg]
        has_std = df["analyte"].isin(STANDARDS)
        rep["dropped"]["analyte_without_standard"][src] = int((~has_std).sum())
        df = df[has_std].copy()
        df["value"] = df["value_raw"].map(parse_result).astype(float)
        # A same-day repeat (two fecal results for one station and day) keeps the HIGHEST, so an exceedance survives.
        df = df.sort_values("value", ascending=False, na_position="last", kind="stable")
        dup = df.duplicated(KEY, keep="first")
        rep["dropped"]["same_source_repeat"][src] = int(dup.sum())
        frames.append(df[~dup])
    allrows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=KEY + ["value_raw", "source", "value"])
    allrows["rank"] = allrows["source"].map({s: i for i, s in enumerate(PRECEDENCE)})
    allrows = allrows.sort_values(KEY + ["rank"], kind="stable")
    lost = allrows.duplicated(KEY, keep="first")
    winner = allrows.groupby(KEY, sort=False)["source"].transform("first")
    for (loser, win), n in allrows[lost].groupby([allrows.loc[lost, "source"], winner[lost]]).size().items():
        rep["dropped"]["superseded"].setdefault(loser, {})[f"by_{win}"] = int(n)
    # Precedence, not value, settles a clash, so a superseded result that DISAGREES with the winner is counted:
    # the sources are copies of one database today (0 disagreements); a nonzero count is how a drift shows.
    win_val = allrows.groupby(KEY, sort=False)["value"].transform("first")
    differs = lost & ~((allrows["value"] == win_val) | (allrows["value"].isna() & win_val.isna()))
    rep["superseded_value_differs"] = {}
    for (loser, win), n in allrows[differs].groupby([allrows.loc[differs, "source"], winner[differs]]).size().items():
        rep["superseded_value_differs"].setdefault(loser, {})[f"by_{win}"] = int(n)
    out = allrows[~lost].drop(columns="rank").reset_index(drop=True)
    recs = out[KEY + ["value"]].to_dict("records")
    flag_exceedances(recs)                            # total-coliform ratio rule uses the same station-day's fecal
    out["standard"] = [r["threshold"] for r in recs]
    out["exceeds"] = [bool(r["exceeds"]) for r in recs]
    out["zone"] = out["station"].map(ZONE_OF_SOURCE)
    out = out[COLUMNS].sort_values(["date", "station", "analyte"], kind="stable").reset_index(drop=True)
    rep["rows"] = int(len(out))
    rep["by_source"] = {s: int(n) for s, n in out["source"].value_counts().items()}
    rep["span"] = {s: [str(g["date"].min().date()), str(g["date"].max().date())] for s, g in out.groupby("source")}
    out.attrs["report"] = rep
    return out


def zone_sample_days(samples: pd.DataFrame) -> pd.DataFrame:
    """Per zone × sampled day: n_stations, n_stations_sampled, n_stations_exceeding, any_exceedance, first_look, sources.

    ``first_look`` is False when the zone had an exceedance on D−1 or D−2
    (a resample; Part B 4). A day nobody sampled has no exceedance on record,
    so it does not make the next days resamples.
    """
    s = samples.assign(zone=samples["station"].map(ZONE_OF_SOURCE))
    st = s.groupby(["zone", "date", "station"], sort=False).agg(exc=("exceeds", "max")).reset_index()
    z = st.groupby(["zone", "date"]).agg(n_stations_sampled=("station", "nunique"), n_stations_exceeding=("exc", "sum"),
                                         any_exceedance=("exc", "max")).reset_index()
    z["any_exceedance"] = z["any_exceedance"].astype(bool)
    z["n_stations_exceeding"] = z["n_stations_exceeding"].astype(int)
    z.insert(2, "n_stations", z["zone"].map({k: len(v.station_ids) for k, v in ZONES.items()}).astype(int))
    bad = set(zip(z.loc[z["any_exceedance"], "zone"], z.loc[z["any_exceedance"], "date"]))
    z["first_look"] = [not any((zk, d - pd.Timedelta(days=k)) in bad for k in RESAMPLE_DAYS) for zk, d in zip(z["zone"], z["date"])]
    src = s.groupby(["zone", "date"])["source"].agg(lambda x: ";".join(sorted(set(x))))
    z["sources"] = z.set_index(["zone", "date"]).index.map(src)
    order = {k: i for i, k in enumerate(ZONES)}
    return z.sort_values(["zone", "date"], key=lambda c: c.map(order) if c.name == "zone" else c, kind="stable").reset_index(drop=True)
