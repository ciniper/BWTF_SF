# CIWQS CSD-event scraping pipeline

Offline pipeline that produced `features/forecast/data/csd/sf_csd_events.csv`
(per-event SF combined sewer discharge records from SFPUC monthly
self-monitoring reports filed with the SF Bay Regional Water Board).
Full provenance and data dictionary: `features/forecast/data/csd/NOTES.md`.

Not part of the deployed app — run locally, on demand (roughly quarterly, to
pick up newly published months). Needs `requests`, `pdfplumber` (not in the
app's `requirements.txt` on purpose).

## Run order

```
python list_documents.py --only-new   # CIWQS eSMR party search -> smr_documents.json (months not yet in the coverage grid)
python harvest_index.py    # smr_documents.json -> CIWQS drilldowns -> attachment index
python download_pdfs.py    # fetch SMR PDFs via CIWQS PublicAttachmentRetriever (no auth)
python batch_parse.py      # extract "CSD Summary" tables (parse_csd.py) from every PDF
python aggregate.py        # dedupe/normalize -> sf_csd_events.csv + coverage grid + QA report + refresh_manifest.json
#   (stops before writing on an outfall id shared/outfalls.py lacks, or a report basin it disagrees with: registry_check.py)
#   → append the CSVs to features/forecast/data/csd/ and copy refresh_manifest.json to data/csd/manifest.json (the Ledger shows refreshed_at)
python parse_old_sep.py    # optional: legacy Bayside format (2013 - Sep 2016), hours/counts only
```

Pre-modern record (2026-10-06): the daily-total reports Mar 2011 → Sep 2016 (Bayside) /
Dec 2017 (Westside) are transcribed into `data/csd/pre2018/` — text PDFs parsed, scans read by
eye, every row with its source file and page. They are fixed history, so there is no refresh:
`build_pre2018.py` rebuilds the published CSVs from `pre2018/transcription/`, and
`qc_pre2018.py` reruns the checks into `qc_summary.json` (shown on `/discharges/reporting`).
See `data/csd/pre2018/NOTES.md`.

Archive step (2026-09-29, `archive_pdfs.py`): after `harvest_index.py`, run
`python archive_pdfs.py --from-year 2013` in `features/forecast/data/csd/pdf_archive/`
to keep the summary-type attachments themselves (the evidence behind every row)
under `pdf_archive/pdfs/` (gitignored, ~1 GB) with `pdf_manifest.csv` (size,
sha256, whether the record cites the file) and `attachments_all.csv` (every
attachment CIWQS lists, downloaded or not) committed beside the record. It skips
files already present, so a refresh only fetches the new quarter.

Run from a scratch directory with this directory on `PYTHONPATH` (batch_parse
imports parse_csd); the scripts read/write their working files where they run.
For a refresh, include one already-covered month as a control (its rows must
come out identical), then **append** the new event rows to
`features/forecast/data/csd/sf_csd_events.csv` (date-sorted) and the new
facility-months to `sf_csd_monthly_coverage.csv` (sorted facility, year, month)
after reviewing `qa_report.json`. Then run
`venv/bin/python features/forecast/src/models/train_v4.py --rescore --promote`
so the Model check scorecard gains the new months as post-training days (the
served models scored on days they never saw; no retraining). Last refresh:
2026-09-24 (Nov 2025 – Jul 2026 + Bayside Aug 2026; rescored through Aug 17 2026).

Step 0 reads CIWQS's own document listing (eSMR At-A-Glance search by party
name), not the data.ca.gov analytical datastore: the datastore lags and skipped
SFPUC's Nov–Dec 2025 entirely even though both plants had filed. `--datastore`
keeps the old route as a cross-check.

Key identifiers: CIWQS facility place IDs — Oceanside plant `256498`,
Southeast plant `256499`. Facilities file monthly; attachments appear in the
public eSMR At-A-Glance drilldown
(`PublicReportEsmrAtGlanceServlet?reportID=2&isDrilldown=true&documentID=<id>`).

Known source-data quirks the parser/aggregator already handle: stale TOTAL
cells in SFPUC's spreadsheet template (trust daily rows — e.g. Jan 2026 Sea
Cliff #2's TOTAL omits the Jan 4 event), misfiled/misnamed attachments, h:mm
durations, `<0.01` volume qualifiers, second events on the same day rendered as
offset rows, and the Westside page header changing from "Oceanside Basin CSD
Summary" to "Westside CSD Summary" (May 2026 on).
