"""Brevo inbound payloads to private archive rows; no remote fetches or HTML rendering."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

EXPECTED_SENDER = "beachwaterquality@sfwater.org"  # Chase confirmed 2026-11-21
MAX_BODY = 100_000
MAX_ITEMS = 20


class InvalidPayload(ValueError):
    pass


def string(value, field, limit, *, optional=False):
    if value is None and optional:
        return ""
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise InvalidPayload(f"Invalid {field}.")
    return value.strip()


def address(value):
    if isinstance(value, dict):
        value = value.get("Address")
    value = string(value, "email address", 320).casefold()
    if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", value):
        raise InvalidPayload("Invalid email address.")
    return value


class TextFromHTML(HTMLParser):
    """Keep readable text only. Never load images, links, or embedded objects."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head", "iframe", "object"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head", "iframe", "object"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def readable_text(item):
    text = string(item.get("RawTextBody"), "text body", MAX_BODY, optional=True)
    html = string(item.get("RawHtmlBody"), "HTML body", MAX_BODY, optional=True)
    if not text and html:
        parser = TextFromHTML()
        parser.feed(html)
        text = "".join(parser.parts)
    if not text:
        text = string(item.get("ExtractedMarkdownMessage"), "message", MAX_BODY, optional=True)
    return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()


def parse_payload(payload, recipient, senders, *, now=None, provider="brevo"):
    """Validate the entire batch before any write. Other envelope recipients are ignored.

    Sender matching is a review hint, NOT proof of authorship. All rows start
    unreviewed; forwarding confirmations and unknown senders are quarantined.
    Raw payloads (including original headers) stay private for source review.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise InvalidPayload("Expected an items array.")
    if not 1 <= len(payload["items"]) <= MAX_ITEMS:
        raise InvalidPayload(f"Expected 1–{MAX_ITEMS} messages.")
    now = now or datetime.now(timezone.utc)
    rows, ignored = [], 0
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise InvalidPayload("Invalid message.")
        recipients = item.get("Recipients")
        if not isinstance(recipients, list) or not 1 <= len(recipients) <= 100:
            raise InvalidPayload("Envelope recipients are required.")
        recipients = sorted({address(value) for value in recipients})
        if recipient not in recipients:
            ignored += 1
            continue
        sender = address(item.get("From"))
        subject = string(item.get("Subject"), "subject", 1000, optional=True) or "Untitled email"
        message_id = string(item.get("MessageId"), "Message-ID", 1000, optional=True)
        sent_raw = string(item.get("SentAtDate"), "sent date", 200, optional=True)
        body = readable_text(item)
        warnings = []
        if sender not in senders:
            warnings.append("Sender does not match SFPUC. This may be a forwarded message or a mailbox verification email.")
        sent_at = None
        if sent_raw:
            try:
                dt = parsedate_to_datetime(sent_raw)
                if dt.tzinfo is None or not 1970 <= dt.year <= 2100 or dt > now + timedelta(days=1):
                    raise ValueError("Unreliable date")
                sent_at = dt.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError):
                warnings.append("The email date could not be trusted; receipt time is shown instead.")
        if not body:
            warnings.append("No readable body. Check the original message before publishing.")
        attachments = item.get("Attachments") or []
        if not isinstance(attachments, list) or len(attachments) > 100:
            raise InvalidPayload("Invalid attachments.")
        if attachments:
            warnings.append("Attachments are not downloaded. Review their contents separately if needed.")
        # Do not use receipt time or provider recipient UUIDs in the content hash:
        # redelivery must not create another bulletin or move the first receipt time.
        content = {"sender": sender, "subject": subject, "sent": sent_raw,
                   "text": item.get("RawTextBody"), "html": item.get("RawHtmlBody"),
                   "extracted": item.get("ExtractedMarkdownMessage"),
                   "attachments": [{k: a.get(k) for k in ("Name", "ContentType", "ContentLength", "ContentID")}
                                   for a in attachments if isinstance(a, dict)]}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        source_key = hashlib.sha256(f"{provider}\n{recipient}\n{message_id or digest}".encode()).hexdigest()
        rows.append({"source_key": source_key, "content_hash": digest,
                     "source_message_id": message_id or None, "sender": sender,
                     "recipient": recipient, "subject": subject, "body_text": body,
                     "sent_at": sent_at, "received_at": now.isoformat(),
                     "sender_matches": sender in senders, "warnings": warnings,
                     "raw_payload": item, "provider": provider})
    return rows, ignored
