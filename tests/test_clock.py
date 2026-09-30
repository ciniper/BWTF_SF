"""The site reads one clock (shared/clock.py): Pacific for anything a person sees, aware UTC
for stored stamps. Vercel runs in UTC, so a bare datetime.now() there is 7–8 hours ahead of
the beach — the board once said "Wed Sep 30 · SFPUC map checked 5:40 AM" at 10:40 PM on a
Tuesday (Chase, 2026-09-29). Offline."""
import pathlib
import re
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import clock  # noqa: E402

# served code: pages, the shared board, the alert pipeline, the shared clients. Offline
# collectors, model training and the tests themselves are out of scope.
SERVED = [*ROOT.glob("app/*.py"), *ROOT.glob("features/*/page.py"), *ROOT.glob("features/comparison/*.py"),
          *ROOT.glob("features/alerts/*.py"), *ROOT.glob("features/today/*.py"), *ROOT.glob("features/cso_history/*.py"),
          ROOT / "features/forecast/live_dashboard.py", ROOT / "shared/sfpuc_api.py", ROOT / "shared/weather_tides.py"]
BARE = re.compile(r"\bdatetime\.now\(\)|\bdate\.today\(\)|\butcnow\(\)")


def test_no_served_module_reads_a_bare_clock():
    offenders = []
    for f in SERVED:
        for i, line in enumerate(f.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]                                 # a comment may mention the pitfall
            if BARE.search(code) and '"""' not in line and "def _utcnow" not in line:
                offenders.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
    assert not offenders, "bare clock reads (use shared/clock):\n" + "\n".join(offenders)


def test_pacific_clock_follows_daylight_saving():
    july, january = datetime(2026, 7, 1, 12, tzinfo=clock.PACIFIC), datetime(2026, 1, 15, 12, tzinfo=clock.PACIFIC)
    assert july.utcoffset().total_seconds() == -7 * 3600 and january.utcoffset().total_seconds() == -8 * 3600
    assert july.strftime("%Z") == "PDT" and january.strftime("%Z") == "PST"
    assert clock.now_pacific().tzinfo is clock.PACIFIC and clock.now_pacific_naive().tzinfo is None
    assert clock.now_utc().tzinfo is timezone.utc and clock.utc_iso().endswith("+00:00")
    assert abs((clock.now_pacific().replace(tzinfo=None) - clock.now_pacific_naive()).total_seconds()) < 2


def test_the_board_renders_the_beachs_date_and_time_not_the_servers():
    from app import landing as L
    # 10:40 PM Tuesday in San Francisco — 5:40 AM Wednesday in UTC, which is what the server clock says
    when = datetime(2026, 9, 29, 22, 40, tzinfo=clock.PACIFIC)
    assert when.astimezone(timezone.utc).strftime("%a %-I:%M %p") == "Wed 5:40 AM"
    b = L.today_board([], now=when)
    assert (b["date"], b["checked"]) == ("Tue Sep 29", "10:40 PM")
    assert clock.now_pacific().strftime("%Z") in ("PDT", "PST")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
