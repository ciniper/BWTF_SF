"""Alert message rendering — single source of truth, shared with Postgres.

THE RENDERER LIVES IN POSTGRES: ``bwtf_render_alert`` (migration 007), the
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
"""
from __future__ import annotations

from shared import supabase as sb

MAP_URL = "https://webapps.sfpuc.org/sapps/beachesandbay.html"
_LABEL = {"cso": "CSO discharge", "posted": "bacteria posting"}
_LINE_ADVICE = {"cso": "CSO discharge — avoid water contact for 72 hours.",
                "posted": "bacteria posting — water contact not recommended."}


def render_alert(transitions: list[dict], simulated: bool) -> dict:
    """{"subject","sms_text","text_body","html_body"} for one recipient's events."""
    if sb.is_configured():
        try:
            out = sb.rpc("bwtf_render_alert",
                         {"p_transitions": transitions, "p_simulated": simulated})
            if isinstance(out, dict) and out.get("subject"):
                return out
        except Exception:
            pass  # fall through to the local port
    return _fallback(transitions, simulated)


def _fallback(transitions: list[dict], simulated: bool) -> dict:
    """Byte-for-byte Python port of bwtf_render_alert (do not restyle here —
    format changes belong in migration SQL; the parity test enforces this)."""
    prefix = "TEST " if simulated else ""
    n = len(transitions)
    label = lambda t: _LABEL.get(t["to"], "bacteria posting")

    if n == 1:
        subject = f"{prefix}SF Beach Alert: {label(transitions[0])} at {transitions[0]['station_name']}"
    else:
        subject = f"{prefix}SF Beach Alert: {n} sites affected"

    sms_text = (f"🚨 {prefix}SF Beach alert: "
                + "; ".join(f"{t['station_name']} ({label(t)})" for t in transitions)
                + f". Avoid water contact. Map: {MAP_URL}")[:320]

    text_body = (
        f"{prefix}SF Beach Water Quality Alert\n\nNew events at your selected sites:\n"
        + "\n".join(f"- {t['station_name']}: {_LINE_ADVICE.get(t['to'], _LINE_ADVICE['posted'])}"
                    for t in transitions)
        + f"\n\nLive map: {MAP_URL}\n\n"
        + "You are receiving this because you subscribed on the Surfrider SF BWTF dashboard."
    )

    items = "".join(f"<li><b>{t['station_name']}</b> — {label(t)}</li>" for t in transitions)
    html_body = (
        '<html><body style="margin:0;padding:24px;background:#e2e8ee;'
        "font-family:'Avenir Next','Trebuchet MS','Segoe UI',sans-serif;color:#26272a;\">"
        '<div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:24px;overflow:hidden;">'
        '<div style="background:#1f6fb0;padding:22px 24px;">'
        '<div style="color:rgba(255,255,255,.85);font-size:12px;font-weight:700;'
        'letter-spacing:.12em;text-transform:uppercase;">Blue Water Task Force • Surfrider SF</div>'
        f'<h1 style="margin:10px 0 0;color:#ffffff;font-size:26px;">{prefix}Beach Water Quality Alert</h1>'
        '</div><div style="padding:22px 24px;">'
        '<div style="background:rgba(209,92,92,.10);border-radius:16px;padding:14px 16px;margin-bottom:16px;">'
        '<p style="margin:0 0 8px;font-weight:700;">New events at your sites</p>'
        f'<ul style="margin:0;padding-left:18px;line-height:1.6;">{items}</ul></div>'
        '<p style="margin:0;color:#5e6a71;line-height:1.6;">Avoid water contact and check the live map before heading out.</p>'
        f'<p style="margin:16px 0 0;"><a href="{MAP_URL}" '
        'style="display:inline-block;background:#317fb2;color:#ffffff;text-decoration:none;'
        'padding:11px 16px;border-radius:999px;font-weight:700;">View SFPUC Beach Map</a></p>'
        "</div></div></body></html>"
    )
    return {"subject": subject, "sms_text": sms_text,
            "text_body": text_body, "html_body": html_body}
