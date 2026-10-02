"""Lab samples for the stage builders (P5, 2026-10-01): src/models/samples.py.

Pinned: one row per (station, day, analyte) after merging DataSF, STARDB and
the Poo Bot archive, with DataSF > STARDB > Poo Bot on a clash and the counts
of what was dropped; "over standard" exactly as shared/standards.py decides it
(fixtures built from its own limits, never typed here); stations mapped through
the registry (STARDB's stale basin/zone columns ignored); the first-look flag
(an exceedance in the zone on D−1 or D−2 makes D a resample, Part B 4); and
the default sources reproduce train_v4.load_samples, which the served models
were fit on.

Run: venv/bin/python tests/test_samples.py
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
for p in (str(ROOT), str(FORECAST), str(FORECAST / "src" / "models"), str(FORECAST / "src" / "collectors")):
    if p not in sys.path:
        sys.path.insert(0, p)

import samples as S  # noqa: E402
from shared.standards import STANDARDS, flag_exceedances, parse_result  # noqa: E402
from shared.zones import ZONE_OF_SOURCE  # noqa: E402

ENT, TOT, FEC = "ENTERO", "COLI_TOTAL", "COLI_FECAL"
LIM = {a: STANDARDS[a]["single_sample_max"] for a in STANDARDS}
RATIO_LIM = STANDARDS[TOT]["single_sample_max_ratio"]
OB, CRISSY = "OCEAN#19_SL", "BAY#202.4_SL"          # Ocean Beach at Lincoln (ocean), Crissy Field East (north)


@contextmanager
def fixture_sources(datasf=(), stardb=(), poobot=()):
    """Point samples.py at three small CSVs in each source's own shape; rows are (date, station, analyte, lab text)."""
    old = dict(S.SOURCE_FILES)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        pd.DataFrame(list(datasf), columns=["sample_date", "station", "analyte", "value_raw"]).to_csv(d / "datasf.csv", index=False)
        sd = pd.DataFrame(list(stardb), columns=["sample_date", "station", "analyte", "value_raw"])
        sd["basin"], sd["zone"] = "Westside", "ocean"   # stale columns, written at parse time: never read
        sd.to_csv(d / "stardb.csv", index=False)
        pb = pd.DataFrame(list(poobot), columns=["sample_date", "source", "analyte", "data"])
        pb[["source", "sample_date", "analyte", "data"]].to_csv(d / "poobot.csv", index=False)
        S.SOURCE_FILES.update(datasf=d / "datasf.csv", stardb=d / "stardb.csv", poobot=d / "poobot.csv")
        try:
            yield
        finally:
            S.SOURCE_FILES.clear()
            S.SOURCE_FILES.update(old)


def test_exceedance_flags_follow_shared_standards():
    rows = [("2021-01-04", OB, ENT, str(LIM[ENT] + 1)),              # over
            ("2021-01-04", OB, TOT, str(2 * RATIO_LIM)),             # total coliform with fecal/total > 10% → limit drops
            ("2021-01-04", OB, FEC, str(int(0.2 * 2 * RATIO_LIM))),
            ("2021-01-05", OB, ENT, str(LIM[ENT])),                  # at the limit is not over
            ("2021-01-05", OB, TOT, str(2 * RATIO_LIM)),             # same total, low fecal share → the default limit
            ("2021-01-05", OB, FEC, "<10"),
            ("2021-01-06", OB, ENT, "<10"),                          # half the detection limit
            ("2021-01-06", OB, "COLI_E", ">24196")]
    with fixture_sources(datasf=rows):
        df = S.load_samples(("datasf",))
    recs = [{"station": s, "date": pd.Timestamp(d), "analyte": a, "value": parse_result(v)} for d, s, a, v in rows]
    flag_exceedances(recs)
    want = {(r["date"], r["analyte"]): (r["exceeds"], r["threshold"]) for r in recs}
    got = {(r.date, r.analyte): (r.exceeds, r.standard) for r in df.itertuples()}
    assert got == want, (got, want)
    assert got[(pd.Timestamp("2021-01-04"), TOT)] == (True, RATIO_LIM) and got[(pd.Timestamp("2021-01-05"), TOT)][0] is False
    assert df.loc[(df.date == "2021-01-06") & (df.analyte == ENT), "value"].item() == 5.0


def test_precedence_dedupe_and_drop_counts():
    d = "2016-11-07"
    with fixture_sources(
            datasf=[(d, OB, ENT, "20")],
            stardb=[(d, OB, ENT, "900"), (d, OB, TOT, "40"), (d, CRISSY, ENT, "10"), (d, CRISSY, ENT, "300"),
                    (d, "BAY#300_SL", ENT, "10")],                        # a retired point, not in the registry
            poobot=[(d, OB, TOT, "50"), (d, CRISSY, ENT, "999"), ("2016-11-08", CRISSY, ENT, "30")]):
        df = S.load_samples(S.PRECEDENCE)
    key = df.set_index(["station", "date", "analyte"])
    assert not key.index.duplicated().any()
    assert key.loc[(OB, pd.Timestamp(d), ENT), "source"] == "datasf" and key.loc[(OB, pd.Timestamp(d), ENT), "value"] == 20
    assert key.loc[(OB, pd.Timestamp(d), TOT), "source"] == "stardb"
    assert key.loc[(CRISSY, pd.Timestamp(d), ENT), "source"] == "stardb" and key.loc[(CRISSY, pd.Timestamp(d), ENT), "value"] == 300
    assert key.loc[(CRISSY, pd.Timestamp("2016-11-08"), ENT), "source"] == "poobot"   # no clash: the archive row stays
    rep = df.attrs["report"]["dropped"]
    assert rep["not_in_registry"] == {"datasf": 0, "stardb": 1, "poobot": 0}
    assert rep["same_source_repeat"] == {"datasf": 0, "stardb": 1, "poobot": 0}       # STARDB's two Crissy results: highest kept
    assert rep["superseded"] == {"stardb": {"by_datasf": 1}, "poobot": {"by_stardb": 2}}
    assert df.attrs["report"]["superseded_value_differs"] == rep["superseded"]          # every fixture clash disagrees
    assert set(df["zone"]) == {"ocean", "north"} and key.loc[(CRISSY, pd.Timestamp(d), ENT), "zone"] == "north"   # registry, not the CSV
    # the same rows without STARDB: the archive's Crissy value stands
    with fixture_sources(datasf=[(d, OB, ENT, "20")], stardb=[(d, CRISSY, ENT, "10")], poobot=[(d, CRISSY, ENT, "999")]):
        df2 = S.load_samples()
    assert df2.loc[df2.station == CRISSY, "source"].item() == "poobot" and bool(df2.loc[df2.station == CRISSY, "exceeds"].item())


def test_first_look_flags_resamples_within_two_days():
    def row(day, station, exceeds):
        return {"station": station, "date": pd.Timestamp(day), "analyte": ENT, "value": 1.0, "value_raw": "1", "standard": LIM[ENT],
                "exceeds": exceeds, "source": "datasf", "zone": ZONE_OF_SOURCE[station]}
    df = pd.DataFrame([row("2025-01-06", CRISSY, True),          # north exceedance
                       row("2025-01-07", CRISSY, False),         # D−1 over → resample
                       row("2025-01-08", "BAY#211_SL", False),   # D−2 over → resample (another north station)
                       row("2025-01-09", CRISSY, False),         # D−3 → a first look again
                       row("2025-01-07", OB, False),             # another zone is not affected
                       row("2025-01-12", CRISSY, True),
                       row("2025-01-14", CRISSY, False)])        # D−1 unsampled, D−2 over → resample
    z = S.zone_sample_days(df).set_index(["zone", "date"])
    fl = z["first_look"].to_dict()
    assert fl[("north", pd.Timestamp("2025-01-06"))] is True
    assert fl[("north", pd.Timestamp("2025-01-07"))] is False and fl[("north", pd.Timestamp("2025-01-08"))] is False
    assert fl[("north", pd.Timestamp("2025-01-09"))] is True and fl[("ocean", pd.Timestamp("2025-01-07"))] is True
    assert fl[("north", pd.Timestamp("2025-01-14"))] is False
    assert z.loc[("north", pd.Timestamp("2025-01-06")), "n_stations"] == 4 and z.loc[("ocean", pd.Timestamp("2025-01-07")), "n_stations"] == 6
    assert bool(z.loc[("north", pd.Timestamp("2025-01-06")), "any_exceedance"]) and z.loc[("north", pd.Timestamp("2025-01-08")), "n_stations_sampled"] == 1


def test_all_sources_merge_without_duplicates_and_the_counts_add_up():
    df = S.load_samples(S.PRECEDENCE)
    assert not df.duplicated(S.KEY).any()
    rep = df.attrs["report"]
    dropped = sum(sum(v.values()) for k, v in rep["dropped"].items() if k != "superseded")
    dropped += sum(n for by in rep["dropped"]["superseded"].values() for n in by.values())
    assert sum(rep["rows_read"].values()) - dropped == rep["rows"] == len(df)
    assert df["station"].isin(ZONE_OF_SOURCE).all() and df["zone"].notna().all()
    assert set(df["analyte"]) <= set(STANDARDS)
    # as of 2026-10-01: every archive result is also in STARDB, so none survives the merge; DataSF and STARDB share 2020-07-27
    assert rep["dropped"]["superseded"].get("poobot", {}).get("by_stardb", 0) == rep["rows_read"]["poobot"] - rep["dropped"]["not_in_registry"]["poobot"]
    assert rep["dropped"]["superseded"]["stardb"]["by_datasf"] == 45
    assert rep["superseded_value_differs"] == {}, rep["superseded_value_differs"]   # copies of one database, value for value
    assert df["date"].min() == pd.Timestamp("2000-01-03")


def test_default_sources_reproduce_train_v4_station_day_exceedances():
    import train_v4
    old = train_v4.load_samples()
    new = S.load_samples()
    a = old.groupby(["station", "sample_date"])["exceeds_standard"].max()
    b = new.groupby(["station", "date"])["exceeds"].max()
    b.index.names = a.index.names
    assert set(a.index) == set(b.index), (len(a), len(b))
    assert (a.reindex(b.index) == b).all(), int((a.reindex(b.index) != b).sum())
    assert len(old) == len(new)                                     # result for result, too
    assert set(new["source"]) == {"datasf", "poobot"}


def test_zone_sample_days_on_the_record():
    z = S.zone_sample_days(S.load_samples())
    assert not z.duplicated(["zone", "date"]).any()
    assert (z["n_stations_sampled"] <= z["n_stations"]).all() and (z["n_stations_exceeding"] <= z["n_stations_sampled"]).all()
    assert (z["any_exceedance"] == (z["n_stations_exceeding"] > 0)).all()
    east = z[z.zone == "east"]
    assert (~east["first_look"]).sum() > 100       # the East is resampled often (as of 2026-10-01: 422 of 790 sampled days)


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
