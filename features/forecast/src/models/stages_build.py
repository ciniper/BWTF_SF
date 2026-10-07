#!/usr/bin/env python3
"""stages_build — every stage of one set, scored on days no fitted component saw
(P7 orchestration and P8's scoring of stage candidates; STAGES_DESIGN.md Part C §7
"How the rows are produced" and the artifacts schema, as amended by Part A A3 (no
alert_lines.json) and Part B 1, 2, 7, 9, 13, 14, 16; STAGES_PROTOCOL.md stages_v3
§2–§9, which rules where they differ).

The pieces were built and reviewed on their own; this module composes them and
adds only what no single piece can decide:

    stages_entries   the input frames each entry feeds S2 (rain known, lead 0–5,
                     as served)
    stages_s2        S2 out of fold: p and v̂ per basin, per (tier, fold)
    compose_v2       S3 → S4 → OUT, one algebra for oracle and chained
    stages_s5        the S5 feeds and live corrections, on the conditional set
    exclusions       the not-scored rules, first match, the partition asserted
    verify           every score: BSS, Brier, log, CORP, ROC, PR, the risk-level
                     edges, block-bootstrap CIs, paired deltas, MDE
    stages_s1        S1 (set-independent): read from stages/_s1/s1_scores.json

**Every fitted component is refit per fold (Part B 1; protocol §2).** S2's
weights and volume heads come from stages_s2's folds. The GEO_V1 adapter's
fitted parts — stage 2 v2's link shares, the impact table S4 composes, the
co-firing shares S5's siblings read — are fitted too, so for T1-holdout (fit on
days before 2023-07-01) and each T2 season (fit on the other eight seasons of
2016-17 … 2024-25, the folds stages_s2 fits, through its own ``_plan``) they are
refit by the served fitters on the fold's training days only:
stage2.fit_outfall_split, stage2.group_event_days and train_v4.fit_impact_table
(``fit_s3_s4``; frames, ledger events and the fold's own volume heads restricted
to the fold), compose_v2.cofire inside stages_s5. On the full training window
``fit_s3_s4`` reproduces the served stage2.json and impact_table.json exactly
(tests/test_stages_build.py), so the fold restriction is the only difference. T1
reads the set's own artifacts (fit through 2025-10-31). A stage 2 v1 set (no
split; candidates.load_stage2 → None) has identity links and the raw table.
A set trained on a longer label record (its manifest's ``record``: the older
SFPUC reports, stages_s2) fits S2 on that record; its S3 / S4 folds are fit on
the served record, like every other set's, so it differs in its S2 weights only.

**T0, the prospective window (protocol §2).** T1 stops at the freeze; T0 is T1's
fold, not refit (the finals, their heads and specs), on every day after it to the
data end, shadow-run through every stage exactly as T1 (S2 → S3 → S4 → OUT, and
S5), on the same archived inputs in every set, so a comparison pairs identical
inputs (Part B 16). Its runs start with the same warm-up, from the same models, so
a T0 row is the row T1 would give on that day. The served set's T0 is also graded
as served (``t0_as_served``): what forecast_history says the page showed, never one
arm of a comparison. While the data end is before T0's first day (2026-10-03),
T0 holds no row and nothing here differs from a build without it.

**Runs and warm-up (§7 step 5).** compose_v2's lags are row offsets, so each
(entry, tier, fold) is composed over runs of consecutive days that start
``WARMUP_DAYS`` (8: live_v2's 9-day window, which S5 replays) before the fold's
first scored day; warm-up p and v̂ come from the fold's own models and are never
emitted. A day the entry lacks (X-S1-NWPGAP / X-S1-NOLEAD, or before a lead
archive starts) breaks a run. S2 and S3 rows (same-day) are emitted on every day
with inputs. Rain known (oracle, rain) is one run per fold, so its S4 and OUT
rows read their own run's D−7…D.

**A lead entry's S4 and OUT read the issue day's chain (protocol §3).** Lead L is
the forecast issued on I = D − L "for days on or after the issue day, gauges
before → S1 → …", which is what the page composes (live_dashboard._day_payload:
one frame, past days from the gauges, I … I+5 from the forecast) and what the
as-served T0 grades from forecast_history. So the S4 / OUT row of D at lead L reads, on
each history day d of D−7…D, the S2 output the issue day knew: rain known for
d < I, and for d = I + j (j = 0 … L) the entry L_j's S2 output on d — the frame
stages_entries builds for target d from the forecast issued on d − j = I (as
served: L0s, L1s). Never the lead-L forecast of an earlier day (issued before
I, when the gauges for that day were already in). ``issue_inputs`` stacks the
8-day window of every target into one compose_v2 run (each window's last row
reads exactly its own 8 rows; S3 is same-day); tests pin it to compose_v2 on
the real 8-day window. A target whose forecast for one of I … D is missing has
no S4 / OUT row; those days are counted in scores["dropped"]. Not emulated for
as served: the page's own rows for the 7 past days (their history features
truncated at the frame's start); rain known stands in for them.

**Entries (protocol §3).** S2's oracle is rain known (one input), so S2 rows are
'oracle' and the lead entries; scores["aliases"] says rain = oracle at S2. From S3
on, each entry has its own rows:
    S3 oracle   the TRUE basin occurrence with v̂ from rain (Part B 2;
                compose_v2.BasinInputs.oracle), never the true volume
    S4 oracle   the true overflow history (0/1) at the S4 spec's unit with its
                true size (``true_history``)
    OUT oracle  the same true history through OUT's algebra: the ladder's
                truth-at-S4 rung. Truth-at-S3 (the S3 oracle carried to OUT) is
                scored in the ladder only: protocol §3's entry ids have no name
                for it, so it is never a row.
    chained     rain, L0–L5 (L0 tagged optimistic), L0s, L1s through the chain
                (a lead entry's S4 / OUT on the issue day's chain, above).
A day the ledger does not know is 0 in an oracle input (BasinInputs.oracle takes
0/1 only); every row that could read it is left out by the coverage rules, which
integrity["oracle_unknown_reads"] checks row by row.

**X-S3-ID from the set's S3 spec (protocol §7).** A zone's oracle is trivially
exact when each feeding basin reaches only that zone and passes its overflow
through a link whose share is identically 1 (compose_v2's ``_share_is_one``),
not when the geography calls the link the basin's only one: under stage 2 v2
North's Crissy Field share is 1, so North is checked, not scored, though its
basin has two links. The context's zone ``identity`` is replaced per fold
(``identity_zones``), and integrity["s3_identity_mismatches"] must be 0.

**S5 (§3.5; protocol §8).** stages_s5.s5_rows per feed (perfect, archive,
degraded:1…5; the watcher has watched no wet season) and per fold, on the
rain-known S2 output with the fold's specs, every variant in stages_s5.VARIANTS;
rows of one (feed, tier) are put through exclusions.apply again together, so
X-POWER counts the whole window. The bootstrap resamples observation events
(``s5_blocks``, protocol §6's S5 blocks); X-POWER, as everywhere, counts the rain
storm blocks (truth.blocks) the cell's rows touch, never observation events
(``score_s5``). S5's window is 2023-07-01 → the data end: T1-holdout ∪ T1 ∪ T0.

**Scores (protocol §4–§6).** Per (stage, unit + pooled, entry, window):
verify.scores_bundle against the climatology of that fold's training days
(unit × calendar month ±1; T1 and T0 from all T2 seasons, T1-holdout from the days
before 2023-07-01, a T2 season from the other T2 seasons), pooled by
Hamill–Juras through the per-row reference; storm blocks from truth.blocks;
B = 2,000, seed 0, 90% CIs; threshold scores at the risk-level edges only.
Paired: chained − oracle on the intersection of scored rows; as served − lead L;
the OUT error budget ladder; S5 corrected − plain and the §8 primary
link_zone_swap − basin_swap; and, for a set that is not the served one,
candidate − served on identical rows (``vs_served``, read from the served set's
rows.csv.gz). S3 adds lead 1 (and every chained entry) scored on the oracle's own
rows against the oracle's reference (``score_on_rows``: scores["s3_on_oracle_rows"]).

**The served set's T0, as served (scores["t0_as_served"]; protocol §2; Part B 20,
28).** grade_prospective.rows (the committed forecast_history snapshot) → the rows
the served set's name made → OUT by lead (L0 … L5), per zone and pooled, on target
days up to the data end, with OUT's truth, exclusions and reference; with counts
(issue days, rows graded, rows waiting for truth, rows under another model). It
confirms what the public saw and never enters a comparison (its inputs are not the
shadow-run's: Part B 16). No snapshot, or nothing yet to grade, and it says so.

**The figure (scores["figure"]).** Every post-training cell is X-POWER, so each
stage's pills show the window where it has power: S1 its whole Previous Runs
window at lead 1 and the floor; S2, S3, S4 and OUT cross-season (T2, labelled
development; an existing set's S2 development (selection-contaminated), protocol
§2); S5 its protocol window, as the change in Brier of the set's own correction
rule (basin_swap = live_v2) on the perfect feed and the degraded feeds' mean. A
set the build does not refit per season shows what it has (FIGURE_FALLBACK).
Each pill carries the words for its window and its post-training cell (T1, with
its CI) for the tooltip; for a GEO_V1 set the post-training words say those days
also picked the served set, so scores on them favour it (X-SEL post_selected);
S3's chained pill is lead 1 on the oracle's rows (every day in the tooltip); S5's
tooltip adds link/zone injection; the figure carries a one-line caption. A pill is
X-POWER when its cell is: below 10 positives or 8 storm blocks, S1's included
(``s1_figure``: either-wet days, and the storm blocks its days touch).
scores["figures"] keeps one figure per fitted window (T1, T1-holdout, T2; T0 is
shown as served), and scores["figure_counts"] adds
S5's own rules from the build's rows to the truth catalog's counts: the chip
counts the perfect feed in S5's window, so each count is distinct zone-days (0
where the rows never hit a rule), and its tooltip gives every feed's own
(``s5_feed_counts``).

**Stage candidates (``--root stages_candidates``; Part B 13, 14; design §7).** A
set under data/models/stages_candidates/<name>/ is read through
stages_candidates.load_set, which asserts its stamps, keys, sha256s and weights
≥ 0, then ``from_saved`` turns it into what the build scores (``StageCandidate``):
geography from the manifest (G.stamped; SFPUC4_V1's basins westside,
north_shore, central, south; no GEO_V1 adapter), and per fold — S2 from the
candidate's own fits, never refit here: T1 its finals, T1-holdout its siblings
fit before 2023-07-01, T2 the bake-off's outer-fold rows (stages_s2_sfpuc4's
nested procedure, rain known; the s2 section names the bake-off), so a T2 fold
has no lead entry and no warm-up before its season (S4 / OUT start a week in,
counted in scores["dropped"]); S3 and S4 from the spec files' per-fold records
(stages_s3_links.fold_spec; s4_quality.json's fit.folds), each fit span checked
against its fold (``check_fit_span``), and the S2 its shares were fit on recorded
(``s3_fit_s2``: a development stand-in, or a spec that does not say, puts a
caveat on S3's primaries, Part B 2), and the link shares φ S4's size classes were
fit at beside the S3 spec's (``s4_fit_sizes``: S4 fit = S4 use, Part B 6; a
mismatch puts a caveat on S4's and OUT's primaries). A volume head under the 20-event floor must
be the declared fallback in every fold, a T2 outer fold's head as the bake-off
records it (``t2_head_records``; Part B 7: the build fails only if no declared
fallback exists). The build's rain-known S2 must reproduce the bake-off's own rows
of those finals and siblings on every tier the served-recipe arm also holds
(``bakeoff_fidelity``), and the recipe's rows must carry the build's labels
(``s2_vs_recipe``), so the served-recipe arm read the same inputs (Part B 16).
S5 runs without live_v2's rule sets (basin_swap and
its kin, stages_s5.LIVE_V2: GEO_V1's basins only). A zone_v3 S4 background reads the two-gauge
rain D−2…D the entry knew: rain known for oracle / rain (and S5); for a lead
entry the issue day's own (the entry frame's precip_avg and lags on D: gauges
before I, the forecast from I), so a lead's S4 is q = 1 − (1 − b(D))·(1 − q₀(D)),
q₀ composed on the stacked issue-day windows with no background
(``issue_levels``). Rows are written for stage candidates too (design §7's size
budget).

**Comparisons (protocol §8; scores["paired"]).** ``vs_served``: candidate − served
on the unit-days both scored, from the served set's written rows, per window, and
for OUT the union T1 post ∪ T0 (OUT_WINDOW) once T0 holds rows; across
geographies only the geography-invariant targets (S3 zones, S4, OUT), never
basin-level S2. ``s2_vs_recipe``: S2 within the candidate's geography, candidate −
the served recipe refit on its labels (the bake-off's served-recipe rows), on
(tier, fold, basin, day). ``s3b_vs_constant``: the S3 oracle on the Westside
split's zones, the candidate's shares − its constant-share benchmark (both fit
per fold by stages_s3_links), with the 95% CI Holm's first step reads.

**Primaries and the promotion criteria (protocol §8, §9).** scores["primaries"]
holds one row per §8 row, its comparison, entry, window and rule in the protocol's
words (read from the frozen file), the computed Δ with its 90% storm-block CI,
P(Δ < 0), MDE and margin, and a status: pass | fail | not yet computable | not
applicable, with the reason; a row whose component is the served set's is not
applicable, its computed status kept (``settle_unchanged``: §9.1 reads changed
components). S3a and S3b are one Holm family (``holm``); S4's non-inferiority
route needs its oracle not worse than its chained, and says whether the served table shows the
oracle-worse-than-chained defect at all, both S4s' chained − oracle on the same rows (``s4_defect``;
where the served table shows none, the fix is not tested: a caveat on S4's row alone, the owner's
reading; ``s4_status``); S2 volume
is decided by the fallback's existence, the bake-off's head − fallback Δ log-MAE
per basin beside it (``vs_fallback``); S5 is read
from the set's own build where basin_swap exists (GEO_V1), else from the served
set's, with a caveat that it tests the rule on the served set's chain, and decides only for the
component it tests (link_zone_swap), as S4's row does for S4 v3; OUT reads T1 post ∪ T0
as one window (vs_served's OUT_WINDOW, on the unit-days both sets scored in either tier)
once T0 holds days, T1 alone while it is empty, and says which (``t0_words``). A stage
candidate's S2 T2 is labelled 'development (nested)' (``t2_label``): the A5 procedure's outer-fold rows. scores["promotion"]
states each §9 criterion as met | not met | not yet computable, with the reason.
It decides nothing: replacing the served set is the owner's action. A set tagged ``post_seen`` (protocol §2:
designed after post-training scores were seen; a stage candidate by stages_candidates.tag, a GEO_V1 candidate by
candidates.tag_candidate) has the tag on its post-training scores: windows["T1"], every pill's post-training words, the caption, and a caveat on each
primary decided on post-training days.

**Artifacts (``write``):** data/models/stages/<set>/manifest.json, scores.json,
and rows.csv.gz for the served set and stage candidates. Nothing else in
data/models/ is written. The manifest holds the sha256 of every input file and of every module
of this repository the build imported (``stale_files``): a change to either
makes the artifacts stale until the build is rerun (the served set first: a
candidate's vs_served reads its rows, so every other set's manifest pins the
served set's rows.csv.gz and scores.json too). s1_scores.json is pinned by its content
without built_at (``input_sha``), so rebuilding S1 on unchanged data stales
nothing. ``preview`` draws the scored figure (stages_flowchart.page) into an
HTML file that no route serves (default: the system temp directory, outside the repo).

    venv/bin/python features/forecast/src/models/stages_build.py --set served [--root served|candidates|stages_candidates]
        [--entries oracle,rain,L1] [--tiers T1,T2] [--steps s2,s3,s4,out,s5,scores] [--write] [--preview PATH]
"""
from __future__ import annotations

import argparse
import dataclasses
import functools
import gzip
import hashlib
import io
import json
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (REPO, FORECAST, HERE, HERE.parent / "collectors"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import candidates as CAND  # noqa: E402  (served.json, candidate manifests and stage 2 specs)
import compose_v2 as C  # noqa: E402
import exclusions as X  # noqa: E402
import grade_prospective as GP  # noqa: E402  (the committed forecast_history snapshot: the served set's T0 as served)
import samples as SMP  # noqa: E402  (the lab records S4's truth reads)
import stage2 as STG2  # noqa: E402  (read only: the served S3 split fitter)
import stage2_variants as SV  # noqa: E402  (read only: the rain sources the served split was fit on)
import stages_entries as E  # noqa: E402
import stages_flowchart as F  # noqa: E402
import stages_s1 as S1  # noqa: E402  (read only: the S1 rows, for the figure's post-training cells)
import stages_s2 as S2  # noqa: E402
import stages_s5 as S5  # noqa: E402
import stages_spec as SP  # noqa: E402
import train_v4 as T4  # noqa: E402  (read only: the training frames, impact-table fitter, events, samples)
import truth as T  # noqa: E402
import verify as V  # noqa: E402
from shared import clock  # noqa: E402
from shared import geography as G  # noqa: E402
from shared import lineup as LU  # noqa: E402  (a GEO_V1 set's S3 / S4 components from its stage 2 id)
from shared import risk_levels as RL  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

SCHEMA = "bwtf.stages/1"
SCORES_SCHEMA = "bwtf.stages.scores/1"
STAGES_DIR = FORECAST / "data" / "models" / "stages"            # the only place this module writes
S1_SCORES = STAGES_DIR / "_s1" / "s1_scores.json"
STAGE_ROOT = "stages_candidates"
ROOTS = S2.ROOTS + (STAGE_ROOT,)
COMPONENTS = ("s1", "s2", "s3", "s4", "s5")        # design §2.7: manifest components
S3B_BASIN = "westside"                             # protocol §8 S3b: the Westside split (its key in every geography)
TIERS = S2.ALL_TIERS                               # T1, T1-holdout, T2, then T0 (T1's finals after the freeze)
OUT_TIERS = ("T1", S2.T0)                          # protocol §8: OUT's window, T1 post ∪ T0 …
OUT_WINDOW = "T1 ∪ T0"                             # … scored as one window in vs_served (never a row's tier)
ENTRIES = X.ENTRIES                                # oracle, rain, L0 … L5, L0s, L1s
STEPS = ("s2", "s3", "s4", "out", "s5", "scores")
ROW_STAGES = ("s2", "s3", "s4", "out", "s5")
WARMUP_DAYS = S5.HISTORY_DAYS                      # 8: live_v2's 9-day window; compose needs 7
HISTORY = len(C.LAGS) - 1                          # 7: S4 / OUT read D−7 … D
B_PROTOCOL, SEED, LEVEL = 2000, 0, 0.9             # protocol §6
# The scored figure: each stage's pills show its powered window (protocol §2, §6): S1 its whole Previous Runs
# window, S2 → OUT the cross-season folds (nine seasons, each scored by weights that never saw it), S5 its protocol
# window. A set not refit per season shows the first of FIGURE_FALLBACK it has. Every pill's tooltip gives the
# post-training (T1) value beside the shown one; scores["figures"] keeps one figure per window.
FIGURE_WINDOWS = {"m.s2": "T2", "m.s3": "T2", "m.s4": "T2", "m.out": "T2", "m.s5": "S5"}
FIGURE_FALLBACK = ("T2", "T1-holdout", "T1")
POST_WINDOW = "T1"
S1_WINDOW = "previous_runs"
S1_FLOOR = "SF Oceanside as SF Downtown"           # the S1 oracle pill (either-wet MAE is the same both ways)
NODE_SCORES = {"m.s2": "s2", "m.s3": "s3", "m.s4": "s4", "m.out": "out", "m.s5": "s5"}
NODE_CODE = {"m.s2": "S2", "m.s3": "S3", "m.s4": "S4", "m.out": "OUT"}
CAPTION_WINDOW = {"T1-holdout": "the holdout", "T1": "post-training days only"}
S5_RULE_WORDS = {"basin_swap": "the served correction rule (basin_swap = live_v2)", "link_zone_swap": "link/zone injection"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
LADDER = ("truth_at_s4", "truth_at_s3", "rain", "L1")   # protocol §3: the OUT error budget
S5_WINDOW = ("T1-holdout", "T1", S2.T0)           # protocol §8: 2023-07-01 → the data end
EPS = 1e-12                                        # same-model predictions in different batch sizes (stages_s2)
FLOAT_FORMAT = "%.6g"                              # rows.csv.gz: 6 significant digits (the scores use full precision)


# ── the set ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SetBundle:
    """What a set scores with: its S2 components (stages_s2.S2Set), its stage 2 spec (None = v1, no split), the
    T1 S3 / S4 specs (its own artifacts), and the rain sources the served S3 / S4 fitters read. A stage candidate
    (``stage``) carries its own folds and specs instead: no stage 2 spec, no variant, no GEO_V1 fitter."""
    name: str
    root: str
    s2: S2.S2Set
    stage2: dict | None
    variant: str | None                            # 'v1' | 'v2'; None for a stage candidate
    t1_specs: dict | None                          # {"s3", "s4"}: the GEO_V1 adapter on the set's artifacts, or the stage set's T1
    chosen: dict                                   # basin name → rain source, as stage2_variants fit the split
    descriptor: dict
    stage: StageCandidate | None = None

    @property
    def geo(self) -> G.Geography:
        return self.s2.geo

    @property
    def is_served(self) -> bool:
        return self.root == "served"

    @property
    def post_seen(self) -> str | None:
        """Protocol §2's tag, or None: a stage candidate's, or a GEO_V1 candidate manifest's (candidates.tag_candidate)."""
        if self.stage is not None:
            return self.stage.post_seen
        return None if self.is_served else (self.descriptor.get("tags") or {}).get("post_seen")


def served_name() -> str:
    """The served set's name, from served.json (never typed)."""
    return CAND.served_info()["name"]


def resolve(set_name: str, root: str = "served") -> str:
    """'served' → the name served.json holds; any other name as given."""
    if set_name == "served":
        if root != "served":
            raise ValueError("--set served names the served set; it lives under --root served")
        return served_name()
    return set_name


def load_set(set_name: str, root: str = "served", stage_set=None) -> SetBundle:
    """Load the set and assert its stamps: stages_s2.load_set (geography, family, training end, rain sources,
    pickles), then the stage 2 spec the set composes with — the served bundle's stage2.json, which served.json's
    'stage2' must name, or a candidate's (candidates.load_stage2: none = v1), whose manifest must agree. Under
    root stages_candidates: ``load_stage_candidate`` (or ``stage_set``: a StageCandidate, or the StageSet
    stages_candidates.load_set returned)."""
    if root == STAGE_ROOT:
        if stage_set is None:
            st = load_stage_candidate(set_name)
        else:
            st = stage_set if isinstance(stage_set, StageCandidate) else from_saved(stage_set)
        if st.name != set_name:
            raise ValueError(f"the stage candidate is {st.name!r}, asked for {set_name!r}")
        return SetBundle(st.name, root, st.s2, None, None, st.specs.get(("T1", S2.FOLD_FINAL)), {}, st.manifest, st)
    if stage_set is not None:
        raise ValueError(f"a stage set is scored under --root {STAGE_ROOT}, not {root!r}")
    name = resolve(set_name, root)
    s2set = S2.load_set(name, root)                            # the stamps: geography (G.stamped), family, training end
    geo = s2set.geo
    if geo.version != "geo_v1":
        raise ValueError(f"{name} is a {geo.version} set: the GEO_V1 adapter scores existing sets only (P8 adds others)")
    raw_table = json.loads((T4.SERVE_DIR / "impact_table.json").read_text())
    if root == "served":
        desc = CAND.served_info()
        p = T4.SERVE_DIR / "stage2.json"
        stage2 = json.loads(p.read_text()) if p.exists() else None
        want = desc.get("stage2", "v1")
        specs = C.geo_v1_adapter_specs()                       # reads the served directory, stamps checked
    else:
        desc = json.loads((CAND.candidate_dir(name) / "manifest.json").read_text())
        stage2 = CAND.load_stage2(name)
        want = (desc.get("stage2") or {}).get("variant", "v1")
        specs = C.geo_v1_adapter_specs(stage2=stage2, impact_table=None if (stage2 or {}).get("impact_table") else raw_table)
    variant = CAND.stage2_variant_id(stage2)                   # v2_d10: v2, its linger table fit on the D10 samples
    if variant != want:
        raise ValueError(f"{name}: the descriptor says stage 2 {want!r}, the set's stage2.json is {variant!r}")
    if variant not in GEO_V1_VARIANTS:
        raise ValueError(f"{name}: stage 2 {variant!r} has no GEO_V1 adapter")
    if variant != "v1" and not stage2.get("impact_table"):
        raise ValueError(f"{name}: a stage 2 {variant} spec without its refit impact table")
    chosen = SV._served_chosen()
    missing = [b.name for b in geo.basins if b.name not in chosen]
    if missing:
        raise KeyError(f"no rain source for the S3 / S4 fit of {missing}")
    return SetBundle(name, root, s2set, stage2, variant, specs, chosen, desc)


GEO_V1_VARIANTS = ("v1", "v2", "v2_d10")          # the stage 2 ids a GEO_V1 set may record (candidates.stage2_variant_id)


def components(bundle: SetBundle) -> dict:
    """Design §2.7: the components behind each stage, by name (a stage candidate's from its manifest)."""
    if bundle.stage is not None:
        return {c: bundle.stage.manifest["components"][c] for c in COMPONENTS}
    return {"s1": E.served_weather_model(), "s2": bundle.s2.stage1, **LU.geo_v1_parts(bundle.s2.stage1, bundle.variant),
            "s5": S5.LR.VERSION}


# ── stage candidates (P8; Part B 13; design §7) ─────────────────────────────

# stages_s2_sfpuc4.run's arms in the bake-off's rows.csv.gz: the nested procedure's outer-fold rows are the
# candidate's T2 S2; the served recipe refit on the same labels is the S2 primary's other arm, per window
RECIPE_ARMS = {"T2": "served_recipe", "T1": "t1:served_recipe", "T1-holdout": "t1h:served_recipe"}
BAKEOFF_ROWS = ("arm", "contender", "date", "basin", "tier", "fold", "p", "v_hat", "y", "excl")
T2_ARM = "procedure"
SAVER_STAMPS = ("set", "component")                # what stages_candidates adds that compose_v2's checks do not know


@dataclass(frozen=True)
class StageS2Set(S2.S2Set):
    """stages_s2.S2Set for a stage candidate: an entry frame must hold every column that some fold's weights or
    head reads, not only the finals' (``parts``: the other model folds' (weights, heads))."""
    parts: tuple = ()

    def _all(self) -> tuple:
        return ((self.models, self.heads),) + tuple(self.parts)

    @property
    def sources(self) -> tuple:
        return tuple(sorted({d[k]["rain_source"] for w, h in self._all() for d in (w, h) for k in self.keys}))

    def features(self, source: str) -> list:
        cols = []
        for w, h in self._all():
            for k in self.keys:
                if w[k]["rain_source"] == source:
                    cols += list(w[k]["features"]) + ["rain_3d_cum"]
                if h[k]["rain_source"] == source:
                    cols += list(h[k]["features"])
        return list(dict.fromkeys(cols))


@dataclass(frozen=True)
class StageCandidate:
    """A stage candidate as the build scores it, from stages_candidates.load_set (``from_saved``):

        s2         the finals (T1) as a stages_s2.S2Set, keyed by the geography's basins
        folds      stages_s2.Fold per (tier, fold) the build composes: T1 'final' and T1-holdout 'pre_holdout'
                   from the saved models and heads; T2 '2016-17' … '2024-25' from the bake-off's outer-fold
                   rows (no weights: ``t2`` holds what they predicted), each with the days its parts may have seen;
                   T0 'final', T1's after the freeze (its specs and S3b benchmark T1's too)
        t2         the bake-off's T2 rows (date, basin, tier, fold, p, v_hat): rain known only, so a T2 fold has
                   no lead entry and no warm-up before its season's first day
        specs      (tier, fold) → {"s3", "s4"}, rebuilt from s3_links.json / s4_quality.json's per-fold records
        s3b        (tier, fold) → S3b's constant-share spec (stages_s3_links.fold_spec, arm 'constant')
        reference  the served recipe refit on the candidate's labels (date, basin, tier, fold, p, y): the S2
                   primary's other arm (protocol §8), from the same bake-off rows (y the labels the bake-off read)
        files      every file read (the candidate's and the bake-off's), for the manifest's sha256s
        heads_used (tier, fold) → {basin: the head's kind} (the declared fallback where a basin is under the floor),
                   every fold: T2's from the bake-off's record of each outer fold's head (``t2_head_records``)
        own_rows   the bake-off's rows of the candidate's own finals and siblings (date, basin, tier, fold, p): the
                   build's S2 must reproduce them, so the served-recipe arm saw the same inputs (Part B 16)
        s3_s2      the S2 whose v̂ (and Central / South p) the S3 shares and union rule were fit on, as s3_links.json
                   says (``s3_fit_s2``): a development stand-in, or a spec that does not say, is scored with a
                   caveat (Part B 2)
        head_records (tier, fold) → {basin: {kind, n_events}}: the heads Part B 7's floor rule was checked on
        s4_sizes   (tier, fold) → the link shares φ S4's size classes were fit at beside the S3 spec's
                   (``s4_fit_sizes``): a mismatch, or records that do not say, puts a caveat on S4 and OUT"""
    name: str
    manifest: dict
    s2: S2.S2Set
    folds: tuple
    t2: pd.DataFrame | None
    specs: dict
    s3b: dict
    reference: pd.DataFrame | None
    files: tuple = ()
    heads_used: dict = field(default_factory=dict)
    bakeoff: dict = field(default_factory=dict)
    own_rows: pd.DataFrame | None = None
    s3_s2: dict = field(default_factory=dict)      # which S2 the S3 shares were fit on (``s3_fit_s2``)
    head_records: dict = field(default_factory=dict)   # (tier, fold) → {basin: {kind, n_events}} (Part B 7)
    s4_sizes: dict = field(default_factory=dict)       # (tier, fold) → the φ S4 was fit at vs S3's (``s4_fit_sizes``)

    @property
    def geo(self) -> G.Geography:
        return self.s2.geo

    @property
    def nested(self) -> bool:
        """Whether its T2 choices were made inside the inner folds (protocol §2): the S2 section says so in the
        bake-off's own words (stages_s2_sfpuc4.T2_NESTED)."""
        import stages_s2_sfpuc4 as S2C  # noqa: PLC0415
        return (self.manifest.get("s2") or {}).get("t2") == S2C.T2_NESTED

    @property
    def post_seen(self) -> str | None:
        """Protocol §2's tag: why its post-training scores were seen before it was designed, or None (untagged)."""
        return (self.manifest.get("tags") or {}).get("post_seen")

    def is_rows_fold(self, fold: S2.Fold) -> bool:
        return not fold.weights


def _saved_spec(saved: dict) -> dict:
    """A spec file as stages_candidates saved it, its saver stamps (set, component) checked and dropped."""
    missing = [k for k in SAVER_STAMPS if k not in saved]
    if missing:
        raise KeyError(f"a saved spec carries the saver's stamps; this one lacks {missing}")
    return {k: v for k, v in saved.items() if k not in SAVER_STAMPS}


def _fold_records(spec: dict, what: str) -> dict:
    """(tier, fold) → the spec's per-fold record (its fit block's 'folds'), every key once."""
    recs = (spec.get("fit") or {}).get("folds")
    if not isinstance(recs, list) or not recs:
        raise KeyError(f"{what}: no per-fold records (fit.folds), so no fold's spec can be rebuilt (Part B 1)")
    out = {}
    for r in recs:
        key = (r["tier"], r["fold"])
        if key in out:
            raise ValueError(f"{what}: fold {key} recorded twice")
        out[key] = r
    return out


def s3_fold_specs(saved: dict, geo, keys) -> tuple:
    """({fold: S3 spec}, {fold: S3b's constant-share spec}) from a saved s3_links.json: stages_s3_links.fold_spec
    for each fold, its ``fit`` set from the record (train_seasons, share_fit_span) so check_fit_span can read it."""
    import stages_s3_links as S3L  # noqa: PLC0415  (P8b; read only when a stage candidate is scored)
    spec = _saved_spec(saved)
    recs = _fold_records(spec, "s3_links.json")
    out, bench = {}, {}
    for key in keys:
        if key not in recs:
            raise KeyError(f"s3_links.json has no record of fold {key}: refit S3 for it (Part B 1)")
        r = recs[key]
        fit = {"span": r.get("share_fit_span"), "seasons": r.get("train_seasons"), "tier": key[0], "fold": key[1]}
        out[key] = C.check_s3_spec({**S3L.fold_spec(spec, *key), "fit": fit}, geo)
        bench[key] = C.check_s3_spec({**S3L.fold_spec(spec, *key, arm="constant"), "fit": fit}, geo)
    return out, bench


def s4_fold_spec(saved: dict, key: tuple, geo) -> dict:
    """One fold's S4 spec from a saved s4_quality.json: the finals' spec with the fold's record (stages_s4_v3's
    _fold_record: background coefficients, buckets, zone medians, fit span and seasons) in place, checked."""
    spec = _saved_spec(saved)
    r = _fold_records(spec, "s4_quality.json").get(key)
    if r is None:
        raise KeyError(f"s4_quality.json has no record of fold {key}: refit S4 for it (Part B 1)")
    top = {k: v for k, v in spec.items() if k != "fit"}
    out = {**top, "background": {**top["background"], "coef": r["background"]}, "buckets": r["buckets"],
           "zone_median_mg": r["zone_median_mg"],
           "fit": {"span": r.get("fit_span"), "seasons": r.get("fit_seasons"), "tier": key[0], "fold": key[1]}}
    return C.check_s4_spec(out, geo)


def bakeoff_files(man: dict, name: str) -> tuple:
    """(results.json, rows.csv.gz) of the bake-off the candidate's S2 section names (s2.bakeoff)."""
    rel = man["s2"]["bakeoff"]
    if not rel:
        raise KeyError(f"{name}: the manifest's s2 section names no bake-off (s2.bakeoff), so T2 has no rows")
    p = Path(rel)
    p = p if p.is_absolute() else REPO / p
    rows = p.parent / "rows.csv.gz"
    for f in (p, rows):
        if not f.exists():
            raise FileNotFoundError(f"{name}: the bake-off file {f} is missing")
    return p, rows


def bakeoff_rows(path: Path, geo) -> pd.DataFrame:
    """The bake-off's rows.csv.gz (stages_s2_sfpuc4.write_results), its columns, basins and tiers checked."""
    r = pd.read_csv(path, parse_dates=["date"], keep_default_na=False, na_values=[""], low_memory=False)
    if list(r.columns) != list(BAKEOFF_ROWS):
        raise ValueError(f"{path.name}: columns {list(r.columns)}, not {list(BAKEOFF_ROWS)}")
    bad = set(r["basin"]) - set(geo.keys)
    if bad:
        raise KeyError(f"{path.name}: basins {sorted(bad)} are not {geo.version}'s")
    if set(r["tier"]) - set(TIERS):
        raise ValueError(f"{path.name}: tiers {sorted(set(r['tier']) - set(TIERS))}; known {TIERS}")
    return r


DEV_S2_PREFIX = "dev:"                             # a development stand-in S2 is named "dev:<set>"


def s3_fit_s2(saved_s3: dict) -> dict:
    """{name, stand_in}: the S2 an s3_links.json's shares and union rule were fit on (its sources.s2, as
    stages_s3_links writes it). Part B 2 fits shares on v̂ the way they are used, so a spec fit on the development
    stand-in ('dev:…') is scored but carries a caveat until the assemble step refits it on the candidate's own S2;
    a spec that does not say is reported as unknown (None)."""
    s2 = (saved_s3.get("sources") or {}).get("s2")
    if not isinstance(s2, dict) or not s2.get("name"):
        return {"name": None, "stand_in": None, "note": "s3_links.json does not say which S2 its shares were fit on"}
    return {"name": str(s2["name"]), "stand_in": str(s2["name"]).startswith(DEV_S2_PREFIX)}


PHI_TOL = 1e-9                                     # two specs' link shares φ as json floats


def s4_fit_sizes(saved_s4: dict, specs: dict) -> dict:
    """(tier, fold) → {s4, s3, match}: the link shares φ an s4_quality.json fold record says its size classes were
    fit at (fit.folds[].vol_share: compose_v2's zone size is the largest φ-sized link per feeding basin) beside the
    φ of the S3 spec the build composes that fold with. S4 fit = S4 use (Part B 6): a zone median and its buckets
    fit on sizes at one φ read wrongly at another. match None: the record does not say."""
    recs = _fold_records(_saved_spec(saved_s4), "s4_quality.json")
    out = {}
    for key, sp in specs.items():
        s3_phi = {lid: float(ln["vol_share"]) for lid, ln in sp["s3"]["links"].items()}
        rec_phi = (recs.get(key) or {}).get("vol_share")
        if not isinstance(rec_phi, dict):
            out[key] = {"s4": None, "s3": s3_phi, "match": None}
            continue
        s4_phi = {lid: float(v) for lid, v in rec_phi.items()}
        same = set(s4_phi) == set(s3_phi) and all(abs(s4_phi[k] - s3_phi[k]) <= PHI_TOL for k in s3_phi)
        out[key] = {"s4": s4_phi, "s3": s3_phi, "match": bool(same)}
    return out


def s4_size_caveat(sizes: dict) -> str | None:
    """The caveat S4's and OUT's primaries carry when S4's size classes were fit at other link shares than the S3
    spec sizes them with (``s4_fit_sizes``), or the S4 records do not say; None when every fold matches."""
    off = [f"{t} {fo}" for (t, fo), s in sorted(sizes.items()) if s["match"] is False]
    unknown = [f"{t} {fo}" for (t, fo), s in sorted(sizes.items()) if s["match"] is None]
    if off:
        t, fo = next(k for k, s in sorted(sizes.items()) if s["match"] is False)
        s = sizes[(t, fo)]
        diff = ", ".join(f"{lid} {s['s4'].get(lid)} vs {v:.3g}" for lid, v in s["s3"].items()
                         if s["s4"].get(lid) is None or abs(s["s4"][lid] - v) > PHI_TOL)
        return (f"S4's size classes were fit on zone sizes at other link shares than the S3 spec it composes with sizes "
                f"them at ({len(off)} of {len(sizes)} folds; {t} {fo}: φ {diff}): S4 fit ≠ S4 use (Part B 6), so this "
                "score, and OUT's, which reads the same table, is not the candidate's own until S4 is refit on its S3's sizes")
    if unknown:
        return (f"s4_quality.json does not say which link shares its size classes were fit at (fit.folds[].vol_share) in "
                f"{len(unknown)} of {len(sizes)} folds, so S4 fit = S4 use (Part B 6) is unchecked for this score")
    return None


def s3_fit_caveat(s3s2: dict, own_s2: str, cand_name: str) -> str | None:
    """The caveat S3's primaries carry from ``s3_fit_s2`` (Part B 2: shares fit on the v̂ they are used with): a
    development stand-in, a spec that does not say which S2 it was fit on (never read as the candidate's own), or
    one naming another S2 than the candidate's (its set name or its S2 component); None when it names the
    candidate's own."""
    name = s3s2.get("name")
    if s3s2.get("stand_in"):
        return (f"S3's shares and union rule were fit on the development stand-in S2 {name!r}, not the candidate's "
                f"own ({own_s2}): Part B 2 fits them on the v̂ they are used with, so this S3 score is not the candidate's "
                "own until they are refit on its S2")
    if name is None:
        return ("s3_links.json does not say which S2 its shares and union rule were fit on, so Part B 2 (fit on the v̂ they "
                f"are used with, the candidate's {own_s2}) is unchecked for this score")
    if name not in (cand_name, own_s2):
        return (f"S3's shares and union rule were fit on the S2 {name!r}, not the candidate's own ({cand_name}: {own_s2}): "
                "Part B 2 fits them on the v̂ they are used with")
    return None


def _check_rows(r: pd.DataFrame, what: str, geo, v_hat: bool, keep_y: bool = False) -> pd.DataFrame:
    """Out-of-fold predictions as a fold's S2: every (tier, basin, day) once, each day inside its fold's window
    (a T2 fold's own season), every basin on every day, p in [0, 1] (and v̂ ≥ 0, finite) — or it raises.
    ``keep_y``: the label the rows carry too (NaN where the bake-off left it unknown)."""
    r = r.copy()
    r["date"] = pd.DatetimeIndex(r["date"])
    if r.duplicated(["tier", "basin", "date"]).any():
        raise ValueError(f"{what}: a (tier, basin, day) appears twice")
    for (t, fo), g in r.groupby(["tier", "fold"], sort=False):
        start, end, season = fold_window(t, fo)
        d = pd.DatetimeIndex(g["date"])
        if (d < start).any() or (d > end).any():
            raise ValueError(f"{what}: {t} {fo} holds a day outside {start.date()} → {end.date()}")
        per = g.groupby("basin")["date"].apply(lambda x: frozenset(x))
        if set(per.index) != set(geo.keys) or len(set(per)) != 1:
            raise ValueError(f"{what}: {t} {fo} does not hold every basin on the same days")
    cols = ["p", "v_hat"] if v_hat else ["p"]
    vals = r[cols].to_numpy(dtype=float)
    if not np.isfinite(vals).all() or (r["p"] < 0).any() or (r["p"] > 1).any() or (v_hat and (r["v_hat"] < 0).any()):
        raise ValueError(f"{what}: a p outside [0, 1] or a missing / negative v̂")
    if keep_y:
        y = pd.to_numeric(r["y"], errors="raise").to_numpy(dtype=float)
        if not np.isin(y[np.isfinite(y)], (0.0, 1.0)).all():
            raise ValueError(f"{what}: a label that is neither 0, 1 nor unknown")
        cols = cols + ["y"]
    return r[["date", "basin", "tier", "fold"] + cols].reset_index(drop=True)


def _head_kind(h: dict) -> str:
    return str(h.get("kind", ""))


def t2_head_records(results: dict, fold: str, geo, name: str) -> dict:
    """{basin: {kind, n_events}} of a T2 outer fold's volume heads, as the bake-off records them (results.json's
    volume.per_basin[basin].recipes[picked].folds: the picked recipe's head fit without that season, whose v̂ the
    procedure's rows carry), so Part B 7's floor rule is checked on every scored fold, T2 included. A basin or fold
    the record lacks raises: the rule is never assumed to hold."""
    per = (results.get("volume") or {}).get("per_basin")
    if not isinstance(per, dict):
        raise KeyError(f"{name}: the bake-off's results.json has no volume.per_basin, so T2's volume heads are unchecked "
                       "(Part B 7)")
    out = {}
    for k in geo.keys:
        b = per.get(k) or {}
        rec = (((b.get("recipes") or {}).get(b.get("picked")) or {}).get("folds") or {}).get(fold)
        if not isinstance(rec, dict) or "kind" not in rec or "n_events" not in rec:
            raise KeyError(f"{name} T2 {fold} {k}: the bake-off records no head (kind, n_events) of its picked volume "
                           f"recipe {b.get('picked')!r} for this fold, so Part B 7's floor rule cannot be checked")
        out[k] = {"kind": rec["kind"], "n_events": rec["n_events"]}
    return out


def check_heads(name: str, key: tuple, heads: dict) -> dict:
    """{basin: head kind} after Part B 7's rule: a head under the 20-event floor must be the declared fallback (the
    bake-off's pooled Bay-side log-linear head); a basin under the floor without one fails the build. ``heads``:
    the fold's head dicts, or (T2) the bake-off's records of them (``t2_head_records``)."""
    import stages_s2_sfpuc4 as S2C  # noqa: PLC0415  (P8a: the declared fallback's kind)
    out = {}
    for k, h in heads.items():
        n = h.get("n_events")
        if n is None:
            raise KeyError(f"{name} {key} {k}: the head states no n_events")
        if int(n) < S2.HEAD_MIN_EVENTS and _head_kind(h) != S2C.FALLBACK_KIND:
            raise ValueError(f"{name} {key} {k}: {n} known-volume events, under the {S2.HEAD_MIN_EVENTS}-event floor, and "
                             f"its head is {_head_kind(h)!r}, not the declared fallback {S2C.FALLBACK_KIND!r}: the build "
                             "fails only if no declared fallback exists (Part B 7)")
        out[k] = _head_kind(h)
    return out


def from_saved(saved, seasons=None) -> StageCandidate:
    """A StageCandidate from stages_candidates.load_set's StageSet (its stamps, keys, sha256s and weights ≥ 0 are
    asserted there): S2 finals and holdout siblings as model folds, the T2 rows and the served-recipe arm from the
    bake-off its s2 section names, and every fold's S3 / S4 spec from the spec files' per-fold records, each fit
    span checked against its fold (check_fit_span); T0 is the finals' fold after the freeze, with T1's specs.
    ``seasons`` keeps those T2 seasons (development, tests)."""
    man = saved.manifest
    name = saved.name
    geo = G.stamped(man)
    missing = [c for c in COMPONENTS if c not in man["components"]]
    if missing or saved.s3_links is None or saved.s4_quality is None:
        raise FileNotFoundError(f"{name}: the candidate lacks components {missing or ''} "
                                f"{'s3_links ' if saved.s3_links is None else ''}{'s4_quality' if saved.s4_quality is None else ''}"
                                ": every stage must be saved before it is scored")
    s2sec = man["s2"]
    for k in ("trained_through", "holdout_start", "bakeoff"):
        if k not in s2sec:
            raise KeyError(f"{name}: the s2 section states no {k} (a missing stamp is never defaulted)")
    tt = pd.Timestamp(s2sec["trained_through"])
    if tt >= S2.POST_START:
        raise ValueError(f"{name}: trained through {tt.date()}; T1 starts {S2.POST_START.date()}")
    folds, used, records = [], {}, {}
    model_parts = [("T1", S2.FOLD_FINAL, saved.models, saved.volume)]
    if saved.holdout_models:
        model_parts.append(("T1-holdout", S2.FOLD_HOLDOUT, saved.holdout_models, saved.holdout_volume))
    for tier, fo, w, h in model_parts:
        start, end, season = fold_window(tier, fo)
        seen = fold_training_days(tier, season, tt)
        if tier == "T1-holdout" and pd.Timestamp(s2sec["holdout_start"]) != S2.HOLDOUT_START:
            raise ValueError(f"{name}: holdout siblings fit before {s2sec['holdout_start']}, not {S2.HOLDOUT_START.date()}")
        folds.append(S2.Fold(tier, fo, start, end, season, dict(w), dict(h), {k: seen for k in geo.keys},
                             {k: {"from": "the saved models and heads"} for k in geo.keys}))
        used[(tier, fo)] = check_heads(name, (tier, fo), h)
        records[(tier, fo)] = {k: {"kind": _head_kind(x), "n_events": x.get("n_events")} for k, x in h.items()}
    res_path, rows_path = bakeoff_files(man, name)
    results = json.loads(res_path.read_text())
    rows = bakeoff_rows(rows_path, geo)
    t2 = rows[(rows["arm"] == T2_ARM) & (rows["tier"] == "T2")]
    if seasons is not None:
        t2 = t2[t2["fold"].isin([S2.season_label(s) for s in seasons])]
    t2 = _check_rows(t2, f"{name} T2 ({T2_ARM})", geo, v_hat=True) if len(t2) else None
    if t2 is not None:
        for fo in pd.unique(t2["fold"]):
            start, end, season = fold_window("T2", fo)
            seen = fold_training_days("T2", season, tt)
            folds.append(S2.Fold("T2", fo, start, end, season, {}, {}, {k: seen for k in geo.keys},
                                 {k: {"from": f"the bake-off's {T2_ARM} rows ({_rel(res_path)})"} for k in geo.keys}))
            records[("T2", fo)] = t2_head_records(results, fo, geo, name)      # Part B 7 on the outer folds' heads
            used[("T2", fo)] = check_heads(name, ("T2", fo), records[("T2", fo)])
    ref = rows[rows["arm"].isin(set(RECIPE_ARMS.values()))]
    ref = ref[[RECIPE_ARMS[t] == a for t, a in zip(ref["tier"], ref["arm"])]]
    reference = _check_rows(ref, f"{name} served recipe", geo, v_hat=False, keep_y=True) if len(ref) else None
    keys = [(f.tier, f.fold) for f in folds]
    s3, s3b = s3_fold_specs(saved.s3_links, geo, keys)
    specs = {k: {"s3": s3[k], "s4": s4_fold_spec(saved.s4_quality, k, geo)} for k in keys}
    sizes = s4_fit_sizes(saved.s4_quality, specs)
    # T0 (protocol §2): T1's fold on every day after the freeze, its finals, heads and specs (no fit of its own)
    t1, t0 = ("T1", S2.FOLD_FINAL), (S2.T0, S2.FOLD_FINAL)
    start, end, _ = fold_window(*t0)
    folds.append(dataclasses.replace(folds[0], tier=S2.T0, start=start, end=end))
    specs[t0], s3b[t0] = specs[t1], s3b[t1]
    parts = tuple((f.weights, f.heads) for f in folds if f.weights and f.fold != S2.FOLD_FINAL)
    fams = {saved.models[k]["family"] for k in geo.keys}
    if len(fams) != 1:
        raise ValueError(f"{name}: its basin models are of families {sorted(fams)}")
    s2 = StageS2Set(name, STAGE_ROOT, fams.pop(), man["components"]["s2"], geo, tt,
                    dict(saved.models), dict(saved.volume), {}, {}, parts=parts)
    files = tuple(sorted(p for p in Path(saved.path).iterdir() if p.is_file())) + (res_path, rows_path)
    win = results["winner"]["contender"]                    # the bake-off's own rows of the winner's finals / siblings
    own = rows[rows["arm"].isin({f"t1:{win}", f"t1h:{win}"})]
    own = _check_rows(own, f"{name} {win}'s own rows", geo, v_hat=False) if len(own) else None
    cand = StageCandidate(name, man, s2, tuple(folds), t2, specs, s3b, reference, files, used,
                          {"results": _rel(res_path), "rows": _rel(rows_path), "volume": results.get("volume"),
                           "south_floor": results.get("south_floor"), "winner": results.get("winner"),
                           "picked_by_fold": (results.get("nested") or {}).get("picked_by_fold")}, own,
                          s3_fit_s2(saved.s3_links), records, sizes)
    for f in cand.folds:                                    # every fold's specs fit on days it never scores
        for st in ("s3", "s4"):
            check_fit_span(cand.specs[(f.tier, f.fold)][st], st, f, name)
    return cand


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def load_stage_candidate(name: str, root=None, seasons=None) -> StageCandidate:
    """The stage candidate ``name`` through stages_candidates.load_set (Part B 13's geography-aware loader: every
    stamp, key and sha256 asserted, every weight ≥ 0), then ``from_saved``. ``root`` overrides its directory."""
    return from_saved(saved_loader()(name, root=root), seasons=seasons)


def saved_loader():
    """stages_candidates.load_set, imported only when a stage candidate is scored."""
    import stages_candidates as SC  # noqa: PLC0415
    return SC.load_set


def fold_window(tier: str, fold: str) -> tuple:
    """(first scored day, last scored day, held-out season) of a protocol §2 fold (stages_s2._plan's), or KeyError."""
    for t, f, start, end, season, _ in S2._plan(TIERS):
        if (t, f) == (tier, fold):
            return start, end, season
    raise KeyError(f"({tier!r}, {fold!r}) is not a protocol §2 fold: T1 final, T1-holdout pre_holdout, T2 2016-17 … 2024-25, "
                   "T0 final")


def fold_training_days(tier: str, season: int | None, trained_through) -> pd.DatetimeIndex:
    """The days a fold's fitted parts may have seen, protocol §2's refit rule: T1 the record through the training
    end, T1-holdout the days before 2023-07-01, a T2 fold every day through the training end outside its season."""
    if tier == "T1":
        return pd.date_range(T.TRUTH_START, pd.Timestamp(trained_through))
    if tier == "T1-holdout":
        return pd.date_range(T.TRUTH_START, S2.HOLDOUT_START - pd.Timedelta(days=1))
    if tier == "T2":
        d = pd.date_range(T.TRUTH_START, pd.Timestamp(trained_through))
        return d[T4.wet_season(pd.Series(d)).to_numpy() != season]
    raise KeyError(f"no training days for tier {tier!r}")


def check_fit_span(spec: dict, stage: str, fold: S2.Fold, name: str) -> dict:
    """Raise unless a fold's spec states the days it was fit on and they hold none of the fold's own (Part B 1):
    ``fit.span`` [first, last] ending before a T1 / T1-holdout window; ``fit.seasons`` (July years) without the
    held-out season for T2. Returns {span, seasons} as stated."""
    fit = spec.get("fit")
    if not isinstance(fit, dict) or (fit.get("span") is None and fit.get("seasons") is None):
        raise ValueError(f"{name} {fold.tier} {fold.fold} {stage}: the spec states no fit span (fit.span / fit.seasons), "
                         "so nothing proves it never saw the fold's days")
    span, seasons = fit.get("span"), fit.get("seasons")
    if fold.tier == "T2":
        if seasons is None:
            raise ValueError(f"{name} {fold.tier} {fold.fold} {stage}: a T2 spec states the seasons it was fit on (fit.seasons)")
        if fold.season in {int(s) for s in seasons}:
            raise ValueError(f"X-ALL-INSAMPLE: {name} {fold.fold} {stage} was fit on its held-out season {fold.fold}")
    elif span is None or pd.Timestamp(span[1]) >= fold.start:
        raise ValueError(f"X-ALL-INSAMPLE: {name} {fold.tier} {stage} was fit through {span and span[1]}, inside its window "
                         f"(from {fold.start.date()})")
    return {"span": list(span) if span is not None else None, "seasons": sorted(int(s) for s in seasons) if seasons else None}


def stage_fitted(cand: StageCandidate, tiers) -> S2.Fitted:
    """stages_s2.Fitted from a stage candidate's model folds (nothing is refit), so its predict path, out-of-fold
    check and X-SEL are stages_s2's; T2 is the bake-off's rows (``t2_pred``), not a model here."""
    folds = tuple(f for f in cand.folds if f.tier in tiers and not cand.is_rows_fold(f))
    stamp = {"schema": S2.SCHEMA, "stage": "s2", "set": cand.name, "root": STAGE_ROOT, "geography": cand.geo.version,
             "basins": list(cand.geo.keys), "protocol": X.protocol_stamp(),
             "trained_through": str(cand.s2.trained_through.date()), "tiers": list(tiers),
             "folds": [{"tier": f.tier, "fold": f.fold} for f in folds]}
    return S2.Fitted(cand.s2, tuple(t for t in tiers if t != "T2"), folds, stamp)


def t2_pred(cand: StageCandidate, folds: list) -> pd.DataFrame:
    """The bake-off's T2 rows as stages_s2 prediction rows (entry 'oracle': rain known) on the folds kept."""
    if cand.t2 is None:
        return pd.DataFrame(columns=list(S2.COLUMNS))
    keep = {f.fold for f in folds if f.tier == "T2"}
    r = cand.t2[cand.t2["fold"].isin(keep)]
    out = pd.DataFrame({"date": r["date"], "basin": r["basin"], "entry": "oracle", "tier": "T2", "fold": r["fold"],
                        "sel": X.selection(r["date"], cand.geo), "p": r["p"].astype(float), "v_hat": r["v_hat"].astype(float)})
    return out[list(S2.COLUMNS)].reset_index(drop=True)


FIDELITY_TOL = 1e-6                                # rows.csv.gz's 6 significant digits on a probability ≤ 1


def bakeoff_fidelity(cand: StageCandidate, pred: pd.DataFrame) -> dict:
    """The build's rain-known S2 against the bake-off's own rows of the candidate's finals and siblings, on the days
    both hold: equal to the file's 6 significant digits, or the two read different inputs and the served-recipe arm
    is no paired arm (Part B 16), which raises. {n, max_abs_diff}. A model-fold tier the build scores and the
    served-recipe arm holds rows of, with no own row to vouch for the inputs on a day both hold, raises too: the
    pairing would be unproven (None only when the recipe arm holds no such tier)."""
    mine = pred[(pred["entry"] == "oracle") & pred["tier"].isin(["T1", "T1-holdout"])]
    ref_tiers = set(cand.reference["tier"]) if cand.reference is not None else set()
    paired_tiers = sorted(set(mine["tier"]) & ref_tiers)
    if cand.own_rows is None:
        if paired_tiers:
            raise AssertionError(f"{cand.name}: the bake-off holds served-recipe rows on {paired_tiers} but no row of the "
                                 "candidate's own fits, so nothing shows both arms read the same inputs (Part B 16)")
        return {"n": 0, "max_abs_diff": None, "note": "the bake-off holds no rows of the candidate's own fits"}
    a = mine.set_index(["tier", "fold", "basin", "date"])["p"]
    b = cand.own_rows.set_index(["tier", "fold", "basin", "date"])["p"]
    common = a.index.intersection(b.index)
    unproven = [t for t in paired_tiers if t not in set(common.get_level_values("tier"))]
    if unproven:
        raise AssertionError(f"{cand.name}: no day of {unproven} holds both the build's S2 and the bake-off's own rows of "
                             "the candidate, so the served-recipe arm there is not shown to be paired (Part B 16)")
    if not len(common):
        return {"n": 0, "max_abs_diff": None, "note": "no day both hold"}
    gap = float(np.abs(_as_written(a.loc[common].to_numpy(dtype=float)) - b.loc[common].to_numpy(dtype=float)).max())
    if gap > FIDELITY_TOL:
        raise AssertionError(f"{cand.name}: the build's S2 differs from the bake-off's own rows by {gap:.1e}: the two read "
                             "different inputs, so the served-recipe arm is not paired (Part B 16)")
    return {"n": int(len(common)), "max_abs_diff": gap}


def runs_from_rows(fold: S2.Fold, pred: pd.DataFrame, entry: str, rain: pd.Series | None, keys) -> list:
    """A rows fold's runs (consecutive days of its window that the rows hold): S2 p and v̂ read from the rows, so
    no warm-up before the window's first day (S4 / OUT start a week in; S5 eight days in). Columns: ``keys``."""
    g = pred[(pred["tier"] == fold.tier) & (pred["fold"] == fold.fold) & (pred["entry"] == entry)]
    p = g.pivot(index="date", columns="basin", values="p")[list(keys)].astype(float).rename_axis(index=None, columns=None)
    v = g.pivot(index="date", columns="basin", values="v_hat")[list(keys)].astype(float).rename_axis(index=None, columns=None)
    out = []
    for r in _runs(pd.DatetimeIndex(sorted(p.index))):
        out.append(Run(entry, fold.tier, fold.fold, r, r, p.loc[r], v.loc[r],
                       None if rain is None else rain.reindex(r).astype(float)))
    return out


# ── S3 / S4 fitted per fold (Part B 1) ──────────────────────────────────────

def fit_s3_s4(frames: dict, chosen: dict, heads: dict, samples: pd.DataFrame, events: pd.DataFrame, variant: str) -> dict:
    """The served fitters on the frames, events and heads given (a fold's, or the full training window):
    {'raw': the impact table on every discharge day (impact_table.json's recipe), 'stage2': stage 2 v2's
    shares with the impact table refit on the days each GEO_V1 link's own outfalls discharged (stage2.json's
    recipe; None for v1), 'table': the table S4 composes}. Exactly stage2_variants.fit's calls, without its file writes and with no clock
    stamp (fit_outfall_split's fitted_at is dropped). ``heads`` are keyed by basin name. v2_d10 is v2 with
    ``samples`` the D10 set (the caller passes stage2_variants.impact_samples'), its spec stamped as stage2_variants
    stamps it."""
    if variant not in GEO_V1_VARIANTS:
        raise ValueError(f"stage 2 {variant!r}: {' | '.join(GEO_V1_VARIANTS)}")
    raw, _ = T4.fit_impact_table(frames, chosen, heads, samples)
    if variant == "v1":
        return {"raw": raw, "stage2": None, "table": raw}
    spec = STG2.fit_outfall_split(frames, chosen, heads, raw, events)
    table, _ = T4.fit_impact_table(frames, chosen, heads, samples, event_days=STG2.group_event_days(frames, chosen, events))
    spec.pop("fitted_at", None)
    spec["impact_table"] = table
    if variant in SV.SAMPLE_VARIANTS:
        spec["impact_samples"] = SV.SAMPLE_VARIANTS[variant]
    return {"raw": raw, "stage2": spec, "table": table}


def fold_specs(bundle: SetBundle, fold: S2.Fold, keep, train: dict, events: pd.DataFrame, samples: pd.DataFrame) -> tuple:
    """({"s3", "s4"}, info) for one S2 fold. T1 (keep None): the set's own artifacts. A refit fold: the served
    fitters on the fold's training days only (``keep``, stages_s2's own filter), with the fold's volume heads. A
    stage candidate: its own specs for the fold (from_saved), their fit spans checked (``check_fit_span``)."""
    if bundle.stage is not None:
        sp = bundle.stage.specs[(fold.tier, fold.fold)]
        fit = {st: check_fit_span(sp[st], st, fold, bundle.name) for st in ("s3", "s4")}
        spans = [f["span"] for f in fit.values() if f["span"]]
        return sp, {"fit": "the candidate's own specs for this fold", "fit_span": _union(spans),
                    "seasons": sorted({s for f in fit.values() for s in (f["seasons"] or ())}) or None,
                    "spec_fit": fit, "scored_overlap": 0}
    if keep is None:
        return bundle.t1_specs, {"fit": "the set's artifacts, fit through 2025-10-31", "fit_span": ["2016-03-01", "2025-10-31"]}
    fr = {s: f[keep(f)].reset_index(drop=True) for s, f in train.items()}
    fit_days = pd.DatetimeIndex(next(iter(fr.values()))["date"])
    if any(not pd.DatetimeIndex(f["date"]).equals(fit_days) for f in fr.values()):
        raise AssertionError("the training frames' sources hold different days")
    ev = events[pd.to_datetime(events["event_date"]).dt.normalize().isin(fit_days)]
    heads = {bundle.geo.basin(k).name: h for k, h in fold.heads.items()}
    fit = fit_s3_s4(fr, bundle.chosen, heads, samples, ev, bundle.variant)
    specs = C.geo_v1_adapter_specs(stage2=fit["stage2"], impact_table=None if fit["stage2"] else fit["table"])
    scored = pd.date_range(fold.start, min(fold.end, E.data_end()))
    overlap = int(fit_days.isin(scored).sum())
    if overlap:
        raise AssertionError(f"{fold.tier} {fold.fold}: the S3 / S4 fit holds {overlap} of its scored days")
    return specs, {"fit": "refit on the fold's training days", "fit_span": [str(fit_days.min().date()), str(fit_days.max().date())],
                   "n_fit_days": int(len(fit_days)), "n_events": int(len(ev)), "seasons": sorted(int(s) for s in fr[next(iter(fr))]["season"].unique()),
                   "scored_overlap": overlap}


def identity_zones(geo: G.Geography, s3_spec: dict) -> dict:
    """{zone: True when the S3 oracle there is exact by construction}: every basin feeding the zone reaches only
    that zone, and one of its links into it has a share that is 1 for every size (X-S3-ID read off the spec)."""
    out = {}
    for z in ZONES:
        ok = True
        for b in T.feeding_basins(geo, z):
            links = geo.links_from(b)
            ok &= all(lk.zone == z for lk in links) and any(C._share_is_one(s3_spec["links"][lk.id]["share"]) for lk in links)
        out[z] = bool(ok)
    return out


# ── truth inputs for the oracle entries ─────────────────────────────────────

@dataclass(frozen=True)
class Truths:
    """The ledger truths over the context span, wide, for the oracle inputs (unknown = NaN)."""
    basin_y: pd.DataFrame        # date × basin
    basin_vol: pd.DataFrame      # date × basin: Σ measured volume (NaN: unknown, or no measured volume)
    link_y: pd.DataFrame         # date × link
    link_vol: pd.DataFrame


def truths(geo: G.Geography, end) -> Truths:
    bo = T.basin_onsets(geo, end=end)
    lo = T.link_onsets(geo, end=end)
    wide = lambda f, unit, col: f.pivot(index="date", columns=unit, values=col).astype(float)  # noqa: E731
    return Truths(wide(bo, "basin", "y")[list(geo.keys)], wide(bo, "basin", "volume_mg")[list(geo.keys)],
                  wide(lo, "link", "y")[[lk.id for lk in geo.links]], wide(lo, "link", "volume_mg")[[lk.id for lk in geo.links]])


def true_history(geo: G.Geography, specs: dict, tr: Truths, days: pd.DatetimeIndex, v_hat: pd.DataFrame) -> tuple:
    """(compose_v2.History at the S4 spec's unit, unknown mask) — the S4 / OUT oracle input: the true overflow
    history (0/1) and its true size, by compose_v2's size rule read on the ledger. S3 sizes a link ℓ of basin b
    as φ_ℓ · v̂_b, so its true size is what that estimates: with φ_ℓ = 1 (the GEO_V1 adapter: a link's size is
    its basin's, which the impact table's size classes were fit on) the basin's measured volume (truth.basin_onsets:
    Σ over the events of all its links, the link_onsets volumes together); with a fitted φ_ℓ the link's own
    truth.link_onsets volume. A zone (zone units) takes Σ_b max_ℓ, as
    compose_v2.s3. An overflow with no measured volume takes the predicted size (fit_impact_table's fill); a day
    the ledger does not know is 0 and marked in the mask."""
    s3 = specs["s3"]
    ly, lv = tr.link_y.reindex(days), tr.link_vol.reindex(days)
    unknown = ly.isna()
    size = {}
    for lk in geo.links:
        phi = float(s3["links"][lk.id]["vol_share"])
        if phi == 1.0:
            true_v = tr.basin_vol[lk.basin].reindex(days)
        else:
            true_v = lv[lk.id]
        fired = ly[lk.id].eq(1)
        guess = phi * v_hat[lk.basin].reindex(days)
        size[lk.id] = np.where(fired, true_v.fillna(guess), 0.0)
    P = ly.fillna(0.0)
    Vz = pd.DataFrame(size, index=days)
    if specs["s4"]["unit"] == "link":
        return C.History(P, Vz), unknown
    zp, zv, zu = {}, {}, {}
    for z in ZONES:
        into = geo.links_into(z)
        zp[z] = P[[lk.id for lk in into]].max(axis=1)
        zu[z] = unknown[[lk.id for lk in into]].any(axis=1)
        zv[z] = sum(Vz[[lk.id for lk in into if lk.basin == b]].max(axis=1) for b in dict.fromkeys(lk.basin for lk in into))
    return C.History(pd.DataFrame(zp), pd.DataFrame(zv)), pd.DataFrame(zu)


# ── S2 runs ─────────────────────────────────────────────────────────────────

def _runs(days: pd.DatetimeIndex) -> list:
    """Consecutive-day runs of a sorted index."""
    if not len(days):
        return []
    br = np.flatnonzero(np.diff(days.values) != np.timedelta64(1, "D")) + 1
    return [days[a:b] for a, b in zip(np.r_[0, br], np.r_[br, len(days)])]


def _s2_values(s2set: S2.S2Set, fold: S2.Fold, ef: dict, days: pd.DatetimeIndex) -> tuple:
    """(p, v̂) date × basin from the fold's own models on `days` (stages_s2's predict path, warm-up days included)."""
    p, v = {}, {}
    for key in s2set.keys:
        m, h = fold.weights[key], fold.heads[key]
        Xp, Xv = ef[m["rain_source"]].loc[days], ef[h["rain_source"]].loc[days]
        for Z, cols in ((Xp, m["features"] + ["rain_3d_cum"]), (Xv, h["features"])):
            if not np.isfinite(Z[cols].to_numpy()).all():
                raise ValueError(f"{fold.tier} {fold.fold} {key}: a missing input between {days[0].date()} and {days[-1].date()}")
        p[key] = T4.calibrated(m, Xp)
        v[key] = T4.predicted_volume(h, Xv)
    return pd.DataFrame(p, index=days)[list(s2set.keys)], pd.DataFrame(v, index=days)[list(s2set.keys)]


@dataclass
class Run:
    """One composed run of an (entry, tier, fold): its days, which are scored, and S2's values on all of them."""
    entry: str
    tier: str
    fold: str
    days: pd.DatetimeIndex
    window: pd.DatetimeIndex     # the days of the run inside the fold's window (emitted)
    p: pd.DataFrame
    v: pd.DataFrame
    rain: pd.Series | None


def runs_for(s2set: S2.S2Set, fold: S2.Fold, entry: str, frames: dict, window: pd.DatetimeIndex) -> list:
    """The runs of one (entry, fold): the fold's window days the entry holds, led by up to WARMUP_DAYS days
    straight before the first of them (from the fold's own models, never emitted); a missing day breaks a run."""
    ef = S2._entry_frames(s2set, entry, frames)
    avail = next(iter(ef.values())).index
    rain = frames["avg"].set_index("date")["precip_avg"] if "avg" in frames else None
    pre = pd.date_range(window[0] - pd.Timedelta(days=WARMUP_DAYS), window[0] - pd.Timedelta(days=1))
    held = pre.isin(avail)
    k = len(pre) if held.all() else len(pre) - 1 - int(np.flatnonzero(~held)[-1])   # the consecutive tail held
    days = pre[len(pre) - k:].append(window)
    out = []
    for r in _runs(days):
        p, v = _s2_values(s2set, fold, ef, r)
        out.append(Run(entry, fold.tier, fold.fold, r, r[r.isin(window)], p, v,
                       None if rain is None else rain.reindex(r).astype(float)))
    return out


# ── a lead entry's issue-day chain (protocol §3) ────────────────────────────

SYNTH_START = pd.Timestamp("2000-01-01")   # issue_inputs' stacked windows: compose_v2 reads row offsets, never these dates


def issue_entries(entry: str) -> tuple:
    """The entries the issue day of a lead entry reads, by day offset j = d − I (0 … L): L0 … LL, as served
    L0s … LLs (stages_entries' frame for target d at lead j is the forecast issued on d − j). Rain known
    (no lead) reads itself only."""
    L = X._lead_of(entry)
    if L is None:
        return (entry,)
    s = "s" if entry.endswith("s") else ""
    out = tuple(f"L{j}{s}" for j in range(L + 1))
    bad = [e for e in out if e not in ENTRIES]
    if bad:
        raise KeyError(f"{entry}: its issue day reads {bad}, which are not entries ({ENTRIES})")
    return out


def issue_inputs(s2set: S2.S2Set, fold: S2.Fold, entry: str, frames: dict, base: Run, targets: pd.DatetimeIndex) -> tuple:
    """(stacked compose_v2.BasinInputs or None, the targets kept, {entry: (days, p)} for the checks) for the
    S4 / OUT rows of lead entry ``entry`` on ``targets``. Target D's window D−7…D, with issue day I = D − L:
    rain known (``base``, the fold's rain-known run) on d < I, entry L_j's S2 output (the fold's own models
    on that entry's frames) on d = I + j. Window i is rows 8i … 8i+7 of one consecutive synthetic index, so
    compose_v2's lags on row 8i+7 read exactly its own 8 rows; S3 (same-day) is unchanged by the stacking.
    A target is kept only when every L_j holds its day I + j; a rain-known day missing from ``base`` raises."""
    L = X._lead_of(entry)
    if L is None:
        raise ValueError(f"{entry!r} has no issue day: rain known reads one run")
    ents = issue_entries(entry)
    efs, ok = {}, np.ones(len(targets), bool)
    for j, e in enumerate(ents):
        if e not in frames:
            raise KeyError(f"{entry}: its issue day reads entry {e}, whose frames were not built")
        efs[e] = S2._entry_frames(s2set, e, frames[e])
        ok &= (targets - pd.Timedelta(days=L - j)).isin(next(iter(efs[e].values())).index)
    kept = targets[ok]
    if not len(kept):
        return None, kept, {}
    keys = list(s2set.keys)
    n_lag = HISTORY + 1
    P = np.empty((len(kept), n_lag, len(keys)))
    Vv = np.empty_like(P)
    used = {}
    for k in range(n_lag):                                     # k = D − d
        days = kept - pd.Timedelta(days=k)
        if k > L:                                              # before the issue day: the gauges (rain known)
            if not days.isin(base.days).all():
                raise AssertionError(f"{entry} {fold.tier} {fold.fold}: rain known lacks {days[~days.isin(base.days)][0].date()}")
            p, v = base.p.loc[days, keys], base.v.loc[days, keys]
        else:                                                  # the issue day's forecast for d = I + (L − k)
            p, v = _s2_values(s2set, fold, efs[ents[L - k]], days)
            used.setdefault(ents[L - k], []).append(p)
        P[:, HISTORY - k], Vv[:, HISTORY - k] = p.to_numpy(dtype=float), v.to_numpy(dtype=float)
    idx = pd.date_range(SYNTH_START, periods=n_lag * len(kept))
    inputs = C.BasinInputs(pd.DataFrame(P.reshape(-1, len(keys)), idx, keys), pd.DataFrame(Vv.reshape(-1, len(keys)), idx, keys))
    return inputs, kept, {e: pd.concat(ps) for e, ps in used.items()}


ISSUE_RAIN = ("rain_lag2d", "rain_lag1d", "precip_avg")   # an entry frame's D−2, D−1, D: the issue day's composite rain


def issue_background(spec: dict, units, avg: pd.DataFrame, kept: pd.DatetimeIndex) -> pd.DataFrame:
    """kept × unit: an S4 rain background b(D) as the issue day knew its rain D−2…D — the entry frame's
    (``avg``: the two-gauge source, stages_entries) precip_avg and lags on D, which hold the gauges before I and
    the forecast from I on — through compose_v2.background on D alone (its own rain3, hinges and season flag)."""
    a = avg.set_index("date") if "date" in avg.columns else avg
    vals = a.reindex(kept)[list(ISSUE_RAIN)]
    if vals.isna().any().any():
        raise ValueError(f"the entry frame lacks its rain on {vals.index[vals.isna().any(axis=1)][0].date()}")
    out = np.empty((len(kept), len(units)))
    for i, (D, row) in enumerate(zip(kept, vals.to_numpy(dtype=float))):
        rain = pd.Series(row, index=pd.date_range(D - pd.Timedelta(days=2), D))
        out[i] = C.background(spec, units, pd.DatetimeIndex([D]), rain)[0]
    return pd.DataFrame(out, index=kept, columns=list(units))


def issue_levels(geo, specs: dict, inputs: C.BasinInputs, kept: pd.DatetimeIndex, avg: pd.DataFrame | None = None) -> tuple:
    """(S4 q_z, OUT r_z) date × zone on the kept targets: compose_v2 on the stacked windows, each window's last
    row. The stacking reads no date, so a constant background (the GEO_V1 adapter's) composes as is. A rain
    background (zone_v3) is applied after: q = 1 − (1 − b(D))·(1 − q₀(D)), q₀ the stacked composition with no
    background (lingering's own algebra: b enters at D only), b(D) from the issue day's rain (``issue_background``
    on the entry frame ``avg``). OUT reads no background (A1)."""
    s4 = specs["s4"]
    last = slice(HISTORY, None, HISTORY + 1)
    if s4["background"]["kind"] == "constant":
        comp = C.compose(geo, specs, inputs)
        return (comp.s4.zone.iloc[last].set_axis(kept, axis=0), comp.out.zone.iloc[last].set_axis(kept, axis=0))
    if s4["unit"] != "zone" or avg is None:
        raise ValueError("a rain background on the stacked issue-day windows needs zone units and the entry's own "
                         "rain (issue_background)")
    units = C.s4_units(geo, s4)
    bare = {"s3": specs["s3"], "s4": {**s4, "background": {"kind": "constant", "p": {u: 0.0 for u in units}}}}
    comp = C.compose(geo, bare, inputs)
    q0 = comp.s4.zone.iloc[last].set_axis(kept, axis=0)
    b = issue_background(s4, units, avg, kept)[list(q0.columns)]
    return 1.0 - (1.0 - b) * (1.0 - q0), comp.out.zone.iloc[last].set_axis(kept, axis=0)


# ── rows ────────────────────────────────────────────────────────────────────

def _rows(stage: str, unit_type: str, frame: pd.DataFrame, days: pd.DatetimeIndex, entry: str, tier: str, fold: str,
          y: pd.Series, extra: dict | None = None) -> pd.DataFrame:
    """exclusions.COLUMNS rows for a date × unit frame of p on `days` (plus a 'fold' column kept in memory)."""
    lead = X._lead_of(entry)
    parts = []
    for u in frame.columns:
        key = pd.MultiIndex.from_arrays([[u] * len(days), days])
        d = {"date": days, "stage": stage, "unit_type": unit_type, "unit": u, "entry": entry,
             "lead": np.nan if lead is None else float(lead), "tier": tier, "sel": "",
             "p": frame.loc[days, u].to_numpy(dtype=float), "q": np.nan, "b": np.nan, "v_hat": np.nan,
             "y": y.reindex(key).to_numpy(dtype=float), "y2": np.nan, "n_sampled": np.nan,
             "stratum": "", "excl": "", "tags": "", "variant": "", "fold": fold}
        for c, f in (extra or {}).items():
            d[c] = (f.loc[days, u] if isinstance(f, pd.DataFrame) else f.reindex(key)).to_numpy(dtype=float)
        if not np.isfinite(d["p"]).all():
            raise ValueError(f"{stage} {u} {entry} {tier} {fold}: a forecast is not a finite number")
        parts.append(pd.DataFrame(d))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(X.COLUMNS) + ["fold"])


def s2_rows(pred: pd.DataFrame, ctx: X.Context, basin_vol: pd.DataFrame) -> pd.DataFrame:
    """S2 rows from stages_s2's out-of-fold predictions: y = the basin onset (truth.basin_onsets), y2 = its
    measured volume (MG; the volume table's truth), v_hat = S2's size."""
    pred = pred[pred["entry"] != "rain"]                       # rain known = the S2 oracle (one input)
    key = pd.MultiIndex.from_arrays([pred["basin"], pd.DatetimeIndex(pred["date"])])
    vol = basin_vol.stack(future_stack=True)
    vol.index = vol.index.swaplevel()
    out = pd.DataFrame({
        "date": pd.DatetimeIndex(pred["date"]), "stage": "s2", "unit_type": "basin", "unit": pred["basin"].to_numpy(),
        "entry": pred["entry"].to_numpy(), "lead": [np.nan if X._lead_of(e) is None else float(X._lead_of(e)) for e in pred["entry"]],
        "tier": pred["tier"].to_numpy(), "sel": "", "p": pred["p"].to_numpy(dtype=float), "q": np.nan, "b": np.nan,
        "v_hat": pred["v_hat"].to_numpy(dtype=float), "y": ctx.frames["basin"]["y"].reindex(key).to_numpy(dtype=float),
        "y2": vol.reindex(key).to_numpy(dtype=float), "n_sampled": np.nan, "stratum": "", "excl": "", "tags": "",
        "variant": "", "fold": pred["fold"].to_numpy()})
    if not (np.isfinite(out["p"]).all() and np.isfinite(out["v_hat"]).all()):
        raise ValueError("S2: a p or v̂ is not a finite number")
    return out


@dataclass
class Composed:
    """Everything the compositions emit: rows per stage, the truth-at-S3 rung, the oracle-unknown masks."""
    rows: dict = field(default_factory=lambda: {s: [] for s in ("s3", "s4", "out")})
    rung: list = field(default_factory=list)                   # OUT from the S3 oracle (ladder only)
    unknown: list = field(default_factory=list)                # (stage, zone, date) an oracle row could read as 0
    dropped: dict = field(default_factory=dict)                # (entry, tier) → window days with no S4 / OUT row
    bench: list = field(default_factory=list)                  # S3b: the S3 oracle under a constant share (paired only)


def _full_history(r: Run) -> pd.DatetimeIndex:
    """The run's emitted days that hold the whole D−7…D history inside the run."""
    return r.window[r.window.isin(r.days[HISTORY:])]


def compose_run(geo, specs: dict, r: Run, ctx: X.Context, tr: Truths, n_sampled: pd.Series, out: Composed,
                steps: tuple, oracle: bool, history: bool = True, rain: pd.Series | None = None,
                bench: dict | None = None) -> None:
    """S3, S4 and OUT rows of one run. ``oracle``: the run is the rain-known S2 output used as the oracle
    entry's v̂ (S3: true occurrence; S4 / OUT: the true history). ``history`` False: S3 rows only (a lead
    entry, whose S4 / OUT read the issue day's chain: ``compose_issue``). ``rain``: the two-gauge daily rain
    an S4 rain background reads (rain known; the GEO_V1 adapter's constant background reads none). ``bench``:
    S3b's constant-share spec, composed on the oracle input beside the set's own (``Composed.bench``)."""
    z = ctx.frames["zone"]
    full = _full_history(r)
    if not history:
        steps = tuple(s for s in steps if s == "s3")
    else:
        out.dropped[(r.entry, r.tier)] = out.dropped.get((r.entry, r.tier), 0) + int(len(r.window) - len(full))
    if oracle:
        yb = tr.basin_y.reindex(r.days)[list(geo.keys)]
        unknown_b = yb.isna()
        comp = C.compose(geo, specs, C.BasinInputs(r.p, r.v, rain).oracle(yb.fillna(0.0)))
        if bench is not None and "s3" in steps:
            b3 = C.s3(geo, bench, yb.fillna(0.0), r.v)
            out.bench.append(_rows("s3", "zone", b3.zone_p, r.window, "oracle", r.tier, r.fold, z["y"]))
        hist, unknown_h = true_history(geo, specs, tr, r.days, r.v)
        s4 = C.s4(geo, specs["s4"], hist, rain)
        o = C.out(geo, specs["s4"], hist)
        zq, zr = s4.zone, o.zone
        # which zone-days read an unknown day: S3 the day itself, S4 / OUT the history D−7…D
        ub = pd.DataFrame({zk: unknown_b[list(T.feeding_basins(geo, zk))].any(axis=1) for zk in ZONES})
        uh = unknown_h if list(unknown_h.columns) == list(ZONES) else pd.DataFrame(
            {zk: unknown_h[[lk.id for lk in geo.links_into(zk)]].any(axis=1) for zk in ZONES})
        uh7 = uh.astype(int).rolling(HISTORY + 1, min_periods=1).max().astype(bool)
        for zk in ZONES:
            out.unknown += [("s3", zk, d) for d in r.window[ub.loc[r.window, zk].to_numpy()]]
            out.unknown += [(s, zk, d) for s in ("s4", "out") for d in full[uh7.loc[full, zk].to_numpy()]]
        out.rung.append(_rows("out", "zone", comp.out.zone, full, "oracle", r.tier, r.fold, z["out_y"]))
        entry = "oracle"
    else:
        comp = C.compose(geo, specs, C.BasinInputs(r.p, r.v, rain))
        zq, zr = comp.s4.zone, comp.out.zone
        entry = r.entry
    if "s3" in steps:
        out.rows["s3"].append(_rows("s3", "zone", comp.s3.zone_p, r.window, entry, r.tier, r.fold, z["y"],
                                    {"v_hat": comp.s3.zone_v}))
    if "s4" in steps:
        out.rows["s4"].append(_rows("s4", "zone", zq, full, entry, r.tier, r.fold, z["s4_y"],
                                    {"q": zq, "n_sampled": n_sampled}))
    if "out" in steps:
        out.rows["out"].append(_rows("out", "zone", zr, full, entry, r.tier, r.fold, z["out_y"]))


def compose_issue(geo, specs: dict, s2set: S2.S2Set, fold: S2.Fold, entry: str, frames: dict, base: Run,
                  targets: pd.DatetimeIndex, ctx: X.Context, n_sampled: pd.Series, out: Composed, steps: tuple,
                  pred_p: dict) -> float:
    """S4 and OUT rows of a lead entry on its window's targets, each from its issue day's chain
    (``issue_inputs``). Returns the largest |p − stages_s2's p| of the S2 outputs it read inside the fold's
    window (``pred_p``: entry → date × basin), which must be 0 up to EPS."""
    z = ctx.frames["zone"]
    inputs, kept, used = issue_inputs(s2set, fold, entry, frames, base, targets)
    out.dropped[(entry, fold.tier)] = out.dropped.get((entry, fold.tier), 0) + int(len(targets) - len(kept))
    gap = 0.0
    for e, p in used.items():
        ref = pred_p.get(e)
        if ref is None:
            continue
        on = p.index.isin(ref.index)
        if on.any():
            gap = max(gap, float(np.abs(p[on].to_numpy() - ref.loc[p.index[on], p.columns].to_numpy()).max()))
    if not len(kept):
        return gap
    zq, zr = issue_levels(geo, specs, inputs, kept, frames[entry].get("avg"))
    if "s4" in steps:
        out.rows["s4"].append(_rows("s4", "zone", zq, kept, entry, fold.tier, fold.fold, z["s4_y"],
                                    {"q": zq, "n_sampled": n_sampled}))
    if "out" in steps:
        out.rows["out"].append(_rows("out", "zone", zr, kept, entry, fold.tier, fold.fold, z["out_y"]))
    return gap


# ── exclusions ──────────────────────────────────────────────────────────────

def tier_context(ctx: X.Context, geo, specs_by_fold: dict, folds: list) -> X.Context:
    """The context with the zone ``identity`` fact (X-S3-ID) read off each fold's S3 spec on that fold's days."""
    zf = ctx.frames["zone"].copy()
    ident = zf["identity"].to_numpy(dtype=bool).copy()
    units = zf.index.get_level_values(0).to_numpy()
    dates = zf.index.get_level_values(1)
    for f in folds:
        iz = identity_zones(geo, specs_by_fold[(f.tier, f.fold)]["s3"])
        on = np.asarray((dates >= f.start) & (dates <= f.end))
        for zk, v in iz.items():
            ident[on & (units == zk)] = v
    zf["identity"] = ident
    return dataclasses.replace(ctx, frames={**ctx.frames, "zone": zf})


def apply_stage(rows: pd.DataFrame, stage: str, ctx_by_tier: dict) -> pd.DataFrame:
    """exclusions.apply per tier (every row of a window in one call, so X-POWER counts the whole window)."""
    parts = [X.apply(g, stage, ctx_by_tier[t]) for t, g in rows.groupby("tier", sort=False)]
    return pd.concat(parts, ignore_index=True) if parts else rows


# ── S5 ──────────────────────────────────────────────────────────────────────

def s5_blocks(feed: pd.DataFrame, dates) -> np.ndarray:
    """Protocol §6 S5 blocks, observation events: the feed's observation days merged while the next one falls
    within the 7-day window after the last; a row's block is the event whose [first day, last day + 7] holds it."""
    obs = pd.DatetimeIndex(sorted(set(pd.to_datetime(feed["date"]).dt.normalize())))
    d = pd.DatetimeIndex(pd.to_datetime(dates))
    if not len(obs):
        return np.full(len(d), -1)
    ev = np.r_[0, np.cumsum(np.diff(obs.values) > np.timedelta64(X.OBS_DAYS, "D"))]
    pos = np.searchsorted(obs.values, d.values.astype(obs.values.dtype), side="right") - 1
    blk = np.where(pos >= 0, ev[np.clip(pos, 0, None)], -1)
    last = pd.Series(obs).groupby(ev).max().to_numpy()
    reach = np.where(pos >= 0, last[np.clip(blk, 0, None)] + np.timedelta64(X.OBS_DAYS, "D"), np.datetime64("NaT"))
    return np.where((pos >= 0) & (d.to_numpy() <= reach), blk, -1)


# ── climatology references (protocol §4.2) ──────────────────────────────────

def _pool(stage: str, ctx: X.Context, geo, entry: str) -> pd.DataFrame:
    """The truth's scored unit-days over the whole context (no forecast needed): the rows a climatology is fit on.
    Built through exclusions.apply on a skeleton, so the reference reads exactly the days a score would."""
    units = list(geo.keys) if stage == "s2" else list(ZONES)
    sk = X.skeleton(stage, units, ctx.start, ctx.end, entry=entry, tier="T2")
    col = {"s2": "y", "s3": "y", "s4": "s4_y", "out": "out_y"}[stage]
    ut = "basin" if stage == "s2" else "zone"
    sk["y"] = ctx.frames[ut][col].reindex(pd.MultiIndex.from_arrays([sk["unit"], sk["date"]])).to_numpy(dtype=float)
    r = X.apply(sk, stage, ctx)
    r = r[r["excl"] == ""]
    return pd.DataFrame({"unit": r["unit"].to_numpy(), "date": pd.DatetimeIndex(r["date"]), "y": r["y"].to_numpy(dtype=float)})


def fit_days(tier: str, season: int | None) -> tuple:
    """(first, last, held-out season) of the days a window's reference is fit on (protocol §4.2): T1 and T0 from
    all T2 seasons; T1-holdout from the days before 2023-07-01; a T2 season from the other T2 seasons."""
    if tier in ("T1", "T0"):
        return pd.Timestamp(S2.T2_SEASONS[0], 7, 1), pd.Timestamp(S2.T2_SEASONS[-1] + 1, 6, 30), None
    if tier == "T1-holdout":
        return T.TRUTH_START, S2.HOLDOUT_START - pd.Timedelta(days=1), None
    if tier == "T2":
        return pd.Timestamp(S2.T2_SEASONS[0], 7, 1), pd.Timestamp(S2.T2_SEASONS[-1] + 1, 6, 30), season
    raise ValueError(f"no reference for tier {tier!r}")


def references(rows: pd.DataFrame, pool: pd.DataFrame) -> np.ndarray:
    """Per row: the climatology (unit × month ±1) of the pool's days in the row's fold's training span."""
    out = np.full(len(rows), np.nan)
    season = T4.wet_season(pd.Series(pd.DatetimeIndex(rows["date"]))).to_numpy()
    tiers = rows["tier"].to_numpy()
    pool_season = T4.wet_season(pool["date"]).to_numpy()
    for t in pd.unique(tiers):
        for s in (pd.unique(season[tiers == t]) if t == "T2" else [None]):
            m = (tiers == t) & ((season == s) if s is not None else True)
            lo, hi, held = fit_days(t, s)
            pm = np.array((pool["date"] >= lo) & (pool["date"] <= hi), dtype=bool)
            if held is not None:
                pm &= pool_season != held
            tr = pool[pm]
            out[m] = V.climatology_ref(tr["y"], (tr["unit"], tr["date"]), (rows["unit"][m], pd.DatetimeIndex(rows["date"][m])))
    return out


# ── scores ──────────────────────────────────────────────────────────────────

def _storm_blocks(dates, blocks: pd.DataFrame) -> tuple:
    """(block id, is a storm block) per date from truth.blocks (protocol §6). A date with no block raises: NaN
    ids would be resampled as one block, silently."""
    b = blocks.reindex(pd.DatetimeIndex(dates))
    if b["block"].isna().any():
        raise ValueError(f"no storm / quiet block on {pd.DatetimeIndex(dates)[b['block'].isna().to_numpy()][0].date()}: "
                         "truth.blocks must cover every scored day")
    return b["block"].to_numpy(dtype=float), (b["block_kind"] == "storm").to_numpy()


def _span(dates) -> dict:
    """{span: [first, last] ISO days, n_seasons: July–June seasons among them} of a cell's rows."""
    d = pd.DatetimeIndex(pd.to_datetime(dates))
    if not len(d):
        return {"span": None, "n_seasons": 0}
    return {"span": [str(d.min().date()), str(d.max().date())],
            "n_seasons": int(len(np.unique(np.where(d.month >= 7, d.year, d.year - 1))))}


def bundle(y, p, ref, blk, storm, strata, n_boot: int, dates=None) -> dict:
    """One unit × window: verify.scores_bundle plus the sample-climatology BSS, the MDE of a paired Brier
    difference on these rows (2.49 × the block SE of forecast − reference), the storm-block count and X-POWER;
    with ``dates``, the rows' span and seasons (``_span``: the figure's tooltips say which days a score is on)."""
    y, p, ref = (np.asarray(a, dtype=float) for a in (y, p, ref))
    out = V.scores_bundle(y, p, ref, blk, edges=RL.edges(), n=n_boot, seed=SEED, level=LEVEL)
    n_storm = int(len(np.unique(np.asarray(blk)[np.asarray(storm, bool)]))) if len(y) else 0
    out["bss_sample"] = V.clean(V.bss(y, p, V.sample_ref(y, strata))) if len(y) else None
    out["mde"] = V.clean(V.mde(V.paired_se(y, p, ref, blk)))
    out["n_storm_blocks"] = n_storm
    out["low_power"] = bool(out["n_pos"] < X.POWER_MIN_POSITIVES or n_storm < X.POWER_MIN_STORM_BLOCKS)
    if dates is not None:
        out.update(_span(dates))
    return out


def _delta(y, a, b, blk, n_boot: int) -> dict:
    """verify.paired_delta (Δ = BS(a) − BS(b), arm b the reference) plus protocol §8's non-inferiority at +5%."""
    raw = V.paired_delta(y, a, b, blk, n=n_boot, seed=SEED, level=LEVEL)
    d = V.clean(raw)
    d["noninferior_5pct"] = bool(V.noninferior(raw)) if raw["n"] else None
    return d


def _windows(rows: pd.DataFrame) -> list:
    return [t for t in TIERS if (rows["tier"] == t).any()]


def score_stage(rows: pd.DataFrame, stage: str, blocks: pd.DataFrame, n_boot: int) -> dict:
    """{unit | 'pooled': {entry: {window: bundle}}} on the scored rows (excl == '')."""
    sc = rows[rows["excl"] == ""]
    out: dict = {}
    for t in _windows(sc):
        for e, g in sc[sc["tier"] == t].groupby("entry", sort=False):
            blk, storm = _storm_blocks(g["date"], blocks)
            for u, gu in [("pooled", g)] + list(g.groupby("unit", sort=False)):
                m = (g["unit"] == u).to_numpy() if u != "pooled" else np.ones(len(g), bool)
                out.setdefault(u, {}).setdefault(e, {})[t] = bundle(gu["y"], gu["p"], gu["ref"], blk[m], storm[m],
                                                                    gu["unit"].to_numpy(), n_boot, gu["date"])
    return out


def score_on_rows(rows: pd.DataFrame, base: str, blocks: pd.DataFrame, n_boot: int) -> dict:
    """{unit | 'pooled': {entry: {window: bundle}}}: every other entry scored on ``base``'s scored rows only, the
    unit-days both scored, against the reference ``base`` is scored with there, so the two skills are paired and
    comparable (S3: chained on the oracle's Westside overflow days, where the oracle is scored)."""
    sc = rows[rows["excl"] == ""]
    out: dict = {}
    for t in _windows(sc):
        st = sc[sc["tier"] == t]
        on = st[st["entry"] == base].set_index(["unit", "date"])
        if not len(on):
            continue
        for e, g in st[st["entry"] != base].groupby("entry", sort=False):
            mine = g.set_index(["unit", "date"])
            common = on.index.intersection(mine.index)
            if not len(common):
                continue
            mine, theirs = mine.loc[common], on.loc[common]
            y = mine["y"].to_numpy(dtype=float)
            if not np.array_equal(y, theirs["y"].to_numpy(dtype=float)):
                raise AssertionError(f"{e} on {base}'s rows {t}: the same unit-days carry different truths")
            p, ref = mine["p"].to_numpy(dtype=float), theirs["ref"].to_numpy(dtype=float)
            units = common.get_level_values("unit").to_numpy()
            dates = common.get_level_values("date")
            blk, storm = _storm_blocks(dates, blocks)
            for u in ["pooled"] + list(pd.unique(units)):
                m = np.ones(len(common), bool) if u == "pooled" else units == u
                out.setdefault(u, {}).setdefault(e, {})[t] = bundle(y[m], p[m], ref[m], blk[m], storm[m], units[m], n_boot, dates[m])
    return out


def _paired(rows: pd.DataFrame, a: str, b: str, blocks: pd.DataFrame, n_boot: int, key=("unit", "date")) -> dict:
    """{unit | 'pooled': {window: Δ = BS(a) − BS(b)}} on the intersection of both entries' scored rows."""
    sc = rows[rows["excl"] == ""]
    out: dict = {}
    for t in _windows(sc):
        st = sc[sc["tier"] == t]
        A = st[st["entry"] == a].set_index(list(key))
        Bf = st[st["entry"] == b].set_index(list(key))
        common = A.index.intersection(Bf.index)
        if not len(common):
            continue
        A, Bf = A.loc[common], Bf.loc[common]
        if not np.array_equal(A["y"].to_numpy(), Bf["y"].to_numpy()):
            raise AssertionError(f"{a} vs {b} {t}: the same unit-days carry different truths")
        units = common.get_level_values("unit").to_numpy()
        dates = common.get_level_values("date")
        blk, _ = _storm_blocks(dates, blocks)
        for u in ["pooled"] + list(pd.unique(units)):
            m = np.ones(len(common), bool) if u == "pooled" else units == u
            out.setdefault(u, {})[t] = _delta(A["y"].to_numpy()[m], A["p"].to_numpy()[m], Bf["p"].to_numpy()[m], blk[m], n_boot)
    return out


def score_volume(s2: pd.DataFrame, ctx: X.Context, ctx_by_tier: dict, basin_vol: pd.DataFrame, blocks: pd.DataFrame,
                 n_boot: int) -> dict:
    """S2's size on event days (protocol §4.3, descriptive): {basin | pooled: {entry: {window: {n, log_mae, ci,
    size_class_acc, median_mg}}}}. Rows: S2's scored rows on true event days, through exclusions' volume table
    (X-S2-VOLQ: no measured volume); log_mae = mean |log1p v̂ − log1p v| (MG); a size class is large when the
    volume reaches the basin's median measured volume on the fold's training days."""
    ev = s2[(s2["excl"] == "") & (s2["y"] == 1)]
    if not len(ev):
        return {}
    ev = pd.concat([X.apply(g, "s2", ctx_by_tier[t], table="volume") for t, g in ev.groupby("tier", sort=False)], ignore_index=True)
    ev = ev[ev["excl"] == ""]
    geo = ctx.geo
    sk = X.skeleton("s2", geo.keys, ctx.start, ctx.end, entry="oracle", tier="T2")
    key = pd.MultiIndex.from_arrays([sk["unit"], sk["date"]])
    sk["y"] = ctx.frames["basin"]["y"].reindex(key).to_numpy(dtype=float)
    sk = sk[sk["y"] == 1].reset_index(drop=True)
    pool = X.apply(sk, "s2", ctx, table="volume")
    pool = pool[pool["excl"] == ""]
    vol = basin_vol.stack(future_stack=True)
    vol.index = vol.index.swaplevel()
    pool_v = vol.reindex(pd.MultiIndex.from_arrays([pool["unit"], pd.DatetimeIndex(pool["date"])])).to_numpy(dtype=float)
    pool = pd.DataFrame({"unit": pool["unit"].to_numpy(), "date": pd.DatetimeIndex(pool["date"]), "v": pool_v}).dropna()
    season = T4.wet_season(pd.Series(pd.DatetimeIndex(ev["date"]))).to_numpy()
    med = np.full(len(ev), np.nan)
    pool_season = T4.wet_season(pool["date"]).to_numpy()
    for t in pd.unique(ev["tier"]):
        for s in (pd.unique(season[ev["tier"] == t]) if t == "T2" else [None]):
            m = (ev["tier"] == t).to_numpy() & ((season == s) if s is not None else True)
            lo, hi, held = fit_days(t, s)
            pm = np.array((pool["date"] >= lo) & (pool["date"] <= hi), dtype=bool) & (pool_season != held if held is not None else True)
            meds = pool[pm].groupby("unit")["v"].median()
            med[m] = ev["unit"][m].map(meds).to_numpy(dtype=float)
    ev = ev.assign(_med=med)
    if ev["_med"].isna().any():
        raise ValueError("S2 volume: a basin has no measured volume on a fold's training days")
    out: dict = {}
    for (e, t), g in ev.groupby(["entry", "tier"], sort=False):
        blk, _ = _storm_blocks(g["date"], blocks)
        for u in ["pooled"] + list(pd.unique(g["unit"])):
            m = np.ones(len(g), bool) if u == "pooled" else (g["unit"] == u).to_numpy()
            a, b = np.log1p(g["v_hat"].to_numpy(dtype=float)[m]), np.log1p(g["y2"].to_numpy(dtype=float)[m])
            est, lo, hi = V.block_bootstrap(lambda x, z: np.mean(np.abs(x - z)), (a, b), blk[m], n=n_boot, seed=SEED, level=LEVEL)
            md = g["_med"].to_numpy(dtype=float)[m]
            out.setdefault(u, {}).setdefault(e, {})[t] = V.clean({
                "n": int(m.sum()), "log_mae": est, "ci": [lo, hi],
                "size_class_acc": float(np.mean((g["v_hat"].to_numpy(dtype=float)[m] >= md) == (g["y2"].to_numpy(dtype=float)[m] >= md))),
                "median_mg": float(md[0]) if u != "pooled" else None})
    return out


def ladder(out_rows: pd.DataFrame, rung: pd.DataFrame, blocks: pd.DataFrame, n_boot: int) -> dict:
    """The OUT error budget (protocol §3): truth-at-S4 → truth-at-S3 → rain known → lead 1 on the unit-days every
    rung scored; each drop is the Brier the stage between two rungs passes downstream."""
    sc = out_rows[out_rows["excl"] == ""]
    arms = {"truth_at_s4": sc[sc["entry"] == "oracle"], "rain": sc[sc["entry"] == "rain"], "L1": sc[sc["entry"] == "L1"]}
    res: dict = {}
    for t in _windows(sc):
        sub = {k: v[v["tier"] == t].set_index(["unit", "date"]) for k, v in arms.items()}
        r3 = rung[rung["tier"] == t].set_index(["unit", "date"])
        sub["truth_at_s3"] = r3
        if any(not len(v) for v in sub.values()):
            continue
        common = sub["truth_at_s4"].index
        for k in LADDER[1:]:
            common = common.intersection(sub[k].index)
        # truth-at-S3 rows also need the history known on D−7…D (an overflow day reads earlier days)
        common = common[sub["truth_at_s3"].loc[common, "hist_known"].to_numpy(dtype=bool)]
        if not len(common):
            continue
        y = sub["truth_at_s4"].loc[common, "y"].to_numpy(dtype=float)
        blk, _ = _storm_blocks(common.get_level_values("date"), blocks)
        units = common.get_level_values("unit").to_numpy()
        for u in ["pooled"] + list(pd.unique(units)):
            m = np.ones(len(common), bool) if u == "pooled" else units == u
            ps = {k: sub[k].loc[common, "p"].to_numpy(dtype=float)[m] for k in LADDER}
            cell = {"n": int(m.sum()), "n_pos": int((y[m] == 1).sum()),
                    "bs": {k: V.clean(V.brier(y[m], ps[k])) for k in LADDER}, "drops": {}}
            for a, b in zip(LADDER[1:], LADDER[:-1]):
                cell["drops"][f"{a} − {b}"] = _delta(y[m], ps[a], ps[b], blk[m], n_boot)
            res.setdefault(u, {})[t] = cell
    return res


def score_s5(rows: pd.DataFrame, feeds: dict, blocks: pd.DataFrame, n_boot: int) -> tuple:
    """(s5 scores {unit | pooled: {feed: {window: {variant: bundle + delta_vs_plain}}}}, the §8 primary
    {feed: {window: {unit | pooled: link_zone_swap − basin_swap}}}). Windows: each tier, and 'S5' = T1-holdout ∪ T1
    ∪ T0. The bootstrap resamples observation events (protocol §6's S5 blocks); X-POWER (§6) counts the rain storm
    blocks the cell's rows touch (truth.blocks, ``blocks``), recorded as n_storm_blocks."""
    sc = rows[rows["excl"] == ""].copy()
    if not len(sc):
        return {}, {}
    sc["window"] = sc["tier"]
    s5win = sc[sc["tier"].isin(S5_WINDOW)].assign(window="S5")
    sc = pd.concat([sc, s5win], ignore_index=True)
    out, prim = {}, {}
    for (feed, w), g in sc.groupby(["entry", "window"], sort=False):
        g = g.assign(_blk=s5_blocks(feeds[feed], g["date"]))
        if (g["_blk"] < 0).any():
            raise AssertionError(f"S5 {feed} {w}: a conditional row lies in no observation event")
        by_v = {v: gv.set_index(["unit", "date"]) for v, gv in g.groupby("variant", sort=False)}
        plain = by_v["plain"]
        for v, gv in by_v.items():
            if not gv.index.equals(plain.index):
                raise AssertionError(f"S5 {feed} {w}: variant {v} is scored on other zone-days than plain")
        units = plain.index.get_level_values("unit").to_numpy()
        blk = plain["_blk"].to_numpy()
        storm_id, in_storm = _storm_blocks(plain.index.get_level_values("date"), blocks)
        for u in ["pooled"] + list(pd.unique(units)):
            m = np.ones(len(plain), bool) if u == "pooled" else units == u
            y = plain["y"].to_numpy(dtype=float)[m]
            n_storm = int(len(np.unique(storm_id[m & in_storm])))
            for v, gv in by_v.items():
                cell = bundle(y, gv["p"].to_numpy(dtype=float)[m], gv["ref"].to_numpy(dtype=float)[m], blk[m],
                              np.zeros(int(m.sum()), bool), units[m], n_boot, plain.index.get_level_values("date")[m])
                cell["n_storm_blocks"] = n_storm               # the rows' rain storms: blk holds observation events
                cell["low_power"] = bool(cell["n_pos"] < X.POWER_MIN_POSITIVES or n_storm < X.POWER_MIN_STORM_BLOCKS)
                cell["delta_vs_plain"] = _delta(y, gv["p"].to_numpy(dtype=float)[m], gv["b"].to_numpy(dtype=float)[m], blk[m], n_boot)
                out.setdefault(u, {}).setdefault(feed, {}).setdefault(w, {})[v] = cell
            if {"link_zone_swap", "basin_swap"} <= set(by_v):
                a, b = (by_v[k]["p"].to_numpy(dtype=float)[m] for k in S5.PRIMARY)
                prim.setdefault(feed, {}).setdefault(w, {})[u] = _delta(y, a, b, blk[m], n_boot)
    return out, prim


def t0_as_served(served: str, ctx: X.Context, pool: pd.DataFrame, blocks: pd.DataFrame, n_boot: int, end,
                 path: Path = GP.SNAPSHOT) -> dict:
    """scores['t0_as_served']: the served set's T0 as the public saw it (protocol §2: graded from forecast_history
    rows whose stamp names it, read from the committed snapshot; Part B 20, 28). grade_prospective.rows → the rows
    whose model is ``served`` and whose target day is on or before the data end → OUT rows by lead (entry L0 … L5,
    tier T0; ``_rows``, zone by zone, day by day) → OUT's own scorer: exclusions.apply on ``ctx`` (the T0 context),
    the reference (``references`` on OUT's pool: all T2 seasons) and ``score_stage``, so {unit | pooled: {L0 … L5:
    {T0: bundle}}}. Never one arm of a paired comparison: its inputs are the page's, not the shadow-run's (Part B 16).
    counts: issue days in the snapshot; the served set's rows graded, and waiting for truth (a target after the data
    end); rows under another model; the served set's rows by live-correction rule."""
    snap = GP.rows(path)
    if snap is None:
        return {"state": "no snapshot", "words": f"No forecast_history snapshot is committed yet ({_rel(path)}; "
                "grade_prospective.py --export writes it): nothing the page showed in the live season is graded."}
    end = pd.Timestamp(end)
    mine = snap[snap["model"] == served]
    due = mine[mine["target_date"] <= end]
    out = {"state": "graded" if len(due) else "waiting", "snapshot": _rel(path),
           "counts": {"issue_days": int(snap["issue_date"].nunique()), "graded": int(len(due)),
                      "waiting": int(len(mine) - len(due)), "other_model": int(len(snap) - len(mine)),
                      "corrections": {str(k): int(n) for k, n in mine["corrections"].value_counts().items()}}}
    if not len(due):
        out["words"] = (f"The snapshot's {len(mine)} forecasts by {served} target days after the data end ({end.date()}): "
                        "graded once a data refresh covers them.")
        return out
    bad = set(due["zone"]) - set(ZONES)
    if bad:
        raise KeyError(f"{_rel(path)}: zones {sorted(bad)} are not shared/zones.py's")
    parts = []
    for lead in sorted(due["lead"].unique()):
        for zk in ZONES:
            g = due[(due["lead"] == lead) & (due["zone"] == zk)].sort_values("target_date")
            if len(g):
                days = pd.DatetimeIndex(g["target_date"])
                parts.append(_rows("out", "zone", pd.DataFrame({zk: g["p"].to_numpy(dtype=float)}, index=days), days,
                                   f"L{lead}", S2.T0, S2.FOLD_FINAL, ctx.frames["zone"]["out_y"]))
    rows = X.apply(pd.concat(parts, ignore_index=True), "out", ctx)
    rows["ref"] = references(rows, pool)
    out["partition"] = X.counts(rows)["partition"]["out"]
    out["out"] = score_stage(rows, "out", blocks, n_boot)
    return out


# ── the figure ──────────────────────────────────────────────────────────────

def _month(d) -> str:
    d = pd.Timestamp(d)
    return f"{MONTHS[d.month - 1]} {d.year}"


def span_words(span) -> str:
    """'Jul 2016 – Jun 2025' for [first, last] ISO days ('' for none)."""
    return f"{_month(span[0])} – {_month(span[1])}" if span else ""


def _union(spans) -> list | None:
    spans = [s for s in spans if s]
    return [min(s[0] for s in spans), max(s[1] for s in spans)] if spans else None


T2_NESTED_LABEL = "development (nested)"


def t2_label(stage: str, geo, nested: bool | None = None) -> str:
    """Protocol §2's label for a cross-season (T2) score: 'development'; an existing (GEO_V1) set's S2 'development
    (selection-contaminated)', its C grids and design searches having been chosen on those seasons, and so is a
    stage candidate's whose S2 does not state nested choices (``nested`` False); a stage candidate's S2 whose choices
    were made in the inner folds (``nested`` True: the A5 procedure's outer-fold rows, not one contender's own)
    'development (nested)', so its T2 is never read as a non-nested score."""
    if stage == "s2" and (geo.version == "geo_v1" or nested is False):
        return S2.T2_LABEL
    return T2_NESTED_LABEL if stage == "s2" and nested else "development"


POST_SELECTED_WORDS = "days also used to pick the served set, so scores on them favour the served set"
POST_SEEN_WORDS = "scores seen before this set was designed, so they do not confirm it (post_seen)"


def window_words(window: str, cell: dict, stage: str, geo, nested: bool | None = None, post_seen: bool = False) -> str:
    """Where a pill's number comes from, as its tooltip says it (no ids): the window and the days its rows span;
    cross-season with the protocol's label (§2: development; an existing set's S2 'development
    (selection-contaminated)', its C grids and design searches having been chosen on those seasons); post-training
    for a GEO_V1 set says those days also picked the served set, so the score favours it (X-SEL post_selected,
    protocol §2: the served set, its split and its line were chosen with them in view), on the stages X-SEL tags,
    and for a set tagged post_seen that its post-training scores were seen before it was designed (protocol §2)."""
    sp = span_words(cell.get("span"))
    if window == "T2":
        n = int(cell.get("n_seasons") or 0)
        return f"{n} season{'' if n == 1 else 's'}, each scored by weights that never saw it, {sp}: {t2_label(stage, geo, nested)}"
    if window == "T1-holdout":
        return f"holdout, {sp}: development only"
    if window == "T1":
        selected = geo.version == "geo_v1" and "X-SEL" in SP.STAGE[stage]["exclusions"]
        return (f"post-training, {sp}" + (f" ({POST_SELECTED_WORDS})" if selected else "")
                + (f"; {POST_SEEN_WORDS}" if post_seen else ""))
    if window == "S5":
        return sp
    raise KeyError(f"no words for window {window!r}")


def _dress(out: dict | None, words=None, post: dict | None = None, also=None) -> dict | None:
    """A pill with its window's words, its post-training pill (``post``, flat) and the other numbers its
    tooltip gives (``also``: [pill with 'what'])."""
    if out is None:
        return None
    if words:
        out["window"] = words
    if post:
        out["post"] = {k: v for k, v in post.items() if k not in ("post", "also")}
    if also:
        out["also"] = [{k: v for k, v in a.items() if k not in ("post", "also")} for a in also]
    return out


def _pill(cell: dict | None, words: str | None = None, post: dict | None = None, also=None) -> dict | None:
    """A bundle as a figure pill: BSS with its 90% CI, n, positives, X-POWER and the days it spans."""
    if not cell or cell.get("bss") is None:
        return None
    lo, hi = cell["ci"]["bss"]
    return _dress({"v": cell["bss"], "lo": lo, "hi": hi, "n": cell["n"], "pos": cell["n_pos"], "low_power": cell["low_power"],
                   "span": cell.get("span"), "n_seasons": cell.get("n_seasons")}, words, post, also)


def _delta_pill(cell: dict | None) -> dict | None:
    """An S5 cell as a pill: the change in Brier, corrected − no correction (delta_vs_plain), with its CI."""
    d = (cell or {}).get("delta_vs_plain") or {}
    if d.get("delta") is None:
        return None
    return {"v": d["delta"], "lo": d["lo"], "hi": d["hi"], "n": d["n"], "pos": cell["n_pos"], "low_power": cell["low_power"],
            "span": cell.get("span"), "n_seasons": cell.get("n_seasons")}


def _seed_mean(cells: list) -> dict | None:
    """The degraded feeds' cells as one pill: the mean change in Brier over the seeds, a CI from the lowest
    seed's lower bound to the highest seed's upper bound, X-POWER when any seed is."""
    cells = [c for c in cells if _delta_pill(c)]
    if not cells:
        return None
    ds = [c["delta_vs_plain"] for c in cells]
    return {"v": float(np.mean([d["delta"] for d in ds])), "lo": min(d["lo"] for d in ds), "hi": max(d["hi"] for d in ds),
            "n": int(round(np.mean([d["n"] for d in ds]))), "pos": int(round(np.mean([c["n_pos"] for c in cells]))),
            "low_power": any(c["low_power"] for c in cells), "span": _union([c.get("span") for c in cells]),
            "n_seasons": max(int(c.get("n_seasons") or 0) for c in cells), "seeds": len(cells)}


def s1_figure(s1: dict, post: dict | None, blocks: pd.DataFrame) -> dict:
    """m.s1's pills from stages_s1, on S1's whole Previous Runs window: the floor (one gauge as a forecast of the
    other; either-wet MAE is the same both ways) and the served model at lead 1 on the two-gauge mean, either-wet
    MAE (protocol §4.1); ``post`` (stages_s1.post_training) gives the same cells on post-training days. A pill is
    X-POWER (protocol §6) below 10 either-wet days or 8 storm blocks: s1_scores.json gives a cell's [first, last]
    days, not its rows' dates, so the storms are truth.blocks' (``blocks``) storm blocks overlapping that span (S1's
    rows run on every day of it, so these are the storms its days touch)."""
    def cell(c, words):
        if not c:
            return None
        ew, ci = c["continuous"]["either_wet"], c["ci"]["continuous"]["either_wet"]["mae"]
        span = [c["first"], c["last"]]
        b = blocks.loc[span[0]:span[1]]
        n_storm = b.loc[b["block_kind"] == "storm", "block"].nunique()
        return {"v": ew["mae"], "lo": ci[0], "hi": ci[1], "n": ew["n"], "pos": c["n_either_wet"],
                "low_power": bool(c["n_either_wet"] < X.POWER_MIN_POSITIVES or n_storm < X.POWER_MIN_STORM_BLOCKS),
                "span": span, "window": f"{words}, {span_words(span)}"}
    post = post or {}
    fl, m = s1["floor"][S1_FLOOR], s1["by_lead"]["1"]["avg"]["models"][s1["served_model"]]
    return {"oracle": _dress(cell(fl, "SF Oceanside as a forecast of SF Downtown"), None,
                             cell((post.get("floor") or {}).get(S1_FLOOR), "post-training")),
            "chained": _dress(cell(m, "lead 1, two-gauge mean"), None, cell(post.get("lead1"), "post-training"))}


def _cell(scores: dict, key: str, entry: str, window: str, unit: str = "pooled") -> dict | None:
    return (((scores.get(key) or {}).get(unit) or {}).get(entry) or {}).get(window)


def window_for(scores: dict, node: str, want: str, fallback: bool = True) -> str:
    """The window a node's pills show: ``want``, else (``fallback``) the first of FIGURE_FALLBACK the set has
    (a set the build does not refit per season, gb, has post-training only)."""
    key = NODE_SCORES[node]
    if key == "s5":
        has = lambda w: any(w in c for c in ((scores.get("s5") or {}).get("pooled") or {}).values())  # noqa: E731
    else:
        has = lambda w: _cell(scores, key, "oracle", w) is not None  # noqa: E731
    if has(want) or not fallback:
        return want
    return next((w for w in FIGURE_FALLBACK if has(w)), want)


def figure_windows(scores: dict, windows=None, fallback: bool = True) -> dict:
    """{node: window} the figure shows: ``windows`` (a window for every node, or {node: window}; default the
    powered FIGURE_WINDOWS), each through ``window_for``; S1 is its own Previous Runs window."""
    want = {n: windows for n in FIGURE_WINDOWS} if isinstance(windows, str) else dict(windows or FIGURE_WINDOWS)
    return {"m.s1": S1_WINDOW, **{n: window_for(scores, n, w, fallback) for n, w in want.items()}}


def figure(scores: dict, windows, s1: dict | None, geo, s1_post: dict | None = None, fallback: bool = False,
           nested: bool | None = None, post_seen: bool = False, blocks: pd.DataFrame | None = None) -> dict:
    """figure{node id: {oracle, chained, lead}, caption} for stages_flowchart.render.

    ``windows``: one window for every node (scores['figures'][w]) or {node: window} (FIGURE_WINDOWS, the
    powered figure, with ``fallback``: ``figure_windows``). S1 is its own Previous Runs window in every
    figure (its X-POWER reads truth.blocks, ``blocks``). Per node: S2 and S4 pooled BSS, oracle vs lead 1;
    S3's oracle on the zones it scores (the Westside split) and lead 1 on the oracle's own rows, paired
    (lead 1 on every day in the tooltip); OUT's oracle, lead 1 and the lead strip L0 … L5; S5's change in
    Brier, corrected − no correction, for the set's own correction rule (GEO_V1: basin_swap = live_v2) on the
    perfect feed and the degraded feeds' mean, link/zone injection in the tooltip. Every pill carries the
    words for its window and, unless it is post-training already, its post-training (T1) pill; ``post_seen``
    tags those (protocol §2)."""
    shown = figure_windows(scores, windows, fallback)
    fig = {}
    if s1:
        fig["m.s1"] = s1_figure(s1, s1_post, blocks)

    def pill(key, e, w, stage, what=""):
        c = _cell(scores, key, e, w)
        p1 = _cell(scores, key, e, POST_WINDOW) if w != POST_WINDOW else None
        return _pill(c, c and what + window_words(w, c, stage, geo, nested, post_seen),
                     _pill(p1, p1 and window_words(POST_WINDOW, p1, stage, geo, nested, post_seen)))
    for st, node in (("s2", "m.s2"), ("s4", "m.s4"), ("out", "m.out")):
        if st in scores:
            w = shown[node]
            fig[node] = {"oracle": pill(st, "oracle", w, st), "chained": pill(st, "L1", w, st)}
            if st == "out":
                fig[node]["lead"] = [pill(st, f"L{i}", w, st) for i in range(6)]
    if "s3" in scores:
        w = shown["m.s3"]
        every = pill("s3", "L1", w, "s3")
        fig["m.s3"] = {"oracle": pill("s3", "oracle", w, "s3", "the Westside split on its overflow days, "),
                       "chained": _dress(pill("s3_on_oracle_rows", "L1", w, "s3", "on the oracle's own days, "),
                                         also=[dict(every, what="lead 1 on every day")] if every else None)}
    if "s5" in scores:
        w = shown["m.s5"]
        rule = "basin_swap" if geo.version == "geo_v1" else S5.PRIMARY[0]      # what the set serves (Part B 15)
        alt = next(v for v in S5.PRIMARY if v != rule)
        pooled5 = scores["s5"].get("pooled") or {}
        seeds = sorted(f for f in pooled5 if f.startswith("degraded:"))
        cell = lambda f, ww, v: ((pooled5.get(f) or {}).get(ww) or {}).get(v)  # noqa: E731

        def s5_pill(ww, v, chained):
            p = _seed_mean([cell(f, ww, v) for f in seeds]) if chained else _delta_pill(cell("oracle", ww, v))
            return _dress(p, p and window_words(ww, p, "s5", geo, post_seen=post_seen))
        fig["m.s5"] = {}
        for k, chained in (("oracle", False), ("chained", True)):
            main = s5_pill(w, rule, chained)
            if main:
                feed = (f"the mean of {main['seeds']} degraded feeds (its range runs from the lowest seed's 90% CI bound to "
                        "the highest's)" if chained else "the perfect feed")
                a = s5_pill(w, alt, chained)
                main = _dress(main, f"{S5_RULE_WORDS[rule]} − no correction, lower is better; {feed}, {main['window']}",
                              s5_pill(POST_WINDOW, rule, chained) if w != POST_WINDOW else None,
                              [dict(a, what=S5_RULE_WORDS[alt])] if a else None)
            fig["m.s5"][k] = main
    fig = V.clean(fig)
    fig["caption"] = figure_caption(fig, shown, t2_label("s2", geo, nested), post_seen)
    return fig


def figure_caption(fig: dict, shown: dict, s2_label: str = "development", post_seen: bool = False) -> str:
    """The one line under the figure title: in plain words, which window each stage's pills show, and that the
    post-training scores are in the tooltips (``post_seen``: and were seen before the set was designed). Cross-season
    says it is development (protocol §2), with S2's own label (``t2_label``) when it differs: a phone has no tooltip,
    so the label must be on the face."""
    parts = []
    c1 = (fig.get("m.s1") or {}).get("chained") or {}
    if c1.get("span"):
        parts.append(f"S1 one day ahead, {span_words(c1['span'])}")
    by_window: dict = {}
    for n in ("m.s2", "m.s3", "m.s4", "m.out"):
        if n in fig:
            by_window.setdefault(shown[n], []).append(n)
    for w, nodes in by_window.items():
        oracle = [fig[n].get("oracle") or {} for n in nodes]
        span = _union([o.get("span") for o in oracle])
        if not span:
            continue
        who = "S2 to OUT" if len(nodes) == 4 else ", ".join(NODE_CODE[n] for n in nodes)
        if w == "T2":
            n_s = max(int(o.get("n_seasons") or 0) for o in oracle)
            words = (f"{n_s} season{'' if n_s == 1 else 's'} ({span_words(span)}), each scored by weights that never saw it: "
                     "development scores")
            if "m.s2" in nodes and s2_label != "development":
                words += f", S2's {s2_label.removeprefix('development').strip(' ()')}"
        else:
            words = f"{CAPTION_WINDOW.get(w, w)} ({span_words(span)})"
        chained = _union([(fig[n].get("chained") or {}).get("span") for n in nodes])
        if chained and chained[0] > span[0]:
            words += f"; one day ahead from {_month(chained[0])}"
        parts.append(f"{who} on {words}")
    s5 = (fig.get("m.s5") or {}).get("oracle") or {}
    if s5.get("span"):
        parts.append(f"S5 {span_words(s5['span'])}")
    line = f"Pills: {' · '.join(parts)}." if parts else ""
    pills = [(n, p) for n, v in fig.items() if isinstance(v, dict) for p in (v.get("oracle"), v.get("chained")) if isinstance(p, dict)]
    post = _union([p["post"].get("span") for _, p in pills if p.get("post")])
    if post:
        every = all(p.get("post") for n, p in pills if shown.get(n) != POST_WINDOW)
        line += (f" Post-training ({span_words(post)}) is in {'each pill’s tooltip' if every else 'the other pills’ tooltips'}"
                 + (f": {POST_SEEN_WORDS}." if post_seen else "."))
    return line


S5_CHIP_FEED = "oracle"                             # S5's chip: the perfect feed in S5's window, each zone-day once
S5_FEED_WORDS = {"oracle": "the perfect feed", "archive": "the 2016-17 archive", "watcher": "the watcher"}


def tier_span(tier: str) -> tuple | None:
    """(first day, last day or None: the data end) of a tier in S5's window (protocol §2: T1 stops at the freeze, T0
    starts the next day); None for T2, whose seasons are no one span."""
    one, freeze = pd.Timedelta(days=1), X.freeze_date()
    return {"T1-holdout": (S2.HOLDOUT_START, S2.POST_START - one), "T1": (S2.POST_START, freeze),
            S2.T0: (freeze + one, None)}.get(tier)


def feed_words(feed: str) -> str:
    """'oracle' → 'the perfect feed', 'degraded:3' → 'degraded feed 3' (KeyError on an unknown feed)."""
    if feed.startswith("degraded:"):
        return f"degraded feed {feed.split(':', 1)[1]}"
    return S5_FEED_WORDS[feed]


def s5_feed_counts(partition: dict, as_of) -> dict:
    """S5's exclusion counts per feed from the build's partition (scores['partition']['s5']: unit → feed → window →
    cell, one variant), each feed on windows that never overlap, so a count is distinct zone-days: a feed with rows in
    S5's window (T1-holdout ∪ T1 ∪ T0, protocol §8: the perfect feed, each degraded feed) on that window only, else on the
    windows it has (the 2016-17 archive: its own season, T2). A feed's T2 seasons and the holdout overlap (2023-24 and
    2024-25 sit in both), and the feeds see the same zone-days, so summing either would count a zone-day twice.

    {feed: {"tiers": [...], "span": [first, last] | None, "counts": {rule: n}}}: counts are every first-match rule
    the feed's rows carry (OUT's rules too, S5 being graded on OUT's label), S5's own exclusions present as 0
    where none; span is S5's window as the tiers give it (None for T2)."""
    own = [x for x in SP.STAGE["s5"]["exclusions"] if SP.EXCLUSIONS[x]["kind"] == "exclude"]
    cells: dict = {}
    for unit, by_feed in partition.items():
        for feed, by_tier in by_feed.items():
            feed_words(feed)                                   # an unknown feed raises
            for tier, cell in by_tier.items():
                cells.setdefault(feed, {}).setdefault(tier, []).append(cell["excluded"])
    out = {}
    for feed in sorted(cells, key=lambda f: (f != S5_CHIP_FEED, f != "archive", f)):
        tiers = [t for t in S5_WINDOW if t in cells[feed]] or sorted(cells[feed])
        n = {x: 0 for x in own}
        for t in tiers:
            for ex in cells[feed][t]:
                for x, k in ex.items():
                    n[x] = n.get(x, 0) + int(k)
        span, spans = None, [tier_span(t) for t in tiers]
        if all(spans):
            lo, hi = min(s[0] for s in spans), min(max(s[1] or pd.Timestamp(as_of) for s in spans), pd.Timestamp(as_of))
            if lo > hi:
                raise ValueError(f"S5 feed {feed!r}: rows of {tiers}, which start after the data end ({pd.Timestamp(as_of).date()})")
            span = [str(lo.date()), str(hi.date())]
        out[feed] = {"tiers": tiers, "span": span, "counts": n}
    return out


def _feed_where(feed: str, c: dict, chip: bool = False) -> str:
    """Where a feed's counts come from (``s5_feed_counts``' cell): S5's window as its span ("S5's window (…)" on
    the chip's line); else the 2016-17 archive's own season, or a feed scored only cross-season (T2, its seasons
    never overlapping), so the words never claim a window the counts do not come from."""
    if c["span"]:
        return f"S5's window ({span_words(c['span'])})" if chip else span_words(c["span"])
    if c["tiers"] == ["T2"]:
        return "its own season" if feed == "archive" else "the cross-season folds"
    raise ValueError(f"S5 feed {feed!r} has no words for its windows {c['tiers']}")


def figure_counts(geo, as_of, s1: dict | None = None, s5_partition: dict | None = None) -> tuple:
    """(chip counts for stages_flowchart, the truth catalog, the claims): exclusions.figure_counts of the catalog
    and the claims as of the data end; the S1 model gaps (X-S1-NWPGAP, a weather-model fact the truth cannot
    count) from stages_s1 for the served model at lead 1 on the two-gauge mean, the chained pill's rows; S5's own
    rules from the build's rows (``s5_partition`` = scores['partition']['s5']): the chip counts the perfect feed in
    S5's window (``s5_feed_counts``), so each count is distinct zone-days, and counts['feeds']['s5'] gives every
    feed's own for the chip's tooltip (an S5 rule the rows never hit is 0; with no perfect-feed rows the chip prints
    "—"); and the slots (n_city_days: days any basin filed an overflow)."""
    cat = X.catalog_counts(geo, as_of=as_of)
    cl = X.claims(geo, as_of=as_of)
    fc = X.figure_counts(cat, cl)
    if s1:
        cell = s1["exclusions"][s1["served_model"]]["previous_runs"][T.MEAN_SERIES]["L1"]["T1"]
        n = int(cell["excluded"].get("X-S1-NWPGAP", 0))
        fc["exclusions"]["X-S1-NWPGAP"] = fc["stages"].setdefault("s1", {})["X-S1-NWPGAP"] = n
    if s5_partition is not None:
        by_feed = s5_feed_counts(s5_partition, as_of)
        own = [x for x in SP.STAGE["s5"]["exclusions"] if SP.EXCLUSIONS[x]["group"] == "S5" and SP.EXCLUSIONS[x]["kind"] == "exclude"]
        chip = by_feed.get(S5_CHIP_FEED)
        for x in own:                                     # the catalog's archive-window counts never stand in
            fc["exclusions"].pop(x, None)
        fc["stages"]["s5"] = dict(chip["counts"]) if chip else {}
        if chip:
            fc["exclusions"].update({x: chip["counts"][x] for x in own})
        fc["feeds"] = {"s5": {
            "chip": (f"{feed_words(S5_CHIP_FEED)}'s zone-days in {_feed_where(S5_CHIP_FEED, chip, chip=True)}, each zone-day once"
                     if chip else None),
            "by_feed": [{"what": f"{feed_words(f)}, {_feed_where(f, c)}", "counts": {x: c["counts"].get(x, 0) for x in own}}
                        for f, c in by_feed.items()]}}
    ev = T.ledger_events(geo)
    fc["slots"] = {"n_city_days": int(ev.loc[ev["date"] <= pd.Timestamp(as_of), "date"].nunique())}
    return fc, cat, cl


# ── the build ───────────────────────────────────────────────────────────────

@dataclass
class Build:
    bundle: SetBundle
    rows: dict                    # stage → DataFrame (exclusions.COLUMNS + fold)
    scores: dict
    manifest: dict
    specs: dict                   # (tier, fold) → {"s3", "s4"}
    spec_info: dict               # (tier, fold) → fit info
    rung: pd.DataFrame            # OUT truth-at-S3 rows (ladder only)
    n_boot: int


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Inputs whose file holds a build clock: hashed without it, so rebuilding them on unchanged data (same scores,
# a new built_at) leaves every manifest that pins them current. Their other bytes still count, as code does.
UNSTAMPED = {S1_SCORES: ("built_at",)}
# The served set's written build that another set's comparisons read (no clock in either: gzip mtime 0, scores
# without built_at), so a served rebuild on the same inputs leaves the others current and any other stales them
SERVED_BUILD_FILES = ("rows.csv.gz", "scores.json")


def input_sha(path: Path) -> str:
    """The sha256 a manifest pins for an input: the file's bytes, or for an UNSTAMPED json its content with the
    clock keys dropped (sorted keys, compact separators), so only a change in what it says makes it stale."""
    path = Path(path)
    drop = next((keys for p, keys in UNSTAMPED.items() if Path(p).resolve() == path.resolve()), None)
    if drop is None:
        return _sha(path)
    doc = json.loads(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name} is not a json object: cannot drop {drop}")
    body = {k: v for k, v in doc.items() if k not in drop}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def code_files() -> list:
    """Every module of this repository the build has imported (this one included; never tests/ or a venv), for
    the manifest's code sha256s: the artifacts are stale when any of them changes, as when an input does."""
    out = set()
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if not f:
            continue
        p = Path(f).resolve()
        if p.suffix != ".py" or REPO not in p.parents or "site-packages" in p.parts:
            continue
        if any(d in p.parents for d in (REPO / "tests", REPO / "venv")):
            continue
        out.add(p)
    return sorted(out)


def stale_files(manifest_: dict) -> list:
    """The manifest's inputs and code files that are missing or changed since it was written (inputs by
    ``input_sha``, so an UNSTAMPED input's clock never makes it stale; code by its bytes)."""
    stale = [f for f, h in manifest_["inputs"].items() if not (REPO / f).exists() or input_sha(REPO / f) != h]
    return stale + [f for f, h in manifest_.get("code", {}).items() if not (REPO / f).exists() or _sha(REPO / f) != h]


def input_files(bundle: SetBundle, model: str) -> list:
    """Every file the build reads, for the manifest's sha256s (a stage candidate's: every file of its directory and
    the bake-off it names; the served set's: the forecast_history snapshot, once one is committed)."""
    raw = T4.RAW_DIR
    sd = T4.SERVE_DIR if bundle.is_served or bundle.stage is not None else CAND.candidate_dir(bundle.name)
    files = [X.PROTOCOL, FORECAST / "live_dashboard.py", S1_SCORES, S1.OUT_DIR / f"{model}.csv.gz", raw / "historical_rain.csv",
             raw / "hourly_rain_openmeteo.csv",
             raw / "historical_bacteria.csv", raw / f"openmeteo_prev_runs_{model}.csv", raw / f"openmeteo_hist_forecast_{model}.csv",
             FORECAST / "data" / "csd" / "sf_csd_events.csv", FORECAST / "data" / "csd" / "sf_csd_monthly_coverage.csv",
             FORECAST / "data" / "poobot" / "samples.csv", FORECAST / "data" / "poobot" / "discharge_onsets.csv",
             FORECAST / "data" / "poobot" / "feed_status.csv", T4.SERVE_DIR / "served.json", T4.SERVE_DIR / "eval_report.json",
             T4.SERVE_DIR / "impact_table.json"]
    files += [SMP.SOURCE_FILES[n] for n in SMP.PRECEDENCE]          # S4's truth reads all three (design D10)
    files += [T4.HOURLY_WIND_CSV, E.OMP.wind_path(model)]           # every entry's south wind (rain_features.WIND_FEATURES)
    if bundle.stage is not None:
        files += list(bundle.stage.files)
    else:
        files += sorted(sd.glob("*_model.pkl")) + sorted(T4.SERVE_DIR.glob("*_volume.pkl"))
        files += [sd / n for n in ("stage2.json", "manifest.json") if (sd / n).exists()]
    if bundle.stage is None and bundle.s2.train_record is not None:   # a longer label record: its reports and rain
        import csd_pre2018 as P
        files += [P.WESTSIDE_CSV, P.WESTSIDE_COVERAGE_CSV, P.BAYSIDE_CSV, P.BAYSIDE_COVERAGE_CSV]
        if pd.Timestamp(bundle.s2.train_record["start"]) < T4.TRAIN_START:
            files += [T4.OLDER_RAIN_CSV, T4.OLDER_HOURLY_CSV]
    if not bundle.is_served:                       # vs_served and the primaries read the served set's written build
        built = STAGES_DIR / served_name()
        files += [built / n for n in SERVED_BUILD_FILES if (built / n).exists()]
    elif GP.SNAPSHOT.exists():                     # its T0 as served (t0_as_served)
        files.append(GP.SNAPSHOT)
    import beachwatch as BW
    files += [BW.POSTED_DAYS_CSV, BW.MANIFEST]
    missing = [p for p in files if not p.exists()]
    if missing:
        raise FileNotFoundError(f"inputs missing: {[str(p) for p in missing]}")
    return list(dict.fromkeys(files))


def build(set_name: str = "served", root: str = "served", entries=ENTRIES, tiers=None, steps=STEPS,
          n_boot: int = B_PROTOCOL, seasons=None, feeds=None, s5_tiers=None, log=print, stage_set=None) -> Build:
    """Score every stage of one set (see the module notes). ``tiers`` default: every tier the set's family
    scores (gb: its finals, T1 and T0), or every tier a stage candidate holds folds for. Development and test
    knobs, all off by default: ``seasons`` keeps the T2 seasons listed, ``feeds`` the S5 feeds listed,
    ``s5_tiers`` the tiers S5 replays, ``stage_set`` a stage candidate already loaded (root stages_candidates);
    ``n_boot`` below the protocol's 2,000 is for tests and is refused by ``write``."""
    t_all = time.time()
    entries = tuple(entries)
    bad = [e for e in entries if e not in ENTRIES]
    if bad or not entries:
        raise KeyError(f"entries must be some of {ENTRIES}, not {list(bad) or entries}")
    steps = tuple(steps)
    if not steps or set(steps) - set(STEPS):
        raise KeyError(f"steps must be some of {STEPS}, not {steps}")
    bundle = load_set(set_name, root, stage_set=stage_set)
    s2set, geo, st = bundle.s2, bundle.geo, bundle.stage
    if tiers is None:
        tiers = (tuple(t for t in TIERS if any(f.tier == t for f in st.folds)) if st is not None
                 else TIERS if s2set.family in S2.REFIT_FAMILIES else S2.FINAL_TIERS)
    tiers = tuple(tiers)
    model = E.served_weather_model()
    end = E.data_end()
    what = f"stage set {components(bundle)}" if st is not None else f"stage 2 {bundle.variant}"
    log(f"{bundle.name} ({root}, {s2set.family}, {what}, {geo.version}); tiers {tiers}; entries {entries}; data to {end.date()}")
    # 1 · truth context, once per geography, with the build's inputs
    fd = S5.feeds(geo, end=end)
    if feeds is not None:
        fd = {k: v for k, v in fd.items() if k in set(feeds) | {"watcher"}}
    ctx = X.context(geo, end=end).with_inputs(nwp=E.nwp_hours(model), feeds=fd,
                                               rain_series={k: s2set.models[k]["rain_source"] for k in s2set.keys})
    blocks = T.blocks(end=end).set_index("date")
    tr = truths(geo, end)
    el = T.zone_elevated(geo, ctx.sources, end=end)
    n_sampled = el.set_index(["zone", "date"])["n_stations_sampled"].astype(float)
    n_sampled = n_sampled.reindex(ctx.frames["zone"].index, fill_value=0.0)
    # 2 · S2 out of fold: a set's own design refit per fold (stages_s2), or a stage candidate's own folds
    t0 = time.time()
    need = sorted(set(s2set.sources) | set(bundle.chosen.values()) | {"avg"})
    if st is None:
        train, _ = T4.build_dataset(sources=need, wind=True)   # the served record: S3 / S4 fold fits read it for every set
        s2_train = train if s2set.train_record is None else S2.training_frames(s2set, need)   # a longer record: S2 only
        fitted = S2.fit(bundle.name, root, tiers, train_frames=s2_train)
        plan = {(p[0], p[1]): p for p in S2._plan(tiers)}
        all_folds = list(fitted.folds)
    else:
        train, plan = None, None
        missing = [t for t in tiers if not any(f.tier == t for f in st.folds)]
        if missing:
            raise ValueError(f"{st.name} holds no fold of {missing}")
        fitted = stage_fitted(st, tiers)
        all_folds = list(fitted.folds) + [f for f in st.folds if f.tier in tiers and st.is_rows_fold(f)]
    folds = [f for f in all_folds if f.tier != "T2" or seasons is None or f.season in set(seasons)]
    # rain known always (S5's inputs; every issue day's days before it), and every entry a lead's issue day reads
    need_entries = tuple(dict.fromkeys(("oracle",) + tuple(x for e in entries if e != "rain" for x in issue_entries(e))))
    frames = {}
    for e in need_entries:
        frames[e] = E.frames(e, need, model=model, end=end)
    pred = fitted.predict(frames) if fitted.folds else pd.DataFrame(columns=list(S2.COLUMNS))
    if st is not None:                                 # a stage candidate's T2: the bake-off's outer-fold rows
        pred = pd.concat([pred, t2_pred(st, folds)], ignore_index=True)
    pred = pred[pred.set_index(["tier", "fold"]).index.isin([(f.tier, f.fold) for f in folds])].reset_index(drop=True)
    rain_known = frames["oracle"]["avg"].set_index("date")["precip_avg"].astype(float)   # an S4 rain background's input
    log(f"  S2: {len(all_folds)} folds {'fit' if st is None else 'read'}, {len(pred)} out-of-fold basin-days ({time.time() - t0:.1f}s)")
    extra = {"reference": st.reference if st is not None else None,
             "fidelity": bakeoff_fidelity(st, pred) if st is not None else None}
    # 3 · S3 / S4 specs per fold
    t0 = time.time()
    events, samples = ((T4.load_events(), SV.impact_samples(SV.SAMPLE_VARIANTS.get(bundle.variant))) if st is None
                       else (None, None))           # v2_d10's linger table refits on the D10 samples in every fold
    specs, spec_info = {}, {}
    for f in folds:
        key = (f.tier, f.fold)
        specs[key], spec_info[key] = fold_specs(bundle, f, plan[key][5] if plan else None, train, events, samples)
    bench = dict(st.s3b) if st is not None else {}                # S3b: the candidate's shares against a constant one
    log(f"  S3 / S4 specs: {len(specs)} folds ({time.time() - t0:.1f}s)")
    # 4 · runs and compositions
    t0 = time.time()
    comp = Composed()
    s2max = 0.0
    s5_runs = {}
    for f in folds:
        sp = specs[(f.tier, f.fold)]
        mine = pred[(pred["tier"] == f.tier) & (pred["fold"] == f.fold)]
        pred_p = {e: g.pivot(index="date", columns="basin", values="p")[list(s2set.keys)] for e, g in mine.groupby("entry", sort=False)}
        base = None
        for e in need_entries:
            if e != "oracle" and e not in entries:
                continue                                   # read only by a lead's issue day (issue_inputs)
            win = pd.DatetimeIndex(sorted(pred_p[e].index)) if e in pred_p else pd.DatetimeIndex([])
            if not len(win):
                continue
            if st is not None and st.is_rows_fold(f):      # the bake-off's rows: S2 read, no warm-up before the window
                rs = runs_from_rows(f, pred, e, frames[e]["avg"].set_index("date")["precip_avg"] if "avg" in frames[e] else None,
                                    s2set.keys)
            else:
                rs = runs_for(s2set, f, e, frames[e], win)
                for r in rs:                               # the warm-up helper is stages_s2's predict path
                    pv = pred_p[e].loc[r.window]
                    s2max = max(s2max, float(np.abs(pv.to_numpy() - r.p.loc[pv.index].to_numpy()).max()))
            if e == "oracle":
                if len(rs) != 1:
                    raise AssertionError(f"rain known breaks into {len(rs)} runs in {f.tier} {f.fold}: a gauge day is missing")
                base = s5_runs[(f.tier, f.fold)] = rs[0]
                if "oracle" in entries:
                    compose_run(geo, sp, base, ctx, tr, n_sampled, comp, steps, oracle=True, rain=rain_known,
                                bench=bench.get((f.tier, f.fold)))
                if "rain" in entries:
                    compose_run(geo, sp, dataclasses.replace(base, entry="rain"), ctx, tr, n_sampled, comp, steps, oracle=False,
                                rain=rain_known)
                continue
            if base is None:
                raise AssertionError(f"{f.tier} {f.fold}: no rain-known run for {e}'s issue days to read")
            for r in rs:                                   # S3 is same-day: the entry's own runs
                compose_run(geo, sp, r, ctx, tr, n_sampled, comp, steps, oracle=False, history=False, rain=rain_known)
            if {"s4", "out"} & set(steps):                 # S4 / OUT: each target's issue-day chain
                s2max = max(s2max, compose_issue(geo, sp, s2set, f, e, frames, base, win, ctx, n_sampled, comp, steps, pred_p))
    if s2max > EPS:
        raise AssertionError(f"the warm-up and issue-day S2 values differ from stages_s2's predictions by {s2max:.1e}")
    log(f"  S3 → S4 → OUT: {sum(len(v) for v in comp.rows.values())} row blocks ({time.time() - t0:.1f}s)")
    # 5 · rows and exclusions
    t0 = time.time()
    ctx_by_tier = {t: tier_context(ctx, geo, specs, [f for f in folds if f.tier == t]) for t in tiers}
    rows = {}
    if "s2" in steps:
        keep = set(entries) | ({"oracle"} if "rain" in entries else set())
        rows["s2"] = apply_stage(s2_rows(pred[pred["entry"].isin(keep)], ctx, tr.basin_vol), "s2", ctx_by_tier)
    for stg in ("s3", "s4", "out"):
        if stg in steps and comp.rows[stg]:
            rows[stg] = apply_stage(pd.concat(comp.rows[stg], ignore_index=True), stg, ctx_by_tier)
    rung = pd.concat(comp.rung, ignore_index=True) if comp.rung else pd.DataFrame(columns=list(X.COLUMNS) + ["fold"])
    extra["s3b"] = pd.concat(comp.bench, ignore_index=True) if comp.bench else None
    log(f"  exclusions: {', '.join(f'{k} {len(v)}' for k, v in rows.items())} rows ({time.time() - t0:.1f}s)")
    # 6 · S5
    if "s5" in steps and s5_runs:
        t0 = time.time()
        rows["s5"] = s5_build(bundle, specs, [f for f in folds if s5_tiers is None or f.tier in s5_tiers], s5_runs, fd, ctx,
                              rain=rain_known)
        log(f"  S5: {len(rows['s5'])} rows ({time.time() - t0:.1f}s)")
    integrity = check_integrity(rows, comp, s2max, spec_info, folds)
    # 7 · scores
    scores = {}
    if "scores" in steps:
        t0 = time.time()
        dropped = {"inputs": {e: frames[e][need[0]].attrs.get("dropped") or {} for e in need_entries},
                   "no_history_days": {f"{e} {t}": n for (e, t), n in sorted(comp.dropped.items())},
                   "note": "inputs: the days an entry has no frame (stages_entries: X-S1-NWPGAP / X-S1-NOLEAD), so no row "
                           "at any stage; no_history_days: days of a window with an S2 row but no S4 / OUT row: a lead entry's "
                           "target whose issue day lacks its forecast for one of I … D−1 (rain known: never)"}
        scores = make_scores(bundle, rows, rung, ctx, ctx_by_tier, blocks, fd, dropped, integrity, tr.basin_vol, n_boot, end, log,
                             extra=extra)
        log(f"  scores ({time.time() - t0:.1f}s)")
    man = manifest(bundle, entries, tiers, steps, model, end, n_boot)
    log(f"done in {time.time() - t_all:.1f}s")
    return Build(bundle, rows, scores, man, specs, spec_info, rung, n_boot)


def s5_variants(geo) -> tuple:
    """stages_s5's variants a geography can replay: every one, less live_v2's rule sets (stages_s5.LIVE_V2:
    basin_swap and its kin, defined on GEO_V1's basins and legacy groups only) outside GEO_V1."""
    return S5.VARIANTS if geo.version == "geo_v1" else tuple(v for v in S5.VARIANTS if v not in S5.LIVE_V2)


def s5_build(bundle: SetBundle, specs: dict, folds: list, s5_runs: dict, fd: dict, ctx: X.Context,
             rain: pd.Series | None = None) -> pd.DataFrame:
    """S5 rows for every feed and fold on the rain-known S2 output, then exclusions.apply per (feed, tier) so
    X-POWER counts the whole window; the excl column must be the same for every variant (one conditional set).
    ``rain``: the two-gauge rain known over the whole record (an S4 rain background reads D−2 before a run's
    first day; live_v2 reads the run's own days, the same values); default each run's own."""
    geo = bundle.geo
    variants = s5_variants(geo)
    parts = []
    for f in folds:
        r = s5_runs.get((f.tier, f.fold))
        if r is None:
            continue
        inputs = C.BasinInputs(r.p, r.v, r.rain if rain is None else rain)
        kw = ({"held_out_season": f.season} if f.tier == "T2"
              else {"trained_through": bundle.s2.trained_through if f.tier in S2.FINAL_TIERS else S2.HOLDOUT_START - pd.Timedelta(days=1)})
        for name, feed in fd.items():
            if name == "watcher" or (name.startswith("degraded") and f.tier not in S5_WINDOW):
                continue                                   # a degraded feed is built for the S5 window only (§8)
            got = S5.s5_rows(geo, specs[(f.tier, f.fold)], inputs, name, feed, variants=variants, tier=f.tier, ctx=ctx, **kw)
            if len(got):
                parts.append(got.assign(fold=f.fold))
    if not parts:
        return pd.DataFrame(columns=list(X.COLUMNS) + ["fold"])
    rows = pd.concat(parts, ignore_index=True)
    out = []
    for (name, t), g in rows.groupby(["entry", "tier"], sort=False):
        a = X.apply(g, "s5", ctx)
        piv = a.pivot_table(index=["unit", "date"], columns="variant", values="excl", aggfunc="first")
        if not (piv.nunique(axis=1) == 1).all():
            raise AssertionError(f"S5 {name} {t}: variants disagree on what is scored")
        out.append(a)
    return pd.concat(out, ignore_index=True)


def t3_rows(rows: dict, folds: list) -> dict:
    """{stage: rows whose scored weights saw the day} (X-ALL-INSAMPLE, protocol §2 T3): a row's (tier, fold) must
    be a fold that was fit, its date inside the fold's window and on no day the fold's S2 weights or volume
    heads were fit on (stages_s2's ``seen``; the fold's S3 / S4 specs were fit on the same training days,
    ``fold_specs``). A T3 tier label counts too."""
    by = {(f.tier, f.fold): f for f in folds}
    out = {}
    for st, r in rows.items():
        n = int((r["tier"] == X.IN_SAMPLE).sum())
        for (t, fo), g in r.groupby(["tier", "fold"], sort=False):
            f = by.get((t, fo))
            if f is None:
                raise AssertionError(f"{st}: rows of {t} fold {fo!r}, which was never fit")
            d = pd.DatetimeIndex(g["date"])
            seen = functools.reduce(lambda a, b: a.union(b), f.seen.values())
            n += int(((d < f.start) | (d > f.end) | d.isin(seen)).sum())
        out[st] = n
    return out


def check_integrity(rows: dict, comp: Composed, s2max: float, spec_info: dict, folds: list) -> dict:
    """The build's own checks, each raising when it fails: no T3 row (``t3_rows``); the X-S3-ID zone-days of the
    oracle equal the truth exactly; no scored oracle row reads a day the ledger does not know; the counts
    partition."""
    t3 = t3_rows(rows, folds)
    if any(t3.values()):
        raise AssertionError(f"X-ALL-INSAMPLE: rows on days their fold's weights saw {t3}")
    out = {"t3_rows": int(sum(t3.values())), "s2_warmup_max_abs_diff": s2max}
    if "s3" in rows:
        r = rows["s3"]
        idr = r[(r["entry"] == "oracle") & (r["excl"] == "X-S3-ID")]
        mism = int((np.abs(idr["p"].to_numpy(dtype=float) - idr["y"].to_numpy(dtype=float)) > 0).sum())
        if mism:
            raise AssertionError(f"X-S3-ID: {mism} identity zone-days where the oracle is not the truth")
        out["s3_identity_mismatches"] = mism
        out["s3_identity_checked"] = int(len(idr))
    reads = {}
    if comp.unknown:
        u = pd.DataFrame(comp.unknown, columns=["stage", "unit", "date"])
        for st in ("s3", "s4", "out"):
            if st not in rows:
                continue
            r = rows[st]
            sc = r[(r["entry"] == "oracle") & (r["excl"] == "")].set_index(["unit", "date"])
            hit = sc.index.isin(pd.MultiIndex.from_frame(u.loc[u["stage"] == st, ["unit", "date"]]))
            if st == "out":                                 # an overflow day is 1 whatever came before (x(0) = 1)
                hit &= ~((sc["y"] == 1) & (sc["p"] == 1)).to_numpy()
            reads[st] = int(hit.sum())
            if reads[st]:
                raise AssertionError(f"{st} oracle: {reads[st]} scored rows read a day the ledger does not know")
    out["oracle_unknown_reads"] = reads
    parts = {}
    for st, r in rows.items():
        if st == "s5":
            one = r[r["variant"] == "plain"]
            parts[st] = X.counts(one)
        elif len(r):
            parts[st] = X.counts(r)
    out["partition"] = "n_total = n_scored + Σ n_rule for every (stage, unit, entry, window): asserted by exclusions.counts"
    out["fold_specs"] = {f"{t} {fo}": {k: v for k, v in info.items() if k in ("fit_span", "n_fit_days", "scored_overlap")}
                         for (t, fo), info in spec_info.items()}
    out["_counts"] = parts
    return out


def make_scores(bundle: SetBundle, rows: dict, rung: pd.DataFrame, ctx: X.Context, ctx_by_tier: dict, blocks: pd.DataFrame,
                fd: dict, dropped: dict, integrity: dict, basin_vol: pd.DataFrame, n_boot: int, end, log,
                extra: dict | None = None) -> dict:
    """scores.json (design §7, protocol §4–§9): per stage {unit | pooled: {entry: {window: bundle}}} with each row's
    reference from its fold's training days; s2_volume; s5 and the paired comparisons (for a set that is not the
    served one: vs_served, and for a stage candidate s2_vs_recipe and s3b_vs_constant from ``extra``); the
    exclusion counts and the partition of the build's rows; the claims and the truth catalog; the figure's counts;
    the integrity checks; the figure on each stage's powered window (``figure``, FIGURE_WINDOWS) and one per
    fitted window; for the served set its T0 as served (``t0_as_served``); for a set that is not the served one,
    the §8 primaries and the §9 criteria. No clock, so a rebuild on the same inputs gives the same file."""
    extra = extra or {}
    geo = bundle.geo
    nested = bundle.stage.nested if bundle.stage is not None else None
    out = {"schema": SCORES_SCHEMA, "set": bundle.name, "geography": geo.version, "protocol": X.protocol_stamp(),
           "as_of": str(end.date()), "windows": {**{t: dict(S2._TIER_TEXT[t]) for t in TIERS},
                                                  "S5": "S5's window, 2023-07-01 → the data end: T1-holdout ∪ T1 ∪ T0 (protocol §8)"},
           "bootstrap": {"n": n_boot, "seed": SEED, "level": LEVEL, "protocol": n_boot == B_PROTOCOL},
           "blocks": {"s2-out": "storm blocks from rain (truth.blocks, protocol §6)", "s5": "observation events (s5_blocks)"},
           "reference": "climatology per unit × calendar month ±1 on the fold's training days (protocol §4.2)",
           "aliases": {"s2": {"rain": "oracle"}}, "edges": list(RL.edges())}
    if bundle.stage is not None:                       # a new candidate's T2: nested choices are development, not contaminated
        out["windows"]["T2"]["label"] = t2_label("s2", geo, nested)
        out["windows"]["T2"]["weights"] = ("S2: the bake-off's outer-fold rows (rain known): the A5 procedure, each season "
                                           "predicted by the contender, bends, C and South mode chosen without it "
                                           + ("(nested)" if nested else "(not stated nested)")
                                           + ", not one contender's own; S3 / S4: the candidate's per-fold records, each "
                                           "fit without its season")
        out["windows"]["T2"]["volume_heads"] = "the bake-off's outer-fold heads (its rows' v̂), each fit without its season"
        out["windows"]["T1-holdout"]["weights"] = "the candidate's holdout siblings, fit on days before 2023-07-01"
        out["windows"]["T1-holdout"]["volume_heads"] = "its holdout siblings' heads, fit on days before 2023-07-01"
        out["windows"]["T1"]["weights"] = "the candidate's finals, fit through 2025-10-31"
        out["windows"]["T1"]["volume_heads"] = "its finals' heads, fit through 2025-10-31"
    if bundle.post_seen:                               # protocol §2: designed after these days' scores were seen
        out["windows"]["T1"]["tag"] = "post_seen"
        out["windows"]["T1"]["post_seen"] = bundle.post_seen
    pools = {st: _pool(st, ctx, geo, "rain") for st in ("s2", "s3", "s4", "out")}
    no_id = dataclasses.replace(ctx, frames={**ctx.frames, "zone": ctx.frames["zone"].assign(identity=False)})
    pools["s3_oracle"] = _pool("s3", no_id, geo, "oracle")
    paired = {"oracle_vs_chained": {}, "as_served": {}}
    for st in ("s2", "s3", "s4", "out"):
        if st not in rows:
            continue
        r = rows[st].copy()
        r["ref"] = np.nan
        oracle = (r["entry"] == "oracle").to_numpy()
        if st == "s3":
            r.loc[oracle, "ref"] = references(r[oracle], pools["s3_oracle"])
            r.loc[~oracle, "ref"] = references(r[~oracle], pools["s3"])
        else:
            r["ref"] = references(r, pools[st])
        rows[st]["ref"] = r["ref"].to_numpy()
        out[st] = score_stage(r, st, blocks, n_boot)
        if st == "s3":
            out["s3_on_oracle_rows"] = score_on_rows(r, "oracle", blocks, n_boot)
        if st == "s2":
            out["s2_volume"] = score_volume(r, ctx, ctx_by_tier, basin_vol, blocks, n_boot)
        for e in sorted(set(r["entry"]) - {"oracle"}):
            if (r["entry"] == "oracle").any():
                paired["oracle_vs_chained"].setdefault(st, {})[e] = _paired(r, e, "oracle", blocks, n_boot)
        for a, b in (("L0s", "L0"), ("L1s", "L1")):
            if {a, b} <= set(r["entry"]):
                paired["as_served"].setdefault(st, {})[f"{a} − {b}"] = _paired(r, a, b, blocks, n_boot)
        log(f"    {st} scored")
    if "out" in rows and len(rung):
        rg = rung.copy()
        zf = ctx.frames["zone"]
        rg["hist_known"] = zf["hist_known"].reindex(pd.MultiIndex.from_arrays([rg["unit"], pd.DatetimeIndex(rg["date"])])).to_numpy(dtype=bool)
        paired["ladder"] = ladder(rows["out"], rg, blocks, n_boot)
    if "s5" in rows and len(rows["s5"]):
        r = rows["s5"]
        r["ref"] = references(r, pools["out"])
        out["s5"], paired["s5_primary"] = score_s5(r, fd, blocks, n_boot)
        paired["s5_primary_rule"] = ("protocol §8: link_zone_swap − basin_swap on the conditional set, superiority (the 90% CI's "
                                     "upper bound below 0) on the perfect feed and on the degraded feed; window 'S5' = 2023-07-01 → "
                                     "the data end (T1-holdout ∪ T1 ∪ T0)")
    if bundle.is_served:
        out["t0_as_served"] = t0_as_served(bundle.name, ctx_by_tier.get(S2.T0, ctx), pools["out"], blocks, n_boot, end)
    art = served_artifacts() if not bundle.is_served else None
    if art is not None:
        paired["vs_served"] = vs_served(rows, art, blocks, n_boot, geo)
    if extra.get("reference") is not None and "s2" in rows:
        paired["s2_vs_recipe"] = s2_vs_recipe(rows["s2"], extra["reference"], blocks, n_boot)
    if extra.get("s3b") is not None and "s3" in rows:
        paired["s3b_vs_constant"] = s3b_vs_constant(rows["s3"], extra["s3b"], geo, blocks, n_boot)
    out["paired"] = paired
    counts = integrity.pop("_counts")
    out["exclusions"] = {st: c["exclusions"].get(st, {}) for st, c in counts.items()}
    out["partition"] = {st: c["partition"].get(st, {}) for st, c in counts.items()}
    s1 = json.loads(S1_SCORES.read_text()) if S1_SCORES.exists() else None
    s1_post = S1.post_training(s1["served_model"]) if s1 else None
    fc, cat, cl = figure_counts(geo, end, s1, out["partition"].get("s5") if len(rows.get("s5", ())) else None)
    out["claims"] = cl
    out["catalog"] = cat
    out["figure_counts"] = fc
    out["integrity"] = integrity
    if extra.get("fidelity") is not None:
        out["integrity"]["s2_bakeoff_fidelity"] = extra["fidelity"]
    if bundle.stage is not None:
        out["integrity"]["s3_fit_with_s2"] = dict(bundle.stage.s3_s2)
        sz = bundle.stage.s4_sizes
        out["integrity"]["s4_fit_sizes"] = {               # Part B 6: S4 fit at the sizes S3 gives it, per fold
            "match": all(s["match"] for s in sz.values()) if all(s["match"] is not None for s in sz.values()) else None,
            "folds_off": [f"{t} {fo}" for (t, fo), s in sorted(sz.items()) if s["match"] is False],
            "folds_unstated": [f"{t} {fo}" for (t, fo), s in sorted(sz.items()) if s["match"] is None]}
    out["dropped"] = dropped
    seen = bool(bundle.post_seen)
    out["figures"] = {w: figure(out, w, s1, geo, s1_post, nested=nested, post_seen=seen, blocks=blocks) for w in S2.TIERS}
    out["figure_window"] = figure_windows(out)
    out["figure_post_window"] = POST_WINDOW
    out["figure"] = figure(out, FIGURE_WINDOWS, s1, geo, s1_post, fallback=True, nested=nested, post_seen=seen, blocks=blocks)
    if art is not None:
        out = V.clean(out)
        out["primaries"] = primaries(bundle, out, rows, art, ctx, blocks, n_boot, end)
        out["promotion"] = promotion(out["primaries"])
    return V.clean(out)


def served_artifacts() -> dict:
    """The served set's written build (data/models/stages/<served>/: rows, manifest, scores) that every comparison
    with it reads, or {'skipped': why} when it was not written or was built on other inputs, code or protocol."""
    d = STAGES_DIR / served_name()
    if not (d / "rows.csv.gz").exists():
        return {"skipped": "the served set's rows are not written: run --set served --write first"}
    theirs = json.loads((d / "manifest.json").read_text())
    stale = stale_files(theirs) if "code" in theirs else ["(no code stamp)"]
    if stale or theirs["protocol"] != X.protocol_stamp():
        return {"skipped": f"the served set's rows were built on other inputs or code ({stale or 'protocol'}): rebuild it first"}
    srv = pd.read_csv(d / "rows.csv.gz", parse_dates=["date"], keep_default_na=False, na_values=[""], low_memory=False)
    sc = json.loads((d / "scores.json").read_text()) if (d / "scores.json").exists() else None
    return {"rows": srv, "manifest": theirs, "scores": sc}


def _pair(a: pd.DataFrame, b: pd.DataFrame, what: str, key=("unit", "date")) -> tuple:
    """(common index, y, p_a at rows.csv.gz's precision, p_b) on the intersection of two arms' scored rows; the same
    unit-day with two truths raises."""
    A, Bf = a.set_index(list(key)), b.set_index(list(key))
    common = A.index.intersection(Bf.index)
    ya, yb = A.loc[common, "y"].to_numpy(dtype=float), Bf.loc[common, "y"].to_numpy(dtype=float)
    if not np.array_equal(ya, yb):
        raise AssertionError(f"{what}: the same unit-days carry different truths")
    return common, ya, _as_written(A.loc[common, "p"].to_numpy(dtype=float)), Bf.loc[common, "p"].to_numpy(dtype=float)


def vs_served(rows: dict, art: dict, blocks: pd.DataFrame, n_boot: int, geo) -> dict:
    """Candidate − served on identical rows (Part B 16: both arms built from the same entries and inputs), read
    from the served set's rows.csv.gz (``served_artifacts``): {stage: {unit | pooled: {entry: {window: Δ}}}}; OUT's
    pooled rain-known and lead-1 cells add ΔMCB (CORP miscalibration, protocol §8's "pooled MCB not worse beyond its
    CI"). Once T0 holds rows, OUT adds protocol §8's window T1 post ∪ T0 as one cell (OUT_WINDOW): the unit-days both
    sets scored in either tier, ordered as one window's rows are (zone by zone, day by day), so the bootstrap reads
    them as it would a single tier. Across geographies only the geography-invariant targets (S3 zones, S4, OUT;
    protocol §8): basin-level S2 is compared within one geography. Skipped, with the reason, when the served build
    cannot be read."""
    if "skipped" in art:
        return {"skipped": art["skipped"]}
    srv, sgeo = art["rows"], art["manifest"]["geography"]
    cross = sgeo != geo.version
    out: dict = {"served": served_name(), "note": "candidate − served on the unit-days both scored, same entries and inputs "
                                                  "(Part B 16); both arms at rows.csv.gz's 6 significant digits",
                 "geography": {"candidate": geo.version, "served": sgeo, "invariant_only": cross}}
    if cross:
        out["geography"]["s2"] = ("not compared: basin-level S2 is compared within one geography only (protocol §8); "
                                  "the S2 primary reads s2_vs_recipe")
    for st, r in rows.items():
        if st == "s5" or (cross and st == "s2"):
            continue
        s = srv[(srv["stage"] == st) & (srv["excl"].fillna("") == "")]
        c = r[r["excl"] == ""]
        cells = [(e, t, g, [t]) for (e, t), g in c.groupby(["entry", "tier"], sort=False)]
        if st == "out" and (c["tier"] == S2.T0).any():
            u = c[c["tier"].isin(OUT_TIERS)]
            zone = {z: i for i, z in enumerate(pd.unique(u["unit"]))}
            u = u.sort_values(["unit", "date"], key=lambda x: x.map(zone) if x.name == "unit" else x)
            cells += [(e, OUT_WINDOW, g, list(OUT_TIERS)) for e, g in u.groupby("entry", sort=False)]
        for e, t, g, tiers in cells:
            b = s[(s["entry"] == e) & s["tier"].isin(tiers)]
            common, y, pa, pb = _pair(g, b, f"vs_served {st} {e} {t}")
            if not len(common):
                continue
            blk, _ = _storm_blocks(common.get_level_values("date"), blocks)
            units = common.get_level_values("unit").to_numpy()
            for u in ["pooled"] + list(pd.unique(units)):
                m = np.ones(len(common), bool) if u == "pooled" else units == u
                cell = _delta(y[m], pa[m], pb[m], blk[m], n_boot)
                if st == "out" and u == "pooled" and e in ("rain", "L1"):
                    cell["mcb"] = V.clean(V.paired_delta(y[m], pa[m], pb[m], blk[m], metric=_mcb, n=n_boot, seed=SEED, level=LEVEL))
                out.setdefault(st, {}).setdefault(u, {}).setdefault(e, {})[t] = cell
    return out


def s2_vs_recipe(s2rows: pd.DataFrame, ref: pd.DataFrame, blocks: pd.DataFrame, n_boot: int) -> dict:
    """The S2 primary within one geography (protocol §8): candidate − the served recipe refit on the candidate's
    labels (the bake-off's served-recipe rows), on the candidate's scored rain-known rows that the recipe also
    predicts, paired on (tier, fold, basin, day): {unit | pooled: {window: Δ}}. Both arms at the bake-off file's 6
    significant digits (``_as_written``), so equal predictions differ by exactly 0; a paired basin-day whose label
    the bake-off recorded must carry the build's, or the recipe was fit on other labels (Part B 16), which raises."""
    sc = s2rows[(s2rows["excl"] == "") & (s2rows["entry"] == "oracle")]
    rf = ref.set_index(["tier", "fold", "basin", "date"])
    if rf.index.duplicated().any():
        raise AssertionError("s2_vs_recipe: the served-recipe arm holds a (tier, fold, basin, day) twice")
    out: dict = {"arm_b": "the served recipe refit on the candidate's labels (stages_s2_sfpuc4's served-recipe rows)",
                 "note": "candidate − recipe on identical rows: the candidate's scored rain-known S2 rows the recipe predicts"}
    for t in _windows(sc):
        g = sc[sc["tier"] == t]
        on = rf.reindex(pd.MultiIndex.from_arrays([g["tier"], g["fold"], g["unit"], pd.DatetimeIndex(g["date"])]))
        b = on["p"].to_numpy(dtype=float)
        m = np.isfinite(b)
        if not m.any():
            continue
        if "y" in on.columns:
            ry, gy = on["y"].to_numpy(dtype=float)[m], g["y"].to_numpy(dtype=float)[m]
            off = np.isfinite(ry) & (ry != gy)
            if off.any():
                raise AssertionError(f"s2_vs_recipe {t}: {int(off.sum())} paired basin-days carry another label in the bake-off's "
                                     "rows than in the build's, so the recipe was not refit on the candidate's labels (Part B 16)")
        y, pa, pb = g["y"].to_numpy(dtype=float)[m], _as_written(g["p"].to_numpy(dtype=float)[m]), b[m]
        dates, units = pd.DatetimeIndex(g["date"])[m], g["unit"].to_numpy()[m]
        blk, _ = _storm_blocks(dates, blocks)
        for u in ["pooled"] + list(pd.unique(units)):
            mm = np.ones(len(y), bool) if u == "pooled" else units == u
            out.setdefault(u, {})[t] = {**_delta(y[mm], pa[mm], pb[mm], blk[mm], n_boot), "n_candidate_rows": int(len(g) if u == "pooled"
                                                                                                                 else (g["unit"] == u).sum())}
    return out


def s3b_vs_constant(s3rows: pd.DataFrame, bench: pd.DataFrame, geo, blocks: pd.DataFrame, n_boot: int) -> dict:
    """S3b (protocol §8): the S3 oracle on the Westside split's zones (true occurrence, v̂ from rain), the
    candidate's share − its constant-share benchmark (both fit per fold by stages_s3_links), on the oracle's scored
    rows: {unit | pooled: {window: Δ}}, the pooled cells with the 95% CI Holm's first step reads (protocol §6)."""
    zones = [lk.zone for lk in geo.links_from(S3B_BASIN) if not lk.identity]
    sc = s3rows[(s3rows["excl"] == "") & (s3rows["entry"] == "oracle") & s3rows["unit"].isin(zones)]
    bp = bench.set_index(["tier", "unit", "date"])["p"]
    if bp.index.duplicated().any():
        raise AssertionError("S3b: the benchmark composed a zone-day twice")
    out: dict = {"zones": zones, "arm_b": "the constant share (no size term), fit per fold (stages_s3_links)"}
    for t in _windows(sc):
        g = sc[sc["tier"] == t]
        b = bp.reindex(pd.MultiIndex.from_arrays([g["tier"], g["unit"], pd.DatetimeIndex(g["date"])])).to_numpy(dtype=float)
        if not np.isfinite(b).all():
            raise AssertionError(f"S3b {t}: {int((~np.isfinite(b)).sum())} scored oracle rows have no constant-share benchmark")
        y, pa = g["y"].to_numpy(dtype=float), g["p"].to_numpy(dtype=float)
        units, blk = g["unit"].to_numpy(), _storm_blocks(g["date"], blocks)[0]
        for u in ["pooled"] + list(pd.unique(units)):
            m = np.ones(len(y), bool) if u == "pooled" else units == u
            cell = _delta(y[m], pa[m], b[m], blk[m], n_boot)
            if u == "pooled":
                d95 = V.paired_delta(y[m], pa[m], b[m], blk[m], n=n_boot, seed=SEED, level=HOLM_LEVEL)
                cell["ci95"] = V.clean([d95["lo"], d95["hi"]])
            out.setdefault(u, {})[t] = cell
    return out


def _as_written(x: np.ndarray) -> np.ndarray:
    """Values at rows.csv.gz's precision (FLOAT_FORMAT), so both arms of vs_served are compared alike: two sets with
    the same weights then differ by exactly 0, never by the file's rounding."""
    return np.array([float(FLOAT_FORMAT % v) for v in x], dtype=float)


def _mcb(y, p) -> float:
    """CORP miscalibration (a loss: lower is better), for paired_delta."""
    return V.corp(y, p)["mcb"]


# ── the primaries and the promotion criteria (protocol §8, §9) ──────────────

# §8's rows, by the label of their first cell, as the build names them
PRIMARY_IDS = {"S1": "S1", "S2": "S2", "S2 South floor (Part B 7)": "S2-south-floor", "S2 volume (Part B 7)": "S2-volume",
               "S3a geography": "S3a", "S3b": "S3b", "S4": "S4", "S5": "S5", "OUT": "OUT"}
PRIMARY_STAGE = {"S1": "s1", "S2": "s2", "S2-south-floor": "s2", "S2-volume": "s2", "S3a": "s3", "S3b": "s3", "S4": "s4",
                 "S5": "s5", "OUT": "out"}
HOLM_FAMILY = ("S3a", "S3b")                      # protocol §6: S3a and S3b form one family
HOLM_LEVEL = 0.95                                  # Holm's first step at α/2 = 0.025: the 95% CI's bound
NI_MARGIN = 0.05                                   # protocol §8: non-inferiority at +5% of the incumbent's BS
PASS, FAIL, NYC, NA = "pass", "fail", "not yet computable", "not applicable"
MET, NOT_MET = "met", "not met"


@functools.lru_cache(maxsize=1)
def protocol_rules() -> dict:
    """§8's comparison table and §9's criteria in the frozen protocol's words (STAGES_PROTOCOL.md, its sha checked
    by protocol_stamp): {definitions: {Superiority, Non-inferiority at +5%}, rows: {id: {label, comparison, entry,
    window, rule}}, criteria: [§9's five]}. A row or criterion the build does not score raises."""
    X.protocol_stamp()
    text = X.PROTOCOL.read_text()

    def section(n: int) -> str:
        m = re.search(rf"^## {n}\. [^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        if not m:
            raise ValueError(f"{X.PROTOCOL.name} has no §{n}")
        return m.group(1)
    s8 = section(8)
    defs = dict(re.findall(r"^- \*\*(Superiority|Non-inferiority at \+5%):\*\* (.+)$", s8, re.M))
    rows = {}
    for line in s8.splitlines():
        if not line.startswith("| ") or line.startswith("| stage "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] not in PRIMARY_IDS:
            raise KeyError(f"§8 row {cells[0]!r}: not one the build scores ({list(PRIMARY_IDS)})")
        rows[PRIMARY_IDS[cells[0]]] = dict(zip(("label", "comparison", "entry", "window", "rule"), cells))
    if list(rows) != list(PRIMARY_IDS.values()) or set(defs) != {"Superiority", "Non-inferiority at +5%"}:
        raise ValueError(f"§8 holds rows {list(rows)} and definitions {list(defs)}; the build scores {list(PRIMARY_IDS.values())}")
    crit = re.findall(r"^(\d)\. (.+)$", section(9), re.M)
    if [int(n) for n, _ in crit] != [1, 2, 3, 4, 5]:
        raise ValueError(f"§9 lists criteria {[n for n, _ in crit]}, not 1 … 5")
    return {"definitions": defs, "rows": rows, "criteria": [c for _, c in crit]}


def _numbers(d: dict | None) -> dict:
    """A paired Δ's numbers as a primary row states them: Δ (a − b), its 90% CI, P(Δ < 0), MDE, n, blocks, both arms'
    scores and the verdict words."""
    if not d or d.get("delta") is None:
        return {}
    return {"delta": d["delta"], "ci": [d.get("lo"), d.get("hi")], "level": d.get("level", LEVEL), "p_neg": d.get("p_neg"),
            "mde": d.get("mde"), "mde_pct": d.get("mde_pct"), "n": d.get("n"), "n_blocks": d.get("n_blocks"),
            "bs_a": d.get("a"), "bs_b": d.get("b"), "verdict": d.get("verdict")}


def s4_defect(rows: dict, art: dict, blocks: pd.DataFrame, n_boot: int) -> dict | None:
    """The oracle-worse-than-chained check behind protocol §8's S4 row, both S4s on identical rows: Δ = BS(chained,
    rain known) − BS(oracle), pooled, T2, the candidate's S4 and the served table, on the unit-days all four arms
    scored (the candidate's oracle and chained rows, the served set's oracle and chained rows). The served table
    shows the defect when its Δ's CI is wholly below 0 (protocol §6). {n, candidate, served}; None when there is no
    such row (no S4 rows, or the served build unreadable). The same unit-day with two truths raises."""
    c, srv = rows.get("s4"), art.get("rows")
    if c is None or srv is None or "skipped" in art:
        return None
    s = srv[srv["stage"] == "s4"]

    def scored(f: pd.DataFrame, entry: str) -> pd.DataFrame:
        return f[(f["excl"].fillna("") == "") & (f["entry"] == entry) & (f["tier"] == "T2")].set_index(["unit", "date"])
    arms = {"cand_oracle": scored(c, "oracle"), "cand_rain": scored(c, "rain"), "srv_oracle": scored(s, "oracle"),
            "srv_rain": scored(s, "rain")}
    if any(a.index.duplicated().any() for a in arms.values()):
        raise AssertionError("S4 defect check: a T2 unit-day scored twice in one arm")
    common = functools.reduce(lambda a, b: a.intersection(b), [a.index for a in arms.values()])
    if not len(common):
        return None
    y = arms["cand_oracle"].loc[common, "y"].to_numpy(dtype=float)
    for k, a in arms.items():
        if not np.array_equal(a.loc[common, "y"].to_numpy(dtype=float), y):
            raise AssertionError(f"S4 defect check: {k} carries another truth on the same unit-days")
    p = {k: a.loc[common, "p"].to_numpy(dtype=float) for k, a in arms.items()}
    blk, _ = _storm_blocks(common.get_level_values("date"), blocks)
    return {"n": int(len(common)),
            "candidate": _delta(y, _as_written(p["cand_rain"]), _as_written(p["cand_oracle"]), blk, n_boot),
            "served": _delta(y, p["srv_rain"], p["srv_oracle"], blk, n_boot)}


def s4_status(c4: dict | None, fx: dict | None, sfx: dict | None) -> dict:
    """Protocol §8's S4 rule on its cells: ``c4`` the primary (S4 − the served table, oracle, T2), ``fx`` / ``sfx``
    the candidate's / the served table's chained − oracle on identical rows (``s4_defect``). Superiority passes; so
    does non-inferiority at +5% with the fix (the candidate's oracle not worse than its chained: Δ ≥ 0 and its CI
    not wholly below 0). The fix is tested only where the served table shows the defect (its Δ's CI wholly below
    0); a pass where it does not, or where that cannot be read, carries a caveat (the owner's reading). The caveat
    is S4's alone: OUT does not inherit it. {status, why, caveat, fixed, shown, superior, noninferior, margin}."""
    sup, _ = _test(c4, "superiority")
    ni, mg = _test(c4, "noninferiority")
    fixed = None if not fx or fx.get("delta") is None else bool(fx["delta"] >= 0 and fx.get("verdict") != "better")
    shown = None if not sfx or sfx.get("hi") is None else bool(sfx["hi"] < 0)
    caveat = None
    if NYC in (sup, ni):
        status, why = NYC, "no T2 S4 oracle row both sets scored"
    elif sup == PASS:
        status, why = PASS, "superior: the 90% CI's upper bound is below 0"
    elif ni == PASS and fixed:
        status, why = PASS, "non-inferior at +5%, and its oracle is not worse than its chained composition (rain known, T2)"
        if shown is False:
            caveat = ("the served table shows no oracle-worse-than-chained defect on these first-look rows (its chained − "
                      f"oracle Δ {sfx['delta']:+.4f} [{sfx['lo']:+.4f}, {sfx['hi']:+.4f}], on the unit-days both S4s' oracle "
                      "and chained rows share), so S4's fix is not tested: whether that meets 'a fix for the defect' is the "
                      "owner's reading")
        elif shown is None:
            caveat = "the served table's oracle-vs-chained check is not readable, so whether S4's defect was there to fix is unknown"
        if caveat:
            why += f"; {caveat}"
    elif ni == PASS and fixed is None:
        status, why = NYC, "non-inferior at +5%, but the oracle-vs-chained check has no T2 rows both S4s scored"
    else:
        status, why = FAIL, ("neither superior nor non-inferior at +5%" if ni != PASS else
                             "non-inferior at +5%, but its oracle scores worse than its chained composition (Δ < 0, or its "
                             "CI wholly below 0)")
    return {"status": status, "why": why, "caveat": caveat, "fixed": fixed, "shown": shown, "superior": sup,
            "noninferior": ni, "margin": mg}


def _test(d: dict | None, margin: str) -> tuple:
    """(status, margin value) of a Δ cell under 'superiority' (upper bound < 0) or 'noninferiority' (upper bound <
    5% of arm b's BS); an absent cell or a CI that cannot be computed is not yet computable."""
    if not d or d.get("delta") is None:
        return NYC, None
    hi = d.get("hi")
    m = 0.0 if margin == "superiority" else NI_MARGIN * float(d["b"])
    if hi is None or not np.isfinite(hi):
        return NYC, m
    return (PASS if hi < m else FAIL), m


def _combine(statuses) -> str:
    st = list(statuses)
    if not st:
        return NYC
    if FAIL in st:
        return FAIL
    if NYC in st:
        return NYC
    return PASS


def holm(parts: dict) -> dict:
    """Holm's step-down over one family at one-sided α = 0.05 (protocol §6), on bootstrap CIs: with p the share of
    resamples at or beyond the margin, p ≤ α/2 exactly when the 95% CI's upper bound is below the margin and p ≤ α
    when the 90% CI's is, so the hypothesis with the smaller p passes when its 95% bound clears, and then the other
    when its 90% bound does (two members; one member: its 90% bound). parts: {id: {hi90, hi95, margin}}; a member
    with no bound leaves the family not yet computable."""
    if len(parts) > 2:
        raise ValueError("this Holm step reads two CI levels: a family of at most two")
    if any(p.get("hi90") is None or p.get("hi95") is None or p.get("margin") is None for p in parts.values()):
        return {k: NYC for k in parts}
    pass90 = {k: p["hi90"] < p["margin"] for k, p in parts.items()}
    if len(parts) == 1:
        return {k: PASS if v else FAIL for k, v in pass90.items()}
    pass95 = {k: p["hi95"] < p["margin"] for k, p in parts.items()}
    if not any(pass95.values()):
        return {k: FAIL for k in parts}
    return {k: PASS if pass90[k] else FAIL for k in parts}


def settle_unchanged(rows: list, mine: dict) -> list:
    """Protocol §9.1 judges changed components only, so a row whose component is the served set's (``changed``
    False) is 'not applicable' whatever its numbers say: its computed status is kept as ``computed_status`` and its
    numbers stay (the S2 Δ of a shared S2 is exactly 0; S5's row tests link/zone injection, not a served live_v2)."""
    out = []
    for r in rows:
        if not r["changed"] and r["status"] != NA:
            r = {**r, "computed_status": r["status"], "status": NA,
                 "reason": f"its {r['stage'].upper()} is the served set's ({mine[r['stage']]}), so this row decides nothing for "
                           f"it (protocol §9.1 reads changed components); computed: {r['status']}"
                           + (f" — {r['reason']}" if r.get("reason") else "")}
        out.append(r)
    return out


def post_seen_caveat(row: dict, why: str) -> dict:
    """A primary with a part decided on post-training days (T1, or OUT's T1 post ∪ T0), for a set tagged post_seen:
    protocol §2's tag as a caveat (those days' scores were seen before the set was designed, so they do not confirm
    it). Other rows as they are."""
    if not any(p.get("window") in (POST_WINDOW, OUT_WINDOW) for p in row.get("parts") or ()):
        return row
    cv = f"post_seen (protocol §2): {why}"
    return {**row, "caveat": f"{row['caveat']}; {cv}" if row.get("caveat") else cv}


S4_V3_KIND = "zone_v3"                            # compose_v2's S4 v3 spec kind: the only S4 §8's S4 row tests
if S4_V3_KIND not in C.S4_KINDS:
    raise ImportError(f"compose_v2.S4_KINDS {C.S4_KINDS} lacks {S4_V3_KIND!r}")
SIZE_FREE_SHARES = ("identity", "constant")       # the share kinds with no size term (no s(v̂) for S3b to test)
SIZE_SHARES = tuple(k for k in C.SHARE_KINDS if k not in SIZE_FREE_SHARES)   # compose_v2's kinds with one, never typed
if set(SIZE_FREE_SHARES) - set(C.SHARE_KINDS):
    raise ImportError(f"compose_v2.SHARE_KINDS {C.SHARE_KINDS} lacks {sorted(set(SIZE_FREE_SHARES) - set(C.SHARE_KINDS))}")


def size_shares(cand: StageCandidate) -> bool:
    """Whether the candidate's Westside split shares have a size term in every T2 fold's spec (S3b's s(v̂); protocol
    §8): with identity or constant shares there is no s(v̂) to test against a constant."""
    links = [lk.id for lk in cand.geo.links_from(S3B_BASIN) if not lk.identity]
    t2 = [sp["s3"] for (t, _), sp in cand.specs.items() if t == "T2"]
    return bool(links and t2) and all(s["links"][lid]["share"]["kind"] in SIZE_SHARES for s in t2 for lid in links)


def t0_words(end) -> tuple:
    """(state, words) of T0 in OUT's T1 post ∪ T0 (protocol §2, §8): 'empty' while the data end is before T0's first
    day (the day after the freeze), so T1 post-training stands alone; 'scored' once it is not: T0 is shadow-run like
    T1 (its finals, on the same inputs in both arms), so each OUT part reads the union as one window (OUT_WINDOW)."""
    start, end = X.freeze_date() + pd.Timedelta(days=1), pd.Timestamp(end)
    if end < start:
        return "empty", (f"T0 (prospective, from {start.date()}) holds no scored day: the data end ({end.date()}) is "
                         "before it, so T1 post-training stands alone")
    return "scored", (f"T0 (prospective, from {start.date()}) holds the days to the data end ({end.date()}), shadow-run "
                      "on the same inputs in both arms, so each part reads T1 post ∪ T0 as one window")


def vs_fallback(cand: StageCandidate) -> dict:
    """{basin: declared head − the declared fallback, Δ log-MAE (head, fallback, Δ, its 90% CI, MDE, verdict) and the
    folds where the head is the fallback}, from the bake-off's per-basin comparison on the same folds and event days
    (stages_s2_sfpuc4 vs_fallback); {basin: {why}} where it made none (no fallback is declared for an Oceanside
    basin, Part B 7)."""
    out = {}
    for k, v in ((cand.bakeoff.get("volume") or {}).get("per_basin") or {}).items():
        x = (v or {}).get("vs_fallback") or {}
        d = x.get("delta")
        if isinstance(d, dict) and d.get("delta") is not None:
            out[k] = {"metric": d.get("metric"), "head": d.get("a"), "fallback": d.get("b"), "delta": d["delta"],
                      "ci": [d.get("lo"), d.get("hi")], "level": d.get("level"), "mde": d.get("mde"), "n": d.get("n"),
                      "n_blocks": d.get("n_blocks"), "verdict": d.get("verdict"),
                      "head_is_fallback_in": list(x.get("head_is_fallback_in") or [])}
        else:
            out[k] = {"why": x.get("why") or "the bake-off made no head − fallback comparison for this basin"}
    return out


def served_components(art: dict) -> dict:
    """The served set's components (its written build's manifest, else from its bundle)."""
    if "manifest" in art:
        return dict(art["manifest"]["components"])
    return components(load_set("served"))


def changed_components(bundle: SetBundle, served: dict, served_geo: str) -> dict:
    """{component: whether the candidate's differs from the served set's}: by name, and S2 / S3 by geography too
    (a basin model or a link set under another geography is another component)."""
    mine = components(bundle)
    cross = bundle.geo.version != served_geo
    return {c: bool(mine[c] != served[c] or (cross and c in ("s2", "s3"))) for c in COMPONENTS}


def _row(rid: str, changed: bool, status: str, reason: str, d: dict | None = None, **kw) -> dict:
    words = protocol_rules()["rows"][rid]
    return V.clean({"id": rid, "stage": PRIMARY_STAGE[rid], **{k: words[k] for k in ("label", "comparison", "entry", "window", "rule")},
                    "changed": changed, "status": status, "reason": reason, **_numbers(d), **kw})


def _part(name: str, window: str, status: str, reason: str, d: dict | None = None, **kw) -> dict:
    return V.clean({"part": name, "window": window, "status": status, "reason": reason, **_numbers(d), **kw})


def _s3a_cells(rows: dict, art: dict, blocks: pd.DataFrame, n_boot: int) -> tuple:
    """(Δ at 90%, Δ at 95%) of S3a: East's chained rain-known S3 rows on T2, candidate − served, on identical rows."""
    srv = art["rows"]
    c = rows["s3"]
    a = c[(c["excl"] == "") & (c["entry"] == "rain") & (c["tier"] == "T2") & (c["unit"] == "east")]
    b = srv[(srv["stage"] == "s3") & (srv["excl"].fillna("") == "") & (srv["entry"] == "rain") & (srv["tier"] == "T2")
            & (srv["unit"] == "east")]
    common, y, pa, pb = _pair(a, b, "S3a")
    if not len(common):
        return None, None
    blk, _ = _storm_blocks(common.get_level_values("date"), blocks)
    return _delta(y, pa, pb, blk, n_boot), V.clean(V.paired_delta(y, pa, pb, blk, n=n_boot, seed=SEED, level=HOLM_LEVEL))


def candlestick_sensitivity(rows: dict, art: dict, ctx: X.Context, blocks: pd.DataFrame, n_boot: int) -> dict | None:
    """A2's sensitivity score beside the S4 row: East's S4 oracle, candidate − served, on T2 East zone-days both
    scored, graded against the Candlestick stations alone (stages_s4_v3.candlestick_stations: the East stations
    SFPUC4's station_basin puts in South), first looks read on those stations. Descriptive."""
    import stages_s4_v3 as S4V3  # noqa: PLC0415  (P8c: which stations are Candlestick)
    keys = S4V3.candlestick_stations()
    smp = T._samples(tuple(ctx.sources))
    zd = SMP.zone_sample_days(smp[smp["station"].isin(keys)])
    zd = zd[(zd["zone"] == "east") & zd["first_look"].astype(bool)]
    y = zd.set_index("date")["any_exceedance"].astype(float)
    c, srv = rows["s4"], art["rows"]
    a = c[(c["excl"] == "") & (c["entry"] == "oracle") & (c["tier"] == "T2") & (c["unit"] == "east")]
    b = srv[(srv["stage"] == "s4") & (srv["excl"].fillna("") == "") & (srv["entry"] == "oracle") & (srv["tier"] == "T2")
            & (srv["unit"] == "east")]
    common, _, pa, pb = _pair(a, b, "S4 Candlestick")
    dates = common.get_level_values("date")
    on = dates.isin(y.index)
    if not on.any():
        return None
    yc = y.reindex(dates[on]).to_numpy(dtype=float)
    blk, _ = _storm_blocks(dates[on], blocks)
    return {"stations": [STATIONS[k].sfpuc_id for k in keys], "names": [STATIONS[k].name for k in keys],
            "truth": "any Candlestick station over the standard, first looks on those stations",
            **_numbers(_delta(yc, pa[on], pb[on], blk, n_boot))}


def primaries(bundle: SetBundle, sc: dict, rows: dict, art: dict, ctx: X.Context, blocks: pd.DataFrame, n_boot: int,
              end) -> dict:
    """One row per protocol §8 row (its words, read from the frozen file), with the computed Δ and its 90%
    storm-block CI, P(Δ < 0), MDE, margin and status (pass | fail | not yet computable | not applicable) and why."""
    pr = protocol_rules()
    cand = bundle.stage
    skipped = art.get("skipped")
    served_geo = art["manifest"]["geography"] if "manifest" in art else load_set("served").geo.version
    comps = served_components(art)
    changed = changed_components(bundle, comps, served_geo)
    cross = bundle.geo.version != served_geo
    paired = sc.get("paired") or {}
    vs = paired.get("vs_served") or {}
    no_served = f"the served set's build is not readable ({skipped})" if skipped else None
    out_rows = []
    # S1 · the weather model, chosen on S1 only (set-independent)
    m = components(bundle)["s1"]
    if not changed["s1"]:
        out_rows.append(_row("S1", False, NA, f"the candidate reads the served weather model ({m}): S1 is set-independent (stages_s1)"))
    else:
        s1 = json.loads(S1_SCORES.read_text()) if S1_SCORES.exists() else {}
        cell = ((s1.get("primary") or {}).get("vs_served") or {}).get(m)
        if not cell:
            out_rows.append(_row("S1", True, NYC, f"stages_s1 holds no paired S1 comparison of {m} with the served model"))
        else:
            ok = cell.get("verdict") == "better"
            out_rows.append(_row("S1", True, PASS if ok else FAIL, f"stages_s1's Holm-adjusted verdict: {cell.get('verdict')}",
                                 cell["mae_either_wet"], metric="either-wet MAE, inches"))
    # S2 · within one geography: the served component, or the served recipe refit on the candidate's labels
    if cross:
        src, arm = paired.get("s2_vs_recipe"), "the served recipe refit on the candidate's labels (the bake-off's rows)"
        why_none = "the candidate carries no served-recipe arm (the bake-off's rows)"
    else:
        src, arm = ((vs.get("s2") or {}) if not skipped else None), "the served component"
        why_none = no_served or "no S2 row both sets scored"
        src = {u: {t: c for t, c in (e.get("oracle") or {}).items()} for u, e in (src or {}).items()} if src else None
    nested = cand.nested if cand is not None else False
    c2 = ((src or {}).get("pooled") or {})
    st2, mg2 = _test(c2.get("T2"), "superiority")
    if st2 != NYC and not nested:
        st2, why2 = FAIL, ("its T2 is development (selection-contaminated): its choices were not made nested (protocol §2), "
                           "so T2 cannot confirm it")
    else:
        why2 = why_none if st2 == NYC else ("nested T2: the 90% CI's upper bound " + ("is" if st2 == PASS else "is not") + " below 0")
    st1, mg1 = _test(c2.get("T1"), "noninferiority")
    why1 = why_none if st1 == NYC else ("the 90% CI's upper bound " + ("is" if st1 == PASS else "is not") + " below 5% of the "
                                        "incumbent's BS on the same rows")
    t2_kw = {}
    if cand is not None and cand.bakeoff.get("picked_by_fold"):     # A5: the outer seasons score the procedure
        t2_kw["picked_by_fold"] = dict(cand.bakeoff["picked_by_fold"])
    parts = [_part("superiority", "T2", st2, why2, c2.get("T2"), margin=mg2, nested=nested, **t2_kw),
             _part("non-inferiority at +5%", "T1", st1, why1, c2.get("T1"), margin=mg1)]
    out_rows.append(_row("S2", changed["s2"], _combine([st2, st1]), f"candidate − {arm}, basin-pooled, rain known", c2.get("T2"),
                         parts=parts, arm_b=arm))
    # S2 South floor (Part B 7)
    if "south" not in bundle.geo.keys:
        out_rows.append(_row("S2-south-floor", changed["s2"], NA, f"{bundle.geo.version} has no South basin"))
    else:
        cell = ((sc.get("s2") or {}).get("south") or {}).get("oracle", {}).get("T2")
        lo = ((cell or {}).get("ci") or {}).get("bss", [None, None])[0] if cell else None
        stf = NYC if lo is None else (PASS if lo > 0 else FAIL)
        out_rows.append(_row("S2-south-floor", changed["s2"], stf, "no South T2 score" if lo is None else
                             f"South's T2 BSS {cell['bss']:.3f}, 90% CI lower bound {lo:.3f}" + (" > 0" if stf == PASS else " ≤ 0"),
                             None, bss=(cell or {}).get("bss"), ci=((cell or {}).get("ci") or {}).get("bss"),
                             n=(cell or {}).get("n"), n_pos=(cell or {}).get("n_pos")))
    # S2 volume (Part B 7): a declared fallback must exist and stand in under the floor
    if cand is None:
        out_rows.append(_row("S2-volume", changed["s2"], NA, "an existing GEO_V1 set shares the served bundle's volume heads, "
                             "refit per fold by stages_s2 under the 20-event floor; the declared fallback is a stage candidate's (P8f)"))
    else:
        import stages_s2_sfpuc4 as S2C  # noqa: PLC0415  (the declared fallback's kind)
        fb = sorted(f"{t} {fo} {k}" for (t, fo), ks in cand.heads_used.items() for k, kind in ks.items() if kind == S2C.FALLBACK_KIND)
        vol = ((sc.get("s2_volume") or {}).get("pooled") or {}).get("oracle", {}).get("T2")
        per_basin = vs_fallback(cand)
        parts = [_part("a declared fallback exists and stands in under the floor", "T2", PASS,
                       "every head under the 20-event floor is the declared fallback, in every fold scored: the finals, the "
                       "holdout siblings and each T2 outer fold's head as the bake-off records it (from_saved fails the "
                       "build otherwise)", folds_checked=sorted(f"{t} {fo}" for t, fo in cand.heads_used)),
                 _part("declared head − fallback, Δ log-MAE on event days", "T2", NA,
                       "descriptive: this row's rule is the fallback's existence (protocol §8). Per basin, the bake-off's "
                       "comparison on its nine-season folds and event days (stages_s2_sfpuc4 vs_fallback; development: the "
                       "recipe pick saw those seasons); the build's own T2 log-MAE of the candidate's heads beside it",
                       per_basin=per_basin, t2_log_mae=(vol or {}).get("log_mae"), t2_log_mae_ci=(vol or {}).get("ci"))]
        out_rows.append(_row("S2-volume", changed["s2"], PASS, "the rule is the fallback's existence (protocol §8): met; the "
                             "head − fallback Δ is reported beside it", None, parts=parts, decided_by=parts[0]["part"],
                             fallback_used=fb, heads_used={f"{t} {fo}": v for (t, fo), v in cand.heads_used.items()},
                             bakeoff_volume={k: v.get("picked") for k, v in ((cand.bakeoff.get("volume") or {}).get("per_basin") or {}).items()}))
    # S3a and S3b: one Holm family
    holm_parts, cells = {}, {}
    if not cross:
        out_rows.append(_row("S3a", changed["s3"], NA, "the candidate is the served set's geography: S3a compares geographies"))
    elif skipped or "s3" not in rows:
        holm_parts["S3a"] = {"hi90": None, "hi95": None, "margin": None}        # the family waits for it
        out_rows.append(_row("S3a", changed["s3"], NYC, no_served or "no S3 rows"))
    else:
        d90, d95 = _s3a_cells(rows, art, blocks, n_boot)
        cells["S3a"] = d90
        _, mg = _test(d90, "noninferiority")
        holm_parts["S3a"] = {"hi90": (d90 or {}).get("hi"), "hi95": (d95 or {}).get("hi"), "margin": mg}
        out_rows.append(_row("S3a", True, NYC, "", d90, margin=mg, ci95=[(d95 or {}).get("lo"), (d95 or {}).get("hi")],
                             note="on S3's East rows (the zone overflow day), which no S4 enters: S4 is held fixed by construction"))
    s3b = (paired.get("s3b_vs_constant") or {}).get("pooled", {}).get("T2")
    if paired.get("s3b_vs_constant") is None or (cand is not None and not size_shares(cand)):
        out_rows.append(_row("S3b", changed["s3"], NA, "no Westside share model with a size term to test (S3b reads a stage "
                             "candidate's s(v̂) and its constant benchmark, stages_s3_links)"))
    else:
        cells["S3b"] = s3b
        holm_parts["S3b"] = {"hi90": (s3b or {}).get("hi"), "hi95": ((s3b or {}).get("ci95") or [None, None])[1], "margin": 0.0}
        out_rows.append(_row("S3b", changed["s3"], NYC, "", s3b, margin=0.0, ci95=(s3b or {}).get("ci95")))
    if holm_parts:
        res = holm(holm_parts)
        for r in out_rows:
            if r["id"] in res:
                st = res[r["id"]]
                r["status"] = st
                r["holm"] = {"family": [k for k in HOLM_FAMILY if k in holm_parts], "alpha": 0.05,
                             "unadjusted": _test(cells.get(r["id"]), "superiority" if r["id"] == "S3b" else "noninferiority")[0]}
                waiting = [k for k, hp in holm_parts.items() if hp.get("hi90") is None or hp.get("hi95") is None]
                if st == NYC:
                    r["reason"] = (r["reason"] or "no CI to test") if r["id"] in waiting else f"Holm waits for {waiting}"
                else:
                    r["reason"] = (f"Holm over {r['holm']['family']}: " + ("passes" if st == PASS else "does not pass") +
                                   (" (superiority: the upper bound below 0)" if r["id"] == "S3b" else
                                    " (non-inferiority: the upper bound below 5% of the GEO_V1 BS)"))
    s3s2 = cand.s3_s2 if cand is not None else {}
    for r in out_rows:                                 # which S2 the shares were fit on (Part B 2), stated on S3's rows
        if r["id"] in HOLM_FAMILY and s3s2 and r["status"] != NA:
            r["s3_fit_with_s2"] = s3s2.get("name")
            cv = s3_fit_caveat(s3s2, components(bundle)["s2"], bundle.name)
            if cv:
                r["caveat"] = cv
    # S4 · v3 vs the served table, oracle, T2 (and whether S4 was fit at the sizes S3 gives it: Part B 6). The size
    # caveat is S4's and OUT's (OUT reads the same table); the fix-not-tested caveat is S4's alone.
    size_cv = s4_size_caveat(cand.s4_sizes) if cand is not None else None
    size_kw = {"caveat": size_cv} if size_cv else {}
    kind4 = ((bundle.t1_specs or {}).get("s4") or {}).get("kind")
    if changed["s4"] and kind4 != S4_V3_KIND:          # the row tests S4 v3: another changed S4 has no §8 row
        out_rows.append(_row("S4", True, NA, f"the §8 S4 row tests S4 v3 ({S4_V3_KIND}) against the served table; the "
                             f"candidate's S4 is {components(bundle)['s4']} ({kind4}), which no §8 row scores"))
    elif skipped or "s4" not in rows:
        out_rows.append(_row("S4", changed["s4"], NYC, no_served or "no S4 rows", **size_kw))
    else:
        c4 = ((vs.get("s4") or {}).get("pooled") or {}).get("oracle", {}).get("T2")
        d4 = s4_defect(rows, art, blocks, n_boot) or {}
        fx, sfx = d4.get("candidate"), d4.get("served")
        s4 = s4_status(c4, fx, sfx)
        cv4 = "; ".join(c for c in (size_cv, s4["caveat"]) if c)
        cs = candlestick_sensitivity(rows, art, ctx, blocks, n_boot)
        out_rows.append(_row("S4", changed["s4"], s4["status"], s4["why"], c4, margin=s4["margin"], superior=s4["superior"],
                             noninferior=s4["noninferior"], **({"caveat": cv4} if cv4 else {}),
                             defect={"chained_minus_oracle": _numbers(fx), "fixed": s4["fixed"],
                                     "served_chained_minus_oracle": _numbers(sfx), "served_shows_defect": s4["shown"],
                                     "fix_tested": s4["shown"], "n": d4.get("n"),
                                     "rows": "T2 unit-days the candidate's S4 oracle and chained (rain known) rows and the "
                                             "served table's oracle and chained rows all scored",
                                     "rule": "fixed when the oracle is not worse than chained: Δ = BS(chained) − BS(oracle) ≥ 0 "
                                             "and its CI not wholly below 0 (a report check, design §8 P8); the fix is tested "
                                             "only where the served table shows the defect, its own Δ's CI wholly below 0, "
                                             "both on the same rows"},
                             candlestick=cs or {"status": NYC, "reason": "no T2 East zone-day sampled at a Candlestick station"}))
    # S5 · link/zone injection vs basin_swap (live_v2), both feeds, S5's window. The row tests S5.PRIMARY's first arm:
    # a changed S5 that is another variant has no §8 row, so this one decides nothing for it (§9.1 then reads no primary)
    prim, where = paired.get("s5_primary"), "this build"
    if not prim:
        prim, where = ((art.get("scores") or {}).get("paired") or {}).get("s5_primary"), "the served set's build"
    mine5 = components(bundle)["s5"]
    if changed["s5"] and mine5 != S5.PRIMARY[0]:
        out_rows.append(_row("S5", True, NA, f"the §8 S5 row tests {S5.PRIMARY[0]} against {S5.PRIMARY[1]}; the candidate's "
                             f"S5 is {mine5}, which no §8 row scores"))
    elif not prim:
        out_rows.append(_row("S5", changed["s5"], NYC, no_served or "no S5 primary in this build or the served set's"))
    else:
        perfect = ((prim.get("oracle") or {}).get("S5") or {}).get("pooled")
        seeds = sorted(f for f in prim if f.startswith("degraded:"))
        sd = [((prim[f].get("S5") or {}).get("pooled") or {}) for f in seeds]
        sp, _ = _test(perfect, "superiority")
        if sd and all(c.get("hi") is not None for c in sd):
            deg = {"delta": float(np.mean([c["delta"] for c in sd])), "lo": min(c["lo"] for c in sd), "hi": max(c["hi"] for c in sd),
                   "n": int(round(np.mean([c["n"] for c in sd]))), "b": float(np.mean([c["b"] for c in sd])), "level": LEVEL,
                   "verdict": V.verdict(min(c["lo"] for c in sd), max(c["hi"] for c in sd))}
            sg = PASS if deg["hi"] < 0 else FAIL
        else:
            deg, sg = None, NYC
        parts = [_part("perfect feed (no sibling rows)", "S5", sp, "superiority on the perfect feed", perfect),
                 _part(f"degraded feed ({len(seeds)} seed{'' if len(seeds) == 1 else 's'})", "S5", sg, "superiority on every seed: the highest seed's "
                       "upper bound below 0 (the range runs from the lowest seed's lower bound to the highest's upper)", deg)]
        s5_kw = {}
        if where != "this build" and bundle.geo.version != served_geo:
            s5_kw["caveat"] = (f"computed on the served set's own chain ({served_geo}: its S2 → S4 and links), where basin_swap "
                               "exists, so it tests the correction rule there, not on the candidate's "
                               f"{bundle.geo.version} links and zones; the candidate's own rule is scored against no correction "
                               "in its S5 rows (scores['s5'])")
        out_rows.append(_row("S5", changed["s5"], _combine([sp, sg]), f"computed by stages_s5 on {where} (basin_swap = live_v2 "
                             "exists on GEO_V1's basins only)", perfect, parts=parts, **s5_kw))
    # OUT · candidate vs served, rain known and lead 1, T1 post ∪ T0 (T1 alone while T0 is empty)
    t0_state, t0 = t0_words(end)
    win = "T1" if t0_state == "empty" else OUT_WINDOW
    oparts = []
    for e, words in (("rain", "rain known"), ("L1", "lead 1")):
        c = (((vs.get("out") or {}).get("pooled") or {}).get(e) or {}).get(win) if not skipped else None
        st, mg = _test(c, "noninferiority")
        why = (no_served or f"no {words} {win} OUT row both sets scored" if st == NYC else
               "the 90% CI's upper bound " + ("is" if st == PASS else "is not") + " below 5% of the served set's BS on the same rows")
        oparts.append(_part(f"non-inferiority at +5%, {words}", win, st, why, c, margin=mg, entry=e))
        mcb = (c or {}).get("mcb")
        if not mcb or mcb.get("lo") is None:
            sm, why = NYC, "no MCB CI"
        else:
            sm = FAIL if mcb["lo"] > 0 else PASS
            why = "pooled MCB worse beyond its CI" if sm == FAIL else "pooled MCB not worse beyond its CI"
        oparts.append(_part(f"pooled MCB, {words}", win, sm, why, mcb, entry=e))
    out_rows.append(_row("OUT", True, _combine(p["status"] for p in oparts), "zone-pooled; " + t0,
                         ((((vs.get("out") or {}).get("pooled") or {}).get("rain") or {}).get(win)) if not skipped else None,
                         parts=oparts, t0=t0_state, **size_kw))
    out_rows = settle_unchanged(out_rows, components(bundle))
    if bundle.post_seen:
        out_rows = [post_seen_caveat(r, bundle.post_seen) for r in out_rows]
    return V.clean({"protocol": X.protocol_stamp(), "candidate": bundle.name, "served": served_name(),
                    "geography": {"candidate": bundle.geo.version, "served": served_geo},
                    "components": {"candidate": components(bundle), "served": comps}, "changed": changed,
                    "definitions": pr["definitions"], "statuses": [PASS, FAIL, NYC, NA],
                    "ci": f"{LEVEL:.0%} storm-block bootstrap (B = {n_boot}, seed {SEED}); Δ = candidate − incumbent",
                    "rows": out_rows})


def promotion(prim: dict) -> dict:
    """Protocol §9's criteria, each met | not met | not yet computable with its reason, from the primaries. It
    decides nothing: replacing the served set is the owner's action."""
    crit = protocol_rules()["criteria"]
    rows = {r["id"]: r for r in prim["rows"]}
    changed = [c for c, v in prim["changed"].items() if v]
    out = []
    # 1 · every changed component passes its own-stage primary
    per, words = [], []
    for c in changed:
        mine = [r for r in prim["rows"] if r["stage"] == c and r["status"] != NA]
        if not mine:
            per.append(NOT_MET)
            words.append(f"{c}: no §8 primary scores this change")
            continue
        sts = [r["status"] for r in mine]
        s = NOT_MET if FAIL in sts else (NYC if NYC in sts else MET)
        per.append(s)
        words.append(f"{c}: " + ", ".join(f"{r['id']} {r['status']}" for r in mine))
    s1 = NOT_MET if NOT_MET in per else (NYC if NYC in per else MET)
    caveats = [f"{r['id']}: {r['caveat']}" for r in prim["rows"] if r.get("caveat") and r["stage"] in changed]
    out.append({"n": 1, "criterion": crit[0], "status": s1, "changed": changed,
                "reason": ("; ".join(words) if words else "no component differs from the served set's")
                          + (f". Caveats: {'; '.join(caveats)}" if caveats else "")})
    # 2 · OUT non-inferior on T1 post ∪ T0, rain known and lead 1; 3 · pooled MCB not worse
    o = rows["OUT"]
    for n, key in ((2, "non-inferiority"), (3, "pooled MCB")):
        ps = [p for p in o.get("parts", []) if p["part"].startswith(key)]
        sts = [p["status"] for p in ps]
        s = NOT_MET if FAIL in sts else (NYC if NYC in sts or not sts else MET)
        why = o["reason"].split("; ", 1)[-1].rstrip(".")
        out.append({"n": n, "criterion": crit[n - 1], "status": s,
                    "reason": "; ".join(f"{p['part']}: {p['status']}" for p in ps) + f". {why}."
                              + (f" Caveat: {o['caveat']}." if o.get("caveat") else "")})
    out.append({"n": 4, "criterion": crit[3], "status": NYC,
                "reason": "the per-zone shadow-run Δ report (P11) has not been made, so the owner has not seen it"})
    out.append({"n": 5, "criterion": crit[4], "status": NYC, "reason": "the owner's own decision, made outside this build"})
    return {"rule": "protocol §9: a candidate replaces the served set only when all five hold",
            "note": ("Each criterion is stated as met, not met or not yet computable, with its reason. This block decides "
                     "nothing: replacing the served set is the owner's action (§9), and nothing on this branch changes "
                     "served.json or what the live forecast computes."),
            "statuses": [MET, NOT_MET, NYC], "criteria": out,
            "summary": f"{sum(c['status'] == MET for c in out)} of 5 met; criteria 4 and 5 are the owner's"}


def _repo_path(p) -> str:
    """A manifest's key for a file: repo-relative inside the repository, absolute outside it (a test's temp dir)."""
    p = Path(p)
    return str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)


def manifest(bundle: SetBundle, entries, tiers, steps, model: str, end, n_boot: int) -> dict:
    st = bundle.stage
    extra = {} if st is None else {"stage_candidate": {
        "s2": {"T1": "its finals", "T1-holdout": "its holdout siblings, fit before 2023-07-01",
               "T2": f"the bake-off's outer-fold rows ({st.bakeoff.get('rows')})"},
        "s3": "s3_links.json's per-fold records (stages_s3_links.fold_spec)", "s4": "s4_quality.json's per-fold records",
        "t2_selection": "nested" if st.nested else "not stated nested", "heads": {f"{t} {fo}": v for (t, fo), v in st.heads_used.items()},
        "tags": dict(st.manifest.get("tags") or {})}}
    return {"schema": SCHEMA, "set": bundle.name, "root": bundle.root, "geography": bundle.geo.version,
            "pipeline": bundle.descriptor.get("pipeline", "two_stage_v1"), "components": components(bundle),
            "stage2": bundle.variant, "family": bundle.s2.family, "spec_version": SP.SPEC_VERSION,
            "protocol": X.protocol_stamp(), "built_at": clock.utc_iso(),
            "windows": {"holdout_start": str(S2.HOLDOUT_START.date()), "post_start": str(S2.POST_START.date()),
                        "freeze": str(X.freeze_date().date()), "seasons": list(S2.T2_SEASONS), "data_end": str(end.date()),
                        "tiers": list(tiers), "t2_label": S2.T2_LABEL if st is None else t2_label("s2", bundle.geo, st.nested)},
            "entries": list(entries), "steps": list(steps), "weather_model": model, "bootstrap": {"n": n_boot, "seed": SEED},
            "inputs": {_repo_path(p): input_sha(p) for p in input_files(bundle, model)},
            "inputs_unstamped": {str(Path(p).relative_to(REPO)): list(k) for p, k in UNSTAMPED.items()},
            "code": {str(p.relative_to(REPO)): _sha(p) for p in code_files()}, **extra,
            **({"train_record": bundle.s2.train_record} if st is None and bundle.s2.train_record is not None else {})}


# ── writing ─────────────────────────────────────────────────────────────────

def out_dir(set_name: str) -> Path:
    if not CAND.valid_name(set_name):
        raise ValueError(f"bad set name {set_name!r}")
    return STAGES_DIR / set_name


def rows_frame(rows: dict) -> pd.DataFrame:
    """Every stage's rows in exclusions.COLUMNS, sorted (stage, entry, tier, variant, unit, date) for a stable file."""
    order = {s: i for i, s in enumerate(ROW_STAGES)}
    f = pd.concat([r[list(X.COLUMNS)] for r in rows.values() if len(r)], ignore_index=True)
    f = f.assign(_s=f["stage"].map(order)).sort_values(["_s", "entry", "tier", "variant", "unit", "date"], kind="stable")
    return f.drop(columns="_s").reset_index(drop=True)


def write(b: Build) -> list:
    """manifest.json, scores.json and (the served set and stage candidates: design §7) rows.csv.gz under
    data/models/stages/<set>/."""
    if b.n_boot != B_PROTOCOL:
        raise ValueError(f"scores made with B = {b.n_boot}; the protocol's is {B_PROTOCOL} (protocol §6)")
    if not b.scores:
        raise ValueError("no scores to write: run the scores step")
    d = out_dir(b.bundle.name)
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    if b.bundle.is_served or b.bundle.stage is not None:
        f = rows_frame(b.rows)
        f["date"] = pd.DatetimeIndex(f["date"]).strftime("%Y-%m-%d")
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(f.to_csv(index=False, float_format=FLOAT_FORMAT).encode())
        (d / "rows.csv.gz").write_bytes(buf.getvalue())
        paths.append(d / "rows.csv.gz")
    else:
        stale = d / "rows.csv.gz"
        if stale.exists():
            stale.unlink()
    # allow_nan=False: a NaN that slipped past verify.clean raises here instead of writing invalid JSON
    (d / "scores.json").write_text(json.dumps(b.scores, sort_keys=False, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    (d / "manifest.json").write_text(json.dumps(b.manifest, indent=1, ensure_ascii=False, allow_nan=False))
    return paths + [d / "scores.json", d / "manifest.json"]


def preview(b: Build, path: Path | None = None) -> Path:
    """The scored figure as a standalone HTML page (stages_flowchart.page; logos as file:// paths) at `path`
    (default: the system temp directory, outside the repo). No route serves it."""
    path = Path(path) if path else Path(tempfile.gettempdir()) / f"stages_figure_{b.bundle.name}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    static = (REPO / "app" / "static").as_uri() + "/"
    path.write_text(F.page(b.bundle.geo.version, scores=b.scores.get("figure") or {}, counts=b.scores.get("figure_counts"), static=static))
    return path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--set", required=True, help="a set name, or 'served' for the one served.json names")
    ap.add_argument("--root", default="served", choices=ROOTS)
    ap.add_argument("--entries", default=",".join(ENTRIES))
    ap.add_argument("--tiers", default=None)
    ap.add_argument("--steps", default=",".join(STEPS))
    ap.add_argument("--seasons", default=None, help="T2 seasons to keep (July years; development only)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--preview", nargs="?", const="", default=None, help="write the scored figure to PATH (default: the temp dir)")
    a = ap.parse_args(argv)
    b = build(a.set, a.root, entries=tuple(a.entries.split(",")), tiers=tuple(a.tiers.split(",")) if a.tiers else None,
              steps=tuple(a.steps.split(",")), seasons=tuple(int(s) for s in a.seasons.split(",")) if a.seasons else None)
    fig = b.scores.get("figure") or {}
    for node, v in fig.items():
        if isinstance(v, dict):
            print(f"  {node:6} " + "  ".join(f"{k} {x['v']:+.3f}" for k, x in v.items() if isinstance(x, dict) and x and x.get("v") is not None))
    if fig.get("caption"):
        print(f"  {fig['caption']}")
    if a.write:
        for p in write(b):
            print(f"wrote {p.relative_to(REPO)} ({p.stat().st_size / 1e6:.2f} MB)")
    if a.preview is not None and b.scores:
        print(f"preview {preview(b, a.preview or None)}")


if __name__ == "__main__":
    main()
