"""Collect (/learn/collect, 2026-10-18): the four sites, the procedure, and the sheet's conditions.
All offline: the fetchers are replaced by fixed payloads."""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.learn import collect as C  # noqa: E402


def test_pins_decode_to_the_beaches_they_name():
    want = {"ob-lincoln": (37.763917, -122.511667), "ob-vicente": (37.737917, -122.508222),
            "china": (37.788167, -122.49175), "aquatic": (37.807194, -122.422861)}
    view = {s["key"]: s for s in C.sites_view()}
    assert [s["n"] for s in C.SITES] == [1, 2, 3, 4] and set(view) == set(want)
    for k, (lat, lon) in want.items():
        assert (view[k]["lat"], view[k]["lon"]) == (lat, lon), k
        assert view[k]["maps"] == f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        city = next(st for z in C.ZONES.values() for st in z.stations if st.sfpuc_id == C.SITE[k]["station"])
        assert abs(city.lat - lat) < 0.01 and abs(city.lon - lon) < 0.01, k      # the city's station at the same beach
    assert {k: C.ZONE_GAUGE[C.zone_of(C.SITE[k])] for k in want} == {"ob-lincoln": "SF Oceanside", "ob-vicente": "SF Oceanside",
                                                                     "china": "SF Oceanside", "aquatic": "SF Downtown"}


def test_the_sheets_vocabulary():
    assert [C.wave_bin(x) for x in (0.4, 1.0, 3.9, 4.7, 8.9, 11, 14, None)] == ["<1 ft", "1-2 ft", "2-4 ft", "4-6 ft", "6-9 ft", "9-12 ft", "12+ ft", None]
    assert [C.compass8(d) for d in (0, 22, 23, 235, 249, 315, 359, None)] == ["N", "N", "NE", "SW", "W", "NW", "N", None]
    assert [C.sky_label(c) for c in (0, 1, 2, 3, 45, 53, 61, 63, 95, None)] == ["Sunny", "Sunny", "Partly Cloudy", "Overcast", "Overcast",
                                                                              "Light Rain", "Light Rain", "Rain", "Rain", None]


def _hilo(*pts):
    return [{"t": datetime.strptime(t, "%Y-%m-%d %H:%M"), "type": k, "ft": ft} for t, k, ft in pts]


def test_tide_state_matches_the_volunteers_on_2026_10_08():
    """NOAA's predictions that day; the volunteers wrote low, low, low, outgoing."""
    ob = _hilo(("2026-10-08 03:31", "L", .3), ("2026-10-08 10:02", "H", 5.9), ("2026-10-08 16:05", "L", .9), ("2026-10-08 22:06", "H", 5.5))
    gate = _hilo(("2026-10-08 04:06", "L", .3), ("2026-10-08 10:51", "H", 5.8), ("2026-10-08 16:40", "L", .9), ("2026-10-08 22:55", "H", 5.4))
    north = _hilo(("2026-10-08 04:17", "L", .3), ("2026-10-08 11:04", "H", 6.0), ("2026-10-08 16:51", "L", .9), ("2026-10-08 23:08", "H", 5.6))
    at = lambda hm: datetime.strptime("2026-10-08 " + hm, "%Y-%m-%d %H:%M")
    assert [C.tide_state(h, at(t))["state"] for h, t in ((ob, "17:05"), (ob, "15:50"), (gate, "16:20"), (north, "15:45"))] == ["low", "low", "low", "outgoing"]
    assert C.tide_state(ob, at("13:00"))["state"] == "outgoing" and C.tide_state(ob, at("19:00"))["state"] == "incoming"
    assert C.tide_state(ob, at("02:00")) is None                                                     # no turn before: not guessed


def _weather():
    return {"hourly": {"time": [f"2026-10-08T{h:02d}:00" for h in range(24)], "temperature_2m": [60.4] * 24, "weather_code": [2] * 24,
                       "cloud_cover": [40] * 24, "wind_speed_10m": [9.6] * 24, "wind_direction_10m": [300] * 24}}


def _marine():
    return {"hourly": {"time": [f"2026-10-08T{h:02d}:00" for h in range(24)], "wave_height": [1.4] * 24, "sea_surface_temperature": [16.4] * 24}}


def test_the_sheet_assembles_in_its_order_and_falls_back_between_gauges():
    at = datetime(2026, 10, 8, 17, 5)
    hilo = _hilo(("2026-10-08 10:02", "H", 5.9), ("2026-10-08 16:05", "L", .9), ("2026-10-08 22:06", "H", 5.5))
    rain = {"SF Oceanside": {d: None for d in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08")},
            "SF Downtown": {"2026-10-05": 0.0, "2026-10-06": 0.12, "2026-10-07": 0.0}}
    out = C.assemble(C.SITE["ob-lincoln"], at, _weather(), _marine(), hilo, rain)
    v = {r["key"]: r["value"] for r in out["rows"]}
    assert [r["key"] for r in out["rows"]] == ["air_temp", "water_temp", "current_weather", "precipitation", "tide", "wave_height", "wind_direction", "wind_speed"]
    assert v == {"air_temp": "60°F", "water_temp": "62°F", "current_weather": "Partly Cloudy", "precipitation": "Yes", "tide": "Low",
                 "wave_height": "4-6 ft", "wind_direction": "NW", "wind_speed": "10 mph"}
    pr = next(r for r in out["rows"] if r["key"] == "precipitation")
    assert "SF Downtown" in pr["source"] and "hasn't reported" in pr["detail"] and "Oct 8" in pr["detail"]   # the lagging gauge is named
    # a sheltered site leaves the waves to the eye; a source that failed leaves its fields blank, never the sheet
    aq = C.assemble(C.SITE["aquatic"], at, None, _marine(), None, None)
    va = {r["key"]: r for r in aq["rows"]}
    assert va["wave_height"]["value"] is None and va["wave_height"]["blank"] == "Check by eye" and "breakwater" in va["wave_height"]["detail"]
    assert va["air_temp"]["value"] is None and va["tide"]["value"] is None and va["precipitation"]["value"] is None and va["water_temp"]["value"] == "62°F"


def test_conditions_survives_a_source_failing():
    def boom(*a):
        raise RuntimeError("down")
    out = C.conditions(C.SITE["china"], datetime(2026, 10, 8, 16, 20),
                       {"weather": lambda *a: _weather(), "marine": lambda *a: _marine(), "hilo": boom, "rain": boom})
    v = {r["key"]: r["value"] for r in out["rows"]}
    assert out["ok"] and v["tide"] is None and v["precipitation"] is None and v["air_temp"] == "60°F"


def test_routes_render_and_refuse_bad_input():
    from app.wsgi import app
    with app.test_client() as c:
        h = c.get("/learn/collect").data.decode()
        bad = c.get("/api/learn/conditions?site=nowhere&date=2026-10-08&time=10:00")
        far = c.get("/api/learn/conditions?site=china&date=2020-01-01&time=10:00")
    assert "{{" not in h and "{%" not in h and "<h1>Collect</h1>" in h and '<meta name="robots" content="noindex">' in h
    assert h.count('class="site"') == 4 and h.count("https://www.google.com/maps/search/?api=1&amp;query=") == 4 and h.count("Photo of the sampling spot coming soon") == 4
    assert h.count('<li>') >= 6 and "about 2 ft" in h and "6 in" in h and "wave coming in" in h and 'id="cx-form"' in h
    assert 'class="on" aria-current="page">Collect' in h and "coming soon" not in h.replace("Photo of the sampling spot coming soon", "")
    assert bad.status_code == 400 and json.loads(bad.data)["error"] == "unknown site"
    assert far.status_code == 400 and "last 90 days" in json.loads(far.data)["error"]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
