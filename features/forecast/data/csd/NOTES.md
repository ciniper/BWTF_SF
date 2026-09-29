# San Francisco Combined Sewer Discharge (CSD) Event Records — Compilation Notes

Compiled 2026-08-21 from SFPUC monthly Self-Monitoring Reports (SMRs) filed with the
SF Bay Regional Water Quality Control Board (Region 2), retrieved as PDF attachments
from CIWQS. Purpose: ground-truth training labels for the BWTF CSO forecaster
(replaces the proxy label in `features/forecast/src/models/train.py`).

Refreshed 2026-09-24 with Nov 2025 – Jul 2026 for both plants plus Bayside Aug 2026
(97 events, 19 facility-months; Oct 2025 re-parsed as a control and came out
identical). See "Refresh log" at the end.

## Files

| File | Rows | What it is |
|---|---|---|
| `sf_csd_events.csv` | 1,104 events | Per-event records, modern format (date, outfall, start time, duration, volume) |
| `sf_csd_monthly_coverage.csv` | 327 facility-months | Month-by-month coverage/status grid — use this to distinguish "no event" from "no data" |
| `sf_csd_events_bayside_legacy_2013_2016.csv` | 115 rows | Bayside legacy format (2013 – Sep 2016): per-day discharge hours + count; **no volumes** |

## Coverage (per-event with volumes: `sf_csd_events.csv`)

| System | NPDES permit | Outfalls | Coverage |
|---|---|---|---|
| Oceanside / Westside (Ocean Beach side) | CA0037681 (Order R2-2019-0028; before Nov 2019 R2-2009-0062) | CSD-001…007 | **Jan 2018 – Jul 2026** (continuous; Aug 2026 not filed yet) |
| Southeast / Bayside | CA0037664 | CSD-009…043 (29 points, 4 basins) | **Oct 2016 – Aug 2026** (continuous) |

Wet years total 1,300–2,400 MG/yr across both systems, consistent with EPA's
"~1.8 billion gallons/yr average since 2016" (2024 federal complaint) — good
external validation.

## Known gaps

1. **Oceanside per-event data Jan 2013 – Dec 2017 does not exist on CIWQS.**
   Under the 2009 permit, SFPUC's monthly filings reported only a *count* of CSODs
   per month (cover-letter narrative; scanned PDFs). Per-event Westside tables begin
   with the Jan 2018 SMR. The underlying telemetry exists at SFPUC (their tables cite
   TELOG/SCADA data tags) → see `records_request_draft.md`.
2. **Bayside per-outfall volumes before Oct 2016** — legacy reports give per-day
   discharge *hours* and counts per outfall (or outfall group), volumes only as
   basin-month estimates. Legacy CSV covers this era. SFPUC demonstrably can
   regenerate modern-format tables for old months (in Sep 2023 they filed revised
   "FINAL WW Summary" tables for Oct 2016 – Oct 2017), so a records request could
   extend volumes further back.
3. **The data.ca.gov eSMR analytical datastore is not a reliable document index.**
   SFPUC's Nov and Dec 2025 SMRs were filed on time (received 12/24–12/29/2025 and
   01/27–01/28/2026) with attachments on CIWQS, yet as of 2026-09-24 neither month has
   a single analytical row in the state's 2025 or 2026 export (other Region 2 plants'
   Nov–Dec rows are there). Enumerate documents from CIWQS itself instead: the eSMR
   At-A-Glance servlet has a search form (`inCommand=reset&reportID=2`, then
   `reportID=1&firstRun=Y&partyName=San Francisco Public Utilities Commission&runReport=Run Report`,
   then `reportID=1&newPageNumber=0&newPageSize=5000` in the same session) that
   lists every SFPUC document with report name, period, due date and date received.
   `list_documents.py` does exactly this; `--datastore` keeps the old route as a
   cross-check. Document ids are a statewide sequence and the two plants' ids for
   the same month can be tens of thousands apart, so never guess them.
   **Aug 2026 – present:** not yet filed (SMRs land ~4–6 weeks after month end);
   re-run quarterly.
4. Dec 2016 Bayside: the modern revision was misfiled on CIWQS (the Nov-2016 file was
   attached twice), so the 23 Dec-2016 events were manually transcribed from the
   scanned original (flagged `manual_transcription`).

## Schema: `sf_csd_events.csv`

- `event_date` — date the discharge started (a discharge crossing midnight is one row
  on its start date; SFPUC logs a second same-day row for a second event, which is preserved).
- `facility` — `Oceanside (CA0037681)` or `Southeast/Bayside (CA0037664)`.
- `outfall_id` — permit discharge point, e.g. `CSD-001`, `CSD-031A`.
- `outfall_name`, `basin`, `receiving_water` — canonical names; receiving water is a
  coarse basin-level label (Oceanside basins → Pacific Ocean; North Shore → northern
  bay waterfront; Central #1 → Mission Creek/China Basin; Central #2 → Islais Creek;
  Southeast → Yosemite Slough/Candlestick area).
- `start_time` — as reported (`2:09 AM`; Dec 2016 manual rows use 24h `15:00`).
- `duration_min` — minutes. `duration_flag`: `reported_hmm` (source printed h:mm),
  `from_monthly_total` (single-event month backfilled from the table's TOTAL row),
  `manual_transcription`, or empty.
- `volume_MG` — million gallons. `volume_qualifier` `<` = reported as less-than
  (e.g. `<0.01`; value column holds the bound, not an estimate).
- `source_document`, `ciwqs_document_id`, `report_period` — provenance. Retrieve the
  source PDF via the CIWQS eSMR drilldown:
  `https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/PublicReportEsmrAtGlanceServlet?reportID=2&isDrilldown=true&documentID=<id>`
  (attachment links on that page; downloads need no session).

## `sf_csd_monthly_coverage.csv` statuses

- `events_parsed` — CSD table found, ≥1 event row extracted.
- `table_present_zero_events` — CSD table present, genuinely zero events.
- `no_table_stated_no_discharge` — no table, but the SMR states no CSDs occurred
  (verified for Feb 2018 by manual OCR of the scanned letters).
- `no_event_table_found` — no per-event table in the filing (all remaining cases are
  pre-2018 Oceanside / pre-Oct-2016 Bayside legacy-era months — see gaps above).

For label generation: treat `events_parsed` dates as positives, and months with
`table_present_zero_events` / `no_table_stated_no_discharge` as verified negatives.

## Data-quality caveats

- **Stale TOTAL cells in SFPUC's spreadsheet template**: Central Basin #1 CSD-24
  shows a hard-coded `300 min / 15.34 MG` monthly TOTAL in several zero-event months
  (2017-08, 2017-09, 2019-09, 2019-11, 2024-07), and Feb-2019 totals persist in the
  Mar/Apr-2019 sheets. Daily rows (what the CSV uses) are correct; only the printed
  TOTAL rows are stale. 20 QA flags remain in `qa_report.json`, all of this type.
- Bayside Sansome (015) values in Jan 2023 are estimates from the Jackson St sensor
  (Sansome sensor offline); Lincoln/Vicente Jan 2023 estimated from Vicente West Box
  levels; Marin (032) sensor stolen in Jan 2023 (Selby data used); Mariposa Jan 2023
  volume modeled (CCSF19 H&H). SFPUC notes such substitutions in table footnotes —
  they are *not* carried per-row in the CSV.
- Legacy CSV outfall groups: Central Basin outfalls were metered as groups in
  2013-2016 (e.g. "18, 19, 22, 23, 24, 25, 26, 27, 28" = Mission Creek system).
- Amended filings: where a month has multiple filed versions, the latest attachment
  (highest CIWQS attachment id — includes the Sep-2023 revisions) was used.

## Method / reproducibility

Pipeline scripts in the session scratchpad (`csd/` directory):
`harvest_index.py` (enumerate monthly SMR documentIDs from the data.ca.gov eSMR
datastore + scrape CIWQS drilldown attachment lists) → `download_pdfs.py` (fetch
457 PDFs via `PublicAttachmentRetriever`, no auth needed) → `parse_csd.py` +
`batch_parse.py` (pdfplumber, x-anchor column mapping + y-nearest date-row binding)
→ `aggregate.py` (dedupe, normalize, QA totals cross-check, coverage grid) →
`parse_old_sep.py` (legacy Bayside format).

Key identifiers: Oceanside facility place ID `256498`, Southeast `256499`
(CIWQS); party name "San Francisco Public Utilities Commission" (CIWQS party
43055). Step 0, `list_documents.py`, lists SFPUC's monthly SMR documents from the
CIWQS eSMR At-A-Glance party search (see gap 3 for the request sequence) and
writes `smr_documents.json`; the data.ca.gov eSMR datastore (`smr_document_id`
column, SQL API) remains available via `--datastore` as a cross-check.

## PDF archive (2026-09-29)

The attachments behind the record are kept locally under `pdf_archive/pdfs/`
(gitignored — 487 files, 477 MB) and described by two committed manifests:
`pdf_archive/pdf_manifest.csv` (one row per archived file: facility, month,
CIWQS document and attachment ids, bytes, sha256, and `parsed` = the record
cites it) and `pdf_archive/attachments_all.csv` (every attachment CIWQS lists
for every SFPUC monthly SMR, 1,646 across 372 documents 2011 → Aug 2026,
downloaded or not). `pdf_archive/ciwqs_documents.csv` is the party-search
listing with due/received dates. Rebuild or extend with
`src/collectors/csd_ciwqs/archive_pdfs.py --from-year 2013` after
`harvest_index.py`; files already present are not re-fetched.

What was archived: the summary-type attachments (`download_pdfs.py`'s rule —
attType 2, "wet weather", "SMR/DMR") from 2013 on: monthly SMR bodies (213 files,
365 MB — the 2013–2017 Oceanside ones are 8–11 MB scans), cover letters (146,
50 MB), flow / WW summaries (40, 10 MB), drainage narratives (12, 23 MB), and
a few quarterlies and lab sheets. All 74 files the events CSV cites are present
(31 MB between them; the Dec 2016 Bayside row cites the drainage PDF it was
transcribed from, attachment 1883351). One archived attachment is a `.doc`, not
a PDF. Not archived: toxicity, shoreline-bacteria, DMR and PCB attachments
(1,076 files; sizes unknown) — `--all-attachments` would take them.

Completeness check (2026-09-29): every one of the 327 grid months has exactly
one CIWQS document and none has been replaced (all 74 cited document ids are
still the listed filing); re-parsing the 47 archived attachments the record does
not cite found no discharge row the record lacks (38 have no CSD table — cover
letters, and the 2021 "CSD Data" sheets are discharge-sample chemistry, not
event tables; the 2016–17 Bayside originals were superseded by the Sep 2023
revisions the aggregator prefers, newest attachment id winning). The one filing
outside the grid is Oceanside **Aug 2026** (document 3124551, received
09/24/2026 — the day of the last refresh, after it ran): table present, zero
events. 44 documents from 2011–2012 predate the grid on purpose.

## Refresh log

- **2026-09-29** — no data change. Archived the source attachments (see "PDF archive")
  and re-checked completeness against CIWQS's full listing: Oceanside Aug 2026 (zero
  events) is the only filing not yet in the grid; add it at the Dec 2026 refresh or now.
- **2026-09-24** — Nov 2025 – Jul 2026, both plants, plus Bayside Aug 2026
  (received 09/23/2026, table present, zero events; Oceanside's Aug not filed
  yet) — 19 SMR documents, 42 PDFs. 97 events, no changes to earlier months.
  `aggregate.py` now writes the events CSV header even when a run has zero
  events (it crashed on the dry Aug 2026 run). The 2025-26 winter: **Nov 13 2025**
  (both plants: Westside CSD-001…004/006/007, North Shore CSD-009…017, Mission
  Creek, Islais), **Nov 17**, **Dec 22** (Lincoln 59 MG, Islais Creek North 48 MG),
  **Dec 24**, **Dec 25** (Division Street 103 MG — the largest single event in the
  record; Evans Ave posted Southeast), **Dec 26**, **Jan 5 2026** (Westside +
  Mission Creek + Islais), **Feb 16–19** Westside, **Apr 11** Westside + Mission
  Creek + Mariposa, **Apr 22** Sea Cliff #2. Mar, May, Jun, Jul: tables present,
  zero events (both plants); Feb Bayside zero. Two QA flags, both the stale-TOTAL
  quirk (Jan 2026 Sea Cliff #2 TOTAL omits the Jan 4 event; daily rows kept).
  Parser note: from May 2026 the Westside page header reads "Westside CSD Summary"
  instead of "Oceanside Basin CSD Summary" — `aggregate.py` maps both. Control:
  Oct 2025 re-parsed identical (4 events). Nov – Dec 2025 needed the CIWQS party
  search because the state datastore has no rows for them (gap 3).
