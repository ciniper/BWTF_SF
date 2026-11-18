# Alert frequency implementation and rollout

## Implemented

The Admin simulator and live SQL policy share the proposed behavior:

- Real escalations enter `alert_pending`, one row per subscriber. The first change fixes `due_at` at 15 minutes later. Further changes join that window without extending it. Each station appears at its worst status in the batch, matching the simulator. An event exactly at the deadline joins; a later event starts a new window.
- `alert_status_day` remembers the last Pacific calendar day each real status (posted or discharge) was observed, keyed separately by station and status. A status carried over from yesterday counts today, even if today's first read clears it. Same-day repeats are logged but excluded from new batches. Repeat postings are suppressed too; a first escalation from posted to discharge remains eligible. Existing real PG history seeds today's memory during installation.
- At send time the dispatcher rechecks active subscriptions and current selected stations. The additional running list uses current real station state, excludes the batch's own stations, and includes suppressed discharges. Its heading uses the subscriber's stored zone selection, omitted for all 20 sites. The shared Python helper and SQL renderer produce the same HTML/text/SMS section.
- Targeted simulations remain immediate and isolated from the real queue and day memory. Manual Python senders retain their existing immediate behavior. The optional Python watcher remains a detection reference, not a mirror of batched delivery counts; do not enable it as a second live sender.
- Status transition logging remains immediate, preserving replay and event end times. Queued transitions use channel `queued`; actual sends get separate `pg_live_batch` / `batch_delivery` log entries linked to delivery records by the existing trigger. Renderer metadata is `alerts-frequency` for this SQL dispatcher.
- Queue and memory tables have RLS enabled and service-role-only access. A transaction advisory lock serializes detector/flush calls. The existing cron tick processes and flushes the queue; no new scheduler is installed.

## Verification

The offline PostgreSQL integration harness applies the SQL to PGlite, with fake HTTP transport and synthetic subscribers. No production credentials, subscriptions, or email sends are used. It verifies the fixed deadline, boundary joining, later-window separation, repeat suppression, overnight carryover, next-day eligibility, subscription changes and unsubscribe during the hold, missing credentials/retry, duplicate flush prevention, targeted simulations, off/shadow mode, rendering parity and role permissions. The actual Nov 13–17 fixture replay yields **5 messages**, matching the simulator's reduction from 14. Same-day suppression alone now produces 10 messages (previously 11 with discharge-only suppression).

Run from the repository root:

```sh
npm install --prefix /tmp/bwtf-alerts-verify @electric-sql/pglite@0.5.16
PGLITE_MODULE=/tmp/bwtf-alerts-verify/node_modules/@electric-sql/pglite/dist/index.js PYTHON=python3 node db/scripts/test_alerts_frequency.mjs
python3 tests/test_email_sim.py
python3 tests/test_alert_deliveries.py
python3 tests/test_cso_history.py
```

Python requires the repository dependencies. Tests also pass using the existing project virtual environment. The SQL test exercises PostgreSQL functions, not hosted Supabase's pg_cron/pg_net runtime or provider delivery. It uses an injected clock in the test copy of SQL for the historical replay.

## Deployment plan

For this rollout, staging is intentionally skipped. Verification completed locally with fake HTTP transport; hosted scheduling and provider delivery have not been tested end to end. Monitor these during the production rollout below.

1. Review and merge the PR. Deploying the website makes the simulator available; it does **not** apply the database policy.
2. Confirm the intended BWTF Supabase project, current migration definitions and live mode. Confirm the Python watcher is observer-only or disabled. Capture the current function definitions for rollback. Clear active targeted simulations (the migration refuses installation while any exist).
3. Apply `db/migrations/20261118051745_alerts_frequency.sql` once, after the existing migrations through 024. It is transactional and immediately enables the policy for the live PG sender. It does not replay old alerts. The Supabase CLI generated the timestamp; the file follows this repository's `db/migrations` storage convention.
4. Inspect RLS/grants and run the hosted database security advisors. Verify the existing cron keeps succeeding, new escalations show `queued`, and batches drain after their deadline.
5. Monitor `alert_pending.due_at`, `pg_live_batch` logs, and `alert_deliveries` provider outcomes through the first real batch. Confirm zone headings and the subscriber's still-active sites in the delivered email.

## Follow-up: reusable staging environment

Set up a separate Supabase test project for future alert changes, with the required schema, scheduled watcher, and email-provider configuration. Use synthetic beach events and fake subscribers restricted to designated test addresses. Exercise fixed-window batching, repeat suppression, rendering, pg_net delivery and delivery-log linking without contacting real subscribers. Targeted simulations alone bypass batching, so controlled real-feed fixtures are needed to test the full policy. This environment is future work, not a prerequisite for this rollout and not created by this PR.

## Operational limits and rollback

A successful fresh feed read triggers flushing. During a feed outage a batch remains pending, so actual delivery can exceed 15 minutes; the next successful read supplies fresh running-list context. Missing sender credentials also retain the batch. The queue is removed after handing a message to pg_net, using existing provider outcome tracking; it does not add automatic retries for downstream provider failures. Transactional locking prevents duplicate queue flushes, not provider-level exactly-once delivery.

As in the simulator, a batch reports the worst change observed within its window, even if that station subsequently clears; the additional running list reflects send-time state. Clears alone do not generate an email. Same-day suppression can hide a genuinely separate posting or discharge later that day (the recorded Vicente case); this is the explicitly selected calendar-day policy, not an episode detector.

For rollback, set `watcher_config.mode` to `off`, save the pending rows and function definitions, and restore the prior `bwtf_process_payload`, `bwtf_log_escalations` (011) and `bwtf_dispatch_live` (021) definitions. Review/dispose of pending batches explicitly before resuming `live`; don't silently replay them. Leave the new tables in place for audit until the rollback is confirmed. Restoring those functions returns immediate delivery; changing the website deployment alone does not roll back SQL.

No production migration has been applied by this PR.
