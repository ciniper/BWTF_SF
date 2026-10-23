"""Collect (/learn/collect, 2026-10-18): the four sites, the procedure, and the sheet's conditions.
All offline: the fetchers are replaced by fixed payloads."""
import json
import re
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
    assert {C.zone_of(C.SITE[k]) for k in want} == {"ocean", "baker_china", "north"}


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


def test_tide_height_follows_noaa_half_cosine_between_turns():
    hilo = _hilo(("2026-10-08 10:02", "H", 5.9), ("2026-10-08 16:05", "L", .9), ("2026-10-08 22:06", "H", 5.5))
    at = lambda hm: datetime.strptime("2026-10-08 " + hm, "%Y-%m-%d %H:%M")
    assert C.tide_height(hilo, at("10:02")) == 5.9 and C.tide_height(hilo, at("16:05")) == 0.9
    assert abs(C.tide_height(hilo, at("13:03")) - 3.4) < 0.05          # halfway in time is halfway in height
    assert C.tide_height(hilo, at("11:00")) > C.tide_height(hilo, at("12:00")) > C.tide_height(hilo, at("15:00"))
    assert C.tide_height(hilo, at("09:00")) is None                     # no turn before: not guessed


def _weather(rain_hours=(6, 7)):   # Open-Meteo's four model days (three back + the day), light rain on the day's morning unless told otherwise
    hrs = [f"2026-10-{d:02d}T{h:02d}:00" for d in (5, 6, 7, 8) for h in range(24)]
    prec = [0.0] * 96
    for h in rain_hours: prec[72 + h] = 0.016
    return {"hourly": {"time": hrs, "temperature_2m": [60.4] * 96, "weather_code": [2] * 96, "cloud_cover": [40] * 96,
                       "wind_speed_10m": [9.6] * 96, "wind_direction_10m": [300] * 96, "precipitation": prec}}


def _marine():
    return {"hourly": {"time": [f"2026-10-08T{h:02d}:00" for h in range(24)], "wave_height": [1.4] * 24, "sea_surface_temperature": [16.4] * 24}}


def test_the_sheet_assembles_in_its_order_and_falls_back_between_gauges():
    at = datetime(2026, 10, 8, 17, 5)
    hilo = _hilo(("2026-10-08 10:02", "H", 5.9), ("2026-10-08 16:05", "L", .9), ("2026-10-08 22:06", "H", 5.5))
    out = C.assemble(C.SITE["ob-lincoln"], at, _weather(), _marine(), hilo)
    v = {r["key"]: r["value"] for r in out["rows"]}
    assert [r["key"] for r in out["rows"]] == ["air_temp", "water_temp", "current_weather", "precipitation", "tide", "wave_height", "wind_direction", "wind_speed"]
    assert v == {"air_temp": "60°F", "water_temp": "62°F", "current_weather": "Partly Cloudy", "precipitation": "Yes", "tide": "Low",
                 "wave_height": "4-6 ft", "wind_direction": "NW", "wind_speed": "10 mph"}
    td = next(r for r in out["rows"] if r["key"] == "tide")
    assert td["level"] == "1.2 ft" and td["detail"] == "Low 4:05 PM → high 10:06 PM"   # the turn before and the turn after, nothing else
    assert td["curve"]["at"] == 17 * 60 + 5 and [t["type"] for t in td["curve"]["turns"]] == ["H", "L", "H"]
    assert len(td["curve"]["pts"]) == 24 and td["curve"]["pts"][0][0] == 10 * 60 + 30   # the curve only where the turns cover it
    pr = next(r for r in out["rows"] if r["key"] == "precipitation")
    assert pr["detail"] == "0.03 in in the 72 hours to 5:05 PM" and pr["source"] == "Open-Meteo, model hours"
    # a sheltered site leaves the waves to the eye; a source that failed leaves its fields blank, never the sheet
    aq = C.assemble(C.SITE["aquatic"], at, None, _marine(), None)
    va = {r["key"]: r for r in aq["rows"]}
    assert va["wave_height"]["value"] is None and va["wave_height"]["blank"] == "Check by eye" and "breakwater" in va["wave_height"]["detail"]
    assert va["air_temp"]["value"] is None and va["tide"]["value"] is None and va["precipitation"]["value"] is None and va["water_temp"]["value"] == "62°F"


def test_rain_is_the_model_hours_in_the_72_hours_before_the_sheets_time():
    """2026-10-22: it rained from 5 am and the sky row said Light Rain while the gauge-based row said No (a gauge's
    day posts the next day). The row now sums Open-Meteo's hourly precipitation over the 72 hours before the time:
    forecast hours after it and hours older than 72 don't count."""
    at = datetime(2026, 10, 8, 10, 27)
    hilo = _hilo(("2026-10-08 06:02", "H", 5.9), ("2026-10-08 12:05", "L", .9), ("2026-10-08 18:06", "H", 5.5))
    pr = lambda w: next(r for r in C.assemble(C.SITE["ob-lincoln"], at, w, _marine(), hilo)["rows"] if r["key"] == "precipitation")  # noqa: E731
    w = _weather(rain_hours=(5, 7, 8)); w["hourly"]["precipitation"][72 + 20] = 0.5          # tonight's forecast is not rain that fell
    assert pr(w)["value"] == "Yes" and pr(w)["detail"] == "0.05 in in the 72 hours to 10:27 AM"
    w = _weather(rain_hours=()); w["hourly"]["precipitation"][72 + 20] = 0.5; w["hourly"]["precipitation"][5] = 0.3   # Oct 5 05:00 is 77 h back
    assert pr(w)["value"] == "No" and pr(w)["detail"] == "0.00 in in the 72 hours to 10:27 AM"
    w = _weather(rain_hours=()); w["hourly"]["precipitation"][24 + 11] = 0.3                 # Oct 6 11:00 is within the window
    assert pr(w)["value"] == "Yes" and pr(w)["detail"] == "0.30 in in the 72 hours to 10:27 AM"
    w = _weather(); del w["hourly"]["precipitation"]
    assert pr(w)["value"] is None                                                              # no model hours: left blank


def test_every_field_can_be_edited_in_the_sheets_own_terms():
    out = C.assemble(C.SITE["china"], datetime(2026, 10, 8, 16, 20), _weather(), _marine(), None)
    ed = {r["key"]: r["edit"] for r in out["rows"]}
    assert set(ed) == set(C.EDIT) and len(ed) == 8
    assert ed["air_temp"] == ed["water_temp"] == {"unit": "°F"} and ed["wind_speed"] == {"unit": "mph"}
    assert ed["tide"]["choices"] == ["High", "Low", "Incoming", "Outgoing"] and ed["precipitation"]["choices"] == ["Yes", "No"]
    assert ed["wave_height"]["choices"][0] == "<1 ft" and ed["wave_height"]["choices"][-1] == "12+ ft" and ed["wind_direction"]["choices"] == C.COMPASS8
    for r in out["rows"]:   # whatever the page fills in automatically is one of the choices it offers
        if r["value"] is not None and "choices" in r["edit"]:
            assert r["value"] in r["edit"]["choices"], r


def test_conditions_survives_a_source_failing():
    def boom(*a):
        raise RuntimeError("down")
    out = C.conditions(C.SITE["china"], datetime(2026, 10, 8, 16, 20),
                       {"weather": lambda *a: _weather(), "marine": lambda *a: _marine(), "hilo": boom})
    v = {r["key"]: r["value"] for r in out["rows"]}
    assert out["ok"] and v["tide"] is None and v["precipitation"] == "Yes" and v["air_temp"] == "60°F"   # rain rides on the weather call now


def test_routes_render_and_refuse_bad_input():
    from app.wsgi import app
    with app.test_client() as c:
        h = c.get("/learn/collect").data.decode()
        china = c.get("/learn/collect?site=china").data.decode()
        bad = c.get("/api/learn/conditions?site=nowhere&date=2026-10-08&time=10:00")
        far = c.get("/api/learn/conditions?site=china&date=2020-01-01&time=10:00")
    assert "{{" not in h and "{%" not in h and "<h1>Collect</h1>" in h and '<meta name="robots" content="noindex">' in h
    assert h.count('class="pick"') == 4 and h.count('class="sec site-card"') == 4 and h.count("https://www.google.com/maps/search/?api=1&amp;query=") == 4
    pressed = lambda page: re.findall(r'class="pick" data-site="([\w-]+)" aria-pressed="true"', page)
    assert h.count("Photo of the sampling spot coming soon") == 1 and h.count("learn/sites/") == 7 and pressed(h) == []   # China Beach's placeholder; seven photos
    # pick a site first (2026-10-18): no site shows until one is picked; ?site= opens on one, the others stay hidden
    shown = lambda page: re.findall(r'<section class="sec site-card" data-site="([\w-]+)">', page)
    assert shown(h) == [] and '<section class="sec" id="sheet" hidden>' in h and 'id="pick-hint">' in h
    assert shown(china) == ["china"] and '<section class="sec" id="sheet">' in china and pressed(china) == ["china"]
    assert '<details class="howto" id="how">' in h and "<details open" not in h                     # Taking a sample, folded
    assert h.count('<li>') >= 6 and "about 2 ft" in h and "6 in" in h and "wave coming in" in h and 'id="cx-form"' in h and 'id="cx-site"' not in h
    assert 'id="cx-obs"' in h and 'id="cx-save"' in h and 'id="cx-tide"' not in h and 'class="tide"' in h and "localStorage" in h and "toBlob" in h   # observations, the image; the tide curve lives in its tile
    assert 'class="on" aria-current="page">Collect' in h and "coming soon" not in h.replace("Photo of the sampling spot coming soon", "")
    assert bad.status_code == 400 and json.loads(bad.data)["error"] == "unknown site"
    assert far.status_code == 400 and "last 90 days" in json.loads(far.data)["error"]


def test_site_photos_exist_and_say_what_they_show():
    """Chase's photos (2026-10-22): the way in and the sampling spot, resized under app/static/learn/sites/."""
    for site in C.SITES:
        for file, caption in site["photos"]:
            path = ROOT / "app/static/learn/sites" / file
            assert path.exists() and path.stat().st_size < 400_000, file          # web-sized, no 4 MB originals
            assert caption and caption[0].isupper() and not caption.endswith("."), file
    assert [len(s["photos"]) for s in C.SITES] == [2, 3, 0, 2]                       # China Beach still wants one
    assert all("outfall" in s["photos"][-1][1] for s in C.SITES if s["key"].startswith("ob-"))   # the concrete platforms are the outfalls, the spot is the surf off them
    html = (ROOT / "app/templates/learn/collect.html").read_text()
    assert "s.photos" in html and "figcaption" in html and "learn/sites/" in html and "s.photo }}" not in html


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
