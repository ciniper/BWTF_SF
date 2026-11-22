"""Offline inbound/review/admin-preview contract checks. SQL behavior has a separate PGlite test."""
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from features.sfpuc_updates import parser as P, routes as R, store as S

RECIPIENT = "capture@inbound.example.test"
NOW = datetime(2026, 11, 21, 16, tzinfo=timezone.utc)
MID = "c37b38cd-a0a0-4111-9000-111111111111"


def payload(**changes):
    item = {"MessageId": "<test-bulletin@example.test>", "Recipients": [RECIPIENT],
            "From": {"Address": "BeachWaterQuality@sfwater.org"}, "Subject": "Beach update",
            "SentAtDate": "Fri, 20 Nov 2026 15:00:00 -0800", "RawTextBody": "Warning remains posted at Ocean Beach.",
            "Headers": {"To": "private-reader@example.test"}, "Attachments": []}
    item.update(changes)
    return {"items": [item]}


def parse(data):
    return P.parse_payload(data, RECIPIENT, {P.EXPECTED_SENDER}, now=NOW)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SFPUC_EMAIL_ENABLED", "true")
    monkeypatch.setenv("SFPUC_EMAIL_RECIPIENT", RECIPIENT)
    monkeypatch.setenv("SFPUC_EMAIL_WEBHOOK_TOKEN", "offline-test-token-" * 3)
    monkeypatch.delenv("SFPUC_EMAIL_SENDERS", raising=False)
    from app.wsgi import app
    app.config["TESTING"] = True
    monkeypatch.setattr(R.sb, "is_configured", lambda: True)
    with app.test_client() as c:
        yield c


def authenticated_post(client, data):
    return client.post("/api/ingest/sfpuc-email", json=data,
                       headers={"Authorization": "Bearer " + "offline-test-token-" * 3})


def unlock(client):
    with client.session_transaction() as session:
        session["alerts_unlocked"] = True
        session["sfpuc_email_csrf"] = "offline-csrf"


def test_payload_matches_real_brevo_shapes_dates_and_dedup_identity():
    row = parse(payload())[0][0]
    assert row["sender_matches"] and row["sent_at"] == "2026-11-20T23:00:00+00:00"
    assert row["raw_payload"]["Headers"]["To"] == "private-reader@example.test"
    again = P.parse_payload(payload(Uuid=["another-delivery"], Recipients=[{"Address": RECIPIENT}]),
                            RECIPIENT, {P.EXPECTED_SENDER}, now=datetime(2026, 11, 22, tzinfo=timezone.utc))[0][0]
    assert (again["source_key"], again["content_hash"]) == (row["source_key"], row["content_hash"])
    changed = parse(payload(RawTextBody="Warning was removed."))[0][0]
    assert changed["source_key"] == row["source_key"] and changed["content_hash"] != row["content_hash"]


def test_forwarding_confirmation_unrecognized_sender_and_bad_dates_stay_reviewable():
    row = parse(payload(From={"Address": "forwarding-noreply@google.com"}, SentAtDate="unknown",
                        RawTextBody="Verification code: 123456"))[0][0]
    assert not row["sender_matches"] and row["sent_at"] is None and len(row["warnings"]) == 2
    assert "123456" in row["body_text"]
    for date in ["Fri, 20 Nov 2026 15:00:00", "Sun, 1 Jan 2090 12:00:00 +0000"]:
        assert parse(payload(SentAtDate=date))[0][0]["sent_at"] is None


def test_html_is_text_only_and_other_recipients_are_ignored():
    row = parse(payload(RawTextBody=None, RawHtmlBody='<p>Warning <b>remains</b>.</p><script>evil()</script><img src="https://tracker.test/pixel"><p>Baker Beach</p>'))[0][0]
    assert row["body_text"] == "Warning remains.\n\nBaker Beach"
    assert parse(payload(Recipients=["other@inbound.example.test"])) == ([], 1)
    with pytest.raises(P.InvalidPayload):
        parse(payload(Recipients=None, To=[{"Address": RECIPIENT}]))


@pytest.mark.parametrize("bad", [{}, {"items": []}, {"items": [None]}, {"items": [payload()["items"][0]] * 21},
                                 payload(RawTextBody="x" * (P.MAX_BODY + 1)), payload(Subject="bad\x00text")])
def test_malformed_payloads_are_rejected(bad):
    with pytest.raises(P.InvalidPayload):
        parse(bad)


def test_webhook_disabled_unconfigured_unauthorized_and_oversized(client, monkeypatch):
    monkeypatch.setattr(S, "capture", lambda *a: pytest.fail("Must not store unauthorized input"))
    assert client.post("/api/ingest/sfpuc-email", json=payload()).status_code == 401
    assert client.post("/api/ingest/sfpuc-email", json=payload(), headers={"Authorization": "Bearer é"}).status_code == 401
    monkeypatch.setenv("SFPUC_EMAIL_WEBHOOK_TOKEN", "too-short")
    assert authenticated_post(client, payload()).status_code == 503
    monkeypatch.setenv("SFPUC_EMAIL_ENABLED", "false")
    assert authenticated_post(client, payload()).status_code == 404
    assert client.get("/alerts/api/sfpuc-updates").status_code == 401
    unlock(client)
    assert client.get("/alerts/api/sfpuc-updates").status_code == 404


def test_batch_is_validated_before_write_and_storage_failures_are_retryable(client, monkeypatch):
    calls = []
    monkeypatch.setattr(S, "capture", lambda rows, ignored: calls.append(rows) or {"inserted": len(rows), "duplicates": 0, "ignored": ignored})
    data = payload()
    data["items"].append(payload(MessageId="second", Subject="Second bulletin")["items"][0])
    result = authenticated_post(client, data)
    assert result.status_code == 200 and result.json["inserted"] == 2 and len(calls) == 1
    invalid = copy.deepcopy(data)
    invalid["items"][1]["Subject"] = ["wrong type"]
    assert authenticated_post(client, invalid).status_code == 400 and len(calls) == 1
    def fail(*args):
        raise RuntimeError("private-reader@example.test secret database detail")
    monkeypatch.setattr(S, "capture", fail)
    result = authenticated_post(client, data)
    assert result.status_code == 503 and b"private-reader" not in result.data
    headers = {"Authorization": "Bearer " + "offline-test-token-" * 3}
    assert client.post("/api/ingest/sfpuc-email", data="x" * (R.MAX_REQUEST + 1), content_type="application/json", headers=headers).status_code == 413
    assert client.post("/api/ingest/sfpuc-email", data="not-json", content_type="application/json", headers=headers).status_code == 400
    assert client.post("/api/ingest/sfpuc-email", data="text", headers=headers).status_code == 415


def test_admin_authentication_csrf_approval_confirmation_and_stale_reviews(client, monkeypatch):
    monkeypatch.setattr(S, "inbox", lambda offset: [])
    monkeypatch.setattr(S, "recent_runs", lambda: [])
    assert b'name="passphrase"' in client.get("/alerts/sfpuc-emails").data
    assert client.post("/alerts/sfpuc-emails/review").status_code == 401
    unlock(client)
    page = client.get("/alerts/sfpuc-emails")
    assert page.status_code == 200 and b'id="sfpuc-updates"' in page.data
    assert b"sfpuc_updates.js?v=" in page.data and b"Approved messages stay in Admin" in page.data
    form = {"message_id": MID, "revision": "0", "action": "approved", "title": "Notice", "body": "Official text", "reviewer": "Coordinator"}
    assert client.post("/alerts/sfpuc-emails/review", data=form).status_code == 403
    form["csrf"] = "offline-csrf"
    assert client.post("/alerts/sfpuc-emails/review", data=form).status_code == 400
    form["verified"] = "yes"
    calls = []
    monkeypatch.setattr(S, "review", lambda *args: calls.append(args) or {"ok": True, "revision": 1})
    assert client.post("/alerts/sfpuc-emails/review", data=form).status_code == 303
    assert calls[0] == (MID, 0, "approved", "Notice", "Official text", "Coordinator")
    monkeypatch.setattr(S, "review", lambda *args: {"ok": False})
    assert client.post("/alerts/sfpuc-emails/review", data=form).status_code == 409
    monkeypatch.setattr(S, "review", lambda *args: {"ok": True})
    form.update(action="rejected", verified="")
    assert client.post("/alerts/sfpuc-emails/review", data=form).status_code == 303


def test_private_html_is_escaped_and_confirmation_can_be_read(client, monkeypatch):
    unlock(client)
    row = parse(payload(Subject="<script>bad()</script>", RawTextBody="Gmail code 123456 <img onerror=evil()>"))[0][0]
    row["id"] = MID
    monkeypatch.setattr(S, "detail", lambda selected: (row, []))
    monkeypatch.setattr(S, "inbox", lambda offset: [])
    monkeypatch.setattr(S, "recent_runs", lambda: [])
    result = client.get(f"/alerts/sfpuc-emails?message={MID}")
    assert result.status_code == 200 and "no-store" in result.headers["Cache-Control"]
    assert b"123456" in result.data and b"<script>bad()" not in result.data and b"<img onerror" not in result.data


def test_private_preview_projection_dates_pagination_and_outage(client, monkeypatch):
    row = {"id": MID, "title": "Reviewed title", "body_text": "Reviewed text", "sent_at": "2026-11-20T23:00:00+00:00",
           "received_at": "2026-11-21T16:00:00+00:00", "reviewed_at": "2026-11-21T17:00:00+00:00",
           "sender": "PRIVATE", "raw_payload": {"secret": "PRIVATE"}, "reviewed_by": "PRIVATE"}
    monkeypatch.setattr(S, "reviewed", lambda offset: [row] * 6)
    assert client.get("/api/today/sfpuc-updates").status_code == 404
    assert client.get("/alerts/api/sfpuc-updates").status_code == 401
    unlock(client)
    assert client.get("/api/today/sfpuc-updates").status_code == 404
    result = client.get("/alerts/api/sfpuc-updates")
    assert result.status_code == 200 and len(result.json["updates"]) == 5 and result.json["next_offset"] == 5
    assert "Nov 20, 2026" in result.json["updates"][0]["date"] and "PST" in result.json["updates"][0]["date"]
    assert b"PRIVATE" not in result.data and result.headers["Cache-Control"] == "private, no-store"
    assert client.get("/alerts/api/sfpuc-updates?offset=nope").status_code == 400
    row["sent_at"] = None
    assert client.get("/alerts/api/sfpuc-updates").json["updates"][0]["date_label"] == "Captured"
    monkeypatch.setattr(S, "reviewed", lambda offset: [])
    assert client.get("/alerts/api/sfpuc-updates").json["updates"] == []
    assert "PDT" in R.fmt_time("2026-07-01T18:00:00Z")
    monkeypatch.setattr(R.sb, "is_configured", lambda: False)
    assert client.get("/alerts/api/sfpuc-updates").status_code == 503


def test_today_feature_flag_does_not_fetch_emails_or_alter_board(client, monkeypatch):
    from app.landing import today_board
    from features.today import page as today
    from app.wsgi import app
    monkeypatch.setattr(S, "reviewed", lambda *a: pytest.fail("Today rendering must not read emails"))
    board = today_board([], now=NOW)
    original = copy.deepcopy(board)
    with app.test_request_context("/"):
        monkeypatch.setenv("SFPUC_EMAIL_ENABLED", "false")
        disabled = today.render_page({"board": board, "generated": "Test"})
        assert 'id="sfpuc-updates"' not in disabled and "sfpuc_updates.js" not in disabled
        monkeypatch.setenv("SFPUC_EMAIL_ENABLED", "true")
        active = today.render_page({"board": board, "generated": "Test"})
        assert 'id="sfpuc-updates"' not in active and "sfpuc_updates.js" not in active
    assert board == original


def test_eml_import_keeps_original_private():
    from scripts.import_sfpuc_email import from_eml
    original = b'From: BeachWaterQuality@sfwater.org\nTo: reader@example.test\nSubject: Test\nDate: Fri, 20 Nov 2026 15:00:00 -0800\nMessage-ID: <original@example.test>\n\nWarning remains posted.'
    row = P.parse_payload(from_eml(original, RECIPIENT), RECIPIENT, {P.EXPECTED_SENDER}, now=NOW, provider="eml_import")[0][0]
    assert row["provider"] == "eml_import" and row["body_text"] == "Warning remains posted."
    assert row["raw_payload"]["OriginalEMLBase64"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
