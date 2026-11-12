"""Forecast vs reality (migration 022; features/forecast/outcomes.py; the Admin Forecast check tab).

Run: venv/bin/python tests/test_forecast_outcomes.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.forecast import outcomes as O  # noqa: E402
from features.forecast import page  # noqa: E402
from shared.zones import ZONES  # noqa: E402

NOW = datetime(2026, 11, 10, 8, 5, tzinfo=timezone.utc)   # 00:05 Pacific on the 10th: the day's first refresh


def snapshot(issue="2026-11-10", stale_day=False):
    """An engine snapshot the way page.py stores it: today + five days ahead, each with rain, zones, plain, rules."""
    preds = {}
    for lead in range(6):
        d = (date.fromisoformat(issue).toordinal() + lead)
        day = date.fromordinal(d).isoformat()
        preds[day] = {"date": day, "day_offset": lead, "is_today": lead == 0,
                      "rain_inches": 0.1 * lead, "rain_2d_cum": 0.2 * lead, "rain_3d_cum": 0.3 * lead, "rain_source": "icon_seamless",
                      "zones": {"ocean": 0.05 + 0.1 * lead, "baker_china": 0.02, "north": 0.6 if lead == 1 else 0.1, "east": 0.9 if lead == 0 else 0.3},
                      "plain": {"zones": {"ocean": 0.05 + 0.1 * lead, "baker_china": 0.02, "north": 0.6 if lead == 1 else 0.1, "east": 0.4}},
                      "discharge_probs": {"westside": 0.1, "southeast": 0.8 if lead == 0 else 0.2},
                      "live_corrections": {"stage1": {"southeast": {"rule": "cso_onset"}} if lead == 0 else {}, "groups": {}}}
    if stale_day:   # a cached yesterday the engine may still carry: no day_offset
        preds["2026-11-09"] = {"date": "2026-11-09", "zones": {"ocean": 0.5}}
    return {"model": {"name": "icon-t9wind3h-osplit-lt2more-bflags", "stage1": "logit_wind8_max3h_older11", "stage2": "v2_d10",
                      "weather_model": "icon_seamless", "live_corrections": "live_v2"},
            "predictions": preds, "beach_status": []}


class _Site:
    def __init__(self, site_id, name, lat, lon):
        self.site_id, self.name, self.latitude, self.longitude = site_id, name, lat, lon


class _Lab:
    def __init__(self, sites):
        self.sites = sites


class _Client:
    """A Surfrider client with the chapter's answers canned: Ocean Beach at Lincoln Way shares a beach with a city
    station (→ ocean); Bayview Hunters Point has no twin and lands in the nearest zone (east); a retired site is unlisted."""
    def __init__(self):
        self.lab = _Lab([_Site("11", "Ocean Beach at Lincoln Way", 37.7645, -122.5105), _Site("12", "Bayview Hunters Point", 37.73, -122.375)])
        self.history = [
            {"site_name": "Ocean Beach at Lincoln Way", "collection_time": datetime(2026, 11, 8, 9, 15), "substance": "Enterococcus", "result_value": 240.0, "result_raw": "240"},
            {"site_name": "Ocean Beach at Lincoln Way", "collection_time": datetime(2026, 11, 8, 9, 15), "substance": "Enterococcus", "result_value": 240.0, "result_raw": "240"},   # the API repeats a page
            {"site_name": "Ocean Beach at Lincoln Way", "collection_time": datetime(2026, 11, 8, 9, 15), "substance": "pH", "result_value": 7.9, "result_raw": "7.9"},             # not the test
            {"site_name": "Bayview Hunters Point", "collection_time": datetime(2026, 11, 8, 10, 0), "substance": "Enterococcus", "result_value": 5.0, "result_raw": "<10"},
            {"site_name": "Retired Site", "collection_time": datetime(2026, 11, 8, 10, 0), "substance": "Enterococcus", "result_value": 900.0, "result_raw": "900"},
            {"site_name": "Bayview Hunters Point", "collection_time": None, "substance": "Enterococcus", "result_value": 900.0, "result_raw": "900"},
        ]
        self.since = None

    def fetch_lab(self):
        return self.lab

    def fetch_history(self, since=None, **_):
        self.since = since
        return self.history


def test_migration_022_makes_two_locked_tables_and_never_lets_the_refresh_write_the_ledger():
    sql = (ROOT / "db/migrations/022_forecast_outcomes.sql").read_text()
    for t in ("forecast_issued", "forecast_outcomes"):
        assert f"create table if not exists public.{t} (" in sql, t
        assert f"alter table public.{t} enable row level security;" in sql, t
        assert f"revoke all on public.{t} from anon, authenticated;" in sql and f"grant all on public.{t} to service_role;" in sql, t
    assert "primary key (target_date, lead, zone)" in sql and "primary key (target_date, zone)" in sql
    assert "check (lead between 0 and 5)" in sql and "check (p between 0 and 1)" in sql
    assert "ledger_overflow" in sql and "ledger_as_of" in sql
    assert "create table if not exists public.bwtf_samples (" in sql and "primary key (site_name, collected_at)" in sql
    assert "revoke all on public.bwtf_samples from anon, authenticated;" in sql
    for col in ("bwtf_n", "bwtf_exceeded", "bwtf_exceeded_sites"):
        assert f"add column if not exists {col}" in sql, col                                      # the sandbox already had the table
    # the outcome rows the refresh writes carry no ledger column, so a merge upsert can never touch it
    row = O.outcome_rows([date(2026, 11, 9)], {}, {}, {}, NOW)[0]
    assert not any(k.startswith("ledger") for k in row) and set(row) >= {"target_date", "zone", "feed_posted", "samples_n", "rain_obs_in", "updated_at"}


def test_issued_rows_are_the_first_forecast_of_each_target_day_with_the_rain_it_was_fed():
    rows = O.issued_rows(snapshot(stale_day=True), "2026-11-10", "2026-11-10T08:05:00Z")
    assert len(rows) == 6 * len(ZONES) and {r["lead"] for r in rows} == set(range(6))                        # the stale day is skipped
    assert all(r["issue_date"] == "2026-11-10" and r["target_date"] == date.fromordinal(date(2026, 11, 10).toordinal() + r["lead"]).isoformat() for r in rows)
    east0 = next(r for r in rows if r["zone"] == "east" and r["lead"] == 0)
    assert east0["p"] == 0.9 and east0["p_plain"] == 0.4 and east0["rules"] == ["cso_onset"]                 # served beside uncorrected, the rule named
    assert east0["basins"] == {"westside": 0.1, "southeast": 0.8} and east0["model"] == "icon-t9wind3h-osplit-lt2more-bflags"
    assert east0["corrections"] == "live_v2" and east0["stamp"]["weather_model"] == "icon_seamless"
    ocean3 = next(r for r in rows if r["zone"] == "ocean" and r["lead"] == 3)
    assert (ocean3["rain_in"], ocean3["rain_2d"], ocean3["rain_3d"], ocean3["rain_source"]) == (0.3, 0.6, 0.9, "icon_seamless")
    assert ocean3["rules"] == [] and abs(ocean3["p"] - 0.35) < 1e-9
    # a day dated against its lead is skipped, never raised (a side job of the refresh)
    snap = snapshot()
    snap["predictions"]["2026-11-12"]["date"] = "2026-11-13"
    assert len(O.issued_rows(snap, "2026-11-10", "x")) == 5 * len(ZONES)


def test_the_fast_rulers_aggregate_stations_to_zone_days():
    feed = O.feed_by_zone_day([
        {"station_id": "4601", "day": "2026-11-08", "posted": False, "cso": True},    # Fort Funston → ocean
        {"station_id": "4602", "day": "2026-11-08", "posted": True, "cso": False},
        {"station_id": "4611", "day": "2026-11-08", "posted": False, "cso": False},   # Crissy W → north
        {"station_id": "9999", "day": "2026-11-08", "posted": True, "cso": True},     # not a registry station
    ])
    assert feed[("2026-11-08", "ocean")] == {"feed_stations": 2, "feed_posted": True, "feed_cso": True}
    assert feed[("2026-11-08", "north")] == {"feed_stations": 1, "feed_posted": False, "feed_cso": False}
    assert ("2026-11-08", "east") not in feed                                                              # nobody watched → absent
    samples = O.samples_by_zone_day([
        {"station_id": "OCEAN#22_SL", "sample_date": "2026-11-08", "exceeds": False},
        {"station_id": "OCEAN#22_SL", "sample_date": "2026-11-08", "exceeds": True},     # a second analyte over → the station-day is over
        {"station_id": "OCEAN#21_SL", "sample_date": "2026-11-08", "exceeds": False},
        {"station_id": "NOPE", "sample_date": "2026-11-08", "exceeds": True},
    ])
    assert samples[("2026-11-08", "ocean")] == {"samples_n": 2, "samples_exceeded": 1, "exceeded_stations": ["Fort Funston"]}
    import pandas as pd
    acis = pd.DataFrame({"date": pd.to_datetime(["2026-11-08", "2026-11-09"]), "SF Downtown": [1.2, float("nan")], "SF Oceanside": [0.8, 0.5]})
    rain = O.rain_by_day(acis)
    assert rain["2026-11-08"] == {"rain_obs_in": 1.0, "rain_obs_by_gauge": {"SF Downtown": 1.2, "SF Oceanside": 0.8}}
    assert rain["2026-11-09"]["rain_obs_in"] == 0.5 and rain["2026-11-09"]["rain_obs_by_gauge"]["SF Downtown"] is None
    assert O.rain_by_day(None) == {} and O.rain_by_day(pd.DataFrame()) == {}
    rows = O.outcome_rows([date(2026, 11, 8)], feed, samples, rain, NOW)
    assert len(rows) == len(ZONES)
    ocean = next(r for r in rows if r["zone"] == "ocean")
    assert ocean["feed_cso"] and ocean["feed_posted"] and ocean["feed_stations"] == 2 and ocean["samples_exceeded"] == 1 and ocean["rain_obs_in"] == 1.0
    east = next(r for r in rows if r["zone"] == "east")
    assert east["feed_stations"] is None and east["feed_posted"] is None and east["samples_n"] == 0 and east["samples_exceeded"] is None
    assert ocean["updated_at"] == "2026-11-10T08:05:00Z"
    assert ocean["bwtf_n"] == 0 and ocean["bwtf_exceeded"] is None and ocean["bwtf_exceeded_sites"] == []    # no mirror given → unknown


def test_surfriders_results_are_mirrored_once_graded_by_the_shared_rule_and_placed_in_zones():
    client = _Client()
    rows = O.bwtf_rows(client.fetch_lab(), client.fetch_history(), NOW)
    assert [(r["site_name"], r["zone"], r["sample_date"], r["exceeds"], r["value_raw"]) for r in rows] == [
        ("Ocean Beach at Lincoln Way", "ocean", "2026-11-08", True, "240"),       # 240 > 104
        ("Bayview Hunters Point", "east", "2026-11-08", False, "<10"),
    ]
    assert rows[0]["collected_at"] == "2026-11-08T09:15" and rows[0]["first_seen_at"] == "2026-11-10T08:05:00Z" and rows[0]["site_id"] == "11"
    agg = O.bwtf_by_zone_day(rows)
    assert agg[("2026-11-08", "ocean")] == {"bwtf_n": 1, "bwtf_exceeded": 1, "bwtf_exceeded_sites": ["Ocean Beach at Lincoln Way"]}
    assert agg[("2026-11-08", "east")] == {"bwtf_n": 1, "bwtf_exceeded": 0, "bwtf_exceeded_sites": []}
    out = O.outcome_rows([date(2026, 11, 8)], {}, {}, {}, NOW, agg)
    ocean = next(r for r in out if r["zone"] == "ocean")
    assert ocean["bwtf_n"] == 1 and ocean["bwtf_exceeded"] == 1 and O._truth(ocean) == {"feed": None, "samples": None, "bwtf": True, "either": True}
    # the mirror: one DO NOTHING upsert on (site_name, collected_at), history asked for from the day given
    calls = []
    real = O.sb.upsert
    O.sb.upsert = lambda table, rows, on_conflict, resolution="merge-duplicates": calls.append((table, len(rows), on_conflict, resolution))
    try:
        assert O.mirror_bwtf(date(2026, 11, 1), client=client, now=NOW) == 2
    finally:
        O.sb.upsert = real
    assert calls == [("bwtf_samples", 2, "site_name,collected_at", "ignore-duplicates")] and client.since == datetime(2026, 11, 1)


def test_record_writes_first_wins_rows_then_merges_outcomes_and_never_raises():
    calls = []
    orig_upsert, orig_select = O.sb.upsert, O.sb.select
    O.sb.upsert = lambda table, rows, on_conflict, resolution="merge-duplicates": calls.append((table, len(rows), on_conflict, resolution))
    O.sb.select = lambda table, params: []
    try:
        out = O.record(snapshot(), "2026-11-10", "2026-11-10T08:05:00Z", None, NOW, bwtf_client=_Client())
        assert out == {"issued": 24, "bwtf": 2, "outcomes": (O.BACKFILL_DAYS + 1) * len(ZONES)}
        assert calls[0] == ("forecast_issued", 24, "target_date,lead,zone", "ignore-duplicates")      # the day's first refresh wins
        assert calls[1] == ("bwtf_samples", 2, "site_name,collected_at", "ignore-duplicates")         # Surfrider's results, then the truth
        assert calls[2] == ("forecast_outcomes", (O.BACKFILL_DAYS + 1) * len(ZONES), "target_date,zone", "merge-duplicates")
        def boom(*a, **k):
            raise RuntimeError("relation forecast_issued does not exist")
        O.sb.upsert = boom
        assert O.record(snapshot(), "2026-11-10", "x", None, NOW, bwtf_client=_Client()) == {"issued": -1, "bwtf": -1, "outcomes": -1}
    finally:
        O.sb.upsert, O.sb.select = orig_upsert, orig_select
    # the refresh's hook: production only, same gate as the cache row
    seen = []
    orig_record, orig_writable = O.record, page._cache_writable
    O.record = lambda *a, **k: seen.append(a) or {"issued": 1, "outcomes": 1}
    try:
        page._cache_writable = lambda: False
        assert page._record_outcomes(snapshot(), NOW) == {} and not seen
        page._cache_writable = lambda: True
        out = page._record_outcomes(snapshot(), NOW)
        assert out == {"issued": 1, "outcomes": 1} and seen[0][1] == "2026-11-10" and seen[0][2] == "2026-11-10T08:05:00Z"   # the engine's day, like forecast_history
    finally:
        O.record, page._cache_writable = orig_record, orig_writable
    src = (ROOT / "features/forecast/page.py").read_text()
    assert "_record_outcomes(snap, now)" in src.split("_poll_deliveries()           # alert deliveries")[1].split("return _json")[0]   # after the snapshot is stored, with the other side jobs


def test_the_report_grades_at_the_medium_edge_and_finds_the_missing_day():
    today = date(2026, 11, 10)
    issued = []
    for issue in ("2026-11-05", "2026-11-06", "2026-11-08", "2026-11-09", "2026-11-10"):   # the 7th never ran
        for r in O.issued_rows(snapshot(issue), issue, f"{issue}T08:05:00Z"):
            issued.append(r)
    # truth for the 9th: an east CSO flag and a clean ocean sample; the 8th: nothing watched, no samples
    outcomes = O.outcome_rows([date(2026, 11, 8), date(2026, 11, 9)],
                              O.feed_by_zone_day([{"station_id": "4615", "day": "2026-11-09", "posted": False, "cso": True},
                                                  {"station_id": "4601", "day": "2026-11-09", "posted": False, "cso": False}]),
                              O.samples_by_zone_day([{"station_id": "OCEAN#22_SL", "sample_date": "2026-11-09", "exceeds": False}]),
                              {"2026-11-09": {"rain_obs_in": 1.4, "rain_obs_by_gauge": {}}}, NOW)
    rep = O.report(issued, outcomes, today)
    h = rep["health"]
    assert h["issue_days"] == 5 and h["expected_days"] == 6 and h["missing"] == ["2026-11-07"] and h["first_issue"] == "2026-11-05"
    assert h["last_generated_at"] == "2026-11-10T08:05:00Z" and h["waiting"] == sum(1 for r in issued if r["target_date"] >= "2026-11-10")
    assert rep["days"][0]["date"] == "2026-11-15" and rep["days"][0]["pending"] and [d["date"] for d in rep["days"]] == sorted({r["target_date"] for r in issued}, reverse=True)
    d9 = next(d for d in rep["days"] if d["date"] == "2026-11-09")
    assert d9["rain_obs"] == {"in": 1.4, "by_gauge": {}} and not d9["pending"]
    east = d9["zones"]["east"]
    assert east["truth"] == {"feed": True, "samples": None, "bwtf": None, "either": True} and east["label"] == "East Beaches"
    assert east["trajectory"][0]["p"] == 0.9 and east["trajectory"][0]["level"] == "extreme" and east["trajectory"][0]["rules"] == ["cso_onset"]
    assert east["trajectory"][1]["p"] == 0.3 and east["trajectory"][1]["level"] == "medium"                 # issued the 8th about the 9th
    assert 2 not in east["trajectory"]                                                                        # the 7th never issued
    assert d9["zones"]["ocean"]["truth"] == {"feed": False, "samples": False, "bwtf": None, "either": False}
    assert d9["zones"]["north"]["truth"] == {"feed": None, "samples": None, "bwtf": None, "either": None}                  # nothing watched, nothing sampled
    # the tally: only the 9th has truth; east lead 0 (0.9, bad) caught; east lead 1 (0.3, bad) caught; ocean lead 0 (0.05, fine) quiet;
    # ocean lead 1 (0.15, fine) quiet; north/baker have no truth
    t = rep["tally"]
    assert t[0]["feed"] == {"n": 2, "caught": 1, "missed": 0, "false_alarm": 0, "quiet": 1, "brier": round(((0.9 - 1) ** 2 + 0.05 ** 2) / 2, 4)}
    assert t[1]["feed"]["caught"] == 1 and t[1]["feed"]["quiet"] == 1 and t[1]["feed"]["n"] == 2
    assert t[0]["samples"] == {"n": 1, "caught": 0, "missed": 0, "false_alarm": 0, "quiet": 1, "brier": round(0.05 ** 2, 4)}   # only ocean was sampled
    assert t[2]["feed"]["n"] == 0 and t[2]["feed"]["brier"] is None                                           # lead 2 about the 9th was issued the 7th: missing
    assert t[0]["either"]["n"] == 2 and t[3]["feed"]["n"] == 2 and t[4]["feed"]["n"] == 2                  # the 9th's lead 3 and 4 came from the 6th and 5th, which ran
    assert all(t[5][r]["n"] == 0 for r in O.RULERS)                                                           # lead 5 would have come from the 4th: before the record began
    assert O.level(0.2) == "low" and O.level(0.205) == "medium" and O.level(0.505) == "high" and O.level(0.805) == "extreme" and O.level(None) == "unknown"


def test_leads_three_and_four_grade_when_their_issue_days_ran_and_today_is_not_missing_yet():
    today = date(2026, 11, 10)
    issued = [r for issue in ("2026-11-05", "2026-11-06") for r in O.issued_rows(snapshot(issue), issue, f"{issue}T08:05:00Z")]
    outcomes = O.outcome_rows([date(2026, 11, 9)], O.feed_by_zone_day([{"station_id": "4615", "day": "2026-11-09", "posted": True, "cso": False}]), {}, {}, NOW)
    t = O.report(issued, outcomes, today)["tally"]
    assert t[3]["feed"] == {"n": 1, "caught": 1, "missed": 0, "false_alarm": 0, "quiet": 0, "brier": round((0.3 - 1) ** 2, 4)}   # east 0.3 is Medium: an alert, and the flag was up
    assert t[4]["feed"]["n"] == 1 and t[4]["feed"]["caught"] == 1 and t[0]["feed"]["n"] == 0                                    # nothing was issued the 9th itself
    # the health count runs through yesterday or the last issue day, whichever is later: today is not missing at 00:03
    rep = O.report(issued, outcomes, today)
    assert rep["health"]["expected_days"] == 5 and rep["health"]["missing"] == ["2026-11-07", "2026-11-08", "2026-11-09"]
    assert O.report(issued, outcomes, date(2026, 11, 6))["health"] | {"missing": []} == O.report(issued, outcomes, date(2026, 11, 6))["health"]


def test_build_degrades_without_supabase_and_the_page_sits_behind_the_passphrase():
    rep = O.build(30, date(2026, 11, 10))
    assert rep["state"] == "unconfigured" and "production" in rep["words"]           # scripts/check.sh blanks the key
    from app.wsgi import app
    with app.test_client() as c:
        locked = c.get("/alerts/forecast-check").data.decode()
        assert "passphrase" in locked.lower() and 'name="next" value="/alerts/forecast-check"' in locked
        assert c.get("/alerts/api/forecast-check").status_code == 401
        with c.session_transaction() as s:
            s["alerts_unlocked"] = True
        h = c.get("/alerts/forecast-check?days=30").data.decode()
        j = c.get("/alerts/api/forecast-check?days=999").get_json()
        d = c.get("/alerts").data.decode()
    assert "<h1>Forecast check</h1>" in h and 'href="/alerts/forecast-check" class="on"' in h and 'name="robots" content="noindex"' in h
    assert "Nothing to show" in h and 'id="zone-tabs"' not in h                         # unconfigured: the notice, not an empty table
    assert j["state"] == "unconfigured" and d.index('href="/alerts/costs"') < d.index('href="/alerts/forecast-check"') < d.index('href="/alerts/lab-sop"')
    # a configured record renders: zone panes, the tally, the trajectory chips
    orig = O.build
    issued = [r for issue in ("2026-11-08", "2026-11-09") for r in O.issued_rows(snapshot(issue), issue, f"{issue}T08:05:00Z")]
    outcomes = O.outcome_rows([date(2026, 11, 9)], O.feed_by_zone_day([{"station_id": "4615", "day": "2026-11-09", "posted": False, "cso": True}]), {}, {}, NOW)
    O.build = lambda days, today=None: {**O.report(issued, outcomes, date(2026, 11, 10)), "state": "ok", "days_back": days}
    try:
        with app.test_client() as c:
            with c.session_transaction() as s:
                s["alerts_unlocked"] = True
            h = c.get("/alerts/forecast-check").data.decode()
    finally:
        O.build = orig
    assert 'id="zone-tabs"' in h and h.count('class="zone-pane') == len(ZONES) + len(O.RULERS) and 'class="chip extreme rules"' in h and ">CSO flag<" in h
    assert "missing 2026-11-10" not in h and 'class="chip unknown"' in h             # today is an issue day only if a row says so; leads nobody issued are dotted


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("  ok  ", name)
            except Exception as e:  # noqa: BLE001
                failures += 1; print("FAIL", name + ":", type(e).__name__ + ":", e)
    print("ALL PASS" if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
