"""Versioned geography (shared/geography.py; STAGES_DESIGN.md §2, P2; Part B 11, 22, 23;
Chase, 2026-10-01).

Pins that geo_v1 is exactly the served geography groups.py draws, that sfpuc4_v1
comes from Outfall.report_basin alone (CSD-119 stays North Shore, Islais Creek is
Central), the five links and their outfalls, and that the label builders relabel the
same ledger under either geography without moving the served frames. Data checks
(every ledger outfall in one link, event-day counts) live here, never at import.
Counts carry an as-of date so a quarterly CIWQS refresh that only adds later days
stays green (Part B 23).

    venv/bin/python tests/test_geography.py
"""
from __future__ import annotations

import ast
import csv
import dataclasses
import functools
import subprocess
import sys
import traceback
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
for p in (ROOT, FORECAST / "src" / "models", FORECAST / "src" / "collectors"):
    sys.path.insert(0, str(p))

from shared import geography as G  # noqa: E402
from shared.outfalls import GEOGRAPHY, OBSERVED, OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

EVENTS = FORECAST / "data" / "csd" / "sf_csd_events.csv"
AS_OF = pd.Timestamp("2026-04-22")            # last ledger event day the design counted (§2.3)
HOLDOUT, POST = pd.Timestamp("2023-07-01"), pd.Timestamp("2025-11-01")
DATASET_END = pd.Timestamp("2026-08-17")      # the served goldens' as-of (tests/test_served_golden.py)

# §2.3: event days (pre / holdout / post) per SFPUC basin through AS_OF
EVENT_DAYS = {"westside": (65, (39, 12, 14)), "north_shore": (42, (31, 9, 2)),
              "central": (97, (69, 20, 8)), "south": (23, (17, 5, 1))}
CITY_EVENT_DAYS = 112

# §2.4: the five links, their outfalls and evidence (O observed, G permit + geography)
LINKS = {
    "westside>ocean": ("westside", "ocean", False, {"CSD-001": "O", "CSD-002": "O", "CSD-003": "O"}),
    "westside>baker_china": ("westside", "baker_china", False,
                             {"CSD-004": "G", "CSD-005": "G", "CSD-006": "G", "CSD-007": "O"}),
    "north_shore>north": ("north_shore", "north", True,
                          {"CSD-009": "O", "CSD-010": "O", "CSD-011": "O", "CSD-013": "O", "CSD-015": "O",
                           "CSD-017": "G", "CSD-119": "G"}),
    "central>east": ("central", "east", True,
                     {**{f"CSD-0{n}": "O" for n in ("22", "23", "24", "25", "26", "27", "31", "31A", "32", "33", "35")},
                      **{f"CSD-0{n}": "G" for n in ("18", "29", "30", "30A")}}),
    "south>east": ("south", "east", True, {"CSD-037": "G", "CSD-040": "O", "CSD-041": "O", "CSD-042": "G", "CSD-043": "O"}),
}


@functools.lru_cache(maxsize=None)
def _labels(version: str | None) -> pd.DataFrame:
    import csd_labels
    return csd_labels.build_daily_labels() if version is None else csd_labels.build_daily_labels(geo=G.get(version))


@functools.lru_cache(maxsize=None)
def _dataset(version: str | None) -> tuple:
    import train_v4
    kw = {} if version is None else {"geo": G.get(version)}
    return train_v4.build_dataset(end=DATASET_END, **kw)


def _windows(dates: pd.Series) -> tuple:
    return int((dates < HOLDOUT).sum()), int(((dates >= HOLDOUT) & (dates < POST)).sum()), int((dates >= POST).sum())


# ── the module ──────────────────────────────────────────────────────────────

def test_module_imports_only_the_three_registries():
    tree = ast.parse((ROOT / "shared" / "geography.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"__future__", "functools", "dataclasses", "shared.outfalls", "shared.stations", "shared.zones"}, mods


def test_import_reads_no_file_and_builds_nothing():
    """app/wsgi.py imports the forecast page at load (Part C fix 11): importing the module
    must open no file, pull in no pandas and build no geography. Both opens are patched:
    pathlib (Path.read_text) goes through io.open, not builtins.open."""
    code = ("import builtins, io, sys; sys.path.insert(0, %r)\n"
            "def _no(*a, **k): raise AssertionError(f'opened {a[0]!r} at import')\n"
            "builtins.open = io.open = _no\n"
            "from shared import geography as g\n"
            "assert g.get.cache_info().currsize == 0, 'built at import'\n"
            "assert 'pandas' not in sys.modules\n"
            "print('inert')") % str(ROOT)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "inert", r.stderr[-600:]


def test_get_names_a_version_and_has_no_default():
    assert G.get("geo_v1") is G.GEO_V1 and G.get("sfpuc4_v1") is G.SFPUC4_V1
    assert (G.GEO_V1.version, G.SFPUC4_V1.version) == G.VERSIONS
    for bad in ("", "GEO_V1", "sfpuc4", "sfpuc4_v2", None):
        try:
            G.get(bad)
        except KeyError:
            continue
        raise AssertionError(f"get({bad!r}) did not raise")
    assert G.stamped({}) is G.GEO_V1, "a missing stamp means geo_v1 (§2.6)"
    assert G.stamped({"geography": "sfpuc4_v1"}) is G.SFPUC4_V1
    try:
        G.stamped({"geography": "nope"})
        raise AssertionError("an unknown stamp must raise")
    except KeyError:
        pass


# ── geo_v1 == groups.py ─────────────────────────────────────────────────────

def test_geo_v1_equals_the_groups_exports():
    import groups
    import stage2
    g = G.GEO_V1
    assert {b.name: b.key for b in g.basins} == groups.BASIN_KEYS and [b.name for b in g.basins] == list(groups.BASIN_KEYS)
    assert {b.key: b.name for b in g.basins} == groups.KEY_TO_BASIN
    assert {b.key: [lk.legacy_group for lk in g.links_from(b.key)] for b in g.basins} == groups.GROUPS_BY_BASIN
    # zone = max over its groups, so the order inside a zone is free (groups.py lists East as Southeast, Mission Creek)
    assert {zk: set(lk.legacy_group for lk in g.links_into(zk)) for zk in ZONES} == {zk: set(v) for zk, v in groups.ZONE_GROUPS.items()}
    assert {s.sfpuc_id: g.station_basin(s.sfpuc_id) for s in STATIONS.values()} == groups.OBSERVED_STATION_BASIN
    by_group = {lk.legacy_group: lk for lk in g.links}
    assert list(by_group) == list(groups.SITE_GROUPS)
    for group, (basin, sids) in groups.SITE_GROUPS.items():
        lk = by_group[group]
        assert g.basin(lk.basin).name == basin, group
        assert set(lk.stations) == {STATIONS[s].sfpuc_id for s in sids}, group
        assert sorted(lk.outfalls) == stage2.outfalls_posting(group), group
    risks = {"Ocean Beach": .3, "Baker-China": .1, "Crissy Field": .2, "Aquatic Park": .05, "Mission Creek": .6, "Southeast": .4}
    assert groups.zone_risks(risks) == {zk: max(risks[lk.legacy_group] for lk in g.links_into(zk)) for zk in ZONES}
    assert g.zone_union == "max_of_link_risk"
    assert [lk.identity for lk in g.links] == [False, False, False, False, True, True], "stage2: identity for Mission Creek, Southeast"
    assert [lk.id for lk in g.links] == ["westside>ocean", "westside>baker_china", "north_shore>north:crissy_field",
                                         "north_shore>north:aquatic_park", "central>east", "southeast>east"]


def _v1_mismatches(outfalls: dict, stations: dict) -> list:
    g = G.build_geo_v1(outfalls, stations)
    return sorted(o.id for o in outfalls.values() if g.basin(g.basin_of_outfall(o.id)).name != o.basin)


def test_geo_v1_basin_of_outfall_is_the_registry_basin():
    """geo_v1 takes an outfall's basin from the stations it posts, so this agreement is a
    real check: an in-place edit of Outfall.basin or Station.basin (the known silent
    failure: Candlestick 0.0, Islais 0.60 vs 0.98) breaks it."""
    g = G.GEO_V1
    for o in OUTFALLS.values():
        assert g.basin(g.basin_of_outfall(o.id)).name == o.basin, o.id
        assert g.basin(g.basin_of_outfall(o)).report_basins.count(o.report_basin) == 1, o.id
    assert _v1_mismatches(OUTFALLS, STATIONS) == []
    edited = {**OUTFALLS, "CSD-031": dataclasses.replace(OUTFALLS["CSD-031"], basin="Central")}
    assert _v1_mismatches(edited, STATIONS) == ["CSD-031"]
    moved = {**STATIONS, "BAY#320_SL": dataclasses.replace(STATIONS["BAY#320_SL"], basin="Central")}   # Islais → Central
    assert _v1_mismatches(OUTFALLS, moved) == ["CSD-031", "CSD-031A", "CSD-032", "CSD-033", "CSD-035", "CSD-037"]
    counts = {b.key: len([o for lk in g.links_from(b.key) for o in lk.outfalls]) for b in g.basins}
    assert counts == {"westside": 7, "north_shore": 7, "central": 7, "southeast": 13}, counts


# ── sfpuc4_v1 ───────────────────────────────────────────────────────────────

def test_sfpuc4_comes_from_report_basin_only():
    g = G.SFPUC4_V1
    scrambled = {k: dataclasses.replace(o, basin="Westside") for k, o in OUTFALLS.items()}
    assert G.build_sfpuc4(scrambled) == g, "sfpuc4_v1 must not read Outfall.basin"
    for o in OUTFALLS.values():
        assert g.basin_of_outfall(o.id) == G.CITY_BASIN_OF_REPORT[o.report_basin] == G.city_basin(o) == G.city_basin(o.id), o.id
    assert g.basin_of_outfall("CSD-119") == "north_shore", "Baker Street (CSD-119) reports as North Shore, not by number range"
    assert {g.basin_of_outfall(f"CSD-0{n}") for n in ("29", "30", "30A", "31", "31A", "32", "33", "35")} == {"central"}
    assert g.keys == ("westside", "north_shore", "central", "south")
    assert [b.name for b in g.basins] == ["Westside", "North Shore", "Central", "South"], "'Southeast' names the plant only"
    assert [b.facility for b in g.basins] == ["Oceanside", "Bayside", "Bayside", "Bayside"]
    assert [b.rain_series for b in g.basins] == ["avg", "SF Downtown", "SF Downtown", "SF Downtown"]
    assert g.basin("central").report_basins == ("Central (Mission Creek)", "Central (Islais Creek)")
    assert g.basin("South") is g.basin("south") and g.zone_union == "max"


def test_sfpuc4_has_the_five_links():
    g = G.SFPUC4_V1
    assert [lk.id for lk in g.links] == list(LINKS)
    tag = {OBSERVED: "O", GEOGRAPHY: "G"}
    for lk in g.links:
        basin, zone, identity, outs = LINKS[lk.id]
        assert (lk.basin, lk.zone, lk.identity, lk.legacy_group) == (basin, zone, identity, None), lk.id
        assert {o: tag[OUTFALLS[o].evidence] for o in lk.outfalls} == outs, lk.id
    assert [lk.id for lk in g.links_into("east")] == ["central>east", "south>east"]
    assert [lk.id for lk in g.links_from("westside")] == ["westside>ocean", "westside>baker_china"]


def test_links_partition_the_outfalls():
    for g in (G.GEO_V1, G.SFPUC4_V1):
        flat = [o for lk in g.links for o in lk.outfalls]
        assert sorted(flat) == sorted(OUTFALLS) and len(flat) == len(set(flat)), g.version
        assert len({lk.id for lk in g.links}) == len(g.links), g.version
        for lk in g.links:
            for o in lk.outfalls:
                assert g.link_of_outfall(o) is lk and g.zone_of_outfall(o) == lk.zone, (g.version, o)
                assert {z for z, zone in ZONES.items() for s in OUTFALLS[o].stations if s in zone.station_ids} == {lk.zone}, o
            assert lk.identity == (len(g.links_from(lk.basin)) == 1), lk.id
        try:
            g.basin_of_outfall("CSD-999")
            raise AssertionError("an unknown outfall must raise")
        except KeyError:
            pass
    # an outfall posting two zones would sit in two links: both builders refuse it
    two = {**OUTFALLS, "CSD-004": dataclasses.replace(OUTFALLS["CSD-004"], stations=("4607", "4606"))}   # China + Pacheco
    for build in (lambda: G.build_geo_v1(two), lambda: G.build_sfpuc4(two)):
        try:
            build()
            raise AssertionError("an outfall posting two zones must raise")
        except ValueError:
            pass


def test_every_ledger_outfall_is_in_exactly_one_link():
    """A data test, here and never at import (Part C fix 11)."""
    with open(EVENTS, newline="") as fh:
        rows = list(csv.DictReader(fh))
    for oid in sorted({r["outfall_id"] for r in rows}):
        for g in (G.GEO_V1, G.SFPUC4_V1):
            assert sum(oid in lk.outfalls for lk in g.links) == 1, (g.version, oid)
    for r in rows:   # sfpuc4_v1 reads the registry's report basin; the ledger must agree with it
        assert OUTFALLS[r["outfall_id"]].report_basin == r["basin"], r["outfall_id"]


def test_station_basin_attributes_islais_to_central_and_candlestick_to_south():
    g = G.SFPUC4_V1
    got = {s.sfpuc_id: g.station_basin(s.sfpuc_id) for s in STATIONS.values()}
    assert got["4619"] == "central", "Islais: observed Central outfalls beat CSD-037 (geography)"
    assert got["4620"] == "central" and got["4618"] == "central", "Crane Cove, Mission Creek"
    assert {got[s] for s in ("4615", "4616", "4617")} == {"south"}, "Candlestick trio"
    assert {got[s] for s in ZONES["ocean"].station_ids + ZONES["baker_china"].station_ids} == {"westside"}
    assert {got[s] for s in ZONES["north"].station_ids} == {"north_shore"}
    # ambiguity → None (zone only): split Islais's observed outfalls, or Crane Cove's geography ones
    b = G.SFPUC4_V1.basins
    rest = tuple(o for o in OUTFALLS if o not in ("CSD-031", "CSD-029"))
    split = G.Geography("t", b, (G.Link("a", "central", "east", ("CSD-031", "CSD-029"), False),
                                 G.Link("b", "south", "east", rest, False)), "max")
    assert split.station_basin("4619") is None and split.station_basin("4620") is None
    assert split.station_basin("4617") == "south"
    for bad in ("BAY#320_SL", "9999"):   # a registry key or a typo must not read as "zone only"
        try:
            g.station_basin(bad)
            raise AssertionError(f"station_basin({bad!r}) did not raise")
        except KeyError:
            pass


# ── the label builders ──────────────────────────────────────────────────────

def test_ledger_event_days_per_sfpuc_basin_as_of():
    ev = pd.read_csv(EVENTS, parse_dates=["event_date"])
    ev = ev[ev["event_date"] <= AS_OF]
    ev["b"] = ev["outfall_id"].map(G.SFPUC4_V1.basin_of_outfall)
    got = {k: (d.nunique(), _windows(pd.Series(d.unique()))) for k, d in ev.groupby("b")["event_date"]}
    assert got == EVENT_DAYS, got
    assert ev["event_date"].nunique() == CITY_EVENT_DAYS
    ev["l"] = ev["outfall_id"].map(lambda o: G.SFPUC4_V1.link_of_outfall(o).id)   # §2.4's zone days per link
    links = {k: (d.nunique(), _windows(pd.Series(d.unique()))) for k, d in ev.groupby("l")["event_date"]}
    assert links == {"westside>ocean": (43, (27, 10, 6)), "westside>baker_china": (54, (31, 10, 13)),
                     "north_shore>north": EVENT_DAYS["north_shore"], "central>east": EVENT_DAYS["central"],
                     "south>east": EVENT_DAYS["south"]}, links


def test_build_daily_labels_default_and_geo_v1_are_identical():
    pd.testing.assert_frame_equal(_labels("geo_v1"), _labels(None), check_exact=True)
    assert [c for c in _labels(None).columns if c.endswith("_csd")] == ["Westside_csd", "North Shore_csd", "Central_csd", "Southeast_csd"]


def test_build_daily_labels_sfpuc4_columns_and_counts():
    s4, v1 = _labels("sfpuc4_v1"), _labels(None)
    assert [c for c in s4.columns if c.endswith("_csd")] == ["Westside_csd", "North Shore_csd", "Central_csd", "South_csd"]
    assert "Southeast_csd" not in s4.columns
    for k, (n, wins) in EVENT_DAYS.items():
        name = G.SFPUC4_V1.basin(k).name
        days = s4[(s4[f"{name}_csd"] == 1) & (s4[f"{name}_covered"] == 1) & (s4["date"] <= AS_OF)]["date"]
        assert (len(days), _windows(days)) == (n, wins), (k, len(days), _windows(days))
    same = [c for c in v1.columns if c.startswith(("Westside_", "North Shore_", "csd_any", "csd_outfalls", "fully_", "date"))]
    pd.testing.assert_frame_equal(s4[same], v1[same], check_exact=True)   # same outfalls, same ledger, same coverage
    assert ((s4["csd_volume_mg"] - v1["csd_volume_mg"]).abs() < 1e-9).all()   # summed in another order
    bay = lambda f, a, b, col: f[f"{a}_{col}"] + f[f"{b}_{col}"]  # noqa: E731
    assert ((bay(s4, "Central", "South", "volume_mg") - bay(v1, "Central", "Southeast", "volume_mg")).abs() < 1e-9).all()
    assert (bay(s4, "Central", "South", "outfalls") == bay(v1, "Central", "Southeast", "outfalls")).all()
    assert (s4["Central_covered"] == v1["Central_covered"]).all() and (s4["South_covered"] == v1["Southeast_covered"]).all()


def test_build_dataset_default_and_geo_v1_are_identical():
    (fd, nd), (fg, ng) = _dataset(None), _dataset("geo_v1")
    assert list(fd) == list(fg)
    for src in fd:
        pd.testing.assert_frame_equal(fg[src], fd[src], check_exact=True)
    ng = dict(ng)   # _dataset is cached: never mutate its notes
    assert ng.pop("geography") == "geo_v1" and ng == nd and "geography" not in nd


def test_build_dataset_sfpuc4_labels_and_archive():
    import train_v4
    (fd, nd), (fs, ns) = _dataset(None), _dataset("sfpuc4_v1")
    g = G.SFPUC4_V1
    f = fs["avg"]
    assert ns["geography"] == "sfpuc4_v1"
    for b in g.basins:
        sub = train_v4.target_frame(f, b.key, geo=g)
        assert sub.equals(train_v4.target_frame(f, b.name)) and sub.equals(train_v4.target_frame(f, b.name, geo=g))
        if b.key in EVENT_DAYS:
            n = int(sub[(sub["date"] <= AS_OF) & (sub[f"{b.name}_label_source"] == "ciwqs")]["y"].sum())
            assert n == EVENT_DAYS[b.key][0], (b.key, n)
    city = lambda fr, **kw: train_v4.target_frame(fr, "citywide", **kw)[["date", "y"]].reset_index(drop=True)  # noqa: E731
    assert city(f, geo=g).equals(city(fd["avg"]))
    a4, a1 = ns["archive_labels"], nd["archive_labels"]
    assert a4["onset_days"]["Westside"] == a1["onset_days"]["Westside"] and a4["onset_days"]["North Shore"] == a1["onset_days"]["North Shore"]
    # empty on both sides today (every Bayside onset falls on a CIWQS-covered day); the
    # real-archive relabel is pinned row by row in test_archive_onsets_split_by_basin_never_truncated
    assert sorted(a4["onset_days"]["Central"] + a4["onset_days"]["South"]) == sorted(a1["onset_days"]["Central"] + a1["onset_days"]["Southeast"])
    assert a4["new_covered_days"] == {"Westside": 283, "North Shore": 183, "Central": 183, "South": 183}, a4["new_covered_days"]
    for col in ("csd_any", "fully_covered", "precip_avg"):
        assert (f[col] == fd["avg"][col]).all(), col


def test_archive_onsets_split_by_basin_never_truncated():
    """Part B 22: a feed structure string spanning basins stamps every basin it names,
    each with its own outfalls (the served reading kept the first basin only)."""
    import train_v4
    d0 = pd.Timestamp("2016-05-02")
    onsets = pd.DataFrame([{"date": d0, "snapshot": d0 + pd.Timedelta(hours=15), "structure": "TEST",
                            "outfall_ids": "CSD-031|CSD-037|CSD-040", "basin": "Southeast", "mapped": True}])
    split = train_v4.geo_onsets(onsets, G.SFPUC4_V1)
    assert list(zip(split["basin"], split["outfall_ids"])) == [("Central", "CSD-031"), ("South", "CSD-037|CSD-040")]
    same = train_v4.geo_onsets(onsets, G.GEO_V1)
    assert list(zip(same["basin"], same["outfall_ids"])) == [("Southeast", "CSD-031|CSD-037|CSD-040")]
    # the real archive: no string spans basins today, so nothing splits or drops; Islais
    # Creek moves Southeast → Central, the Candlestick structures become South
    real = train_v4.archive_tables()["onsets"]
    for geo, want in ((G.GEO_V1, {"ISLAIS CREEK": "Southeast", "MISSION CREEK": "Central", "YOSEMITE": "Southeast",
                                  "SUNNYDALE": "Southeast", "SEA CLIFF II": "Westside", "BEACH STREET CSD13": "North Shore"}),
                      (G.SFPUC4_V1, {"ISLAIS CREEK": "Central", "MISSION CREEK": "Central", "YOSEMITE": "South",
                                     "SUNNYDALE": "South", "SEA CLIFF II": "Westside", "BEACH STREET CSD13": "North Shore"})):
        rel = train_v4.geo_onsets(real, geo)
        assert len(rel) == len(real) and (rel["outfall_ids"].values == real["outfall_ids"].values).all(), geo.version
        got = dict(zip(rel["structure"], rel["basin"]))
        assert {k: got[k] for k in want} == want, (geo.version, got)
    assert (train_v4.geo_onsets(real, G.GEO_V1)["basin"].values == real["basin"].values).all(), "geo_v1 relabel = the served reading"
    names = [b.name for b in G.SFPUC4_V1.basins]
    df = pd.DataFrame({"date": pd.date_range(d0 - pd.Timedelta(days=1), periods=3)})
    for n in names:
        df[f"{n}_csd"], df[f"{n}_outfalls"], df[f"{n}_covered"], df[f"{n}_volume_known"], df[f"{n}_label_source"] = 0, 0, 0, 0, ""
    arch = {"covered_dates": pd.DatetimeIndex(df["date"]), "onsets": onsets}
    notes = train_v4.apply_archive_labels(df, arch, pd.Series(0.0, index=df["date"]), geo=G.SFPUC4_V1)
    row = df[df["date"] == d0].iloc[0]
    assert (row["Central_csd"], row["Central_outfalls"], row["South_csd"], row["South_outfalls"]) == (1, 1, 1, 2)
    assert row["Westside_csd"] == 0 and row["North Shore_csd"] == 0
    assert notes["onset_days"] == {"Westside": [], "North Shore": [], "Central": ["2016-05-02"], "South": ["2016-05-02"]}


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
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
