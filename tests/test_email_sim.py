"""Email simulator (features/alerts/email_sim.py, /alerts/email-sim): the current sender's count, the hold and its
two window rules, the same-day rule, subscriptions, and the 11/13–11/17 storm audit it was built from."""
import json
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.alerts import email_sim as E  # noqa: E402

PT = E.PACIFIC
FIXTURE = ROOT / "tests/fixtures/email_sim_windows_2026-11-17.json"


def at(day, hh, mm):
    return datetime(2026, 11, day, hh, mm, tzinfo=PT)


def ch(day, hh, mm, sid, frm, to, name=None):
    return E.Change(at(day, hh, mm), sid, name or sid, frm, to)


START, END = at(13, 0, 0), at(18, 0, 0)


def storm_week():
    return E.changes_from_windows(json.loads(FIXTURE.read_text())["stations"])


def test_the_storm_week_audit_14_today_11_with_the_same_day_rule_5_with_a_15_minute_hold():
    """docs/discharge-observations.md, "Too many alert emails": someone subscribed to every station, 11/13–11/17."""
    c, every = storm_week(), sorted(E.NAME_OF)
    now = E.simulate(c, every, START, END)
    rule = E.simulate(c, every, START, END, same_day=True)
    held = E.simulate(c, every, START, END, 15, "first", same_day=True)
    assert (now["total"], rule["total"], held["total"]) == (14, 11, 5)
    assert {d.isoformat(): n for d, n in now["by_day"].items()} == {"2026-11-13": 6, "2026-11-15": 5, "2026-11-17": 3}
    assert {d.isoformat(): n for d, n in rule["by_day"].items()} == {"2026-11-13": 6, "2026-11-15": 4, "2026-11-17": 1}
    skipped = [(x.station_id, x.at.astimezone(PT).strftime("%m/%d %H:%M")) for x in rule["skipped"]]
    assert skipped == [("4603", "11/15 19:03"), ("4605", "11/17 09:11"), ("4601", "11/17 09:27")]   # Vicente, Lincoln, Fort Funston
    assert held["longest_wait"] == 15


def test_the_current_sender_is_one_email_per_minute_with_a_worse_status():
    c = [ch(13, 12, 45, "a", "ok", "posted"), ch(13, 12, 45, "b", "ok", "posted"),   # same minute: one email
         ch(13, 12, 48, "a", "posted", "cso"),                                          # orange → red: its own email
         ch(13, 18, 0, "a", "cso", "posted"), ch(14, 9, 0, "b", "posted", "ok")]      # getting better sends nothing
    r = E.simulate(c, ["a", "b"], START, END)
    assert r["total"] == 2 and [len(e.changes) for e in r["emails"]] == [2, 1] and r["longest_wait"] == 0
    assert r["emails"][1].stations == [("a", "cso")]


def test_the_hold_from_the_first_change_never_waits_longer_than_the_hold():
    c = [ch(13, 8, m, s, "ok", "posted") for m, s in ((0, "a"), (10, "b"), (20, "c"), (30, "d"))]
    first = E.simulate(c, "abcd", START, END, 15, "first")
    assert [len(e.changes) for e in first["emails"]] == [2, 2] and first["longest_wait"] == 15
    restart = E.simulate(c, "abcd", START, END, 15, "restart")   # every change restarts the clock: one email, 45 minutes on
    assert restart["total"] == 1 and restart["longest_wait"] == 45
    edge = E.simulate([ch(13, 8, 0, "a", "ok", "posted"), ch(13, 8, 15, "b", "ok", "posted")], "ab", START, END, 15)
    assert edge["total"] == 1                                    # a change exactly at the close still joins (the audit's rule)


def test_one_email_shows_each_station_at_its_worst():
    c = [ch(13, 6, 32, "w", "ok", "posted", "Baker West"), ch(13, 6, 35, "w", "posted", "cso", "Baker West")]
    r = E.simulate(c, ["w"], START, END, 15)
    assert r["total"] == 1 and r["emails"][0].stations == [("Baker West", "cso")]


def test_the_same_day_rule_skips_an_on_off_on_but_not_a_flag_back_the_next_day():
    lincoln = [ch(13, 12, 48, "L", "posted", "cso"),          # Friday's discharge
               ch(17, 8, 34, "L", "cso", "ok"), ch(17, 9, 11, "L", "ok", "cso")]   # Tuesday: off, back 37 minutes later
    r = E.simulate(lincoln, ["L"], START, END, same_day=True)
    assert r["total"] == 1 and [x.at for x in r["skipped"]] == [at(17, 9, 11)]
    next_day = [ch(14, 7, 0, "v", "ok", "cso"), ch(14, 18, 0, "v", "cso", "ok"), ch(15, 9, 0, "v", "ok", "cso")]
    assert E.simulate(next_day, ["v"], START, END, same_day=True)["total"] == 2 and not E.simulate(next_day, ["v"], START, END, same_day=True)["skipped"]
    posted_only = [ch(14, 7, 0, "p", "ok", "posted"), ch(14, 8, 0, "p", "posted", "ok"), ch(14, 9, 0, "p", "ok", "posted")]
    assert E.simulate(posted_only, ["p"], START, END, same_day=True)["total"] == 2   # the rule is for discharges only


def test_a_flag_up_before_the_range_still_counts_for_the_same_day_rule():
    c = [ch(14, 1, 0, "x", "ok", "cso"), ch(14, 6, 0, "x", "cso", "ok"), ch(14, 9, 0, "x", "ok", "cso")]
    r = E.simulate(c, ["x"], at(14, 8, 0), END, same_day=True)
    assert r["total"] == 0 and len(r["skipped"]) == 1


def test_subscriptions_and_the_form_defaults():
    opts = {o["value"]: o for o in E.subscription_options()}
    assert len(opts["all"]["ids"]) == 20 and opts["zone:ocean"]["ids"] == ["4601", "4602", "4603", "4604", "4605", "4606"]
    assert opts["station:4605"]["label"] == "Ocean Beach at Lincoln Way"
    s = E.parse_settings({}, date(2026, 11, 17))           # first visit: the proposal
    assert (s["from"], s["to"], s["hold"], s["window"], s["same_day"], s["sub"]) == (date(2026, 11, 11), date(2026, 11, 17), 15, "first", True, "all")
    s = E.parse_settings({"go": ["1"], "hold": ["0"], "sub": ["zone:ocean"], "from": ["2026-11-17"], "to": ["2026-11-13"]}, date(2026, 11, 17))
    assert s["same_day"] is False and s["hold"] == 0 and s["sub"] == "zone:ocean" and (s["from"], s["to"]) == (date(2026, 11, 13), date(2026, 11, 17))
    s = E.parse_settings({"go": ["1"], "hold": ["abc"], "sub": ["nope"], "window": ["x"]}, date(2026, 11, 17))
    assert (s["hold"], s["sub"], s["window"]) == (15, "all", "first")
    r = E.run(storm_week(), {"from": date(2026, 11, 13), "to": date(2026, 11, 17), "hold": 0, "window": "first", "same_day": True, "sub": "station:4605"})
    assert (r["now"]["total"], r["plan"]["total"], len(r["days"])) == (3, 2, 5)    # Lincoln: Fri posted, Fri discharge, Tue's repeat


def test_the_page_renders_and_sits_behind_the_passphrase():
    from app.wsgi import app
    settings = E.parse_settings({}, date(2026, 11, 17))
    settings["from"] = date(2026, 11, 13)
    with app.test_request_context("/alerts/email-sim"):
        html = E.render_page(storm_week(), settings)
    assert "<h1>Email simulator</h1>" in html and 'href="/alerts/email-sim" class="on" aria-current="page"' in html
    assert '<div class="n">14</div>' in html and '<div class="n">5</div>' in html and html.count('class="skipped"') == 3
    assert "Ocean Beach at Lincoln Way · sewage discharge: its discharge flag was already up earlier that day" in html
    with app.test_client() as c:
        locked = c.get("/alerts/email-sim").data.decode()
        assert 'name="next" value="/alerts/email-sim"' in locked and "<h1>Admin</h1>" in locked


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("  ok  ", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1; print("  FAIL", name, type(exc).__name__, exc)
    sys.exit(1 if failures else 0)
