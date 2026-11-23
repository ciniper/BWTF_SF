"""Private Today prototype: no public email read or email fetch during page load."""
import copy
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from app.landing import today_board
from app.wsgi import app
from features.sfpuc_updates import routes as R, store as S
from features.today import page as T

PAGE = "/alerts/sfpuc-emails/today-preview"
API = "/alerts/api/sfpuc-updates/latest"
MID = "c37b38cd-a0a0-4111-9000-111111111111"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SFPUC_EMAIL_ENABLED", "true")
    monkeypatch.setattr(R.sb, "is_configured", lambda: True)
    monkeypatch.setattr(S, "image_payloads", lambda ids: {})
    monkeypatch.setattr(S, "reviewed", lambda *a, **k: pytest.fail("Page rendering must not fetch emails"))
    board = today_board([SimpleNamespace(station_id="4601", status="safe", has_cso=False,
                                        sample_date=datetime(2026, 11, 21))], now=datetime(2026, 11, 23))
    monkeypatch.setattr(T, "board_context", lambda *a, **k: {"board": board, "generated": "Offline fixture"})
    with app.test_client() as client:
        yield client


def unlock(client):
    with client.session_transaction() as session:
        session["alerts_unlocked"] = True


def row(**changes):
    item = {"id": MID, "title": "Reviewed bulletin", "body_text": "The reviewed bulletin text.",
            "sent_at": "2026-11-21T21:05:00Z", "received_at": "2026-11-23T12:00:00Z",
            "sender": "PRIVATE", "recipients": ["PRIVATE"], "reviewed_by": "PRIVATE", "raw_payload": {"PRIVATE": True}}
    item.update(changes)
    return item


def test_preview_page_and_latest_api_require_admin_without_loading_emails(client):
    response = client.get(PAGE)
    assert response.status_code == 200 and b'name="passphrase"' in response.data
    assert b'id="city-email-dialog"' not in response.data
    assert client.get(API).status_code == 401


def test_authenticated_preview_reuses_today_without_fetching_an_email(client):
    unlock(client)
    response = client.get(PAGE)
    html = response.data.decode()
    assert response.status_code == 200 and response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert 'id="today-h"' in html and 'id="layers"' in html and 'data-sample-station=' in html
    assert 'data-city-email aria-haspopup="dialog"' in html and 'aria-controls="city-email-dialog"' in html
    assert '<dialog class="city-email-dialog" id="city-email-dialog"' in html
    assert '<dialog open' not in html and 'data-city-email-message hidden' in html
    assert "Samples were collected 2 days ago (Nov 21)." in html
    assert 'city_email.js?v=' in html and 'city_email.css?v=' in html
    assert 'aria-label="Admin preview"' in html and 'noindex, nofollow' in html
    assert 'http-equiv="refresh"' not in html and 'rel="canonical"' not in html
    assert '/_vercel/insights/script.js' not in html


def test_public_today_never_opts_in_even_for_admin_or_query_parameters(client):
    for logged_in in (False, True):
        if logged_in:
            unlock(client)
        for path in ("/", "/today", "/?city_email_preview=true"):
            html = client.get(path).data.decode()
            assert 'data-city-email' not in html and 'city_email.js' not in html and 'city_email.css' not in html
            assert '<dt>City samples</dt>' in html
            assert 'http-equiv="refresh" content="300"' in html
    assert client.get("/api/today/sfpuc-updates/latest").status_code == 404


def test_missing_sample_dates_keep_the_preview_trigger_without_inventing_dates(client, monkeypatch):
    unlock(client)
    board = today_board([], now=datetime(2026, 11, 23))
    original = copy.deepcopy(board)
    monkeypatch.setattr(T, "board_context", lambda *a, **k: {"board": board, "generated": "Offline"})
    html = client.get(PAGE).data.decode()
    assert html.count('data-city-email aria-haspopup="dialog"') == 1
    assert "Sample dates unavailable." in html and "Samples were collected" not in html
    assert board == original


def test_feature_disabled_hides_private_preview_and_api(client, monkeypatch):
    unlock(client)
    monkeypatch.setenv("SFPUC_EMAIL_ENABLED", "false")
    assert client.get(PAGE).status_code == 404 and client.get(API).status_code == 404


def test_latest_projects_one_reviewed_message_without_private_source_fields(client, monkeypatch):
    unlock(client)
    calls = []
    monkeypatch.setattr(S, "reviewed", lambda offset, *, limit: calls.append((offset, limit)) or [row()])
    monkeypatch.setattr(S, "image_payloads", lambda ids: {MID: {"RawHtmlBody": '<img src="https://d31hzlhk6di2h5.cloudfront.net/example.png" alt="Beach">'}})
    response = client.get(API)
    assert response.status_code == 200 and calls == [(0, 1)]
    item = response.json["update"]
    assert item["text"] == "The reviewed bulletin text." and item["date_label"] == "Email dated"
    assert "Nov 21, 2026" in item["date"] and "1:05 PM PST" in item["date"]
    assert item["images"][0]["src"] == f"/alerts/sfpuc-emails/images/{MID}/0"
    assert b"PRIVATE" not in response.data and b"cloudfront" not in response.data
    assert response.headers["Cache-Control"] == "private, no-store"


def test_latest_uses_reviewed_view_ordered_by_email_date_and_one_row(monkeypatch):
    calls = []
    monkeypatch.setattr(S.sb, "select", lambda table, params: calls.append((table, params)) or [])
    assert S.reviewed(0, limit=1) == []
    table, params = calls[0]
    assert table == "sfpuc_email_reviewed" and params["limit"] == "1" and params["offset"] == "0"
    assert params["order"] == "sort_at.desc,id.desc" and "raw_payload" not in params["select"]


def test_latest_reflects_edits_and_withdrawal_on_next_read_and_labels_missing_dates(client, monkeypatch):
    unlock(client)
    rows = [row(sent_at=None)]
    monkeypatch.setattr(S, "reviewed", lambda *a, **k: rows)
    response = client.get(API)
    assert response.json["update"]["date_label"] == "Captured" and "Nov 23, 2026" in response.json["update"]["date"]
    rows[0]["body_text"] = "Corrected review"
    assert client.get(API).json["update"]["text"] == "Corrected review"
    rows.clear()
    assert client.get(API).json == {"ok": True, "update": None}


def test_latest_outages_are_generic_and_empty_does_not_query_originals(client, monkeypatch):
    unlock(client)
    monkeypatch.setattr(S, "reviewed", lambda *a, **k: [])
    monkeypatch.setattr(S, "image_payloads", lambda ids: pytest.fail("No original is needed when nothing is approved"))
    assert client.get(API).json == {"ok": True, "update": None}
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE database detail")
    monkeypatch.setattr(S, "reviewed", fail)
    response = client.get(API)
    assert response.status_code == 503 and b"PRIVATE" not in response.data
    monkeypatch.setattr(R.sb, "is_configured", lambda: False)
    assert client.get(API).status_code == 503


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
