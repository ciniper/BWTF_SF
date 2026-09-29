# Stage 2 v3 — "shares from the water": findings (2026-09-28)

**Status: negative result. The code was not merged (Chase, 2026-09-28). The remote
branch was deleted on 2026-09-29; the code survives only as local branch `stage2-v3`
(commit `9eea48a`) in Chase's clone.** This note keeps what the
investigation found so the question does not have to be re-opened from scratch.

## The question

Stage 2 `v2` (the outfall split) multiplies a basin's discharge probability by the
share of that basin's discharge days on which the beach group's *own* outfalls took
part, read off the CIWQS record and the outfall→beach map (`shared/outfalls.py`):

| group | large-event share | small-event share | source of the share |
|---|---|---|---|
| Ocean Beach | 1.00 | 0.46 | 45 of 51 Westside discharge days attributed; 12 of 26 small ones |
| Baker–China | 0.80 | 0.81 | 49 of 51 Westside days |
| Aquatic Park | 1.00 | 0.50 | 30 of 40 North Shore days |
| Crissy Field, Mission Creek, Southeast | 1 | 1 | every basin day is attributed (identity) |

The analysis report scores the split a clear win under the primary ruler (discharge
days + samples: logit_v1_s2v2 136 → 116 at 25%) and a small loss under the postings
ruler (Ocean Beach posted days caught 40 → 33 of 50 at 25%). The disagreement is about
one thing: what happens on Ocean Beach after a **Sea Cliff-only** Westside discharge.
SFPUC posts Ocean Beach; the split says the beach is mostly not fouled. The map cannot
settle that, so the recommendation was to refit the Ocean Beach shares from the water
(sample outcomes) rather than from the outfall map.

## What v3 does

Same arithmetic as v2 — `p_group(D−k) = p_basin(D−k) · [w·g_large + (1−w)·g_small]`,
`w = V/(V+median)` — with each size's share replaced by a blend of the map and the water:

    share = map + (1 − map) · f

`f` is the fraction of the non-attributed discharges' effect the beach actually
shows: the group's over-standard rate on **pure non-attributed tails** (a
non-attributed basin discharge in the last 3 days and *no* attributed one in the last
7), above its dry-weather baseline, divided by the same excess after attributed
discharges. `f = 1` means the beach is fouled after a Sea Cliff-only event as much as
after its own; `f = 0` means the map is right. The water estimate is blended with the
map at 10 pure-tail sample-days of prior weight, and ignored when the attributed
excess is below 0.05. Identity groups stay at 1. Fitted on the training window
(2016-03-01 → 2025-10-31), so the holdout stays clean.

A first estimator compared *any* discharge tail to *attributed* tails. It was
circular: the two sample sets overlap about 90% (most Westside discharges involve
the Ocean Beach outfalls), so the ratio was near 1 by construction. The pure-tail
definition above fixes that.

## What the water says: nothing yet

| group | non-attributed basin days | pure tails with a sample | over standard | attributed tail rate (large / small) | dry-weather baseline |
|---|---|---|---|---|---|
| Ocean Beach | 14 | 1 | 0 | 0.67 / 0.33 (43 / 27 sample-days) | 0.044 (298 days) |
| Baker–China | 10 | 2 | 0 | 0.39 / 0.35 (23 / 20) | 0.166 |
| Aquatic Park | 10 | 5 | 0 | 0.48 / 0.33 (25 / 12) | 0.054 |
| Crissy Field | 0 | — | — | 0.54 | 0.117 |
| Mission Creek | 0 | — | — | 0.88 / 0.69 | 0.055 |
| Southeast | 0 | — | — | 0.95 / 0.89 | 0.353 |

- **Ocean Beach:** ten years of record hold 14 Sea Cliff-only Westside discharge days
  and exactly **one** sampled pure tail (clean). One clean sample against a prior
  weight of ten moves nothing: both shares fell back to the map (1.00 / 0.46).
- **Baker–China:** 10 non-attributed days, two sampled pure tails, both clean. Map stands (0.80 / 0.81).
- **Aquatic Park:** 10 non-attributed days, five sampled pure tails, all clean. The
  water agrees with the map's 0.5 as far as it can see.
- The three identity groups have no non-attributed days by construction.

**v3 == v2 numerically for every group and both sizes**, so no `*_s2v3` candidate set
was made. The split remains a judgment call, not a measurement.

## Why the sample is so thin

Pure Sea Cliff-only tails are rare (about one a year on the Westside) and SFPUC's
Ocean Beach sampling is weekly, so most of those tails were never sampled. The
postings ruler and the samples ruler therefore disagree on days that carry no water
evidence at all — the postings are precautionary, the samples absent.

## What would change the answer

- More pure-tail samples. The Supabase `samples` mirror (migration 012) plus SFPUC's
  resampling after a posting will add them slowly; each Sea Cliff-only event that is
  sampled within three days is one data point.
- A different ruler for Ocean Beach: if postings are taken as the truth there, the
  split is a small loss and the Ocean Beach shares should be raised toward the
  posting rate rather than fitted from the water.

## Re-running the fit

The fitter is not on `main` and no longer on GitHub. From a worktree on the local branch
(if the branch is gone, the commit is `9eea48a`; tag it before it is garbage-collected):

    git worktree add ~/Personal/BWTF-stage2-v3 stage2-v3
    cd ~/Personal/BWTF-stage2-v3
    venv/bin/python features/forecast/src/models/stage2_variants.py fit --variant v3

It writes `features/forecast/data/models/stage2/v3.json` with the per-group evidence
(`shares.<group>.<size>.n_tail_pure_non_attributed`, `tail_rate_pure_non_attributed`,
`water`, `map`, `p`). If any contested share's `water` comes back non-null and differs
from `map`, `stage2_variants.py save --variant v3` makes a candidate set to grade in
the Model check and the analysis report, exactly as v2 was. Re-run after each samples
refresh; the branch may need rebasing onto `main` first (it predates the promotion of
logit_v1_s2v2 and the `served.json` descriptor).

Related: `TODO.md` ("Decide the stage 2 for production"), the analysis report
`reports/2026-09_model_analysis.html` (recommendation 3), `src/models/stage2.py` (v1/v2).
