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

# zone key -> (label, [(station_id, station_name, lat, lon), ...])
# Station ids are SFPUC LIMS ids (what subscriptions + the pg dispatcher use);
# coordinates from the getBeaches feed (fixed monitoring points).
ZONES = {
    "ocean": ("Ocean Beach", [
        ("4601", "Fort Funston", 37.71526, -122.50476),
        ("4602", "Ocean Beach at Sloat", 37.73567, -122.50769),
        ("4603", "Ocean Beach at Vicente", 37.73782, -122.50825),
        ("4604", "Ocean Beach at Balboa", 37.77492, -122.51351),
        ("4605", "Ocean Beach at Lincoln", 37.7638, -122.511),
        ("4606", "Ocean Beach at Pacheco", 37.74891, -122.50996),
    ]),
    "baker_china": ("Baker & China Beach", [
        ("4607", "China Beach", 37.78816, -122.49136),
        ("4608", "Baker Beach West", 37.78977, -122.48741),
        ("4609", "Baker Beach East", 37.79258, -122.48465),
        ("4610", "Baker Beach at Lobos Creek", 37.79088, -122.48594),
    ]),
    "north": ("North Beaches", [
        ("4611", "Crissy Field West", 37.8069, -122.4683),
        ("4612", "Crissy Field East", 37.8066, -122.4519),
        ("4613", "Aquatic Park", 37.8076, -122.4221),
        ("4614", "Hyde Street Pier", 37.8089, -122.4212),
    ]),
    "east": ("East Beaches", [
        ("4615", "Jackrabbit Beach", 37.7114, -122.3801),
        ("4616", "Windsurfer Circle", 37.7091, -122.3823),
        ("4617", "Sunnydale Cove", 37.7096, -122.3899),
        ("4618", "Mission Creek", 37.7716, -122.397),
        ("4619", "Islais Creek", 37.74703, -122.38793),
        ("4620", "Crane Cove Park", 37.7634, -122.3868),
    ]),
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
