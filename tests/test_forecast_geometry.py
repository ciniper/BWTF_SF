"""Forecast geography + stage-2 composition (v4, 2026-09).

Pins the contracts between training (train_v4) and serving (live_dashboard):
basins ↔ groups ↔ zones all derive from the registries, and the shared
composition treats a discharge day as a bad day.

    venv/bin/python tests/test_forecast_geometry.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "features" / "forecast" / "src" / "models"))

import groups  # noqa: E402
import impact  # noqa: E402
from shared.outfalls import APP_BASINS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES, ZONE_OF_SOURCE, ZONE_OF_STATION  # noqa: E402


def test_basins_groups_zones_derive_from_the_registries():
    assert list(groups.BASIN_KEYS) == list(APP_BASINS)
    assert sorted(sid for _, sids in groups.SITE_GROUPS.values() for sid in sids) == sorted(STATIONS)
    for zk, gs in groups.ZONE_GROUPS.items():
        assert set(ZONES[zk].source_ids) == {sid for g in gs for sid in groups.SITE_GROUPS[g][1]}
    assert set(ZONE_OF_STATION) == {s.sfpuc_id for s in STATIONS.values()}
    assert set(ZONE_OF_SOURCE) == set(STATIONS)
    assert groups.OBSERVED_STATION_BASIN["4618"] == "central"
    assert groups.GROUPS_BY_BASIN["central"] == ["Mission Creek"]
    assert ZONES["east"].basins == ("Central", "Southeast")


def test_signup_zones_are_the_shared_zones():
    from features.signup.page import ZONES as SIGNUP
    for zk, z in ZONES.items():
        assert SIGNUP[zk][0] == z.label
        assert [sid for sid, *_ in SIGNUP[zk][1]] == list(z.station_ids)


def test_pava_makes_curves_nonincreasing_and_preserves_weighted_mean():
    vals, wts = [0.5, 0.7, 0.2, 0.3], [10, 2, 5, 5]
    out = impact.pava_nonincreasing(vals, wts)
    assert all(a >= b - 1e-12 for a, b in zip(out, out[1:]))
    assert abs(sum(v * w for v, w in zip(out, wts)) - sum(v * w for v, w in zip(vals, wts))) < 1e-9
    table = {"G": {"median_event_volume_mg": 5, "buckets": {
        "baseline_no_recent_discharge": {"p_elevated": 0.1, "n": 100},
        "d0_large": {"p_elevated": 0.9, "n": 10}, "d1_large": {"p_elevated": 0.5, "n": 10},
        "d2_large": {"p_elevated": 0.6, "n": 3}, "d3_large": {"p_elevated": 0.2, "n": 10}}}}
    sm = impact.smooth_table(table)["G"]["buckets"]
    assert sm["d1_large"]["p_elevated"] >= sm["d2_large"]["p_elevated"] >= sm["d3_large"]["p_elevated"]
    assert table["G"]["buckets"]["d2_large"]["p_elevated"] == 0.6  # input untouched


def test_compose_treats_a_discharge_day_as_bad_and_decays_after():
    table = {"G": {"median_event_volume_mg": 5, "buckets": {
        "baseline_no_recent_discharge": {"p_elevated": 0.0, "n": 100},
        "d0_small": {"p_elevated": 0.2, "n": 10}, "d1_small": {"p_elevated": 0.5, "n": 10},
        "d2_small": {"p_elevated": 0.25, "n": 10}, "d3_small": {"p_elevated": 0.0, "n": 10},
        "d0_large": {"p_elevated": 0.2, "n": 10}, "d1_large": {"p_elevated": 0.5, "n": 10},
        "d2_large": {"p_elevated": 0.25, "n": 10}, "d3_large": {"p_elevated": 0.0, "n": 10}}}}
    gb = {"b": ["G"]}
    probs = [{"b": 0.0}, {"b": 1.0}, {"b": 0.0}, {"b": 0.0}, {"b": 0.0}]
    vols = [{"b": 5.0}] * 5
    risks = [impact.compose(table, gb, probs, vols, i)[1]["G"] for i in range(5)]
    assert risks[1] == 1.0            # discharge day: x(0) ≡ 1 even though d0 buckets say 0.2
    assert risks[2] == 0.5 and risks[3] == 0.25 and risks[4] == 0.0
    assert risks[0] == 0.0
    per_basin, _ = impact.compose(table, gb, probs, vols, 1)
    assert per_basin == {"b": 1.0, "citywide": 1.0}
    # an observed discharge overrides a low model probability
    dates = list(range(5))
    _, g = impact.compose(table, gb, [{"b": 0.01}] * 5, vols, 2, dates, {1: {"b"}})
    assert g["G"] >= 0.5


def test_actuals_ignore_simulations_and_clear_downs():
    """2026-09-05: a simulated CSO at Aquatic Park (14:19, simulated=true) and
    the dispatcher's clear-down of it at 14:30 — logged simulated=FALSE with a
    cso→ok transition. Neither is a real posting; 'What happened' must show
    nothing for that day."""
    sys.path.insert(0, str(ROOT / "features" / "forecast"))
    import live_dashboard as ld
    rows = [
        {"created_at": "2026-09-05T14:19:00.013025+00:00", "event_type": "cso", "station_ids": ["4613"], "simulated": True,
         "results": {"transitions": [{"to": "cso", "from": "ok", "simulated": True, "station_id": "4613", "station_name": "Aquatic Park"}]}},
        {"created_at": "2026-09-05T14:30:00.013662+00:00", "event_type": "cleared", "station_ids": ["4613"], "simulated": False,
         "results": {"transitions": [{"to": "ok", "from": "cso", "simulated": False, "station_id": "4613", "station_name": "Aquatic Park"}]}},
        {"created_at": "2026-09-05T15:00:00+00:00", "event_type": "posted", "station_ids": ["4616"], "simulated": False, "source": "pg_live",
         "results": {"transitions": [{"to": "posted", "from": "ok", "simulated": False, "station_id": "4616", "station_name": "Windsurfer Circle"}]}},
        # the retired observer thread double-logging the same posting two minutes earlier (2026-09-01 shape)
        {"created_at": "2026-09-05T14:58:00+00:00", "event_type": "posted", "station_ids": ["4616"], "simulated": False, "source": "thread_shadow",
         "results": {"transitions": [{"to": "posted", "from": "ok", "simulated": False, "station_id": "4616", "station_name": "Windsurfer Circle"}]}},
        # a manual button dispatch is not a detection
        {"created_at": "2026-09-05T16:00:00+00:00", "event_type": "posted", "station_ids": ["4601"], "simulated": False, "source": "manual",
         "results": {"transitions": [{"to": "posted", "from": "ok", "simulated": False, "station_id": "4601", "station_name": "Fort Funston"}]}},
    ]
    import datetime as dt
    out = ld.LiveData._escalations_from_rows(rows, dt.date(2026, 9, 5))
    assert out == [{"date": "2026-09-05", "station_id": "4616", "station_name": "Windsurfer Circle", "zone": "east", "to": "posted"}]


def test_forecast_beach_status_uses_the_shared_classification():
    """Islais Creek on 2026-09-09: feed row posted='BAY#320_SL', p_color='G'.
    The pg watcher and shared/sfpuc_api call that POSTED; the forecast page
    used to call it safe."""
    sys.path.insert(0, str(ROOT / "features" / "forecast"))
    import live_dashboard as ld
    from shared.sfpuc_api import SFPUCRealTimeAPI, StationStatus
    api = SFPUCRealTimeAPI()
    row = {"stationid": "4619", "stationname": "Islais Creek", "cso": None, "s_color": None,
           "posted": "BAY#320_SL", "p_color": "G", "sample_date": "08/31/26", "lat": "37.747", "lon": "-122.388"}
    assert api._parse_station_status(row) == StationStatus.POSTED

    class St:  # the fields _beach_status reads
        def __init__(self, status, has_cso=False):
            self.status, self.has_cso = status, has_cso
    assert ld.LiveData._beach_status(St(StationStatus.POSTED)) == "posted"
    assert ld.LiveData._beach_status(St(StationStatus.SAFE)) == "safe"
    assert ld.LiveData._beach_status(St(StationStatus.NOT_ROUTINELY_SAMPLED)) == "not_sampled"
    assert ld.LiveData._beach_status(St(StationStatus.POSTED, has_cso=True)) == "cso"


def test_zone_risk_is_the_worst_group():
    r = groups.zone_risks({"Ocean Beach": .3, "Baker-China": .1, "Crissy Field": .2,
                           "Aquatic Park": .05, "Mission Creek": .6, "Southeast": .4})
    assert r == {"ocean": .3, "baker_china": .1, "north": .2, "east": .6}


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"  FAIL {name}: {exc!r}")
    print("PASS" if not failures else f"{failures} FAILED")
    raise SystemExit(1 if failures else 0)
