# Independent 30-minute map-color collector — prepared, not deployed

This replaces the earlier, unapplied watcher-hook draft. It does **not** modify
or call the alert watcher, its runtime/configuration, alert classification,
dispatch, forecast refresh, or existing schedules. No production changes have
been made. The prepared migration is still unapplied and can be revised in place.

## How it runs

The collector owns its own `getBeaches` request and response cursor. It attempts
one HTTP request at most every 30 minutes, with a 15-second network timeout.
Failures, including failure to queue a request, do not trigger early retries.

Because pg_net completes HTTP requests asynchronously after commit, a lightweight
cron task runs every minute to harvest the pending response. That task does not
poll SFPUC every minute: the database's `last_attempt_at` enforces the 30-minute
HTTP cadence. A missing response is marked failed after five minutes. Response
bodies are bounded and validated before any history is written.

The migration registers this new cron task **inactive**, and the runtime switch
also defaults **off**. Installation makes no HTTP request. Both switches require
a separately approved activation. Deploying the website alone installs nothing.

## What gets retained

- Exact `s_color`, `p_color`, `posted`, and `cso` values, including null, empty
  strings, gray W/Y codes, and unknown future codes. No collapse to alert `ok`.
- `feed_color_reads`: each successful read's timestamp and station-state set,
  so the timeline can show the real observation checkpoints and gaps.
- `feed_color_runs`: baselines, changed states, explicit missing stations, and
  returns. Unchanged reads extend last-confirmed times and confirmation counts.
- `feed_color_state`: pointers to current runs; `feed_color_runtime`: enable
  switch, pending request, attempt/observation times, and collector health.
- Response IDs protect against duplicates/replays. A sample-date change alone
  is not a color transition. Empty/malformed responses do not clear the roster.
- Gaps over 45 minutes break a run even when its color returns unchanged. This
  distinguishes missed half-hour observations from regular collection jitter.

A change is bounded by the previous confirmation and first new observation.
These are response-observation times, **not** SFPUC publication times. Short-lived
changes between half-hour checks can be missed. Solid runs do not establish
continuous monitoring; use saved checkpoints to display that uncertainty.

No retroactive backfill or deletion/pruning of retained observations is installed.
The existing preview still uses archived forecast snapshots. A later reader
update should prefer the new checkpointed history after collection begins and
retain snapshot fallback before that date, without inventing observations.

## Separation and permissions

The collector uses its own advisory lock, runtime row, tables, cron name, and
error reporting. It does not call the alert watcher's error logger. Error and
health updates stay in `feed_color_runtime`; malformed or failed reads roll back
their history writes and preserve the last good state. Lock waits are bounded.

Functions are invoker-only and unavailable to public, anon, authenticated, or
service-role API clients. The scheduled owner can run them. RLS is enabled;
the application service role has read-only access to history and health.

This is stronger operational separation than attaching a recorder to the alert
transaction. It still shares the database and SFPUC service, so database-wide
outages/resource exhaustion can affect both collectors and the alert watcher.

## Local validation

No-network pg_net and cron stubs run in an in-memory PostgreSQL engine. Tests
cover the 30-minute request gate (including failures), waiting/harvest behavior,
missing responses, malformed/stale responses, atomic history rollback, raw
transitions, station absence/return, duplicates, gaps, permissions, inactive
installation, and preserving shutdown. Sentinel assertions verify the original
watcher function, runtime, and existing cron job are unchanged.

```sh
PGLITE_MODULE=/absolute/path/to/@electric-sql/pglite/dist/index.js \
  node tests/run_feed_color_recorder.mjs
```

Tested with PGlite 0.5.16. This verifies SQL logic, not real pg_net/pg_cron
execution or concurrent backend contention. Never run the fixture SQL on a
hosted database: it deliberately replaces local stubs and injects failures.

## Proposed rollout — only after approval

1. Review `db/migrations/20261119080653_feed_color_transitions.sql`. Confirm the
   old watcher-hook draft was never applied; do not apply this as a replacement
   over an already-installed older draft. Use the established migration process.
2. Apply the migration as its single transaction. The new job and runtime remain
   off. Confirm no existing watcher functions or schedules changed.
3. When activation is separately authorized, enable both switches atomically:

   ```sql
   begin;
   update public.feed_color_runtime set enabled=true where id=1;
   select cron.alter_job(jobid, active := true)
   from cron.job where jobname='bwtf-feed-color-collector';
   commit;
   ```

4. After the first read and a later half-hour read, inspect using SELECT only:

   ```sql
   select enabled, request_id, requested_at, last_attempt_at,
          last_observed_at, last_new_runs, last_http_status,
          last_error, consecutive_failures
   from public.feed_color_runtime where id=1;

   select response_id, observed_at from public.feed_color_reads
   order by observed_at desc limit 10;

   select kind,count(*) from public.feed_color_runs group by kind;

   select station_id,state,previous_state,previous_last_confirmed_at,
          first_observed_at,last_confirmed_at,confirmations
   from public.feed_color_runs order by first_observed_at desc limit 30;
   ```

   Expect initial baselines and approximately half-hour-spaced observations.
   Do not inject simulated changes into production to test collection.

## Stop / operational rollback

When authorized, run `db/scripts/disable_feed_color_recorder.sql`. It disables
only this collector's runtime and cron job, clears its pending cursor, and
preserves all observations. An already-queued HTTP request may finish, but its
response will not be ingested. No alert watcher function, runtime, schedule,
subscriber, or history is modified or removed.
