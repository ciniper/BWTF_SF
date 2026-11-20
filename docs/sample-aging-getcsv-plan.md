# Sample aging and earlier City lab results

Started 2026-11-17 on `design/sample-aging-getcsv`, based on
`discharge-observations` at `a7d0c247cb488110dce7800a5b359eb99842d907`.
Production Today behavior has not changed; the serving changes below are implemented on this design branch. On November 17, the approved
`sfpuc_getcsv_observations` table was created in the bwtf-sf Supabase project.
The migration is `db/migrations/20261118042120_sfpuc_getcsv_observations.sql`.
It records result changes separately from DataSF, with original observation
timestamps, recorder-import provenance, and baseline markers for unknown arrival
times. The existing recorder already provides timing evidence; no new research
period is required.

## Implemented November 17

- Imported 203 observations from 16 getCSV snapshots (November 14–17), keeping
  the original read timestamps. Re-running the import inserted zero duplicates.
- Started an independent Supabase collector, `bwtf-getcsv-tick`. A minute tick
  harvests asynchronous HTTP responses and issues a request at least two minutes
  after the previous request. The initial live baseline contains 782 results.
  Seven all-null feed placeholders are ignored. No alert functions are called.
- Added `sfpuc_getcsv_runtime` for collection state and health. Results are
  append-only; unavailable indicators on a still-present sample get a removal
  observation. Whole samples aging out of the feed do not become removals.
- Built the passphrase-gated `/alerts/sample-gap` Admin page on this branch:
  collection-date/station filters, collector health, indicator values, a shared
  arrival timeline for getCSV/map/DataSF, and correction history. Inspired by
  the supplied `The Sample Gap.html` artifact. The page is not yet deployed.
- DataSF on this page is the existing observation archive, not a live query;
  baseline and backfill timestamps are not presented as exact arrivals.

### Public UI implemented on the design branch

- Today and its realtime refresh use the newest validated collection date from
  the map or recorded getCSV snapshot. Posting changes no longer imply a sample.
- Sample popovers merge by station, collection day, and indicator; DataSF wins
  each matching key. Partial results display immediately with remaining
  indicators pending. Open latest-sample popovers refresh every minute.
- Recorded snapshots cache for 30 seconds and expire after six minutes without
  a successful collection. An outage falls back to DataSF with an explicit note.
- Online Postings Timeline has a separate getCSV diamond for the first observed
  indicator, with individual arrival times in the tooltip. Baselines say “by”
  and use a hollow marker. getCSV-only samples also appear.
- Focused regression suite: 54 passing tests, including partial arrivals,
  corrections, DataSF precedence, outages, and removal of posting-based guesses.
  Browser fixtures check partial arrivals, automatic handoff, outage fallback,
  Today age, and timeline tooltips at desktop and mobile widths.
- Preview deployment is the next release step; alerts and forecasts are outside
  this change. Full comparison graphs remain DataSF-based.

### Operations

Migration: `db/migrations/20261118051928_sfpuc_getcsv_collector.sql` (the
observation-table migration must be applied first). Its default is disabled;
after verification enable with `update public.sfpuc_getcsv_runtime set enabled
= true where id = 1`. This has been enabled in the BWTF project. Set it false to
pause; existing observations are retained. The collector runs as the cron job's
database owner; the service role can only read its runtime state. Both tables
have RLS enabled and no browser-role grants.

Inspect `last_success_at`, `last_error`, `consecutive_failures`, `last_row_count`,
and `last_changes` on the runtime row. `previous_values` is the last successfully
validated feed, used for change detection. Failed reads never replace it.
The initial baseline means “present by this time,” not newly published now.
The upstream feed rejects the custom headers tested through pg_net; use the
same simple HTTP request shape as the existing beach watcher.

Import: `python3 scripts/import_getcsv_recordings.py --output /tmp/getcsv.json`
is a dry run. `python3 -m scripts.import_getcsv_recordings --apply` uses configured
server credentials and inserts with conflict-ignore on the observation key.
Run from the repository root. Never stamp historical rows with the import time.

Checks: `tests/getcsv_collector.sql` exercises ingestion in a rolled-back
transaction. Python regression tests cover the importer, dashboard data model,
filtering, unavailable data, escaping, and Admin passphrase gate.

## Sources reviewed

- `TODO.md`, B12: result timing and the five-step getCSV plan.
- `docs/discharge-observations.md`: precautionary postings on November 13,
  result-day observations November 14–16, and the November 17 repeat false alarm.
- `docs/discharge-observations/sample-gap.html`: timing diagram and intended behavior.
- `docs/discharge-observations/feed/changes.log`: recorded feed evidence, including
  November 17 indicator arrivals through 4:03 PM Pacific in this snapshot.
- `shared/result_gap.py`, Today board integration in `app/landing.py`, and
  `tests/test_result_gap.py`: the existing posting-transition inference.

## Problem and intended behavior

The current safe ↔ posted heuristic claims a new sample is unpublished after
precautionary postings. Baker East and Lobos on November 13, and the Today card
for Vicente and Lincoln on November 17, demonstrate the false claim.
A posting or discharge transition alone must not establish a pending sample.
Sample age continues to mean time since collection; result arrival time is a
separate fact. An older sample should retain its actual collection date and age.

getCSV supplies all stations in one request, including storm-only stations, and
can provide numbers hours before DataSF. The recorded comparison found 396
matching overlapping rows since October 1. Treat that as recorded evidence,
not a guarantee that feeds will always agree. DataSF takes precedence per row.

## Implementation sequence

1. Replace unsupported pending-sample claims with evidence from actual sample
   dates. Keep posting status independent of whether a new lab result exists.
2. Collection is now in Supabase, independently of the email watcher. Add the
   serving reader for the recorded getCSV results; do not fetch the upstream feed
   per visitor or add a second collector in Python. Normalize keys for DataSF
   merges and preserve `<` and `>` qualifiers. Define freshness/fallback behavior
   before public serving.
3. Merge by normalized source, collection day, and analyte. Prefer DataSF for
   each matching key; otherwise include the getCSV value with early provenance.
   Re-read at least the last seven days so corrections can land.
4. Record first-observed time per early result. Label it as our observation time,
   not an exact lab publication timestamp. Use “lab results” wording; do not
   assert that these were the counts behind SFPUC's posting decision.
5. Wire Today, `/api/realtime`, sample popovers, and their summaries to the same
   sample state. Show missing indicators explicitly (for example “Entero pending”).
   Comparison and site-analysis integration can follow later.

## Clarifications to resolve during implementation

The source notes describe two comparisons: getCSV versus the map's sample date,
and getCSV versus the sample our site displays. These are distinct once Today
shows early rows. Track the map date, newest observed lab date, and displayed
result date separately. Once a newer row is displayed, it should not remain
“unpublished” merely because the City's map or DataSF has not caught up.
A newer map collection date can establish a real gap even if getCSV is unavailable;
a posting transition cannot. Partial rows require indicator-level pending state,
not a claim that the entire sample is unpublished.

Confirm the ingestion/cache and first-seen storage boundary before coding; do not
add one upstream request per visitor. Decide how cached early rows expire after
an outage, and how revised/withdrawn rows are handled without losing provenance.
Do not silently combine an older sample's Enterococcus with a newer sample's
coliform values into one apparently complete sample.

## Acceptance checks

- Precautionary posting with unchanged dates: no new-sample claim; age remains accurate.
- Posting/discharge flag flaps: no invented sample count.
- getCSV newer than map and DataSF: newer collection date and available values shown.
- Only total coliform present: other indicators pending, never zero or implied passing.
- Enterococcus and fecal coliform arriving in either order: same sample fills in.
- DataSF catches up: its values win per key and early provenance clears per key.
- Qualifier correction, malformed response, timeout, and unknown source: handled explicitly.
- Storm-only stations, mixed station dates, and Pacific midnight: correct mapping and ages.
- Server-rendered Today, realtime refresh, popovers, and zone counts agree.

## Existing holds and scope

The source plan schedules observation through Friday November 20, 2026. Review
that evidence before enabling alerts based on early numbers. The notes also
leave open whether to ask SFPUC about featuring the feed after removal of its
map button, and whether first-published values are preliminary or confirmed.
No alerting changes are included here. Email frequency, separate discharge map
symbols, and forecast changes belong to their own workstreams.

## Integration onto main

Rebased onto `9ea8af8` after PR #28 merged. Preserved main's newer Today map
label and replay handling while removing posting-based sample guesses. Carried
forward only the 16 original getCSV recording files needed by the importer and
regression fixtures; the other discharge-observations notes remain on that branch.
The PR now targets main. No migrations are reapplied during this integration.

Integration validation: 64 focused tests passed, including Today and the updated
email simulator from main. Desktop/mobile browser checks passed for partial
arrivals, automatic DataSF handoff, outages, timeline tooltips, and Admin filters.
Forecast source/artifacts and PR #28's migration and email simulator are unchanged
relative to main. The long forecast suite was not repeated, per the agreed scope.
