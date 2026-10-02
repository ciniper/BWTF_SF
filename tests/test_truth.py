"""The stage truths (features/forecast/src/models/truth.py; STAGES_DESIGN.md §3.0–§3.6,
§4, §8 P4; Part B 3, 4, 8, 10, 22; STAGES_PROTOCOL.md §1, §6, §7).

Pins that the truths read the ledger, the gauges, the samples and BeachWatch the
way the protocol words them, and that the served scorecard agrees with them
wherever its labels came from the same ledger:

  - zone_overflow(GEO_V1) reproduces the served scorecard's zone discharge label
    on every day outside the Poo Bot archive window (2016-03-19 → 2017-01-10);
    inside it the differences are the feed's, listed and pinned;
  - the S3 oracle zone truth is the same under GEO_V1 and SFPUC4_V1 (§3.3);
  - out_label(GEO_V1) is scorecard.combined_label read on the ledger, with Part
    B 3's change (an exceedance with no overflow is a negative of the claim);
    every difference is classified by its reason and the counts are pinned;
  - counts against the design (§2.3, §3.3, §3.4, §4.3), each with an as-of date
    so a quarterly refresh that only adds later days stays green (Part B 23);
  - storms are verify's defaults on the masked two-gauge mean, and a missing
    gauge day is never a zero;
  - the CIWQS refresh (aggregate.py) stops on an outfall the registry lacks.

Where a measured count differs from the design's figure, the figure and the
reason sit beside the pin.

    venv/bin/python tests/test_truth.py
"""
from __future__ import annotations

import csv
import dataclasses
import gzip
import json
import subprocess
import sys
import tempfile
import traceback
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
CIWQS_DIR = FORECAST / "src" / "collectors" / "csd_ciwqs"
for p in (ROOT, FORECAST / "src" / "models", FORECAST / "src" / "collectors", CIWQS_DIR, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import scorecard as SC  # noqa: E402
import truth as T  # noqa: E402
import verify  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.zones import ZONES  # noqa: E402
from test_geography import AS_OF as LEDGER_AS_OF, EVENT_DAYS, _windows  # noqa: E402  (§2.3's event days, pinned once)

AS_OF = pd.Timestamp("2026-08-17")            # the committed data's end: the served scorecard's last day
SERVED_SCORECARD = FORECAST / "data" / "models" / "scorecard.json.gz"
EVENTS = FORECAST / "data" / "csd" / "sf_csd_events.csv"
WINDOW = (T.ARCHIVE_START, T.ARCHIVE_END)
WINDOW_TAIL = (T.ARCHIVE_START, T.ARCHIVE_END + pd.Timedelta(days=T.TAIL_DAYS))   # a feed onset's tail reaches 7 days past it

# ── pins (as of AS_OF unless noted) ─────────────────────────────────────────

# In the archive window the scorecard took Westside's labels and some Bay-side onsets from the feed.
# (zone, scorecard discharge, truth y) → zone-days; None = not known. The six True/False rows are design
# Part C fix 22's list exactly (feed onsets on days the ledger knows and files nothing for the zone).
WINDOW_LABEL_DIFFS = {
    ("ocean", False, None): 278, ("ocean", True, None): 5,
    ("baker_china", False, None): 279, ("baker_china", True, None): 4,
    ("north", False, None): 38, ("north", None, False): 7, ("north", True, False): 2,
    ("east", False, None): 38, ("east", None, False): 7, ("east", True, False): 4,
}
WINDOW_VALUE_DIFFS = [("2016-10-17", "east"), ("2016-11-23", "east"), ("2016-12-16", "north"), ("2016-12-16", "east"),
                      ("2016-12-23", "north"), ("2016-12-23", "east")]

# out_label(GEO_V1) vs scorecard.combined_label on the served scorecard's days through AS_OF, by reason:
#   part_b3          combined 'scope' (an exceedance with no overflow in the week) is a good day of the claim
#   feed_onset       the scorecard's discharge on D or in its week came from the feed, not the ledger
#   feed_only_day    the scorecard knew D through the feed archive; the ledger does not cover it
#   legacy_month     Bayside 2016 months the grid files as "no discharge": the ledger knows them, the
#                    scorecard (no feed snapshot that day) did not
#   history_unknown  D is known but a day of D−7…D−1 is not; combined_label checks D alone (§3.6: "a quiet
#                    day with the history known")
OUT_DIFFS = {
    "ocean": {"feed_only_day": 225, "feed_onset": 6, "history_unknown": 7, "part_b3": 14},
    "baker_china": {"feed_only_day": 227, "feed_onset": 4, "history_unknown": 7, "part_b3": 55},
    "north": {"feed_only_day": 32, "feed_onset": 8, "history_unknown": 13, "legacy_month": 6, "part_b3": 56},
    "east": {"feed_only_day": 34, "feed_onset": 11, "history_unknown": 12, "legacy_month": 6, "part_b3": 175},
}

# Ledger-unknown basin-days, TRUTH_START → AS_OF: (archive = X-S2-ARCHIVE, uncovered = X-S2-UNCOV).
# Design §4.3: Westside 283 + 374 (same); each Bay basin 183 + 31. The protocol's rule is the facility-month
# status (§1), and the grid files Bayside Feb, Apr, May, Jun, Jul and Sep 2016 as "no discharge" (stated, or a
# zero-event table); the served frames never labelled them because build_daily_labels starts at the first
# event month (2016-10). By the protocol's words they are known: only Mar and Aug 2016 stay unknown.
UNKNOWN_BASIN_DAYS = {"westside": (283, 374), "north_shore": (38, 24), "central": (38, 24), "south": (38, 24)}

# §4.3 X-S2-CARRY (SFPUC4): Central 17, North Shore 4, South 3, Westside 3, as the design says.
CARRY = {"basin": {"westside": 3, "north_shore": 4, "central": 17, "south": 3},
         "link": {"westside>ocean": 5, "westside>baker_china": 0, "north_shore>north": 4, "central>east": 17, "south>east": 3},
         "zone": {"ocean": 5, "baker_china": 0, "north": 4, "east": 16}}
EVENT_FACTS = {"rows": 1104, "start_unparsed": 3, "duration_unknown": 5, "vol_null": 5, "vol_lt": 45, "cross_midnight": 122}
VOLQ_BASIN_DAYS = {"westside": 16, "north_shore": 9, "central": 15, "south": 2}

# §3.3 / §2.4: zone overflow days and the X-S3-GEO tag (Baker & China 7 of 54, East 6 of 100), as the design says.
ZONE_DAYS = {"ocean": 43, "baker_china": 54, "north": 42, "east": 100}
ZONE_GEO_ONLY = {"ocean": 0, "baker_china": 7, "north": 0, "east": 6}

# §3.4 power: sampled zone-days (DataSF + Poo Bot), TRUTH_START → AS_OF: the design's 451 / 483 / 473 / 761.
# With the history known (scored after X-S4-HISTUNK) the design has 390 / 418 / 439 / 712: Westside matches;
# North and East gain the Bayside 2016 "no discharge" months above.
SAMPLED = {"ocean": 451, "baker_china": 483, "north": 473, "east": 761}
SAMPLED_HIST_KNOWN = {"ocean": 390, "baker_china": 418, "north": 460, "east": 738}

# Rain storms (protocol §6) on the gauge record 2016-01-01 → AS_OF, and storm-level zone truth (§3.3 a).
N_STORMS, N_BLOCKS, N_STORMS_IN_SPAN = 146, 482, 141
STORM_ZONE_POSITIVE = {"ocean": 29, "baker_china": 34, "north": 33, "east": 56}

# S1 status per gauge-day, 2016-01-01 → AS_OF; 12 gauge_outage_v1 runs ending by AS_OF, 378 gauge-days
# (design §4.3: same). Runs are found on the whole record, so only those that end by AS_OF are pinned.
GAUGE_STATUS = {"SF Downtown": {"ok": 3808, "missing": 37, "outage": 37},
                "SF Oceanside": {"ok": 3126, "missing": 415, "outage": 341}}
N_OUTAGE_RUNS, N_OUTAGE_DAYS = 12, 378
# BeachWatch's last SF filing at the 2026-09-26 pull (X-PL-END: 170 days to AS_OF). A refresh moves the
# span end forward, never back, so the pin is exact only for that pull.
POSTINGS_END, POSTINGS_PULL, PL_END_DAYS = pd.Timestamp("2026-02-28"), "2026-09-26", 170


def _scorecard() -> dict:
    with gzip.open(SERVED_SCORECARD, "rt") as f:
        return json.load(f)


def _in(d, lo_hi) -> bool:
    return lo_hi[0] <= pd.Timestamp(d) <= lo_hi[1]


def _na(v) -> bool:
    return v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v))


# ── the module ──────────────────────────────────────────────────────────────

def test_import_reads_no_data():
    """No module-level IO: importing truth opens nothing under features/forecast/data."""
    code = ("import builtins, io, sys; sys.path.insert(0, %r)\n"
            "real = io.open\n"
            "def spy(f, *a, **k):\n"
            "    if '/features/forecast/data/' in str(f): raise AssertionError(f'opened {f} at import')\n"
            "    return real(f, *a, **k)\n"
            "builtins.open = io.open = spy\n"
            "import truth\n"
            "assert truth._gauge_record.cache_info().currsize == 0 and truth._events.cache_info().currsize == 0\n"
            "print('inert')") % str(FORECAST / "src" / "models")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().endswith("inert"), r.stderr[-800:]


def test_the_exclusion_ids_named_are_the_protocols():
    """Every X-/C- id truth.py names for the exclusion rules is in the catalog (stages_spec.EXCLUSIONS, the
    protocol's §7 ids), and X-E2E-SCOPE, which Part B 3 dropped, is not named as a rule."""
    import re
    import stages_spec
    src = Path(T.__file__).read_text()
    named = set(re.findall(r"\b[XC]-[A-Z0-9]+(?:-[A-Z0-9]+)*", src))
    assert named and named <= set(stages_spec.EXCLUSIONS), sorted(named - set(stages_spec.EXCLUSIONS))
    assert "X-E2E-SCOPE" not in named


def test_geography_is_explicit_and_unknowns_raise():
    for bad in ("", "GEO_V1", "sfpuc4"):
        try:
            T.zone_overflow(bad, end=AS_OF)
            raise AssertionError(f"geo {bad!r} did not raise")
        except KeyError:
            pass
    try:
        T.basin_onsets(None)
        raise AssertionError("geo=None must raise: there is no default geography")
    except TypeError:
        pass
    g = G.SFPUC4_V1
    lacks = G.Geography("t", g.basins, tuple(lk for lk in g.links if lk.id != "south>east"), "max")
    try:
        T.ledger_events(lacks)
        raise AssertionError("an outfall the geography lacks must raise")
    except KeyError:
        pass
    # a ledger event on a day its basin's facility did not file is a contradiction, not a zero
    swapped = G.Geography("t2", tuple(dataclasses.replace(b, facility="Oceanside") if b.key == "central" else b for b in g.basins),
                          g.links, "max")
    try:
        T.basin_onsets(swapped, end=AS_OF)
        raise AssertionError("events on unfiled days must raise")
    except ValueError as e:
        assert "did not file" in str(e), e
    try:
        T.carry_days(g, level="station")
        raise AssertionError("an unknown level must raise")
    except ValueError:
        pass


# ── (1) the served scorecard's zone discharge label ─────────────────────────

def test_zone_overflow_reproduces_the_scorecard_outside_the_archive_window():
    sc = _scorecard()
    zo = T.zone_overflow(G.GEO_V1, end=sc["span"][1])
    truth = {(z, d.strftime("%Y-%m-%d")): (None if _na(y) else bool(y)) for z, d, y in zip(zo["zone"], zo["date"], zo["y"])}
    outside, inside, values = [], Counter(), []
    for day in sc["days"]:
        for zk, z in day["zones"].items():
            a, b = z["discharge"], truth[(zk, day["date"])]
            if a == b:
                continue
            if not _in(day["date"], WINDOW):
                outside.append((day["date"], zk, a, b))
            elif pd.Timestamp(day["date"]) <= AS_OF:
                inside[(zk, a, b)] += 1
                if a is not None and b is not None:
                    values.append((day["date"], zk))
    assert outside == [], outside[:10]
    assert dict(inside) == WINDOW_LABEL_DIFFS, dict(inside)
    assert sorted(values) == sorted(WINDOW_VALUE_DIFFS), values


def test_zone_elevated_is_the_scorecards_elevated_label():
    sc = _scorecard()
    el = T.zone_elevated(G.GEO_V1, end=sc["span"][1])
    mine = {(z, d.strftime("%Y-%m-%d")): bool(y) for z, d, y in zip(el["zone"], el["date"], el["y"])}
    bad = [(day["date"], zk) for day in sc["days"] for zk, z in day["zones"].items()
           if z["elevated"] != mine.get((zk, day["date"]))]
    assert bad == [], bad[:10]


# ── (2) geography invariance (§3.3) ─────────────────────────────────────────

def test_zone_truth_is_the_same_under_both_geographies():
    """Every outfall posts one zone, and a zone's feeding basins share a facility, so the zone
    truths cannot tell the geographies apart. Only the fired link ids are geography words."""
    a, b = T.zone_overflow(G.GEO_V1, end=AS_OF), T.zone_overflow(G.SFPUC4_V1, end=AS_OF)
    pd.testing.assert_frame_equal(a.drop(columns="links"), b.drop(columns="links"), check_exact=True)
    assert (a["links"] != b["links"]).any(), "GEO_V1 splits North Shore's cell into two links"
    ca, cb = T.carry_days(G.GEO_V1, "zone", end=AS_OF), T.carry_days(G.SFPUC4_V1, "zone", end=AS_OF)
    pd.testing.assert_frame_equal(ca, cb)
    ea, eb = T.zone_elevated(G.GEO_V1, end=AS_OF), T.zone_elevated(G.SFPUC4_V1, end=AS_OF)
    pd.testing.assert_frame_equal(ea, eb)
    oa, ob = T.out_label(G.GEO_V1, end=AS_OF), T.out_label(G.SFPUC4_V1, end=AS_OF)
    pd.testing.assert_frame_equal(oa, ob)
    sa, sb = T.zone_overflow_storm(G.GEO_V1, end=AS_OF), T.zone_overflow_storm(G.SFPUC4_V1, end=AS_OF)
    pd.testing.assert_frame_equal(sa, sb)
    # not equal because both are empty or flat: every zone has positives, negatives and unknowns in each frame
    for z in ZONES:
        for f, col in ((a, "y"), (oa, "y"), (sa, "y")):
            g = f.loc[f["zone"] == z, col]
            assert (g == 1).sum() > 0 and (g == 0).sum() > 0 and g.isna().sum() > 0, (z, col)
        assert ca.loc[ca["zone"] == z, "n_active"].sum() > 0 or z == "baker_china", z
        assert eb.loc[eb["zone"] == z, "dayof"].any() and eb.loc[eb["zone"] == z, "y"].sum() > 0, z


# ── OUT: combined_label on the ledger, with Part B 3 ────────────────────────

def _out_reason(day: dict, zk: str, verdict: str, row, by_date: dict, ledger_y: dict) -> str:
    """The reason the two labels part, or 'unexplained'. Each reason also names the label pair it can
    produce, so a wrong truth label on such a day still fails instead of hiding under the reason."""
    def back(k):
        return str(date.fromisoformat(day["date"]) - timedelta(days=k))
    feed = any((by_date.get(back(k)) or {}).get("zones", {}).get(zk, {}).get("discharge")
               and ledger_y.get((zk, back(k))) != 1 for k in range(0, T.TAIL_DAYS + 1))
    if verdict == "scope" and row.why == "exceedance_no_overflow":
        return "part_b3"
    if feed:
        return "feed_onset"
    if day["zones"][zk]["discharge"] is not None and not row.known and row.why == "uncovered":
        return "feed_only_day"
    if day["zones"][zk]["discharge"] is None and row.known and verdict == "unknown" and row.label != "unknown":
        return "legacy_month"
    if row.known and not row.hist_known and row.why == "uncovered" and verdict in ("good", "scope"):
        return "history_unknown"
    return f"unexplained: combined {verdict}, truth {row.label}/{row.why}"


def test_out_label_is_the_combined_label_on_the_ledger():
    sc = _scorecard()
    ol = T.out_label(G.GEO_V1, end=sc["span"][1])
    rows = {(z, d.strftime("%Y-%m-%d")): r for z, d, r in zip(ol["zone"], ol["date"], ol.itertuples(index=False))}
    zo = T.zone_overflow(G.GEO_V1, end=sc["span"][1])
    ledger_y = {(z, d.strftime("%Y-%m-%d")): (None if _na(y) else int(y)) for z, d, y in zip(zo["zone"], zo["date"], zo["y"])}
    by_date = {d["date"]: d for d in sc["days"]}
    pinned, outside = defaultdict(Counter), Counter()
    for zk in sc["zones"]:
        dis = {d["date"] for d in sc["days"] if d["zones"][zk]["discharge"]}
        for day in sc["days"]:
            verdict, _ = SC.combined_label(day, zk, dis)
            row = rows[(zk, day["date"])]
            if verdict == row.label:
                continue
            reason = _out_reason(day, zk, verdict, row, by_date, ledger_y)
            assert not reason.startswith("unexplained"), (day["date"], zk, reason)
            if pd.Timestamp(day["date"]) <= AS_OF:
                pinned[zk][reason] += 1
            if not _in(day["date"], WINDOW_TAIL):
                outside[reason] += 1
    assert {z: dict(c) for z, c in pinned.items()} == OUT_DIFFS, {z: dict(c) for z, c in pinned.items()}
    # outside the feed's window and its tail, only the two rule changes remain
    assert set(outside) <= {"part_b3", "history_unknown"}, outside


def test_out_label_rows_follow_their_reasons():
    o = T.out_label(G.SFPUC4_V1, end=AS_OF)
    over = (o["overflow"] == 1).fillna(False).astype(bool)
    want = {
        "overflow": over,
        "sample_after_overflow": ~over & o["exceed"] & o["tail"],
        "clean_sample": ~over & o["sampled"] & ~o["exceed"],
        "exceedance_no_overflow": ~over & o["exceed"] & ~o["tail"] & o["hist_known"],
        "quiet": ~o["sampled"] & ~o["tail"] & o["hist_known"] & ~over,
        "tail_unsampled": ~o["sampled"] & o["tail"] & ~over,
        # unknown only for want of the ledger: not an overflow, not a clean sample, not an exceedance after one
        "uncovered": ~over & ~o["hist_known"] & ~(o["sampled"] & ~o["exceed"]) & ~(o["exceed"] & o["tail"])
                     & ~(~o["sampled"] & o["tail"]),
    }
    assert set(want) == set(T.OUT_WHY), "every reason is checked"
    for why, mask in want.items():
        assert (mask[o["why"] == why]).all(), why
        assert set(o.loc[o["why"] == why, "label"]) <= {T.OUT_WHY[why][0]}, why
    assert set(o["why"]) <= set(T.OUT_WHY)
    assert (o["exceed"] <= o["sampled"]).all()
    assert (o["y"].isna() == (o["label"] == "unknown")).all()
    assert (o.loc[o["label"] == "bad", "y"] == 1).all() and (o.loc[o["label"] == "good", "y"] == 0).all()
    assert o["overflow"].isna().equals(~o["known"]), "overflow is <NA> exactly where the ledger does not know D"
    assert len(o) == len(ZONES) * len(pd.date_range(T.TRUTH_START, AS_OF))


# ── (3) pinned counts, as of a date ─────────────────────────────────────────

def test_basin_event_days_as_of():
    """§2.3's event days per SFPUC basin (test_geography.EVENT_DAYS, as of its LEDGER_AS_OF), from basin_onsets."""
    b = T.basin_onsets(G.SFPUC4_V1, end=LEDGER_AS_OF)
    for k, (n, wins) in EVENT_DAYS.items():
        d = b[(b["basin"] == k) & (b["y"] == 1)]["date"]
        assert (len(d), _windows(d)) == (n, wins), (k, len(d), _windows(d))
    # GEO_V1 relabels the same ledger: Westside and North Shore are the same outfalls, and a Bay-side day
    # with an SFPUC4 Central or South onset is a day with an onset in GEO_V1's central or southeast key
    v1 = T.basin_onsets(G.GEO_V1, end=LEDGER_AS_OF)
    for k in ("westside", "north_shore"):
        pd.testing.assert_frame_equal(v1[v1["basin"] == k].reset_index(drop=True), b[b["basin"] == k].reset_index(drop=True))

    def bay(f, keys):
        return f[f["basin"].isin(keys)].assign(on=lambda x: x["y"] == 1).groupby("date")["on"].any()
    assert bay(v1, ["central", "southeast"]).equals(bay(b, ["central", "south"]))


def test_ledger_unknown_basin_days_as_of():
    for geo in (G.SFPUC4_V1, G.GEO_V1):
        lk = T.ledger_known(geo, end=AS_OF)
        got = {k: (int(g["archive"].sum()), int((~g["known"] & ~g["archive"]).sum())) for k, g in lk.groupby("basin", sort=False)}
        want = {("south" if geo is G.SFPUC4_V1 else "southeast") if k == "south" else k: v for k, v in UNKNOWN_BASIN_DAYS.items()}
        assert got == want, (geo.version, got)
        assert not (lk["archive"] & lk["known"]).any()
        assert lk.loc[lk["archive"], "date"].between(*WINDOW).all()
    b = T.basin_onsets(G.SFPUC4_V1, end=AS_OF)
    assert b["y"].isna().equals(~b["known"]) and b["volume_mg"].isna().sum() >= (~b["known"]).sum()
    assert len(b) == 4 * len(pd.date_range(T.TRUTH_START, AS_OF))


def test_events_carry_days_and_volume_qualifiers_as_of():
    ev = T.ledger_events(G.SFPUC4_V1)
    ev = ev[ev["date"] <= AS_OF]
    got = {"rows": len(ev), "start_unparsed": int((~ev["start_parsed"]).sum()), "duration_unknown": int((~ev["duration_known"]).sum()),
           "vol_null": int(ev["vol_null"].sum()), "vol_lt": int(ev["vol_lt"].sum()),
           "cross_midnight": int((ev["end"] > ev["date"] + pd.Timedelta(days=1)).sum())}
    assert got == EVENT_FACTS, got
    unparsed = ev[~ev["start_parsed"]]
    assert (unparsed["start"] == unparsed["date"]).all(), "no parseable start: counts as starting on event_date"
    for level, want in CARRY.items():
        c = T.carry_days(G.SFPUC4_V1, level, end=AS_OF)
        got = {u: int(g["carry"].sum()) for u, g in c.groupby(level, sort=False)}
        assert got == want, (level, got)
    onsets = T.basin_onsets(G.SFPUC4_V1, end=AS_OF)
    c = T.carry_days(G.SFPUC4_V1, "basin", end=AS_OF)
    assert not (c["carry"].to_numpy() & (onsets["y"] == 1).fillna(False).to_numpy()).any(), "a carry day has no onset"
    got = {k: int(g["volq"].sum()) for k, g in onsets.groupby("basin", sort=False)}
    assert got == VOLQ_BASIN_DAYS, got
    assert (onsets["volq"] <= (onsets["n_events"] > 0)).all()
    # volume = Σ of measured volumes (protocol §1): a blank or '<' volume is not one (X-S2-VOLQ), so a basin-day
    # whose every event is qualified has no volume, never its '<' bound or a zero
    measured = ev[~ev["vol_lt"] & ~ev["vol_null"]].groupby(["basin", "date"])["volume_mg"].sum()
    fired = onsets[onsets["n_events"] > 0].set_index(["basin", "date"])
    want = measured.reindex(fired.index)
    assert ((fired["volume_mg"] - want).abs() < 1e-9).sum() + (fired["volume_mg"].isna() & want.isna()).sum() == len(fired)
    only_q = fired[fired["n_vol_lt"] + fired["n_vol_null"] == fired["n_events"]]
    assert len(only_q) == 3 and only_q["volume_mg"].isna().all() and only_q["volq"].all(), only_q
    assert (onsets.loc[onsets["known"] & (onsets["n_events"] == 0), "volume_mg"] == 0).all()


def test_zone_overflow_days_and_geography_tag_as_of():
    zo = T.zone_overflow(G.SFPUC4_V1, end=AS_OF)
    assert {z: int((g["y"] == 1).sum()) for z, g in zo.groupby("zone", sort=False)} == ZONE_DAYS
    assert {z: int(g["only_geography"].sum()) for z, g in zo.groupby("zone", sort=False)} == ZONE_GEO_ONLY
    assert zo["y"].isna().equals(~zo["known"])
    assert (zo["hist_known"] <= zo["known"]).all()
    # the history is D−7…D, eight days: Westside's ledger starts 2017-12-01, so the first day whose week is known
    # is 2017-12-08 (a seven- or nine-day window moves it)
    oc = zo[(zo["zone"] == "ocean") & (zo["date"] >= "2017-11-01")]
    assert oc.loc[oc["known"], "date"].min() == pd.Timestamp("2017-12-01")
    assert oc.loc[oc["hist_known"], "date"].min() == pd.Timestamp("2017-12-08")
    links = T.link_onsets(G.SFPUC4_V1, end=AS_OF)
    by_link = {lk: int((g["y"] == 1).sum()) for lk, g in links.groupby("link", sort=False)}
    assert by_link == {"westside>ocean": 43, "westside>baker_china": 54, "north_shore>north": 42, "central>east": 97, "south>east": 23}
    # a zone overflows exactly when one of its links does (East is Central or South)
    lz = links.assign(on=(links["y"] == 1).fillna(False)).groupby(["zone", "date"])["on"].any()
    assert ((zo.set_index(["zone", "date"])["y"] == 1).fillna(False) == lz.reindex(zo.set_index(["zone", "date"]).index)).all()


def test_sampled_zone_days_as_of():
    el = T.zone_elevated(G.SFPUC4_V1, end=AS_OF)
    assert {z: int(len(g)) for z, g in el.groupby("zone", sort=False)} == SAMPLED
    assert {z: int(g["hist_known"].sum()) for z, g in el.groupby("zone", sort=False)} == SAMPLED_HIST_KNOWN
    assert (el["n_stations_sampled"] <= el["n_stations"]).all() and (el["few"] == (2 * el["n_stations_sampled"] < el["n_stations"])).all()
    assert el["max_ratio"].notna().all() and ((el["max_ratio"] > 1) >= (el["y"] == 1)).all()
    assert el.groupby("zone")["n_stations"].first().to_dict() == {k: len(z.station_ids) for k, z in ZONES.items()}
    # a resample day follows an exceedance in the zone on D−1 or D−2 (Part B 4)
    hit = {(z, d) for z, d, y in zip(el["zone"], el["date"], el["y"]) if y}
    for z, d, fl in zip(el["zone"], el["date"], el["first_look"]):
        if pd.Timestamp(d) - pd.Timedelta(days=2) >= T.TRUTH_START:
            assert fl == (not any((z, d - pd.Timedelta(days=k)) in hit for k in (1, 2))), (z, d)


def test_storms_and_storm_level_zone_truth_as_of():
    st = T.storms(end=AS_OF)
    assert len(st) == N_STORMS and T.blocks(end=AS_OF)["block"].nunique() == N_BLOCKS
    s3 = T.zone_overflow_storm(G.SFPUC4_V1, end=AS_OF)
    assert s3["storm"].nunique() == N_STORMS_IN_SPAN
    assert {z: int((g["y"] == 1).sum()) for z, g in s3.groupby("zone", sort=False)} == STORM_ZONE_POSITIVE
    assert s3.attrs["overflow_days_outside_storms"] == {z: 0 for z in ZONES}, "every zone overflow day falls in a rain storm"
    assert (s3["y"].isna() == ((s3["n_overflow_days"] == 0) & (s3["n_days_known"] < s3["n_days"]))).all()


def test_postings_and_their_end():
    import beachwatch as BW
    lo, hi = T.postings_span()
    assert hi >= POSTINGS_END, hi
    if json.loads(BW.MANIFEST.read_text())["fetched_at"].startswith(POSTINGS_PULL):
        assert hi == POSTINGS_END, hi
        assert len(pd.date_range(hi + pd.Timedelta(days=1), AS_OF)) == PL_END_DAYS, "X-PL-END days to AS_OF (design §4.3)"
    p = T.postings()
    assert set(p["zone"]) <= set(ZONES) and set(p["cause_class"]) <= {"cso", "rain", "other"}
    assert p["date"].max() <= hi and not p.duplicated(["zone", "date"]).any()
    for z, g in p.groupby("zone"):
        d = g["date"].reset_index(drop=True)
        assert (g["onset"].to_numpy() == np.r_[True, (d.diff().dt.days != 1).to_numpy()[1:]]).all(), z
        assert (g.loc[g["onset"], "class_onset"]).all(), z


def test_gauge_status_and_outage_runs_as_of():
    g = T.gauges(end=AS_OF)
    got = {s: g[g["series"] == s]["status"].value_counts().to_dict() for s in T.GAUGE_SERIES}
    assert got == GAUGE_STATUS, got
    runs = [r for r in T.outage_runs() if pd.Timestamp(r["end"]) <= AS_OF]
    assert (len(runs), sum(r["days"] for r in runs)) == (N_OUTAGE_RUNS, N_OUTAGE_DAYS)
    assert N_OUTAGE_DAYS == sum(c["outage"] for c in got.values()), "every outage gauge-day is inside a run"
    avg = g[g["series"] == T.MEAN_SERIES]
    assert avg["rain"].notna().all() and (avg["status"] == "ok").all(), "through AS_OF one gauge always stands"


# ── (4) storms are verify's defaults; a missing day is never a zero ─────────

def test_storms_are_verify_defaults_on_the_masked_mean():
    """Protocol §6's numbers are verify's defaults, and blocks/storms use them on the masked two-gauge mean."""
    assert T.storm_rule() == {"wet": 0.1, "gap": 2, "pad": (1, 7)}
    rain = T.gauge_rain(end=AS_OF)[T.MEAN_SERIES]
    spans = verify.storm_spans(rain.index, rain.to_numpy(), wet=0.1, gap=2)
    st = T.storms(end=AS_OF)
    assert list(zip(st["start"], st["end"])) == spans
    b = T.blocks(end=AS_OF)
    assert (b["block"].to_numpy() == verify.storm_blocks(rain.index, rain.to_numpy(), wet=0.1, gap=2, pad=(1, 7))).all()
    # the words, checked directly: wet ends; at most 2 dry days inside; at least 3 between storms
    wet = set(b.loc[b["wet"], "date"])
    for s, e in spans:
        assert s in wet and e in wet
        inside = sorted(d for d in wet if s <= d <= e)
        assert all((y - x).days <= 3 for x, y in zip(inside, inside[1:]))
    assert all((s2 - e1).days > 3 for (_, e1), (s2, _) in zip(spans, spans[1:]))
    assert (b.loc[b["storm"] >= 0, "block_kind"] == "storm").all()
    masked = T.gauges(end=AS_OF)
    assert np.allclose(b["rain"], masked[masked["series"] == T.MEAN_SERIES]["rain"]), "storms read the masked mean"


def test_gauges_never_zero_fill_a_missing_day():
    raw = pd.read_csv(T.RAIN_CSV, parse_dates=["date"])
    g = T.gauges(end=AS_OF)
    for s in T.GAUGE_SERIES:
        r = raw[(raw["rain_station_name"] == s) & (raw["date"] <= AS_OF)].set_index("date")["precip_inches"]
        mine = g[g["series"] == s].set_index("date")
        assert mine.loc[r.index[r.isna()], "rain"].isna().all() and (mine.loc[r.index[r.isna()], "status"] == "missing").all(), s
        assert mine.loc[mine["status"] != "ok", "rain"].isna().all(), s
    # a fixture record: a day absent from the file, a dead-gauge run, a trace, a one-gauge gap
    days = pd.date_range("2020-01-01", "2020-01-10")
    dt = {d: 0.3 for d in days} | {days[7]: T.TRACE_IN}
    oc = {d: 0.1 for d in days} | {days[1]: 0.0, days[2]: 0.0, days[8]: None}
    rows = [{"date": d.date(), "rain_station": sid, "rain_station_name": name, "basin": "x", "precip_inches": v[d]}
            for name, sid, v in (("SF Downtown", "047772", dt), ("SF Oceanside", "047767", oc)) for d in days if d != days[4]]
    old = T.RAIN_CSV
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "historical_rain.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        T.RAIN_CSV = path
        T._gauge_record.cache_clear()
        try:
            f = T.gauges()
            w = f.pivot(index="date", columns="series", values="rain")
            st = f.pivot(index="date", columns="series", values="status")
            assert w.loc[days[4]].isna().all() and (st.loc[days[4]] == "missing").all(), "a day absent from the file is missing"
            assert st.loc[days[1], "SF Oceanside"] == "outage" and np.isnan(w.loc[days[1], "SF Oceanside"])
            assert w.loc[days[1], T.MEAN_SERIES] == 0.3, "the mean falls back to the live gauge"
            assert w.loc[days[7], "SF Downtown"] == 0.0, "a trace scores 0"
            assert st.loc[days[8], "SF Oceanside"] == "missing" and w.loc[days[8], T.MEAN_SERIES] == 0.3
            assert np.isclose(w.loc[days[0], T.MEAN_SERIES], 0.2)
            assert T.outage_runs() == [{"gauge": "SF Oceanside", "start": "2020-01-02", "end": "2020-01-03", "days": 2, "other_total": 0.6}]
            assert np.isnan(T.blocks().set_index("date").loc[days[4], "rain"])
            try:
                T.gauges(end="2020-01-11")
                raise AssertionError("asking past the record must raise")
            except ValueError:
                pass
        finally:
            T.RAIN_CSV = old
            T._gauge_record.cache_clear()


def test_gauges_match_the_s1_evaluation_truth():
    """weather_models_eval.load_gauges is the S1 report's truth: same masking, same trace rule, same values."""
    import weather_models_eval as W
    lg = W.load_gauges()
    g = T.gauges()
    for s in T.SERIES:
        mine = g[g["series"] == s].set_index("date")
        theirs = lg["truth"][s]
        idx = theirs.index
        a, b = mine.loc[idx, "rain"], theirs
        assert ((a == b) | (a.isna() & b.isna())).all(), s
        assert (mine.loc[idx, "status"] == lg["status"][s]).all(), s


# ── archive onsets (Part B 22) ──────────────────────────────────────────────

def test_archive_onsets_split_through_the_geography():
    a = T.archive_onsets(G.SFPUC4_V1)
    assert len(a) == 38 and set(a["basin"]) <= set(G.SFPUC4_V1.keys)
    assert a["date"].between(*WINDOW).all()
    islais = a[a["structure"] == "ISLAIS CREEK"]
    assert set(islais["basin"]) == {"central"} and set(islais["zone"]) == {"east"}
    assert set(T.archive_onsets(G.GEO_V1)[lambda f: f["structure"] == "ISLAIS CREEK"]["basin"]) == {"southeast"}


def test_archive_onsets_split_a_multi_basin_string():
    """No committed onset spans basins, so a fixture: a string over Central and South is two SFPUC4 rows,
    each with its own outfalls, and one GEO_V1 row (both are Southeast there); a string over two zones of
    one basin cannot be placed and raises instead of keeping the first zone."""
    import train_v4
    real = train_v4.archive_tables
    d = pd.Timestamp("2016-12-16")

    def fake(ids):
        return lambda: {"onsets": pd.DataFrame({"date": [d], "snapshot": [d + pd.Timedelta(hours=8)], "structure": ["X"],
                                                "outfall_ids": [ids], "basin": ["Southeast"], "mapped": [True]})}
    try:
        train_v4.archive_tables = fake("CSD-031|CSD-040|CSD-041")
        a = T.archive_onsets(G.SFPUC4_V1)
        assert list(zip(a["basin"], a["outfall_ids"], a["zone"])) == [("central", "CSD-031", "east"),
                                                                     ("south", "CSD-040|CSD-041", "east")], a
        v1 = T.archive_onsets(G.GEO_V1)
        assert list(zip(v1["basin"], v1["outfall_ids"])) == [("southeast", "CSD-031|CSD-040|CSD-041")], v1
        train_v4.archive_tables = fake("CSD-003|CSD-005")
        try:
            T.archive_onsets(G.SFPUC4_V1)
            raise AssertionError("an onset over two zones of one basin must raise")
        except ValueError:
            pass
    finally:
        train_v4.archive_tables = real


def test_the_feed_never_makes_a_day_known_or_an_overflow():
    """Part C fix 22 / protocol §1: truth is the ledger. ``known`` is the facility's filed months and nothing
    else, no archive day is known, and a feed onset the ledger lacks is never a positive."""
    import csd_labels
    for geo in (G.SFPUC4_V1, G.GEO_V1):
        lk = T.ledger_known(geo, end=AS_OF)
        for b in geo.basins:
            g = lk[lk["basin"] == b.key]
            assert g["known"].equals(g["date"].isin(csd_labels.facility_covered_dates(b.facility))), (geo.version, b.key)
        assert not (lk["archive"] & lk["known"]).any()
        zo = T.zone_overflow(geo, end=AS_OF).set_index(["zone", "date"])
        ev = set(zip(T.ledger_events(geo)["zone"], T.ledger_events(geo)["date"]))
        on = T.archive_onsets(geo)
        feed_only = {(z, d) for z, d in zip(on["zone"], on["date"]) if (z, d) not in ev}
        y = {k: zo.loc[k, "y"] for k in feed_only}
        # the ledger knows Part C fix 22's six and files nothing for the zone: a negative, not the feed's onset;
        # the other feed-only onsets are Westside's, on days the ledger does not cover: unknown, never a zero
        known = {(z, str(d.date())) for (z, d), v in y.items() if not pd.isna(v)}
        assert known == {(z, d) for d, z in WINDOW_VALUE_DIFFS}, known
        assert all(v == 0 for v in y.values() if not pd.isna(v))
        assert {z for (z, d), v in y.items() if pd.isna(v)} == {"ocean", "baker_china"}
        assert ((zo["y"] == 1).fillna(False) <= pd.Series([k in ev for k in zo.index], index=zo.index)).all()


def test_frames_do_not_depend_on_where_they_start():
    """A frame clipped at a later start is the full frame's tail: the history (D−7…D), the overflow tail and
    the first-look flag read the days before ``start``, never a cut record."""
    x = pd.Timestamp("2017-12-03")      # Westside's ledger starts 2017-12-01: the week before is unknown
    for fn in (T.out_label, T.zone_overflow, T.zone_elevated, T.basin_onsets, T.ledger_known):
        full, part = fn(G.SFPUC4_V1, end=AS_OF), fn(G.SFPUC4_V1, start=x, end=AS_OF)
        pd.testing.assert_frame_equal(full[full["date"] >= x].reset_index(drop=True), part.reset_index(drop=True))
    for level in T.LEVELS:
        full, part = T.carry_days(G.SFPUC4_V1, level, end=AS_OF), T.carry_days(G.SFPUC4_V1, level, start=x, end=AS_OF)
        pd.testing.assert_frame_equal(full[full["date"] >= x].reset_index(drop=True), part.reset_index(drop=True))


def test_carry_days_on_a_fixture_ledger():
    """X-S2-CARRY / X-S3-CARRY at the midnight boundary: an event that ends exactly at midnight does not carry,
    one that runs a minute past does; an onset on D cancels the carry (at the unit that fired); an unknown
    duration claims none; an unparseable start counts from event_date's midnight (§4.3)."""
    import csd_labels
    rows = [("2020-01-10", "CSD-003", "11:00 PM", 60),     # ends 01-11 00:00: no carry
            ("2020-01-20", "CSD-002", "11:00 PM", 61),     # ends 01-21 00:01: carries into 01-21 ...
            ("2020-01-21", "CSD-005", "1:00 AM", 30),      # ... where Westside (not Ocean Beach) has an onset
            ("2020-02-01", "CSD-001", "10:00 PM", 2000),   # ends 02-03 07:20: carries 02-02 and 02-03
            ("2020-03-01", "CSD-003", "0.42", 1500),       # no parseable start: 03-01 00:00 + 25 h → carries 03-02
            ("2020-03-10", "CSD-004", "11:30 PM", None)]   # no duration: no carry claimed
    ev = pd.DataFrame(rows, columns=["event_date", "outfall_id", "start_time", "duration_min"])
    ev["event_date"] = pd.to_datetime(ev["event_date"])
    ev["volume_MG"], ev["volume_qualifier"] = 1.0, None
    real = csd_labels.load_events
    try:
        csd_labels.load_events = lambda: ev.copy()
        T._events.cache_clear()
        span = dict(start="2020-01-01", end="2020-03-31")
        got = {}
        for level, unit in (("basin", "westside"), ("link", "westside>ocean"), ("zone", "ocean")):
            c = T.carry_days(G.SFPUC4_V1, level, **span)
            c = c[c[level] == unit]
            got[level] = {str(d.date()): n for d, n in zip(c.loc[c["carry"], "date"], c.loc[c["carry"], "n_active"])}
        assert got["basin"] == {"2020-02-02": 1, "2020-02-03": 1, "2020-03-02": 1}, got
        assert got["link"] == got["zone"] == {"2020-01-21": 1, "2020-02-02": 1, "2020-02-03": 1, "2020-03-02": 1}, got
        c = T.carry_days(G.SFPUC4_V1, "zone", **span)
        assert not c.loc[c["zone"] == "baker_china", "carry"].any() and c.loc[c["zone"] == "baker_china", "n_active"].sum() == 0
    finally:
        csd_labels.load_events = real
        T._events.cache_clear()


# ── (5) the CIWQS refresh stops on an outfall the registry lacks ────────────

def _aggregate(tmp: Path, outfall: str) -> subprocess.CompletedProcess:
    """Run aggregate.py on a one-event fixture month, the way the refresh runs it (from a scratch directory)."""
    (tmp / "parse_results.json").write_text(json.dumps([{
        "file": "pdfs/OSP_2026-01_100_200_x.pdf", "report_name": "Monthly SMR for January 2026", "facility": "OSP",
        "document_id": 100, "att_name": "x.pdf",
        "parse": [{"basin": "Oceanside Basin CSD Summary", "totals": [],
                   "events": [{"date": "1/5/2026", "outfall": outfall, "outfall_name": "x", "start_time": "2:09 AM",
                               "duration_min": "60", "volume_MG": "1.2"}]}]}]))
    (tmp / "download_manifest.json").write_text(json.dumps([{"report_name": "Monthly SMR for January 2026", "facility": "OSP",
                                                             "attType": "2", "file": "none.pdf", "ciwqs_document_id": 100}]))
    return subprocess.run([sys.executable, str(CIWQS_DIR / "aggregate.py")], cwd=tmp, capture_output=True, text=True)


def test_aggregate_fails_loudly_on_an_unregistered_outfall():
    import registry_check as RC
    with open(EVENTS, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert RC.problems(rows) == [], "the committed ledger passes"
    RC.assert_registered(rows)
    bad = [{"event_date": "2026-01-05", "outfall_id": "CSD-099", "basin": "Oceanside"},
           {"event_date": "2026-01-05", "outfall_id": "CSD-003", "basin": "North Shore"}]
    assert len(RC.problems(bad)) == 2
    try:
        RC.assert_registered(bad)
        raise AssertionError("an unknown outfall must raise")
    except ValueError as e:
        assert "CSD-099" in str(e) and "CSD-003" in str(e)
    with tempfile.TemporaryDirectory() as d:
        r = _aggregate(Path(d), "99")
        assert r.returncode != 0 and "CSD-099" in r.stderr and "not in shared/outfalls.py" in r.stderr, r.stderr[-600:]
        assert not (Path(d) / "sf_csd_events.csv").exists(), "nothing is written"
    with tempfile.TemporaryDirectory() as d:
        r = _aggregate(Path(d), "3")
        assert r.returncode == 0, r.stderr[-600:]
        with open(Path(d) / "sf_csd_events.csv", newline="") as fh:
            got = list(csv.DictReader(fh))
        assert [(g["outfall_id"], g["basin"]) for g in got] == [("CSD-003", "Oceanside")]


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
