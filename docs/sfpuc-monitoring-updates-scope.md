# SFPUC monitoring updates — original scope and staged rollout

Scope recorded November 21, 2026 on `design/sfpuc-monitoring-updates`, based on
`main` at `d2144a3`. Start with SFPUC's beach notification emails, archive them
in the existing Supabase project, and show reviewed messages in an expandable
**SFPUC updates** panel on Today. Add recorded hotline bulletins after testing
whether they provide useful information beyond the emails.

**Current rollout, updated November 21, 2026:** the user requested Admin-only
access for now. Capture, review, and the dropdown preview are built locally at
`/alerts/sfpuc-emails`; Today and a public read API are excluded. The isolated
Supabase migration is applied, and DNS/Brevo inbound registration are complete.
App deployment, Gmail forwarding, and a real delivered-email test remain pending.
See [Email capture setup and behavior](sfpuc-email-capture.md) for the implemented
schema, verification, and activation steps. The remaining sections preserve the
original broader proposal; public Today display and voice capture are future work.

## Sources and contacts

SFPUC offers email alerts for beach postings and de-postings. Its beach hotline
is **415-242-2214**, also **1-877-SFBEACH / 1-877-732-3224**. The agency says its
hotline and website are updated when new sample results are available. Its
[Ocean and Beach Monitoring page](https://www.sfpuc.gov/programs/ocean-and-beach-monitoring)
contains the email signup, embedded from Emma. A real received email is still
needed to establish the sending address, authentication headers, message format,
and whether notices contain attachments or links instead of the full bulletin.

- [x] **Save the help hotline email contact note:** `BeachWaterQuality@sfwater.org`,
  supplied by Chase on November 21 as the notification sender. Whether it accepts
  help enquiries is unverified. Keep it separate from the ingestion mailbox.
- [ ] Store the confirmed contact with its purpose, source URL, and verification
  date in shared configuration for reuse by Today, About, and the footer.
- [ ] Obtain representative original emails: posting, de-posting, discharge,
  multi-beach update, and any correction. Preserve headers and timestamps;
  remove recipient details from committed test fixtures.

“Hotline voice messages” is scoped here as SFPUC's public recorded bulletin.
Voicemails left by callers for BWTF would need a separate private inbox and
publication policy.

## What already exists

| Area | Existing integration | Required addition |
| --- | --- | --- |
| Today | `/`, `/today`, and `/index.html` use `features/today/page.py`, `app/templates/today/page.html`, and the shared `_today_board.html` macros | One expandable panel and a bounded read endpoint |
| Storage | `shared/supabase.py` is a server-only PostgREST client | Message archive, reviewed updates, and ingestion health |
| Live data | `getBeaches`, recorded `getCSV` changes, DataSF samples, and watcher history | An independently attributed narrative source |
| Email | Brevo sends outbound alerts; delivery polling lives under `features/alerts/` | A receiving address and inbound ingestion path |
| Admin | `/alerts` pages and APIs have an existing coordinator session gate | Review, publish, reject, retry, and source-health controls |
| Voice | Twilio SMS support exists in `features/alerts/notifiers.py` | Voice capture and transcription are new work |

The connected `bwtf-sf` Supabase project's public table inventory was inspected
read-only on November 21. It includes `alert_log`, `alert_deliveries`, `samples`,
`feed_station_days`, and `sfpuc_getcsv_observations`, but no bulletin archive.
Use new tables: `alert_log` records detected transitions and `alert_deliveries`
records our outbound messages, which have different meanings.

## Email ingestion

Preferred candidate: a dedicated receiving subdomain with Brevo inbound parsing,
since this application already uses Brevo. Confirm account availability and cost
before selecting it. The connected Brevo tools do not expose inbound-email setup;
this feasibility assessment uses the provider's API documentation.

[Brevo inbound parsing](https://developers.brevo.com/docs/inbound-parse-webhooks)
requires a verified receiving domain distinct from the sending domain, MX records,
and an HTTP webhook. It delivers a batch of parsed messages with IDs, headers,
bodies, and attachment metadata. Use a new subdomain such as
`inbound.<owned-domain>`; configure only that subdomain's MX records. Subscribe a
dedicated address through SFPUC's signup, including any confirmation step. Direct
delivery makes original metadata easier to retain than ordinary forwarding.

If a dedicated mailbox already receives these emails, a scheduled mailbox-API
import is an alternative. That option needs provider-specific OAuth access,
durable cursors, overlapping recovery reads, and a scheduler. A manual `.eml`
import should seed fixtures and support recovery regardless of the live path.

Proposed processing sequence:

1. Accept the provider callback at a new endpoint such as
   `POST /api/ingest/sfpuc-email`. Authenticate it independently of the browser
   session gate, enforce payload limits, and process every item in a batch.
   Brevo documents [webhook authentication](https://developers.brevo.com/docs/secured-webhooks);
   verify the configured mechanism with an actual callback before enabling it.
2. Check the expected recipient and sender against real examples. Provider
   callback authentication proves transport origin, not SFPUC authorship.
   Evaluate trusted delivery authentication results; a From address alone is
   insufficient. Quarantine mismatches and forwarded messages needing review.
3. Persist the original payload before acknowledging success. Give each delivery
   a stable provider-scoped deduplication key; use original Message-ID and a
   content hash to identify forwarded duplicates. A repeated ID with different
   content is a conflict to review, not an overwrite. Make partial-batch retries
   safe. A database failure must remain retryable.
4. Convert the bulletin to readable text; separate quoted history, signatures,
   unsubscribe links, and recipient information. Render text with escaping.
   Never embed received HTML, tracking pixels, or personalized links on Today.
5. Suggest beach/zone tags using `shared/stations.py` and `shared/zones.py`.
   Unknown names stay unclassified. Keep the original wording and dates;
   automated summaries and status inference are unnecessary for the first release.
6. Place the message in Admin for review. Publish only its approved public text.
   Keep publication/rejection history and source originals. Later automation
   can publish known formats after fixture coverage demonstrates reliability.

Attachments need an explicit size/type allowlist and private storage. Do not
automatically follow arbitrary URLs from a message. If notices require linked
content, implement a separate allowlisted fetch after inspecting real examples.
Review actions need session authorization and CSRF protection.

## Proposed Supabase storage

Use UTC timestamps and render in America/Los_Angeles. Keep source publication
time, ingestion time, and any stated effective date distinct. An unknown source
time stays null; receiving an old message today must not turn it into today's
notice.

| Proposed table | Main fields and purpose |
| --- | --- |
| `sfpuc_monitoring_messages` | UUID, channel (`email` or `hotline`), provider, provider event ID, deduplication key, original Message-ID or recording ID, sender/recipient metadata, subject, source time, received/captured time, content hash, raw text/payload or private object path, parser version and processing state. Source fields are immutable. |
| `sfpuc_monitoring_updates` | Message foreign key, revision number, approved title/text, beach/zone tags, effective date if explicitly stated, review state, reviewer, reviewed/published timestamps, and superseded-update reference. Preserve revisions and corrections. |
| `sfpuc_monitoring_ingest_runs` | Source/channel, attempt times, result, received/duplicate/rejected counts, sanitized error, and optional provider cursor. Records successful checks with no new messages as well as failures. |

Require unique delivery keys and unique `(message_id, revision)` pairs. Add an
index for newest published updates, preferably a partial index on source time
and ID for published rows, and indexes on foreign keys and recent run times.
Order messages with unknown source time using a documented received-time fallback
and label that time correctly. Select explicit columns and cap page size.

Follow the existing service-only pattern: enable RLS, revoke browser-role
access, and explicitly grant the server role only the operations each table
needs. The app returns an allowlist of published fields; it must enforce the
publication filter because the service role bypasses RLS. Raw headers, personal
addresses, rejected messages, and credentials must never enter the public API.
Supabase now requires [explicit grants for new Data API tables](https://supabase.com/changelog/45329-breaking-change-tables-not-exposed-to-data-and-graphql-api-automatically).

Small text can live in Postgres. Put audio, `.eml` originals, and attachments in
a [private Storage bucket](https://supabase.com/docs/guides/storage/buckets/fundamentals),
with file-size/type limits and authenticated access or short-lived signed URLs
for authorized review. Decide separately whether approved audio should be public.
Set retention and deletion rules before enabling capture; do not rely on a
provider's temporary download link as the archive. The current REST helper needs
a Storage adapter for object uploads/downloads.

## Today dropdown behavior

Use an accessible `<details>` disclosure titled **SFPUC updates**, collapsed by
default, below the existing board. Show the latest five published updates when
opened, newest first, with a small bounded history link or pagination. Each entry
has the agency, channel, source date (or explicitly labeled receipt/capture date),
affected beaches when known, and the approved message text. Keep contact links
available even if the update feed is empty.

Load from `GET /api/today/sfpuc-updates` on first expansion, so a slow database
does not delay the map or Today headline. Use short caching and explicit loading,
empty, error, and retry states. “No updates captured” is not “no warnings.” An
older bulletin stays visibly dated; a quiet event-driven email channel alone
does not prove a collector failure. Report ingestion failures separately from
message age.

For this first release, bulletins supply context. They do not change map colors,
trigger our subscriber alerts, clear postings, establish a new sample date, or
feed forecasts. A disagreement with the map remains visible with separate source
timestamps. While viewing Past week, label this panel as latest messages or hide
it so current bulletins cannot be mistaken for historical evidence.

Likely file changes:

- New `features/sfpuc_updates/` modules for parsing, persistence, review, and reads.
- New importer under `scripts/` and migrations under `db/migrations/`.
- Routes and Admin gates in `app/wsgi.py`; Today read route integrated with
  `features/today/page.py`.
- A new `app/templates/today/_sfpuc_updates.html` include in `today/page.html`,
  a small stamped JS asset, and scoped styles.
- Admin review template/tab, focused tests, and deployment instructions.

## Hotline follow-up

Public-source inspection on November 21 found no exposed recording endpoint:
the monitoring page and beach-map HTML contain no audio element or MP3, WAV,
M4A, or OGG URL. The map's linked `map.js` and the monitoring page's custom
`interactions.js` and `sfwaterowl.js` contain no hotline/audio source reference.
The map calls `getBeaches`, `getStation`, and `getCSV`; those are also the only
operations advertised by the public
[LIMS service description](https://infrastructure.sfwater.org/lims.asmx?WSDL).
This does not rule out a separate telephone-provider API or unlinked recording,
but the inspected website assets provide no route to one.

First obtain a sample of the public recording and determine whether SFPUC can
provide its audio file or written script directly. The monitoring page documents
phone access but does not establish a downloadable audio feed. Direct access,
if available, removes the need for calls and speech recognition.

Otherwise, a candidate is scheduled Twilio Voice capture. Its
[Call resource](https://www.twilio.com/docs/voice/api/call-resource) supports
outbound recording and a callback when audio is available. This would require
Voice setup, an allowed caller identity, a durable scheduled job, any menu-key
sequence, maximum call duration, and an agreed polling/spend limit. Confirm
SFPUC's arrangement for automated capture and republication before recurring use.

Persist call attempts separately from changed bulletins. Validate the callback,
download audio into private Storage, then transcribe in a retryable worker.
Recording byte hashes catch exact duplicates but telephone noise can change
bytes; use normalized transcripts as duplicate candidates and retain meaningful
date, location, and negation changes. Human review must resolve uncertain beach
names, numbers, and “posted” versus “de-posted” before publication. Record the
capture time separately from any date spoken in the message. Failed calls,
silence, and incomplete recordings must never publish an empty all-clear.

If the recording adds no useful detail beyond email, keep the call link and
skip ongoing voice capture. Voice can reuse the same archive, review workflow,
and Today panel if it proves valuable.

## Implementation sequence and checks

1. **Source examples and contact:** confirm the receiving mailbox,
   sender identity, and representative messages; choose the inbound provider.
2. **Archive and review:** implement schema, private storage as needed, manual
   importer, parser, and Admin review against redacted fixtures.
3. **Live email:** configure the receiving address, authenticated webhook,
   durable processing, retry/reconciliation, and source health; verify one real
   bulletin is archived exactly once.
4. **Today panel:** publish a reviewed fixture in preview, check desktop/mobile
   and keyboard behavior, and verify empty/error states and Past week labeling.
5. **Voice feasibility:** compare a recording with email, then decide whether
   automated capture is worth adding.

Acceptance checks should cover webhook authentication; spoofed sender handling;
duplicate and partial-batch delivery; old, corrected, and out-of-order notices;
HTML escaping and private-field exclusion; unrecognized beaches; Pacific/DST date
display; storage failure/retry; Admin authorization; and unpublished rows being
inaccessible from public routes. Verify a bulletin never changes map or alert
state. For voice, add duplicate callbacks, repeated/noisy recordings, truncation,
and transcription corrections.

Planning estimate, assuming working provider access and representative messages:
**3–5 engineering days** for email/archive/review/Today, and **3–5 additional days**
for voice capture and transcription if needed. DNS, subscription confirmation,
source cooperation, and observing real updates can extend elapsed time. Monthly
cost depends on inbound provider eligibility, audio minutes, transcription, and
retention; confirm those inputs before quoting a price.
