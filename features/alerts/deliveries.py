"""Alert deliveries — what each subscriber was sent, and whether Brevo took it
(migration 015, table ``alert_deliveries``).

Two sides:
  * read — ``fetch_recent`` + ``render_section`` for the /alerts dashboard: the
    last alert events, one row per message with its state and the message itself.
  * write — ``sends_to_deliveries`` / ``record_manual`` for the Python manual
    dispatch (the /alerts send button), which sees Brevo's reply synchronously
    and stores a complete row at once. The pg dispatcher writes its own rows
    inside ``bwtf_dispatch_live``; ``bwtf_check_deliveries`` fills in the reply.

States (``status_of``):
  pending   — sent, no reply read yet, younger than PENDING_MINUTES
  accepted  — Brevo answered 2xx (queued for delivery; not the same as arrived)
  failed    — Brevo or pg_net said no (the error column says why)
  unknown   — nothing could be read before pg_net purged the reply
  delivered / bounced / blocked / spam / deferred — level 2: ``poll_brevo``
  asks Brevo's events API what became of recent accepted messages and stamps
  delivery_state / delivery_at (a failure's reason goes into error). It runs as
  a side job of the production forecast refresh (every 30 min), never as a
  public endpoint: nothing to spoof, and a week of downtime loses nothing
  (Brevo keeps 90 days of events). It also stamps ``opened_at`` (016) from the
  first 'opened' event — pixel-based, so null is not proof of unread.

Nothing here may break the dashboard or a send: every Supabase call is wrapped
by its caller, and ``record_manual`` swallows its own failures.
"""
from __future__ import annotations

import html
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from shared import supabase as sb

PACIFIC = ZoneInfo("America/Los_Angeles")
PENDING_MINUTES = 15       # no reply yet but this young → "pending", not "unknown"
RECENT_EVENTS = 12         # alert events shown on the dashboard
SUMMARY_DAYS = 30          # the one-line tally above them
UNLINKED_HOURS = 24        # rows the trigger did not link (should be none) still show

# level 2 — Brevo's transactional events (GET /v3/smtp/statistics/events)
BREVO_EVENTS_URL = "https://api.brevo.com/v3/smtp/statistics/events"
POLL_DAYS = 7              # accepted rows younger than this without a final state are re-asked
BREVO_PAGE = 2500          # Brevo's page cap
BREVO_MAX_PAGES = 4
_FINAL = {"delivered": "delivered", "hardBounces": "hard_bounce", "blocked": "blocked",
          "spam": "spam", "invalid": "invalid", "error": "error"}
_INTERIM = {"softBounces": "soft_bounce", "deferred": "deferred"}
OPEN_EVENT = "opened"      # not 'loadedByProxy': Apple Mail's prefetch is not a reader
_TAG = re.compile(r"^bwtf-delivery-(\d+)$")
_BOUNCING = ("bounced", "blocked", "spam")

_EVENT_LABEL = {"posted": "bacteria posting", "cso": "CSO discharge",
                "manual_dispatch": "manual send", "cleared": "cleared"}
_STATE_CLASS = {"accepted": "ok", "delivered": "ok", "pending": "wait", "deferred": "wait",
                "unknown": "unk", "failed": "bad", "bounced": "bad", "blocked": "bad", "spam": "bad"}


# ── time helpers ─────────────────────────────────────────────────────────────

def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pacific(value) -> str:
    dt = _parse_ts(value)
    if dt is None:
        return ""
    local = dt.astimezone(PACIFIC)
    return local.strftime("%b %-d, %-I:%M %p")


# ── state ────────────────────────────────────────────────────────────────────

def status_of(row: dict, now: datetime | None = None) -> tuple[str, str]:
    """(state, detail) for one delivery row. Level 2's delivery_state wins when
    present; otherwise Brevo's reply (http_status / error); otherwise age."""
    now = now or datetime.now(timezone.utc)
    state = (row.get("delivery_state") or "").strip()
    if state:
        when = _pacific(row.get("delivery_at"))
        reason = (row.get("error") or "").strip()
        tail = (f" {when}" if when else "") + (f": {reason}" if reason and state != "delivered" else "")
        if state == "delivered":
            return "delivered", f"delivered{tail}"
        if state in ("soft_bounce", "hard_bounce"):
            return "bounced", f"{state.replace('_', ' ')}{tail}"
        if state == "deferred":
            return "deferred", f"deferred by the receiving server{tail}"
        if state in ("blocked", "spam"):
            return state, f"{state}{tail}"
        return "failed", f"{state}{tail}"

    http = row.get("http_status")
    if http is not None:
        if 200 <= int(http) < 300:
            return "accepted", f"Brevo accepted it (HTTP {http})"
        return "failed", row.get("error") or f"HTTP {http}"
    if row.get("checked_at"):
        # checked, but no status: pg_net had purged the reply (or never got a request id)
        return "unknown", row.get("error") or "no reply recorded"
    sent = _parse_ts(row.get("sent_at"))
    if sent is not None and now - sent < timedelta(minutes=PENDING_MINUTES):
        return "pending", "waiting for Brevo's reply"
    return "unknown", "no reply read yet"


def summarize(rows: list[dict], now: datetime | None = None) -> dict:
    counts = {"messages": len(rows), "accepted": 0, "failed": 0, "pending": 0, "unknown": 0,
              "delivered": 0, "bounced": 0, "opened": 0}
    for r in rows:
        state, _ = status_of(r, now)
        key = {"blocked": "bounced", "spam": "bounced", "deferred": "pending"}.get(state, state)
        counts[key] = counts.get(key, 0) + 1
        if r.get("opened_at"):
            counts["opened"] += 1
    return counts


# ── read side ────────────────────────────────────────────────────────────────

def fetch_recent(limit: int = RECENT_EVENTS, now: datetime | None = None) -> dict:
    """The last ``limit`` alert events that reached anyone, each with its
    delivery rows, plus a SUMMARY_DAYS tally. Raises on Supabase errors — the
    caller decides how to show that (the table may not exist before 015)."""
    now = now or datetime.now(timezone.utc)
    logs = sb.select("alert_log", {
        "select": "id,created_at,event_type,station_names,recipient_count,channel,simulated,source",
        "recipient_count": "gt.0", "order": "created_at.desc", "limit": str(limit)})
    ids = [str(r["id"]) for r in logs]
    deliveries = sb.select("alert_deliveries", {
        "select": "*", "alert_log_id": f"in.({','.join(ids)})", "order": "id.asc"}) if ids else []
    since = (now - timedelta(hours=UNLINKED_HOURS)).isoformat()
    unlinked = sb.select("alert_deliveries", {
        "select": "*", "alert_log_id": "is.null", "sent_at": f"gte.{since}", "order": "id.asc"})
    tally_rows = sb.select("alert_deliveries", {
        "select": "recipient,http_status,checked_at,error,delivery_state,delivery_at,sent_at,opened_at",
        "sent_at": f"gte.{(now - timedelta(days=SUMMARY_DAYS)).isoformat()}"})

    by_log: dict = {}
    for d in deliveries:
        by_log.setdefault(d.get("alert_log_id"), []).append(d)
    events = [{"log": log, "deliveries": by_log.get(log["id"], [])} for log in logs]
    if unlinked:
        events.insert(0, {"log": {"id": None, "created_at": unlinked[0].get("sent_at"),
                                  "event_type": "unlinked", "station_names": [],
                                  "recipient_count": len(unlinked), "simulated": False},
                          "deliveries": unlinked})
    return {"events": events, "summary": summarize(tally_rows, now), "days": SUMMARY_DAYS,
            "bouncing": bouncing(tally_rows, now)}


def bouncing(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """Addresses whose messages bounced, were blocked or went to spam in the
    window — for Chase to review; nothing is deactivated automatically."""
    seen: dict = {}
    for r in rows:
        state, detail = status_of(r, now)
        if state in _BOUNCING and r.get("recipient"):
            e = seen.setdefault(r["recipient"], {"recipient": r["recipient"], "count": 0, "last": ""})
            e["count"] += 1
            e["last"] = detail
    return sorted(seen.values(), key=lambda e: (-e["count"], e["recipient"]))


def _chip(state: str, label: str | None = None) -> str:
    return f'<span class="dchip {_STATE_CLASS.get(state, "unk")}">{html.escape(label or state)}</span>'


def _message_details(d: dict) -> str:
    parts = []
    if d.get("sms_text"):
        parts.append(f"<pre class='dmsg'>{html.escape(d['sms_text'])}</pre>")
    if d.get("text_body"):
        parts.append(f"<pre class='dmsg'>{html.escape(d['text_body'])}</pre>")
    if d.get("html_body"):
        parts.append(f"<iframe class='dhtml' sandbox='' title='email as sent' "
                     f"srcdoc=\"{html.escape(d['html_body'], quote=True)}\"></iframe>")
    if not parts:
        return ""
    return f"<details class='dmessage'><summary>message</summary>{''.join(parts)}</details>"


def _event_block(ev: dict, now: datetime, open_: bool) -> str:
    log, rows = ev["log"], ev["deliveries"]
    kind = _EVENT_LABEL.get(log.get("event_type"), log.get("event_type") or "")
    if log.get("event_type") == "unlinked":
        kind = "messages not linked to an alert"
    stations = ", ".join(log.get("station_names") or [])
    tally = summarize(rows, now)
    chips = "".join(_chip(s, f"{tally[s]} {s}") for s in ("accepted", "delivered", "pending", "unknown", "failed", "bounced") if tally.get(s))
    head = (f"<span class='dwhen'>{html.escape(_pacific(log.get('created_at')))}</span> "
            f"<span class='dkind'>{html.escape(kind)}</span>"
            + (f" <span class='dsites'>{html.escape(stations)}</span>" if stations else "")
            + (' <span class="dchip sim">test</span>' if log.get("simulated") else "")
            + f" <span class='mute'>{len(rows)} message{'s' if len(rows) != 1 else ''}"
            + (f" · {log.get('recipient_count')} recipient{'s' if log.get('recipient_count') != 1 else ''}" if log.get("recipient_count") else "")
            + "</span> " + chips)
    if not rows:
        body = "<p class='mute dnone'>No delivery rows for this alert (sent before migration 015, or the sender had nothing to send).</p>"
    else:
        trs = []
        for d in rows:
            state, detail = status_of(d, now)
            if d.get("opened_at"):
                detail += f" · opened {_pacific(d['opened_at'])}"
            trs.append("<tr>"
                       f"<td class='dto'>{html.escape(d.get('recipient') or '')}</td>"
                       f"<td>{html.escape(d.get('channel') or '')}</td>"
                       f"<td>{_chip(state)} <small class='mute'>{html.escape(detail)}</small></td>"
                       f"<td class='dsubj'>{html.escape((d.get('subject') or '').strip() or '—')}</td>"
                       f"<td class='dtime mute'>{html.escape(_pacific(d.get('sent_at')))}</td>"
                       f"<td>{_message_details(d)}</td>"
                       "</tr>")
        body = ("<table class='dtable'><thead><tr><th>To</th><th>Channel</th><th>Status</th>"
                "<th>Subject</th><th>Sent</th><th></th></tr></thead><tbody>" + "".join(trs) + "</tbody></table>")
    return f"<details class='devent'{' open' if open_ else ''}><summary>{head}</summary>{body}</details>"


def render_section(data: dict, now: datetime | None = None) -> str:
    """HTML for the dashboard's Deliveries panel from ``fetch_recent``'s result."""
    now = now or datetime.now(timezone.utc)
    s = data.get("summary") or {}
    days = data.get("days", SUMMARY_DAYS)
    parts = [f"{s.get('messages', 0)} message{'s' if s.get('messages', 0) != 1 else ''}"]
    for key in ("accepted", "delivered", "opened", "pending", "unknown", "failed", "bounced"):
        if s.get(key):
            parts.append(f"{s[key]} {key}")
    line = f"<p class='dsummary'>Last {days} days: {' · '.join(parts)}.</p>"
    if data.get("bouncing"):
        items = "; ".join(f"{html.escape(b['recipient'])} ({b['count']}: {html.escape(b['last'])})" for b in data["bouncing"])
        line += f"<p class='dbouncing'>{_chip('bounced', 'review')} Not arriving: {items}. Fix or remove the address — nothing is deactivated automatically.</p>"
    events = data.get("events") or []
    if not events:
        return line + "<p class='mute'>No alerts have reached anyone yet.</p>"
    return line + "<div class='dlist'>" + "".join(_event_block(ev, now, i == 0) for i, ev in enumerate(events)) + "</div>"


# ── write side (Python manual dispatch) ──────────────────────────────────────

def sends_to_deliveries(notifier, channel: str, rendered: dict, simulated: bool) -> list[dict]:
    """Delivery rows for what a notifier just did (its ``last_sends``), with the
    rendered message attached. Complete rows: the reply was synchronous."""
    from features.alerts.render import RENDERER_VERSION
    out = []
    for s in getattr(notifier, "last_sends", None) or []:
        row = {
            "recipient": s.get("to") or "",
            "channel": channel,
            "simulated": bool(simulated),
            "subject": " " if channel == "sms" else rendered.get("subject"),
            "renderer": RENDERER_VERSION,
            "http_status": s.get("http_status"),
            "message_id": s.get("message_id"),
            "error": s.get("error"),
            "source": "manual",
        }
        if channel == "sms":
            row["sms_text"] = rendered.get("sms_text")
        else:
            row["text_body"] = rendered.get("text_body")
            row["html_body"] = rendered.get("html_body")
        out.append(row)
    return out


def record_manual(alert_log_id: int | None, deliveries: list[dict]) -> int:
    """Insert complete delivery rows for a manual dispatch. Never raises."""
    if not deliveries or not sb.is_configured():
        return 0
    stamp = datetime.now(timezone.utc).isoformat()
    rows = []
    for d in deliveries:
        row = dict(d)
        row["alert_log_id"] = alert_log_id
        row["checked_at"] = stamp     # the reply was read synchronously
        rows.append(row)
    try:
        sb.insert("alert_deliveries", rows)
        return len(rows)
    except Exception as exc:  # logging must never break the dispatch path
        print(f"[alert_deliveries] failed to record {len(rows)} deliveries: {exc}")
        return 0


# ── level 2: did it arrive? (Brevo's events API, polled from the refresh) ────

def brevo_key() -> str | None:
    """BREVO_API_KEY from the environment, else watcher_config (the key the pg
    sender uses). Never log or return it to a page."""
    key = os.environ.get("BREVO_API_KEY")
    if key:
        return key
    try:
        rows = sb.select("watcher_config", {"select": "value", "key": "eq.brevo_api_key"})
        return (rows[0].get("value") or None) if rows else None
    except Exception:
        return None


def _fetch_brevo_events(key: str, days: int) -> list[dict]:
    """Every transactional event of the last ``days`` days, newest first, paged."""
    import requests
    out: list[dict] = []
    for page in range(BREVO_MAX_PAGES):
        r = requests.get(BREVO_EVENTS_URL,
                         params={"days": days, "limit": BREVO_PAGE, "offset": page * BREVO_PAGE, "sort": "desc"},
                         headers={"api-key": key, "accept": "application/json"}, timeout=20)
        if r.status_code >= 300:
            raise RuntimeError(f"Brevo events API {r.status_code}: {r.text[:200]}")
        events = (r.json() or {}).get("events") or []
        out.extend(events)
        if len(events) < BREVO_PAGE:
            break
    return out


def _pick_state(events: list[dict]) -> tuple[str, str | None, str | None] | None:
    """(state, at, reason) for one message: delivered wins; else the latest hard
    failure; else a soft bounce; else deferred. None when Brevo has only
    'requests' (accepted) or nothing yet — the row stays as it is."""
    final = [e for e in events if e.get("event") in _FINAL]
    interim = [e for e in events if e.get("event") in _INTERIM]
    latest = lambda lst: max(lst, key=lambda e: e.get("date") or "")
    if final:
        delivered = [e for e in final if e.get("event") == "delivered"]
        e = latest(delivered) if delivered else latest(final)
    elif interim:
        soft = [e for e in interim if e.get("event") == "softBounces"]
        e = latest(soft) if soft else latest(interim)
    else:
        return None
    state = _FINAL.get(e.get("event")) or _INTERIM.get(e.get("event"))
    at = _parse_ts(e.get("date"))
    return state, (at.astimezone(timezone.utc).isoformat() if at else None), (e.get("reason") or None)


def _first_open(events: list[dict]) -> str | None:
    """ISO time of the earliest 'opened' event, or None. 'loadedByProxy'
    (Apple Mail prefetch) is not an open."""
    opens = [e for e in events if e.get("event") == OPEN_EVENT and _parse_ts(e.get("date"))]
    if not opens:
        return None
    return min(_parse_ts(e["date"]) for e in opens).astimezone(timezone.utc).isoformat()


def poll_brevo(now: datetime | None = None, days: int = POLL_DAYS, dry_run: bool = False) -> dict:
    """Stamp delivery_state / delivery_at on recent accepted rows from Brevo's
    events. Candidates: message_id known, sent within ``days``, no final state
    yet (null, deferred or soft_bounce). One paged events call per poll, matched
    on messageId, else on the bwtf-delivery-<id> tag. A failure's reason goes
    into ``error``; a delivery clears it. Rows without an open yet are asked
    again too (016: ``opened_at`` = the first 'opened' event). Raises on
    Brevo/Supabase errors — the refresh hook swallows and logs them.
    ``dry_run`` returns the patches instead of writing them."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    rows = sb.select("alert_deliveries", {
        "select": "id,message_id,delivery_state,sent_at,opened_at",
        "message_id": "not.is.null", "sent_at": f"gte.{since}",
        "or": "(delivery_state.is.null,delivery_state.in.(deferred,soft_bounce),opened_at.is.null)",
        "order": "id.asc", "limit": "500"})
    if not rows:
        return {"candidates": 0, "updated": 0, "events": 0, "states": {}}
    key = brevo_key()
    if not key:
        raise RuntimeError("no Brevo API key (BREVO_API_KEY or watcher_config.brevo_api_key)")
    events = _fetch_brevo_events(key, days + 1)
    by_msg: dict = {}
    by_tag: dict = {}
    for e in events:
        if e.get("messageId"):
            by_msg.setdefault(e["messageId"], []).append(e)
        tags = e.get("tag") or e.get("tags") or []      # Brevo: one comma-joined string
        for t in (re.split(r"[,\s]+", tags) if isinstance(tags, str) else tags):
            m = _TAG.match(str(t))
            if m:
                by_tag.setdefault(int(m.group(1)), []).append(e)
    updated = 0
    states: dict = {}
    patches: list[dict] = []
    for r in rows:
        evs = by_msg.get(r["message_id"]) or by_tag.get(int(r["id"])) or []
        patch: dict = {}
        picked = _pick_state(evs)
        if picked and picked[0] != r.get("delivery_state"):
            state, at, reason = picked
            patch = {"delivery_state": state, "delivery_at": at or now.isoformat()}
            if state == "delivered":
                patch["error"] = None
            elif reason:
                patch["error"] = reason[:500]
            states[state] = states.get(state, 0) + 1
        opened = _first_open(evs)
        if opened and not r.get("opened_at"):
            patch["opened_at"] = opened
            states["opened"] = states.get("opened", 0) + 1
        if not patch:
            continue
        patches.append({"id": r["id"], **patch})
        if not dry_run:
            sb.update("alert_deliveries", {"id": f"eq.{r['id']}"}, patch)
        updated += 1
    out = {"candidates": len(rows), "updated": updated, "events": len(events), "states": states}
    if dry_run:
        out["patches"] = patches
    return out
