"""Offline inbound/review/admin-preview contract checks. SQL behavior has a separate PGlite test."""
import copy
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from features.sfpuc_updates import parser as P, routes as R, store as S
from features.sfpuc_updates import presentation as V

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
    monkeypatch.setattr(S, "image_payloads", lambda ids: {})
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
    assert page.status_code == 200 and b'aria-label="Email sources"' in page.data
    assert b"No approval needed" in page.data and b"Approve for preview" not in page.data
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


IMAGE = "https://d31hzlhk6di2h5.cloudfront.net/email/banner.png"
FORWARDED = ("My note to the coordinator\n\n---------- Forwarded message ---------\n"
             "From: Beach Water Quality <source@example.test>\n"
             "Date: Sat, Nov 21, 2026 at 1:05 PM\nSubject: Beach update\n"
             "To: reader@example.test\n\nBEACH MONITORING UPDATE\n"
             "STATUS: DE-POSTED (11/21/2026)\nOther beaches remain posted.\n\n"
             "About the monitoring program\nPlease check the official bulletin.")


def test_images_only_use_reviewed_origins_and_skip_pixels_and_sender_markup():
    html = (f'<img src="{IMAGE}" width="640" alt="Beach &amp; ocean">'
            f'<img src="{IMAGE}#duplicate">'
            f'<img src="{IMAGE}?pixel" width="1" height="1">'
            f'<img src="{IMAGE}?hidden" style="height: 1px !important">'
            f'<img src="{IMAGE}?none" style="display:none">'
            f'<script><img src="{IMAGE}?script"></script>'
            f'<img src="{IMAGE}?icon" width="32" height="32" alt="Social icon">'
            '<img src="https://tracker.example.test/pixel"><img src="cid:photo">'
            '<img src="data:image/png;base64,example"><svg><img src="bad"></svg>')
    result = V.images({"RawHtmlBody": html})
    assert result == [{"url": IMAGE, "alt": "Beach & ocean", "small": False},
                      {"url": IMAGE + "?icon", "alt": "Social icon", "small": True}]
    assert V.images(None) == [] and V.images({"RawHtmlBody": "x" * 100_001}) == []
    assert len(V.images({"RawHtmlBody": "".join(f'<img src="{IMAGE}?{i}">' for i in range(30))})) == V.MAX_IMAGES


@pytest.mark.parametrize("url", ["http://d31hzlhk6di2h5.cloudfront.net/a.png", "https://127.0.0.1/a",
    "https://169.254.169.254/metadata", "https://d31hzlhk6di2h5.cloudfront.net.evil.test/a",
    "https://user@d31hzlhk6di2h5.cloudfront.net/a", "https://d31hzlhk6di2h5.cloudfront.net:8080/a",
    "//d31hzlhk6di2h5.cloudfront.net/a", "javascript:alert(1)", IMAGE + "\n", IMAGE + "\\extra"])
def test_untrusted_image_destinations_are_rejected(url):
    assert V.image_url(url) is None


class ImageResponse:
    def __init__(self, data=b"\x89PNG\r\n\x1a\nexample", status=200, mime="image/png", length=None):
        self.data, self.status_code = data, status
        self.headers = {"Content-Type": mime}
        if length is not None:
            self.headers["Content-Length"] = str(length)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def iter_content(self, size):
        yield self.data


def install_image_response(monkeypatch, response):
    calls = []
    class Session:
        trust_env = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, **kwargs):
            calls.append((url, kwargs, self.trust_env))
            return response
    monkeypatch.setattr(V.requests, "Session", Session)
    return calls


def test_image_download_is_bounded_raster_only_and_does_not_follow_redirects(monkeypatch):
    calls = install_image_response(monkeypatch, ImageResponse())
    assert V.fetch_image(IMAGE)[1] == "image/png"
    assert calls[0][1]["allow_redirects"] is False and calls[0][1]["stream"] is True
    assert calls[0][2] is False and "Authorization" not in calls[0][1]["headers"]
    for response in [ImageResponse(status=302), ImageResponse(mime="image/svg+xml"),
                     ImageResponse(data=b"<html>not a PNG</html>"),
                     ImageResponse(length=V.MAX_IMAGE_BYTES + 1),
                     ImageResponse(data=b"x" * (V.MAX_IMAGE_BYTES + 1))]:
        calls = install_image_response(monkeypatch, response)
        with pytest.raises(ValueError):
            V.fetch_image(IMAGE)
        assert len(calls) == 1
    calls = install_image_response(monkeypatch, ImageResponse())
    with pytest.raises(ValueError):
        V.fetch_image("https://127.0.0.1/private")
    assert not calls


def test_image_download_stops_a_slow_stream(monkeypatch):
    install_image_response(monkeypatch, ImageResponse())
    ticks = iter([0, 11])
    monkeypatch.setattr(V.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError, match="limit"):
        V.fetch_image(IMAGE)


def test_private_images_are_gated_eager_and_never_render_source_html(client, monkeypatch):
    row = parse(payload(Subject="SF BEACH POSTED", RawHtmlBody=f'<img src="{IMAGE}" width="640" alt="&lt;script&gt;bad&lt;/script&gt;" onerror="evil()">'))[0][0]
    row["id"] = MID
    monkeypatch.setattr(S, "image_payloads", lambda ids: {MID: row["raw_payload"]})
    monkeypatch.setattr(S, "detail", lambda mid: (row, []))
    monkeypatch.setattr(S, "inbox", lambda offset: [])
    monkeypatch.setattr(S, "recent_runs", lambda: [])
    path = f"/alerts/sfpuc-emails/images/{MID}/0"
    assert client.get(path).status_code == 401
    unlock(client)
    page = client.get(f"/alerts/sfpuc-emails?message={MID}").data
    assert f'src="{path}"' in html.unescape(page.decode()) and b'title="SFPUC bulletin as shown on Today"' in page
    assert b'onerror="evil()"' not in page and b'<script>bad</script>' not in page
    assert b"Hide from Today" in page and b"Remove forwarding text" not in page
    monkeypatch.setattr(V, "fetch_image", lambda url: (b"image-bytes", "image/png"))
    response = client.get(path)
    assert response.status_code == 200 and response.data == b"image-bytes"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response.headers["Content-Security-Policy"]
    assert client.get(path[:-1] + "12").status_code == 404
    monkeypatch.setenv("SFPUC_EMAIL_ENABLED", "false")
    assert client.get(path).status_code == 404


def test_preview_includes_only_private_image_routes(client, monkeypatch):
    unlock(client)
    row = {"id": MID, "title": "Notice", "body_text": "Reviewed text", "sent_at": None, "received_at": NOW.isoformat()}
    monkeypatch.setattr(S, "reviewed", lambda offset: [row])
    monkeypatch.setattr(S, "image_payloads", lambda ids: {MID: {"RawHtmlBody": f'<img src="{IMAGE}" alt="Beach">'}})
    response = client.get("/alerts/api/sfpuc-updates")
    assert response.status_code == 200
    assert response.json["updates"][0]["images"][0]["src"] == f"/alerts/sfpuc-emails/images/{MID}/0"
    assert b"cloudfront" not in response.data and b"RawHtmlBody" not in response.data


def test_forwarding_cleanup_preserves_the_full_bulletin_and_handles_nested_quotes():
    result = V.clean_forward("Fwd: Beach update", FORWARDED)
    assert result["removed"] == 1 and result["title"] == "Beach update"
    assert result["body"] == FORWARDED[FORWARDED.index("BEACH MONITORING UPDATE"):]
    assert "STATUS: DE-POSTED (11/21/2026)" in result["body"]
    assert "Other beaches remain posted." in result["body"] and "About the monitoring program" in result["body"]
    nested = "---------- Forwarded message ---------\nFrom: second@example.test\nSubject: Fwd: Notice\n\n" + "\n".join("> " + line for line in FORWARDED.splitlines())
    result = V.clean_forward("Fw: Fwd: Beach update", nested)
    assert result["removed"] == 2 and result["title"] == "Beach update"
    assert "reader@example.test" not in result["body"] and "second@example.test" not in result["body"]


def test_cleanup_preserves_greater_than_thresholds_inside_the_bulletin():
    body = FORWARDED + "\n> 104 CFU/100mL\n> Quoted official guidance"
    assert V.clean_forward("Fwd: Notice", body)["body"].endswith("> 104 CFU/100mL\n> Quoted official guidance")
    quoted = "\n".join("> " + line for line in body.splitlines())
    assert V.clean_forward("Fwd: Notice", quoted)["body"].endswith("> 104 CFU/100mL\n> Quoted official guidance")


@pytest.mark.parametrize("body", ["Unforwarded bulletin\nFrom: an official source", "Gmail verification code: 123456",
    "---------- Forwarded message ---------\nSubject: A note\n\nKeep this text.",
    "---------- Forwarded message ---------\nFrom: source@example.test\nSubject: Empty\n\n"])
def test_cleanup_does_not_guess_or_erase_unrecognized_and_empty_messages(body):
    assert V.clean_forward("Original title", body) == {"title": "Original title", "body": body, "removed": 0}


def test_cleanup_requires_auth_and_csrf_and_does_not_save_a_review(client, monkeypatch):
    monkeypatch.setattr(S, "review", lambda *args: pytest.fail("Cleanup must not save"))
    path = "/alerts/sfpuc-emails/clean-forward"
    draft = {"title": "Fwd: Notice", "body": FORWARDED}
    assert client.post(path, json=draft).status_code == 401
    unlock(client)
    assert client.post(path, json=draft).status_code == 403
    response = client.post(path, json=draft, headers={"X-CSRF-Token": "offline-csrf"})
    assert response.status_code == 200 and response.json["removed"] == 1
    assert response.json["title"] == "Notice" and "no-store" in response.headers["Cache-Control"]
    assert client.post(path, json={"title": "x", "body": "x" * 30_001}, headers={"X-CSRF-Token": "offline-csrf"}).status_code == 400


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
