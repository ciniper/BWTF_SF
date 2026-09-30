"""Alert message rendering — single source of truth, shared with Postgres.

THE RENDERER LIVES IN POSTGRES: ``bwtf_render_alert`` (migration 008), the
same function the production pg dispatcher uses. Python senders (the legacy
manual-dispatch endpoint and the watcher thread's send mode) call it via RPC,
so any format change made in the SQL function automatically applies to every
sender — the two-templates drift Chase hit on 2026-09-02 (duplicate emails
with different designs) can't recur.

``_fallback()`` below is a byte-for-byte Python port of the SQL, used only
when Supabase is unreachable (in which case the Python path likely couldn't
have loaded subscribers either — it's a parachute for the parachute).
``db/scripts/test_render_parity.py`` asserts RPC output == fallback output,
so if the SQL format changes without this port being updated, the parity test
fails loudly rather than the fallback drifting silently.

Transitions shape (same as the pg dispatcher's): a list of
``{"station_id", "station_name", "to"}`` dicts ("to" is 'cso' or 'posted').
``zone`` is the subscriber's signup zone label ('East Beaches', ...) or None;
the pg dispatcher looks it up from subscribers.region_zone — the legacy
Python paths don't and pass None (the email just omits the zone line).
"""
from __future__ import annotations

import os

from shared import supabase as sb
from shared.clock import now_pacific

MAP_URL = "https://webapps.sfpuc.org/sapps/beachesandbay.html"
# The migration whose bwtf_render_alert wrote the message; stored on every
# alert_deliveries row (015) so an old message is never mistaken for a
# re-render with a newer template. Bump with the SQL (015's renderer_ver and
# tests/test_alert_deliveries.py hold it to the latest renderer migration).
RENDERER_VERSION = "021"
SITE_URL = os.environ.get("SITE_URL", "https://bwtf-sf.vercel.app").rstrip("/")


def unsubscribe_url(token: str | None) -> str | None:
    """The per-subscriber one-click link (017). None when there is no token
    (phone-only rows, or a row read before 017 was applied). The renderer
    derives the "Change your sites" link (/manage?t=…) from it."""
    return f"{SITE_URL}/unsubscribe?t={token}" if token else None


STAMP_FORMAT = "%a %b %-d, %-I:%M %p %Z"   # "Tue Sep 30, 7:12 AM PDT" — the same shape the dispatcher's to_char produces


def alert_stamp() -> str:
    return now_pacific().strftime(STAMP_FORMAT)


def render_alert(transitions: list[dict], simulated: bool, zone: str | None = None,
                 unsubscribe_url: str | None = None, when: str | None = None) -> dict:
    """{"subject","sms_text","text_body","html_body"} for one recipient's events.
    ``unsubscribe_url`` (017) puts the one-click link in the footer; without it
    the footer keeps the older 'reply to unsubscribe' sentence."""
    when = when or alert_stamp()
    if sb.is_configured():
        try:
            out = sb.rpc("bwtf_render_alert",
                         {"p_transitions": transitions, "p_simulated": simulated,
                          "p_zone": zone, "p_unsubscribe_url": unsubscribe_url, "p_when": when})
            if isinstance(out, dict) and out.get("subject"):
                return out
        except Exception:
            pass  # fall through to the local port
    return _fallback(transitions, simulated, zone, unsubscribe_url, when)


def _fallback(transitions: list[dict], simulated: bool, zone: str | None = None,
              unsubscribe_url: str | None = None, when: str | None = None) -> dict:
    """Byte-for-byte Python port of bwtf_render_alert (migration 021 — do not restyle here;
    format changes belong in migration SQL; db/scripts/test_render_parity.py enforces this)."""
    prefix = "TEST " if simulated else ""
    n = len(transitions)
    zone = (zone or "").strip() or None
    unsub = (unsubscribe_url or "").strip() or None
    stamp = (when or "").strip() or None
    manage = unsub.replace("/unsubscribe?t=", "/manage?t=") if unsub else None
    site = (unsub.split("/unsubscribe?t=", 1)[0] if unsub else "") or "https://bwtf-sf.vercel.app"
    is_cso = lambda t: t["to"] == "cso"  # noqa: E731
    cso = [t for t in transitions if is_cso(t)]
    posted = [t for t in transitions if not is_cso(t)]
    n_cso, n_posted = len(cso), len(posted)
    name1, cso1 = transitions[0]["station_name"], is_cso(transitions[0])
    tone = "#b5310a" if n_cso > 0 else "#d4763a"
    beaches = lambda k: f"{k} beach" + ("es" if k > 1 else "")  # noqa: E731
    col = lambda t: "#b5310a" if is_cso(t) else "#d4763a"  # noqa: E731

    if n == 1:
        subject = f"{prefix}Beach alert: {name1}" + (" — sewage discharge" if cso1 else " — posted")
    else:
        subject = f"{prefix}Beach alert: " + ", ".join(filter(None, [
            f"sewage discharge at {beaches(n_cso)}" if n_cso > 0 else None,
            f"posted at {beaches(n_posted)}" if n_posted > 0 else None]))

    if n == 1:   # names and counts stay black; only the status phrase takes its colour
        headline = name1 + (' has a <span style="color:#b5310a">sewage discharge</span>.' if cso1 else ' is <span style="color:#d4763a">posted</span>.')
    else:
        headline = " ".join(filter(None, [
            f'<span style="color:#b5310a">Sewage discharge</span> at {beaches(n_cso)}.' if n_cso > 0 else None,
            f'<span style="color:#d4763a">Posted</span> at {beaches(n_posted)}.' if n_posted > 0 else None]))
    facts = ((f"Discharging: {', '.join(t['station_name'] for t in cso)}. " if n_cso > 0 else "")
             + (f"Posted: {', '.join(t['station_name'] for t in posted)}. " if n_posted > 0 else "")
             + "From SFPUC's beach map, which this site checks every minute.")

    sms_text = (f"{prefix}Beach alert: "
                + "; ".join(f"{t['station_name']} — " + ("sewage discharge" if is_cso(t) else "posted") for t in transitions)
                + f". Live status: {site}/today")[:320]

    text_body = (
        subject + (f"\n{stamp}" if stamp else "") + "\n\n"
        + "\n".join(f"- {t['station_name']}: " + ("sewage discharge." if is_cso(t) else "posted.") for t in transitions)
        + "\n\nFrom SFPUC: beach users should be aware that during and immediately after rainfall, nearshore bacteria concentrations may be elevated, even when there has not been a combined sewer discharge.\n\n"
        + f"Live status: {site}/today\nSFPUC's map: {MAP_URL}\n\n"
        + "Community science by Surfrider SF's Blue Water Task Force, not an official advisory; posted signs and notices from SFPUC or the health department take precedence.\n"
        + ('Reply to this email with "unsubscribe" to stop alerts.' if unsub is None
           else f"Change your sites: {manage}\nUnsubscribe: {unsub}")
    )

    rows_html = "".join(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 10px;border:2px solid '
        f'{col(t)};border-radius:14px;"><tr>'
        f'<td width="112" style="padding:0;line-height:0;"><img src="{site}/static/emailmaps/{t["station_id"]}'
        '.jpg" alt="" width="112" height="82" style="display:block;border:0;border-radius:12px 0 0 12px;"></td>'
        '<td style="padding:10px 14px;vertical-align:middle;">'
        f'<div style="font-weight:700;font-size:15px;color:#26272a;">{t["station_name"]}</div>'
        '<div style="font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-top:2px;color:'
        + ('#b5310a;">Sewage discharge' if is_cso(t) else '#d4763a;">Posted') + "</div>"
        '<div style="font-size:13px;color:#54576F;margin-top:3px;line-height:1.45;">'
        + ("A combined sewer discharge reported by SFPUC; the beach is posted proactively and sampled until it clears." if is_cso(t)
           else "SFPUC posts a beach when samples show bacteria above State standards, and sometimes as a precaution; repeat samples are collected until it clears.")
        + "</div></td></tr></table>"
        for t in transitions
    )

    footer_sub = ('You subscribed to SF beach alerts. Reply to this email with &quot;unsubscribe&quot; to stop them.' if unsub is None
                  else "You get this because you chose " + ("these beaches" if zone is None else f"the {zone} zone")
                       + f' &middot; <a href="{manage}" style="color:#54576F;">Change your sites</a>'
                       + f' &middot; <a href="{unsub}" style="color:#54576F;">Unsubscribe</a>')

    html_body = (
        "<html><body style=\"margin:0;padding:24px 16px;background:#E3EBF2;font-family:Roboto,'Helvetica Neue',Arial,sans-serif;color:#26272a;\">"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
        '<table role="presentation" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">'
        '<tr><td style="padding:0 6px 12px;"><table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="padding:0 10px 0 0;line-height:0;"><img src="{site}/static/brand/bwtf_144x144.png" alt="" width="36" height="36" style="display:block;border:0;border-radius:9px;"></td>'
        f'<td><div style="font-weight:900;font-size:17px;letter-spacing:.04em;text-transform:uppercase;color:#26272a;line-height:1.1;">{prefix}SF Beach Water Quality Alert</div>'
        '<div style="font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#54576F;margin-top:2px;">Surfrider SF &middot; Blue Water Task Force</div></td>'
        "</tr></table></td></tr>"
        f'<tr><td style="background:#ffffff;border:2px solid {tone};border-radius:20px;padding:20px 22px 18px;">'
        '<div style="font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#54576F;">Beach alert'
        + ("" if stamp is None else f" &middot; {stamp}") + ("" if zone is None else f" &middot; {zone} zone") + "</div>"
        f'<h1 style="margin:6px 0 0;font-size:26px;line-height:1.15;font-weight:900;letter-spacing:.01em;color:#26272a;">{headline}</h1>'
        f'<p style="margin:8px 0 16px;color:#54576F;font-size:14px;line-height:1.5;">{facts}</p>'
        + rows_html
        + "<p style=\"margin:14px 0 0;font-size:13.5px;color:#26272a;line-height:1.5;\"><b>From SFPUC:</b> beach users should be aware that during and immediately after rainfall, nearshore bacteria concentrations may be elevated, even when there has not been a combined sewer discharge.</p>"
        f'<p style="margin:16px 0 4px;"><a href="{site}/today" style="display:inline-block;background:#0072BC;color:#ffffff;text-decoration:none;padding:11px 18px;border-radius:999px;font-weight:700;font-size:14px;">Live status</a></p>'
        "</td></tr>"
        '<tr><td style="padding:14px 8px 0;font-size:12px;color:#54576F;line-height:1.55;">'
        "Community science by Surfrider SF's Blue Water Task Force, not an official advisory; posted signs and notices from SFPUC or the health department take precedence. "
        f'SFPUC\'s own map: <a href="{MAP_URL}" style="color:#54576F;">webapps.sfpuc.org</a>.<br>{footer_sub}'
        "</td></tr></table></td></tr></table></body></html>"
    )
    return {"subject": subject, "sms_text": sms_text, "text_body": text_body, "html_body": html_body}
