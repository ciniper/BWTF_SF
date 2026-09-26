# BeachWatch — SF beach postings (signs up / signs down)

**Source.** California State Water Resources Control Board, *Beach Advisories (Postings and
Closures) and Beach Water Quality Monitoring*, https://data.ca.gov/dataset/beach-water-quality-postings-and-closures
— resource "Beach Posting and Closures- Advisories" (statewide CSV, ~30 MB). County health
departments file every advisory with the State; SF files per SFPUC station with the same ids as
`shared/stations.py`. This is the only public record of *when each SF beach was posted and for
how long*. DataSF (`v3fv-x3ux`) and SFPUC's live feed carry the lab samples, not postings; the
2016-17 Poo Bot archive (`../poobot/feed_status.csv`) has POSTED rows for ten months only.

**Files** (written by `src/collectors/beachwatch.py --refresh`):
- `sf_beach_advisories.csv` — one row per station-advisory (2,142 at the 2026-09-26 pull,
  1999-01-31 → 2026-02-28), trimmed columns, plus `station` (registry id), `zone`,
  `cause_class`, `duration_days`.
- `sf_posted_zone_days.csv` — one row per (zone, day) a beach in the zone was posted, with the
  dominant cause class (cso > rain > other), the stations, and the number of advisories.
- `manifest.json` — provenance: fetch time, URL, statewide sha256, counts, span, dropped and
  excluded rows.

**Semantics.** A station is posted from `posted_on` (DateofAdvisory) through `reopened_on`
(DateOpened) *inclusive*. Checked against SFPUC's own POSTED rows in the Poo Bot snapshots:
93% of BeachWatch posted station-days show POSTED in the feed the same day, 98% within ±1 day;
reading the reopening day as already open scores worse both ways. CSO postings last a median
2 days (90th percentile 4); in the East 3–7 days after a discharge is common, in the west
postings end within ~2 days.

**Cause classes.** `cso` = AdvisoryCause "Combined Sewer Overflow" or Source "Combined Sewer
Discharge" (988 rows); `rain` = the 72-hour rain rule (116); `other` = Unknown / bacterial
standards violation / spills (1,038) — dry-weather postings a rain model is not expected to see.

**Quality notes.** One row with a year-1014 date dropped. Two pre-2016 advisories run for a year
or more (a 2008 Ocean Beach rain advisory "closed" in 2015; a 2007-08 Crissy Field closure) —
kept in the CSV, excluded from the daily label (`MAX_DURATION_DAYS = 60`); since 2016 the longest
is 26 days. BeachWatch also has "Crissy Field, Trees" (BAY#202.2_SL), a retired station mapped
to North Shore by hand. SF files months late: the last record is Feb 2026 at a Sept 2026 pull;
our watcher's `alert_log` (posting transitions since Aug 2026) covers the gap going forward.

**Why we keep it.** The scorecard's discharge label marks only the day sewage went in, so the
week of elevated risk stage 2 holds afterwards graded as false alarms. Against real postings,
59 of 60 East and 9 of 11 Ocean Beach "tail" alarms (served set, out of sample, 50%) fell on
posted days. Samples exist on only 185–316 of the 974 out-of-sample days per zone and have a
hole from mid-2017 to 2019; postings cover every day. Grading against postings is the open
TODO item.

**Refresh.** `venv/bin/python features/forecast/src/collectors/beachwatch.py --refresh`, then
`venv/bin/python tests/test_beachwatch.py`. Quarterly, with the CIWQS refresh.
