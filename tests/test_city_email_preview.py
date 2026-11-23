"""Public bulletin projection and its identical Admin preview; no database writes."""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from app.landing import today_board
from app.wsgi import app
from features.sfpuc_updates import routes as R, store as S, layout as L
from features.today import page as T

PAGE = "/alerts/sfpuc-emails/today-preview"
API = "/api/city-emails"
MID = "c37b38cd-a0a0-4111-9000-111111111111"
OLD = "c37b38cd-a0a0-4111-9000-000000000000"
IMAGE = "https://d31hzlhk6di2h5.cloudfront.net/example.png"


def row(**changes):
    item = dict(id=MID, subject="SF BEACH DE-POSTED — Example", body_text="Bulletin text",
                sender_matches=True, status="pending", sent_at="2026-11-21T21:05:00Z",
                received_at="2026-11-23T12:00:00Z", raw_payload={"RawHtmlBody": f'<img src="{IMAGE}" alt="SFPUC"><p>Bulletin text</p>'})
    item.update(changes)
    return item


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SFPUC_EMAIL_ENABLED", "true")
    monkeypatch.setattr(R.sb, "is_configured", lambda: True)
    monkeypatch.setattr(S, "captured", lambda *a, **k: pytest.fail("Page rendering must not fetch emails"))
    board = today_board([SimpleNamespace(station_id="4601", status="safe", has_cso=False,
                                        sample_date=datetime(2026, 11, 21))], now=datetime(2026, 11, 23))
    monkeypatch.setattr(T, "board_context", lambda *a, **k: {"board": board, "generated": "Offline fixture"})
    with app.test_client() as client:
        yield client


def unlock(client):
    with client.session_transaction() as session:
        session["alerts_unlocked"] = True


def test_today_and_admin_use_same_viewer_without_fetching_email(client):
    assert b'name="passphrase"' in client.get(PAGE).data
    for path in ("/", "/today"):
        html = client.get(path).data.decode()
        assert 'data-city-email aria-haspopup="dialog"' in html and 'id="city-email-dialog"' in html
        assert 'city_email.js?v=' in html and 'data-city-email-older' in html
        assert 'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"' in html
        assert "allow-scripts" not in html
    unlock(client)
    response = client.get(PAGE)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert b'id="city-email-dialog"' in response.data
    assert b'/_vercel/insights/script.js' not in response.data


def test_missing_sample_dates_keep_trigger_and_disabled_feature_hides_it(client, monkeypatch):
    monkeypatch.setattr(T, "board_context", lambda *a, **k: {"board": today_board([], now=datetime(2026, 11, 23)), "generated": "Offline"})
    assert b"Sample dates unavailable." in client.get("/").data
    monkeypatch.setenv("SFPUC_EMAIL_ENABLED", "false")
    assert b'data-city-email' not in client.get("/").data
    assert client.get(API).status_code == 404
    assert client.get(f"{API}/{MID}/images/0").status_code == 404


def test_public_viewer_projects_pending_capture_without_original_headers(client, monkeypatch):
    r = row(sender="PRIVATE", recipient="PRIVATE", reviewed_by="PRIVATE")
    monkeypatch.setattr(S, "captured", lambda *a, **k: [r])
    response = client.get(API)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    item = response.json["update"]
    assert item["text"] == "Bulletin text" and item["date_label"] == "Email dated"
    assert "1:05 PM PST" in item["date"]
    assert f'{API}/{MID}/images/0' in item["html"]
    assert b"PRIVATE" not in response.data and b"cloudfront" not in response.data and b"RawHtmlBody" not in response.data
    assert response.json["next_cursor"] is None
    assert client.get("/alerts/api/sfpuc-updates").status_code == 401
    assert client.get(f"/alerts/sfpuc-emails/images/{MID}/0").status_code == 401


def test_history_cursor_and_new_arrival_do_not_shift_older_page(client, monkeypatch):
    rows = [row(), row(id=OLD, received_at="2026-11-22T12:00:00Z", sent_at=None)]
    calls = []
    def captured(cursor=None):
        calls.append(cursor)
        return rows if not cursor else [rows[-1]]
    monkeypatch.setattr(S, "captured", captured)
    first = client.get(API).json
    token = first["next_cursor"]
    rows.insert(0, row(id="d37b38cd-a0a0-4111-9000-111111111111", received_at="2026-11-24T12:00:00Z"))
    older = client.get(API, query_string={"before": token}).json
    assert older["update"]["id"] == OLD and older["next_cursor"] is None
    assert older["update"]["date_label"] == "Captured"
    assert calls[-1] == ("2026-11-23T12:00:00+00:00", MID)
    for bad in ("invalid", "2026-11-23|oops", "2026-11-23T12:00:00|" + MID, "x),id.gt.0|" + MID):
        assert client.get(API, query_string={"before": bad}).status_code == 400


def test_store_uses_keyset_and_rejection_filter_without_changing_reviews(monkeypatch):
    calls = []
    def select(table, params):
        calls.append((table, params))
        return [row()] if table == "sfpuc_email_inbox" else [{"id": MID, "body_text": "Body", "raw_payload": {}}]
    monkeypatch.setattr(S.sb, "select", select)
    assert S.captured(("2026-11-23T12:00:00+00:00", MID))[0]["body_text"] == "Body"
    table, params = calls[0]
    assert table == "sfpuc_email_inbox" and params["status"] == "neq.rejected"
    assert params["order"] == "received_at.desc,id.desc" and params["subject"] == "ilike.*SF BEACH*"
    assert "received_at.lt.2026-11-23T12:00:00+00:00" in params["or"]
    assert len(calls) == 2


def test_setup_unknown_and_rejected_messages_are_excluded(client, monkeypatch):
    rows = [row(subject="Gmail forwarding confirmation"), row(sender_matches=False), row(status="rejected")]
    monkeypatch.setattr(S, "captured", lambda *a, **k: rows)
    assert client.get(API).json == {"ok": True, "update": None, "next_cursor": None}
    assert client.get(f"{API}/{MID}/images/0").status_code == 404
    forward = "Personal preface\n---------- Forwarded message ---------\nFrom: SFPUC <beachwaterquality@sfwater.org>\nSubject: SF BEACH POSTED\nTo: private@example.test\n\nBulletin"
    assert L.eligible(row(sender_matches=False, body_text=forward, subject="Fwd: SF BEACH POSTED"))
    assert not L.eligible(row(sender_matches=False, body_text=forward.replace("beachwaterquality@sfwater.org", "spoof@example.test")))


def test_image_route_rechecks_eligibility_and_returns_only_raster(client, monkeypatch):
    rows = [row()]
    monkeypatch.setattr(S, "captured", lambda *a, **k: rows)
    monkeypatch.setattr(R.presentation, "fetch_image", lambda url: (b"fixture", "image/png"))
    response = client.get(f"{API}/{MID}/images/0")
    assert response.status_code == 200 and response.content_type == "image/png"
    assert response.headers["Cache-Control"] == "no-store"
    rows[0]["status"] = "rejected"
    assert client.get(f"{API}/{MID}/images/0").status_code == 404


def test_outages_are_generic_and_empty_is_supported(client, monkeypatch):
    monkeypatch.setattr(S, "captured", lambda *a, **k: [])
    assert client.get(API).json["update"] is None
    def fail(*a, **k):
        raise RuntimeError("PRIVATE connection details")
    monkeypatch.setattr(S, "captured", fail)
    response = client.get(API)
    assert response.status_code == 503 and b"PRIVATE" not in response.data


def test_html_preserves_inline_order_and_links_removing_forwarding_and_footer():
    html = f'''<div>PRIVATE PREFACE<div class="gmail_quote"><div class="gmail_attr">---------- Forwarded message ---------<br>From: SFPUC<br>Subject: SF BEACH POSTED<br>To: PRIVATE</div><br>
      <table width="640"><tr><td><a href="https://www.sfpuc.gov/"><img src="{IMAGE}" alt="SFPUC"></a><p>BULLETIN</p>
      <a href="https://www.sfpuc.gov/programs/ocean-and-beach-monitoring">Program</a><img src="{IMAGE}?social" width="32" alt="Social">
      <p><a href="https://t.e2ma.net/optout/PRIVATE">Manage</a> your preferences</p><p>This email was sent to PRIVATE@example.test.</p>
      </td></tr></table></div></div>'''
    r = row(body_text="---------- Forwarded message ---------\nFrom: SFPUC\nSubject: SF BEACH POSTED\n\nBULLETIN", raw_payload={"RawHtmlBody": html})
    output = L.document(r, lambda i: f"/api/city-emails/{MID}/images/{i}")
    assert output.index("images/0") < output.index("BULLETIN") < output.index("images/1")
    assert 'href="https://www.sfpuc.gov/programs/ocean-and-beach-monitoring"' in output
    assert "PRIVATE" not in output and "gmail_attr" not in output and "optout" not in output
    assert 'target="_blank"' in output and 'rel="noopener noreferrer"' in output
    assert 'width="640"' in output


def test_active_content_tracking_and_remote_resources_are_removed():
    assert L.safe_link("tel:(415)%20242-2214") == "tel:4152422214"
    assert L.safe_link("tel:1234567;phone-context=evil") is None
    attack = f'''<script>BAD_SCRIPT</script><style>BAD_STYLE</style><svg><script>BAD_SVG</script></svg>
      <form action="https://evil.test"><input name="secret"></form><iframe src="https://evil.test"></iframe>
      <base href="https://evil.test"><meta http-equiv="refresh" content="0;url=https://evil.test">
      <div style="display:none">HIDDEN</div><img src="https://evil.test/pixel"><img src="{IMAGE}?pixel" width="1">
      <img src="{IMAGE}" onerror="BAD_EVENT" srcset="https://evil.test/a 2x">
      <a href="javascript:BAD_LINK()">Bad</a><a href="/alerts/logout">Local</a>
      <p style="position:fixed;background-image:url(https://evil.test);width:9000px;color:red" onclick="BAD_CLICK">GOOD</p>'''
    output = L.document(row(raw_payload={"RawHtmlBody": attack}), lambda i: f"/api/city-emails/{MID}/images/{i}")
    for forbidden in ("BAD_", "evil.test", "HIDDEN", "srcset", "javascript:", "position:fixed", "9000px", "pixel", "/alerts/logout"):
        assert forbidden not in output
    assert "GOOD" in output and "color:red" in output.replace(" ", "")
    assert "default-src 'none'" in output and "form-action 'none'" in output


def test_unknown_forward_html_falls_back_without_leaking_envelope():
    body = "Preface\n---------- Forwarded message ---------\nFrom: City\nSubject: Notice\nTo: private@example.test\n\nPublic bulletin\nManage your preferences\nPRIVATE FOOTER"
    r = row(subject="Fwd: SF BEACH POSTED", body_text=body, raw_payload={"RawHtmlBody": "<p>Unknown forwarding layout</p>"})
    assert L.document(r, lambda i: "unused") is None
    assert L.clean_text(r["subject"], body)["body"] == "Public bulletin"
    assert L.clean_text(r["subject"], body)["title"] == "SF BEACH POSTED"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
