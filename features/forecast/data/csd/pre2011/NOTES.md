# Discharge records before CIWQS (Jul 2004 – Aug 2010), from Kyle's records request

Added 2026-11-01. Kyle got these from SFPUC (zip "CSD History Jan 2000-2018", 99 PDFs). Despite
the name, nothing in it predates July 2004. The originals are in the chapter's data archive,
`~/Personal/BWTF_Data_Archive/07_kyle_csd_records_2004-2010/` (manifest with sha256 there).
**Nothing here is read by the site or the forecast yet.**

## What is in the zip

| what | months | format | status |
|---|---|---|---|
| Bayside monthly wet weather reports | Jul 2004 – Aug 2006 (22 months) | text PDFs, 4–7 pages | **new** |
| Westside (Oceanside) monthly self-monitoring reports | Sep 2006 – Aug 2010 (34 monthly reports + a Jul–Sep 2009 quarterly) | 38–71 pages; 2008 has a text layer, the rest are scans | **new** |
| Westside cover letter, December 2004 | 1 page | text | **new** (counts only) |
| "CSO Information FY 04-05" | Jul 2004 – Jun 2005 | one-page table of Bayside discharges per basin per month | **new** |
| Bayside wet weather reports 2011, 2013–2015 | 27 files | text | same files as CIWQS (byte-identical) |
| Westside SMRs Jan – Jul 2013 | 7 files | scans | 6 byte-identical to CIWQS; July 2013 a different copy of a dry month |
| Bayside SMR July 2011 | 45 pages | text | dry month, already in the record |
| page-1 extracts (Oct/Nov/Dec 2004) and two earlier drafts of Oct 2005 | 5 files | | duplicates |

`inventory.csv` lists every file with its plant, report month, page count, text layer and status.

## Files

| file | what |
|---|---|
| `bayside_daily_2004-07_2006-08.csv` | 55 rows on 25 days: one per Bayside outfall GROUP per discharge day, hours and count, no volumes. Same groups and ids as `../pre2018/bayside_legacy_*` |
| `westside_daily_2008-01_2010-02.csv` | 27 rows on 9 days: one per Westside outfall per discharge day. Hours; volumes from Feb 2009 on |
| `monthly_coverage_2004-2011.csv` | Jul 2004 → Feb 2011 (CIWQS starts Mar 2011), each plant and basin: `events` / `zero` (table read), `stated_only` (SFPUC's count, table not read or report missing), `no_report`; discharge days found vs SFPUC's count |
| `season_check.csv` | each wet season July–June: discharge days in the reports read, the months still missing, SFPUC's own season-to-date, and the permits' published season counts |
| `transcription/` | the inputs: parser output and its per-column total checks, the hand-read Westside rows and printed totals, SFPUC's stated counts, the published season tables |

Rebuild: `python features/forecast/src/collectors/csd_pre2011/build_pre2011.py` (reads only `transcription/`).
Reparse Bayside: `parse_bayside_2004.py --checks <csv> <the 22 monthly reports>` in the same folder.

## How it was read

- **Bayside:** the "Discharge Summary" pages are the same layout as 2011–2016: per day, per
  outfall group, discharge hours and a count. A new parser (`parse_bayside_2004.py`) places each
  number on the nearest day row and column pair; the 2011 parser reads line by line and lost
  cells printed a few points off their row (the whole South basin, in places). All 286 columns
  were checked against the printed Total row: 281 agree; two are 0.1-hour roundings (Sansome
  Dec 2004, Islais Apr 2006); the other three are the Sunnydale note below.
- **Westside:** cover letters were OCR'd for "This Month" and season-to-date counts, and checked
  by eye where the OCR garbled them. Only five months in the set had discharges: Jan 2008 (2),
  Feb 2009 (2), Mar 2009 (1), Jan 2010 (3), Feb 2010 (1). Their "Westside Drainage Area – Wet
  Weather Discharge Summary" tables were read by eye; every outfall's hours and volumes add up to
  the printed column totals, and from 2009 to the printed day totals too. The tables of the
  zero months were not read; those months rest on the cover letter and the unchanged season
  count.

## Checks

- **Against SFPUC's own counts:** every month agrees except Oct 2004 Central (2 discharge days
  found, SFPUC counts 3: Mission Creek's 2 + Mariposa's 1).
- **Against the permits' season tables** (2013 Bayside order, Table F-4; 2019 Oceanside order, Table F-3):
  - Bayside 2004–05: reports 4 / 15 / 1 (North Shore / Central / Southeast) = published 4 / 15 / 1.
  - Bayside 2005–06: reports 5 / 16 / 2, published 3 / 16 / 2. North Shore differs by 2.
  - Westside: the months in hand fit inside each season's published per-outfall counts.

## Quirks kept as printed

- **Oct 2004:** the table puts a Mariposa discharge on 10/19; the narrative says 10/19 had none
  and Mariposa discharged on 10/26.
- **Sunnydale, Jan–Mar 2006:** the Total row prints 5.9 hours with no rows behind it, three months
  running. Probably a spreadsheet formula left over from December 2005, whose report is missing.
- **Apr 2006:** Islais discharged 4/2 and 4/3 with one count: a new discharge needs six dry hours.
- **Oct 2005:** the discharge pages are titled "September 2005"; rows (none) take the filing month.
- **Westside Jan 2008:** hours and a "No." column, no volumes. Volumes start Feb 2009.
- **Westside Feb 2009:** Sea Cliff Brick Sewer (CSD-006) discharged 0.2 h, though the 2019 permit
  says CSD-006 was not monitored until telemetry in 2010.
- **Westside Jan 2010:** Lake Merced's volume equals its hours on both days (1.10, 4.52). Possibly copied.
- **Westside Feb 2010:** the page is printed sideways; Sea Cliff #1's 0.13 h sits between rows
  25 and 26 and the day-total column puts it on the 26th.
- **Mile Rock (CSD-004)** is printed "NOT OBSERVED" in every Westside table: not monitored then.
- **Basin names:** 2004–2006 reports call the Southeast basin "South" and list Evans and Hudson
  (CSD-037, 038) inside the "Islais Creek System" group, as in the 2011 reports.

## What is still missing

- **Westside:** every October–December (2006, 2007, 2008, 2009), January 2009, and Sep 2010
  onward; before Sep 2006 only the December 2004 count. These are the wettest months:
  Oct–Dec 2006 hold all 3 of that season's discharges, Oct–Dec 2007 2 of 4, Oct 2008 – Jan 2009
  1 of 4, Sep–Dec 2009 1 of 5.
- **Bayside:** December 2005 (counts known: 2 / 6 / 2) and everything from Sep 2006 to Feb 2011.
- Nothing before July 2004 for either system.
