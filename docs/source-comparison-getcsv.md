# Source Comparison: early results and shared dates

Branch: `design/source-comparison-getcsv`

Source Comparison now includes current, validated getCSV readings in the City
results and its history modal. It reuses the existing snapshot reader and
per-station, collection-date, indicator merge: valid DataSF results take
precedence, including multiple City samples on one day. Early results retain
qualifiers such as `<10` and `>200` and carry a getCSV label. Partial arrivals
do not stand in for a missing indicator. Stale or withdrawn early readings are
excluded by the existing snapshot reader; source failures are shown explicitly.

## Experimental shared-date view

`/compare?view=shared` selects each site's latest collection date with
Enterococcus results from both programs within the last 365 days, inclusive of
the first collection day. This is per site, not one date shared by every site.
Each program's highest reading on that date determines its comparison. A site
without a match shows “No shared result date”; it does not fall back to comparing
different days. The existing summary cards still count all same-day pairs in the
year, while the last two cards describe the selected table rows.

The default remains `/compare?view=latest`. Selecting another indicator returns
to latest results because Surfrider measures Enterococcus only. Shared dates
may be old and do not establish identical collection times or sampling points.

The comparison modal opts into early results through `/api/site-series?early=1`.
Standalone Graphs and Samples keep their existing data selection. No migration,
collector schedule, or forecast changes are needed.

## Historical overview date

The “Compare through” date defaults to today in Pacific time. `as_of=YYYY-MM-DD`
on `/compare` or `/api/compare` moves the overview table and the rolling yearly
agreement totals to that collection-date cutoff. Switching view or indicator
retains the date; “Today” resets it. The history modal initially ends on the
selected date. Invalid dates and future dates resolve to today.

Historical views use the currently available results for collections on or
before the selected date, not a replay of what had been published by then.
They hide current beach postings. Latest mode retains the City's 60-day lookup
and searches one year of BWTF history; shared mode searches one year for both
programs. Missing historical data never falls back to today's lab result.

## Validation

Regression coverage includes partial indicators, qualified values, DataSF
handoff and duplicate samples, missing shared dates, the first-day boundary,
stale/withdrawn early results, feed failures, and route rendering. Desktop and
mobile fixture browser checks cover view switching, history charts, early labels,
and page overflow. These fixture checks do not verify production data.

Before graduating the experiment, review the shared-date interpretation and
source labels with real data, especially sites whose most recent shared date is
much older than their latest individual samples.
