# Sample-age mobile refinements

Branch: `design/sample-age-refinements`, based on Brian's PR #9 at `0aae369`.

## Changes

- Enabling Surfrider no longer inserts text into the four zone tiles. Volunteer markers, tooltips, results in the selected zone's station strip, and the map's layer status remain available.
- Surfrider samples become stale when their age exceeds seven calendar days in Pacific time. Exactly seven days remains fresh; eight days is stale. The map, legend, station chips, and explanatory key use the same constant. City posting colors are unaffected.
- Standalone sample facts use sentence case: `Today`, `Yesterday`, `Between today and yesterday`. Embedded ages stay lowercase, as in `sampled yesterday`. Source labels are intentionally uppercase through CSS; the underlying labels use sentence case.
- User-facing Today copy uses American `colors`, `colored`, and `Gray`. Internal CSS class names are retained.
- The roadmap records the later task to combine not sampled, no result, and stale result into one display category. This branch does not implement that policy.

## Headline comparison

These are actual outputs of `today_board`, comparing Brian's PR base (`db52f8c`) with his PR head. The mobile-refinement branch retains his new headline text.

| Fixture | Before Brian's PR | Brian's PR and this branch |
| --- | --- | --- |
| 17 clear statuses | All 17 monitored beaches meet state standards today. | No warnings posted at any of the 17 monitored beaches. |
| One posted, 16 clear | Posted at 1 beach. 16 others meet state standards. | Posted at 1 beach. 16 others have no warning. |
| One discharge, one posted, 15 clear | Sewage discharge at 1 beach. Posted at 1 beach. 15 meet state standards. | Sewage discharge at 1 beach. Posted at 1 beach. 15 have no warning. |
| No usable status | Beach status is unavailable right now. | Beach status is unavailable right now. |

## Email alignment

The repository's email renderer already reports official posting/discharge events, so these headlines are consistent with it. A single-site email says `Ocean Beach at Lincoln Way is posted.` A combined email says `Sewage discharge at 1 beach. Posted at 1 beach.` It makes no claim about all other beaches meeting standards.

The homepage is a current citywide snapshot; an email describes the events that triggered it at its timestamp. A later homepage can therefore show a different count without contradicting the email. Clear-downs are logged rather than sent as all-clear emails. Surfrider freshness is a display policy, not an alert trigger.

Evidence: `features/alerts/render.py` (`_fallback`, renderer version 021), `db/migrations/021_live_status_to_today.sql` (production renderer definition), and `db/migrations/009_simulation_provenance.sql` (log-only clear-downs). This review checked repository definitions, not the currently deployed database function.

No email copy, dispatch logic, subscription rules, or headline-generation logic was changed in this branch. Brian's separate sample-evidence issue is outside these requested refinements.

## Validation

- Ran the standalone Today, Landing, Alert Render, Clock, Risk Levels, and Static Cache test suites; all passed. The environment has no pytest installation. Existing live-source fallback tests encountered unavailable network sources and passed their fallback assertions.
- Added sentence-case rendering coverage for today, yesterday, and split sampling dates; updated the seven/eight-day threshold boundary coverage.
- Rendered Brian's template and the changed template against identical fixture data at 390 × 844. With Surfrider enabled, the zone grid decreased from 453 px to 283 px; the map moved up 170 px. Exact heights depend on the data and viewport.
- Verified toggling Surfrider preserves zone tile text and height; selecting a zone still exposes volunteer result chips; eight-day-old results render hollow, with `over 7 days old` in the legend and explanatory key.
- Rendered the old/new headline outputs alongside actual email-renderer headline excerpts using the same status scenarios. These are local fixture previews, not live measurements or sent emails.
