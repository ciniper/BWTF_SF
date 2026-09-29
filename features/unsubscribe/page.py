"""Public one-click unsubscribe (migration 017).

Every alert email carries ``<site_url>/unsubscribe?t=<token>`` in its footer
and in RFC 8058 ``List-Unsubscribe`` headers. The token is a per-subscriber
uuid on the subscribers row; knowing it proves you received the mail, so no
login is needed and nothing else is accepted.

  GET  /unsubscribe?t=…   a page: who this is for, one button (a form POST).
                          Never unsubscribes on its own — link scanners and
                          previewers follow GETs.
  POST /unsubscribe?t=…   the button, or a mail client's one-click POST
                          (body ``List-Unsubscribe=One-Click``): sets
                          active = false, stamps unsubscribed_at, refreshes
                          the JSON warm backup, shows "done". Idempotent.

Re-subscribing on /signup reactivates the same row (the store's upsert sets
active = true), so the token stays valid for life.

Each route handler returns ``(status, content_type, body_bytes)``, like signup.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from flask import render_template

from shared import supabase as sb

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _token(query) -> str | None:
    t = (query.get("t") or [""])[0].strip()
    return t if _UUID_RE.match(t) else None


def _lookup(token: str) -> dict | None:
    rows = sb.select("subscribers", {"select": "id,email,active,station_ids,unsubscribed_at",
                                     "unsubscribe_token": f"eq.{token}", "limit": "1"})
    return rows[0] if rows else None


def _page(state: str, status: int = 200, **ctx):
    html = render_template("unsubscribe/page.html", state=state, **ctx)
    return status, "text/html; charset=utf-8", html.encode()


def handle_page(query, body):
    token = _token(query)
    if not token:
        return _page("missing", 400)
    try:
        row = _lookup(token)
    except Exception as exc:  # noqa: BLE001 — Supabase down: say so, never guess
        print(f"[unsubscribe] lookup failed: {exc}")     # server log only; the page never shows internals
        return _page("error", 503)
    if row is None:
        return _page("unknown", 404)
    if not row.get("active", True):
        return _page("already", email=row.get("email", ""))
    return _page("ask", email=row.get("email", ""), token=token,
                 sites=len(row.get("station_ids") or []))


def handle_unsubscribe(query, body):
    token = _token(query)
    if not token:
        return _page("missing", 400)
    try:
        row = _lookup(token)
        if row is None:
            return _page("unknown", 404)
        if row.get("active", True):
            sb.update("subscribers", {"unsubscribe_token": f"eq.{token}"},
                      {"active": False, "unsubscribed_at": datetime.now(timezone.utc).isoformat()})
            _refresh_backup()
    except Exception as exc:  # noqa: BLE001
        print(f"[unsubscribe] update failed: {exc}")
        return _page("error", 503)
    return _page("done", email=row.get("email", ""))


def _refresh_backup() -> None:
    """The JSON warm backup mirrors active subscribers after every store write;
    keep it honest after an unsubscribe too. Best effort."""
    try:
        from features.alerts.subscriptions import SubscriptionStore
        SubscriptionStore()._mirror_to_json()
    except Exception:  # noqa: BLE001
        pass


GET_ROUTES = {"/unsubscribe": handle_page}
POST_ROUTES = {"/unsubscribe": handle_unsubscribe}
