# Live corrections (live_v1) — what observed CSO flags and bacteria results do to the forecast, and how to grade it

**Status (2026-09-26, later the same day): §2–§4 are built as `src/models/live_rules.py` (version `live_v1`, "live corrections" — not a third stage: edits before and after stage 2), wired
into `live_dashboard` for the live forecast and the live-mode hindcast. The downgrade is a schedule by quiet days (Chase):
R = 0.60 the morning after, 0.87 after one quiet day, 0.95 after two, bayside basins only, none for the Westside. Each day's payload carries a `live_corrections` block (every
rule that fired, from → to) beside `plain` (the model alone), and the page shows them as badges with a
"Live corrections v1: on/off" toggle; the server switch is the env var `LIVE_CORRECTIONS` or the `watcher_config`
key `live_corrections` ("off"). The old observed-CSO override is now the `cso_onset` rule, so "off" is the pure two-stage model. The downgrade is gated on the watcher being live and
ticking (`watcher_runtime`). §5, the replay, is built too: `src/models/replay_live.py` → `reports/2026-09_live_replay.html` (archive era 2016-17; the watcher era joins after the next rescore). Graded on the start-of-day forecast (a day never sees its own flag). Result on the bayside zones at 50%: the model alone caught 21 of 35 bad days for 1 false alarm (cost 29); live_v1 caught 32 of 35 for 7 (cost 13). At 25% live_v1 costs 16 against the model's 22 (the sub-50% sample floors, since zeroed, had pushed it to 34); the conservative downgrade changed one day. **Synthetic-feed replay (2026-09-27, `--synthetic` → `reports/2026-09_live_replay_synthetic.html`):** the filed discharge days as onsets over every out-of-sample day (Jul 2023 → Aug 2026, 1,144 days, 88 onset basin-days), degraded with the archive's rates (13% never flagged, 60% a day late), BeachWatch CSO postings as flag windows, mean of 5 draws. Primary ruler at 50%: model alone 129 of 182 bad days for 38 false alarms (cost 144) → live_v1 150 for 65 (cost 130; perfect feed 115); bayside 72 → 58; postings 311 → 257. The downgrade earns 2–7 points (6 fewer false alarms for 2 more misses at 50%). **Sample floors below 50% zeroed (Chase, 2026-09-27):** they never changed a 50% call but turned 20% days into alarms at the 25% line (cost 147 → 207). The empirical rates stay in `live_rules.SAMPLE_RATES` as the evidence; `RULES` keeps a floor only where the rate is ≥ 0.5 (today the East's 0.80 tail floor) and every clean-sample cap. Re-run: at 50% nothing changes (synthetic 130, archive bayside 13); at 25% live_v1 costs 149 against the model's 147 (bayside 60 against 70). What is left at 25% is the Westside (90 against 77), where the downgrade is off and the onset and anchor rules only add. The `all_floors` replay variant keeps the old behaviour as the counterfactual. Tests: `tests/test_live_rules.py`.**

*Design, 2026-09-26. It answers Chase's three questions (how much to upgrade on a
confirmed CSO, how much to downgrade when an expected CSO did not appear, what a confirmed or clean sample
should do) with the numbers we have, and lays out the replay that would grade it. Companion to
`RETRAIN_PLAN.md` (models) and `docs/OVERVIEW.md` (the system).*

## 0. What the live forecast does today

- Past complete days are scored on the two NOAA gauges (with the gauge-outage rule); today on NWS hours so
  far plus the ECMWF forecast; the next five days on ECMWF. Stage 2 composes the last eight days.
- **Observed CSO:** a watcher transition into `cso` for a station sets that basin-day's stage 1 probability to
  **1** on the onset day. That is the only live correction. It never lowers anything, and samples are not used.

## 1. Evidence

**Feed flags vs the filed record** (2016-17 Poo Bot archive vs CIWQS, bayside only — the archive does carry Westside flags (Sea Cliff and neighbours, 8 onset days) but CIWQS has no Westside per-event records before 2018 to check them against, so Westside recall is unmeasured):

| Measure | Value | n |
|---|---|---|
| Recall — CIWQS discharge days flagged the same or next day | **87%** | 13 of 15 |
| Precision of every flagged basin-day (flag within ±1 day of a filed discharge) | 39% | 20 of 51 |
| Precision of flag **onsets** (first day a structure shows active) | **67%** | 16 of 24 |
| Of the matching onsets, how many appeared the **day after** CIWQS's date | 10 of 16 | |

The flag stays up two to four days after a discharge (SFPUC's advisory window), so continuing flag days are not
new discharges. The onset usually lands a day after the filed date — the filed date is the discharge start,
often overnight; the feed updates when staff post the beach.

**How bacteria results carry from one sample to the next** (scorecard samples, pairs of sampled days ≤ 3 days
apart, zone-level "over standard"):

| Zone | In the week after a discharge: P(elevated next \| elevated now) | … P(elevated next \| clean now) | Otherwise: P(elev \| elev) | … P(elev \| clean) |
|---|---|---|---|---|
| Ocean Beach | 40% (n=45) | 42% (n=24) | 6% (n=18) | 75% (n=12) |
| Baker–China | 14% (n=28) | 36% (n=14) | 20% (n=66) | 41% (n=17) |
| North Shore | 35% (n=34) | 67% (n=9) | 16% (n=56) | 44% (n=16) |
| East | **80%** (n=208) | **67%** (n=24) | 38% (n=186) | 62% (n=26) |

Read: in the East a clean sample during a discharge tail is followed by an elevated one two times in three —
a single clean bottle is weak evidence of recovery there. On the Westside an elevated result rarely repeats.
(The "otherwise / clean → elevated" cells are resamples that followed a clean routine sample; small n.)

## 2. Upgrade on a confirmed CSO

- **Onset day → p = 1** for the basin, as now. Rationale: 67% of onsets sit within a day of a filed discharge,
  and the cost of an under-call on a real discharge day is the higher one.
- **Anchor the day before too.** Because the feed onset lags the filed date by a day in 10 of 16 archive cases,
  a flag first seen on day D most likely means a discharge on D−1. Proposal: on onset, set p(D−1) = 1 as well
  when the model had p(D−1) ≥ 0.25 or ≥ 0.25" fell on D−1; otherwise leave D−1 alone. The replay (§5) decides
  whether this helps.
- **Continuing flag days are not new discharges.** Do not set p = 1 again on D+1, D+2 while the flag stays up
  (that would compose several phantom events). Treat a persisting flag as stage 2 evidence instead: hold the
  group's risk at no less than the large-event curve's value for that day-since-onset while the flag is up.
- **Size.** The feed carries no volume. Use the volume head's prediction; if the flag persists two days or
  more, use the large-event curve (large events post longer).

## 3. Downgrade when an expected CSO did not appear

Bayes, with a recall that grows with every quiet day observed after the storm day (Chase, 2026-09-26: conservative
the morning after; "if there are two quiet days after the big storm there should be a more significant downgrade —
farther from the event, more proof there really was no CSO"). At the start of day T, a past day D the model put at
p, with no onset for the basin on D … min(T−1, D+2), becomes

    p' = p · (1 − R[k]) / ( p · (1 − R[k]) + (1 − p) ),   k = quiet days fully observed after D = (T − D) − 1

| quiet days after D | R[k] | source | p = 0.9 → | 0.7 → | 0.5 → | 0.3 → |
|---|---|---|---|---|---|---|
| 0 (the morning after) | **0.60** | conservative — the flag can lag | 0.78 | 0.48 | 0.29 | 0.15 |
| 1 | **0.87** | the archive: 13 of 15 bayside discharge days flagged same or next day | 0.54 | 0.23 | 0.12 | 0.05 |
| 2 or more | **0.95** | assumed — the archive's two misses were never flagged at all; to be measured in the watcher era | 0.31 | 0.10 | 0.05 | 0.02 |

- **Bayside only** (North Shore, Central, Southeast). **Westside:** recall unmeasured — the archive's Westside flags
  cannot be checked against CIWQS before 2018 — so no downgrade until the watcher era measures it.
- Only days the model actually called (p ≥ 0.15); tiny days are left alone.
- An onset on D+1 cancels the downgrade of D (the discharge probably started on D; the anchor rule in §2 usually
  already set D to 1).
- **What it does downstream:** the false forecast's tail dissipates as the quiet days accumulate. A 0.7 Ocean-Beach-
  sized day contributes 0.7 × 0.54 = 0.38 to the next morning's risk on the plain model; 0.26 the morning after
  (R = 0.60); 0.12 the day after that; 0.06 with two quiet days behind it — on top of the impact table's own decay.
- **Grading:** a downgraded day is scored at p', so a wrong downgrade still costs.

## 4. Bacteria results in the live cast

Results arrive late: DataSF one to two days after sampling, the feed's sample colour when the lab posts. Apply
each rule from the day the result is known, and only for the next-sample horizon (three days), after which the
model's own composition resumes.

- **Elevated sample at a group, in a discharge tail:** floor the group's risk at P(elevated next | elevated now)
  for that zone — East 80%, Ocean Beach 40%, North Shore 35%, Baker–China 14%. In the East an elevated bottle
  keeps the zone red; on the Westside it barely moves it, which is what the samples say. **Policy (Chase,
  2026-09-27): a floor applies only where the rate is ≥ 0.5, so today the East's alone.** A 40% floor never
  changes a 50% call but turns every 20% day into an alarm at the 25% line; both replays priced that at 60
  points. The rates stay in `live_rules.SAMPLE_RATES`.
- **Clean sample in a discharge tail:** cap the group's persistence term at P(elevated next | clean now) — East
  67%, Ocean Beach 42%, North Shore 67% (n=9, weak), Baker–China 36%. Do **not** zero the tail on one clean
  bottle. This departs from SFPUC practice (a clean sample lifts the posting); the East data say that practice
  is optimistic there.
- **Elevated sample with no discharge in the prior week (dry weather):** the same floor at the dry-weather rate
  (East 38%, Baker–China 20%, North Shore 16%, Ocean Beach 6%) for three days. Every rate is under 0.5, so
  under the policy above this rule is off today; the rates are recorded. This is the seed of the non-rain
  model in TODO; it says "a dirty beach tends to stay dirty" and nothing about why.
- **Clean sample, no recent discharge:** nothing to do.

## 5. How to grade all of it: the replay

Compose the same days five ways, each using only what was known by the end of that day, and grade them on the
primary ruler (discharge days + samples) and on postings:

| Variant | Adds |
|---|---|
| (a) hindcast | model probabilities only — what the Model check shows today |
| (b) + CSO upgrade | onset day p = 1, as served (and the D−1 anchor as a sub-variant) |
| (c) + downgrade | §3 posterior on unflagged expected days, bayside only |
| (d) + samples | §4 floors and caps |
| (e) + persisting flag | §2 hold while the flag stays up |

Eras with flag data: **Mar 2016 → Jan 2017** (the archive: 55 active-CSO snapshots, 153 with posted stations)
and **Aug 2026 →** (the watcher's alert_log). BeachWatch postings and the samples cover both. Report per zone:
bad days caught, false alarms, days each rule changed the zone's call, and the same for postings.

Caveats to build in: grading a sample-informed composition against samples is partly circular inside the
three-day horizon — grade (d) against the *next* sample after the one it used, or lean on postings for (d).
Expect (b) and (c) to move the East most (long tail, reliable flags). In 2016-17 the Westside discharge labels
are the archive onsets themselves, so grade the Westside there on samples and postings only. Everything above is a serving-side and evaluation change; the models and the impact
table are untouched.

## 6. Numbers still missing

- r and onset precision for the **Westside**, and for every basin in the watcher era (first estimate after the
  2026-27 season's CIWQS refresh).
- Whether the feed's one-day lag holds today (compare alert_log onsets with the 2025-26 CIWQS dates once filed).
- Posting-lift behaviour vs samples: how often SFPUC lifts a posting on a clean sample that the next sample
  contradicts (BeachWatch reopen dates vs DataSF), per zone.

## 7. live_v2 (2026-09-29): the samples come from the beach map first

live_v1 read bacteria results from DataSF alone. Measured this week, DataSF publishes about five days after sampling (the Monday 21 September round appeared on the 26th; as of the 29th nothing newer than the 23rd exists), while the sample rules act on the three days after a sample. By the time DataSF had a result, the days it could move were past: the rules could relabel history but never touch today or a forecast day.

SFPUC's beach map is faster. The watcher polls it every minute and has recorded it per station-day since 2026-09-27 (`feed_station_days`, migration 012) and its transitions since 2026-08-20 (`alert_log`): Sunnydale Cove was sampled on the 21st and posted on the map on the 23rd at 08:55; Baker Beach at Lobos Creek was posted on the 22nd and cleared on the 23rd after its resample. One to two days, against five.

**Reading the map as results** (`live_dashboard._feed_sample_flags`): for each station-day the watcher recorded, the map's `sample_date` names the latest sample. A station posted for bacteria (status `posted`, not the precautionary `cso`) → that sample was over standard. A station clear all day (`status_max = ok`) with a sample date → that sample was clean. A day the sign came down is ambiguous and says nothing; an elevated station outranks a clean sibling within a beach group. DataSF's lab record overrides the map wherever it has published the same group-day, so the map only fills the days DataSF has not reached. The rule arithmetic (§4) is unchanged; both replays grade live_v2 exactly as they graded live_v1, and the version is bumped because what is known when has changed.

**Fixed with it:** `_cso_flag_days` still read `watcher_state`, dropped by migration 012, so every call failed and returned no flag windows: the flag hold and the no-flag downgrade's flag windows have been empty in production since 2026-09-27. It now reads `watcher_state_shadow` and ignores simulated rows.

**To measure next:** the map's own lag for clean results (when `raw_last.sample_date` advances in `feed_station_days` relative to the date it names), and a real-arrival replay variant once a season of `feed_station_days` exists (§5).
