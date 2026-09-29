"""Migration 015 (alert deliveries) and its Python sides.

Run: venv/bin/python tests/test_alert_deliveries.py

The SQL cannot be executed here (no local Postgres), so the migration tests pin
its shape: the dispatcher is 008's loop plus marked 015 blocks, the Brevo POSTs
are never inside a swallowing block, the checker and trigger are wired, and
nothing touches the tick or the health gate's column.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.alerts import alert_log, cso_alerts, notifiers, render  # noqa: E402
from features.alerts import deliveries as D  # noqa: E402

MIG = ROOT / "db/migrations"
SQL = (MIG / "015_alert_deliveries.sql").read_text()
NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)


def _grab(sql: str, name: str) -> str:
    m = re.search(r"create or replace function public\." + re.escape(name) + r"\(.*?\nend \$\$;\n", sql, re.S)
    assert m, name
    return m.group(0)


def _code_only(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))


def test_table_is_service_role_only_and_indexed_for_the_checker():
    assert "create table if not exists public.alert_deliveries (" in SQL
    for col in ("alert_log_id   bigint references public.alert_log(id)", "recipient      text not null",
                "channel        text not null", "request_id     bigint", "subject        text", "sms_text       text",
                "text_body      text", "html_body      text", "renderer       text",
                "sent_at        timestamptz not null default now()", "checked_at     timestamptz",
                "http_status    integer", "message_id     text", "error          text",
                "delivery_state text", "delivery_at    timestamptz", "source         text not null default 'pg'"):
        assert col in SQL, col
    assert "alter table public.alert_deliveries enable row level security;" in SQL
    assert "revoke all on public.alert_deliveries from anon, authenticated;" in SQL
    assert "grant all on public.alert_deliveries to service_role;" in SQL
    assert "on public.alert_deliveries (id) where checked_at is null;" in SQL


def test_dispatch_is_008_plus_marked_blocks_and_the_posts_are_never_swallowed():
    d015 = _grab(SQL, "bwtf_dispatch_live")
    d008 = _grab((MIG / "008_email_redesign.sql").read_text(), "bwtf_dispatch_live")
    blocks = re.findall(r"^[ \t]*-- 015.*?^[ \t]*-- /015\n", d015, re.S | re.M)
    assert len(blocks) == 7, len(blocks)   # declare + (insert, tags, request id) × email, sms
    stripped = d015
    for b in blocks:
        stripped = stripped.replace(b, "")
    # the only unmarked differences: each body's closing paren moved to its own
    # line (to make room for the tags block) and delivery_id in the sends entry
    stripped = re.sub(r"(rendered->>'(?:html_body|sms_text)'),\n\s*\),", r"\1),", stripped)
    stripped = stripped.replace(", 'delivery_id', del_id)", ")")
    assert stripped == d008
    # each insert/update of the delivery row sits in its own exception block…
    for stmt in ("insert into alert_deliveries", "update alert_deliveries set request_id"):
        assert d015.count(stmt) == 2, stmt
    assert d015.count("exception when others then") == 4
    # …and the POSTs themselves never do: a fault in the new code costs a row, not an email
    posts = re.findall(r"select net\.http_post\(.*?\) into req_id;", d015, re.S)
    assert len(posts) == 2
    for p in posts:
        assert "exception" not in p and "'https://api.brevo.com/v3/smtp/email'" in p
        assert "timeout_milliseconds := 15000" in p and "'bwtf-delivery-' || del_id" in p
    # the renderer stamp is the migration that last defined bwtf_render_alert, on both sides
    latest = max(p.name[:3] for p in MIG.glob("0*.sql")
                 if "create or replace function public.bwtf_render_alert(" in p.read_text())
    newest_dispatch = max((p for p in MIG.glob("0*.sql")
                           if "create or replace function public.bwtf_dispatch_live(" in p.read_text()),
                          key=lambda p: p.name)
    assert f"renderer_ver text := '{latest}'" in _grab(newest_dispatch.read_text(), "bwtf_dispatch_live"), latest
    assert render.RENDERER_VERSION == latest


def test_link_trigger_and_checker_are_wired_and_never_touch_the_tick_or_health_gate():
    assert "create or replace function public.bwtf_link_deliveries()" in SQL
    assert "create trigger alert_log_link_deliveries" in SQL
    assert "after insert on public.alert_log" in SQL
    assert "execute function public.bwtf_link_deliveries()" in SQL
    assert "(s->>'delivery_id')::bigint" in SQL and "jsonb_typeof(new.results->'recipients') = 'array'" in SQL
    chk = _grab(SQL, "bwtf_check_deliveries")
    for needle in ("left join net._http_response r on r.id = ad.request_id", "where ad.checked_at is null",
                   "d.status_code between 200 and 299", "body->>'messageId'", "bwtf_log_error('delivery',",
                   "interval '6 hours'", "no reply recorded before pg_net purged it",
                   "request id not recorded at dispatch", "bwtf_log_error('deliveries', sqlerrm, null)"):
        assert needle in chk, needle
    assert "cron.schedule('bwtf-check-deliveries', '* * * * *', 'select public.bwtf_check_deliveries()')" in SQL
    assert "cron.unschedule('bwtf-check-deliveries')" in SQL
    code = _code_only(SQL)
    assert "last_error" not in code            # the health gate's column
    assert "bwtf_shadow_tick" not in code       # the tick is not redefined
    for fn in ("bwtf_dispatch_live(jsonb, jsonb, boolean)", "bwtf_link_deliveries()", "bwtf_check_deliveries()"):
        assert f"revoke all on function public.{fn} from public, anon, authenticated;" in SQL, fn
        assert f"grant execute on function public.{fn} to service_role;" in SQL, fn


def _rows():
    return [
        {"id": 1, "recipient": "a@x.org", "channel": "email", "sent_at": "2026-09-28T19:29:00+00:00",
         "checked_at": "2026-09-28T19:30:00+00:00", "http_status": 201, "message_id": "<m1>",
         "subject": "SF Beach Alert: <b>", "text_body": "hi <b>", "html_body": '<html><body><b>x</b> "q"</body></html>'},
        {"id": 2, "recipient": "b@x.org", "channel": "email", "sent_at": "2026-09-28T19:29:00+00:00",
         "checked_at": "2026-09-28T19:30:00+00:00", "http_status": 401, "error": "Key not found", "subject": "s"},
        {"id": 3, "recipient": "5551234567@vtext.com", "channel": "sms", "sent_at": "2026-09-28T19:58:00+00:00",
         "subject": " ", "sms_text": "test"},
        {"id": 4, "recipient": "c@x.org", "channel": "email", "sent_at": "2026-09-28T12:00:00+00:00",
         "checked_at": "2026-09-28T18:30:00+00:00", "error": "no reply recorded before pg_net purged it"},
        {"id": 5, "recipient": "d@x.org", "channel": "email", "sent_at": "2026-09-28T19:29:00+00:00",
         "http_status": 201, "delivery_state": "delivered", "delivery_at": "2026-09-28T19:29:41+00:00"},
        {"id": 6, "recipient": "e@x.org", "channel": "email", "sent_at": "2026-09-28T19:00:00+00:00"},
        {"id": 7, "recipient": "f@x.org", "channel": "email", "sent_at": "2026-09-28T19:29:00+00:00",
         "http_status": 201, "delivery_state": "hard_bounce", "delivery_at": "2026-09-28T19:30:00+00:00"},
    ]


def test_status_reads_level2_then_brevo_then_age():
    states = [D.status_of(r, NOW)[0] for r in _rows()]
    assert states == ["accepted", "failed", "pending", "unknown", "delivered", "unknown", "bounced"], states
    assert D.status_of(_rows()[1], NOW)[1] == "Key not found"
    assert "12:29 PM" in D.status_of(_rows()[4], NOW)[1]          # Pacific
    s = D.summarize(_rows(), NOW)
    assert s == {"messages": 7, "accepted": 1, "failed": 1, "pending": 1, "unknown": 2, "delivered": 1, "bounced": 1, "opened": 0}, s


def test_render_section_escapes_collapses_and_tallies():
    data = {"events": [{"log": {"id": 114, "created_at": "2026-09-28T19:29:00+00:00", "event_type": "posted",
                                "station_names": ["Islais Creek"], "recipient_count": 3, "simulated": True},
                        "deliveries": _rows()},
                       {"log": {"id": 100, "created_at": "2026-09-20T19:29:00+00:00", "event_type": "cso",
                                "station_names": ["Sunnydale Cove"], "recipient_count": 2, "simulated": False},
                        "deliveries": []}],
            "summary": D.summarize(_rows(), NOW), "days": 30}
    html = D.render_section(data, NOW)
    assert html.startswith("<p class='dsummary'>Last 30 days: 7 messages · 1 accepted · 1 delivered · 1 pending · 2 unknown · 1 failed · 1 bounced.</p>")
    assert html.count("<details class='devent'") == 2 and html.count("<details class='devent' open>") == 1
    assert "Sep 28, 12:29 PM" in html and "bacteria posting" in html and "CSO discharge" in html
    assert "<b>" not in html.replace("&lt;b&gt;", "")           # subject/body escaped
    assert "srcdoc=\"&lt;html&gt;" in html and "sandbox=''" in html
    assert 'class="dchip sim">test</span>' in html
    assert "sent before migration 015" in html                 # the empty event explains itself
    assert "dchip bad\">failed</span> <small class='mute'>Key not found" in html
    empty = D.render_section({"events": [], "summary": {"messages": 0}, "days": 30}, NOW)
    assert "0 messages" in empty and "No alerts have reached anyone yet" in empty


def test_sends_to_deliveries_carries_message_and_reply():
    class Fake:
        last_sends = [{"to": "a@x.org", "http_status": 201, "message_id": "<m>", "error": None},
                      {"to": "b@x.org", "http_status": 401, "message_id": None, "error": "BrevoSendError: Brevo API 401"}]
    rendered = {"subject": "S", "sms_text": "T", "text_body": "B", "html_body": "<h>"}
    rows = D.sends_to_deliveries(Fake(), "email", rendered, True)
    assert [r["recipient"] for r in rows] == ["a@x.org", "b@x.org"]
    assert rows[0] == {"recipient": "a@x.org", "channel": "email", "simulated": True, "subject": "S",
                       "renderer": render.RENDERER_VERSION, "http_status": 201, "message_id": "<m>", "error": None,
                       "source": "manual", "text_body": "B", "html_body": "<h>"}
    assert rows[1]["http_status"] == 401 and "sms_text" not in rows[1]
    sms = D.sends_to_deliveries(Fake(), "sms", rendered, False)
    assert sms[0]["subject"] == " " and sms[0]["sms_text"] == "T" and "html_body" not in sms[0]
    assert D.sends_to_deliveries(object(), "email", rendered, False) == []


def test_record_dispatch_stores_bodies_once_and_links_them_to_the_new_row():
    calls = []
    orig = (alert_log.sb.insert, alert_log.sb.is_configured)
    alert_log.sb.is_configured = lambda: True
    def fake_insert(table, rows, returning=False):
        calls.append((table, rows, returning))
        return [{"id": 42}] if returning else []
    alert_log.sb.insert = fake_insert
    try:
        alert_log.record_dispatch(source="manual", event_type="manual_dispatch", station_ids=["4619"],
                                  station_names=["Islais Creek"], recipient_count=1, channel="email", simulated=True,
                                  results=[{"email": "a@x.org", "delivery": "email", "deliveries": [
                                      {"recipient": "a@x.org", "channel": "email", "simulated": True, "subject": "S",
                                       "html_body": "<h>", "http_status": 201, "message_id": "<m>", "source": "manual"}]}])
    finally:
        alert_log.sb.insert, alert_log.sb.is_configured = orig
    assert [c[0] for c in calls] == ["alert_log", "alert_deliveries"]
    log_rows, returning = calls[0][1], calls[0][2]
    assert returning is True and "deliveries" not in log_rows[0]["results"][0]
    dl = calls[1][1][0]
    assert dl["alert_log_id"] == 42 and dl["checked_at"] and dl["html_body"] == "<h>" and dl["http_status"] == 201
    # a plain result list (no deliveries) still logs as before, without asking for the id back
    calls.clear()
    alert_log.sb.is_configured = lambda: True
    alert_log.sb.insert = fake_insert
    try:
        alert_log.record_dispatch(source="manual", event_type="manual_dispatch", station_ids=[], station_names=[],
                                  recipient_count=0, channel="email", simulated=False, results=[{"email": "a@x.org"}])
    finally:
        alert_log.sb.insert, alert_log.sb.is_configured = orig
    assert [c[0] for c in calls] == ["alert_log"] and calls[0][2] is False


def test_brevo_reply_is_kept_on_success_and_failure():
    class Resp:
        def __init__(self, status, body):
            self.status_code, self.text = status, body
        def json(self):
            import json
            return json.loads(self.text)
    orig = notifiers.requests.post
    notifiers.requests.post = lambda *a, **k: Resp(201, '{"messageId":"<201@relay>"}')
    try:
        assert notifiers._send_via_brevo("from@x.org", "to@x.org", "s", "t") == {"http_status": 201, "message_id": "<201@relay>"}
        notifiers.requests.post = lambda *a, **k: Resp(401, '{"code":"unauthorized","message":"Key not found"}')
        try:
            notifiers._send_via_brevo("from@x.org", "to@x.org", "s", "t")
            assert False, "should have raised"
        except notifiers.BrevoSendError as exc:
            assert exc.status == 401 and "Key not found" in str(exc)
        # the notifier records both outcomes per recipient, and still reports False on failure
        os.environ["BREVO_API_KEY"] = "test-key"
        n = notifiers.EmailNotifier(from_email="from@x.org", to_emails=["to@x.org"])
        assert n.send_message("s", "t", ["to@x.org"], "<h>") is False
        assert n.last_sends == [{"to": "to@x.org", "http_status": 401, "message_id": None,
                                 "error": "BrevoSendError: Brevo API 401: " + '{"code":"unauthorized","message":"Key not found"}'}]
        notifiers.requests.post = lambda *a, **k: Resp(201, '{"messageId":"<ok>"}')
        assert n.send_message("s", "t", ["to@x.org"]) is True
        assert n.last_sends == [{"to": "to@x.org", "http_status": 201, "message_id": "<ok>", "error": None}]
    finally:
        notifiers.requests.post = orig
        os.environ.pop("BREVO_API_KEY", None)


def test_manual_dispatcher_attaches_deliveries_to_each_result():
    from features.alerts.subscriptions import SiteSubscription
    from shared.sfpuc_api import SFPUCStation, StationStatus

    class FakeNotifier:
        def __init__(self, to_emails=None):
            self.to_emails = to_emails
            self.last_error = None
            self.last_sends = []
        def send_message(self, subject, text, to_emails, html=None):
            self.last_sends = [{"to": e, "http_status": 201, "message_id": "<x>", "error": None} for e in to_emails]
            return True

    orig = cso_alerts.EmailNotifier, cso_alerts.render_alert
    cso_alerts.EmailNotifier = FakeNotifier
    cso_alerts.render_alert = lambda tr, sim, zone=None, **kw: {"subject": "S", "sms_text": "T", "text_body": "B", "html_body": "<h>"}
    os.environ["BREVO_API_KEY"] = "test-key"
    try:
        st = SFPUCStation("4619", "Islais Creek", StationStatus.POSTED, True, "BAY#320", None, None, 37.7, -122.4, "R", "R")
        sub = SiteSubscription("a@x.org", "", "", ["4619"], "", "")
        results = cso_alerts.dispatch_subscription_alerts([sub], [st], ["4619"], "email")
    finally:
        cso_alerts.EmailNotifier, cso_alerts.render_alert = orig
        os.environ.pop("BREVO_API_KEY", None)
    assert len(results) == 1 and results[0]["delivered"] is True
    d = results[0]["deliveries"]
    assert d == [{"recipient": "a@x.org", "channel": "email", "simulated": True, "subject": "S",
                  "renderer": render.RENDERER_VERSION, "http_status": 201, "message_id": "<x>", "error": None,
                  "source": "manual", "text_body": "B", "html_body": "<h>"}]



def test_migration_016_adds_only_the_opened_column():
    sql = (MIG / "016_alert_deliveries_opened.sql").read_text()
    code = _code_only(sql)
    assert "alter table public.alert_deliveries add column if not exists opened_at timestamptz;" in code
    assert code.count("alter table") == 1 and "create " not in code and "drop " not in code


# ── level 2: Brevo's events ──────────────────────────────────────────────────

def _ev(kind, msg, date, **extra):
    return {"event": kind, "messageId": msg, "date": date, "email": "x@x.org", **extra}


def test_pick_state_prefers_delivery_then_hard_failure_then_soft_then_deferred():
    assert D._pick_state([]) is None
    assert D._pick_state([_ev("requests", "<a>", "2026-09-29T06:40:01.000Z")]) is None
    assert D._pick_state([_ev("requests", "<a>", "2026-09-29T06:40:01.000Z"),
                          _ev("deferred", "<a>", "2026-09-29T06:41:00.000Z"),
                          _ev("delivered", "<a>", "2026-09-29T06:45:00.000Z")]) == ("delivered", "2026-09-29T06:45:00+00:00", None)
    st = D._pick_state([_ev("softBounces", "<a>", "2026-09-29T06:41:00.000Z", reason="mailbox full"),
                        _ev("hardBounces", "<a>", "2026-09-29T07:00:00.000Z", reason="user unknown")])
    assert st == ("hard_bounce", "2026-09-29T07:00:00+00:00", "user unknown")
    assert D._pick_state([_ev("softBounces", "<a>", "2026-09-29T06:41:00.000Z", reason="mailbox full"),
                          _ev("deferred", "<a>", "2026-09-29T06:50:00.000Z")])[0] == "soft_bounce"
    assert D._pick_state([_ev("deferred", "<a>", "2026-09-29T06:50:00.000Z")])[0] == "deferred"
    assert D._pick_state([_ev("blocked", "<a>", "2026-09-29T06:50:00.000Z", reason="reputation")])[0] == "blocked"
    # a delivery after a failure still counts as delivered (Brevo retried and got through)
    assert D._pick_state([_ev("blocked", "<a>", "2026-09-29T06:50:00.000Z"),
                          _ev("delivered", "<a>", "2026-09-29T06:20:00.000Z")])[0] == "delivered"


def test_poll_brevo_matches_rows_by_message_id_or_tag_and_writes_only_changes():
    selects, updates, gets = [], [], []
    rows = [{"id": 1, "message_id": "<m1>", "delivery_state": None, "sent_at": "2026-09-29T06:40:00+00:00"},
            {"id": 2, "message_id": "<m2>", "delivery_state": "soft_bounce", "sent_at": "2026-09-29T06:40:00+00:00"},
            {"id": 3, "message_id": "<m3>", "delivery_state": None, "sent_at": "2026-09-29T06:40:00+00:00"},
            {"id": 4, "message_id": "<m4>", "delivery_state": "deferred", "sent_at": "2026-09-29T06:40:00+00:00"},
            {"id": 5, "message_id": "<lost>", "delivery_state": None, "sent_at": "2026-09-29T06:40:00+00:00"},
            {"id": 6, "message_id": "<m6>", "delivery_state": "delivered", "sent_at": "2026-09-29T06:40:00+00:00", "opened_at": None},
            {"id": 7, "message_id": "<m7>", "delivery_state": "delivered", "sent_at": "2026-09-29T06:40:00+00:00", "opened_at": None}]
    events = [_ev("delivered", "<m1>", "2026-09-29T06:40:05.000Z"),
              _ev("opened", "<m1>", "2026-09-29T07:10:00.000Z"), _ev("opened", "<m1>", "2026-09-29T06:50:00.000Z"),   # earliest open wins
              _ev("opened", "<m6>", "2026-09-29T06:59:00.000-07:00"),                    # delivered earlier, opened now → only opened_at
              _ev("loadedByProxy", "<m7>", "2026-09-29T06:59:00.000Z"),                 # Apple prefetch → not an open
              _ev("softBounces", "<m2>", "2026-09-29T06:41:00.000Z", reason="mailbox full"),   # unchanged → no write
              _ev("requests", "<m3>", "2026-09-29T06:40:01.000Z"),                             # accepted only → no write
              _ev("hardBounces", "<m4>", "2026-09-29T07:00:00.000Z", reason="user unknown"),
              _ev("delivered", None, "2026-09-29T06:40:09.000Z", tag="bwtf-alert,bwtf-delivery-5")]   # no messageId: the tag finds it (Brevo joins tags with commas)

    class Resp:
        status_code = 200
        text = ""
        def __init__(self, ev): self._ev = ev
        def json(self): return {"events": self._ev}

    import requests
    orig = (D.sb.select, D.sb.update, requests.get)
    D.sb.select = lambda table, params: (selects.append((table, params)) or rows) if table == "alert_deliveries" else [{"value": "k"}]
    D.sb.update = lambda table, filters, patch: updates.append((table, filters, patch)) or []
    requests.get = lambda url, params=None, headers=None, timeout=None: gets.append((url, params, headers)) or Resp(events)
    os.environ.pop("BREVO_API_KEY", None)
    try:
        out = D.poll_brevo(now=datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc))
    finally:
        D.sb.select, D.sb.update, requests.get = orig
    assert out["candidates"] == 7 and out["events"] == 9
    assert out["updated"] == 4 and out["states"] == {"delivered": 2, "hard_bounce": 1, "opened": 2}, out
    # candidate filter: accepted rows of the last week without a final state, or not yet opened
    _, params = selects[0]
    assert params["message_id"] == "not.is.null"
    assert params["or"] == "(delivery_state.is.null,delivery_state.in.(deferred,soft_bounce),opened_at.is.null)"
    assert params["sent_at"] == "gte.2026-09-22T08:00:00+00:00"
    # one events call, keyed with the watcher_config key, never echoed in the result
    assert len(gets) == 1 and gets[0][2]["api-key"] == "k" and gets[0][1]["days"] == 8 and "k" not in str(out)
    by_id = {int(f["id"].split(".")[1]): p for _, f, p in updates}
    assert by_id[1] == {"delivery_state": "delivered", "delivery_at": "2026-09-29T06:40:05+00:00", "error": None,
                        "opened_at": "2026-09-29T06:50:00+00:00"}
    assert by_id[4] == {"delivery_state": "hard_bounce", "delivery_at": "2026-09-29T07:00:00+00:00", "error": "user unknown"}
    assert by_id[5]["delivery_state"] == "delivered"
    assert by_id[6] == {"opened_at": "2026-09-29T13:59:00+00:00"}       # state untouched, offset normalised to UTC
    assert 7 not in by_id                                                # proxy load is not an open
    assert D._first_open([]) is None
    # dry run: same decisions, nothing written
    D.sb.select = lambda table, params: rows if table == "alert_deliveries" else [{"value": "k"}]
    D.sb.update = lambda *a: (_ for _ in ()).throw(AssertionError("wrote in dry run"))
    requests.get = lambda *a, **k: Resp(events)
    try:
        dry = D.poll_brevo(now=datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc), dry_run=True)
    finally:
        D.sb.select, D.sb.update, requests.get = orig
    assert [p["id"] for p in dry["patches"]] == [1, 4, 5, 6]
    # no candidates → no Brevo call at all
    D.sb.select = lambda table, params: []
    requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("called Brevo with nothing to ask"))
    try:
        assert D.poll_brevo()["candidates"] == 0
    finally:
        D.sb.select, D.sb.update, requests.get = orig


def test_refresh_hook_swallows_poller_failures():
    from features.forecast import page as fpage
    orig = D.poll_brevo
    D.poll_brevo = lambda: (_ for _ in ()).throw(AssertionError("polled from a non-production host"))
    os.environ.pop("FORECAST_CACHE_WRITE", None); os.environ.pop("VERCEL_ENV", None)
    try:
        assert fpage._poll_deliveries() == 0                # laptops and tests never poll (same gate as the cache row)
        os.environ["FORECAST_CACHE_WRITE"] = "1"
        D.poll_brevo = lambda: (_ for _ in ()).throw(RuntimeError("Brevo events API 401"))
        assert fpage._poll_deliveries() == -1
        D.poll_brevo = lambda: {"candidates": 2, "updated": 1, "events": 9, "states": {"delivered": 1}}
        assert fpage._poll_deliveries() == 1
    finally:
        D.poll_brevo = orig
        os.environ.pop("FORECAST_CACHE_WRITE", None)
    src = (ROOT / "features/forecast/page.py").read_text()
    assert "_mirror_samples()\n            _poll_deliveries()" in src      # runs with the other post-store side jobs


def test_panel_shows_arrival_states_reasons_and_bouncing_addresses():
    rows = [{"id": 1, "recipient": "ok@x.org", "channel": "email", "sent_at": "2026-09-28T19:29:00+00:00",
             "http_status": 201, "delivery_state": "delivered", "delivery_at": "2026-09-28T19:29:41+00:00"},
            {"id": 2, "recipient": "gone@x.org", "channel": "email", "sent_at": "2026-09-28T19:29:00+00:00",
             "http_status": 201, "delivery_state": "hard_bounce", "delivery_at": "2026-09-28T19:30:00+00:00", "error": "user unknown"},
            {"id": 3, "recipient": "gone@x.org", "channel": "email", "sent_at": "2026-09-27T19:29:00+00:00",
             "http_status": 201, "delivery_state": "blocked", "delivery_at": "2026-09-27T19:30:00+00:00", "error": "reputation"}]
    assert D.status_of(rows[1], NOW) == ("bounced", "hard bounce Sep 28, 12:30 PM: user unknown")
    assert D.status_of(rows[0], NOW) == ("delivered", "delivered Sep 28, 12:29 PM")
    b = D.bouncing(rows, NOW)
    assert b == [{"recipient": "gone@x.org", "count": 2, "last": "blocked Sep 27, 12:30 PM: reputation"}]
    rows[0]["opened_at"] = "2026-09-28T19:33:00+00:00"
    assert D.summarize(rows, NOW)["opened"] == 1
    html = D.render_section({"events": [{"log": {"id": 1, "created_at": "2026-09-28T19:29:00+00:00", "event_type": "posted",
                                                 "station_names": ["X"], "recipient_count": 3, "simulated": False},
                                         "deliveries": rows}],
                             "summary": D.summarize(rows, NOW), "days": 30, "bouncing": b}, NOW)
    assert "1 delivered · 1 opened · 2 bounced" in html
    assert "delivered Sep 28, 12:29 PM · opened Sep 28, 12:33 PM" in html
    assert "Not arriving: gone@x.org (2: blocked Sep 27, 12:30 PM: reputation)" in html
    assert "nothing is deactivated automatically" in html

if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
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
