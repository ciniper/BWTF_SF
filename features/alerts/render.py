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

from shared import supabase as sb

MAP_URL = "https://webapps.sfpuc.org/sapps/beachesandbay.html"
THUMB_BASE = "https://bwtf-sf.vercel.app/static/emailmaps"
LOGO_URL = "https://bwtf.surfrider.org/images/BWTF-Logo_White.png"
_LABEL = {"cso": "CSO discharge", "posted": "bacteria posting"}
_LINE_ADVICE = {"cso": "CSO discharge — avoid water contact for 72 hours.",
                "posted": "bacteria posting — water contact not recommended."}
_SEV_COLOR = {"cso": "#b5310a", "posted": "#d4763a"}
_SEV_LABEL = {"cso": "CSO DISCHARGE", "posted": "BACTERIA POSTING"}
_SEV_ADVICE = {"cso": "Sewage discharge — avoid water contact for 72 hours.",
               "posted": "Elevated bacteria — water contact not recommended."}


def render_alert(transitions: list[dict], simulated: bool, zone: str | None = None) -> dict:
    """{"subject","sms_text","text_body","html_body"} for one recipient's events."""
    if sb.is_configured():
        try:
            out = sb.rpc("bwtf_render_alert",
                         {"p_transitions": transitions, "p_simulated": simulated,
                          "p_zone": zone})
            if isinstance(out, dict) and out.get("subject"):
                return out
        except Exception:
            pass  # fall through to the local port
    return _fallback(transitions, simulated, zone)


def _fallback(transitions: list[dict], simulated: bool, zone: str | None = None) -> dict:
    """Byte-for-byte Python port of bwtf_render_alert (do not restyle here —
    format changes belong in migration SQL; the parity test enforces this)."""
    prefix = "TEST " if simulated else ""
    n = len(transitions)
    zone = (zone or "").strip() or None
    label = lambda t: _LABEL.get(t["to"], "bacteria posting")
    sev = lambda t: t["to"] if t["to"] in _SEV_COLOR else "posted"

    if n == 1:
        subject = f"{prefix}SF Beach Alert: {label(transitions[0])} at {transitions[0]['station_name']}"
    else:
        subject = f"{prefix}SF Beach Alert: {n} sites affected"

    sms_text = (f"🚨 {prefix}SF Beach alert: "
                + "; ".join(f"{t['station_name']} ({label(t)})" for t in transitions)
                + f". Avoid water contact. Map: {MAP_URL}")[:320]

    text_body = (
        f"{prefix}SF Beach Water Quality Alert"
        + (f" — {zone}" if zone else "")
        + "\n\nNew events at your selected sites:\n"
        + "\n".join(f"- {t['station_name']}: {_LINE_ADVICE.get(t['to'], _LINE_ADVICE['posted'])}"
                    for t in transitions)
        + f"\n\nLive map: {MAP_URL}\n\n"
        + "Alerts are a community-science tool, not an official advisory. "
        + "Posted signs and SFPUC or health-department notices always win.\n\n"
        + 'You subscribed to SF beach alerts (Surfrider SF Blue Water Task Force). Reply "unsubscribe" to stop.'
    )

    rows_html = "".join(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 12px;border:1px solid #dde5ee;border-left:4px solid {_SEV_COLOR[sev(t)]}'
        ';border-radius:12px;"><tr>'
        f'<td width="132" style="padding:0;line-height:0;"><img src="{THUMB_BASE}/'
        f'{t["station_id"]}.jpg" alt="Map: {t["station_name"]}'
        '" width="132" height="96" style="display:block;border:0;"></td>'
        '<td style="padding:10px 14px;vertical-align:middle;">'
        f'<div style="font-weight:700;font-size:15px;color:#26272a;">{t["station_name"]}</div>'
        '<div style="font-size:12px;font-weight:700;letter-spacing:.06em;margin-top:3px;color:'
        f'{_SEV_COLOR[sev(t)]};">{_SEV_LABEL[sev(t)]}'
        "</div>"
        '<div style="font-size:13px;color:#54576F;margin-top:3px;line-height:1.45;">'
        f"{_SEV_ADVICE[sev(t)]}"
        "</div></td></tr></table>"
        for t in transitions
    )

    zone_html = (
        f'<div style="margin-top:6px;color:#cfe8f9;font-size:14px;font-weight:700;">Your zone: {zone}</div>'
        if zone else ""
    )

    html_body = (
        '<html><body style="margin:0;padding:24px;background:#E3EBF2;'
        "font-family:Roboto,'Helvetica Neue',Arial,sans-serif;color:#26272a;\">"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="max-width:640px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;">'
        '<tr><td style="background:#0072BC;padding:22px 24px;">'
        f'<img src="{LOGO_URL}" alt="Blue Water Task Force" '
        'width="150" style="display:block;border:0;">'
        '<div style="color:rgba(255,255,255,.85);font-size:12px;font-weight:700;'
        'letter-spacing:.12em;text-transform:uppercase;margin-top:12px;">Surfrider San Francisco</div>'
        '<h1 style="margin:6px 0 0;color:#ffffff;font-size:24px;line-height:1.2;">'
        f"{prefix}Beach Water Quality Alert</h1>"
        f"{zone_html}"
        "</td></tr>"
        '<tr><td style="padding:20px 24px 0;">'
        '<p style="margin:0 0 14px;color:#54576F;line-height:1.6;">New events at your selected sites. '
        "Avoid water contact and check conditions before heading out.</p>"
        f"{rows_html}"
        '<p style="margin:18px 0 0;">'
        f'<a href="{MAP_URL}" '
        'style="display:inline-block;background:#0072BC;color:#ffffff;text-decoration:none;'
        'padding:11px 18px;border-radius:999px;font-weight:700;font-size:14px;">View SFPUC Beach Map</a></p>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:18px 0 0;background:#E3EBF2;border-radius:12px;"><tr>'
        '<td style="padding:12px 14px;font-size:12.5px;color:#54576F;line-height:1.5;">'
        "Alerts are a community-science tool, not an official advisory. "
        "Posted signs and SFPUC or health-department notices always win.</td></tr></table>"
        '<p style="margin:16px 0 22px;font-size:12px;color:#8a93a3;line-height:1.5;">'
        "You subscribed to SF beach alerts from Surfrider San Francisco&#39;s Blue Water Task Force. "
        "Reply to this email with &quot;unsubscribe&quot; to stop alerts.</p>"
        "</td></tr></table></td></tr></table></body></html>"
    )
    return {"subject": subject, "sms_text": sms_text,
            "text_body": text_body, "html_body": html_body}
