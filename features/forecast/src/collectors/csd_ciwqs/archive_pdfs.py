"""Archive the SMR attachments behind the CSD record, with sizes and checksums.

    python archive_pdfs.py [--from-year 2013] [--all-attachments]

Reads ``attachment_index.json`` (harvest_index.py) in the working directory and
downloads the summary-type attachments (the same rule download_pdfs.py uses:
attType 2, "wet weather", "SMR/DMR") into ``pdfs/`` as
``<facility>_<YYYY-MM>_<documentID>_<attachmentID>_<name>``. Writes

- ``pdf_manifest.csv`` — one row per downloaded file: facility, year, month,
  document_id, attachment_id, att_type, name, file, bytes, sha256, fetched_at,
  and ``parsed`` = whether sf_csd_events.csv cites this file as a source.
- ``attachments_all.csv`` — every attachment CIWQS lists for every document
  (downloaded or not), so the manifest can be judged against the full filing.

Re-runnable: a file already present with a plausible size is not re-fetched
(its row is still written, from disk). ``--all-attachments`` archives the
toxicity / shoreline / DMR attachments too (three times the volume).
The PDFs are gitignored; the two CSVs are meant to be committed next to the
record so the archive can be rebuilt or audited.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

RETRIEVER = "https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/PublicAttachmentRetriever"
UA = {"User-Agent": "Mozilla/5.0 (research; CSD records compilation)"}
MONTHS = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                            "August", "September", "October", "November", "December"])}
EVENTS = Path(__file__).resolve().parents[3] / "data" / "csd" / "sf_csd_events.csv"
MIN_BYTES = 1000   # below this the servlet returned an error page, not a PDF


def want(a: dict) -> bool:
    n = a["name"].lower()
    # "\bww\b": two Bayside months were filed as "... WW Report.pdf" and missed until 2026-10-06
    return a["attType"] == "2" or "wet weather" in n or bool(re.search(r"\bww\b|smr[- ]?dmr", n))


def parsed_sources() -> set[str]:
    if not EVENTS.exists():
        return set()
    with EVENTS.open() as f:
        return {row["source_document"].strip() for row in csv.DictReader(f)}


def month_of(report_name: str) -> int | None:
    m = re.search(r"for (\w+) (\d{4})", report_name)
    return MONTHS.get(m.group(1)) if m else None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str]) -> None:
    from_year = int(argv[argv.index("--from-year") + 1]) if "--from-year" in argv else 0
    everything = "--all-attachments" in argv
    idx = json.load(open("attachment_index.json"))
    parsed = parsed_sources()
    os.makedirs("pdfs", exist_ok=True)
    sess = requests.Session()
    sess.headers.update(UA)

    all_rows, rows = [], []
    n_fetched = n_skipped = n_err = 0
    for d in sorted(idx, key=lambda d: (int(d["year"]), month_of(d["report_name"]) or 0, d["facility"])):
        year, mon = int(d["year"]), month_of(d["report_name"])
        for a in d["attachments"]:
            name = a["name"].strip()
            selected = (everything or want(a)) and year >= from_year
            all_rows.append({"facility": d["facility"], "year": year, "month": mon, "document_id": d["document_id"],
                             "attachment_id": a["attachmentID"], "att_type": a["attType"], "name": name,
                             "selected": int(selected), "parsed": int(name in parsed)})
            if not selected:
                continue
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
            fn = Path("pdfs") / (f"{d['facility']}_{year}-{mon:02d}_{d['document_id']}_{a['attachmentID']}_{safe}" if mon
                                 else f"{d['facility']}_{year}_{d['document_id']}_{a['attachmentID']}_{safe}")
            if fn.exists() and fn.stat().st_size >= MIN_BYTES:
                n_skipped += 1
            else:
                try:
                    r = sess.get(RETRIEVER, params={"parentID": a["parentID"], "attachmentID": a["attachmentID"],
                                                    "attType": a["attType"]}, timeout=180)
                    r.raise_for_status()
                    fn.write_bytes(r.content)
                    n_fetched += 1
                    if n_fetched % 25 == 0:
                        print(f"{n_fetched} fetched...", flush=True)
                    time.sleep(0.2)
                except Exception as e:  # noqa: BLE001
                    n_err += 1
                    print("ERR", fn.name, e, flush=True)
                    continue
            size = fn.stat().st_size
            head = fn.open("rb").read(5)
            rows.append({"facility": d["facility"], "year": year, "month": mon, "document_id": d["document_id"],
                         "attachment_id": a["attachmentID"], "att_type": a["attType"], "name": name, "file": str(fn),
                         "bytes": size, "is_pdf": int(head == b"%PDF-"), "sha256": sha256_of(fn),
                         "fetched_at": datetime.fromtimestamp(fn.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                         "parsed": int(name in parsed)})

    with open("pdf_manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["file"])
        w.writeheader()
        w.writerows(rows)
    with open("attachments_all.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    total = sum(r["bytes"] for r in rows)
    print(f"DONE {n_fetched} fetched, {n_skipped} already present, {n_err} errors; "
          f"{len(rows)} files, {total / 1e6:.1f} MB; {len(all_rows)} attachments listed", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
