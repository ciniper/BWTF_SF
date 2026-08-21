# San Francisco Combined Sewer Discharge (CSD) Event Records — Compilation Notes

Compiled 2026-08-21 from SFPUC monthly Self-Monitoring Reports (SMRs) filed with the
SF Bay Regional Water Quality Control Board (Region 2), retrieved as PDF attachments
from CIWQS. Purpose: ground-truth training labels for the BWTF CSO forecaster
(replaces the proxy label in `features/forecast/src/models/train.py`).

## Files

| File | Rows | What it is |
|---|---|---|
| `sf_csd_events.csv` | 1,007 events | Per-event records, modern format (date, outfall, start time, duration, volume) |
| `sf_csd_monthly_coverage.csv` | 308 facility-months | Month-by-month coverage/status grid — use this to distinguish "no event" from "no data" |
| `sf_csd_events_bayside_legacy_2013_2016.csv` | 115 rows | Bayside legacy format (2013 – Sep 2016): per-day discharge hours + count; **no volumes** |

## Coverage (per-event with volumes: `sf_csd_events.csv`)

| System | NPDES permit | Outfalls | Coverage |
|---|---|---|---|
| Oceanside / Westside (Ocean Beach side) | CA0037681 (Order R2-2019-0028; before Nov 2019 R2-2009-0062) | CSD-001…007 | **Jan 2018 – Oct 2025** |
| Southeast / Bayside | CA0037664 | CSD-009…043 (29 points, 4 basins) | **Oct 2016 – Oct 2025** |

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
3. **Nov 2025 – present:** monthly SMR documents exist in the eSMR system but their
   attachments are not yet exposed in CIWQS's public report (drilldown pages return
   empty). Re-run the pipeline in a few months.
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
(CIWQS); eSMR analytical datasets on data.ca.gov are datastore-enabled (SQL API),
column `smr_document_id` links analytical rows to the monthly report documents.
