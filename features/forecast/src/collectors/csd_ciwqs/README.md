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
python harvest_index.py    # eSMR datastore (data.ca.gov) -> monthly SMR documentIDs -> attachment index
python download_pdfs.py    # fetch SMR PDFs via CIWQS PublicAttachmentRetriever (no auth)
python batch_parse.py      # extract "CSD Summary" tables (parse_csd.py) from every PDF
python aggregate.py        # dedupe/normalize -> sf_csd_events.csv + coverage grid + QA report
python parse_old_sep.py    # optional: legacy Bayside format (2013 - Sep 2016), hours/counts only
```

Scripts read/write their working files in the directory they run in; copy the
resulting `sf_csd_events.csv`, `sf_csd_monthly_coverage.csv` into
`features/forecast/data/csd/` after reviewing `qa_report.json`.

Key identifiers: CIWQS facility place IDs — Oceanside plant `256498`,
Southeast plant `256499`. Facilities file monthly; attachments appear in the
public eSMR At-A-Glance drilldown
(`PublicReportEsmrAtGlanceServlet?reportID=2&isDrilldown=true&documentID=<id>`).

Known source-data quirks the parser/aggregator already handle: stale TOTAL
cells in SFPUC's spreadsheet template (trust daily rows), misfiled/misnamed
attachments, h:mm durations, `<0.01` volume qualifiers, second events on the
same day rendered as offset rows.
