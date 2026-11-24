# Admin email archive review

This change is stacked on PR #39 (`codex/latest-city-email-overlay`). It should
be retargeted to main after that PR merges; it does not change PR #39's preview.

Admin (`/alerts`) now opens Email history, with SFPUC (received bulletins) and
Surfrider (the app's sent beach alerts) subtabs. BWTF email analysis remains its
own top-level admin button and opens the existing email simulator.

**Alert controls** (`/alerts/controls`) contains Live and Alerts simulation behind
the existing Admin login and an additional, case-sensitive passphrase configured
as `ADMIN_ALERT_CONTROLS_PASSPHRASE`. Missing/blank configuration keeps controls
locked, without blocking history or analysis. Existing Admin sessions must still
unlock the second gate. Lock controls clears that grant in the current browser
session and returns to history. Changing the secret invalidates existing second
grants on deployments using the changed configuration. The cookie contains a
grant bound to the configured secret through the app's signing key, not a password.

The same second gate protects subscription reads/writes, simulation reads/writes,
watcher status, and manual dispatch before handlers are entered. Control POSTs
also require the session CSRF token. Subscription management appears only in
Live. Existing sending and subscriber handlers remain unchanged; no migrations.

Before a newly approved preview deployment, the user will configure the new
Vercel Secret for Preview branch `codex/admin-email-viewer`. Production needs its
own configured value before merging/deploying. See `DEPLOY.md`.

## Current validation limit

At the user's explicit instruction, the subscriber-panel move and subsequent
second-gate change received **source diff review only**. No tests, app/server
runs, browser checks, subscriber reads, or subscriber actions were performed for
these changes. The second gate has not been exercised at runtime. Existing
screenshots and the checks below describe the preceding archive/navigation work,
not the current gate or default landing page. Keep this PR draft for review.

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

## Earlier checks (before the subscriber-panel move and second gate)

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
and unavailable states were checked using local fixtures only. The initial archive screenshots
reflect the user's enabled Chrome dark-mode extension; no site theme was changed.
Responsive emulation does not replace a physical iPhone Chrome check.

The full suite after the naming update completed with 80 test files passing and
four failing in 563s. All six relevant email/admin test files pass. The four known
unrelated failures remain `test_comparison`, `test_single_source`,
`test_stages_build`, and `test_stages_s1` (date fixture, standards literal, and two
forecast artifact-hash checks).

Navigation naming update: verified the desktop dashboard and shared admin tab row
show Live, Alerts simulation, BWTF email analysis, and Email history. Analysis has
its own active tab and matching page heading; Email history has its own active
tab and the two email-source subtabs. Clicking Alerts simulation still switches
the existing dashboard panel. A 390px check found no horizontal page overflow.
Updated screenshots: `navigation-desktop.jpg`, `analysis-desktop.jpg`,
`dashboard-navigation-desktop.jpg`, and `navigation-mobile.jpg`. These show the
browser without its earlier dark-mode extension effect.
