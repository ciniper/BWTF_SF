# Mobile reader cleanup

Reported on iPhone Chrome: sideways email scrolling and the page underneath
appearing during scrolling. This update keeps the dialog fixed within the
viewport, makes the email the only scrolling pane, and preserves the underlying
page position with a fixed-body lock. Close and history controls remain visible.
The five-minute Today page refresh also pauses while reading and restarts after
closing, so it cannot silently dismiss the popup.

The follow-up navigation change makes the opener an inline linked sentence,
**See Latest SFPUC update.**, within the beach-status subheader. City samples
remains a plain sample-date label. **Older is on the left; Newer is on the right**,
with matching arrow directions and keyboard order. Local checks confirmed the
link at 320px with no status description and at 390px with a posted-beach
description, Older/Newer navigation, and focus returning to the update link.
The inline sentence uses the existing paragraph's typography and spacing. Enter
opens the popup without changing the URL hash. Past-day replay hides the latest
update link; returning to Today preserves its click handler and opens the popup.
Screenshots: `city-email-subheader-mobile.jpg`,
`city-email-subheader-desktop.jpg`, and `city-email-older-left-mobile.jpg`.

## Local browser verification

Desktop Chrome responsive view, using read-only copies of three captured City
bulletins and the real local API/sanitizer/image proxy. No database writes.
This is not a physical iPhone or an iOS browser-engine test.

| Case | Result |
| --- | --- |
| 320 × 568, 390 × 844, 430 × 932 portrait | Email scroll width equals visible width; no horizontal overflow |
| 844 × 390 landscape and normal desktop | Reader fits; Close and history remain visible |
| 900px table, 850px cell, 800px inner block, long unbroken URL | Reflows at 320px; no child extends beyond the email viewport |
| Repeated scrolling at email bottom and on backdrop | Dialog stays open and in place; underlying page does not scroll |
| Open from a page offset of 84px; Escape inside email | Original 84px page position and inline styles restored |
| Older/Newer, newest pending bulletin | History works and newly selected email starts at its top |
| Keyboard through iframe and footer | Focus remains in dialog; Escape closes; focus returns to the update link |
| API failure then retry | Recovers to bulletin without reopening |
| Empty inbox and plain-text fallback | Official links remain available; fallback has one contained scrolling pane |
| All six image requests fail | Readable alt-text replacements; links remain usable |
| Real bulletin images | All six load in original order |
| Automatic page refresh (interval shortened to 1.5 seconds in the local fixture only) | Page stayed loaded throughout reading/history navigation across multiple intervals; reload resumed after close |

Screenshots:

- `city-email-mobile-contained.jpg`: 390px portrait, beginning of bulletin.
- `city-email-mobile-footer-contained.jpg`: same viewport at the email's bottom.
- `city-email-mobile-landscape.jpg`: 844 × 390 landscape.
- `city-email-desktop-contained.jpg`: normal desktop viewport.

After an approved preview deployment, confirm on iPhone Chrome: swipe vertically
through a bulletin, try a horizontal swipe, overscroll both ends, rotate the
phone, switch Older/Newer, and close. The popup should remain contained and the
background page should return to its starting position. Physical-device touch
verification remains outstanding.
