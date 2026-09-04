"""Public alert signup — the friendly, zone-based subscribe page.

Deliberately OUTSIDE the alerts passphrase gate: this is the one alerts
surface meant for the public. Design follows Kyle's "Know Before You Go"
mockup; product decisions from Chase (2026-09-02):

  * Zones, not stations. Four named zones expand to station-id lists at
    signup time, so the (frozen, behaviorally-verified) pg dispatcher is
    untouched — a zone subscription IS a per-station subscription. The zone
    label lands in ``subscribers.region_zone`` for future alert copy.
  * Email only. Phone/SMS stays on the gated /alerts dashboard (carrier
    gateways are fragile and SMS consent/compliance is a launch-checklist
    item — see TODO B4/B10).
  * Bad-news-only alerts (posted + CSO escalations) — matching what the
    dispatcher does. All-clear / caution tiers are TODO B6.

BETA note: hardening is minimal by design while the site is unshared — a
honeypot field plus server-side validation. The full launch checklist lives
in TODO B10; publicizing this page is the public-launch tripwire.

Each route handler returns ``(status, content_type, body_bytes)``.
"""
from __future__ import annotations

import json
import re

from flask import render_template

from features.alerts.subscriptions import SubscriptionStore
from shared import supabase as sb
from shared.stations import STATIONS

# zone key -> (label, [(station_id, station_name, lat, lon), ...])
# Station ids are SFPUC LIMS ids (what subscriptions + the pg dispatcher use).
# Names and coordinates come from the canonical registry (shared/stations.py)
# — this table used to be hand-typed, the last copy not built from it. Which
# stations form a zone is the product decision kept here; everything about a
# station is looked up by id.
_BY_SFPUC_ID = {s.sfpuc_id: s for s in STATIONS.values()}


def _stations(*sfpuc_ids: str) -> list[tuple[str, str, float, float]]:
    return [(sid, _BY_SFPUC_ID[sid].name, _BY_SFPUC_ID[sid].lat, _BY_SFPUC_ID[sid].lon)
            for sid in sfpuc_ids]


ZONES = {
    "ocean": ("Ocean Beach", _stations("4601", "4602", "4603", "4604", "4605", "4606")),
    "baker_china": ("Baker & China Beach", _stations("4607", "4608", "4609", "4610")),
    "north": ("North Beaches", _stations("4611", "4612", "4613", "4614")),
    "east": ("East Beaches", _stations("4615", "4616", "4617", "4618", "4619", "4620")),
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


def handle_page(query, body):
    zones = [{"key": k, "label": label,
              "stations": [{"id": sid, "name": name, "lat": lat, "lon": lon}
                           for sid, name, lat, lon in sts]}
             for k, (label, sts) in ZONES.items()]
    html = render_template("signup/page.html", zones_json=json.dumps(zones))
    return 200, "text/html; charset=utf-8", html.encode()


def handle_subscribe(query, body):
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return _json({"ok": False, "error": "Bad request."}, status=400)

    # Honeypot: bots fill every field; humans never see this one. The field
    # must not be named "website"/"url" — Chrome autofill fills offscreen
    # inputs with those names and bot-flags real people (hit 2026-09-02).
    # The fake success carries the same fields as the real one so neither a
    # bot nor a false-positived human can tell the difference.
    if (data.get("hp-extra") or data.get("website") or "").strip():
        fake_zones = [z for z in (data.get("zones") or []) if z in ZONES]
        return _json({"ok": True,
                      "zones": ", ".join(ZONES[z][0] for z in fake_zones) or "your selected areas",
                      "station_count": len({sid for z in fake_zones for sid, *_ in ZONES[z][1]})})

    email = (data.get("email") or "").strip()
    zone_keys = [z for z in (data.get("zones") or []) if z in ZONES]
    if not _EMAIL_RE.match(email):
        return _json({"ok": False, "error": "That email doesn't look right — check it and try again."}, status=400)
    if not zone_keys:
        return _json({"ok": False, "error": "Pick at least one area to follow."}, status=400)

    station_ids = sorted({sid for z in zone_keys for sid, *_ in ZONES[z][1]})
    zone_label = ", ".join(ZONES[z][0] for z in zone_keys)
    try:
        store = SubscriptionStore()
        subscription = store.upsert_subscription(email, station_ids)
        if sb.is_configured():  # informational label for future alert copy
            try:
                sb.update("subscribers",
                          {"email": f"eq.{subscription.email}"},
                          {"region_zone": zone_label})
            except Exception:
                pass  # best-effort; the subscription itself already saved
        return _json({"ok": True,
                      "zones": zone_label,
                      "station_count": len(station_ids)})
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        return _json({"ok": False, "error": "Something went wrong saving your signup — try again in a minute."},
                     status=500)


GET_ROUTES = {"/signup": handle_page}
POST_ROUTES = {"/signup/api/subscribe": handle_subscribe}
