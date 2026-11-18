"""Email simulator (/alerts/email-sim, an Admin tab since 2026-11-17): how many alert emails one subscriber
would have got over a past stretch, from the current sender and from a proposed one.

Chase, 2026-11-17, after the 11/13–11/17 storm audit (docs/discharge-observations.md, "Too many alert emails"):
"add this number of emails analysis ... as a tool in admin page, where we could analyze a set time range and
subscription type to see how many emails we'd have given a hold time, maybe retrigger time and the no same day
on-off-on rule".

History is the Online Postings Timeline's (features/cso_history/page.py): the alert log's transitions, with the
parallel sources' duplicate detections already merged. That's the stream the live sender (the Postgres tick,
db/migrations/004 and later) acted on.

The current sender, as modelled here: each minute's tick sends a subscriber one email listing their stations
that moved to a worse status in that minute (ok → posted, ok → discharge, posted → discharge). Clears send
nothing. The proposed knobs (all held, none live):

  hold        wait this many minutes after a change and send one email for everything that changed meanwhile.
  window      "first": the window starts at the first change and doesn't move, so an alert waits at most the hold.
              "restart": every new change restarts the clock; the email goes once things are quiet for the hold.
  same-day    a station that already had a discharge flag earlier the same Pacific day doesn't email a discharge
              again that day. A flag that comes back the next day emails as usual.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import render_template

from shared.stations import STATIONS
from shared.zones import ZONES

PACIFIC = ZoneInfo("America/Los_Angeles")
SEVERITY = {"ok": 0, "posted": 1, "cso": 2}
STATUS_LABEL = {"posted": "posted", "cso": "sewage discharge", "ok": "clear"}
NAME_OF = {s.sfpuc_id: s.sfpuc_name for s in STATIONS.values()}
DEFAULT_DAYS = 7
MAX_HOLD = 24 * 60


@dataclass(frozen=True)
class Change:
    at: datetime          # aware
    station_id: str       # SFPUC feed id, e.g. "4605"
    station_name: str
    frm: str
    to: str

    @property
    def worse(self) -> bool:
        return SEVERITY.get(self.to, 0) > SEVERITY.get(self.frm, 0)

    @property
    def day(self) -> date:
        return self.at.astimezone(PACIFIC).date()


@dataclass
class Email:
    first_at: datetime                       # the first change in it
    send_at: datetime                        # when it goes out
    changes: list = field(default_factory=list)

    @property
    def worst(self) -> dict[str, tuple[str, str]]:
        """{station id: (name, worst status in this email)}, in the order they first changed."""
        worst: dict[str, tuple[str, str]] = {}
        for c in self.changes:
            name, to = worst.get(c.station_id, (c.station_name, "ok"))
            worst[c.station_id] = (name, c.to if SEVERITY[c.to] > SEVERITY[to] else to)
        return worst

    @property
    def stations(self) -> list[tuple[str, str]]:
        return list(self.worst.values())

    @property
    def waited_minutes(self) -> int:
        return round((self.send_at - self.first_at).total_seconds() / 60)


def _ts(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def changes_from_windows(stations: list[dict]) -> list[Change]:
    """Every status change, oldest first, from the timeline's per-station event windows
    (build_station_windows' shape: windows → segments with a start and a status, and an end when it cleared)."""
    out = []
    for s in stations:
        sid, name = s["station_id"], s.get("station_name") or NAME_OF.get(s["station_id"], s["station_id"])
        for w in s.get("windows") or []:
            prev = "ok"
            for seg in w.get("segments") or []:
                out.append(Change(_ts(seg["start"]), sid, name, prev, seg["status"]))
                prev = seg["status"]
            if w.get("end"):
                out.append(Change(_ts(w["end"]), sid, name, prev, "ok"))
    out.sort(key=lambda c: (c.at, c.station_id))
    return out


def simulate(changes: list[Change], station_ids, start: datetime, end: datetime,
             hold_minutes: int = 0, window: str = "first", same_day: bool = False) -> dict:
    """The emails one subscriber to ``station_ids`` gets for changes in [start, end).

    hold_minutes=0 is the current sender: one email per minute that has a worse status in it.
    Changes before ``start`` still count for the same-day rule (a flag that was up that morning)."""
    wanted = set(station_ids)
    hold = timedelta(minutes=max(0, int(hold_minutes)))
    discharge_days: dict[str, set] = {}
    triggers, skipped = [], []
    for c in changes:
        if c.station_id in wanted and start <= c.at < end and c.worse:
            if same_day and c.to == "cso" and c.day in discharge_days.get(c.station_id, ()):
                skipped.append(c)
            else:
                triggers.append(c)
        if "cso" in (c.frm, c.to):
            discharge_days.setdefault(c.station_id, set()).add(c.day)

    emails: list[Email] = []
    for c in triggers:
        cur = emails[-1] if emails else None
        if not hold:
            minute = c.at.replace(second=0, microsecond=0)
            if cur and cur.first_at == minute:
                cur.changes.append(c)
            else:
                emails.append(Email(minute, minute, [c]))
            continue
        if cur and c.at <= cur.send_at:            # inside the open window (a change exactly at its close still joins)
            cur.changes.append(c)
            if window == "restart":
                cur.send_at = c.at + hold
        else:
            emails.append(Email(c.at, c.at + hold, [c]))

    by_day: dict[date, int] = {}
    for e in emails:
        d = e.send_at.astimezone(PACIFIC).date()
        by_day[d] = by_day.get(d, 0) + 1
    return {"emails": emails, "skipped": skipped, "by_day": by_day, "total": len(emails),
            "longest_wait": max((e.waited_minutes for e in emails), default=0)}


# ── the page ────────────────────────────────────────────────────────────────

def subscription_options() -> list[dict]:
    """What a subscriber can pick, grouped as the signup page does: everything, a zone, one station."""
    opts = [{"value": "all", "label": "Every station", "group": "", "ids": sorted(NAME_OF)}]
    for z in ZONES.values():
        opts.append({"value": f"zone:{z.key}", "label": z.label, "group": "Zone", "ids": list(z.station_ids)})
    for z in ZONES.values():
        for sid in z.station_ids:
            opts.append({"value": f"station:{sid}", "label": NAME_OF.get(sid, sid), "group": "Station", "ids": [sid]})
    return opts


def _first(query, key, default=None):
    v = (query or {}).get(key)
    if isinstance(v, list):
        v = v[0] if v else None
    return default if v in (None, "") else v


def _date(text, default: date) -> date:
    try:
        return date.fromisoformat(str(text))
    except (TypeError, ValueError):
        return default


def parse_settings(query, today: date) -> dict:
    """The form's settings, with the proposal (15 minutes from the first change, same-day rule on) as the default."""
    submitted = _first(query, "go") is not None
    end_day = _date(_first(query, "to"), today)
    start_day = _date(_first(query, "from"), end_day - timedelta(days=DEFAULT_DAYS - 1))
    if start_day > end_day:
        start_day, end_day = end_day, start_day
    try:
        hold = min(MAX_HOLD, max(0, int(_first(query, "hold", 15))))
    except ValueError:
        hold = 15
    window = _first(query, "window", "first")
    content = _first(query, "content", "new")
    opts = {o["value"]: o for o in subscription_options()}
    sub = _first(query, "sub", "all")
    return {
        "from": start_day, "to": end_day, "hold": hold,
        "window": window if window in ("first", "restart") else "first",
        "same_day": (_first(query, "same_day") == "on") if submitted else True,
        "content": content if content in ("new", "running") else "new",
        "sub": sub if sub in opts else "all",
    }


MAX_PREVIEWS = 100   # each preview carries a whole email's HTML; a long range keeps the page light
PREVIEW_UNSUB = "https://bwtf-sf.vercel.app/unsubscribe?t=00000000-0000-0000-0000-000000000000"   # a real email has a token; this one goes nowhere


def preview(email: Email, zone: str | None = None, still: list[tuple[str, str]] | None = None) -> dict:
    """What the subscriber would see: subject, text message and HTML, from the live renderer's Python port
    (features/alerts/render._fallback, held byte-for-byte to the SQL bwtf_render_alert by
    db/scripts/test_render_parity.py), stamped with the email's send time. Local, so a range of
    emails costs no database calls.

    ``still`` (the "running list" idea, held): the subscriber's other stations still posted or discharging,
    added as a "Still in effect" section after the new alerts. That section is a mock-up added here; the live
    template doesn't have it."""
    from features.alerts.render import STAMP_FORMAT, _fallback
    transitions = [{"station_id": sid, "station_name": name, "to": to} for sid, (name, to) in email.worst.items()]
    out = _fallback(transitions, False, zone, PREVIEW_UNSUB, email.send_at.astimezone(PACIFIC).strftime(STAMP_FORMAT))
    return with_still_in_effect(out, still) if still else out


_STILL_ANCHOR = '<p style="margin:14px 0 0;font-size:13.5px;'   # the template's "From SFPUC" paragraph, after the station cards
_STILL_COLOR = {"cso": "#b5310a", "posted": "#d4763a"}


def with_still_in_effect(msg: dict, still: list[tuple[str, str]]) -> dict:
    """The running-list mock-up: a compact "Still in effect" list in the HTML, text and SMS, in the template's colours."""
    from html import escape
    rows = "".join(
        '<tr><td style="padding:6px 0;border-top:1px solid #E3EBF2;font-size:14px;color:#26272a;">'
        f'{escape(name)}</td><td align="right" style="padding:6px 0;border-top:1px solid #E3EBF2;font-size:12px;'
        f'font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:{_STILL_COLOR[to]};">'
        + ("Sewage discharge" if to == "cso" else "Posted") + "</td></tr>"
        for name, to in still)
    block = ('<div style="margin:16px 0 0;font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;'
             f'color:#54576F;">Still in effect &middot; {len(still)}</div>'
             f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:4px 0 0;">{rows}</table>')
    html = msg["html_body"]
    html = html.replace(_STILL_ANCHOR, block + _STILL_ANCHOR, 1) if _STILL_ANCHOR in html else html
    lines = "\n".join(f"- {name}: " + ("sewage discharge." if to == "cso" else "posted.") for name, to in still)
    text = msg["text_body"].replace("\n\nFrom SFPUC:", f"\n\nStill in effect:\n{lines}\n\nFrom SFPUC:", 1)
    brief = "; ".join(f"{name} ({'discharge' if to == 'cso' else 'posted'})" for name, to in still)
    sms = msg["sms_text"].replace(". Live status:", f". Still in effect: {brief}. Live status:", 1)[:320]
    return {**msg, "html_body": html, "text_body": text, "sms_text": sms}


def status_at(changes: list[Change], station_ids, when: datetime) -> dict[str, tuple[str, str]]:
    """{station id: (name, status)} for the stations not clear at ``when``, from every change up to then."""
    wanted, state = set(station_ids), {}
    for c in changes:
        if c.at > when:
            break
        if c.station_id in wanted:
            state[c.station_id] = (c.station_name, c.to)
    return {sid: v for sid, v in state.items() if v[1] != "ok"}


def still_in_effect(changes: list[Change], station_ids, email: Email) -> list[tuple[str, str]]:
    """The subscriber's stations still posted or discharging when ``email`` goes out, other than the ones it's about;
    discharges first, as the template orders them."""
    own = email.worst
    rest = [v for sid, v in status_at(changes, station_ids, email.send_at).items() if sid not in own]
    return sorted(rest, key=lambda v: (v[1] != "cso", v[0]))


def run(changes: list[Change], settings: dict) -> dict:
    opts = {o["value"]: o for o in subscription_options()}
    ids = opts[settings["sub"]]["ids"]
    zone = ZONES[settings["sub"].split(":", 1)[1]].label if settings["sub"].startswith("zone:") else None
    start = datetime.combine(settings["from"], time.min, PACIFIC)
    end = datetime.combine(settings["to"] + timedelta(days=1), time.min, PACIFIC)
    now = simulate(changes, ids, start, end)
    plan = simulate(changes, ids, start, end, settings["hold"], settings["window"], settings["same_day"])
    days = []
    d = settings["from"]
    while d <= settings["to"]:
        days.append({"day": d, "now": now["by_day"].get(d, 0), "plan": plan["by_day"].get(d, 0)})
        d += timedelta(days=1)
    # one list in time order: the proposal's emails, and the discharges the same-day rule skipped
    running = settings.get("content") == "running"
    rows = []
    for i, e in enumerate(plan["emails"]):
        still = still_in_effect(changes, ids, e) if running else []
        rows.append({"kind": "email", "at": e.send_at, "email": e, "still": still,
                     "preview": preview(e, zone, still) if i < MAX_PREVIEWS else None})
    rows += [{"kind": "skipped", "at": c.at, "change": c} for c in plan["skipped"]]
    rows.sort(key=lambda r: r["at"])
    return {"now": now, "plan": plan, "days": days, "rows": rows, "label": opts[settings["sub"]]["label"],
            "previews_cut": max(0, len(plan["emails"]) - MAX_PREVIEWS)}


def load_changes() -> tuple[list[Change], str | None]:
    """The timeline's history as changes, and a note when it's unavailable or cut short."""
    from shared import supabase as sb
    from features.cso_history import page as H
    if not sb.is_configured():
        return [], "Alert history isn't available in this environment (no database configured)."
    try:
        rows = H._fetch_event_rows()
    except Exception as exc:  # noqa: BLE001 — a flaky backend shows a note, not an error page
        return [], f"Alert history is temporarily unavailable ({type(exc).__name__})."
    note = None
    if len(rows) >= H._ROW_LIMIT:
        note = f"The alert log returned its limit of {H._ROW_LIMIT} rows, so the newest changes may be missing."
    windows = H.build_station_windows(rows, {}, datetime.now(timezone.utc))
    return changes_from_windows(windows), note


def fmt_time(dt: datetime) -> str:
    p = dt.astimezone(PACIFIC)
    return p.strftime("%a %-m/%-d %-I:%M %p")


def render_page(changes: list[Change], settings: dict, note: str | None = None) -> str:
    result = run(changes, settings)
    since = min((c.at for c in changes), default=None)
    return render_template("alerts/email_sim.html", s=settings, r=result, note=note,
                           options=subscription_options(), labels=STATUS_LABEL, fmt_time=fmt_time,
                           history_since=fmt_time(since) if since else None)


def handle_page(query, body):
    from shared.clock import today_pacific
    changes, note = load_changes()
    settings = parse_settings(query, today_pacific())
    return 200, "text/html; charset=utf-8", render_page(changes, settings, note).encode()


GET_ROUTES = {"/alerts/email-sim": handle_page}
