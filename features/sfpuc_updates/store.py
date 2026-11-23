"""Only the email feature's own tables and RPCs are accessed here."""
from shared import supabase as sb


def capture(rows, ignored=0):
    return sb.rpc("sfpuc_capture_email_batch", {"messages": rows, "ignored_count": ignored})


def inbox(offset=0):
    return sb.select("sfpuc_email_inbox", {"select": "*", "order": "received_at.desc,id.desc",
                                         "limit": "25", "offset": str(offset)})


def detail(message_id):
    rows = sb.select("sfpuc_email_messages", {"select": "*", "id": f"eq.{message_id}", "limit": "1"})
    if not rows:
        return None, []
    conflicts = sb.select("sfpuc_email_messages", {"select": "id", "source_key": f"eq.{rows[0]['source_key']}",
                                                   "content_hash": f"neq.{rows[0]['content_hash']}", "limit": "1"})
    rows[0]["has_conflict"] = bool(conflicts)
    revisions = sb.select("sfpuc_email_reviews", {"select": "*", "message_id": f"eq.{message_id}",
                                                 "order": "revision.desc", "limit": "50"})
    return rows[0], revisions


def review(message_id, expected_revision, action, title, body, reviewer):
    return sb.rpc("sfpuc_review_email", {"message": message_id, "expected_revision": expected_revision,
                  "decision": action, "reviewed_title": title, "reviewed_text": body, "reviewer": reviewer})


def reviewed(offset=0):
    # View already restricts to the newest review being approved. No raw fields
    # are selected or serialized, even though the server role can read originals.
    return sb.select("sfpuc_email_reviewed", {"select": "id,title,body_text,sent_at,received_at,reviewed_at",
                    "order": "sort_at.desc,id.desc", "limit": "6", "offset": str(offset)})


def recent_runs():
    return sb.select("sfpuc_email_ingest_runs", {"select": "received_at,inserted_count,duplicate_count,ignored_count",
                                               "order": "received_at.desc", "limit": "1"})


def image_payloads(message_ids):
    if not message_ids:
        return {}
    rows = sb.select("sfpuc_email_messages", {"select": "id,raw_payload",
                     "id": "in.(" + ",".join(message_ids) + ")"})
    return {row["id"]: row["raw_payload"] for row in rows}
