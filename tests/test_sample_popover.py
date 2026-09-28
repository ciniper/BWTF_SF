"""The sample popover (app/static/sample_popover.js over /api/sample-day): the
chips on the alerts and forecast pages must hand it a DataSF station id, and
the forecast's beach status must carry one. Offline: stubbed feed + clients."""
import pathlib
import sys
import types
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.stations import STATIONS  # noqa: E402


def test_forecast_beach_status_carries_the_datasf_station_id(monkeypatch=None):
    """2026-09-27: the first cut referenced SFPUC_TO_SFGOV_SOURCES without importing it,
    which made the whole forecast refresh raise inside its try — pin the path."""
    import shared.sfpuc_api as api
    from features.forecast import live_dashboard as ld

    class Status:
        value = "safe"

    feed = [types.SimpleNamespace(station_name=st.sfpuc_name, station_id=st.sfpuc_id, status=Status(), has_cso=False,
                                  sample_date=datetime(2026, 9, 22)) for st in STATIONS.values()]
    orig = api.SFPUCRealTimeAPI.fetch_stations
    api.SFPUCRealTimeAPI.fetch_stations = lambda self: feed
    try:
        out = ld.LiveData._fetch_sfpuc(types.SimpleNamespace(_beach_status=ld.LiveData._beach_status))
    finally:
        api.SFPUCRealTimeAPI.fetch_stations = orig
    assert len(out) == 20 and all(b["source"] in STATIONS for b in out)
    aq = next(b for b in out if b["name"] == "Aquatic Park")
    assert aq == {"name": "Aquatic Park", "status": "safe", "has_cso": False, "sample_date": "09/22/26",
                  "source": "BAY#211_SL", "sample_date_iso": "2026-09-22"}


def test_alerts_card_chip_opens_the_popover_and_keeps_the_raw_link():
    from features.alerts.page import AlertsRoutes
    st = types.SimpleNamespace(station_id="4613", station_name="Aquatic Park", sample_date=datetime(2026, 9, 23))
    lab = {"sample_date": datetime(2026, 9, 22), "source_id": "BAY#211_SL", "results_url": "https://data.sf.gov/resource/x.json?q"}
    card = AlertsRoutes._generate_station_card(types.SimpleNamespace(), st, "safe", False, lab)
    assert card.count('data-sample-station="BAY#211_SL"') == 2 and 'data-sample-name="Aquatic Park"' in card   # the card and the date link
    assert 'class="station-card safe clickable" role="button" tabindex="0"' in card
    plain = AlertsRoutes._generate_station_card(types.SimpleNamespace(), st, "safe", False, None)
    assert "clickable" not in plain and "data-sample-station" not in plain                # no lab results: nothing to open
    assert 'data-sample-date="2026-09-22"' in card and 'data-sample-feed-date="2026-09-23"' in card
    assert 'href="https://data.sf.gov/resource/x.json?q"' in card          # the raw rows stay one click away
    assert "not yet published" in card                                      # feed date newer than the lab's: the pending note


def test_both_pages_include_the_shared_script_and_the_route_exists():
    js = (ROOT / "app" / "static" / "sample_popover.js").read_text()
    charts = (ROOT / "app" / "static" / "charts.js").read_text()
    assert "/api/sample-day?" in js and "data-sample-station" in js and "BWTFCharts.miniBars" in js and "chart.umd.js" not in js   # one chart module
    assert "chart.umd.js" in charts and '"limitLines"' in charts and "seriesChart" in charts and "pairedBars" in charts and "miniBars" in charts
    assert "p.over ? COLORS.OVER" not in charts.split("function seriesChart")[1].split("function pairedBars")[0]   # dots keep their series colour (Chase, 2026-09-28)
    for tpl in ("app/templates/alerts/dashboard.html", "app/templates/forecast/page.html", "app/templates/graphs/page.html", "app/templates/samples/page.html"):
        assert "/static/sample_popover.js" in (ROOT / tpl).read_text(), tpl
    from app import wsgi
    assert wsgi._COMPARE_GET["/api/sample-day"] == "send_api_sample_day"
    forecast_tpl = (ROOT / "app/templates/forecast/page.html").read_text()
    assert "item.dataset.sampleStation = b.source" in forecast_tpl and 'data-sample-date="${esc(s.date)}"' in forecast_tpl
    assert 'class="beach-date"' in forecast_tpl and "sample-link" not in forecast_tpl.split("function renderBeaches")[1].split("// ─── WHAT HAPPENED")[0]
    charts_js = (ROOT / "app" / "static" / "charts.js").read_text()
    assert '"limitLines"' in charts_js and "getPixelForValue" in charts_js and "limitLines" not in js   # limits drawn as real horizontal lines, in one place


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
