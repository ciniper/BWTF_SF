"""SFPUC Alerts Timeline (/cso-history): the lab-samples row (collection day + publish lag) added 2026-09-27."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.cso_history import page as P  # noqa: E402
from shared.stations import STATIONS  # noqa: E402


def test_sample_days_collapse_analytes_and_measure_the_lag_in_pacific_days():
    sid = "OCEAN#21.1_SL"; sfpuc = STATIONS[sid].sfpuc_id
    rows = [
        {"station_id": sid, "sample_date": "2026-09-21", "exceeds": False, "first_seen_at": "2026-09-26T23:30:00+00:00", "source": "refresh"},
        {"station_id": sid, "sample_date": "2026-09-21", "exceeds": True,  "first_seen_at": "2026-09-27T05:00:00+00:00", "source": "refresh"},  # 22:00 Pacific on the 26th
        {"station_id": sid, "sample_date": "2026-09-14", "exceeds": False, "first_seen_at": "2026-09-27T18:27:00+00:00", "source": "backfill"},
        {"station_id": sid, "sample_date": "2026-09-14", "exceeds": False, "first_seen_at": "2026-09-27T18:27:00+00:00", "source": "refresh"},   # mixed day → no honest lag
        {"station_id": "NOT_A_STATION", "sample_date": "2026-09-21", "exceeds": True, "first_seen_at": "2026-09-26T23:30:00+00:00", "source": "refresh"},
        {"station_id": sid, "sample_date": None, "exceeds": True, "first_seen_at": "2026-09-26T23:30:00+00:00", "source": "refresh"},
    ]
    out = P.build_sample_days(rows)
    assert set(out) == {sfpuc}
    d14, d21 = out[sfpuc]
    assert d21 == {"date": "2026-09-21", "elevated": True, "n": 2, "source": "refresh", "first_seen": "2026-09-26T23:30:00Z", "lag_days": 5, "lane": 0}
    assert d14["source"] == "backfill" and d14["first_seen"] is None and d14["lag_days"] is None and d14["elevated"] is False and d14["n"] == 2 and d14["lane"] is None
    # the Pacific day matters: 05:00Z on the 27th is still the 26th in SF → lag 5, not 6
    late = P.build_sample_days([rows[1]])[sfpuc][0]
    assert late["lag_days"] == 5 and late["first_seen"] == "2026-09-27T05:00:00Z"
    assert P._median([]) is None and P._median([3]) == 3 and P._median([1, 4]) == 2.5 and P._median([5, 1, 3]) == 3


def test_lanes_stack_overlapping_publish_bars_and_skip_short_lags():
    mk = lambda date, fs, lag: {"date": date, "first_seen": fs, "lag_days": lag}
    # a storm week: Mon/Tue/Wed resamples all published Fri → three lanes; the following Monday's, published Thu, reuses lane 0
    entries = [mk("2026-11-02", "2026-11-06T22:00:00Z", 4), mk("2026-11-03", "2026-11-06T22:00:00Z", 3), mk("2026-11-04", "2026-11-06T22:00:00Z", 2),
               mk("2026-11-09", "2026-11-12T22:00:00Z", 3),
               mk("2026-11-16", None, None),                     # history: no bar
               mk("2026-11-23", "2026-11-24T18:00:00Z", 1)]      # published next day: tooltip only, no bar
    assert P.assign_lanes(entries) == 3
    assert [e["lane"] for e in entries] == [0, 1, 2, 0, None, None]
    # non-overlapping weekly bars share lane 0
    weekly = [mk("2026-10-05", "2026-10-09T22:00:00Z", 4), mk("2026-10-12", "2026-10-16T22:00:00Z", 4)]
    assert P.assign_lanes(weekly) == 1 and [e["lane"] for e in weekly] == [0, 0]
    assert P.assign_lanes([]) == 0
    # build_sample_days assigns lanes itself
    sid = "BAY#300.1_SL"
    rows = [{"station_id": sid, "sample_date": d, "exceeds": False, "first_seen_at": "2026-11-06T22:00:00+00:00", "source": "refresh"} for d in ("2026-11-02", "2026-11-03")]
    out = P.build_sample_days(rows)[STATIONS[sid].sfpuc_id]
    assert [e["lane"] for e in out] == [0, 1]


def test_page_carries_the_samples_row_and_the_events_payload_has_the_keys():
    html = (ROOT / "app/templates/cso_history/page.html").read_text()
    for needle in ("className = 'row srow'", "showSampleTip", "smark", "swatch lag", "DATA.samples", "sample_lag", "Appeared online", "d.lane", "window.__timeline"):
        assert needle in html, needle
    # the unconfigured path keeps the old shape; the configured path adds samples + sample_lag (exercised against Supabase when available)
    from shared import supabase as sb
    import json
    status, ctype, body = P.handle_events({}, b"")
    d = json.loads(body)
    assert status == 200 and "stations" in d and "event_count" in d
    if sb.is_configured() and not d.get("note"):
        assert "samples" in d and "sample_lag" in d and set(d["sample_lag"]) == {"n", "median_days", "measured_since"}
        for sid, days in d["samples"].items():
            assert any(s["station_id"] == sid for s in d["stations"]), sid    # every sampled station has a row


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failed else f"{failed} FAILED"); sys.exit(1 if failed else 0)
