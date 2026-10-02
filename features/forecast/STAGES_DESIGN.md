# BWTF five-stage forecast — design contract (branch `forecast-stages`)

This file is the contract every change on this branch follows. **Part A (owner decisions) and Part B (red-team resolutions) override the merged design in Part C wherever they disagree.** Part C is the judge-panel design (three independent designs, three judges, one synthesis, 2026-10-01), kept verbatim for its detail.

## Part A — Owner decisions (Chase, 2026-10-01)

| # | Decision | What it means for the build |
|---|---|---|
| A1 | **The public % claims sewer overflow only.** | OUT = P(the zone is affected by a sewer overflow): the overflow day, or one still lingering. No rain-runoff background term in OUT. Rain runoff with no overflow and dry-weather exceedances are *does-not-claim* exclusions. S4 still scores water quality (with a rain background) against lab samples as a stage check. **TODO (not this branch):** a "water over the state standard after rain" public variant — add to TODO.md. |
| A2 | **Drop the group level.** | New geography `SFPUC4_V1`: 4 SFPUC basins → 5 internal links → 4 zones. Lingering curves per zone. "Group" survives only inside the GEO_V1 adapter that keeps the served set working. Add a Candlestick-station sensitivity check so the small South basin is not invisible. |
| A3 | **Fixed public risk levels, no cost-chosen line.** | One module `shared/risk_levels.py`: on the whole percent the page shows, **Low 0–20, Medium 21–50, High 51–80, Extreme 81–100**. Wire it into every public display (forecast page `riskInfo`, Today board tiles and map bands + legend, home page "rising", any copy that names the old bands). The cost rule (FA + k×miss) ranks nothing and sets nothing: delete every cost ranking (report_models King/cheapest line, how-it-is-graded cost tables, Model check cost columns, replay cost sort, weather_models_eval cost appendix). Threshold scores (POD, FAR, POFD, CSI, PSS, frequency bias) are reported **at each level edge**: Medium p ≥ 0.205, High p ≥ 0.505, Extreme p ≥ 0.805. The Model check's default grading threshold is the Medium edge. `served.json` keeps its `line` field (pinned until a promotion) but nothing chooses it by cost and the page stops defaulting to it. No `alert_lines.json`, no Platt line, no `shared/cost_basis.py`. |
| A4 | **Everything at once.** | Build P0–P11, including the model changes (S4 rain term, observations after the split, `history_full_v1`, SFPUC4 S2 incl. South, volume heads) and the promotion tooling for `stages_v1` pipelines. **Promotion itself stays the owner's action**: nothing in this branch changes `served.json`, the served pickles, or what the live forecast computes, except the risk-level display bands (A3), which the owner asked for. |

Terminology (public and code): **basin** (4, SFPUC's), **zone** (4, public), **outfall**, **station**, **stage S1–S5**, **oracle / chained**, **risk level**, **not scored**, **does not claim**. "Link" is internal only. Retired: group (except GEO_V1 internals), King, cheapest line, cost, alarm line, "Southeast" as a basin (it is the plant).

## Part B — Red-team resolutions (these fix flaws in Part C)

1. **Windows.** Only post-training (≥ 2025-11-01) is clean for every fitted component. The holdout (2023-07 → 2025-10) is NOT weights-clean for S3 shares, the S4 table or the volume heads (fit through 2025-10-31). Every scored window refits *every* fitted component (S2, volume heads, S3 shares, S4 table) without the scored days: T1-holdout = all components refit on pre-holdout; T2 = all components refit per held-out season. Choices made by maximizing pre-holdout LOSO scores (C grids, design searches) contaminate T2 for S2: new choices use **nested** LOSO (choices in inner folds); existing sets' T2 is labelled "development (selection-contaminated)". Confirmation = post-training (+ prospective seasons). State power honestly; never claim a difference whose storm-block CI includes 0.
2. **S3 oracle must not leak.** The oracle input is the true basin *occurrence* with the size predicted from rain (v̂), never the true basin volume (it contains the link's own discharge: Ocean Beach outfalls are a median 87% of Westside volume). Link shares are fit on v̂ the same way they are used.
3. **Exceedances with no overflow are negatives of the public claim.** Do not exclude them from OUT scoring (drop X-E2E-SCOPE as an exclusion; C-DRY / C-RUNOFF remain *does-not-claim* counts only, reported as episodes and days).
4. **S4 truth is missing not at random** (SFPUC resamples within 2 days after 93–100% of exceedances). S4's primary score uses *first-look* samples: exclude a zone sample-day if that zone had an exceedance on D−1 or D−2 (X-S4-RESAMPLE); report resamples as a separate stratum. Report honest power (independent first-look tail samples are ~20 per zone in ten years).
5. **No issue-time-unknown predictors.** Remove η·log(n_sampled) from the S4 background; keep n_sampled as a stratum only. Benchmarks use issue-time information (feed-visible state), not the ledger that arrives months later.
6. **S4 fit = S4 use.** The S4 v3 fit must use the same product (noisy-OR over prior event days) it applies, not bucketing by "days since the last event".
7. **South.** Report a per-basin S2 floor for South (BSS CI must not be below a stated bound) and the Candlestick-station S4 sensitivity score. Volume: pre-register the fallback — a pooled Bay-side log-linear volume model with a basin offset when a basin has fewer than 20 known-volume events; the build fails only if no declared fallback exists.
8. **Ledger completeness.** Add X-LEDGER-SUSPECT: a ledger-known basin-day window flagged when a CSO-cause BeachWatch posting onset or an extreme sample (≥ 10× the standard) in the basin's zones has no ledger event in D−3..D+1 (e.g. Bayside Feb 2026, coded zero events while East/North were posted for CSO and samples read 24,196 MPN). Excluded from S2/S3 scoring, counted and listed.
9. **S5 oracle is circular for sibling units** (the perfect feed is the ledger). Score sibling zone-days only on real or degraded feeds, never on the perfect feed.
10. **Storm windows from rain, not from the ledger,** for S3 storm-level checks (otherwise rainy no-overflow periods are never scored); posting windows de-duplicated.
11. **Never edit the registries.** `shared/{stations,outfalls,zones}.py` and `groups.py` are untouched (groups.py and train_v2.py assert the registry at import; an edit breaks the live import). Display names for SFPUC basins come from `shared/geography.py`, switched at promotion by the served pipeline, never by editing `Station.basin` / `Outfall.basin`.
12. **The served path is everything live_dashboard imports**, including train_v4, train_v2, csd_labels, leaderboard, candidates, scorecard, posting_label (served pickles reference `leaderboard`). Edits there must keep defaults byte-identical, be additive, do no module-level IO, and be covered by the served golden test. There is no CI: add `scripts/check.sh` (the full test suite + goldens) and document in DEPLOY.md that it must pass before any push.
13. **Stage candidates are saved by a geography-aware saver** under `data/models/stages_candidates/` (not `candidates.save_candidate`, which drops the stamp and assigns 'avg' rain to unknown keys). They join `candidates/` only once the Model check reads basin keys/names from the artifact.
14. **Readers of SERVE_DIR by GEO_V1 key** (volume heads, impact_table, thresholds.json, eval_report.json, explorer links) must be handled by the promotion tooling: a stages promotion swaps a complete bundle, and every reader branches on the stamped pipeline/geography.
15. **The figure describes what it is rendered for.** Rendered for a GEO_V1 set, node text must say what that set does (its basins, observations at the basin, its line). The public how-it-works page keeps describing the served set until a promotion. The new stages report is public at merge and scores every set (served included) inside the stage framework.
16. **Identical inputs for paired comparisons.** Served-vs-candidate deltas use identical rain inputs (same masking) in both arms; the unmasked served reproduction is a fidelity check only.
17. **Goldens.** Add goldens for `build_dataset(end=2026-08-17)` frames and `build_scorecard` days (labels included), not only zone risk.
18. **Payload for stages_v1** (used only after a promotion): keep `zones{zk: float}` scalar, and define `predictions{basin}`, `impact_groups` (zone-keyed under stages), `plain{}`, `discharge_probs`, `live_corrections.stage1/.groups` so the page, Today and the change fingerprint keep working; golden-test it.
19. **Model check after a stages promotion**: `stages_build --rescore` keeps writing a legacy-schema served scorecard; every promoted set has an explorer page (or the link changes).
20. **No prod keys.** The prospective grader reads a committed snapshot (or a GET-only read path); never the service key from tests. Raw-input archiving for T0 (Open-Meteo + ACIS frames per refresh) is a later item, listed in TODO.md.
21. **Line grid.** One grid shared by scorecard and every report: (0.05, 0.10, 0.15, 0.205, 0.25, 0.30, 0.40, 0.505, 0.60, 0.75, 0.805). Rename the Model check's 'far' (fp/(fp+tn)) to POFD; FAR = fp/(tp+fp) everywhere.
22. **Archive onsets** take their basin from `outfall_ids` through the geography; multi-basin strings are split, never truncated or dropped.
23. **Pinned counts carry an as-of date** so quarterly data refreshes do not break tests.
24. **Coverage = the continuous ledger** (protocol `stages_v2` §1, 2026-10-01). A facility-month is known only inside the unbroken run of covered months that ends at the facility's last grid month, opened at its first filed CSD table: Bayside from 2016-10, Oceanside from 2017-12. Older "no discharge" statements are not trusted (the grid marks Bayside 2015-02 "no discharge"; the legacy record lists a discharge on 2015-02-08). §3.0's coverage rule below is read this way.
25. **X-LEDGER-SUSPECT tests the zone, in wet weather** (protocol `stages_v2` §7). A trigger counts only when no basin feeding its zone has a ledger event in D−3…D+1 and the window holds a wet day. Read per basin (v1), it flagged South on 438 days and left 64 of East's 100 overflow days out of S3; v2 leaves out none and still flags Bayside, February 2026.

## Part C — Merged design (verbatim)

# BWTF five-stage forecast: merged design (branch `forecast-stages`)

Worktree: `/Users/chasecooper/Personal/BWTF-stages`. It was read-only for this design. Commits stay local and the owner pushes.

## How this design was put together

**Spine.** The *science* design (stage contracts, tiers, exclusion mechanics, proper scoring, measured power, decision rule). It was the overall winner (judge scores 8.5 / 7 / 7).

**Grafted from *explain*:**
- the house-style 1290-px figure;
- one plain question per stage;
- the split between S3 (the overflow day) and S4 (water quality, days 0–7);
- OUT = 1 − (1 − day-of)(1 − tail);
- Platt calibration for the alert line;
- the exclusion waterfall in each Model check panel.

**Grafted from *pragmatic*:**
- serving sits behind one `served.json` flag and is guarded by a golden test of `_day_payload`;
- no existing file changes its schema;
- the S3 oracle cannot answer the remap question (it is geography-invariant), so the remap is decided on the chained East-zone score;
- a carry-over exclusion for S2;
- cost lives in one module, with a grep test;
- volume heads fail loudly;
- per-zone alert lines only when the difference is significant.

**Prototype figure.** Generated from data and geometry-checked: no box overlaps, no arrow crossings, no arrow passes through a box, no label sits on a box or an arrow. All files are in `/private/tmp/claude-501/-Users-chasecooper-Personal-BWTF/e8d72723-01c5-4b30-818a-c20681aa15ae/scratchpad/synth/`:
- `fig.py` (spec, renderer and check)
- `fig.png`
- `fig_spec.json`

The new counts in this document come from read-only scripts in the same folder: `cov.py`, `counts.py`, `scope.py`, `s4hist.py`, `s4rates.py`, `zdiff.py`.

### Invariant: nothing served changes until the owner promotes

**Byte-identical, pinned by `tests/test_served_golden.py`:**
- `data/models/served.json`
- every `*_model.pkl` and `*_volume.pkl`
- `stage2.json`
- `impact_table.json`
- `thresholds.json`
- `eval_report.json`

`scorecard.json.gz` is deliberately *not* pinned. `train_v4.py --rescore --promote` rewrites it after every quarterly CIWQS refresh (`train_v4.py:794`, `881–886`), and that routine keeps running.

**Unchanged code on the forecast path:**
- `features/forecast/live_dashboard.py`
- `src/models/{impact.py, stage2.py, live_rules.py, groups.py, scorecard.py}`
- `shared/{stations, outfalls, zones}.py`
- `app/landing.py`
- `_today_board.html`
- the alert dispatcher

**Payload.** `predictions[*].zones{zk: float}` stays a scalar, because Today and the home page read it.

**Allowed before promotion (additive, read-only):**
- new offline modules;
- a new route `/forecast/api/stages` that reads precomputed `scores.json` through a new `features/forecast/stages_api.py`;
- display-only Model check changes.

All of it stays on the branch until the owner merges. Draft reports never go to public `/reports` before the merge.

### Flaws in the input designs and how this design fixes them

Each fix was checked against the repo.

| # | Flaw (source) | Fix | How it was checked |
|---|---|---|---|
| 1 | The figure drew "a named structure → basin" into S2, which contradicts injection at the link (science) | S5 arrows enter only S3 (a CSO flag or a named outfall, after the split) and S4 (a lab result) | `fig.py` geometry check |
| 2 | C-DRY and C-RUNOFF ignored coverage and did not add up to the scope class (science) | One coverage rule everywhere: every feeding basin has a CIWQS record on D−7…D, and archive days count as no record. C-DRY 214 + C-RUNOFF 80 = X-E2E-SCOPE 294, exactly | `scope.py` |
| 3 | X-S2-UNCOV swallowed X-S2-ARCHIVE under first-match (science) | ARCHIVE is applied first. Westside: 283 archive + 374 uncovered. Each Bay basin: 183 + 31 | `cov.py` |
| 4 | C-UNMON's count measured a different rule (science) | Rule: a geography-evidence outfall that is more than 1.5 km from every station it is mapped to. Result: 4 outfalls (CSD-004, 017, 018, 037), 70 event-days | `counts.py` |
| 5 | Taking the zone max of the day-of and tail terms separately is not what `compose` does (explain) | The GEO_V1 adapter composes each legacy group first, then takes the zone max. Code-vs-code parity is exact. The separate-terms version differs on 244 East days and 65 North days, by up to 0.09 | `eng/zone_max_split.py` |
| 6 | A byte pin on `scorecard.json.gz` breaks the quarterly rescore (science, explain) | Not pinned | `train_v4.py:794, 881–886` |
| 7 | Tolerances of 5e-4 and 1e-6 against an artifact rounded to 3 dp (science, explain) | Exact parity code-vs-code; ≤ 0.0025 against the artifact | `tests/test_live_rules.py:236–239` precedent |
| 8 | The invariant said `live_dashboard.py` stays as is, yet the plan added `get_stage_scores` and `?lines=` to it (science) | New module `stages_api.py`. No `?lines=` before promotion | — |
| 9 | Tier T0 needed a stamp in `model_stamp`, which was deferred to P10 (science) | The prospective window is defined by date plus the served name, both already stored in `forecast_history`. Candidates are shadow-run | — |
| 10 | SFPUC4 candidates would be listed automatically and drawn wrong (science, pragmatic) | They go in `data/models/stages_candidates/` until the Model check reads basins from the artifact | `page.html:220–229, 744, 995`; `candidates.py:68–79`; `test_scorecard_window.py:320–345` |
| 11 | `shared/geography.py` checked data at import time; `wsgi` imports the forecast page at load (pragmatic, explain) | `geography.py` imports only the three registries. Data checks live in tests and the CIWQS refresh | `app/wsgi.py:51` |
| 12 | A `groups.py` facade would re-point offline tools after a promotion (science) | `groups.py` stays GEO_V1 forever. Serving branches on `served.json.pipeline` | — |
| 13 | The S1 evidence came from the stitched archive and conditioned on observed rain (science, pragmatic) | The S1 primary uses Previous Runs leads, days where either the gauge or the forecast was wet, outage-masked | — |
| 14 | The S3 "storm-event primary" was undefined for a daily forecast (pragmatic) | Same-day ledger truth is primary. The storm-level score is secondary, with an explicit max-over-days rule | — |
| 15 | Ranking on post-training only (the served set was selected there; MDE about 70%), and pooling T2 with T1 (pragmatic, explain, science) | Develop on cross-season. Confirm by non-inferiority on post-training plus prospective days | `report_models` King uses oos = holdout + post |
| 16 | BSS = (DSC − MCB)/UNC claimed against a stratified reference (explain) | CORP decomposes the Brier score itself. BSS against a fixed training-fold climatology is reported separately | — |
| 17 | The inverse of an isotonic fit gives a jumpy line (science) | Platt calibration per zone, a CI on the raw line, and one pooled line unless a zone's line is significantly better | — |
| 18 | S5's scored set was defined by each variant's own output (pragmatic) | The scored set is defined by the feed, so it is identical across variants | — |
| 19 | A 19-line grid doubles request-time compute (science) | Keep `scorecard.LINE_GRID` (10 lines) plus exact served lines. REV curves are computed offline | eng probe: 1.39 s → 2.64 s |
| 20 | Fit arrows printed "before Nov 2025", but the scores come from LOSO refits (science) | The arrow says "fit". The legend says "scored on days the fit never saw" | — |
| 21 | "step / Reach / Water", and "North" used for North Shore (explain) | "stage", full basin names, zone labels from `shared/zones.py` | `zones.py` labels |
| 22 | The served scorecard's zone discharge label includes feed-archive days the ledger lacks: North 2016-12-16 and 12-23; East 2016-10-17, 11-23, 12-16 and 12-23 | Stage truth comes from the ledger only. The P4 test reproduces scorecard labels outside 2016-03-19 → 2017-01-10 and lists the diffs inside that window | `zdiff.py` |
| 23 | "East is 21 outfalls" (pragmatic) | East has 20 outfalls: Central 15 + South 5 | registry |

---

## 1. The flowchart (the spec)

### 1.1 What the figure encodes

1. **Columns are stages, left to right:** S1 Rain → S2 Basin overflow → S3 Zone overflow → S4 Water quality → OUT, the public overflow risk.
   - S5 Live corrections is a band under S3–S4.
   - The Alert line sits under OUT.
2. **Rows:**
   - TRUTH (what each stage is graded against)
   - MODEL (the stage)
   - LIVE (observations that replace a prediction)
   - a NOT SCORED chip per column
   - a full-width "THE FORECAST DOES NOT CLAIM" strip
   - the legend
3. **Every stage is scored twice.** Each model box has two pills:
   - *oracle*: fed the true input. A green dotted diagonal runs from the previous column's TRUTH box.
   - *chained*: fed the real output of the stage before. This is the solid blue chain arrow.
   
   The pills sit under a one-line metric label.
4. **Every stage is fitted on its own truth and scored on days the fit never saw.** A grey dashed "fit" arrow runs down from TRUTH to MODEL; a grey "scored" arrow runs up from MODEL to TRUTH. S1 has no fit arrow: the weather model is external.
5. **An observation replaces a prediction at the stage it measures** (orange arrows):
   - observed rain → S1;
   - a CSO flag or a named outfall → S3, after the split;
   - a lab result → S4.
6. **Cost appears in exactly one box:** "Alert line per zone".
7. **The figure is data.** `stages_spec.py` is the only source. The SVG, the phone list, the report sections, the Model check tabs, the stage ids in the artifacts and the test list all iterate over it.
   - Every box is a link (`#s1` … `#alert`).
   - Rule ids, artifact names and geography versions appear only in `<title>` tooltips (plain chips).

### 1.2 Canvas and grid

`<svg class="pipe" viewBox="0 0 1290 820">`. CSS is the same family as today's `svg.pipe` in `export_how_it_works.CSS`: Roboto, `.t` 16 px, `.s` 13 px, 40-px tiles, rx 14.

**Columns.** x = 44, 296, 548, 800, 1052. Width 222. Gutters of 30 px at 266–296, 518–548, 770–800 and 1022–1052.

**Headers.** Title at y = 18 (`.col`, 11.5 px bold, letter-spaced); unit at y = 33 (`.q`, 12 px italic):

| Column | Title | Unit line |
|---|---|---|
| C0 | S1 · RAIN | per rain gauge · day · lead 0–5 |
| C1 | S2 · BASIN OVERFLOW | per basin · day |
| C2 | S3 · ZONE OVERFLOW | per zone · day |
| C3 | S4 · WATER QUALITY | per zone · sampled day |
| C4 | OUT · OVERFLOW RISK | per zone · 6 days |

**Rows:**

| Row | Content | Boxes (y, h) | Band rect (x 8, w 1274, rx 18, fill #f7fafc) | Row label |
|---|---|---|---|---|
| 0 | TRUTH | y 50, h 80 | y 42–138 | rotated −90° at x = 26: TRUTH |
| 1 | MODEL | y 220, h 152 | y 212–380 | MODEL |
| 2 | LIVE a | y 416, h 56 | y 408–608 | LIVE |
| 3 | LIVE b | y 480, h 56 | (same band) | |
| 4 | LIVE c | y 544, h 56 | (same band) | |
| 5 | NOT SCORED chips | y 632, h 54 | none | in-chip title |
| 6 | DOES NOT CLAIM strip | y 700, h 62, x 44, w 1230 | none | in-strip title |
| — | legend | chip legend y 772–792; arrow legend y 806 | | |

### 1.3 Nodes

Inner layout by kind:

- **Truth boxes (h 80):** tile at (x+10, y+14); title (x+60, y+28); lines at (x+60, y+46) and (x+60, y+61).
- **Model boxes (h 152):**
  - tile (x+12, y+12); title (x+62, y+30), 17 px bold, as "S{n} · {title}"; question (x+62, y+47), 12 px italic;
  - lines (x+14, y+72 / y+89);
  - metric label (x+14, y+h−36), 11 px #8a949b;
  - two pills at y+h−30, h 20, each (w−36)/2 wide, at x+14 and x+w/2+4.
- **S5 band:**
  - lines at y+66 and y+82;
  - metric label at (x+14, y+108);
  - pills 118 wide at x 764 and x 890, y 510.
- **Live and input boxes (h 56):** tile (x+10, y+8); title (x+60, y+h/2−10); line (x+60, y+h/2+11).
- **Decision box (h 120):** tile (x+10, y+14); title (x+60, y+38); lines (x+14, y+72 / +89 / +106).
- **NOT SCORED chips:** title "NOT SCORED" (x+14, y+17), 10.5 px caps; lines at (x+14, y+32) and (x+14, y+46), 11.5 px.
- **Claim strip:** title (x+16, y+19); six pills at y+28, h 24, w 193, gap 8, from x 60.

**TRUTH row**

| id | col,row | box | logo | title | lines | Tooltip / bound values |
|---|---|---|---|---|---|---|
| `t.s1` | 0,0 | 44,50,222,80 | `logos/noaa.svg` | Rain gauges | Downtown · Oceanside / dead-gauge days masked | ACIS 047772 / 047767 |
| `t.s2` | 1,0 | 296,50,222,80 | `logos/water-boards.png` | Overflow ledger | CIWQS, by basin / {n_city_days} overflow days | n = 112 today |
| `t.s3` | 2,0 | 548,50,222,80 | `logos/water-boards.png` | Ledger, by zone | outfall → its stations / same day, covered days | — |
| `t.s4` | 3,0 | 800,50,222,80 | `logos/sf-city-seal.png` | Lab samples | any station over / the state standard | AB411 single-sample |
| `t.out` | 4,0 | 1052,50,222,80 | icon `circle-alert` | Bad beach days | overflow day, or over / standard within 7 days | second ruler: BeachWatch postings to 2026-02-28 |

**MODEL row**

| id | col,row | box | icon | title | question | lines / inset | metric · pills |
|---|---|---|---|---|---|---|---|
| `m.s1` | 0,1 | 44,220,222,152 | `cloud-rain` | S1 · Rain | how much rain, and when? | ICON via Open-Meteo, one point / today → 5 days ahead | wet-day error, inches · `floor —` / `lead 1 —` |
| `m.s2` | 1,1 | 296,220,222,152 | `gauge` | S2 · Basin overflow | does the sewer overflow? | chance + size, per basin / SFPUC's four basins | skill vs climatology (BSS) · `oracle —` / `chained —` |
| `m.s3` | 2,1 | 548,220,222,152 | `git-branch` | S3 · Zone overflow | which zones does it reach? | **inset** (below) | skill (BSS) · oracle on the Westside split · `oracle —` / `chained —` |
| `m.s4` | 3,1 | 800,220,222,152 | `flask` | S4 · Water quality | is the water over standard? | overflow history + rain / per zone, days 0–7 after | skill vs climatology (BSS) · `oracle —` / `chained —` |
| `m.out` | 4,1 | 1052,220,222,152 | `map-pin` | Overflow risk | what people see | % per zone · every 30 min | skill by lead, chained · **lead strip** |

**S3 inset.** It is drawn from `geo.links`, using the unique (basin, zone) pairs.
- Basin dots at x+84 with labels end-anchored at x+77. Zone dots at x+128 with labels starting at x+135. Labels are 10 px.
- Rows at y+58, +71, +84 and +97.
- Basins, top to bottom: Westside, North Shore, Central, South.
- Zones, using `shared/zones.py` short labels: Ocean Beach, Baker & China, North Beaches, East Beaches.
- Five lines: W→OB, W→B&C, NS→North, C→East, S→East.
- The two Westside links are the only split. They are drawn 2 px in #0072BC; the identity links are 1.5 px in #b9c7cf.
- No line crosses another, because the order is monotone.
- The scored render writes each Westside share at the line midpoint, inside `<title>`.

**OUT lead strip.**
- Six bars, 24 px wide, at x+14+33i, with the baseline at y+h−22. Height = 26·BSS; a missing value draws a 4-px grey bar.
- Labels "today, +1 … +5" at y+h−10.

**LIVE rows**

| id | kind | col,row | box | logo/icon | title | lines |
|---|---|---|---|---|---|---|
| `l.rain` | live | 0,2 | 44,416,222,56 | `logos/noaa.svg` | Rain so far | gauges, then SFO today |
| `l.map` | live | 1,2 | 296,416,222,56 | `logos/sfpuc.png` | SFPUC beach map | CSO flags · outfall names |
| `l.lab` | live | 1,3 | 296,480,222,56 | `logos/sf-city-seal.png` | Lab results, new | map 1–2 days · DataSF ~5 |
| `l.perfect` | input (oracle, dashed green box) | 1,4 | 296,544,222,56 | `circle-check` | Perfect feed | filed overflows, on time |
| `m.s5` | stage | cols 2–3, rows 2–3 | 548,416,474,120 | `satellite-dish` | S5 · Live corrections | question: does what was seen improve what comes after? / a CSO flag or named outfall → its zone, after the split / a lab result → that day's water quality / metric: change in Brier on the days after · `oracle —` / `chained —` |
| `o.line` | decision (output) | col 4, rows 2–3 | 1052,416,222,120 | `scales` | Alert line per zone | the only place cost is used: / a miss costs 2 false alarms / alert at calibrated risk ≥ 1 in 3 |

**NOT SCORED chips** (row 5, fill #f3f6f9, no stroke, rx 12). Counts are bound from `scores.json.exclusions`.

| id | box | line 1 | line 2 |
|---|---|---|---|
| `x.s1` | 44,632,222,54 | dead-gauge days {n} · missing {n} | model gaps {n} · peak hours |
| `x.s2` | 296,632,222,54 | no filing {n} · feed archive {n} | carry-over {n} · days the fit saw |
| `x.s3` | 548,632,222,54 | identity links: checked only | no basin overflow (oracle) {n} |
| `x.s4` | 800,632,222,54 | days nobody sampled {n} | overflow history unknown {n} |
| `x.out` | 1052,632,222,54 | unsampled days after one {n} | over standard, no overflow {n} |

**DOES NOT CLAIM strip** `x.claim` (44,700,1230,62). Title "THE FORECAST DOES NOT CLAIM". Six pills:
- dry-weather exceedances {n}
- rain runoff, no overflow {n}
- other causes posted {n}
- shore far from a station {n}
- SFPUC's posting decisions
- the hour, the size, past day 5

**Legend.**
- Chip row, y 772:
  - `[oracle]` `[chained]` "every stage is scored twice: on its true input (oracle) and on the real output of the stage before it (chained)";
  - `[NOT SCORED]` "each rule has a count; ids in tooltips".
- Arrow row, y 806: flows every 30 minutes · oracle: fed the true input · fits the stage · scored on days the fit never saw · an observation replaces a prediction · decision.

### 1.4 Edges

All paths are absolute, from `fig_spec.json`.

| id | from → to | style | path `d` | label (x, y, anchor) |
|---|---|---|---|---|
| c12 | m.s1 → m.s2 | data | M266,276 L296,276 | — (tooltip: inches by day and lead) |
| c23 | m.s2 → m.s3 | data | M518,276 L548,276 | — (P + size per basin) |
| c34 | m.s3 → m.s4 | data | M770,276 L800,276 | — (P + size per zone) |
| c45 | m.s4 → m.out | data | M1022,276 L1052,276 | — (the overflow part) |
| f2 | t.s2 → m.s2 | fit | M360,130 L360,220 | "fit" (354,168,end) |
| f3 | t.s3 → m.s3 | fit | M612,130 L612,220 | — |
| f4 | t.s4 → m.s4 | fit | M864,130 L864,220 | — |
| s1 | m.s1 → t.s1 | score | M148,220 L148,130 | "scored" (154,168,start) |
| s2 | m.s2 → t.s2 | score | M400,220 L400,130 | "scored" (406,168,start) |
| s3 | m.s3 → t.s3 | score | M652,220 L652,130 | — |
| s4 | m.s4 → t.s4 | score | M904,220 L904,130 | — |
| s5 | m.out → t.out | score | M1156,220 L1156,130 | — |
| o2 | t.s1 → m.s2 | oracle | M252,130 L326,220 | "true rain" (287,186,end) |
| o3 | t.s2 → m.s3 | oracle | M504,130 L578,220 | "true basin overflows" (539,186,end) |
| o4 | t.s3 → m.s4 | oracle | M756,130 L830,220 | "true zone overflows" (791,186,end) |
| o5 | l.perfect → m.s5 | oracle | M518,572 L584,572 L584,536 | — |
| l1 | l.rain → m.s1 | live | M155,416 L155,372 | "observed rain" (161,398,start) |
| l2 | l.map → m.s5 | live | M518,444 L548,444 | — |
| l3 | l.lab → m.s5 | live | M518,508 L548,508 | — |
| l4 | m.s5 → m.s3 | live | M659,416 L659,372 | "a flag or a named outfall" (665,398,start) |
| l5 | m.s5 → m.s4 | live | M911,416 L911,372 | "a lab result" (917,398,start) |
| d1 | m.out → o.line | decision | M1163,372 L1163,416 | "calibrated %" (1169,398,start) |

**Styles**

| style | class | stroke | dash | marker |
|---|---|---|---|---|
| data | `.flow` | #0072BC, 2 px | — | `pa` |
| fit | `.fit` | #8a949b, 2 px | 6 5 | `pg` |
| score | `.grade` | #8a949b, 1.5 px | — | `pk` |
| oracle | `.orc` | #237059, 2 px, round caps | 2 4 | `po` |
| live | `.live` | #d4763a, 2 px | — | `pl` |
| decision | `.dec` | #26272a, 2 px | — | `pd` |

Label colours follow the style. The oracle label is italic.

| Box class | Style |
|---|---|
| `.box.truth` | stroke #b9c7cf 1.5 |
| `.box.hub` (stages, OUT) | #0072BC 2 |
| `.box.live` | #efc9ad 1.5 |
| `.box.oracle` | #237059 1.5, dash 3 3 |
| `.box.dec` | #26272a 2 |
| `.xbox` | #f3f6f9 |
| `.chip.or` | white fill, #237059 stroke |
| `.chip.ch` | #0072BC fill, white text |

### 1.5 Data binding and renders

`stages_flowchart.render(spec, geo, scores=None) -> (svg: str, phone_html: str)`.

`scores` is `stages/<set>/scores.json["figure"]`:
```
{ node_id: {"oracle": {"v", "lo", "hi", "n", "pos", "low_power"},
            "chained": {...},
            "lead": [{"v", "lo", "hi"} x 6]} }
```
plus `scores.json["exclusions"]` and `scores.json["claims"]` for the chip counts.

| Situation | What the render does |
|---|---|
| Value missing | "—". The spec render shows "—" everywhere; never placeholder or in-sample numbers. |
| X-POWER tag | Pill at 55% opacity; CI and n in `<title>` |
| S1 pills | inches, 2 dp ("0.21″") |
| Other pills | BSS, 2 dp |

- Basin names, the inset and `{n_city_days}` come from the geography and the truth tables. The same renderer draws the served GEO_V1 set: its inset shows Westside, North Shore, Central (Mission Creek) and Southeast, with the same 5 unique pairs.
- `FIT_SCRIPT` and `_fit` are imported from `export_how_it_works` in P1. They move to `src/models/svgkit.py` in P9, when that report is regenerated anyway.

**Phone render.** At ≤ 700 px, the same `.pipe-m` pattern as today: an `<ol>` with 8 items (S1, S2, S3, S4, OUT, S5, Alert line, Does not claim). Each item reads "question · truth · oracle X · chained Y · not scored: …".

**Companion figures** (same module, same spec data):
- **Figure 2, "Basins to zones":** a large bipartite drawing. Basin side shows the CSD range and overflow days; zone side shows the station count. The five links carry their outfalls and days. A dot on East explains Islais Creek: posted by Central 031–035 and South 037.
- **Figure 3, "Where the alert line comes from":** relative-value curves V(α), α ∈ (0, 1), with α = 1/3 dashed, beside the zone's reliability curve with the 1/3 crossing marked. This is the only cost chart anywhere.

### 1.6 The spec is enforced

`features/forecast/src/models/stages_spec.py` holds, as plain data:

- **`STAGES`.** Each entry carries: id, name, question, unit, truth builder, oracle entry, chained entry, scorer, exclusion ids, artifact table, Model check tab id, report section id, primary comparison.
- **`NODES`** and **`EDGES`**, as in §1.3–1.4.
- **`EXCLUSIONS`** and **`CLAIMS`**, as in §4.
- **`SPEC_VERSION`** = the first 10 characters of sha1(json(spec)).

`tests/test_stages_spec.py` checks that:
- every edge endpoint exists;
- every style is in the allowed set;
- every stage has a scorer, a truth builder, an exclusions entry, an artifact table, a tab and a report section;
- every exclusion id appears in the spec, in `exclusions.py` and in a stage;
- the geometry holds (a port of `fig.py check()`): no box overlaps; no segment crosses another, including diagonals; no edge passes through a box other than its own ends; no label box sits on a box or an edge;
- the SVG has no rule ids or artifact names outside `<title>`;
- the report sections and Model check tabs follow `STAGES` order.

### 1.7 How it differs from today's figure 1 (`export_how_it_works.pipeline_svg`)

| Today | New |
|---|---|
| "19 rain inputs → Stage 1 · 4 basins → Stage 2 · 6 beach groups → Live corrections" | Five stages, one unit each. Old stage 1 becomes S2. Old stage 2 is split into S3 (which zones the overflow reaches, the day-of term) and S4 (water quality, scored on samples). Rain is S1. "19 rain inputs" is folded into S2. |
| No truths, no scores | A TRUTH row and two pills per stage: oracle (green dotted, from the truth to its left) and chained (blue chain) |
| Global "teaches stage N" fit arrows | A fit arrow and a scored arrow per stage, each on its own truth |
| Corrections enter at the basin, before the split | A flag or named outfall enters S3 (after the split); a lab result enters S4 |
| "6 beach groups" | Gone. SFPUC's four basins; five links drawn inside S3 |
| "Alarm line 25%" | "Alert line per zone", a decision box and the only place cost appears |
| No exclusions | NOT SCORED chips with counts, and a DOES NOT CLAIM strip |
| History tables box | Dropped from the figure; kept in the text |

---

## 2. Terminology and geography

### 2.1 Units: two levels and one edge

| Term | Definition | Count | Role |
|---|---|---|---|
| **basin** | SFPUC drainage basin: the outfalls sharing a CIWQS `report_basin` group (§2.3) | 4 | S2 output |
| **zone** | Public grouping of stations (`shared/zones.py`, unchanged) | 4 | S3, S4 and OUT output; signup; tiles |
| outfall | CSD structure (CIWQS id) | 34 | truth atom for S2/S3; S5 named outfall |
| station | SFPUC sampling site (LIMS id) | 20 | truth atom for S4; S5 flag |
| link | Edge (basin, zone). Derived as `{(basin(o), zone(s)) : o ∈ OUTFALLS, s ∈ o.stations}`. Every outfall posts stations in exactly one zone, so the links partition the outfalls. | 5 | S3 parameters only. Internal word: code and methods text, never public copy |
| storm | Maximal run of citywide ledger event days at most 2 days apart (≤ 1 dry day between) | 74 (52 pre / 15 holdout / 7 post) | bootstrap blocks; storm-level S3 check |
| lead | Target day minus issue day | 0–5 | S1; every chained entry |
| window | prospective / weights-clean / cross-season / in-sample (§3.0) | 4 | every score |
| facility | Treatment-plant permit: Oceanside, or Southeast/Bayside | 2 | ledger coverage only. "Southeast" is a facility name only from now on. |
| ~~group~~ | Retired. Exists only inside GEO_V1 (and `groups.py`) so the served set keeps working | — | — |

### 2.2 Why "group" goes and "link" stays internal

**Some intermediate unit is unavoidable, because basins and zones are many-to-many.**
- Westside feeds two zones. On Westside days: both 32, Ocean Beach only 11, Baker & China only 22.
- East is fed by two basins: Central only 77 days, South only 3, both 20.

The link is the minimal such unit. It is computed, not hand-drawn, and it never carries a forecast that anyone sees.

**What groups added beyond links:**
- separate S4 decay curves inside a zone: Crissy Field vs Aquatic Park; Mission Creek vs Islais/Crane vs Candlestick;
- under SFPUC's basins, an Islais two-parent problem.

The public claim and the S4 truth are zone-level ("any sampled zone station over standard"), so a zone-level S4 estimates the claim directly. Taking the max over groups understates P(any station).

**Gate to bring sub-zone resolution back (pre-registered, `T-group`).** Fit S4 per GEO_V1 group, composed to the zone by union. It returns only if all of these hold:
- it beats zone-level S4 v3 on zone-level Brier;
- the paired storm-block 90% CI excludes 0, on cross-season, with the oracle entry;
- it is not worse on post-training.

Default: dropped.

### 2.3 SFPUC four-basin geography `SFPUC4_V1`

Built from `Outfall.report_basin`, never from CSD number ranges:
- Oceanside → `westside`
- North Shore → `north_shore`
- Central (Mission Creek) and Central (Islais Creek) → `central`
- Southeast → `south`

CSD-119 (Baker Street, one event on 2017-11-16) reports as North Shore. By number range it would land in South. A test pins it to North Shore.

Event days by window: pre < 2023-07-01; holdout = 2023-07-01 → 2025-10-31; post ≥ 2025-11-01, ledger through 2026-04-22.

| key | Display | Outfalls | Facility (coverage) | S2 rain series | Event days (storms) | pre / hold / post | Previous Runs window from 2024-01-20 |
|---|---|---|---|---|---|---|---|
| `westside` | Westside | CSD-001…007 | Oceanside (CIWQS 2017-12-01 → 2026-07-31) | two-gauge mean | 65 (46) | 39 / 12 / 14 | 22 |
| `north_shore` | North Shore | 009, 010, 011, 013, 015, 017, 119 | Bayside (2016-10-01 →) | SF Downtown | 42 (36) | 31 / 9 / 2 | 9 |
| `central` | Central | 018, 022–027, 029, 030, 030A, 031, 031A, 032, 033, 035 (15) | Bayside | SF Downtown | 97 (67) | 69 / 20 / 8 | 22 |
| `south` | South (the memo's "South Basin"; shortened so it doesn't clash with CSD-042's receiving water) | 037, 040, 041, 042, 043 (5) | Bayside | SF Downtown (confirmed on cross-season) | 23 (21) | 17 / 5 / 1 | 6 |

**Citywide:** 112 event days (78 / 20 / 14) in 74 storms; 28 event days in the Previous Runs window. The ICON short-lead archive (2022-11-16 → 2024-01-19) adds 23 city days (W 13, NS 12, C 23, S 4).

**South:**
- fires without Central on 3 of 23 days;
- 22 of its event days through 2025-10-31 have a known volume, against the volume head's floor of 20.

### 2.4 Links (`SFPUC4_V1`)

Evidence codes: O = observed in the 2016-17 feed; G = permit + geography.

| link id | outfalls | zone days (pre / hold / post) | S3 share |
|---|---|---|---|
| `westside>ocean` | 001, 002, 003 (O) | 43 (27 / 10 / 6) | fitted s(v) |
| `westside>baker_china` | 004, 005, 006 (G), 007 (O) | 54 (31 / 10 / 13) | fitted s(v) |
| `north_shore>north` | 009–015 (O), 017, 119 (G) | 42 (31 / 9 / 2) | identity |
| `central>east` | 022–027, 031–035 (O); 018, 029, 030, 030A (G) | 97 (69 / 20 / 8) | identity |
| `south>east` | 040, 041, 043 (O); 037, 042 (G) | 23 (17 / 5 / 1) | identity |

**Co-firing shares for S5 siblings:**
- P(Ocean Beach link | Baker & China link) = 32/54 = 0.59
- P(Baker & China | Ocean Beach) = 32/43 = 0.74
- P(Central | South) = 20/23 = 0.87

**East union rule.** Pre-registered choice on cross-season chained East Brier (rain-known entry). Every candidate rule is bounded by the Fréchet limits [max(p_C, p_S), min(1, p_C + p_S)].
- **Default: `max`.** Today's comonotone semantics, no parameters.
- Challenger: `noisy_or`, 1 − (1 − p_C)(1 − p_S).
- Challenger: `cofire`, p_C + p_S·π with π = P(no Central | South) = 3/23, fit per fold.

### 2.5 Islais Creek, CSD-037, and station attribution

- **Zone level.** Islais (4619) is in East. Central (031–035, observed) and South (037, geography) both reach East, so S3, S4 and OUT have no conflict.
- **S2 truth** follows `report_basin`, so 037 counts for South. It fired on 10 days. On 2 of them (2022-12-01, 2025-12-25) no Islais outfall fired. It never fired without some Central outfall.
- **Decision (default):** keep CSD-037 → Islais, tagged geography.
- **`Geography.station_basin(sfpuc_id)`** is used only for S5 sibling effects:
  - take the basins of observed-evidence outfalls posting the station;
  - if there are none, take geography-evidence outfalls, but only if they all sit in one basin;
  - otherwise return `None` and attribute to the zone only.
  
  Results: Islais → Central; Crane Cove → Central; Candlestick stations → South; Ocean Beach and Baker & China stations → Westside. A structure name from the feed (`FEED_NAME_TO_OUTFALLS`) always overrides.

### 2.6 Versioning without touching the served geography

New module `shared/geography.py`. It imports only `shared.outfalls`, `shared.stations` and `shared.zones`. It reads no CSV and runs no data assertions at import.

```python
@dataclass(frozen=True)
class Basin:  key: str; name: str; facility: str; report_basins: tuple[str, ...]; rain_series: str
@dataclass(frozen=True)
class Link:   id: str; basin: str; zone: str; outfalls: tuple[str, ...]; identity: bool; legacy_group: str | None = None
@dataclass(frozen=True)
class Geography:
    version: str; basins: tuple[Basin, ...]; links: tuple[Link, ...]; zone_union: str   # 'max_of_link_risk' (GEO_V1) | 'max' | 'noisy_or' | 'cofire'
    def basin_of_outfall(self, oid) -> str: ...
    def zone_of_outfall(self, oid) -> str: ...
    def links_into(self, zone) -> tuple[Link, ...]: ...
    def links_from(self, basin) -> tuple[Link, ...]: ...
    def station_basin(self, sfpuc_id) -> str | None: ...
CITY_BASIN_OF_REPORT = {"Oceanside": "westside", "North Shore": "north_shore", "Central (Mission Creek)": "central",
                        "Central (Islais Creek)": "central", "Southeast": "south"}
GEO_V1: Geography      # constants written out: 4 app basins, 6 legacy-group links (two share north_shore>north), compose per link then zone max
SFPUC4_V1: Geography   # derived from report_basin
def get(version: str) -> Geography     # KeyError on unknown; never a default
def city_basin(outfall) -> str         # function, not an Outfall property (avoids a shared/ import cycle)
```

**Rules:**
- `groups.py`, `stations.py`, `outfalls.py` and `zones.py` are never edited by this branch. `groups.py` stays GEO_V1 permanently. Serving under the new pipeline reads `geography.get(served.json["geography"])` explicitly (P11), and offline tools always take `geo=` as an explicit argument.
- `Station.basin` and `Outfall.basin` stay GEO_V1 for display until the owner switches display names at promotion.

**Stamps.** `"geography"` (and `"pipeline"`: `two_stage_v1` | `stages_v1`) is written into:
- every new pickle dict;
- candidate manifests;
- `s3_links.json` and `s4_quality.json`;
- stages manifests;
- SFPUC4 candidate scorecards;
- at promotion, `served.json` and `model_stamp`.

A missing stamp means `geo_v1`.

**Loaders assert:**
- stamp == the geography in use;
- model keys == basin keys == the S3/S4 spec keys.

They use no `.get(default)`.

**Keys.** `westside`, `north_shore`, `central`, `south`. `central` changes meaning, so the stamp and the assert are what make a mismatch fail loudly. An in-place edit of a registry is the known silent failure (Candlestick 0.0; Islais 0.60 vs 0.98), so it is forbidden and tested.

### 2.7 Glossary, retired words, rename map

**Public copy uses:** basin, zone, station (in figures; "beach" in prose where it reads better), outfall, overflow ("discharge" only when quoting CIWQS), stage S1–S5, oracle / chained, alert line, not scored, does not claim, posted (SFPUC's word).

**Retired words:**
- "group", except inside GEO_V1 code;
- "King", "cheapest line", "cost" outside the alert line;
- "alarm line";
- "Southeast" as a basin;
- the Model check's "far" for fp/(fp+tn).

| Old | New |
|---|---|
| stage 1 (basin models) | S2 · Basin overflow |
| stage 2 outfall split | S3 · Zone overflow (link shares) |
| impact table, lingering | S4 · Water quality |
| live corrections | S5 · Live corrections |
| alarm line | alert line (per zone) |
| Southeast basin | South basin; "Southeast" = the plant |

Manifests gain `components: {s1, s2, s3, s4, s5}`, for example `{"s1": "icon_seamless", "s2": "logit_v1", "s3": "split_v2", "s4": "impact_v2", "s5": "live_v2"}`. The old `stage1` and `stage2` fields stay, and `stage2` stays one of v1/v2/v3 (`test_scorecard_window.py:320`).

---

## 3. Stage contracts

### 3.0 Shared definitions

**Coverage, one rule.** `ledger_known(basin, day)` means a CIWQS record exists for the basin's facility-month (status `events_parsed`, `table_present_zero_events` or `no_table_stated_no_discharge`). Poo Bot archive days (2016-03-19 → 2017-01-10) are **not** known. They are feed onsets, used only as S5's real archive feed. S2, S3, S4 (history) and OUT all use this rule.

**Windows** (tier codes are internal; reports use the names):

| code | name | definition | clean for | bias | use |
|---|---|---|---|---|---|
| T0 | prospective | Days after the protocol-freeze commit date (P0). Served set: graded from `forecast_history` rows whose `model_stamp` names it. Candidates: shadow-run on archived inputs with the design frozen at the freeze. | weights and decisions | none | confirmation |
| T1 | weights-clean | Holdout siblings 2023-07-01 → 2025-10-31 (854 days) + finals 2025-11-01 → 2026-08-17 (290 days) | weights | The served set was selected here (King on holdout + post; rain source; 25% line) → favours the incumbent | confirmation (non-inferiority) |
| T2 | cross-season | Leave-one-season-out refits, design fixed. Seasons Jul 2016–Jun 2025: 9 (Westside 8, since CIWQS starts 2017-12). S2, S3 and S4 all refit per fold. | weights | Hinge knots and features have unrecorded provenance; holdout seasons informed the served design | development, calibration, power |
| T3 | in-sample | Weights saw the day | — | — | never emitted |

Every row also carries a `sel` tag: `holdout_selected` or `post_selected`, for GEO_V1 sets.

**Nested rule for new candidates:**
1. Every design choice is made on T2 seasons ≤ 2024-25.
2. 2025-26 (T1 post) confirms.
3. T0 accrues on top.

A candidate whose design changes after its post-training score was seen gets a new name. Its post score is tagged `post_seen`.

**Decision log** (what has already seen data):

| Decision | Data it looked at | Contaminates |
|---|---|---|
| Rain source per basin; archive-label ablation | holdout PR-AUC + Brier | holdout |
| Family / leaderboard | holdout | holdout |
| C (L2) | pre-holdout LOSO | — |
| 19 features and hinge knots | provenance unrecorded | assume all |
| Split v2; served set and 25% line (2026-09-25 → 28) | holdout + post + BeachWatch | holdout + post |
| `gauge_outage_v1` | built from Feb 2026 misses | post inputs |
| ICON (2026-09-30) | S1 2024-02 → 2026-08 | the S1 window |
| `live_v2`, FLOOR_MIN | archive + synthetic | S5 |

**Entry points.** Where the chain starts, and what feeds it:

| entry | feeds | available |
|---|---|---|
| truth at stage k (**oracle** for stage k) | the true input of stage k (§3.1–3.6) | full span |
| **rain known** | gauge rain (masked) + ERA5 peak hours → S2 → S3 → S4 → OUT | 2016-10 → |
| **lead L** (L = 1…5) | Previous Runs forecast at lead L for days ≥ the issue day, gauges before → S1 → … | ICON/GFS ≈ 2024-01-20 →, ECMWF ≈ 2024-02-03 → |
| lead 0 (tagged *optimistic*) | stitched short-lead archive | ICON 2022-11-16 → |
| as served (L0s, L1s) | lead L with the live 7-day feature window (`METEO_PARAMS past_days=7`) | same as lead L |

- The figure's **chained** pill = the full chain at **lead 1**. The stage cards also show rain known (10 seasons, the powered rung), lead 0 and as served.
- The OUT error budget is the ladder truth-at-S4 → truth-at-S3 → rain known → lead L. Each drop is the error one stage passes downstream.

### 3.1 S1 · Rain forecast vs gauges

| Field | Contract |
|---|---|
| Question | how much rain, and when? |
| Input | Hourly precipitation at 37.7749, −122.4194 from ICON (served, `icon_seamless`), ECMWF and GFS. Hindcast: Open-Meteo Previous Runs `precipitation_previous_day1..5` (fixed leads). The base variable (stitched short lead) = lead 0, optimistic. |
| Output | Rain series g ∈ {SF Downtown 047772, SF Oceanside 047767, two-gauge mean} × Pacific day × lead 0–5: daily total; peak 1/3/6 h |
| Component | External NWP (no weights). Live overlay: ACIS for complete past days, KSFO hours today |
| Truth | ACIS daily totals; `gauge_outage_v1` mask on **all** days; 'T' = 0 |
| Oracle input | None upstream. The oracle pill is the **representativeness floor**: Oceanside as a perfect point forecast of Downtown, and the reverse |
| Chained input | Forecast at lead L (pill: lead 1) |
| Metrics | JWGFVR continuous: ME, MAE, RMSE, r, multiplicative bias, on all days, observed-wet, forecast-wet and either-wet (≥ 0.1"). 2×2 at 0.1 / 0.25 / 0.5 / 1.0": POD, FAR, POFD, frequency bias, CSI, ETS, Peirce (PSS), HSS; SEDI at 1.0". By lead × season × series; ISO-week-block bootstrap CIs; paired NWP differences. **Primary:** MAE on either-wet days, lead 1, two-gauge mean, with ETS@0.5" as companion. |
| Benchmarks | Persistence (gauge D−L−1); monthly climatology; competing NWP (ECMWF, GFS, mean of three); the floor |
| Exclusions | X-S1-MISSING, X-S1-OUTAGE, X-S1-NWPGAP, X-S1-NOLEAD, X-S1-PEAK |
| Window and power | Previous Runs window 2024-01-20 → 2026-08-17: ≈ 940 days, ≈ 100 wet, ≈ 30 ≥ 0.5", ≈ 12 ≥ 1.0", ≈ 40 wet week-blocks. Lead-0 extension from 2022-11-16: 1,366 days, 169 wet, 58 ≥ 0.5" (tagged optimistic). Preliminary (stitched, observed-wet, unmasked): ICON − ECMWF wet MAE −0.015" [−0.063, +0.032], so the 2026-09-30 switch is inside the noise. Floor: wet MAE 0.184", CSI@0.5" 0.73. |
| Known biases | One grid point for the city; ICON was chosen on this window (mild winner's curse); ACIS observation day vs calendar sums near midnight storms; the stitched lead 0 is optimistic |
| Owner rule | A weather model is chosen on S1 only. The chained S2 score shows the error it passes on but never picks the model. |

### 3.2 S2 · Basin rain → basin overflow

| Field | Contract |
|---|---|
| Question | does the sewer overflow? |
| Input | The 19 daily rain features + hinges from the basin's rain series; peak 1/3/6 h (ERA5 in training; forecast and KSFO live) |
| Output | Basin × day × entry: p_b(D) = P(≥ 1 CIWQS event *starts* on D at an outfall of b); v̂_b(D) = E[MG \| event] |
| Component | Served: `logit_v1` (GEO_V1 keys). SFPUC4 candidate: Westside and North Shore keep the served weights, restamped (identical outfalls and labels); Central and South are refit (§6, changes 4–5) |
| Truth | Ledger `{b}_csd` on `ledger_known` days. Volume = Σ volume_MG, known values only |
| Oracle input | Gauge features, outage-masked in every era, plus ERA5 peaks, full history |
| Chained input | Lead L: gauges through D−L−1 and forecast for D−L…D, with forecast peaks. As served: the same with the frame truncated to 7 days |
| Metrics | **BSS** (primary), Brier, log score, CORP reliability + MCB / DSC / UNC, ROC-AUC, PR-AUC with prevalence. Volume on event days: log-MAE and size-class accuracy vs the basin median |
| Benchmarks | Climatology (basin × month, training fold); a one-feature logistic on rain_D (rain-bin rule); persistence y(D−1) |
| Exclusions (first-match order) | X-S2-ARCHIVE → X-S2-UNCOV → X-S2-CARRY → X-ALL-INSAMPLE. Volume table: X-S2-VOLQ. Tags: X-S2-OUTAGEIN, X-POWER, X-SEL |
| Window and power | Event days in §2.3; T2 holds all pre + holdout seasons. Preliminary GEO_V1 holdout BSS (sample climatology, week blocks): Westside 0.54 [−0.07, 0.76], North Shore 0.84 [0.70, 0.94], Central 0.80 [0.70, 0.88], Southeast 0.78 [0.64, 0.88]. Post-training Central 0.36 [−1.62, 0.82]. |
| Known biases | The label is a filing keyed on start day (≈ 120 of 1,104 events cross midnight, hence X-S2-CARRY); holdout selection; ERA5 peaks vs live peaks; 7-day train/serve skew (today's Westside p moves up to +0.11); volume heads overfit (training log-MAE 0.29–0.41 vs post-training 0.66–1.27) |
| Pre-registered primary | Within one geography: candidate vs served component, basin-pooled ΔBS (Hamill–Juras), oracle entry, T2 |

### 3.3 S3 · Basin → zone

| Field | Contract |
|---|---|
| Question | which zones does it reach? |
| Input | p_b, v̂_b from S2, or the truth y_b, v_b |
| Output | Link: p_ℓ = p_b · s_ℓ(v̂_b), v̂_ℓ = φ_ℓ · v̂_b (φ_ℓ = the link's median share of basin-day volume). Zone: p_z = union over links into z (identity zones: their single link; East: the union rule, §2.4) |
| Component | `s3_links.json`. Westside shares s_ℓ(v) = logistic(a_ℓ + b_ℓ · log1p v), with **one** size median per basin (this fixes today's 3.7 MG vs 13.93 MG double median); identity for 3 links; union rule; co-firing matrix. GEO_V1 adapter: the `stage2.json` v2 group shares, composed per group, zone = max |
| Truth | Zone overflow day: an outfall of a link into z started an event on D (ledger + registry), with every feeding basin `ledger_known`. **Secondary:** (a) storm-level: P_storm = max over storm days of p_z vs "any zone overflow in the storm"; (b) posting concordance: a BeachWatch CSO-cause posting onset within [storm start − 1, end + 2], on clean single-zone storms only |
| Oracle input | True y_b(D), v_b(D) |
| Chained input | S2 output on rain known; lead L |
| Metrics | Westside zones: BSS, Brier, log, CORP, ROC, PR. The oracle is scored on basin-overflow days; chained is scored on all days *and* on the oracle's rows, for the paired gap. Identity links: integrity mismatches, which must be 0. East: chained BSS for each union rule. Storm-level BSS. Posting share |
| Benchmarks | Constant share (no size term); identity (share 1, the v1 behaviour); East: `max` |
| Exclusions | X-S3-UNCOV → X-S3-CARRY → (oracle only) X-S3-QUIET → X-S3-ID → X-ALL-INSAMPLE. Tag: X-S3-GEO. Posting check only: X-S3-NOTCLEAN, X-PL-END |
| Power | 65 Westside basin days (Ocean Beach link 43, Baker & China link 54); East 100 zone days; North 42. Posting: 20 same-day Baker & China–only days to 2026-02-28 → 10 clean → Ocean Beach posted on 1 |
| Preliminary | In-sample shares, oracle, 65 days: Ocean Beach ROC 0.92, Brier 0.128, BSS 0.43. Baker & China ROC 0.48, BSS −0.006: its share is flat at 0.80–0.81 against a base rate of 0.83 |
| Geography invariance | S3 oracle zone outputs are identical under GEO_V1 and SFPUC4: every outfall posts one zone, and the zone truth is the union of the same outfalls. A test asserts this. The oracle therefore cannot answer "does the zone number survive the remap"; the chained East zone answers it (§5.5) |
| Known biases | Geography-evidence registry rows are unverified (208 event rows on 83 days); the volume share is approximate; same-day attribution splits multi-day storms (the storm-level secondary covers this). A prior negative result applies: refitting shares from the water left v3 = v2 (`STAGE2_V3_FINDINGS.md`) |

### 3.4 S4 · Zone overflow → water quality

| Field | Contract |
|---|---|
| Question | is the water over standard? |
| Input | Zone overflow history o_z(D−k), k = 0…7 (0/1 for the oracle, p_z for chained); size class s (large if v ≥ the zone median); two-gauge rain D−2…D; wet-season flag; n stations sampled |
| Output | Zone × sampled day: q_z(D) = 1 − (1 − b_z(D)) · Π_{k=0..7} (1 − o_z(D−k) · x_z(k, s)), where b_z = P(over standard \| no overflow in 7 days, same rain). The same algebra serves oracle and chained, so they differ only in inputs |
| Component | Served/GEO_V1 adapter: impact v2 table per legacy group, with its own `baseline_no_recent_discharge`, measured d0 buckets and the PAVA tail; zone = max over groups. (x(0) = 1 is OUT's policy, not S4's.) SFPUC4: `s4_quality.json` v3, zone-level. b_z = logistic(α_z + δ1·hinge(rain3, 0.1") + δ2·hinge(rain3, 0.5") + ω·wet + η·log n_sampled). x_z(k, s) over buckets {0, 1, 2, 3, 4–5, 6–7} × {small, large}, monotone non-increasing (cumulative parameterization). Fit jointly by maximum likelihood on sampled zone-days per T2 fold, with L2 toward the zone-pooled curve |
| Truth | Max over station × analyte × day via `shared/standards.flag_exceedances` (AB411 single sample). Sources: DataSF 2020-07 →; Poo Bot 2015-12 → 2017-01; STARDB 2016-10 → 2020-07 (owner decision D10, de-duplicated) |
| Oracle input | True zone overflow history and volumes (ledger), and gauge rain |
| Chained input | The S3 chain (rain known; lead L) |
| Metrics | BSS vs zone × month sampled-day climatology; Brier; log; CORP; ROC; PR. Sensitivity, specificity and accuracy at q ≥ 0.5 vs persistence (AB411 nowcast convention). Strata: dry / wet without an overflow / day-of / days 1–7 after; few stations sampled; source; analyte era |
| Benchmarks | Climatology; persistence (the zone's last sample within 7 days, SFPUC's current practice); **background only** b_z (does overflow history add skill beyond rain?); served impact table (oracle and chained); rain-only logistic |
| Exclusions | X-S4-UNSAMPLED → X-S4-HISTUNK → X-ALL-INSAMPLE. Strata: X-S4-DAYOF, X-S4-FEW. Fit only: X-S4-FOLLOWUP. Tag: X-S4-ANALYTE |
| Power | Sampled zone-days (DataSF + Poo Bot): Ocean 451, Baker & China 483, North 473, East 761. Scored after X-S4-HISTUNK: 390 / 418 / 439 / 712. STARDB adds ≈ 258 / 281 / 277 / 467 |
| Known biases | Sampling follows overflows (43–94% of days 1–3 after vs 15–23% of dry days), so every score is *conditional on sampled* (missing-at-random given the covariates); Monday cadence; OCEAN#20/21/22 sampled only as follow-up; indicator switch in 2021 |
| Pre-registered primary | S4 v3 vs served table (adapter), zone-pooled ΔBS, oracle entry, T2 |

**Why S4 needs a rain term.** Exceedance rate on sampled zone-days with the overflow history known; n in brackets (`s4rates.py`):

| Zone | dry | wet, no overflow in 7 d | day-of | days 1–7 after |
|---|---|---|---|---|
| Ocean | 0.04 (254) | 0.09 (46) | 0.79 (28) | 0.32 (62) |
| Baker & China | 0.15 (293) | 0.18 (56) | 0.67 (24) | 0.24 (45) |
| North | 0.13 (291) | 0.23 (75) | 0.85 (26) | 0.26 (47) |
| East | 0.32 (374) | 0.60 (82) | 0.98 (58) | 0.77 (198) |

The served table pools dry and wet days into one baseline and has no rain term. That is why phase 1 found S4 fed the true discharges scoring *worse* than the chained composition (post-training Brier 0.182 vs 0.150): upstream probabilities that track rain stood in for the missing term.

Explain's preliminary check (in-sample tables, so indicative only) agrees: overflow history adds skill beyond rain in Ocean Beach, roughly nothing in East, and is slightly worse in North; Baker & China shows no skill.

### 3.5 S5 · Live corrections

| Field | Contract |
|---|---|
| Question | does what was seen improve what comes after? |
| Input | Station CSO flags (alert_log `station_id`); named structures (`feed_station_days.raw_last.cso` → `FEED_NAME_TO_OUTFALLS`, once the field is verified); lab results (feed map 1–2 days; DataSF about 5 days); "no flag while the watcher is healthy" |
| Output | Corrected p_ℓ / p_z (S3), q_z (S4) and r_z (OUT) on D…D+7 |
| Variants | `plain`; `basin_swap` = `live_v2` exactly (the incumbent: p_b = 1, then the split); `link_swap` (named outfall: its link p = 1, siblings at the co-firing share, e.g. a Baker & China flag → Ocean Beach 0.59); `zone_swap` (flagged station: p_z = 1; basin only via §2.5); `sample_swap` (q_z(D) = the result; D+1…D+3 from fitted next-sample transition rates); `downgrade` (Bayes, feed recall per basin; South uses the pooled Bay-side recall) |
| Truth | Downstream truths: S3 zone overflow for sibling zone-days, S4 samples, OUT bad days |
| Oracle input | Perfect feed: CIWQS outfalls on the filed day, no misses, no lag |
| Chained input | Real feeds: the 2016-17 archive (Bay side); a degraded feed (13% missed, 60% one day late, 5 seeds) over 2023-07 → 2026-08; the watcher era (2026-08-20 →) once a wet season exists |
| Metrics | **Primary:** paired ΔBrier (corrected − plain) against the OUT label on the **observation-conditional set**: zone-days D…D+7 after any injected observation in the same basin or zone, minus the replaced unit-day. The set is defined by the feed, so it is identical across variants. Blocks = observation events. Secondary: Δlog, ΔBrier vs S4 samples and vs S3 sibling zone-days, all-days Δ (expected ≈ 0), reliability change |
| Benchmarks | `plain`; `basin_swap` (live_v2) |
| Exclusions | X-S5-HEALTH → X-S5-CIRC → X-S5-SELF → X-S5-QUIET (conditional score only). Tag: X-S5-INSAMPLE |
| Power | Archive: 23 onset basin-days (Bay side: 15 CIWQS events, 13 caught by the feed). Degraded feed: 88 true onset basin-days × 5 seeds. Watcher: no wet season yet |
| Preliminary | Perfect feed at the 25% line: Ocean Beach false alarms 16 (plain) → 23 (basin onset) → 34 (live_v2 persistence); group-level injection 21. All-days Brier barely moves (0.0116 → 0.0115), hence the conditional primary |
| Known biases | The live `cso` field's meaning is unverified; the degraded feed is built from the truth (only its drops, lags and tails are informative); the replay assumes next-day sample arrival (real: 1–5 days) |
| Pre-registered primary | `link_swap + zone_swap` vs `basin_swap`, ΔBS on the conditional set, perfect feed and degraded feed |

### 3.6 OUT · the public number

| Field | Contract |
|---|---|
| Output | r_z(D) = 1 − (1 − p_z(D)) · Π_{k=1..7} (1 − p_z(D−k) · x_z(k, s)). The day-of term counts as bad by policy (x(0) ≡ 1), and there is no background term. GEO_V1: per group, then zone max (exactly `impact.compose` + `zone_risks`). This is the scalar `zones{zk}` |
| Claim | "the chance that the zone's beaches are affected by a sewer overflow on D" |
| Truth | Combined label. Bad = zone overflow day, or a sample over standard on k = 1…7 after one. Good = a clean sample, or a quiet day with the history known. Second ruler: BeachWatch CSO + rain postings (to 2026-02-28) |
| Entries scored | Error-budget ladder (§3.0) + lead 0–5 + as served. The prospective grade uses `forecast_history` by lead |
| Metrics | Per-zone Brier / BSS, log, CORP, ROC, PR. At the alert line only: POD, FAR, POFD, CSI, PSS, frequency bias |
| Benchmarks | Climatology; persistence (yesterday bad); served set; gb_v1 |
| Exclusions | X-E2E-UNCOV → X-E2E-UNK → X-E2E-SCOPE → X-ALL-INSAMPLE; X-PL-END for the posting ruler |
| Power | Scored bad days (coverage rule, full span): Ocean 63, Baker & China 65, North 56, East 256. Out of sample / post-training (old rule, approximate): 30 / 9, 32 / 16, 18 / 3, 102 / 22 |
| Preliminary | Served, out of sample, Brier / BSS: Ocean 0.0115 / 0.58, Baker & China 0.0178 / 0.42, North 0.0062 / 0.62, East 0.0249 / 0.73 (beats gb_v1 in every zone) |

---

## 4. Exclusions catalog

### 4.1 Mechanics (`src/models/exclusions.py`)

- Every rule is a pure function of a stage row plus context (ledger coverage, samples, gauge masks, feed events). Rules are applied in the listed first-match order, and the first match is written to the row's `excl` column.
- **Counts partition the table.** For every (stage, unit, entry, window): n_total = n_scored + Σ n_rule. A test enforces this.
- **Rule kinds:**
  - *Tag* rules never remove rows. They define a sensitivity subset reported beside the main score.
  - *Stratum* rules split the score.
  - *Fit-only* rules affect fitting, not scoring.
- **Paired comparisons** (oracle vs chained; candidate vs incumbent) use the intersection of non-excluded rows.
- **Display:**
  - Report: a table with the id and plain words.
  - Figure chips: plain words and count; the id in `<title>`.
  - Model check: a collapsed "left out of this score" waterfall per stage (all days → with a record → out of sample → scored), plain words, no ids.
- The counts below were measured on the committed data (2016-03-01 → 2026-08-17, 3,822 days unless noted). `exclusions.py` recomputes them on every build.

### 4.2 What the forecast does not claim

| id | Rule (computable) | Why | Count |
|---|---|---|---|
| C-DRY | Sampled zone exceedance; every feeding basin `ledger_known` on D−7…D; no zone overflow on D−7…D; two-gauge rain D−2…D < 0.1" | Not overflow-related | 214 zone-days (Ocean 10, Baker & China 45, North 38, East 121) |
| C-RUNOFF | As C-DRY, but rain D−2…D ≥ 0.1" | Stormwater, not a sewer overflow. S4 models it through b_z so it cannot hide, but the public % counts only overflow-attributable risk | 80 (4 / 10 / 17 / 49) |
| C-OTHER | BeachWatch zone-days with `cause_class = other`, 2016-10-16 → 2026-02-28 | Not in the CSD ledger (SSOs, wildlife, unexplained) | 698 (17 / 129 / 135 / 417) |
| C-UNMON | Geography-evidence outfall whose nearest mapped station is > 1.5 km away (CSD-004 Mile Rock 1.7 km, CSD-017 Jackson St 2.6 km, CSD-018 Howard St 2.5 km, CSD-037 Evans Ave 1.8 km) | Their overflows count toward the mapped zone; nothing is claimed about the shoreline at the outfall | 4 outfalls, 70 event-days |
| C-POSTING | Whether SFPUC posts a sign | A policy decision; BeachWatch is a second ruler, never truth | structural |
| C-TIME | The hour of an overflow (≈ 120 events cross midnight); its size (volume is internal); a single station within a zone; days beyond D+5 | Daily, zone-level, 6-day product | structural |

### 4.3 What each stage's score leaves out

| id | Stage | Rule (first-match order within the stage) | Rationale | Count |
|---|---|---|---|---|
| X-ALL-INSAMPLE | S2–OUT | Window = T3 (the scored weights saw the day) | Leakage | 0 by construction (T3 rows are never emitted; the build asserts it). Today: finals on ≤ 2025-10-31 = 3,532 days; holdout siblings < 2023-07-01 = 2,678 |
| X-POWER (tag) | any unit × window | < 10 positives or < 8 storm blocks | Shown with a CI, never decides | e.g. South post 1, North Shore post 2, North OUT post 3 |
| X-SEL (tag) | GEO_V1 sets | Day in a window used to select the set (holdout_selected / post_selected) | Winner's curse | holdout 854 days, post 290 |
| X-S1-MISSING | S1 | Gauge value null or 'M' (per-gauge rows; the two-gauge mean falls back to the other gauge) | No truth | Previous Runs window: Downtown 36, Oceanside 26 (all time: 37 / 430) |
| X-S1-OUTAGE | S1 | Gauge-day inside a `gauge_outage_v1` run | A dead gauge reads 0.00 | 12 runs, 378 gauge-days all time; 40 in the window |
| X-S1-NWPGAP | S1 | Model-day with < 24 archived hours, never zero-filled (fixes `daily_from_hourly`) | NaN-as-zero invents dry days | ECMWF 14 days (306 h); ICON 0; GFS 0 |
| X-S1-NOLEAD | S1 | (model, lead) not in the archive for that day | No forecast | Leads 1–5 before ≈ 2024-01-20 (ICON/GFS) / 2024-02-03 (ECMWF); lead 0 before 2022-11-16 |
| X-S1-PEAK | S1 peak hours | All days, until KSFO hourly history is committed | No measured hourly truth (ERA5 is reanalysis) | all |
| X-S2-ARCHIVE | S2 | `label_source = poobot` | Feed onsets: lagged, and circular with S5 | Westside 283 (8 onset days); each Bay basin 183 |
| X-S2-UNCOV | S2 | Not `ledger_known` | Unknown ≠ no overflow | Westside 374; each Bay basin 31 |
| X-S2-CARRY | S2 | An event that started before D is still active on D, and none starts on D | Neither an onset nor a clean negative | SFPUC4: Central 17, North Shore 4, South 3, Westside 3 (3 events with no parseable start time are kept as starting on event_date) |
| X-S2-VOLQ | S2 volume | volume_MG null or with a '<' qualifier | Not a measured magnitude | 5 null, 45 '<' |
| X-S2-OUTAGEIN (tag) | S2 oracle | A masked gauge-day in the row's 30-day input window | The oracle input is degraded | per build |
| X-S3-UNCOV | S3 | Any feeding basin not `ledger_known` on D | Unknown zone truth | Ocean / Baker & China 657; North / East 214 |
| X-S3-CARRY | S3 | A zone outfall is still active from before D and none starts on D | As X-S2-CARRY | per build |
| X-S3-QUIET | S3 oracle | No feeding basin overflowed on D (the output 0 is exact) | Exact zeros inflate BSS | all covered days except Westside 65 (both Westside zones), North 42, East 100 |
| X-S3-ID | S3 oracle skill | Link share ≡ 1 | Trivially perfect; integrity check instead (mismatches must be 0) | North Shore 42, Central 97, South 23 basin-days checked |
| X-S3-GEO (tag) | S3 | The zone fired only through geography-evidence outfalls | Unverified posting map | Baker & China 7 of 54, East 6 of 100, Ocean 0, North 0 |
| X-S3-NOTCLEAN | S3 posting check | A sibling zone's link fired within ±2 days | Storm spillover is not misattribution | 10 of 20 Baker & China–only days |
| X-PL-END | posting rulers (S3, OUT) | D > 2026-02-28 (BeachWatch's last filing) | Unknown | 170 days |
| X-S4-UNSAMPLED | S4 | No sample at any zone station | No truth | Ocean 3,371; Baker & China 3,339; North 3,349; East 3,061 |
| X-S4-HISTUNK | S4 (all entries, so oracle and chained stay paired) | Any feeding basin not `ledger_known` on some day in D−7…D | Unknown oracle input | 61 / 65 / 34 / 49 of sampled zone-days |
| X-S4-DAYOF (stratum) | S4 | Zone overflow on D (k = 0) | The day-of is S3's event; reported separately so S3 and S4 stay separable | 28 / 24 / 26 / 58 |
| X-S4-FEW (stratum) | S4 | Fewer than half the zone's stations sampled | "Any station" is a weaker test | East 36%, North 14%, Ocean 9%, Baker & China 8% of sampled zone-days |
| X-S4-FOLLOWUP (fit only) | S4 background fit | OCEAN#20 / 21 / 22 rows | Sampled only after discharges | 46–54 station-days each |
| X-S4-ANALYTE (tag) | S4 | E. coli era 2020-07 → 2021 | The standard changed | ≈ 848 results |
| X-E2E-UNCOV | OUT | A feeding basin not `ledger_known` on D, or on D−7…D for a non-overflow day | Unknown | Ocean 664, Baker & China 664, North 220, East 220 |
| X-E2E-UNK | OUT | Unsampled day within 7 days after a zone overflow | Nobody measured the water | 172 / 224 / 203 / 248 |
| X-E2E-SCOPE | OUT | C-DRY ∪ C-RUNOFF | Not claimed | 14 / 55 / 55 / 170 (294 = 214 + 80) |
| X-S5-HEALTH | S5 live era | Watcher unhealthy (corrections off) | No correction made | from `watcher_runtime` |
| X-S5-CIRC | S5 archive | Westside, 2016-03-19 → 2017-01-10 | The labels are the feed onsets | 283 days |
| X-S5-SELF | S5 | The unit-day whose value the observation replaced | Tautological | per variant and feed |
| X-S5-QUIET | S5 conditional score | No observation in the same basin or zone on D−7…D | The correction is the identity | per feed |
| X-S5-INSAMPLE (tag) | S5 archive | 2016-17 | S2 was in-sample there | all archive days |

---

## 5. Scoring and alert lines

### 5.1 Primary metric: Brier score, reported as BSS

Ranks and decisions use paired Brier differences on identical rows. Why:
1. The Brier score is strictly proper.
2. It equals the mean cost-loss expense integrated uniformly over C/L (Murphy 1966; Schervish 1989; Ehm et al. 2016). It ranks for every user without building in k = 2, which is why cost can stay out of ranking.
3. It is finite on the exact 0s and 1s that the composition and the injections emit.
4. It decomposes (CORP: BS = MCB − DSC + UNC).

Fitting may still use log loss. The log score is the first secondary metric. When Brier and log disagree on a ranking, the report says "no dominance" and shows the Murphy diagram.

### 5.2 Secondary metrics

- Log score in bits, clipped at [0.001, 0.999]; the clip count is reported.
- CORP reliability diagram per unit (isotonic, with bootstrap consistency bands) and MCB / DSC / UNC.
- ROC-AUC.
- PR-AUC beside its prevalence, with lift = AP / prevalence.
- Murphy diagram (elementary scores over θ ∈ (0, 1)).
- At the alert line only: POD, FAR, POFD, CSI, PSS, frequency bias, HSS.
- The S1 deterministic suite (§3.1).
- S4 sensitivity / specificity / accuracy vs persistence.
- Volume log-MAE.

### 5.3 Reference climatology

- Per-stratum climatology from the training fold: unit × calendar month, smoothed over ±1 month. For post-training and prospective days, the reference comes from all T2 seasons. The scored window's own base rate is never the reference.
- **Pooled skill** = 1 − Σ_s n_s·BS_s / Σ_s n_s·BS_clim,s (Hamill & Juras 2006), never a ratio of pooled scores.
- Sample-climatology BSS is also shown, for comparison with older reports.
- The CORP decomposition is of BS itself.

### 5.4 Blocks, CIs, paired tests, power

- **Blocks:**
  - storm blocks [storm start − 1, end + 7], overlaps merged; quiet stretches in ISO weeks;
  - S1: ISO weeks;
  - S5: observation events.
- **Bootstrap:** B = 2,000, seeded. Paired (both arms on the same resample). **90% percentile CIs**, used for both decisions and display (one-sided α = 0.05). P(Δ < 0) is reported too.
- **Multiplicity:** the pre-registered primary comparisons (§5.5) are confirmatory; everything else is descriptive. Holm across simultaneous candidate claims.
- **MDE:** ≈ 2.5 × the bootstrap SE of Δ (80% power at the 90% convention). `stages_build` writes it beside every comparison.

Measured, served vs gb_v1, paired ΔBS with 95% CIs (MDE as % of served BS at 2.8 × SE; about 11% smaller at the 90% convention):

| Unit | Window | ΔBS (served − gb_v1) | MDE |
|---|---|---|---|
| OUT Ocean | out of sample | −0.0030 [−0.0060, −0.0002] | ≈ 38% |
| OUT Baker & China | out of sample | −0.0004 [−0.0031, +0.0024] | ≈ 22% |
| OUT North | out of sample | −0.0010 [−0.0023, +0.0000] | ≈ 27% |
| OUT East | out of sample | −0.0014 [−0.0066, +0.0029] | ≈ 32% |
| OUT Ocean | post only | −0.0024 [−0.0101, +0.0044] | ≈ 70% |
| S2 Westside | out of sample | −0.0007 [−0.0023, +0.0009] | ≈ 21% |
| S2 North Shore | out of sample | −0.0016 [−0.0032, −0.0002] | ≈ 120% |
| S2 Central | post only | −0.0080 [−0.0179, −0.0008] | ≈ 81% |

Consequence: post-only comparisons detect only large effects, so development runs on T2 (all 9 seasons) and confirmation is by non-inferiority.

### 5.5 Pre-registered primary comparisons (in `STAGES_PROTOCOL.md`, frozen at P0)

| Stage | Comparison | Entry | Window | Rule |
|---|---|---|---|---|
| S1 | NWP model vs served ICON: either-wet MAE, lead 1, two-gauge mean | chained | Previous Runs window | CI of ΔMAE excludes 0 |
| S2 | Candidate vs served component, basin-pooled ΔBS, within one geography | oracle (rain known) | T2, confirmed on T1 post | superiority on T2; non-inferiority +5% on T1 |
| S3a | **Geography:** East-zone Brier, SFPUC4 vs GEO_V1 (S4 held fixed) | chained, rain known | T2 | non-inferior (upper bound < +5% of GEO_V1 BS). SFPUC4 fixes a listed defect: Islais was in the wrong basin, so Candlestick was mis-forecast |
| S3b | Westside share s(v) vs constant share | oracle | T2 | superiority (Holm with S3a) |
| S4 | S4 v3 vs served table (adapter), zone-pooled ΔBS | oracle | T2 | superiority, or non-inferiority + it fixes the oracle-worse-than-chained defect |
| S5 | link/zone injection vs basin_swap, conditional set | oracle (perfect feed) and chained (degraded feed) | 2023-07 → 2026-08 | superiority on both feeds |
| OUT | candidate set vs served, zone-pooled ΔBS | rain known and lead 1 | T1 post ∪ T0 | non-inferiority +5%; MCB not worse |

### 5.6 Promotion decision rule (replaces the "King")

A candidate replaces the served set only when all of these hold:
1. Each changed component passes its own-stage primary (§5.5) on T2.
2. OUT is non-inferior at the +5% margin on T1 post ∪ T0, for rain known and lead 1. This is a confirmation where any bias favours the incumbent.
3. Pooled MCB is not worse beyond its CI.
4. The shadow-run Δ report per zone (P11) has been shown.
5. The owner approves.

Comparisons across geographies use only geography-invariant targets: S3 zones, S4, OUT. Basin-level S2 is compared within a geography only.

### 5.7 Cost lives only in the alert line

- **One source, `shared/cost_basis.py`:**
  - `MISS_COST = 2.0`, `FALSE_ALARM_COST = 1.0` (a miss costs 2 false alarms);
  - `P_STAR = FALSE_ALARM_COST / (FALSE_ALARM_COST + MISS_COST) = 1/3`;
  - `relative_value(tp, fp, fn, tn, alpha)` (Richardson REV).
- The five existing copies are removed (P9): `page.html:766 costRatio`, `report_models.py:53 PRIMARY`, `replay_live.py:92 WFN`, `weather_models_eval.py:81 MISS_WEIGHT`, `export_how_it_works.py:61 MISS_WEIGHT`. `tests/test_single_source.py` gains a grep test forbidding miss-weight literals anywhere else.
- **`src/models/alert_lines.py`:**
  1. **Calibration.** Fit a Platt map g_z(r) = σ(a_z + b_z·logit r) per zone, on T2 out-of-fold OUT risks against the OUT label (seasons ≤ 2024-25). Isotonic is drawn only as a diagnostic.
  2. **Line.** Raw line r*_z = g_z⁻¹(1/3) (closed form), with a bootstrap CI. A pooled line r* comes from the pooled map.
  3. **Per-zone or pooled.** A zone gets its own line only if its REV gain at α = 1/3 over the pooled line has a 90% CI above 0 on T2. Otherwise all zones use the pooled line.
  4. **Validation** on T1 post ∪ T0: POD, FAR, POFD, CSI, PSS, cost per day vs never/always alerting, V(1/3) with CI, and the REV curve V(α) (Figure 3).
  5. Output: `stages/<set>/alert_lines.json`.
- The served line stays 0.25 until a promotion.
- **No alert reads the forecast today.** The line sets only the Model check's default grading line; B5 (upcoming-CSO alert) will be its first reader.
- The public bands (Today ≥ 25, map 10/25/50, `riskInfo`, landing "rising" 0.25) stay unchanged. Owner decision D12.

### 5.8 What replaces each current cost ranking

| Current | Replacement |
|---|---|
| `report_models.py`: PRIMARY / WEIGHTINGS / cost() / rank_table* / King / cheap_table / sensitivity grids | Stage leaderboard: per stage, paired ΔBS vs the served component with CI, MDE and verdict words ("better", "no clear difference", "worse"); OUT BSS by entry and lead; a Murphy diagram (shows every cost ratio at once) |
| `export_how_it_works.report_graded`: cost at the line, cheapest line per zone, cost charts, candidate cost bars | The stages report, one section per figure box; cost appears only in its "Alert line" section (Figure 3) |
| `export_how_it_works.pipeline_svg` | `stages_flowchart.render` |
| Model check `costRatio` / `lineCost` / `cheapestLine` / cost columns / "Every model set ranked by cost" | Stages tab: scored figure as header and navigation; one card per stage (oracle / chained BSS with CI, reliability sparkline, benchmark row, the left-out waterfall); an Alert line panel (per-zone line, REV curve; the "one miss = N" input lives only there and only moves the suggested line p* = 1/(1+N)) |
| `replay_live` WFN, cost sort, "best" | Sorted by conditional ΔBS; "best" only when the CI excludes 0; counts kept as description; `cost` keys dropped in the same commit as `tests/test_live_rules.py:217–243` |
| `weather_models_eval` appendix cost | S1 suite + S2 chained by lead (ΔBS vs rain known) |
| `live_rules.FLOOR_MIN` (chosen on cost at 25%) | Re-tested as an S5 variant by ΔBS; served live_v2 untouched |
| `test_scorecard_window.py:371` "King under the primary setting" | Assertions on the stage report (sections = spec stages; no "cost" outside the alert section), in the same commit |

### 5.9 Naming fix and one line grid

**Names.**
- The Model check's `zoneStats.far = fp/(fp+tn)` (`page.html:851`) is renamed `pofd` ("share of quiet days alerted").
- A true `far = fp/(tp+fp)` ("share of alerts that were false") is added.
- `verify.contingency(tp, fp, fn, tn)` is the only Python implementation and uses JWGFVR names.
- The report carries a glossary.

**Grid.** `scorecard.LINE_GRID` (10 lines) stays the single grid for server-side confusions, so its test pins don't change.
- `export_how_it_works.LINE_GRID` is deleted with its cost charts.
- `verify.grid_with(*lines)` adds served and per-zone lines exactly; it is used only after promotion. An off-grid line (1/3, or a per-zone line) can therefore never blank a zone card.
- REV curves are computed offline, so request-time work does not grow.

---

## 6. Model changes the stage scores imply

| # | Change | Demanded by | Scope | Served impact |
|---|---|---|---|---|
| 1 | `compose_v2.py`: S3 (links → zones), S4 (q with background), OUT (r), inject hooks at basin, link, zone and sample; GEO_V1 adapter composes per legacy group then zone max | Every per-stage score; S5 | Now. Golden: adapter with policy (x(0)=1, b=0) == `impact.compose` exactly on the same inputs; ≤ 0.0025 vs stored risks | none |
| 2 | Observations enter after the split: link and zone injection, sibling co-firing shares, flag persistence per link, station → basin per §2.5 | S5 (basin injection is what makes live_v2 lose at 25%) | Now in replay; served at promotion | at promotion |
| 3 | S4 v3 zone-level with a rain background (§3.4) + STARDB 2016-10 → 2020-07 | S4 oracle worse than chained; the rates table | Now, as a candidate | at promotion (the public % moves) |
| 4 | SFPUC4 S2: Westside and North Shore restamped; Central refit (logit_v1 recipe, C by T2 LOSO) | Geography | Now, as a candidate | at promotion |
| 5 | South S2 by partial pooling: shared Bay design (`shared_logit`) with a basin intercept and scaled slope, vs standalone, vs P(S \| C, rain); chosen on T2 log loss | South: 23 days, 21 storms | Now | at promotion |
| 6 | East union rule (max / noisy-OR / cofire) | S3 chained East | Now | at promotion |
| 7 | Volume heads owned per candidate; out-of-fold evaluation; log-linear rain → log(MG \| event) replaces the GBR heads if not worse on T2; the build **fails** if a declared head is below its event floor (no silent volume 0) | S2 volume score; South at 22 vs 20 | Now | at promotion |
| 8 | `history_full_v1`: the live frame reaches back ≥ 35 days (Open-Meteo `past_days` 35, ACIS 45 days), so 14/30-day sums, antecedent moisture and dry spell match training | Gap between S2 as-served and lead L | Candidate input rule now; served only with a promotion | at promotion |
| 9 | Gauge-outage mask on every training day (not only post-training) for new candidates | Oracle input consistency | Now | none |
| 10 | `weather_models_eval`: NaN days excluded, not zero-filled; full JWGFVR suite; by lead | S1 | Now | none (a report changes) |
| 11 | Previous Runs fetch (ICON, GFS, ECMWF, leads 1–5) + ICON short lead back to 2022-11-16 (tagged) | S1 by lead; every chained entry | Now (data) | none |
| 12 | Archive onsets take their basin from `outfall_ids` through the geography (`train_v4.py:165` keeps the first basin; `replay_live.py:128–130` drops multi-basin strings) | SFPUC4 labels; S5 archive | Now | none |
| 13 | Public % recalibration per zone (Platt), shown as the "stages" value first | OUT MCB | Later, owner call | at promotion |
| 14 | KSFO hourly history (IEM) as peak-hour truth; L0 live variant | X-S1-PEAK | Later | none |
| 15 | Legacy Bay-side 2013–16 records; STARDB before 2016; Westside follow-up proxy | S2 / S4 power | Later (needs pre-2016 rain) | none |
| 16 | Realistic sample arrival in S5 (`feed_sample_dates`); watcher-era export | S5 chained | Later | none |
| 17 | Ensemble S1 (CRPS) propagated | S1 → S2 uncertainty | Later | none |
| 18 | B5 upcoming-CSO alert reading `lines` | The alert line has no reader | Later (product) | product |

---

## 7. Artifacts and schema

**Rule:** no existing file changes schema. Every addition is additive and stamped.

**Per set** (the served set included), under `features/forecast/data/models/stages/<set>/`. This is a sidecar: never written into the served directory and not copied by `promote.py` before P11.

```
manifest.json      {schema:"bwtf.stages/1", set, geography, pipeline, components{s1..s5}, spec_version, protocol:"stages_v1@<sha>",
                    built_at (utc_iso), windows{holdout_start:"2023-07-01", post_start:"2025-11-01", freeze:"<date>", seasons:[2016..2024]},
                    entries:[oracle,rain,L0..L5,L0s,L1s], inputs{file:sha256}}
rows.csv.gz        date, stage(s1|s2|s3|s4|s5|out), unit_type(series|basin|link|zone), unit, entry, lead, tier(T0|T1|T2), sel,
                   p, q, b, v_hat, y, y2(posting / volume / storm), n_sampled, stratum, excl(first-match id or ''), tags(';'-joined)
scores.json        {stage:{unit:{entry:{window:{n,n_pos,n_blocks,bs,bss,bss_ref,bss_sample,logs,clipped,roc,pr,prev,
                    corp{mcb,dsc,unc,curve},ci{...},mde}}}}, paired{vs_served{...}}, exclusions{stage:{rule:{unit:n}}},
                    claims{id:{unit:n}}, integrity{s3_identity_mismatches:0,...}, figure{node_id:{oracle,chained,lead}}}
alert_lines.json   {cost_basis:{miss:2,fa:1,p_star:0.3333}, calibration:"platt_v1", window:"T2", zones{z:{a,b,line_raw,ci,iso_knots}},
                    pooled{line_raw,ci}, per_zone_adopted{z:bool}, validation{window:{pod,far,pofd,csi,pss,cost_per_day,rev_1_3,ci}}, rev_curve{...}}
report.html        draft stages report (not routed; public copy only at merge)
```

**S1 is set-independent:** `data/models/stages/_s1/<model>.csv.gz` (date, series, lead, fc_in, obs_in, fc_max1h, fc_max3h, excl) plus `s1_scores.json`.

**Size budget.** About 3–6 MB of gzip per set. Rows are committed only for the served set and the SFPUC4 stage candidates; GEO_V1 candidates get `scores.json` only. (No `.vercelignore`: the whole repo deploys, unrouted.)

**SFPUC4 stage candidates** go under `data/models/stages_candidates/<name>/`, which `list_candidates` does not scan. They would otherwise be auto-listed: they have no `southeast` model (`test_scorecard_window.py`) and no public explorer, and the Model check hard-codes basins. Contents:
- `{westside,north_shore,central,south,citywide}_model.pkl` (+ `geography`)
- `*_volume.pkl`
- `s3_links.json`
- `s4_quality.json`
- `manifest.json` (+ geography, pipeline, components)
- a legacy-schema `scorecard.json.gz`: zones = r_z, `groups: {}`, basins incl. `south`, + a `geography` block with basin names.

They join `candidates/` only after P9 makes the Model check read basin keys and names from the artifact, with `test_candidate_models_unpickle_anywhere` generalized to the manifest's basin keys.

**Spec files:**
```
s3_links.json    {geography, links{id:{basin,zone,outfalls,evidence,identity,share:{kind:"identity"|"logit_logvol",coef,basin_median_mg},vol_share}},
                  union{zone:{rule:"max"|"noisy_or"|"cofire",pi?}}, cofire{"a|b":p}, fit{window,folds}}
s4_quality.json  {geography, kind:"impact_v2_adapter"|"zone_v3", background{coef,features}, buckets{zone:{k_s:x}}, zone_median_mg,
                  monotone:true, sources, fit{window,folds}}
```

**How the rows are produced** (`stages_build.py --set NAME [--root served|candidates|stages_candidates] [--steps ...] [--write]`):
1. Load the components and assert the stamps.
2. Build the truth frames once (`truth.py`): masked gauges, the ledger by outfall/basin/zone for the geography, `ledger_known`, samples by zone, postings, storms.
3. **S2 out-of-fold:** `leaderboard.season_cv(..., return_oof=True)` and `shared_logit.season_cv(..., return_oof=True)` give T2; holdout siblings via `stage2_variants._refit_holdouts` (fidelity ≤ 0.002) and finals on post give T1. S3 shares and S4 fits are refit per held-out season.
4. Rebuild features per entry: rain known; lead L (gauges through D−L−1 plus lead-matched Previous Runs days); as served (7-day frame).
5. Run `compose_v2` per entry → S3, S4, OUT.
6. S5: `replay_live` variants through the inject hooks, emitting per-day risks (new `replay_live.per_day_risks`; old JSON keys kept).
7. Apply `exclusions.apply`, then `verify`, then write `rows.csv.gz` and `scores.json`.

**API.** `GET /forecast/api/stages?model=&window=`: a handler in `page.py` → `features/forecast/stages_api.py`. It reads `scores.json` only (no sklearn, no bootstrap on Vercel) and dates through `shared/clock.py`.

**Payload (at promotion only):**
- adds `predictions[*].stages{s2:{basin:p}, s3:{zone:p}, s4:{zone:q}}` and a top-level `geography{key, basins[], zones[]}`;
- `zones{zk: float}` stays a scalar;
- `model_stamp` gains `geography`, `pipeline`, `protocol`;
- `forecast_history` graders read zone keys (geography-invariant) and basin keys by stamp.
- No database migration.

---

## 8. Implementation plan

All work is commits on `forecast-stages`, local only; the owner pushes and merges. Each phase updates its tests in the same commit.

| Phase | Title | Can run with |
|---|---|---|
| P0 | Guard rails and protocol | first |
| P1 | Flowchart spec and renderer | P2, P3, P5 |
| P2 | Geography | P1, P3, P5 |
| P3 | Verification library and cost basis | P1, P2, P5 |
| P4 | Truth and exclusions | after P2 and P3; with P5, P6 |
| P5 | Data: forecast archives, S1, samples | from day one |
| P6 | compose_v2 | after P2; with P4, P5 |
| P7 | Stages build for the served set | after P3–P6 |
| P8a–f | Candidates | each independent, after P7; with P9 |
| P9 | Report, Model check, ranking replacement | after P7 (SFPUC4 views after P8) |
| P10 | Alert lines and prospective grader | after P7 and P8 |
| P11 | Promotion tooling | owner only |

**Untouched through P10:**
- `live_dashboard.py`, `impact.py`, `stage2.py`, `live_rules.py`, `groups.py`, `scorecard.py`
- `shared/{stations,outfalls,zones}.py`
- the served files in `data/models/`
- `app/landing.py`, `_today_board.html`, the alert dispatcher

`tests/test_promotion.py` pins stay green throughout.

**P0 · Guard rails and protocol.**
- `tests/test_served_golden.py`:
  - sha256 pins of `served.json`, the pickles, `stage2.json`, `impact_table.json`, `thresholds.json`, `eval_report.json`;
  - a `_day_payload` golden: build frames from the committed fixture `tests/fixtures/stages_golden_rain.csv` via `LiveData._add_daily_features` → `_score_frames` → `_day_payload(..., observed={}, live=None)` on 6 days; compare `zones`, `predictions`, `impact_groups` and `discharge_probs` exactly against `tests/fixtures/stages_golden_payload.json`. No network.
- Only a promotion commit may update these pins.
- `features/forecast/STAGES_PROTOCOL.md`: windows, entries, primary metric, reference, blocks, CI level, MDE, §5.5 primaries, exclusion order, decision rule. Its commit date is the freeze date that starts T0.

**P1 · Flowchart spec and renderer.**
- `src/models/stages_spec.py` (§1.6).
- `src/models/stages_flowchart.py`: `render(spec, geo, scores=None) -> (svg, phone_html)`, ported from the scratch `fig.py`; helpers imported from `export_how_it_works`.
- `tests/test_stages_spec.py` (§1.6).
- A scratch render with "—" values.

**P2 · Geography.**
- `shared/geography.py` (§2.6).
- `csd_labels.build_daily_labels(geo=GEO_V1)`: coverage via `Basin.facility`.
- `train_v4.build_dataset(geo=...)`, `target_frame`, `apply_archive_labels` (archive basin from `outfall_ids`).
- `stage2.fit_link_shares` lives in a new `stage3_links.py` instead, so `stage2.py` is untouched.
- Defaults stay byte-identical.
- The CIWQS refresh (`src/collectors/csd_ciwqs/aggregate.py`) fails loudly on an outfall id that is not in the registry.

`tests/test_geography.py`:
- GEO_V1 derived maps == `groups.py` exports (BASIN_KEYS, GROUPS_BY_BASIN, ZONE_GROUPS, OBSERVED_STATION_BASIN);
- GEO_V1.basin_of_outfall(o) == o.basin for every outfall (catches in-place edits);
- SFPUC4 is derived only from report_basin; CSD-119 → north_shore;
- the 5 links and their outfall sets; the links partition the outfalls;
- every `sf_csd_events.csv` outfall is in one link (a data test, here and not at import);
- event days 65 / 42 / 97 / 23;
- station_basin('4619') == 'central';
- the `build_daily_labels(GEO_V1)` frame hash is unchanged.

**P3 · Verification library and cost basis.**
- `src/models/verify.py`: `continuous`, `contingency` (POD, FAR, POFD, FBI, CSI, ETS, PSS, HSS, SEDI), `brier`, `bss` (stratified reference, Hamill–Juras pooling), `log_score`, `corp` (sklearn IsotonicRegression), `roc_auc`, `pr_auc`, `murphy`, `rev_curve`, `storm_blocks`, `block_bootstrap`, `paired_delta`, `mde`, `grid_with`.
- `shared/cost_basis.py`, not wired anywhere yet.
- `tests/test_verify.py`:
  - the WWRP 2×2 worked example;
  - BS = MCB − DSC + UNC to 1e-12;
  - seeded deterministic bootstrap;
  - PSS = V at α = base rate;
  - p* = 1/3.

**P4 · Truth and exclusions.**
- `src/models/truth.py`: `ledger_known`, `basin_onsets`, `carry_days`, `zone_overflow(day)`, `zone_overflow(storm)`, `zone_elevated`, `storms`, `postings`.
- `src/models/exclusions.py` (§4).

`tests/test_exclusions.py`:
- counts partition;
- each rule on a fixture;
- C-DRY + C-RUNOFF = X-E2E-SCOPE per zone;
- §4's counts snapshot;
- `truth.zone_overflow` reproduces the scorecard's zone discharge labels outside 2016-03-19 → 2017-01-10, and the in-window diff list is pinned;
- the S3 oracle zone truth is identical under both geographies.

**P5 · Data.**
- `src/collectors/openmeteo_previous_runs.py`: probe first, then cache `data/raw/openmeteo_prev_runs_<model>.csv` (timestamp, lead_day, precip_inches). ICON short lead back to 2022-11-16.
- `weather_models_eval`: NaN fix and full suite.
- `train_v4.load_samples(sources=...)` gains STARDB, de-duplicated.

Tests:
- cache schema;
- a NaN day is excluded, not zero;
- no duplicate (station, day, analyte) after the STARDB merge;
- the floor reproduces 0.184" / 0.73 (with the either-wet variant recorded).

**P6 · compose_v2.**
- `src/models/compose_v2.py`: `s3`, `s4`, `out`, `Inject`, GEO_V1 adapter.

`tests/test_compose_v2.py`:
- adapter with policy == `impact.compose` + `zone_risks`, exactly, on all served-scorecard days with the same unrounded inputs;
- ≤ 0.0025 vs the stored `zones.risk`;
- nothing injected → identity;
- Sea Cliff link_swap → Baker & China = 1 and Ocean Beach = max(pred, 0.59);
- Islais flag → East = 1 whatever the basin;
- East union inside the Fréchet bounds.

**P7 · Stages build for the served set.**
- `src/models/stages_build.py` in sub-steps:
  - 7a: `return_oof=True` in `leaderboard.season_cv` and `shared_logit.season_cv` (pooled OOF PR-AUC == manifest `season_cv_pre_holdout` within 1e-6);
  - 7b: entries and feature rebuild by lead;
  - 7c: composition and rows;
  - 7d: scores.
- Run it for the served set (name read from `served.json`, never hard-coded), `logit_v1` and gb_v1 (T1 only; no LOSO for the GBM).
- Tests on a small fixture:
  - no T3 row emitted;
  - tier labels correct;
  - as served differs from lead L only in history features.

**P8 · Candidates** (each independent; saved through `candidates.save_candidate(root=stages_candidates)` with stamps; scored by `stages_build`):
- (a) SFPUC4 S2 (central + south, pooled);
- (b) S3 links + union rule;
- (c) S4 v3 (+ STARDB);
- (d) S5 link/zone/sample swaps in `replay_live` (candidate only; `live_rules_v3.py`);
- (e) `history_full_v1`;
- (f) volume heads.

Tests: fit determinism; stamp asserts; a head below its floor raises. "S4 v3 oracle ≤ chained" is a report check, not a CI gate.

**P9 · Report, Model check, ranking replacement.**
- `src/models/stages_report.py` writes drafts to `data/models/stages/<set>/report.html`. Sections follow `STAGES`: Figure 1 (scored), S1…S5, OUT, Alert line (Figure 3), Figure 2, the exclusions ledger, how sets are ranked.
- `export_how_it_works` figure 1 → `stages_flowchart`; report B rewritten (§5.8); helpers moved to `svgkit.py`.
- `report_models.py` rewritten as the stage leaderboard.
- `replay_live` sorts by ΔBS.
- `stages_api.py` + route.
- `page.html`:
  - a Stages tab (figure header, stage cards lazy-loaded, heavy parts collapsed, plain chips);
  - FAR/POFD fix; cost columns removed;
  - basin keys and names read from the artifact (fallback `BASIN_META`).
- Grep test for cost literals.
- Run the phone/desktop grid scan before hand-over.

Tests:
- the API equals `scores.json`;
- no "cost" outside the alert section;
- `test_scorecard_window:371` and `test_live_rules:217–243` updated in the same commits;
- the page renders with GEO_V1 and SFPUC4 artifacts.

Public `reports/2026-10_forecast_stages.html` is written only at the owner's merge.

**P10 · Alert lines and prospective grader.**
- `src/models/alert_lines.py` (§5.7).
- `src/models/grade_prospective.py`: a read-only `forecast_history` export (Supabase REST, read key) → T0 rows by lead.
- `shared/risk_levels.py` (one per-zone band table), **not wired**.

Tests:
- line = g⁻¹(1/3);
- the pooled-vs-zone rule;
- held-out metrics present.

**P11 · Promotion tooling (only when the owner promotes).**
- `served.json` gains `pipeline: "stages_v1"`, `geography`, `components`, `lines`, `line_rule: "cost_loss_k2_platt_v1"`. The scalar `line` is kept.
- `live_dashboard` branches on `pipeline`. Under `stages_v1`:
  - it loads pickles listed in the manifest, keyed by geography (a missing file raises);
  - it uses `geography.get(...)` resolved once at import, plus `compose_v2` with link/zone injection and `history_full_v1`.
- `promote.py`: an atomic whole-bundle swap (pickles, heads, `s3_links.json`, `s4_quality.json`, `alert_lines.json`, `served.json`). It refuses mixed bundles, takes KEYS from the manifest, `--lines auto`, and never defaults to 0.5.
- `train_v4 --rescore` refuses when `pipeline != two_stage_v1` (use `stages_build --rescore`).
- `model_stamp` gains geography, pipeline and protocol.
- `page.html` reads basins from the payload.

Tests:
- the `_day_payload` golden is unchanged for `two_stage_v1`;
- a temporary served dir holding a stages set yields scalar zones, and a missing pickle raises;
- a per-zone shadow-run Δ report over post-training and live history goes to the owner before the switch;
- `test_promotion` pins move in the promotion commit only.

---

## 9. Risks, unknowns, owner decisions

**Risks:**
1. **Power.** Most comparisons will read "no clear difference" (MDE 20–40% out of sample, ≥ 70% post-only; South 1 post event). The rule is that the incumbent stays unless the CI excludes 0. The SFPUC4 remap is decided by non-inferiority because it fixes a defect.
2. **Selection.** The served set was chosen on holdout + post, so T1 favours it. The nested rule protects only new candidates, and T0 (from the 2026-27 wet season) is the only fully clean evidence.
3. **Silent geography mismatch.** Mitigated by: no registry edits, stamps plus asserts without defaults, `groups.py` frozen, the pipeline flag, the `_day_payload` golden, and a whole-bundle promote.
4. **The public % moves at promotion.** S4 v3's tail, zone-level S4, East union and `history_full_v1` all shift numbers. Shown first in the shadow Δ report.
5. **Test churn.** Pins on the King string, the replay cost keys and the candidate explorers. Each is updated with its producer.
6. **Public reports.** `/reports` is public, so drafts stay under `data/models/stages/`.
7. **Vercel cost.** Scores are precomputed; no request-time bootstrap; no grid growth.
8. **East union π** rests on 23 South days. It is Fréchet-bounded and tested against max and noisy-OR.
9. **Previous Runs semantics** (lead-L daily sums from `previous_dayL` hours) need the P5 probe. If they fail, chained runs fall back to tagged lead 0.

**Unknowns, each with a probe:**
- the exact first day of each (model, lead) in Previous Runs (P5);
- the meaning of the live `cso` field (`feed_station_days.raw_last`) for named-outfall S5;
- BeachWatch completeness in its last filed months;
- the provenance of the hinge knots (treated as contaminating everything);
- real sample publish times;
- a reproducible watcher-era Supabase export;
- whether the archived ICON configuration matches the served `icon_seamless`.

**Owner decisions** (recommended defaults are in the structured list):
- D1 drop group / keep derived links
- D2 basin keys
- D3 display name South
- D4 CSD-037
- D5 South S2 pooling
- D6 East union default
- D7 primary score
- D8 BSS reference
- D9 public claim semantics
- D10 S4 truth and STARDB
- D11 alert-line rule
- D12 public bands
- D13 protocol freeze and confirmation windows
- D14 promotion rule
- D15 geography decision rule
- D16 chained headline lead
- D17 `history_full_v1` timing
- D18 display names at promotion
- D19 archive labels excluded from truth
- D20 archive fetches
- D21 report / UI publication timing
