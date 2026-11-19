# Raw map-color transition recorder — prepared, not deployed

Use the existing once-a-minute SFPUC watcher, with a separate recorder attached
to the real response it already fetched. A second watcher would duplicate
requests and can disagree about observation times. The recorder does not change
alert classification, subscribers, dispatch, the getCSV collector, or schedules.

This change has **not been applied to production**. Production inspection was
read-only. Local tests use an in-memory PostgreSQL engine and no-network stubs.
The migration installs the recorder **disabled**; recording requires a separately
authorized enable step. Deploying the website alone does not install it.

## What gets retained

- Exact `s_color`, `p_color`, `posted`, and `cso` values, including JSON null,
  empty strings, W/Y/G/R, and future unrecognized codes. They are not collapsed
  to the alert watcher's `ok` classification.
- A baseline per station on its first successful read; never a fabricated
  historical transition. Unchanged reads extend a run's last-confirmed time.
- Each different raw state starts a run with the previous state and its last
  confirmation. A gray-to-green change is recorded even if both classify `ok`.
- Response IDs prevent duplicate confirmations and replayed/out-of-order data.
  Observation timestamps come from the saved HTTP response, not the later
  processing time. These are observation times, not SFPUC publication times.
- An absent station is explicitly missing, not green. Its return starts a new
  run. A poll gap over 90 seconds also starts a resume run, even if colors agree.
- Sample date is provenance, stored separately as the run's first/last date.
  A sample-date change alone is not a color transition.

`feed_color_runs` is the retained history. `feed_color_state` points to current
runs. `feed_color_runtime` holds the enable switch, response cursor, last new-run
count, and recorder errors. No pruning or retrospective backfill is installed.

The current timeline still uses archived forecast snapshots; this change
prepares the new high-frequency source. Connecting the new source to the UI
should preserve the raw fields and observation uncertainty, and fall back to
forecast snapshots before collection begins. Do not relabel old snapshots as
minute-level recordings.

## Isolation from alerts

The hook is after the existing alert-processing call and receives the untouched
real payload. It does not read simulated overrides. Existing alert calls,
daily-summary calls, forecast refreshes, health pings, and next-request behavior
are preserved. The installation refuses to modify a watcher body that differs
from the version inspected during preparation (MD5
`96497e15a283c0a6ec3c9686cf0feb5e`).

Recorder functions are invoker-only and not callable by public, anon,
authenticated, or service-role API clients. The existing owner-run watcher can
call them. RLS is enabled; the application service role receives only read
access to history and health, not ingestion or enable privileges.

The recorder validates a bounded payload before writing, uses a non-waiting
advisory lock and runtime row lock, and limits lock waits to 100 milliseconds.
History failures roll back their own subtransaction. Error reporting is guarded
too, and the outer hook survives a missing recorder function. Ordinary recorder
failures therefore do not roll back alert processing. This is not protection
against database-wide outages, resource exhaustion, or cancellation of the
whole watcher transaction. A separate polling job would share those database
risks while adding another upstream request stream.

## Validation performed locally

The local harness executes the actual patched `bwtf_shadow_tick` against stubs
and compares its alert/daily/forecast/HTTP effects with the unmodified tick.
Disabled, healthy, storage-failure, logging-failure, and missing-function cases
must all preserve those effects. SQL checks cover raw transitions, unknown/null
colors, malformed payloads, duplicates, stale reads, station loss/return, missed
polls, the kill switch, permissions, exact rollback, and watcher drift refusal.

Run with a local installation of `@electric-sql/pglite` (tested with 0.5.16):

```sh
PGLITE_MODULE=/absolute/path/to/@electric-sql/pglite/dist/index.js \
  node tests/run_feed_color_recorder.mjs
```

This verifies PostgreSQL logic, not real pg_net scheduling or concurrent backend
contention. Do not run the fixture SQL on production: it deliberately injects
failures and replaces functions inside rolled-back local test transactions.

## Proposed rollout, only after approval

1. Recheck the live tick definition and project. If the guard differs, rebase
   and rerun tests instead of bypassing it. Capture the current definition for
   rollback. Review the migration and ensure it runs as one transaction.
2. Apply `db/migrations/20261119080653_feed_color_transitions.sql` using the
   established migration process. It stays disabled. Confirm existing watcher
   health, schedules, and alert behavior are unchanged.
3. Separately enable the recorder when authorized:

   ```sql
   update public.feed_color_runtime set enabled = true where id = 1;
   ```

4. After a few successful watcher cycles, use read-only checks:

   ```sql
   select enabled, last_response_id, last_observed_at, last_processed_at,
          last_new_runs, last_error, consecutive_failures
   from public.feed_color_runtime where id = 1;

   select kind, count(*) from public.feed_color_runs group by kind;

   select station_id, kind, first_observed_at, last_confirmed_at,
          confirmations, state, previous_state, previous_last_confirmed_at
   from public.feed_color_runs order by first_observed_at desc limit 30;

   select last_processed_at, last_fetch_status, last_error,
          last_summary->'feed_colors' as color_recorder
   from public.watcher_runtime where id = 1;
   ```

   Expect baselines first, then confirmations and real changes; no synthetic
   events should be injected to validate production. Compare observed raw
   states with the existing successful feed, without issuing another poll.

## Stop or roll back

When authorized, disabling is a single operation that preserves all history:

```sql
update public.feed_color_runtime set enabled = false where id = 1;
```

`db/scripts/disable_feed_color_recorder.sql` disables it and restores the exact
original tick **only if** its body still matches this installation. If later
changes exist, it leaves them intact, disables recording, and warns for manual
review. The script retains tables and observations; it never deletes history or
changes cron schedules.
