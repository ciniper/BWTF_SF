"""Migration 017 (one-click unsubscribe) and its Python sides.

Run: venv/bin/python tests/test_unsubscribe.py

No local Postgres, so the SQL is pinned by shape: the token column and
site_url row exist, the renderer keeps 008's body with the footer swapped, the
dispatcher is 015's loop plus marked 017 blocks (the POSTs still never inside a
swallowing block), and the RFC 8058 headers ride on every email.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.alerts import render, subscriptions  # noqa: E402
from features.unsubscribe import page as unsub  # noqa: E402

MIG = ROOT / "db/migrations"
SQL = (MIG / "017_unsubscribe.sql").read_text()
TOKEN = "0f3b2c9e-1d2a-4e5f-8a9b-0c1d2e3f4a5b"
URL = f"https://bwtf-sf.vercel.app/unsubscribe?t={TOKEN}"


def _grab(sql: str, name: str) -> str:
    m = re.search(r"create or replace function public\." + re.escape(name) + r"\(.*?\nend \$\$;\n", sql, re.S)
    assert m, name
    return m.group(0)


def test_migration_adds_the_token_the_site_url_and_drops_the_old_renderer_signatures():
    assert "add column if not exists unsubscribe_token uuid not null default gen_random_uuid()" in SQL
    assert "add column if not exists unsubscribed_at   timestamptz" in SQL
    assert "create unique index if not exists subscribers_unsubscribe_token_key" in SQL
    assert "values ('site_url', 'https://bwtf-sf.vercel.app')" in SQL and "on conflict (key) do nothing" in SQL
    assert "drop function if exists public.bwtf_render_alert(jsonb, boolean);" in SQL
    assert "drop function if exists public.bwtf_render_alert(jsonb, boolean, text);" in SQL
    for fn in ("bwtf_render_alert(jsonb, boolean, text, text)", "bwtf_dispatch_live(jsonb, jsonb, boolean)"):
        assert f"revoke all on function public.{fn} from public, anon, authenticated;" in SQL, fn
        assert f"grant execute on function public.{fn} to service_role;" in SQL, fn


def test_renderer_is_008_with_the_footer_swapped():
    r017 = _grab(SQL, "bwtf_render_alert")
    r008 = _grab((MIG / "008_email_redesign.sql").read_text(), "bwtf_render_alert")
    assert "p_unsubscribe_url text default null" in r017
    # both footers exist: the link when a URL is given, 008's sentence otherwise
    assert """case when unsub is null then 'Reply "unsubscribe" to stop.'""" in r017
    assert "else 'Change your sites: ' || manage || E'\\nUnsubscribe: ' || unsub end" in r017
    assert "'Reply to this email with &quot;unsubscribe&quot; to stop alerts.'" in r017
    assert """'<a href="' || manage || '" style="color:#8a93a3;text-decoration:underline;">Change your sites</a>'""" in r017
    assert """' &middot; <a href="' || unsub || '" style="color:#8a93a3;text-decoration:underline;">Unsubscribe</a>' end""" in r017
    assert "replace(nullif(trim(coalesce(p_unsubscribe_url, '')), ''), '/unsubscribe?t=', '/manage?t=')" in r017
    # everything above the footers is byte-identical to 008 (subject, sms, rows, header)
    cut = lambda s: s[s.index("select case (p_transitions->0->>'to')"):s.index("'You subscribed to SF beach alerts (Surfrider SF Blue Water Task Force). ")]
    assert cut(r017) == cut(r008)


def test_dispatch_is_015_plus_marked_blocks_and_sends_the_rfc8058_headers():
    d017 = _grab(SQL, "bwtf_dispatch_live")
    d015 = _grab((MIG / "015_alert_deliveries.sql").read_text(), "bwtf_dispatch_live")
    blocks = re.findall(r"^[ \t]*-- 017.*?^[ \t]*-- /017\n", d017, re.S | re.M)
    assert len(blocks) == 4, len(blocks)          # declare, site_url, token lookup, headers
    stripped = d017
    for b in blocks:
        stripped = stripped.replace(b, "")
    stripped = stripped.replace("rendered := bwtf_render_alert(matched, p_simulated, zone, unsub_url);",
                                "rendered := bwtf_render_alert(matched, p_simulated, zone);")
    stripped = stripped.replace("renderer_ver text := '017'", "renderer_ver text := '008'")
    assert stripped == d015
    assert "'List-Unsubscribe', '<' || unsub_url || '>'" in d017
    assert "'List-Unsubscribe-Post', 'List-Unsubscribe=One-Click'" in d017
    assert "unsub_url := site_url || '/unsubscribe?t=' || tok;" in d017
    posts = re.findall(r"select net\.http_post\(.*?\) into req_id;", d017, re.S)
    assert len(posts) == 2 and all("exception" not in p for p in posts)
    assert "List-Unsubscribe" in posts[0] and "List-Unsubscribe" not in posts[1]   # email only, never the SMS gateway


def test_python_renderer_matches_the_sql_footer_logic_and_version():
    tr = [{"station_id": "4619", "station_name": "Islais Creek", "to": "posted"}]
    plain = render._fallback(tr, False, None)
    linked = render._fallback(tr, False, None, URL)
    MANAGE = URL.replace("/unsubscribe?t=", "/manage?t=")
    assert plain["text_body"].endswith('Reply "unsubscribe" to stop.')
    assert linked["text_body"].endswith(f"Change your sites: {MANAGE}\nUnsubscribe: {URL}")
    assert "Reply to this email with &quot;unsubscribe&quot; to stop alerts.</p>" in plain["html_body"]
    assert (f'<a href="{MANAGE}" style="color:#8a93a3;text-decoration:underline;">Change your sites</a> &middot; '
            f'<a href="{URL}" style="color:#8a93a3;text-decoration:underline;">Unsubscribe</a></p>') in linked["html_body"]
    assert "reply" not in linked["html_body"].lower()                 # the old sentence is gone when linked
    for k in ("subject", "sms_text"):
        assert plain[k] == linked[k]                      # only the footer moves
    assert render._fallback(tr, False, None, "  ") == plain   # blank URL = none
    assert render.RENDERER_VERSION == "017"
    assert render.unsubscribe_url(TOKEN) == URL and render.unsubscribe_url("") is None
    # render_alert passes the URL to the pg renderer
    seen = []
    orig = (render.sb.rpc, render.sb.is_configured)
    render.sb.is_configured = lambda: True
    render.sb.rpc = lambda fn, payload: seen.append((fn, payload)) or {"subject": "S"}
    try:
        assert render.render_alert(tr, True, "East Beaches", URL) == {"subject": "S"}
    finally:
        render.sb.rpc, render.sb.is_configured = orig
    assert seen == [("bwtf_render_alert", {"p_transitions": tr, "p_simulated": True, "p_zone": "East Beaches",
                                           "p_unsubscribe_url": URL})]


def test_subscription_rows_carry_the_token_and_old_rows_still_load():
    row = {"email": "a@x.org", "phone_number": "", "carrier": "", "station_ids": ["4619"],
           "created_at": "", "updated_at": "", "unsubscribe_token": TOKEN}
    assert subscriptions.SubscriptionStore._from_row(row).unsubscribe_token == TOKEN
    assert subscriptions.SubscriptionStore._from_row({k: v for k, v in row.items() if k != "unsubscribe_token"}).unsubscribe_token == ""
    assert subscriptions.SiteSubscription("a@x.org", "", "", ["4619"], "", "").unsubscribe_token == ""


class _FakeSB:
    def __init__(self, rows):
        self.rows, self.updates = rows, []
    def select(self, table, params):
        assert table == "subscribers" and params["unsubscribe_token"].startswith("eq.")
        tok = params["unsubscribe_token"][3:]
        return [r for r in self.rows if r["unsubscribe_token"] == tok]
    def update(self, table, filters, patch):
        self.updates.append((table, filters, patch))
        return []


def _run(handler, query, fake):
    orig = (unsub.sb.select, unsub.sb.update, unsub.render_template, unsub._refresh_backup)
    rendered = {}
    unsub.sb.select, unsub.sb.update = fake.select, fake.update
    unsub.render_template = lambda name, **ctx: rendered.update(ctx) or f"<page {ctx.get('state')}>"
    unsub._refresh_backup = lambda: rendered.update(backup=True)
    try:
        status, ctype, body = handler(query, b"")
    finally:
        unsub.sb.select, unsub.sb.update, unsub.render_template, unsub._refresh_backup = orig
    return status, body.decode(), rendered


def test_page_never_unsubscribes_on_get_and_post_deactivates_once():
    fake = _FakeSB([{"id": "1", "email": "a@x.org", "active": True, "station_ids": ["4619", "4617"],
                     "unsubscribe_token": TOKEN, "unsubscribed_at": None}])
    assert _run(unsub.handle_page, {}, fake)[0] == 400                                   # no token
    assert _run(unsub.handle_page, {"t": ["not-a-uuid"]}, fake)[0] == 400
    assert _run(unsub.handle_page, {"t": ["11111111-2222-4333-8444-555555555555"]}, fake)[0] == 404
    status, body, ctx = _run(unsub.handle_page, {"t": [TOKEN]}, fake)
    assert status == 200 and ctx["state"] == "ask" and ctx["email"] == "a@x.org" and ctx["sites"] == 2 and ctx["token"] == TOKEN
    assert fake.updates == []                                                             # GET wrote nothing
    status, body, ctx = _run(unsub.handle_unsubscribe, {"t": [TOKEN]}, fake)
    assert status == 200 and ctx["state"] == "done" and ctx.get("backup") is True
    assert len(fake.updates) == 1
    table, filters, patch = fake.updates[0]
    assert table == "subscribers" and filters == {"unsubscribe_token": f"eq.{TOKEN}"}
    assert patch["active"] is False and patch["unsubscribed_at"].startswith("20")
    # already inactive: GET says so, POST is idempotent (no second write)
    fake.rows[0]["active"] = False
    assert _run(unsub.handle_page, {"t": [TOKEN]}, fake)[2]["state"] == "already"
    assert _run(unsub.handle_unsubscribe, {"t": [TOKEN]}, fake)[2]["state"] == "done" and len(fake.updates) == 1
    # unknown / missing token on POST
    assert _run(unsub.handle_unsubscribe, {"t": ["11111111-2222-4333-8444-555555555555"]}, fake)[0] == 404
    assert _run(unsub.handle_unsubscribe, {}, fake)[0] == 400


def test_routes_are_registered_ungated():
    src = (ROOT / "app/wsgi.py").read_text()
    assert "import features.unsubscribe.page as unsubscribe_page" in src
    assert 'app.add_url_rule(path, f"unsubscribe-get:{path}", _forecast_view(handler), methods=["GET"])' in src
    assert 'app.add_url_rule(path, f"unsubscribe-post:{path}", _forecast_view(handler), methods=["POST"])' in src
    assert unsub.GET_ROUTES == {"/unsubscribe": unsub.handle_page} and unsub.POST_ROUTES == {"/unsubscribe": unsub.handle_unsubscribe}
    tpl = (ROOT / "app/templates/unsubscribe/page.html").read_text()
    for state in ("ask", "done", "already", "missing", "unknown"):
        assert f"state == '{state}'" in tpl, state
    assert 'method="post" action="/unsubscribe?t={{ token }}"' in tpl and 'name="robots" content="noindex"' in tpl



# ── /manage: change your sites ───────────────────────────────────────────────

def test_manage_page_prechecks_whole_zones_and_saves_through_the_signup_store():
    from features.manage import page as manage
    from features.signup.page import ZONES
    east = ZONES["east"] if "east" in ZONES else next(iter(ZONES.values()))
    east_key = next(k for k, v in ZONES.items() if v is east)
    east_ids = [sid for sid, *_ in east[1]]
    fake = _FakeSB([{"id": "1", "email": "a@x.org", "active": False, "station_ids": east_ids[:-1],
                     "unsubscribe_token": TOKEN, "unsubscribed_at": "2026-09-29T00:00:00+00:00"}])
    # a partial zone (legacy per-station signup) is NOT pre-checked; a whole zone is
    z = manage.zones_for(east_ids[:-1]); assert not any(x["checked"] for x in z)
    z = manage.zones_for(east_ids);      assert [x["key"] for x in z if x["checked"]] == [east_key]
    assert all(x["stations"] for x in z) and len(z) == len(ZONES)

    rendered = {}
    orig = (manage.sb.select, manage.render_template)
    manage.sb.select = fake.select
    manage.render_template = lambda name, **ctx: rendered.update(ctx) or "<page>"
    try:
        assert manage.handle_page({}, b"")[0] == 400
        assert manage.handle_page({"t": ["11111111-2222-4333-8444-555555555555"]}, b"")[0] == 404
        status, _, _ = manage.handle_page({"t": [TOKEN]}, b"")
    finally:
        manage.sb.select, manage.render_template = orig
    assert status == 200 and rendered["state"] == "edit" and rendered["active"] is False and rendered["token"] == TOKEN
    assert rendered["email"] == "a@x.org" and len(rendered["zones"]) == len(ZONES)

    saved = []
    class FakeStore:
        def upsert_subscription(self, email, station_ids):
            saved.append((email, station_ids))
            return type("S", (), {"email": email})()
    updates = []
    orig = (manage.sb.select, manage.sb.update, manage.SubscriptionStore)
    manage.sb.select, manage.sb.update, manage.SubscriptionStore = fake.select, (lambda t, f, p: updates.append((t, f, p)) or []), FakeStore
    try:
        status, _, body = manage.handle_update({}, json.dumps({"t": TOKEN, "zones": [east_key, "nope"]}).encode())
        out = json.loads(body)
        assert status == 200 and out["ok"] and out["reactivated"] is True and out["station_count"] == len(east_ids), out
        assert saved == [("a@x.org", sorted(east_ids))]
        assert updates == [("subscribers", {"email": "eq.a@x.org"}, {"region_zone": east[0]})]
        assert manage.handle_update({}, json.dumps({"t": TOKEN, "zones": []}).encode())[0] == 400
        assert manage.handle_update({}, json.dumps({"t": "bad", "zones": [east_key]}).encode())[0] == 400
        assert manage.handle_update({}, json.dumps({"t": "11111111-2222-4333-8444-555555555555", "zones": [east_key]}).encode())[0] == 404
        assert manage.handle_update({}, b"{not json")[0] == 400
    finally:
        manage.sb.select, manage.sb.update, manage.SubscriptionStore = orig
    src = (ROOT / "app/wsgi.py").read_text()
    assert 'app.add_url_rule(path, f"manage-get:{path}", _forecast_view(handler), methods=["GET"])' in src
    assert 'app.add_url_rule(path, f"manage-post:{path}", _forecast_view(handler), methods=["POST"])' in src
    tpl = (ROOT / "app/templates/manage/page.html").read_text()
    assert 'fetch("/manage/api/update"' in tpl and 'href="/unsubscribe?t={{ token }}"' in tpl
    assert 'href="/manage?t={{ token }}"' in (ROOT / "app/templates/unsubscribe/page.html").read_text()

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
