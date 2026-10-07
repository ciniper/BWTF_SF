# Discharge records before the modern format (Mar 2011 → Sep 2016 / Dec 2017)

Added 2026-10-06. SFPUC's monthly reports went online on CIWQS in March 2011. Until the
modern per-event tables (Bayside Oct 2016, Westside Jan 2018) they report **daily totals**.
These files hold those years. The Discharge Ledger shows them, with the differences spelled
out on `/discharges/reporting`. **The served forecast does not read them**: its training
labels, the Model check and the scoring protocol's truth use `../sf_csd_events.csv` +
`../sf_csd_monthly_coverage.csv` only. Candidate model sets can opt in to them as extra
**training** labels (2026-10-06): `src/collectors/csd_pre2018.py` reads them, through
`train_v4.build_dataset(record=...)`; `src/models/train_older_reports.py` fits the
candidates, and `SWAPS.md` ("Training on the older discharge reports") says how they are scored.

## Files

| file | what |
|---|---|
| `westside_daily_2011-03_2017-12.csv` | one row per Westside outfall per discharge day: hours (`duration_hours`, and `hours_as_printed`), volume MG, method, source file + page |
| `westside_monthly_coverage_2011-03_2017-12.csv` | all 82 months: `events` / `zero` / `stated_zero` (no table, annual report says 0) / `basin_days_esmr` (Dec 2012) with the printed month total and the row sum |
| `bayside_legacy_2011-03_2016-09.csv` | one row per Bayside outfall GROUP per discharge day: hours and event count, no volumes; `outfall_id` is the registry id for one-outfall groups and a range id (e.g. `CSD-018–028`) otherwise |
| `bayside_legacy_monthly_coverage_2011-03_2016-09.csv` | all 67 months, with event counts per basin parsed vs the annual report's |
| `stated_monthly_counts_2011-2017.csv` | SFPUC's own monthly counts: Bayside annual-report tables, Oceanside annual tables 2011–12 and cover letters 2013–17 |
| `acis_daily_rain_2011-2017.csv` | NOAA gauges 047772 + 047767 daily, for the rain check |
| `qc_summary.json` | results of every check (written by `qc_pre2018.py`; the reporting page reads it) |
| `transcription/` | the inputs: parsed text-layer rows, hand-read rows (each with file + page), month statuses, the eSMR series |

## How it was made

- Westside Mar 2011 – Oct 2012: text PDFs ("OSP Monthly WW Report" attachments, then the report bodies), parsed by column position.
- Westside Nov 2012 and Jan 2013 – Dec 2017: scanned; every month's daily-total column was checked on a contact sheet, and every month with a discharge read by eye in full, row totals and column totals checked.
- Westside Dec 2012: no per-outfall table was attached; the five discharge days and basin volumes come from the state's eSMR analytical data (location EFF-CSD), which matches the filed tables in 2011–2012.
- Bayside: `parse_old_sep.py` output, with the filing's month winning over a stale page header (Apr 2012 is headed "March"), and three scanned 2011 months read by eye. Feb 2014 and May 2013 were missed earlier because their attachments are named "WW Report"; the scraper filters now match that.
- The PDFs (annual reports, 2011–2012 monthly reports) are in the chapter's local data archive, folder `04_pre2018_discharge_records/`; the 2013–2017 scans are in its folder `01_`.

Rebuild and recheck from the repo root:

    python features/forecast/src/collectors/csd_ciwqs/build_pre2018.py
    python features/forecast/src/collectors/csd_ciwqs/qc_pre2018.py

## Checks (qc_summary.json)

Totals against printed totals; SFPUC's stated monthly counts; rain at the two gauges; the
State's BeachWatch postings; follow-up sampling at Pacheco / Vicente / Fort Funston; the
2016–17 Poo Bot feed archive; the eSMR flow series. Not yet done: a second independent
reading of every hand-read page.

## Caveats

- Daily totals, not events: a discharge across midnight is two rows; no start times.
- Dec 11 2014: Lake Merced, Vicente, Lincoln printed NA (unmeasured; `volume_flag = not_measured`); the printed month total omits the day.
- Jun 10 2015 Sea Cliff 1: a dry-weather equipment-failure spill (~1,000 gal), kept because SFPUC logged it.
- The eSMR EFF-CSD flow equals filed volumes in 2011–2013 and 2017+, not 2014–2016: a day marker only.
