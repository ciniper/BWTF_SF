# Admin email archive review

This change is stacked on PR #39 (`codex/latest-city-email-overlay`). It should
be retargeted to main after that PR merges; it does not change PR #39's preview.

Admin → Emails has SFPUC (received bulletins) and Surfrider (the app's sent beach
alerts) subtabs. The email simulator is linked from Surfrider. Existing SFPUC
URLs and the dashboard's delivery diagnostics remain available.

SFPUC opens the newest captured message, with a paginated history below. Matching
bulletins show the same sanitized layout as Today. Setup/other mail stays private
and is displayed as escaped text. The old approval editor is removed. Hide and
Restore append decisions through the existing revision-checked RPC; no migration,
original-message edits, or history deletion is needed. Restore uses the legacy
`approved` state internally, but new bulletins do not need this action to appear.
The original text, payload, legacy review history, and capture diagnostics are
collapsed. Legacy private reviewed-text endpoints remain for compatibility.

Visibility affects the shared database: hiding in an authorized preview also
hides the message in other environments reading that archive. Restore cannot
publish a setup message or bypass the public viewer's matching rules.

Surfrider reads only `channel=email` records from `alert_deliveries`, with 25
metadata rows per page and the body fetched for the selected delivery. The newest
email opens first; actual delivery status and test sends are labeled. Rendering
is sandboxed, subscriber links and active content are removed, and CSP allows
images only from the site's static paths or embedded data. The saved content is
not rewritten. Responsive constraints stack the alert thumbnail on narrow screens.
This page cannot send, resend, unsubscribe, or poll Brevo. It is admin-only and
returns `Cache-Control: private, no-store`.

## Checks

- Route tests cover authentication, disabled SFPUC capture with working sent-email
  archive, empty/error states, input validation, selection/pagination, escaped
  iframe content, and inert subscriber actions.
- Visibility tests cover CSRF, stale forms, hide/restore through the public
  projection, immutable originals, and ineligible mail.
- Existing capture, public viewer, admin passphrase, delivery, and simulator tests.
- Read-only production count: 269 outgoing emails (all have HTML), six captures.
  No production messages, subscriber details, or credentials used in screenshots.
- Browser screenshots use synthetic recipients and a synthetic SFPUC bulletin.
  Outgoing content uses the application's existing renderer with fixture input.

## Before merging the public viewer

Check the next 3–5 real SFPUC arrivals in the existing PR #39 preview against Gmail:
received once, correct latest/order/date, intact bulletin/images/official links,
removed forwarding/subscription details, and no sideways scrolling or background
scroll problems in iPhone Chrome. Record discrepancies with the message date and
phone/browser. A few quiet days without arrivals do not exercise capture.

The main branch's required reviews and all required checks must pass. The prior
review found the PR author was also the only listed code owner; an eligible
independent code-owner review must be resolved without bypass. Existing unrelated
suite failures are recorded on PR #39 and are not changed by this cleanup.

Browser verification used desktop Chrome at 1280×900, SFPUC at 390×844 and 360×780,
and Surfrider at 390×844. No horizontal overflow was measured in the page or
email frame. Both outgoing images loaded; the rendered archive contained no
active links. Hide/Restore, private Gmail confirmation text, and sent-email empty
and unavailable states were checked using local fixtures only. The screenshots
reflect the user's enabled Chrome dark-mode extension; no site theme was changed.
Responsive emulation does not replace a physical iPhone Chrome check.

The full suite completed with 79 test files passing and five failing in 551s.
One failure was the simulator's old top-level navigation assertion: updated for
Emails and rechecked successfully. All six relevant test files pass after the
final changes. The four remaining known unrelated failures are `test_comparison`,
`test_single_source`, `test_stages_build`, and `test_stages_s1` (date fixture,
standards literal, and two forecast artifact-hash checks).
