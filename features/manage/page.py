"""Public "change your sites" page (migration 017's token, same credential as
/unsubscribe).

Every alert email links ``<site_url>/manage?t=<token>`` next to Unsubscribe.
The page shows the four signup zones with the subscriber's current ones
checked (a zone counts as chosen when every one of its stations is on the
row — signups are zone-based, so that is the normal case) and saves through
the same store call /signup uses. Saving on an unsubscribed row turns alerts
back on, and says so.

  GET  /manage?t=…              the page
  POST /manage/api/update       JSON {"t": token, "zones": [zone keys]} → JSON

Each route handler returns ``(status, content_type, body_bytes)``.
"""
from __future__ import annotations

import json

from flask import render_template

from features.alerts.subscriptions import SubscriptionStore
from features.signup.page import ZONES
from features.unsubscribe.page import _lookup, _token
from shared import supabase as sb


def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


def _page(state: str, status: int = 200, **ctx):
    html = render_template("manage/page.html", state=state, **ctx)
    return status, "text/html; charset=utf-8", html.encode()


def zones_for(station_ids: list[str]) -> list[dict]:
    have = set(station_ids or [])
    return [{"key": key, "label": label,
             "stations": [name for _sid, name, *_ in sts],
             "checked": bool(sts) and all(sid in have for sid, *_ in sts)}
            for key, (label, sts) in ZONES.items()]


def handle_page(query, body):
    token = _token(query)
    if not token:
        return _page("missing", 400)
    try:
        row = _lookup(token)
    except Exception as exc:  # noqa: BLE001
        print(f"[manage] lookup failed: {exc}")
        return _page("error", 503)
    if row is None:
        return _page("unknown", 404)
    return _page("edit", email=row.get("email", ""), token=token, active=bool(row.get("active", True)),
                 zones=zones_for(row.get("station_ids") or []), sites=len(row.get("station_ids") or []))


def handle_update(query, body):
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return _json({"ok": False, "error": "Bad request."}, status=400)
    token = _token({"t": [str(data.get("t") or "")]}) or _token(query)
    if not token:
        return _json({"ok": False, "error": "This link is missing its code."}, status=400)
    zone_keys = [z for z in (data.get("zones") or []) if z in ZONES]
    if not zone_keys:
        return _json({"ok": False, "error": "Pick at least one area, or use Unsubscribe to stop all alerts."}, status=400)
    try:
        row = _lookup(token)
        if row is None:
            return _json({"ok": False, "error": "We don't recognise this link."}, status=404)
        station_ids = sorted({sid for z in zone_keys for sid, *_ in ZONES[z][1]})
        zone_label = ", ".join(ZONES[z][0] for z in zone_keys)
        sub = SubscriptionStore().upsert_subscription(row["email"], station_ids)   # reactivates if needed
        try:
            sb.update("subscribers", {"email": f"eq.{sub.email}"}, {"region_zone": zone_label})
        except Exception:  # noqa: BLE001 — informational label; the change itself is saved
            pass
        return _json({"ok": True, "zones": zone_label, "station_count": len(station_ids),
                      "reactivated": not bool(row.get("active", True))})
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        print(f"[manage] update failed: {exc}")
        return _json({"ok": False, "error": "Something went wrong saving your change — try again in a minute."}, status=500)


GET_ROUTES = {"/manage": handle_page}
POST_ROUTES = {"/manage/api/update": handle_update}
