# SFPUC email capture

Brevo captures incoming email in a private archive. When enabled, **See Latest
SFPUC update.** in Today’s subheader opens the newest matching City bulletin,
with Older on the left and Newer on the right. Matching arrivals appear
automatically. Admin’s **Email history → SFPUC** shows the same cleaned bulletin
and allows Hide/Restore. There is no approval step or editable copy.

Original email content is retained, including the three previously captured
bulletins and future arrivals. Automatic forwarding/footer removal, HTML
sanitization, image placement, and links are applied when displaying an email;
they do not rewrite the stored original. Gmail confirmations and other setup
mail remain private in Admin.

## Storage and cleanup rollout

The original `20261122020135_sfpuc_email_capture.sql` migration is already
applied to BWTF project `fqujpgxgilzgpkleehvg`. Do not rerun or rewrite it.

The `20261125074617_sfpuc_email_visibility.sql` migration was **applied on
November 24, 2026 (Pacific)** after production deployment
`dpl_9WCFfkfv2zgsUMumYNKLARN2nwDL` reached READY on merged commit `c3aa2e1`.
The filename matches Supabase’s recorded version; it supersedes the prepared
filename `20261125052538_sfpuc_email_visibility.sql` without changing its SQL.
Do not apply it again.

Read-only before/after checks confirmed identical original-content fingerprints
for all six archived messages, identical visibility states/revision counters,
and an unchanged fingerprint for all six capture-log records. The legacy table,
view, and review RPC were removed. Both remaining tables have RLS enabled;
browser roles cannot read originals or execute the visibility RPC. The service
role can update visibility but cannot update original body text. No tests,
subscriber access, email sends, or Hide/Restore actions were performed.

The feature now has two tables and one view:

| Object | Purpose |
| --- | --- |
| `sfpuc_email_messages` | Private original messages/payloads plus current `visibility_hidden` and a concurrency counter, `visibility_revision` |
| `sfpuc_email_ingest_runs` | Counts for successful deliveries, duplicates, and ignored recipients |
| `sfpuc_email_inbox` | A query over originals/current visibility; no duplicate email storage |

The migration transfers each message’s latest hidden/shown decision and
revision counter, then drops `sfpuc_email_reviews`, `sfpuc_email_reviewed`, and
the old review RPC. Old edited titles/bodies and review history are discarded.
It does not delete, replace, or re-ingest any captured email, and does not alter
capture timestamps, original text/HTML/payloads, or capture logs. An email with
no prior review remains automatically eligible. Previously hidden mail stays
hidden. The view retains `status = 'rejected'` as its internal compatibility
label for Hidden; it no longer represents an approval workflow.

**Completed rollout order (retain for future releases sharing this database):**

1. Obtain approval for the new app deployment/merge, with required reviews and
   checks satisfied without bypass. Deploy the new app before applying cleanup.
   It can read the existing archive through the existing inbox view without
   fetching edits. Until the migration is applied, Hide/Restore is unavailable
   and its buttons are omitted; original email viewing/capture continue.
2. Before the separately approved database migration, confirm production uses
   the new app. Old deployments that still query reviews must no longer be used
   for Admin email history. Arrange a database backup/recovery point if the old
   review text needs to be recoverable: its deletion is intentional.
3. Apply only the prepared cleanup migration. It locks the two affected email
   tables while transferring state, uses a five-second lock timeout, and runs
   in a transaction. Unknown dependencies stop the drop (no `CASCADE`). If it
   fails, the schema/data changes roll back; viewing/capture continue on the old
   schema and Hide/Restore remains unavailable until migration succeeds.
4. After application, manually inspect the three retained bulletins and the
   next real arrivals: newest-first ordering, images/links, forwarding/footer
   removal, mobile scrolling, and unchanged source content. A day is useful
   when new bulletins arrive; elapsed time alone does not validate capture.

A preview may be deployed against the old schema to inspect the cleaned UI,
but do not drop the shared review table just to enable preview controls while
old production still depends on it. After cleanup, roll back only to an app
version that uses current visibility. Restoring the old review editor requires
schema/data restoration; do not deploy an older incompatible build blindly.

No migration or deployment occurs merely by editing or pushing a branch.
Both require explicit approval. The user approved this cleanup after merging
PR #39; only the email migration was applied. No tests were run.

## Configuration and receiving setup

| Variable | Purpose |
| --- | --- |
| `SFPUC_EMAIL_ENABLED` | `true` enables capture, Admin email screens, and the public viewer; default off |
| `SFPUC_EMAIL_RECIPIENT` | `sfpuc@inbound.surftober.com` |
| `SFPUC_EMAIL_SENDERS` | Optional expected senders; default `beachwaterquality@sfwater.org` |
| `SFPUC_EMAIL_WEBHOOK_TOKEN` | Independent random bearer secret, at least 32 characters; same value in Brevo and the production receiver |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Existing server-only database configuration |
| `ADMIN_DASHBOARD_PASSPHRASE` | Required Admin login secret; no default |
| `ADMIN_ALERT_CONTROLS_PASSPHRASE` | Separate secret for Live/Alerts simulation; not required to view Email history |
| `FLASK_SECRET_KEY` | Existing Admin session signing key |

The receiving address is `sfpuc@inbound.surftober.com`. The existing Brevo
inbound webhook `2692827` targets
`https://bwtf-sf.vercel.app/api/ingest/sfpuc-email` for
`inboundEmailProcessed`. Do not create a duplicate webhook for the domain.
Gmail selectively forwards City emails to the capture address; Gmail
verification messages are readable only in Admin. Preview deployments read the
shared archive and do not need their own incoming deliveries.

The user confirmed `BeachWaterQuality@sfwater.org` as the notification sender.
This is also the requested help/hotline contact note; its ability to accept
help enquiries has not been verified. The official Beach Hotline is
415-242-2214. No hotline recordings are captured.

## Isolation and access

The feature package does not import the watcher, notifier, forecast engine, or
status classifiers. Email content does not alter map colors, sample dates,
water-quality calculations, or automatic alert sends. Surfrider sent-email
history remains separate and reads the existing delivery archive.

The capture RPC atomically stores originals and delivery counts. Duplicate
source-key/content-hash pairs are ignored; content conflicts remain separately
archived. Hide/Restore uses `sfpuc_set_email_visibility`, an atomic
compare-and-set update: stale browser forms return a conflict, not an overwrite.
The private route requires the Admin session and a session-bound CSRF token.
Hiding affects every environment using the shared archive.

RLS stays enabled and browser roles have no table/view/RPC access. The
service role can read/insert originals, but UPDATE is granted only on the two
visibility columns. It cannot update original email content or delete archived
messages. Views/functions use `security invoker`; public function execution is
revoked. The migration does not touch subscriber, watcher, delivery, sample,
forecast, cron, or other application objects.

The old edited-text API `/alerts/api/sfpuc-updates`, review POST, draft-cleanup
POST, JavaScript, and template are removed. Automatic cleanup helpers remain
because both Admin and Today use them to display the original bulletin.

## Public viewer and images

`/api/city-emails` fetches only when the viewer is opened, starts at the newest
capture on reopen, and paginates using capture-time/ID cursors. Each message
retains its own email date (or explicitly labeled capture date), independent
of the map. The private `/alerts/sfpuc-emails/today-preview` uses the same viewer.

Candidates must have an `SF BEACH` subject and either the expected sender or a
recognized forwarded envelope naming that sender. This is a feed filter, not
cryptographic sender authentication. Gmail setup and unrelated mail stay out;
hidden messages are excluded on subsequent reads, including image requests.

The HTML projection preserves table layout, inline images, telephone links,
and social links. Recognized Gmail forwarding blocks and recipient/subscription
footer paragraphs are removed. Pinned `nh3==0.3.8` sanitizes allowed HTML and
bounded layout styles. Scripts, forms, embedded content, event handlers, remote
CSS, hidden content, and unsupported images are removed. A sandboxed iframe
and restrictive CSP prohibit scripts/forms/top navigation. Links open a new
tab with no referrer. Provider click links remain, so click tracking is not
eliminated. Unsupported forwarding HTML falls back to cleaned text.

Visible raster images from the exact SFPUC email CDN
`d31hzlhk6di2h5.cloudfront.net` load automatically through the image proxy.
Known hidden/1×1 images and other origins are excluded. Downloads require HTTPS,
no redirects or inherited credentials, raster MIME/signature checks, timeouts,
a 3 MiB per-image cap, and at most 12 images. No images are fetched during
capture or stored as bytes in Supabase. Attachments and `cid:` images are not
supported. Images removed with private wrappers/footers cannot be fetched
through public image routes. Unavailable images leave readable fallback text.

All email responses use `no-store`. The popup has one vertical email scroller,
keeps Close/history controls visible, locks the background, and restores focus
and page position on close. Today’s refresh pauses while the popup is open.
The private preview is noindex and omits analytics/timed refresh.

## Validation status

Earlier viewer screenshots and checks are recorded under
`docs/review/city-email-viewer/` and `docs/review/admin-email-viewer/`. They
predate this cleanup and do not validate it. Current Python test definitions
were updated for removed editing routes, original/current-state reads,
Hide/Restore concurrency, and the pre-migration state. The offline PGlite
script now covers preservation of three originals, latest-state transfer,
legacy-object removal, stale writes, capture retries/atomicity, grants, and RLS.
**These updated tests have not been run.** No subscriber reads, sends, browser
checks, or local app startup were performed. The authorized live migration was
verified through read-only data fingerprints and schema/privilege metadata.
