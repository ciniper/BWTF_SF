# Service limits and growth

Investigation baseline: November 20, 2026 (America/Los_Angeles).
Branch: `codex/service-limits-and-growth`, based on `d2144a3` (the checkout's
`origin/main` at branch creation). Investigation only; no plan changes,
production mutations, cleanup, or backup setup performed.

## Verified baseline

| Item | Observed usage | Scope / measurement |
| --- | --- | --- |
| Active subscribers | 22 email; 0 SMS; 0 currently unsubscribed | Supabase retained subscriber rows |
| New subscribers | Aug 2; Sep 3; Oct 3; Nov to date 14 | Pacific signup dates; deleted records cannot be reconstructed |
| Page views | October 645; November to query time 1,655 | Vercel production analytics; tracking started Oct 2; November incomplete |
| Comparable page views | Oct 2–20: 398; Nov 2–20: 1,640 (+312%) | UTC daily buckets, equal 19-day windows |
| November alert deliveries | 239 real sends, all marked delivered | Application delivery log |
| Peak recorded email day | 64 sends (Nov 13 and Nov 18) | UTC days, application delivery log |
| Brevo | Free, 300 shared marketing/transactional sends per day | Account usage dashboard; 300 remaining when checked |
| Brevo recent delivery health | 163 sent in dashboard's last 7 days; 100% delivered; no hard bounces or blocks | Monthly provider report could not be independently reconciled because its page stalled |
| Vercel Pro | $5.63 of $20 included credit; $20 total shown after credits | Entire Chazington team; Nov 1–Dec 1 cycle |
| Vercel CDN | 151.8K requests; 6 GB transfer | Entire team; included capacity 1M requests / 1 TB monthly |
| BWTF database | 76.05 MB reported by dashboard | 500 MB per-project Free allowance; direct live SQL measured ~64 MB at an earlier sample, so use the dashboard figure for conservative planning |
| Supabase egress | 0.173 / 5 GB current cycle; 0.426 GB previous full cycle | Entire organization; current cycle Nov 16–Dec 16 |
| Cron history | ~223,500 rows; 37.5 MB allocated | Largest database table; no cleanup job observed in active cron list |
| Supabase Auth | 0 users | Email subscribers are application records, not Auth MAU |

Vercel page views are browser measurements, not a count of distinct people.
The application suppresses reload page views; owner/developer visits can remain.
Provider dashboards and SQL snapshots have different refresh times and windows.

## Reset behavior

- Brevo Free: 300 sends each day, shared by marketing and transactional email.
  Unused daily sends do not roll over.
- Vercel: monthly usage credit and CDN allowances renew monthly. This team's
  observed cycle ends December 1. Unused credit expires. Pro usage beyond the
  credit is billed; the $20 credit is not a hard spending cap.
- Supabase: egress, MAU, invocation and message allowances renew with the
  organization's monthly cycle, next December 16 for this organization.
- Database contents, stored files, subscribers and cron history do not reset.
  Space remains occupied until data is removed or storage is otherwise reclaimed.
- Concurrent connection limits and individual function timeouts are instantaneous
  or per-execution limits, not monthly counters.
- Analytics billing counters renew; historical analytics do not become zero
  simply because a billing cycle changes. History is subject to provider retention.

## Email-frequency change and image requests

The branch already includes the 15-minute collection window, same-day repeat
suppression and the still-active-sites section described in
[the alert rollout notes](alerts-frequency-rollout.md).

Production delivery records confirm 163 November messages with renderer `021`
(last November 17 at 09:27 PST), followed by 76 with `alerts-frequency`
(first November 18 at 09:01 PST; latest November 19 at 10:32 PST).
These unequal periods and different beach conditions are not a before/after
effectiveness comparison. The November 18 peak remains in the new-policy period.

November 18 had approximately 102K function invocations, overwhelmingly email
maps and the logo; subsequent UTC days had roughly 2.4K–2.6K calls. The user
identified the expanded still-active-sites email as a likely contributor.
More images per message is a plausible explanation for greater request volume,
but request sources, repeat fetching and cache behavior remain unverified.
Do not label this as subscriber growth, an attack, or a confirmed cache bug.

## Follow-up work

- [ ] Measure sends per subscriber and peak daily sends after the frequency fix,
      with renderer/version and beach-event context. Validate the new assumption
      of 2.5 emails per email subscriber on a heavy storm day against post-fix usage.
- [ ] Reconcile provider account-wide daily totals with application deliveries;
      include other marketing, test and transactional sends sharing the quota.
- [ ] Inspect email-map/logo cache headers and request sources; confirm whether
      static requests need to invoke Flask and explain November 18's spike.
- [ ] Establish a durable subscriber/database backup and verify a restore.
      The documented Railway mirror is no longer a durable backup destination.
- [ ] Choose cron-history retention and implement bounded cleanup with reviewable
      counts and a retention policy before deleting any production records.
- [x] Replace Vercel/Supabase subscriber cutoffs with usage allowances and reflect
      the existing Vercel Pro subscription. Usage remains a forecast, not live billing.
- [ ] Add an operator view of usage, scope, measurement window, reset date and
      warning thresholds. Distinguish monthly consumption from persistent storage.

## Email cost-model update

The model now chooses Brevo Free or paid using projected peak daily emails,
rounded up, instead of the former hardcoded 50-subscriber cutoff. The user-selected
planning assumption is 2.5 emails per email subscriber on a heavy storm day after
the fixed 15-minute collection window and same-day repeat suppression. This is an
average across recipients, not an enforced per-person or account-wide maximum.

At 50 email subscribers the projection is 125 emails/day; at 120 it is 300, using
the entire free allowance; at 121 it is 303 and the model selects a paid plan.
Text-only subscribers do not use email allowance. Other account emails and a
safety buffer are not included. The page shows the projected sends and remaining
daily allowance as the subscriber count and channel mix change.

Historical station-event counts remain the conservative monthly-volume estimate
for sizing a paid plan; they cannot reconstruct exact 15-minute batches. The
other service prices retain their existing published-price snapshot.

## Infrastructure cost-model update

Vercel now always includes the existing $20/month Pro team subscription, with
$20 usage credit. Function invocations, CPU and memory use that credit from the
first unit. CDN traffic uses the included 1 million requests / 1 TB capacity;
the model selects the smallest published Flat Rate CDN tier fitting a sustained
month, with its subscription fee outside the credit. Beyond the highest tier,
the model applies on-demand CDN pricing to all CDN traffic. This forecasts a
future month's capacity, not an immediate surcharge for an isolated spike.

Supabase remains free while modeled database size is at most 500 MB and monthly
uncached egress is at most 5 GB. Either excess selects Pro. The paid estimate
assumes both active projects in the organization use Micro compute: $25 plan +
$20 compute - $10 compute credit = $35/month before overages. The BWTF disk
estimate includes 8 GB, then whole-GB allocation at $0.125/GB-month; organization
egress includes 250 GB, then $0.09/GB. Larger compute and other project usage
can increase the real bill. Email subscriber records are not Auth MAUs.

The database baseline is the Nov 20 dashboard reading of 76.05 MB, plus projected
retained message copies (a conservative addition, not a reconstruction). Pruning
copies affects this projection; future cron-history growth is not projected.
Egress models page reads only. Vercel forecasts exclude builds, email-image
traffic, other projects and unmodeled services, with compute/on-demand prices
assuming iad1. The UI separates these forecasts from the verified snapshot.

Infrastructure prices/allowances were checked November 20, 2026. Traffic
coefficients remain estimates and need calibration against observed usage.
The domain's separate purchase-at-100 assumption remains explicitly optional.

## Sources

- [Vercel team usage](https://vercel.com/chazington/~/usage)
- [Vercel Pro credit and allowances](https://vercel.com/docs/plans/pro-plan)
- [Vercel Flat Rate CDN tiers](https://vercel.com/docs/pricing/flat-rate-cdn)
- [Vercel compute pricing](https://vercel.com/docs/functions/usage-and-pricing)
- [Vercel on-demand CDN pricing](https://vercel.com/docs/manage-cdn-usage)
- [Supabase organization usage](https://supabase.com/dashboard/org/hftzlfmrpawywbgimgsh/usage)
- [Supabase billing](https://supabase.com/docs/guides/platform/billing-on-supabase)
- [Supabase pricing and compute credit](https://supabase.com/pricing)
- [Supabase backup guidance](https://supabase.com/docs/guides/platform/backups)
- [Brevo account usage](https://app.brevo.com/billing/account/plans/subscriptions)
- [Brevo Free limits](https://help.brevo.com/hc/en-us/articles/208580669-FAQs-What-are-the-limits-of-the-Free-plan)
- Production aggregate queries of `subscribers`, `alert_deliveries`,
  `cron.job_run_details`, database sizes and Vercel observability/analytics.
