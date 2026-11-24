"""ECMWF uses the real five-stage engine, without promoted state or writes."""
import copy
import sys
import subprocess
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
from features.forecast import page
from features.forecast import alternative_forecast as alt
from features.forecast.live_options import ECMWF_ID
import make_stages_goldens as G


def test_five_stages_with_ecmwf_inputs_and_no_promoted_mutation():
    rain, gauges = G.load_fixture_rain()
    ld = alt.ld
    before = copy.deepcopy(ld.LIVE.get_snapshot())
    params_before = dict(ld.METEO_PARAMS)
    hourly = {
        "time": rain.timestamp.dt.strftime("%Y-%m-%dT%H:%M").tolist(),
        "precipitation": (rain.precip_inches * 25.4).tolist(),
        "temperature_2m": [12.] * len(rain),
        "wind_speed_10m": rain.wind_speed_kmh.tolist(),
        "wind_direction_10m": rain.wind_dir_deg.tolist(),
    }
    response = Mock()
    response.json.return_value = {"hourly": hourly}
    with ExitStack() as stack:
        stack.enter_context(patch.object(ld, "_now_local", return_value=G.NOW))
        stack.enter_context(patch.object(ld, "_supabase", None))
        get = stack.enter_context(patch.object(ld.requests, "get", return_value=response))
        stack.enter_context(patch.object(ld.requests, "post", G._fake_acis_post(gauges, ld.ACIS_URL)))
        stack.enter_context(patch.object(alt.ECMWFForecast, "_overlay_observed_rain", lambda self, df: df))
        stack.enter_context(patch.object(alt.ECMWFForecast, "_fetch_sfpuc", return_value=[]))
        stack.enter_context(patch.object(alt.ECMWFForecast, "_fetch_observed_cso", return_value=G.LIVE_ONSETS))
        stack.enter_context(patch.object(alt.ECMWFForecast, "_live_corrections_enabled", return_value=(True, "fixture")))
        stack.enter_context(patch.object(alt.ECMWFForecast, "_watcher_health", return_value=G.LIVE_HEALTH))
        stack.enter_context(patch.object(alt.ECMWFForecast, "_cso_flag_days", return_value=G.LIVE_FLAGS))
        stack.enter_context(patch.object(alt.ECMWFForecast, "_sample_flags", return_value=G.LIVE_DATASF))
        stack.enter_context(patch.object(alt.ECMWFForecast, "_feed_sample_flags", return_value=G.LIVE_FEED))
        first = alt.compute()
        # Change only the provider's future rain: the fully composed forecast must respond.
        hourly["precipitation"] = [v if t < G.NOW.strftime("%Y-%m-%dT%H:%M") else 20.
                                    for t, v in zip(hourly["time"], hourly["precipitation"])]
        second = alt.compute()
        assert get.call_count == 2  # every selection fetches and computes anew
        assert all(c.kwargs["params"]["models"] == "ecmwf_ifs025" for c in get.call_args_list)
    assert first["model"]["name"] == ECMWF_ID and first["on_demand"]
    assert len(first["predictions"]) == 6
    assert any(d["live_corrections"]["stage1"] or d["live_corrections"]["groups"] for d in first["predictions"].values())
    for day in first["predictions"].values():
        assert set(day["stage3_zones"]) == {"ocean", "baker_china", "north", "east"}
        assert all(0 <= v <= 1 for v in day["zones"].values())
        assert "plain" in day
    assert any(first["predictions"][d]["zones"] != second["predictions"][d]["zones"] for d in first["predictions"])
    assert ld.METEO_PARAMS == params_before
    assert ld.LIVE.get_snapshot() == before


def test_api_never_reads_or_writes_forecast_storage_and_is_not_http_cached():
    from app.wsgi import app
    with ExitStack() as stack:
        for name in ("_read_row", "_store_snapshot", "_claim_refresh", "_record_history", "_record_change"):
            stack.enter_context(patch.object(page, name, side_effect=AssertionError("alternative touched promoted persistence")))
        for name in ("update", "rpc", "insert"):
            stack.enter_context(patch.object(page.sb, name, side_effect=AssertionError("database write")))
        require = stack.enter_context(patch.object(page, "_require_engine", return_value=None))
        compute = stack.enter_context(patch.object(alt, "compute", side_effect=[{"run": 1}, {"run": 2}, RuntimeError("private diagnostics")]))
        client = app.test_client()
        for query in ("", "?model=../served", "?model=anything", f"?model={ECMWF_ID}&model={ECMWF_ID}"):
            response = client.get("/forecast/api/alternative" + query)
            assert response.status_code == 400 and response.headers["Cache-Control"] == "no-store"
        require.assert_not_called()
        for run in (1, 2):
            response = client.get("/forecast/api/alternative", query_string={"model": ECMWF_ID})
            assert response.json == {"run": run}
            assert response.headers["Cache-Control"] == "no-store"
        response = client.get("/forecast/api/alternative", query_string={"model": ECMWF_ID})
        assert response.status_code == 503 and "private diagnostics" not in response.text
        assert response.headers["Cache-Control"] == "no-store"
        assert compute.call_count == 3


def test_incomplete_inputs_and_shared_artifact_drift_fail_closed():
    engine = alt.ECMWFForecast()
    response = Mock()
    response.json.return_value = {"hourly": {"time": ["2026-11-23T12:00"], "precipitation": [None],
                                           "temperature_2m": [12], "wind_speed_10m": [1], "wind_direction_10m": [180]}}
    with patch.object(alt.ld.requests, "get", return_value=response):
        try:
            engine._fetch_open_meteo()
            raise AssertionError("null rain was accepted")
        except ValueError:
            pass
        response.json.return_value["hourly"]["precipitation"] = [0.]
        try:
            engine._fetch_open_meteo()
            raise AssertionError("truncated forecast window was accepted")
        except ValueError:
            pass
    with patch.dict(alt.SHARED_ARTIFACTS, {"thresholds.json": "changed"}):
        try:
            alt.ECMWFForecast()
            raise AssertionError("changed artifact was accepted")
        except ValueError:
            pass
    with patch.object(alt.ECMWFForecast, "refresh"):
        try:
            alt.compute()
            raise AssertionError("empty forecast was accepted")
        except RuntimeError:
            pass


def test_browser_loader_regressions():
    for script in ("test_alternative_forecast_ui.mjs", "test_forecast_outage_ui.mjs"):
        subprocess.run(["node", str(ROOT / "tests" / script)], check=True)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
