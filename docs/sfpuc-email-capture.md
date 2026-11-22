# SFPUC email capture

The implementation captures Brevo inbound messages privately and lets a
coordinator review and approve plain text in an Admin-only dropdown preview.
There is no Today panel or public email-read endpoint. App capture is disabled
by default until deployment configuration is explicitly approved.

**Database applied November 21, 2026:** migration `20261122020135`
(`sfpuc_email_capture`) is applied to BWTF Supabase project
`fqujpgxgilzgpkleehvg`. The local filename matches the recorded hosted version.
The three new tables are empty. Existing relation/function definitions and
grants, all four scheduled jobs, and watcher configuration had identical
before/after fingerprints. Live service-role capture, deduplication, approval,
stale-review rejection, and removal checks passed inside a rolled-back
transaction; no synthetic records remain. App changes are still local.

**Source confirmed by Chase on November 21, 2026:**
`BeachWaterQuality@sfwater.org`. Chase already receives these messages in Gmail;
use selective Gmail forwarding into a capture address. A new SFPUC subscription
is optional. This also records the requested help/hotline email contact note;
the address was confirmed as the notification sender, and its ability to accept
help enquiries has not been verified.

**Receiving setup confirmed November 21, 2026:** use
`sfpuc@inbound.surftober.com`. The user added the records through GoDaddy.
Both inbound MX records are published (priorities 10/20, TTL 3600), and the
Brevo code TXT and both DKIM CNAME records are published. Brevo reports
`inbound.surftober.com` as verified and authenticated. This confirms domain
setup. A subsequent live setup trial on the Free account succeeded:
Brevo accepted the inbound webhook with HTTP 201 and returned ID `2692827`;
reading it back returned HTTP 200 with the expected domain, event, receiver
URL, and bearer authentication. The inbound-events endpoint also returned
HTTP 200. No plan upgrade was performed. Actual email delivery and any
provider receiving quotas remain untested; Gmail forwarding is not configured.

**Webhook registered, receiver pending:** `2692827` points to
`https://bwtf-sf.vercel.app/api/ingest/sfpuc-email`, with `type: inbound`,
event `inboundEmailProcessed`, and domain `inbound.surftober.com`. Its independent
bearer token was generated server-side and is stored in Brevo's webhook
configuration; no credential was printed or committed. During approved
deployment, transfer that token securely into `SFPUC_EMAIL_WEBHOOK_TOKEN`, or
rotate both ends together. Do not create a second webhook for this domain.
The setup used the existing Brevo credential within BWTF Supabase's network
extension, without changing watcher configuration, application tables, or jobs.

## Isolation and behavior

- New feature package: `features/sfpuc_updates/`. It never imports the watcher,
  notifier, forecast engine, or status classifiers.
- Three new tables: `sfpuc_email_messages` (private originals),
  `sfpuc_email_reviews` (append-only review decisions), and
  `sfpuc_email_ingest_runs` (successful delivery counts).
- Two server-only views: `sfpuc_email_inbox` and `sfpuc_email_reviewed`.
  Only the latest review being approved includes a message in the private preview.
- Two narrowly granted, security-invoker RPCs make a received batch atomic and
  serialize review revisions. RLS is enabled and browser roles have no grants.
- Existing tables, scheduled jobs, alert sends, sample dates, map colors, and
  forecast inputs are unchanged. The existing app gains route registration and
  conditional Admin links only. Today is unchanged.
- The Admin preview makes no email request until its disclosure is opened.
  Its read endpoint `/alerts/api/sfpuc-updates` requires the Admin session,
  and `/api/today/sfpuc-updates` does not exist. Storage failure affects this
  preview independently. It shows five updates per page, capped at
  1,005 entries, with original email dates or explicitly labeled capture times.

## Configuration

| Variable | Value |
| --- | --- |
| `SFPUC_EMAIL_ENABLED` | `true` to enable capture and Admin screens; absent or `false` disables it |
| `SFPUC_EMAIL_RECIPIENT` | `sfpuc@inbound.surftober.com` |
| `SFPUC_EMAIL_SENDERS` | Optional comma-separated expected senders; defaults to `beachwaterquality@sfwater.org` |
| `SFPUC_EMAIL_WEBHOOK_TOKEN` | An independently generated random bearer secret, at least 32 characters; configure the same value in Brevo |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Existing server-only storage configuration |
| `ADMIN_DASHBOARD_PASSPHRASE`, `FLASK_SECRET_KEY` | Existing Admin session configuration; use configured production values |

Keep the webhook secret in deployment/provider secret settings. It is unrelated
to the Brevo API credential and the Admin passphrase. Nothing needs a Gmail
password or broad access to Chase's mailbox.

## Activation sequence

1. The receiving address is `sfpuc@inbound.surftober.com`; DNS, Brevo domain
   authentication, and inbound webhook registration on the Free plan are
   complete. The connected Brevo plugin does not expose inbound setup.
2. The migration `db/migrations/20261122020135_sfpuc_email_capture.sql` is
   already applied; do not run it a second time. Deploy the branch with the feature
   enabled in the intended environment and its variables set. Each merge or
   deployment requires the user's explicit approval; preserve all required
   reviews, checks, and branch protections. Pushing a `design/*` branch can
   trigger a Vercel preview deployment and must respect this approval requirement.
3. The receiving subdomain is already configured using
   [Brevo's inbound parsing instructions](https://developers.brevo.com/docs/inbound-parse-webhooks):
   verify the domain and set MX priority 10 to `inbound1.sendinblue.com.` and
   priority 20 to `inbound2.sendinblue.com.`. Use only the new subdomain's MX;
   retain existing mail routing for all other domains.
4. Webhook `2692827` is already registered with `type: inbound`,
   `events: [inboundEmailProcessed]`, the receiving domain, and URL
   `https://bwtf-sf.vercel.app/api/ingest/sfpuc-email`. It has
   `auth: {type: bearer, token: <secret>}` as documented in
   [Brevo webhook authentication](https://developers.brevo.com/docs/secured-webhooks).
   Test actual callback authentication before enabling regular delivery.
5. In Gmail, open Settings → See all settings → Forwarding and POP/IMAP →
   Add a forwarding address. Enter the capture address. Open the received
   confirmation in `/alerts/sfpuc-emails` and expand its private original text
   to obtain Gmail's verification code or link. Complete verification in Gmail.
   Keep this setup message private.
6. Keep mailbox-wide forwarding disabled. Create a Gmail filter whose From
   field is `BeachWaterQuality@sfwater.org`, and choose “Forward it to” the
   capture address. Keep Gmail's original copy. Google's
   [forwarding guide](https://support.google.com/mail/answer/10957?hl=en) and
   [filter guide](https://support.google.com/mail/answer/6579?hl=en) describe this
   setup; forwarding filters apply to new messages.
7. Confirm one real message is captured once, then review its source and remove
   private/footer content. Approve it and confirm the Admin preview displays
   the intended date/text. Test removal and confirm Today stays unchanged. Do not
   infer delivery health solely from a lack of new bulletins.

The receiving domain, inbound webhook, and database migration are complete.
The receiver remains local. Deployment configuration and Gmail forwarding are still
pending. No live callback or actual SFPUC email has been tested yet;
development fixtures are synthetic.

## Private review and preview

Open **Admin → SFPUC emails** at `/alerts/sfpuc-emails`. The existing Admin
session is required. Select a message, inspect its original text and headers,
edit the reviewed title/text, enter the reviewer's name, and confirm the source
and absence of private information before approving. Sender matching alone
does not authenticate SFPUC. All captured messages require review, including
matched senders. Unknown senders, manual forwards, and Gmail confirmations can
be read privately and rejected without adding them to the preview.

Originals are immutable. Corrections to approved text create another review
revision. “Remove from preview” creates a rejection revision and removes the
message on the next Admin preview read. Both the page and preview API use
`Cache-Control: private, no-store`. Approval never publishes a message to Today.
An old browser form cannot overwrite a newer decision. The reviewer name is an
entered audit label under the shared coordinator session, not a verified user
identity. Review POSTs also require a session-bound CSRF token.

Original HTML is never rendered. Readable text is extracted without fetching
images or URLs. Preview rendering uses escaped text only; headers, recipients,
reviewer names, and raw payloads are excluded by the API field allowlist.
Reviewers must remove personal details within the message text itself.

## Limits and recovery

- Inbound requests: authenticated JSON, at most 512 KB and 20 items; body text
  and HTML each at most 100,000 characters. Exact envelope recipients are used
  to route messages; other addresses on the receiving domain are ignored.
- Source Message-ID plus a content hash identifies repeat deliveries. Retries
  preserve the original capture time and review decision. A reused Message-ID
  with different content is retained as another private message and marked as
  a conflict for review. Imports and live callbacks have separate provider keys;
  compare originals before approving a manually imported duplicate.
- A whole batch is validated before writing. Storage and run counts are saved
  in one transaction. A storage failure returns 503 for provider retry; malformed
  requests return 400. Provider retry/retention behavior must be checked during
  setup. Retained Gmail originals provide manual recovery if retries expire.
- Attachments are recorded as metadata only. They are not downloaded or
  publicly displayed; Brevo attachment tokens may expire. This release does
  not add an audio bucket or hotline capture.
- The capture status shows the last accepted delivery, including duplicates or
  other-recipient messages. Failed requests are logged without payloads or
  addresses. It is not a periodic mailbox health check.
- Raw emails are retained until an operator deliberately removes them through
  a separately reviewed maintenance action. No automatic deletion job is added.
  Agree a retention period before enabling routine capture.

For a saved original `.eml`, first run a dry-run import:

```sh
python -m scripts.import_sfpuc_email /path/to/message.eml
python -m scripts.import_sfpuc_email /path/to/message.eml --apply
```

Both use the configured capture recipient and expected senders. `--apply` saves
privately and does not approve a message. Imports are limited to 256 KB and preserve the
original MIME bytes privately; their capture timestamp is the import time.
Do not commit originals or recipient information as fixtures.

Disable capture and the Admin screens by setting `SFPUC_EMAIL_ENABLED=false`.
Pause forwarding/the inbound webhook as well so deliveries do not repeatedly
hit a disabled endpoint. Existing records and every other feature remain intact.

## Verification

Hosted grants confirm that `anon` and `authenticated` cannot read any of the
feature's tables/views or execute its RPCs. All three tables have RLS; both
views and RPCs use security-invoker behavior. The server role can select/insert
originals and decisions but cannot update/delete them. Security advisors added
only three informational “RLS enabled, no policy” notices: intentional for these
server-only tables with no browser-role grants. Existing warnings are unchanged.


The November 21 full pre-push check finished with 77 files passing and 2 failing
in 550 seconds. It exposed an inherited forecast-artifact freshness failure
in `tests/test_stages_build.py` and `tests/test_stages_s1.py`:
their stored manifest hash for `features/forecast/live_dashboard.py` differs
from the source file. The mismatch is already present in this branch's base
commit; the source and manifests have not been edited by this feature. Resolve
that existing build-provenance issue before pushing or deploying, without
skipping the checks or rewriting provenance merely to make them pass.

- `tests/test_sfpuc_emails.py`: parsing, direct/forwarded setup messages,
  authentication, request limits, atomic validation, retryable outages, Admin
  gate/CSRF, review confirmation/conflicts, HTML escaping, private-field exclusion,
  dates/DST, feature-off behavior, public-route absence, unchanged Today,
  and `.eml` import.
- `db/scripts/test_sfpuc_emails.mjs`: actual PostgreSQL semantics using local
  PGlite, including batch rollback, duplicates, conflicting content, append-only
  review history, withdrawal, RLS/grants, and existing-table isolation.
- Browser fixture flow: authenticated webhook → local PostgreSQL → private
  Gmail confirmation → review/approval → keyboard/mobile Admin preview → removal
  → unavailable/retry/empty states. Synthetic data only; external map/font
  requests were blocked in that fixture preview.

```sh
PYTHON=/path/to/python scripts/check.sh tests/test_sfpuc_emails.py tests/test_today.py tests/test_landing.py tests/test_static_cache.py tests/test_alert_render.py tests/test_alert_deliveries.py tests/test_email_sim.py tests/test_sample_gap_dashboard.py
PGLITE_MODULE=/path/to/node_modules/@electric-sql/pglite/dist/index.js node db/scripts/test_sfpuc_emails.mjs
```

The Python check script blanks production Supabase variables. The SQL test uses
no credentials or network calls. PGlite 0.5.16 was used for verification.
