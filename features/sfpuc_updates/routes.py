"""Opt-in capture, private coordinator review, and public bulletin projections."""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from flask import Response, abort, jsonify, redirect, render_template, request, session, url_for
import requests
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from features.sfpuc_updates import layout, parser, presentation, store
from shared import supabase as sb

LOG = logging.getLogger(__name__)
MAX_REQUEST = 512 * 1024
PACIFIC = ZoneInfo("America/Los_Angeles")


def enabled():
    return os.environ.get("SFPUC_EMAIL_ENABLED", "").lower() in {"1", "true", "yes"}


def configuration():
    recipient = os.environ.get("SFPUC_EMAIL_RECIPIENT", "").strip().casefold()
    senders = {s.strip().casefold() for s in os.environ.get("SFPUC_EMAIL_SENDERS", parser.EXPECTED_SENDER).split(",") if s.strip()}
    token = os.environ.get("SFPUC_EMAIL_WEBHOOK_TOKEN", "")
    try:
        parser.address(recipient)
        for sender in senders:
            parser.address(sender)
    except parser.InvalidPayload:
        return None
    return (recipient, senders, token) if senders and len(token) >= 32 else None


def fmt_time(value):
    if not value:
        return "Unknown"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(PACIFIC).strftime("%b %-d, %Y · %-I:%M %p %Z")


def offset_arg():
    try:
        return max(0, min(1000, int(request.args.get("offset", "0"))))
    except ValueError:
        abort(400)


def csrf_token():
    if "sfpuc_email_csrf" not in session:
        session["sfpuc_email_csrf"] = secrets.token_urlsafe(32)
    return session["sfpuc_email_csrf"]


def _failure():
    # Never log Supabase exception text: it can contain email content or addresses.
    LOG.warning("SFPUC email storage request failed")
    return jsonify(ok=False, error="Email storage is temporarily unavailable. Please retry."), 503


def webhook():
    if not enabled():
        abort(404)
    config = configuration()
    if not config or not sb.is_configured():
        return jsonify(ok=False, error="Email capture is not configured."), 503
    recipient, senders, token = config
    supplied = request.headers.get("Authorization", "")
    if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        return jsonify(ok=False, error="Unauthorized."), 401
    # Per-route cap: do not change the limits on existing app routes.
    if request.content_length is not None and request.content_length > MAX_REQUEST:
        return jsonify(ok=False, error="Message batch is too large."), 413
    if not request.is_json:
        return jsonify(ok=False, error="Expected application/json."), 415
    try:
        raw = request.stream.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise RequestEntityTooLarge()
        payload = json.loads(raw)
        rows, ignored = parser.parse_payload(payload, recipient, senders)
    except RequestEntityTooLarge:
        return jsonify(ok=False, error="Message batch is too large."), 413
    except (ValueError, BadRequest, RecursionError) as exc:
        error = str(exc) if isinstance(exc, parser.InvalidPayload) else "Invalid JSON payload."
        return jsonify(ok=False, error=error), 400
    try:
        result = store.capture(rows, ignored)
    except Exception:
        return _failure()
    return jsonify(ok=True, **result)


def reviewed_updates():
    if not enabled():
        abort(404)
    if not sb.is_configured():
        return _failure()
    offset = offset_arg()
    try:
        rows = store.reviewed(offset)
        payloads = store.image_payloads([str(UUID(row["id"])) for row in rows[:5]])
        items = [reviewed_item(row, payloads.get(row["id"])) for row in rows[:5]]
    except Exception:
        return _failure()
    response = jsonify(ok=True, updates=items, next_offset=offset + 5 if len(rows) > 5 and offset < 1000 else None)
    # Withdrawal should be reflected on the next read, not retained in shared caches.
    response.headers["Cache-Control"] = "private, no-store"
    return response


def reviewed_item(row, payload):
    """Only reviewed fields and private image routes leave the server."""
    return {"id": row["id"], "title": row["title"], "text": row["body_text"],
            "date": fmt_time(row.get("sent_at") or row["received_at"]),
            "date_label": "Email dated" if row.get("sent_at") else "Captured",
            "source": "SFPUC email", "images": image_descriptors(row["id"], payload)}


def captured_update():
    if not enabled():
        abort(404)
    if not sb.is_configured():
        return _failure()
    cursor = None
    if request.args.get("before"):
        try:
            date, mid = request.args["before"].split("|", 1)
            date = datetime.fromisoformat(date.replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError()
            cursor = (date.isoformat(), str(UUID(mid)))
        except ValueError:
            abort(400)
    try:
        candidates, rows = [], []
        for _ in range(4):
            rows = store.captured(cursor)
            candidates.extend(row for row in rows if layout.eligible(row))
            if len(candidates) >= 2 or len(rows) < 25:
                break
            cursor = (rows[-1]["received_at"], rows[-1]["id"])
        item, next_cursor = None, None
        if candidates:
            row = candidates[0]
            mid = str(UUID(row["id"]))
            clean = layout.clean_text(row["subject"], row["body_text"])
            item = {"id": mid, "title": clean["title"], "text": clean["body"],
                    "date": fmt_time(row.get("sent_at") or row["received_at"]),
                    "date_label": "Email dated" if row.get("sent_at") else "Captured",
                    "html": layout.document(row, lambda index: url_for("city-email-image", message_id=mid, index=index))}
            if len(candidates) > 1 or len(rows) == 25:
                next_cursor = row["received_at"] + "|" + mid
        elif len(rows) == 25:
            next_cursor = rows[-1]["received_at"] + "|" + rows[-1]["id"]
    except Exception:
        return _failure()
    return jsonify(ok=True, update=item, next_cursor=next_cursor), 200, {"Cache-Control": "no-store"}


def captured_image(message_id, index):
    if not enabled() or index >= presentation.MAX_IMAGES:
        abort(404)
    try:
        rows = store.captured(message_id=str(message_id))
        row = rows[0] if rows and layout.eligible(rows[0]) else None
        # Reapply public eligibility on every image request, including rejection.
        images = presentation.images(row["raw_payload"]) if row else []
        if index >= len(images):
            abort(404)
        image_path = lambda i: url_for("city-email-image", message_id=message_id, index=i)
        html = layout.document(row, image_path)
        if not html or f'src="{image_path(index)}"' not in html:
            abort(404)  # Never serve an image removed with a private wrapper/footer.
        data, mime = presentation.fetch_image(images[index]["url"])
    except (ValueError, requests.RequestException):
        return "Image unavailable.", 502, {"Cache-Control": "no-store"}
    except Exception as exc:
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            raise
        return _failure()
    return Response(data, content_type=mime, headers={"Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
                    "Referrer-Policy": "no-referrer"})


def today_preview():
    if not enabled():
        abort(404)
    # Reuse the actual Today board; only this authenticated route opts into the UI.
    from features.today import page as today
    status, content_type, body = today.handle_page({}, b"", city_email_preview=True)
    return Response(body, status=status, content_type=content_type,
                    headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"})


def admin():
    if not enabled():
        abort(404)
    offset = offset_arg()
    message, revisions, rows, runs, error = None, [], [], [], None
    selected = request.args.get("message")
    if selected:
        try:
            selected = str(UUID(selected))
        except ValueError:
            abort(400)
    if not sb.is_configured():
        error = "Email storage is not configured in this environment."
    else:
        try:
            rows, runs = store.inbox(offset), store.recent_runs()
            selected = selected or (rows[0]["id"] if rows else None)
            if selected:
                message, revisions = store.detail(selected)
                if message is None:
                    abort(404)
        except Exception as exc:
            from werkzeug.exceptions import HTTPException
            if isinstance(exc, HTTPException):
                raise
            error = "Email storage is unavailable. Check the feature's migration and connection."
    bulletin = bool(message and layout.eligible(dict(message, status=None)))
    hidden = bool(revisions and revisions[0]["status"] == "rejected")
    document = (layout.document(message, lambda index: url_for("sfpuc-email-image", message_id=message["id"], index=index))
                if bulletin else None)
    clean = layout.clean_text(message["subject"], message["body_text"]) if bulletin else None
    response = render_template("sfpuc_updates/admin.html", rows=rows, message=message, revisions=revisions,
                               runs=runs, offset=offset, error=error, ready=bool(configuration()),
                               csrf=csrf_token(), fmt_time=fmt_time, expected_sender=parser.EXPECTED_SENDER,
                               bulletin=bulletin, hidden=hidden, document=document, clean=clean)
    return response, 503 if error else 200, {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"}


def set_visibility():
    """Append a Hide/Restore decision; never edit the archived bulletin."""
    if not enabled():
        abort(404)
    if request.content_length is None or request.content_length > 4096:
        abort(413)
    expected = session.get("sfpuc_email_csrf", "")
    supplied = request.form.get("csrf", "")
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        abort(403)
    try:
        mid = str(UUID(request.form.get("message_id", "")))
        revision = int(request.form.get("revision", "-1"))
        action = request.form.get("action")
        if revision < 0 or action not in {"hide", "restore"}:
            raise ValueError()
    except ValueError:
        abort(400)
    try:
        message, revisions = store.detail(mid)
        if message is None:
            abort(404)
        # Restore is not an override for the public feed's eligibility rules.
        if not layout.eligible(dict(message, status=None)):
            return "Only matching SFPUC beach bulletins can be shown on Today.", 400
        latest = revisions[0] if revisions else None
        clean = layout.clean_text(message["subject"], message["body_text"])
        # Keep legacy edited text where present. Today always reads the original.
        title = (latest.get("title") if latest else None) or clean["title"]
        body = (latest.get("body_text") if latest else None) or clean["body"] or title
        result = store.review(mid, revision, "rejected" if action == "hide" else "approved",
                              title[:300], body[:30000], "Admin dashboard · " + action)
    except Exception as exc:
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            raise
        return _failure()
    if not result.get("ok"):
        return "This message changed. Reload the email and try again.", 409
    return redirect(url_for("sfpuc-email-admin", message=mid), 303)


def image_descriptors(message_id, payload):
    return [{"src": url_for("sfpuc-email-image", message_id=message_id, index=index),
             "alt": image["alt"], "small": image["small"]}
            for index, image in enumerate(presentation.images(payload))]


def email_image(message_id, index):
    if not enabled():
        abort(404)
    if index >= presentation.MAX_IMAGES:
        abort(404)
    message_id = str(message_id)
    try:
        payload = store.image_payloads([message_id]).get(message_id)
    except Exception:
        return _failure()
    images = presentation.images(payload)
    if index >= len(images):
        abort(404)
    try:
        data, mime = presentation.fetch_image(images[index]["url"])
    except (ValueError, requests.RequestException):
        return "Image unavailable.", 502, {"Cache-Control": "private, no-store"}
    return Response(data, content_type=mime, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer"})


def clean_forward():
    if not enabled():
        abort(404)
    if request.content_length is None or request.content_length > 128 * 1024:
        abort(413)
    expected = session.get("sfpuc_email_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        abort(403)
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError()
        title = parser.string(data.get("title"), "title", 300)
        body = parser.string(data.get("body"), "body", 30_000)
    except (ValueError, BadRequest):
        return jsonify(ok=False, error="Invalid draft."), 400
    return jsonify(ok=True, **presentation.clean_forward(title, body)), 200, {"Cache-Control": "private, no-store"}


def save_review():
    if not enabled():
        abort(404)
    if request.content_length is None or request.content_length > 128 * 1024:
        abort(413)
    token = request.form.get("csrf", "")
    expected = session.get("sfpuc_email_csrf", "")
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        abort(403)
    try:
        message_id = str(UUID(request.form.get("message_id", "")))
        revision = int(request.form.get("revision", "-1"))
        action = request.form.get("action")
        if action not in {"approved", "rejected"} or revision < 0:
            raise ValueError()
        title = parser.string(request.form.get("title"), "title", 300)
        body = parser.string(request.form.get("body"), "reviewed text", 30_000)
        reviewer = parser.string(request.form.get("reviewer"), "reviewer", 100)
        if not reviewer or (action == "approved" and (not title or not body or request.form.get("verified") != "yes")):
            raise ValueError()
    except ValueError:
        return "Check the fields and confirm the source and reviewed text before approving.", 400
    try:
        result = store.review(message_id, revision, action, title, body, reviewer)
    except Exception:
        return _failure()
    if not result.get("ok"):
        return "This message changed while you reviewed it. Reload the review page and try again.", 409
    return redirect(url_for("sfpuc-email-admin", message=message_id), 303)


def register(app, gated_page, gated_api):
    # Jinja globals are available to imported macros without changing every page.
    app.jinja_env.globals["sfpuc_email_enabled"] = enabled
    app.add_url_rule("/api/ingest/sfpuc-email", "sfpuc-email-webhook", webhook, methods=["POST"])
    app.add_url_rule("/alerts/api/sfpuc-updates", "sfpuc-email-preview", gated_api(reviewed_updates), methods=["GET"])
    app.add_url_rule("/api/city-emails", "city-emails", captured_update, methods=["GET"])
    app.add_url_rule("/api/city-emails/<uuid:message_id>/images/<int:index>", "city-email-image", captured_image, methods=["GET"])
    app.add_url_rule("/alerts/sfpuc-emails", "sfpuc-email-admin", gated_page(admin, "/alerts/sfpuc-emails"), methods=["GET"])
    app.add_url_rule("/alerts/sfpuc-emails/visibility", "sfpuc-email-visibility", gated_api(set_visibility), methods=["POST"])
    app.add_url_rule("/alerts/sfpuc-emails/today-preview", "sfpuc-email-today-preview",
                     gated_page(today_preview, "/alerts/sfpuc-emails/today-preview"), methods=["GET"])
    app.add_url_rule("/alerts/sfpuc-emails/review", "sfpuc-email-review", gated_api(save_review), methods=["POST"])
    app.add_url_rule("/alerts/sfpuc-emails/clean-forward", "sfpuc-email-clean-forward", gated_api(clean_forward), methods=["POST"])
    app.add_url_rule("/alerts/sfpuc-emails/images/<uuid:message_id>/<int:index>", "sfpuc-email-image", gated_api(email_image), methods=["GET"])
