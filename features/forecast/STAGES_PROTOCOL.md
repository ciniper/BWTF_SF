# BWTF five-stage forecast — scoring protocol `stages_v1`

Freeze date: 2026-10-01 (the date of the commit that adds this file). The prospective window T0 starts the next day, 2026-10-02.
protocol sha256: 3dea312e3e939645cb906a7e01f632375ec28aadd9cdd76db3a2ae0eff117a6d

This file is the frozen scoring protocol for branch `forecast-stages`. Every score that `stages_build`, the stages report, the Model check's Stages tab or a promotion uses is computed under it. The contract it implements is `STAGES_DESIGN.md`: §3.0 and §5.1–5.6 of Part C, adapted by the owner decisions (Part A) and the red-team resolutions (Part B), which override Part C. Decisions are Chase's, 2026-10-01, unless marked otherwise.

**Changing it.** After the freeze nothing here is edited in place. A change is a new protocol version (`stages_v2`) with its own freeze date and its own T0. Every score records the protocol it was made under (`manifest.protocol = "stages_v1@<sha>"`), and scores made under different versions are never compared. `<sha>` is the sha256 of this file with its `protocol sha256:` line (at the top) reading `protocol sha256: <filled at commit>`; once that line holds the digest, `tests/test_served_golden.py` fails on any edit.

---

## 1. What is scored

| | |
|---|---|
| **Units** | basin (SFPUC's four: `westside`, `north_shore`, `central`, `south` under `SFPUC4_V1`; the served set's GEO_V1 keys inside its adapter), zone (the four in `shared/zones.py`), and gauge series for S1 (SF Downtown 047772, SF Oceanside 047767, two-gauge mean). Links are internal and are never a reported unit. |
| **Stages** | S1 rain forecast vs gauges · S2 basin rain → basin overflow · S3 basin → zone · S4 zone overflow → water quality · S5 live corrections · OUT the public number |
| **The public claim (A1)** | OUT = the chance that a zone's beaches are affected by a sewer overflow on day D: the overflow day itself, or one still lingering. OUT has no rain-runoff background term. Rain runoff with no overflow and dry-weather exceedances are things the forecast *does not claim*. |
| **S4** | S4 is scored against lab samples as a stage check, with a rain background term. The S4 score is never the public claim. |
| **Coverage, one rule** | `ledger_known(basin, D)`: a CIWQS record exists for the basin's facility-month with status `events_parsed`, `table_present_zero_events` or `no_table_stated_no_discharge`. Poo Bot archive days (2016-03-19 → 2017-01-10) are **not** known. They serve only as S5's real archive feed. S2, S3, S4 history and OUT all use this rule. |

**Truth per stage:**
- **S1:** ACIS daily totals, with `gauge_outage_v1` masking on every day. 'T' counts as 0.
- **S2:** a ledger event *starting* on D at an outfall of the basin, on `ledger_known` days. Volume is Σ volume_MG over known values.
- **S3:** a zone overflow day. An outfall of a link into the zone started an event on D (ledger plus registry), and every feeding basin is `ledger_known`.
- **S4:** any sampled zone station over the AB411 single-sample standard (`shared/standards.flag_exceedances`), on first-look samples (§7).
- **S5:** the downstream truths (S3 sibling zone-days, S4 samples, OUT).
- **OUT:** the combined label.
  - Bad: a zone overflow day, or a sample over standard 1–7 days after one.
  - Good: a clean sample, or a quiet day with the history known.
  - A sample over standard with no overflow in the window is a **negative** of the claim, not an exclusion (Part B 3).
  - BeachWatch CSO and rain postings (to 2026-02-28) are a second ruler, never truth.

---

## 2. Windows

| tier | name | days | refit rule | clean for | bias | use |
|---|---|---|---|---|---|---|
| **T0** | prospective | Every day after the freeze date, from 2026-10-02 | Served set: graded from `forecast_history` rows whose `model_stamp` names it, read from a committed snapshot or a GET-only path, never with the service key (Part B 20). Candidates: shadow-run on archived inputs with the design frozen at the freeze. | weights and decisions | none | confirmation |
| **T1** | post-training | 2025-11-01 → the data end (2026-08-17 at the freeze: 290 days) | Every fitted component of every scored set is fit on days before 2025-11-01. The served set's are: it was fit through 2025-10-31. | every fitted component (Part B 1) | The served set, its stage 2 split and its 25% line were chosen with these days in view (2026-09-25 → 28), so T1 favours the incumbent. Tag `post_selected`. | confirmation (non-inferiority) |
| **T1-holdout** | holdout | 2023-07-01 → 2025-10-31 (854 days) | **Every** fitted component is refit on days before 2023-07-01: S2 weights, volume heads, S3 link shares and the S4 table. The served artifacts' own holdout numbers do not qualify, because their heads, shares and table were fit through 2025-10-31. | weights, once refit | Holdout picked the rain source, the family and the served set. Tag `holdout_selected`. | development only. Confirmation is post-training plus prospective (Part B 1), and the holdout seasons sit inside T2, where design choices are made. |
| **T2** | cross-season | Leave-one-season-out over July–June seasons 2016-17 … 2024-25 (9 seasons; Westside 8, since CIWQS Oceanside starts 2017-12) | **Every** fitted component is refit per held-out season, with the design fixed | weights | Existing sets' S2 choices (C grids, design searches) maximized pre-holdout LOSO scores, so their S2 T2 scores are labelled **"development (selection-contaminated)"**. New design choices are made by **nested** LOSO, with every choice made inside the inner folds. | development, calibration reference, power |
| **T3** | in-sample | Any day the scored weights saw | — | — | — | **never emitted**; the build asserts it |

- T2 is never pooled with T1 or T0. In any one comparison a day is scored in exactly one tier.
- Every GEO_V1 row also carries an `X-SEL` tag (`holdout_selected` or `post_selected`).

**Nested rule for new candidates:**
1. Every design choice is made on T2 seasons up to 2024-25, nested.
2. T1 post-training (the 2025-26 season) confirms.
3. T0 accrues on top.

A candidate whose design changes after its post-training score was seen gets a new name, and its post-training score is tagged `post_seen`.

**Decision log: what has already seen data.**

| Decision | Data it looked at | Contaminates |
|---|---|---|
| Rain source per basin; archive-label ablation | holdout PR-AUC + Brier | holdout |
| Family / leaderboard | holdout | holdout |
| C (L2) | pre-holdout LOSO | T2 for S2 (development label) |
| 19 features and hinge knots | provenance unrecorded | assume all |
| Split v2; served set and 25% line (2026-09-25 → 28) | holdout + post + BeachWatch | holdout + post |
| `gauge_outage_v1` | built from the Feb 2026 misses | post-training inputs |
| ICON (2026-09-30) | S1 2024-02 → 2026-08 | the S1 window |
| `live_v2`, FLOOR_MIN | archive + synthetic | S5 |

---

## 3. Entries

| entry | what feeds the chain | available |
|---|---|---|
| **oracle** for stage k | the true input of stage k | full span |
| **rain known** | gauge rain (outage-masked) + ERA5 peak hours → S2 → S3 → S4 → OUT | 2016-10 → |
| **lead L**, L = 1…5 | Open-Meteo Previous Runs forecast at lead L for days on or after the issue day, gauges before → S1 → … | ICON/GFS ≈ 2024-01-20 →, ECMWF ≈ 2024-02-03 → |
| **lead 0**, tagged **optimistic** | the stitched short-lead archive | ICON 2022-11-16 → |
| **as served** (L0s, L1s) | lead L with the live 7-day feature window (`METEO_PARAMS past_days=7`) | same as lead L |

- The figure's chained pill is the full chain at lead 1. The stage cards also show rain known, lead 0 (tagged optimistic) and as served.
- The OUT error budget is the ladder truth-at-S4 → truth-at-S3 → rain known → lead L. Each drop is the error one stage passes downstream.

**Entry rules:**
- **S3 oracle (Part B 2):** the input is the true basin *occurrence* with the size predicted from rain (v̂), never the true basin volume, which contains the link's own discharge. Link shares are fit on v̂, the same way they are used.
- **S5 oracle (Part B 9):** the perfect feed is the ledger. Sibling zone-days are scored only on real or degraded feeds, never on the perfect feed.
- **Issue-time information only (Part B 5):**
  - No predictor or benchmark uses what was unknown at issue time. `n_sampled` is a stratum, never a predictor.
  - Benchmarks read the feed-visible state, not the ledger that arrives months later.
- **Identical inputs for paired comparisons (Part B 16):**
  - Served-vs-candidate deltas use the same rain inputs, with the same masking, in both arms.
  - The unmasked reproduction of the served scorecard is a fidelity check only, never one arm of a comparison.

---

## 4. Metrics

### 4.1 Primary metric per stage

| stage | primary | reported as |
|---|---|---|
| S1 | MAE on **either-wet** days (gauge or forecast ≥ 0.10"), **lead 1**, **two-gauge mean**; companion: **ETS at 0.5"** | inches; ETS |
| S2 | Brier score per basin and basin-pooled | **BSS** vs the reference climatology (§4.2) |
| S3 | Brier score: Westside zones (oracle and chained), East (chained). Identity links: an integrity check, where mismatches must be 0. | **BSS** |
| S4 | Brier score on first-look sampled zone-days | **BSS** |
| OUT | Brier score per zone and zone-pooled | **BSS** |
| S5 | paired ΔBrier (corrected − plain) against the OUT label on the observation-conditional set (zone-days D…D+7 after any injected observation in the same basin or zone, minus the replaced unit-day; the feed defines the set, so it is the same for every variant) | ΔBS with CI |

**Why Brier ranks:**
- It is strictly proper.
- It is the mean elementary score over every decision threshold, so it ranks forecasts without weighing a miss against a false alarm.
- It is finite on the exact 0s and 1s the composition and the injections emit.
- It decomposes (CORP: BS = MCB − DSC + UNC).

Fitting may use log loss. Decisions use paired Brier differences on identical rows.

### 4.2 Reference climatology

- **Reference:** per unit × calendar month, smoothed over ±1 month (months m−1, m and m+1 pooled), from the **training fold**.
- For T1 and T0 days, the reference comes from all T2 seasons. For T1-holdout days it comes from the seasons before 2023-07-01, the holdout refit's own training fold. The scored window's own base rate is never the reference.
- **Pooled skill** = 1 − Σ_s n_s·BS_s / Σ_s n_s·BS_clim,s (Hamill & Juras 2006): each stratum s is scored against its own climatology, never against one pooled base rate.
- Sample-climatology BSS is shown beside it, for comparison with older reports. It decides nothing.
- The CORP decomposition is of BS itself, not of BSS.

### 4.3 Secondary metrics (descriptive)

- Log score in bits, clipped to [0.001, 0.999], with the clip count reported. When Brier and log disagree on a ranking, the report says "no dominance" and shows the Murphy diagram.
- CORP reliability diagram per unit (isotonic, with bootstrap consistency bands), plus MCB / DSC / UNC.
- ROC-AUC. PR-AUC beside its prevalence, with lift = AP / prevalence.
- Murphy diagram: elementary scores over θ ∈ (0, 1). It shows where on the probability scale two forecasts differ and ranks nothing.
- **Threshold scores only at the risk-level edges (A3):**
  - The edges: p ≥ 0.205 (Medium), p ≥ 0.505 (High), p ≥ 0.805 (Extreme).
  - The scores: POD, FAR = fp/(tp+fp), POFD = fp/(fp+tn), CSI, PSS, frequency bias, HSS.
  - The Model check's default grading threshold is the Medium edge.
  - Server-side confusions use one grid shared by the scorecard and every report, which holds the three edges: (0.05, 0.10, 0.15, 0.205, 0.25, 0.30, 0.40, 0.505, 0.60, 0.75, 0.805) (Part B 21). Reports and decisions read threshold scores at the edges only.
- The S1 deterministic suite (`STAGES_DESIGN.md` §3.1), by lead × season × series:
  - ME, MAE, RMSE, r and multiplicative bias;
  - the 2×2 scores at 0.1 / 0.25 / 0.5 / 1.0";
  - SEDI at 1.0".
- S4 sensitivity, specificity and accuracy at q ≥ 0.5, against persistence (the AB411 nowcast convention).
- S2 volume on event days: log-MAE and size-class accuracy against the basin median.

---

## 5. Risk levels (A3)

On the whole percent the page shows:

| level | percent shown | probability edge used for threshold scores |
|---|---|---|
| Low | 0–20 | — |
| Medium | 21–50 | p ≥ 0.205 |
| High | 51–80 | p ≥ 0.505 |
| Extreme | 81–100 | p ≥ 0.805 |

- The levels are fixed. No line is chosen from data, and no score or decision weighs a miss against a false alarm.
- `served.json` keeps its `line` field, pinned until a promotion. Nothing chooses it, and the page stops defaulting to it.

---

## 6. Blocks, confidence intervals, tests, power

**Storms are built from rain, not from the ledger (Part B 10):**
1. A **wet day** has two-gauge-mean rain ≥ 0.10". The mean is outage-masked by `gauge_outage_v1`, 'T' counts as 0, and a missing gauge takes the other gauge.
2. A **storm** is a maximal run of wet days with no more than 2 dry days between consecutive wet days.
3. A **storm block** is [first wet day − 1, last wet day + 7]. Overlapping blocks are merged.
4. Days outside every storm block form **quiet blocks**: one per ISO week between the same two storm blocks, so a week a storm block cuts in two gives two quiet blocks.
5. `verify.storm_spans` and `verify.storm_blocks` implement this at their defaults (wet 0.1, gap 2, pad (1, 7)). If the two ever disagree, the words above rule.

Other blocks:
- S1 blocks: ISO weeks.
- S5 blocks: observation events.
- S3's storm-level secondary uses these rain storms: P_storm = the maximum of p_z over the storm's days, against "any zone overflow in the storm". Posting windows are de-duplicated.

**Bootstrap:**
- B = 2,000 resamples of blocks, seeded with seed 0 (the `verify` default, which the S1 evaluation already uses).
- **Paired:** both arms are scored on the same resample.
- **90% percentile CIs** serve both decisions and display (one-sided α = 0.05). P(Δ < 0) is reported beside them.

**Other rules:**
- **MDE** ≈ 2.5 × the bootstrap SE of Δ (80% power under the 90% convention). It is written beside every comparison.
- **Multiplicity:** the primaries in §8 are confirmatory and everything else is descriptive. Holm's correction applies across simultaneous candidate claims; S3a and S3b form one family.
- **Power tag `X-POWER`:** a unit × window with fewer than 10 positives or fewer than 8 storm blocks. It is shown with its CI and never decides.
- **Power at the freeze** (`STAGES_DESIGN.md` §2.3, §3, §5.4), stated up front:
  - Post-training-only comparisons detect only large effects: the OUT Ocean MDE is about 70% of the served Brier score.
  - South has 1 post-training event day, and North Shore has 2.
  - S4 has about 20 independent first-look tail samples per zone in ten years (Part B 4).
  - So development runs on T2 and confirmation is by non-inferiority.
- **Honesty (Part B 1):** never claim a difference whose storm-block CI includes 0. State power as measured.
  - Verdict words: **better** (the CI is wholly on the improving side), **worse** (the CI is wholly on the other side), **no clear difference** (otherwise).

---

## 7. Exclusions, in first-match order

**Mechanics:**
- Every rule is a pure function of a stage row plus context (ledger coverage, samples, gauge masks, feed events).
- Rules apply in the order listed. The first match is written to the row's `excl` column.
- **Counts partition the table:** for every (stage, unit, entry, window), n_total = n_scored + Σ n_rule.
- **Tag** rules never remove rows; they define a sensitivity subset. **Stratum** rules split a score. **Fit-only** rules affect fitting, not scoring.
- Paired comparisons use the intersection of non-excluded rows.

| stage | first-match order | also |
|---|---|---|
| S1 | X-S1-MISSING → X-S1-OUTAGE → X-S1-NWPGAP → X-S1-NOLEAD | peak-hour metrics: X-S1-PEAK |
| S2 | X-S2-ARCHIVE → X-S2-UNCOV → X-LEDGER-SUSPECT → X-S2-CARRY → X-ALL-INSAMPLE | volume table: X-S2-VOLQ · tags: X-S2-OUTAGEIN, X-POWER, X-SEL |
| S3 | X-S3-UNCOV → X-LEDGER-SUSPECT → X-S3-CARRY → X-S3-QUIET (oracle only) → X-S3-ID (oracle skill only) → X-ALL-INSAMPLE | tag: X-S3-GEO · posting check only: X-S3-NOTCLEAN, X-PL-END |
| S4 | X-S4-UNSAMPLED → X-S4-HISTUNK → X-S4-RESAMPLE → X-ALL-INSAMPLE | strata: X-S4-DAYOF, X-S4-FEW, stations sampled, resamples · fit only: X-S4-FOLLOWUP · tag: X-S4-ANALYTE |
| S5 | X-S5-HEALTH → X-S5-CIRC → X-S5-PERFECT-SIBLING → X-S5-SELF → X-S5-QUIET (conditional score only) | tag: X-S5-INSAMPLE |
| OUT | X-E2E-UNCOV → X-E2E-UNK → X-ALL-INSAMPLE | posting ruler: X-PL-END |

**Rules:**

| id | rule |
|---|---|
| X-ALL-INSAMPLE | the row's tier is T3. Zero by construction: T3 rows are never emitted. |
| X-POWER (tag) | fewer than 10 positives or fewer than 8 storm blocks in the unit × window |
| X-SEL (tag) | a GEO_V1 set's day in a window used to select it (`holdout_selected`, `post_selected`) |
| X-S1-MISSING | gauge value null or 'M'. Rows are per gauge; the two-gauge mean falls back to the other gauge. |
| X-S1-OUTAGE | a gauge-day inside a `gauge_outage_v1` run |
| X-S1-NWPGAP | a model-day with fewer than 24 archived hours. Such days are never zero-filled. |
| X-S1-NOLEAD | the (model, lead) pair is not in the archive for that day |
| X-S1-PEAK | peak-hour truth: every day, until KSFO hourly history is committed |
| X-S2-ARCHIVE | `label_source = poobot` |
| X-S2-UNCOV | not `ledger_known` |
| X-LEDGER-SUSPECT (Part B 8) | a `ledger_known` basin-day in D−3…D+1 around a CSO-cause BeachWatch posting onset, or a sample ≥ 10× the standard, in the basin's zones, when the ledger has no event for the basin in that window (example: Bayside, February 2026). Excluded from S2 and S3, and counted and listed. |
| X-S2-CARRY | an event that started before D is still active on D, and none starts on D |
| X-S2-VOLQ | volume_MG is null or carries a '<' qualifier |
| X-S2-OUTAGEIN (tag) | a masked gauge-day in the row's 30-day input window |
| X-S3-UNCOV | any feeding basin is not `ledger_known` on D |
| X-S3-CARRY | a zone outfall is still active from before D, and none starts on D |
| X-S3-QUIET | oracle only: no feeding basin overflowed on D (an output of exactly 0) |
| X-S3-ID | oracle skill only: the link share is identically 1. These rows get an integrity check instead, where mismatches must be 0. |
| X-S3-GEO (tag) | the zone fired only through geography-evidence outfalls |
| X-S3-NOTCLEAN | posting check: a sibling zone's link fired within ±2 days |
| X-PL-END | posting rulers: D after 2026-02-28 (BeachWatch's last filing) |
| X-S4-UNSAMPLED | no sample at any of the zone's stations |
| X-S4-HISTUNK | any feeding basin is not `ledger_known` on some day in D−7…D. Applied to every entry, so oracle and chained stay paired. |
| X-S4-RESAMPLE (Part B 4) | the zone had an exceedance on D−1 or D−2. The primary score uses first-look samples, and these rows are reported as their own stratum. |
| X-S4-DAYOF (stratum) | a zone overflow on D |
| X-S4-FEW (stratum) | fewer than half the zone's stations were sampled |
| X-S4-FOLLOWUP (fit only) | OCEAN#20 / 21 / 22 rows, in the background fit |
| X-S4-ANALYTE (tag) | the E. coli era, 2020-07 → 2021 |
| X-S5-HEALTH | live era: the watcher was unhealthy, so corrections were off |
| X-S5-CIRC | archive: Westside, 2016-03-19 → 2017-01-10 (the labels are the feed onsets) |
| X-S5-PERFECT-SIBLING (Part B 9) | a sibling zone-day on the perfect feed |
| X-S5-SELF | the unit-day whose value the observation replaced |
| X-S5-QUIET | conditional score only: no observation in the same basin or zone on D−7…D |
| X-S5-INSAMPLE (tag) | archive days, 2016-17, where S2 was in-sample |
| X-E2E-UNCOV | a feeding basin is not `ledger_known` on D, or on D−7…D for a non-overflow day |
| X-E2E-UNK | an unsampled day within 7 days after a zone overflow |

**Does not claim (counted, never excluded):**
- C-DRY and C-RUNOFF are reported as episodes and as days. Their rows stay in the OUT score as negatives of the claim, because X-E2E-SCOPE was dropped (Part B 3).
- C-OTHER, C-UNMON, C-POSTING and C-TIME are as listed in `STAGES_DESIGN.md` §4.2.

---

## 8. Pre-registered primary comparisons

- **Superiority:** the upper bound of the 90% CI of ΔBS (candidate − incumbent) is below 0.
- **Non-inferiority at +5%:** the upper bound is below 0.05 × the incumbent's BS on the same rows.
- Comparisons across geographies use only geography-invariant targets: S3 zones, S4 and OUT. Basin-level S2 is compared within one geography.

| stage | comparison | entry | window | rule |
|---|---|---|---|---|
| S1 | NWP model vs served ICON: either-wet MAE, lead 1, two-gauge mean | chained | Previous Runs window (2024-01-20 →) | the CI of ΔMAE excludes 0. A weather model is chosen on S1 only. |
| S2 | candidate vs served component, basin-pooled ΔBS, within one geography | oracle (rain known) | T2 (nested for new choices), confirmed on T1 post | superiority on T2; non-inferiority at +5% on T1 post |
| S2 South floor (Part B 7) | South's S2 BSS against its climatology | rain known | T2 | the lower bound of the 90% CI is above 0 |
| S2 volume (Part B 7) | declared head vs the pre-registered fallback | rain known | T2 | Fallback: when a basin has fewer than 20 known-volume events, a pooled Bay-side log-linear volume model with a basin offset. The build fails only if no declared fallback exists. |
| S3a geography | East-zone Brier, SFPUC4 vs GEO_V1, with S4 held fixed | chained, rain known | T2 | non-inferiority at +5% of the GEO_V1 BS. SFPUC4 fixes a listed defect: Islais was in the wrong basin, so Candlestick was mis-forecast. |
| S3b | Westside share s(v̂) vs a constant share | oracle (true occurrence, v̂ from rain) | T2 | superiority (Holm with S3a) |
| S4 | S4 v3 vs the served table (GEO_V1 adapter), zone-pooled ΔBS on first-look samples | oracle | T2 | superiority, or non-inferiority at +5% plus a fix for the oracle-worse-than-chained defect. The Candlestick-station sensitivity score is reported beside it (A2). |
| S5 | link/zone injection vs `basin_swap` (= `live_v2`), conditional set | oracle (perfect feed, no sibling rows) and chained (degraded feed: 13% missed, 60% one day late, 5 seeds) | 2023-07-01 → the data end | superiority on both feeds |
| OUT | candidate set vs served, zone-pooled ΔBS | rain known and lead 1 | T1 post ∪ T0 | non-inferiority at +5%, and pooled MCB not worse beyond its CI |

---

## 9. Promotion decision rule

A candidate replaces the served set only when all of these hold:
1. Each changed component passes its own-stage primary (§8) on T2, nested for new choices.
2. OUT is non-inferior at the +5% margin on T1 post ∪ T0, for rain known and for lead 1. Any bias in these windows favours the incumbent.
3. Pooled MCB is not worse beyond its CI.
4. The owner has seen the per-zone shadow-run Δ report (P11).
5. The owner approves.

- No step weighs a miss against a false alarm, and no step chooses a probability line. The risk-level edges are fixed (§5).
- Promotion is the owner's action. Nothing on this branch changes `served.json`, the served pickles or what the live forecast computes, except the risk-level display bands (A3, A4).
- The pins in `tests/test_served_golden.py` move only in the promotion commit, by re-running `tests/fixtures/make_stages_goldens.py`.
- After a promotion, the replaced set becomes a candidate.
