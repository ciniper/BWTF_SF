"""Private, read-only viewer for the beach-alert emails already sent by the app."""
from flask import abort, redirect, render_template, request, url_for
import nh3

from features.alerts.deliveries import status_of
from features.sfpuc_updates.routes import enabled, fmt_time, offset_arg
from shared import supabase as sb

PAGE_SIZE = 25


def archived_html(value):
    """Keep the sent layout, but make subscriber actions and tracking inert."""
    if not value:
        return None
    tags = {"a", "p", "div", "span", "br", "hr", "b", "strong", "i", "em", "u", "small",
            "h1", "h2", "h3", "h4", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
            "img", "ul", "ol", "li", "blockquote", "style", "center"}
    return nh3.clean(value, tags=tags,
                     clean_content_tags={"script", "iframe", "object", "svg", "form", "template"},
                     attributes={"*": {"class", "style", "width", "height", "align", "valign", "bgcolor"},
                                 "table": {"cellpadding", "cellspacing", "border", "role"},
                                 "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"},
                                 "img": {"src", "alt"}})


def home():
    return redirect(url_for("sfpuc-email-admin" if enabled() else "sent-email-admin"))


def archive():
    offset = offset_arg()
    selected = request.args.get("message")
    if selected is not None:
        try:
            selected = int(selected)
            if selected < 1 or selected > 9223372036854775807:
                raise ValueError()
        except ValueError:
            abort(400)
    rows, message, error = [], None, None
    try:
        if not sb.is_configured():
            raise RuntimeError()
        rows = sb.select("alert_deliveries", {
            "select": "id,subject,recipient,sent_at,simulated,http_status,checked_at,error,delivery_state,delivery_at,opened_at",
            "channel": "eq.email", "order": "sent_at.desc,id.desc", "limit": str(PAGE_SIZE), "offset": str(offset)})
        selected = selected or (rows[0]["id"] if rows else None)
        if selected:
            found = sb.select("alert_deliveries", {"select": "*", "channel": "eq.email",
                              "id": f"eq.{selected}", "limit": "1"})
            if found:
                message = found[0]
    except Exception:
        # Connection errors can contain credentials or recipient data.
        error = "Sent emails are temporarily unavailable. Please retry."
    if selected and message is None and not error:
        abort(404)
    return render_template("alerts/emails/surfrider.html", rows=rows, message=message, offset=offset,
                           error=error, fmt_time=fmt_time, status_of=status_of,
                           archived_html=archived_html(message.get("html_body")) if message else None), 503 if error else 200, {
                               "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"}


def register(app, gated_page):
    app.add_url_rule("/alerts/emails", "admin-emails", gated_page(home, "/alerts/emails"))
    app.add_url_rule("/alerts/emails/surfrider", "sent-email-admin", gated_page(archive, "/alerts/emails/surfrider"))
