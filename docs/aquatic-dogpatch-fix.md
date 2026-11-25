# Aquatic Park / Dogpatch correction (#44)

## Evidence and scope

SFPUC's map assigns station 4613's CSO triangle to Dogpatch. Its inline
comment explicitly names `BAY#211_SL`, 20th Street (CSD-030), and 22nd Street
(CSD-030A). Our existing outfall registry assigns both structures to Crane
Cove Park (4620). See [issue #44](https://github.com/ciniper/BWTF_SF/issues/44).

Production `feed_station_days` records queried November 24, 2026 confirm
4613's `raw_last.cso` was `BAY#211_SL` at 23:59 PST on November 18, 19 and
20. November 21's last value was JSON null; its daily CSO boolean remained
true, meaning the flag had been present earlier that day. These daily final
snapshots do not establish exact start/clear times or individual outfall activity.

The correction is deliberately keyed to BOTH carrier station 4613 and raw
value `BAY#211_SL`. It removes that signal from Aquatic Park and applies it
to the existing Crane Cove row. Crane Cove remains flagged if either its
own native CSO or this Dogpatch signal is active. Posting flags and lab
results are unchanged. Other Aquatic Park CSO values retain their existing
behavior. No Hyde Street → Aquatic Park propagation is introduced.

Python keeps the original value as `raw_cso_station_id`, uses the interpreted
value as `cso_station_id`, and provides a source explanation on Crane Cove's
Today-map tooltip. All consumers of `fetch_stations()` receive the correction,
including Today, live forecast status inputs and the Python alert path.
The migration applies the same rule to the Postgres alert watcher.
If Crane Cove's row is missing, no station or lab results are invented.

## History

`feed_station_days` continues to store the original raw station/day evidence,
including its raw classification summaries. Existing `alert_log` transitions
are not rewritten. Future watcher transitions use corrected attribution;
the timeline therefore spans a change in interpretation at activation.
Historical relabeling, forecast training labels, and broader mapping evidence
belong in the separate follow-up draft PR.

## Verification and activation

Run `scripts/check.sh` with the project's Python dependencies installed and
`PGLITE_MODULE=/path/to/@electric-sql/pglite/dist/index.js node db/scripts/test_cso_mapping.mjs`.
Both are offline; the SQL test stubs outbound email requests.

Merging and deploying require explicit user approval. Coordinate the Python
deployment with `20261125075243_aquatic_dogpatch_cso.sql`; applying only one
leaves display and alerts inconsistent. The migration does not itself send
messages or rewrite stored observations, but the next scheduled poll can
clear Aquatic Park and queue an alert for Crane Cove. Review current watcher
state and any pending alert batches before activation, especially if an event
is still active. After activation, compare the live display, watcher state,
and original raw feed for both stations. Record activation time in the PR.

Rollback requires restoring the previous Python release and the
`bwtf_process_payload` body from `20261118051745_alerts_frequency.sql` together;
it reintroduces the known attribution error and can cause new transitions.
