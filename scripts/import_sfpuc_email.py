"""Import an original .eml into the private review queue. Dry-run by default.

    python -m scripts.import_sfpuc_email message.eml
    python -m scripts.import_sfpuc_email message.eml --apply

Uses the same source parser as the webhook. No email is sent or published.
"""
import argparse
import base64
import json
import os
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

from features.sfpuc_updates import parser, store
from shared import supabase as sb


def from_eml(data, recipient):
    if len(data) > 256 * 1024:
        raise ValueError("Original email exceeds the 256 KB import limit.")
    msg = BytesParser(policy=policy.default).parsebytes(data)
    plain = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    attachments = [{"Name": part.get_filename(), "ContentType": part.get_content_type(),
                    "ContentLength": len(part.get_payload(decode=True) or b""), "ContentID": part.get("Content-ID")}
                   for part in msg.iter_attachments()]
    return {"items": [{"MessageId": msg.get("Message-ID", ""), "From": {"Address": parseaddr(msg.get("From", ""))[1]},
                       "Recipients": [recipient], "Subject": msg.get("Subject", ""), "SentAtDate": msg.get("Date", ""),
                       "RawTextBody": plain.get_content() if plain else "",
                       "RawHtmlBody": html.get_content() if html else "", "Attachments": attachments,
                       "Headers": list(msg.raw_items()), "OriginalEMLBase64": base64.b64encode(data).decode(),
                       "ImportedManually": True}]}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("email", type=Path)
    cli.add_argument("--apply", action="store_true", help="Save privately; never publishes")
    args = cli.parse_args()
    try:
        recipient = parser.address(os.environ.get("SFPUC_EMAIL_RECIPIENT", ""))
        senders = {parser.address(s.strip()) for s in os.environ.get("SFPUC_EMAIL_SENDERS", parser.EXPECTED_SENDER).split(",")}
        with args.email.open("rb") as original:
            payload = from_eml(original.read(256 * 1024 + 1), recipient)
        rows, ignored = parser.parse_payload(payload, recipient, senders, provider="eml_import")
        rows[0]["warnings"].append("Manually imported. Capture time is the import time, not original delivery time.")
        if args.apply:
            if not sb.is_configured():
                cli.error("Supabase is not configured.")
            print(json.dumps(store.capture(rows, ignored)))
        else:
            print(json.dumps({"dry_run": True, "messages": len(rows), "sender_matches": rows[0]["sender_matches"],
                              "has_source_date": bool(rows[0]["sent_at"]), "warnings": rows[0]["warnings"]}))
    except (OSError, ValueError) as exc:
        cli.error(str(exc))
    except Exception:
        cli.error("Import failed; check storage configuration. No message was published.")


if __name__ == "__main__":
    main()
