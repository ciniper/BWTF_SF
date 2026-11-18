# Alert frequency — simulator and reduction plan

Extracted from `discharge-observations` at `a7d0c247cb488110dce7800a5b359eb99842d907` (2026-11-17).
The simulator and email previews are implemented; the delivery-policy changes below remain held and are not active in the live sender.

The original feed recordings and broader discharge observations remain on the source branch. References below to `feed/changes.log` refer to that branch's `docs/discharge-observations/feed/changes.log`.

## Too many alert emails on a storm day
- Because our watcher checks so often, a storm day sends tons of emails.
- SFPUC sends far fewer updates (e.g. one morning posting email covering all three Baker sites), so subscribers aren't spammed.
- To consider: batching or throttling our alerts (e.g. one email per status change per area, or a daily cap) so a storm day doesn't flood people.
- **Postings flip on and off quickly, and each flip is an email.** On 11/15 (from `feed/changes.log`): Ocean Beach postings came down at 10:13am, Vicente's CSO cleared at 10:28am, Baker went back up at 4:37pm, Pacheco came down at 4:41pm, Ocean Beach went back up at 6:57pm, Crane Cove at 6:59pm, and Vicente's CSO returned at 7:02pm. That's 7 changes in about 9 hours, several only minutes apart. A short hold before alerting (e.g. wait ~10–15 minutes and send one email for everything that changed) would merge the ones that land close together.
- **Re-alert rule (decided 11/17, held):** if a station already had a discharge flag earlier the same Pacific calendar day, don't send another discharge email for it that day. If the flag comes back the next day, it emails as usual.
  - Why a calendar day and not 72 hours: if SFPUC takes the flag down and a real discharge happens the next day, people should hear about it.
  - The rule has to check when the flag was up, not when we last emailed. Lincoln's and Fort Funston's earlier discharge emails went out on Fri 11/13, so "already emailed today" would have caught neither of 11/17's repeats.
  - What it catches on 11/17:
    - Lincoln: off 8:34am, back on 9:11am, which sent the repeat email.
    - Fort Funston: off 8:24am, back on 9:27am, which sent another repeat.
    - (Times are from the Postings Timeline. The recorder logged 8:35 and 9:09 for Lincoln.)
  - Known edge: a flag that drops at 11:50pm and comes back at 12:10am still emails twice. That's acceptable.
  - How: the live sender is the Postgres tick (`db/migrations/004_pg_live_dispatch.sql`). It emails on every move to a worse status and has no memory of past alerts. A new migration would check `alert_log` for a discharge flag on that station (an upgrade into discharge, or a clear from it) earlier the same Pacific day.
  - **Audit, Fri 11/13 to Tue 11/17, someone subscribed to every station.** Rebuilt from the Online Postings Timeline (`/cso-history/api/events`, read 11/17 4:38pm). The sender sends one email per minute that has at least one new posting or discharge.

    | Day | Today | Same-day rule | Rule + 15-min hold |
    |---|---|---|---|
    | Fri 11/13 | 6 | 6 | 2 |
    | Sun 11/15 | 5 | 4 | 2 |
    | Tue 11/17 | 3 | 1 | 1 |
    | **Total** | **14** | **11** | **5** |

    Sat and Mon had only clears, so no emails.
    - The rule drops three emails: Vicente at 7:03pm on 11/15, and Lincoln at 9:11am and Fort Funston at 9:27am on 11/17.
    - Friday's six emails all came from first flags of the day, so the rule doesn't touch them. They cluster in two bursts (6:32–6:35am and 12:45–12:49pm), which is what the 15-minute hold would merge. That column is approximate: changes within 15 minutes of the first change go into one email. The window starts at the first change and doesn't restart on later ones, so an alert is never held more than 15 minutes.
    - **Trade-off to check:** Vicente's flag came back 8½ hours after it cleared at 10:28am, so it may have been a real second discharge. An all-stations subscriber still got the 6:58pm Ocean Beach email, but someone subscribed only to Vicente would have heard nothing. Check whether SFPUC reported a second discharge that evening.
    - Not cross-checked against the actual delivery records. The Supabase connector here points at a different project.
    - **Tool (built 11/17):** Admin → Email simulator (`/alerts/email-sim`, `features/alerts/email_sim.py`). Pick a date range, what someone subscribes to (every station, a zone or one station), a hold, the window type and the same-day rule. It compares the count with today's sender. `tests/test_email_sim.py` pins this audit's 14 / 11 / 5. Each email can be previewed as the live template writes it, either as today's "only new alerts" or as a "running list" with a mock-up "Still in effect" section. On Sun 11/15 at 7:13pm, the running list still shows Vicente as discharging, even though the same-day rule skipped its own email.
  - The new migration would still log the status change, so the Today replay and event start and end times stay right. The Python watcher (`features/alerts/watcher.py`) runs in shadow mode and would just need to match.
  - **Orange → red emails too.** On our map orange is posted and red is discharge. Posted → discharge counts as a worse status, so it emails right away, usually a few minutes after the posting email. Since Friday: Baker West 6:32 → 6:35am Fri, Lincoln 12:45 → 12:48pm Fri, Baker West 4:37 → 4:38pm Sun, two emails each time. The 15-minute hold merges each pair into one email that shows the discharge. The same-day rule treats posted → discharge as a discharge flag. Red → orange (discharge → posted) sends nothing, now or under the plan.
- **Idea (11/17, held): add a "Still in effect" section to every alert email.** Today an email lists only the stations that changed in that minute for that subscriber (`bwtf_dispatch_live` and `bwtf_render_alert`, `db/migrations/021_live_status_to_today.sql`). Nothing shows their other sites that are still posted or discharging.
  - The subject and lead stay "what just changed", so a new alert still stands out.
  - Below that, a new section lists the subscriber's other stations that are currently posted or discharging, read from the live station status when the email is sent.
  - This fits the same-day rule: a flag that comes back skips its own email, but it still shows in "Still in effect" on the next one. It also fits the hold: each email gives the whole picture at that moment.
  - Limit: clears don't send email, so the list is only current when an email goes out. If everything clears, nobody hears until the next alert.
