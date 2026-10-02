#!/usr/bin/env python3
"""stages_build — every stage of one set, scored on days no fitted component saw
(P7 orchestration; STAGES_DESIGN.md Part C §7 "How the rows are produced" and the
artifacts schema, as amended by Part A A3 (no alert_lines.json) and Part B 1, 2,
9, 14, 16; STAGES_PROTOCOL.md stages_v2 §2–§8, which rules where they differ).

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
one frame, past days from the gauges, I … I+5 from the forecast) and what T0
will grade from forecast_history. So the S4 / OUT row of D at lead L reads, on
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
X-POWER counts the whole window. Blocks are observation events (``s5_blocks``).

**Scores (protocol §4–§6).** Per (stage, unit + pooled, entry, window):
verify.scores_bundle against the climatology of that fold's training days
(unit × calendar month ±1; T1 from all T2 seasons, T1-holdout from the days
before 2023-07-01, a T2 season from the other T2 seasons), pooled by
Hamill–Juras through the per-row reference; storm blocks from truth.blocks;
B = 2,000, seed 0, 90% CIs; threshold scores at the risk-level edges only.
Paired: chained − oracle on the intersection of scored rows; as served − lead L;
the OUT error budget ladder; S5 corrected − plain and the §8 primary
link_zone_swap − basin_swap; and, for a set that is not the served one,
candidate − served on identical rows (``vs_served``, read from the served set's
rows.csv.gz).

**Artifacts (``write``):** data/models/stages/<set>/manifest.json, scores.json,
and rows.csv.gz for the served set only. Nothing else in data/models/ is
written. The manifest holds the sha256 of every input file and of every module
of this repository the build imported (``stale_files``): a change to either
makes the artifacts stale until the build is rerun (the served set first: a
candidate's vs_served reads its rows). ``preview`` draws the scored figure (stages_flowchart.page) into an
HTML file that no route serves (default: the system temp directory, outside the repo).

    venv/bin/python features/forecast/src/models/stages_build.py --set served [--root served|candidates]
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
import stage2 as STG2  # noqa: E402  (read only: the served S3 split fitter)
import stage2_variants as SV  # noqa: E402  (read only: the rain sources the served split was fit on)
import stages_entries as E  # noqa: E402
import stages_flowchart as F  # noqa: E402
import stages_s2 as S2  # noqa: E402
import stages_s5 as S5  # noqa: E402
import stages_spec as SP  # noqa: E402
import train_v4 as T4  # noqa: E402  (read only: the training frames, impact-table fitter, events, samples)
import truth as T  # noqa: E402
import verify as V  # noqa: E402
from shared import clock  # noqa: E402
from shared import geography as G  # noqa: E402
from shared import risk_levels as RL  # noqa: E402
from shared.zones import ZONES  # noqa: E402

SCHEMA = "bwtf.stages/1"
SCORES_SCHEMA = "bwtf.stages.scores/1"
STAGES_DIR = FORECAST / "data" / "models" / "stages"            # the only place this module writes
S1_SCORES = STAGES_DIR / "_s1" / "s1_scores.json"
ROOTS = S2.ROOTS
TIERS = S2.TIERS                                   # T1, T1-holdout, T2 (T0 is graded from forecast_history)
ENTRIES = X.ENTRIES                                # oracle, rain, L0 … L5, L0s, L1s
STEPS = ("s2", "s3", "s4", "out", "s5", "scores")
ROW_STAGES = ("s2", "s3", "s4", "out", "s5")
WARMUP_DAYS = S5.HISTORY_DAYS                      # 8: live_v2's 9-day window; compose needs 7
HISTORY = len(C.LAGS) - 1                          # 7: S4 / OUT read D−7 … D
B_PROTOCOL, SEED, LEVEL = 2000, 0, 0.9             # protocol §6
FIGURE_WINDOW = "T1"                               # the scored figure's window: post-training, confirmation
LADDER = ("truth_at_s4", "truth_at_s3", "rain", "L1")   # protocol §3: the OUT error budget
S5_WINDOW = ("T1-holdout", "T1")                   # protocol §8: 2023-07-01 → the data end
EPS = 1e-12                                        # same-model predictions in different batch sizes (stages_s2)
FLOAT_FORMAT = "%.6g"                              # rows.csv.gz: 6 significant digits (the scores use full precision)


# ── the set ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SetBundle:
    """What a set scores with: its S2 components (stages_s2.S2Set), its stage 2 spec (None = v1, no split), the
    T1 S3 / S4 specs (its own artifacts), and the rain sources the served S3 / S4 fitters read."""
    name: str
    root: str
    s2: S2.S2Set
    stage2: dict | None
    variant: str                                   # 'v1' | 'v2'
    t1_specs: dict                                 # {"s3", "s4"}: compose_v2's GEO_V1 adapter on the set's artifacts
    chosen: dict                                   # basin name → rain source, as stage2_variants fit the split
    descriptor: dict

    @property
    def geo(self) -> G.Geography:
        return self.s2.geo

    @property
    def is_served(self) -> bool:
        return self.root == "served"


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


def load_set(set_name: str, root: str = "served") -> SetBundle:
    """Load the set and assert its stamps: stages_s2.load_set (geography, family, training end, rain sources,
    pickles), then the stage 2 spec the set composes with — the served bundle's stage2.json, which served.json's
    'stage2' must name, or a candidate's (candidates.load_stage2: none = v1), whose manifest must agree."""
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
    variant = (stage2 or {}).get("variant", "v1")
    if variant != want:
        raise ValueError(f"{name}: the descriptor says stage 2 {want!r}, the set's stage2.json is {variant!r}")
    if variant not in ("v1", "v2"):
        raise ValueError(f"{name}: stage 2 {variant!r} has no GEO_V1 adapter")
    if variant == "v2" and not stage2.get("impact_table"):
        raise ValueError(f"{name}: a stage 2 v2 spec without its refit impact table")
    chosen = SV._served_chosen()
    missing = [b.name for b in geo.basins if b.name not in chosen]
    if missing:
        raise KeyError(f"no rain source for the S3 / S4 fit of {missing}")
    return SetBundle(name, root, s2set, stage2, variant, specs, chosen, desc)


def components(bundle: SetBundle) -> dict:
    """Design §2.7: the components behind each stage, by name."""
    return {"s1": E.served_weather_model(), "s2": bundle.s2.stage1,
            "s3": "split_v2" if bundle.variant == "v2" else "basin_v1",
            "s4": "impact_v2" if bundle.variant == "v2" else "impact_v1", "s5": S5.LR.VERSION}


# ── S3 / S4 fitted per fold (Part B 1) ──────────────────────────────────────

def fit_s3_s4(frames: dict, chosen: dict, heads: dict, samples: pd.DataFrame, events: pd.DataFrame, variant: str) -> dict:
    """The served fitters on the frames, events and heads given (a fold's, or the full training window):
    {'raw': the impact table on every discharge day (impact_table.json's recipe), 'stage2': stage 2 v2's
    shares with the impact table refit on the days each GEO_V1 link's own outfalls discharged (stage2.json's
    recipe; None for v1), 'table': the table S4 composes}. Exactly stage2_variants.fit's calls, without its file writes and with no clock
    stamp (fit_outfall_split's fitted_at is dropped). ``heads`` are keyed by basin name."""
    if variant not in ("v1", "v2"):
        raise ValueError(f"stage 2 {variant!r}: v1 | v2")
    raw, _ = T4.fit_impact_table(frames, chosen, heads, samples)
    if variant == "v1":
        return {"raw": raw, "stage2": None, "table": raw}
    spec = STG2.fit_outfall_split(frames, chosen, heads, raw, events)
    table, _ = T4.fit_impact_table(frames, chosen, heads, samples, event_days=STG2.group_event_days(frames, chosen, events))
    spec.pop("fitted_at", None)
    spec["impact_table"] = table
    return {"raw": raw, "stage2": spec, "table": table}


def fold_specs(bundle: SetBundle, fold: S2.Fold, keep, train: dict, events: pd.DataFrame, samples: pd.DataFrame) -> tuple:
    """({"s3", "s4"}, info) for one S2 fold. T1 (keep None): the set's own artifacts. A refit fold: the served
    fitters on the fold's training days only (``keep``, stages_s2's own filter), with the fold's volume heads."""
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


def issue_levels(geo, specs: dict, inputs: C.BasinInputs, kept: pd.DatetimeIndex) -> tuple:
    """(S4 q_z, OUT r_z) date × zone on the kept targets: compose_v2 on the stacked windows, each window's last
    row. The stacking reads no date, so it needs an S4 background that reads none (the GEO_V1 adapter's
    constant); a rain background raises."""
    if specs["s4"]["background"]["kind"] != "constant":
        raise ValueError("issue_inputs stacks windows on a synthetic index: an S4 background that reads the date "
                         "or the rain needs the real 8-day windows")
    comp = C.compose(geo, specs, inputs)
    last = slice(HISTORY, None, HISTORY + 1)
    return (comp.s4.zone.iloc[last].set_axis(kept, axis=0), comp.out.zone.iloc[last].set_axis(kept, axis=0))


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


def _full_history(r: Run) -> pd.DatetimeIndex:
    """The run's emitted days that hold the whole D−7…D history inside the run."""
    return r.window[r.window.isin(r.days[HISTORY:])]


def compose_run(geo, specs: dict, r: Run, ctx: X.Context, tr: Truths, n_sampled: pd.Series, out: Composed,
                steps: tuple, oracle: bool, history: bool = True) -> None:
    """S3, S4 and OUT rows of one run. ``oracle``: the run is the rain-known S2 output used as the oracle
    entry's v̂ (S3: true occurrence; S4 / OUT: the true history). ``history`` False: S3 rows only (a lead
    entry, whose S4 / OUT read the issue day's chain: ``compose_issue``)."""
    z = ctx.frames["zone"]
    full = _full_history(r)
    if not history:
        steps = tuple(s for s in steps if s == "s3")
    else:
        out.dropped[(r.entry, r.tier)] = out.dropped.get((r.entry, r.tier), 0) + int(len(r.window) - len(full))
    if oracle:
        yb = tr.basin_y.reindex(r.days)[list(geo.keys)]
        unknown_b = yb.isna()
        comp = C.compose(geo, specs, C.BasinInputs(r.p, r.v).oracle(yb.fillna(0.0)))
        hist, unknown_h = true_history(geo, specs, tr, r.days, r.v)
        s4 = C.s4(geo, specs["s4"], hist)
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
        comp = C.compose(geo, specs, C.BasinInputs(r.p, r.v))
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
    zq, zr = issue_levels(geo, specs, inputs, kept)
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


def bundle(y, p, ref, blk, storm, strata, n_boot: int) -> dict:
    """One unit × window: verify.scores_bundle plus the sample-climatology BSS, the MDE of a paired Brier
    difference on these rows (2.49 × the block SE of forecast − reference), the storm-block count and X-POWER."""
    y, p, ref = (np.asarray(a, dtype=float) for a in (y, p, ref))
    out = V.scores_bundle(y, p, ref, blk, edges=RL.edges(), n=n_boot, seed=SEED, level=LEVEL)
    n_storm = int(len(np.unique(np.asarray(blk)[np.asarray(storm, bool)]))) if len(y) else 0
    out["bss_sample"] = V.clean(V.bss(y, p, V.sample_ref(y, strata))) if len(y) else None
    out["mde"] = V.clean(V.mde(V.paired_se(y, p, ref, blk)))
    out["n_storm_blocks"] = n_storm
    out["low_power"] = bool(out["n_pos"] < X.POWER_MIN_POSITIVES or n_storm < X.POWER_MIN_STORM_BLOCKS)
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
                                                                    gu["unit"].to_numpy(), n_boot)
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


def score_s5(rows: pd.DataFrame, feeds: dict, n_boot: int) -> tuple:
    """(s5 scores {unit | pooled: {feed: {window: {variant: bundle + delta_vs_plain}}}}, the §8 primary
    {feed: {window: {unit | pooled: link_zone_swap − basin_swap}}}). Windows: each tier, and 'S5' = T1-holdout ∪ T1."""
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
        for u in ["pooled"] + list(pd.unique(units)):
            m = np.ones(len(plain), bool) if u == "pooled" else units == u
            y = plain["y"].to_numpy(dtype=float)[m]
            for v, gv in by_v.items():
                cell = bundle(y, gv["p"].to_numpy(dtype=float)[m], gv["ref"].to_numpy(dtype=float)[m], blk[m],
                              np.ones(int(m.sum()), bool), units[m], n_boot)
                cell.pop("n_storm_blocks")
                cell["low_power"] = bool(cell["n_pos"] < X.POWER_MIN_POSITIVES or cell["n_blocks"] < X.POWER_MIN_STORM_BLOCKS)
                cell["delta_vs_plain"] = _delta(y, gv["p"].to_numpy(dtype=float)[m], gv["b"].to_numpy(dtype=float)[m], blk[m], n_boot)
                out.setdefault(u, {}).setdefault(feed, {}).setdefault(w, {})[v] = cell
            if {"link_zone_swap", "basin_swap"} <= set(by_v):
                a, b = (by_v[k]["p"].to_numpy(dtype=float)[m] for k in S5.PRIMARY)
                prim.setdefault(feed, {}).setdefault(w, {})[u] = _delta(y, a, b, blk[m], n_boot)
    return out, prim


# ── the figure ──────────────────────────────────────────────────────────────

def _pill(cell: dict | None) -> dict | None:
    """A bundle as a figure pill: BSS with its 90% CI, n, positives and X-POWER."""
    if not cell or cell.get("bss") is None:
        return None
    lo, hi = cell["ci"]["bss"]
    return {"v": cell["bss"], "lo": lo, "hi": hi, "n": cell["n"], "pos": cell["n_pos"], "low_power": cell["low_power"]}


def _delta_pill(d: dict | None, n_pos=None, low_power=None) -> dict | None:
    if not d or d.get("delta") is None:
        return None
    return {"v": d["delta"], "lo": d["lo"], "hi": d["hi"], "n": d["n"], "pos": n_pos, "low_power": low_power}


def s1_figure(s1: dict) -> dict:
    """m.s1's pills from stages_s1: the floor (one gauge as a forecast of the other; either-wet MAE is the same
    both ways) and the served model at lead 1 on the two-gauge mean, either-wet MAE (protocol §4.1)."""
    fl = s1["floor"]["SF Oceanside as SF Downtown"]
    m = s1["by_lead"]["1"]["avg"]["models"][s1["served_model"]]
    pick = lambda c: {"v": c["continuous"]["either_wet"]["mae"], "lo": c["ci"]["continuous"]["either_wet"]["mae"][0],  # noqa: E731
                      "hi": c["ci"]["continuous"]["either_wet"]["mae"][1], "n": c["continuous"]["either_wet"]["n"],
                      "pos": c["n_either_wet"], "low_power": c["n_either_wet"] < X.POWER_MIN_POSITIVES}
    return {"oracle": pick(fl), "chained": pick(m)}


def figure(scores: dict, window: str, s1: dict | None, geo) -> dict:
    """figure{node id: {oracle, chained, lead}} for stages_flowchart.render, on one window: pooled BSS per stage
    (oracle vs lead 1), S3's oracle on the zones it scores (the Westside split), OUT's lead strip L0 … L5, and
    S5's change in Brier (the set's own live corrections − none) on the perfect feed and the degraded feeds."""
    pooled = lambda st, e: ((scores.get(st) or {}).get("pooled") or {}).get(e, {}).get(window)  # noqa: E731
    fig = {}
    if s1:
        fig["m.s1"] = s1_figure(s1)
    for st, node in (("s2", "m.s2"), ("s3", "m.s3"), ("s4", "m.s4")):
        if st in scores:
            fig[node] = {"oracle": _pill(pooled(st, "oracle")), "chained": _pill(pooled(st, "L1"))}
    if "out" in scores:
        fig["m.out"] = {"oracle": _pill(pooled("out", "oracle")), "chained": _pill(pooled("out", "L1")),
                        "lead": [_pill(pooled("out", f"L{i}")) for i in range(6)]}
    if "s5" in scores:
        variant = "basin_swap" if geo.version == "geo_v1" else S5.PRIMARY[0]   # what the set serves (Part B 15)
        cell = lambda f: (((scores["s5"].get("pooled") or {}).get(f) or {}).get(window) or {}).get(variant)  # noqa: E731
        o = cell("oracle")
        degr = [cell(f) for f in sorted(scores["s5"]["pooled"]) if f.startswith("degraded:")]
        degr = [c for c in degr if c]
        ch = None
        if degr:
            ds = [c["delta_vs_plain"] for c in degr]
            ch = {"v": float(np.mean([d["delta"] for d in ds])), "lo": min(d["lo"] for d in ds), "hi": max(d["hi"] for d in ds),
                  "n": int(round(np.mean([d["n"] for d in ds]))), "pos": int(round(np.mean([c["n_pos"] for c in degr]))),
                  "low_power": any(c["low_power"] for c in degr)}
        fig["m.s5"] = {"oracle": _delta_pill(o and o["delta_vs_plain"], o and o["n_pos"], o and o["low_power"]), "chained": ch}
    return V.clean(fig)


def figure_counts(geo, as_of, s1: dict | None = None) -> tuple:
    """(chip counts for stages_flowchart, the truth catalog, the claims): exclusions.figure_counts of the catalog
    and the claims as of the data end; the S1 model gaps (X-S1-NWPGAP, a weather-model fact the truth cannot
    count) from stages_s1 for the served model at lead 1 on the two-gauge mean, the chained pill's rows; and the
    slots (n_city_days: days any basin filed an overflow)."""
    cat = X.catalog_counts(geo, as_of=as_of)
    cl = X.claims(geo, as_of=as_of)
    fc = X.figure_counts(cat, cl)
    if s1:
        cell = s1["exclusions"][s1["served_model"]]["previous_runs"][T.MEAN_SERIES]["L1"]["T1"]
        fc["exclusions"]["X-S1-NWPGAP"] = int(cell["excluded"].get("X-S1-NWPGAP", 0))
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
    """The manifest's inputs and code files that are missing or changed since it was written."""
    files = {**manifest_["inputs"], **manifest_.get("code", {})}
    return [f for f, h in files.items() if not (REPO / f).exists() or _sha(REPO / f) != h]


def input_files(bundle: SetBundle, model: str) -> list:
    """Every file the build reads, repo-relative, for the manifest's sha256s."""
    raw = T4.RAW_DIR
    sd = T4.SERVE_DIR if bundle.is_served else CAND.candidate_dir(bundle.name)
    files = [X.PROTOCOL, FORECAST / "live_dashboard.py", S1_SCORES, raw / "historical_rain.csv", raw / "hourly_rain_openmeteo.csv",
             raw / "historical_bacteria.csv", raw / f"openmeteo_prev_runs_{model}.csv", raw / f"openmeteo_hist_forecast_{model}.csv",
             FORECAST / "data" / "csd" / "sf_csd_events.csv", FORECAST / "data" / "csd" / "sf_csd_monthly_coverage.csv",
             FORECAST / "data" / "poobot" / "samples.csv", FORECAST / "data" / "poobot" / "discharge_onsets.csv",
             FORECAST / "data" / "poobot" / "feed_status.csv", T4.SERVE_DIR / "served.json", T4.SERVE_DIR / "eval_report.json",
             T4.SERVE_DIR / "impact_table.json"]
    files += sorted(sd.glob("*_model.pkl")) + sorted(T4.SERVE_DIR.glob("*_volume.pkl"))
    files += [sd / n for n in ("stage2.json", "manifest.json") if (sd / n).exists()]
    import beachwatch as BW
    files += [BW.POSTED_DAYS_CSV, BW.MANIFEST]
    missing = [p for p in files if not p.exists()]
    if missing:
        raise FileNotFoundError(f"inputs missing: {[str(p) for p in missing]}")
    return list(dict.fromkeys(files))


def build(set_name: str = "served", root: str = "served", entries=ENTRIES, tiers=None, steps=STEPS,
          n_boot: int = B_PROTOCOL, seasons=None, feeds=None, s5_tiers=None, log=print) -> Build:
    """Score every stage of one set (see the module notes). ``tiers`` default: every tier the set's family
    refits (gb: T1). Development and test knobs, all off by default: ``seasons`` keeps the T2 seasons listed,
    ``feeds`` the S5 feeds listed, ``s5_tiers`` the tiers S5 replays; ``n_boot`` below the protocol's 2,000 is
    for tests and is refused by ``write``."""
    t_all = time.time()
    entries = tuple(entries)
    bad = [e for e in entries if e not in ENTRIES]
    if bad or not entries:
        raise KeyError(f"entries must be some of {ENTRIES}, not {list(bad) or entries}")
    steps = tuple(steps)
    if not steps or set(steps) - set(STEPS):
        raise KeyError(f"steps must be some of {STEPS}, not {steps}")
    bundle = load_set(set_name, root)
    s2set, geo = bundle.s2, bundle.geo
    if tiers is None:
        tiers = TIERS if s2set.family in S2.REFIT_FAMILIES else ("T1",)
    tiers = tuple(tiers)
    model = E.served_weather_model()
    end = E.data_end()
    log(f"{bundle.name} ({root}, {s2set.family}, stage 2 {bundle.variant}, {geo.version}); tiers {tiers}; entries {entries}; data to {end.date()}")
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
    # 2 · S2 out of fold
    t0 = time.time()
    need = sorted(set(s2set.sources) | set(bundle.chosen.values()) | {"avg"})
    train, _ = T4.build_dataset(sources=need)
    fitted = S2.fit(bundle.name, root, tiers, train_frames=train)
    plan = {(p[0], p[1]): p for p in S2._plan(tiers)}
    folds = [f for f in fitted.folds if f.tier != "T2" or seasons is None or f.season in set(seasons)]
    # rain known always (S5's inputs; every issue day's days before it), and every entry a lead's issue day reads
    need_entries = tuple(dict.fromkeys(("oracle",) + tuple(x for e in entries if e != "rain" for x in issue_entries(e))))
    frames = {}
    for e in need_entries:
        frames[e] = E.frames(e, need, model=model, end=end)
    pred = fitted.predict(frames)
    pred = pred[pred.set_index(["tier", "fold"]).index.isin([(f.tier, f.fold) for f in folds])].reset_index(drop=True)
    log(f"  S2: {len(fitted.folds)} folds fit, {len(pred)} out-of-fold basin-days ({time.time() - t0:.1f}s)")
    # 3 · S3 / S4 specs per fold
    t0 = time.time()
    events, samples = T4.load_events(), T4.load_samples()
    specs, spec_info = {}, {}
    for f in folds:
        specs[(f.tier, f.fold)], spec_info[(f.tier, f.fold)] = fold_specs(bundle, f, plan[(f.tier, f.fold)][5], train, events, samples)
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
            rs = runs_for(s2set, f, e, frames[e], win)
            for r in rs:                                   # the warm-up helper is stages_s2's predict path
                pv = pred_p[e].loc[r.window]
                s2max = max(s2max, float(np.abs(pv.to_numpy() - r.p.loc[pv.index].to_numpy()).max()))
            if e == "oracle":
                if len(rs) != 1:
                    raise AssertionError(f"rain known breaks into {len(rs)} runs in {f.tier} {f.fold}: a gauge day is missing")
                base = s5_runs[(f.tier, f.fold)] = rs[0]
                if "oracle" in entries:
                    compose_run(geo, sp, base, ctx, tr, n_sampled, comp, steps, oracle=True)
                if "rain" in entries:
                    compose_run(geo, sp, dataclasses.replace(base, entry="rain"), ctx, tr, n_sampled, comp, steps, oracle=False)
                continue
            if base is None:
                raise AssertionError(f"{f.tier} {f.fold}: no rain-known run for {e}'s issue days to read")
            for r in rs:                                   # S3 is same-day: the entry's own runs
                compose_run(geo, sp, r, ctx, tr, n_sampled, comp, steps, oracle=False, history=False)
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
    for st in ("s3", "s4", "out"):
        if st in steps and comp.rows[st]:
            rows[st] = apply_stage(pd.concat(comp.rows[st], ignore_index=True), st, ctx_by_tier)
    rung = pd.concat(comp.rung, ignore_index=True) if comp.rung else pd.DataFrame(columns=list(X.COLUMNS) + ["fold"])
    log(f"  exclusions: {', '.join(f'{k} {len(v)}' for k, v in rows.items())} rows ({time.time() - t0:.1f}s)")
    # 6 · S5
    if "s5" in steps and s5_runs:
        t0 = time.time()
        rows["s5"] = s5_build(bundle, specs, [f for f in folds if s5_tiers is None or f.tier in s5_tiers], s5_runs, fd, ctx)
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
        scores = make_scores(bundle, rows, rung, ctx, ctx_by_tier, blocks, fd, dropped, integrity, tr.basin_vol, n_boot, end, log)
        log(f"  scores ({time.time() - t0:.1f}s)")
    man = manifest(bundle, entries, tiers, steps, model, end, n_boot)
    log(f"done in {time.time() - t_all:.1f}s")
    return Build(bundle, rows, scores, man, specs, spec_info, rung, n_boot)


def s5_build(bundle: SetBundle, specs: dict, folds: list, s5_runs: dict, fd: dict, ctx: X.Context) -> pd.DataFrame:
    """S5 rows for every feed and fold on the rain-known S2 output, then exclusions.apply per (feed, tier) so
    X-POWER counts the whole window; the excl column must be the same for every variant (one conditional set)."""
    geo = bundle.geo
    parts = []
    for f in folds:
        r = s5_runs.get((f.tier, f.fold))
        if r is None:
            continue
        inputs = C.BasinInputs(r.p, r.v, r.rain)
        kw = ({"held_out_season": f.season} if f.tier == "T2"
              else {"trained_through": bundle.s2.trained_through if f.tier == "T1" else S2.HOLDOUT_START - pd.Timedelta(days=1)})
        for name, feed in fd.items():
            if name == "watcher" or (name.startswith("degraded") and f.tier not in S5_WINDOW):
                continue                                   # a degraded feed is built for the S5 window only (§8)
            got = S5.s5_rows(geo, specs[(f.tier, f.fold)], inputs, name, feed, tier=f.tier, ctx=ctx, **kw)
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
                fd: dict, dropped: dict, integrity: dict, basin_vol: pd.DataFrame, n_boot: int, end, log) -> dict:
    """scores.json (design §7, protocol §4–§6): per stage {unit | pooled: {entry: {window: bundle}}} with each row's
    reference from its fold's training days; s2_volume; s5 and the paired comparisons; the exclusion counts and
    the partition of the build's rows; the claims and the truth catalog; the figure's counts; the integrity
    checks; the figure per window. No clock, so a rebuild on the same inputs gives the same file."""
    geo = bundle.geo
    out = {"schema": SCORES_SCHEMA, "set": bundle.name, "geography": geo.version, "protocol": S2.protocol_stamp(),
           "as_of": str(end.date()), "windows": {**{t: dict(S2._TIER_TEXT[t]) for t in TIERS},
                                                  "S5": "S5's window, 2023-07-01 → the data end: T1-holdout ∪ T1 (protocol §8)"},
           "bootstrap": {"n": n_boot, "seed": SEED, "level": LEVEL, "protocol": n_boot == B_PROTOCOL},
           "blocks": {"s2-out": "storm blocks from rain (truth.blocks, protocol §6)", "s5": "observation events (s5_blocks)"},
           "reference": "climatology per unit × calendar month ±1 on the fold's training days (protocol §4.2)",
           "aliases": {"s2": {"rain": "oracle"}}, "edges": list(RL.edges())}
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
        out["s5"], paired["s5_primary"] = score_s5(r, fd, n_boot)
        paired["s5_primary_rule"] = ("protocol §8: link_zone_swap − basin_swap on the conditional set, superiority (the 90% CI's "
                                     "upper bound below 0) on the perfect feed and on the degraded feed; window 'S5' = 2023-07-01 → "
                                     "the data end (T1-holdout ∪ T1)")
    if not bundle.is_served:
        paired["vs_served"] = vs_served(rows, blocks, n_boot)
    out["paired"] = paired
    counts = integrity.pop("_counts")
    out["exclusions"] = {st: c["exclusions"].get(st, {}) for st, c in counts.items()}
    out["partition"] = {st: c["partition"].get(st, {}) for st, c in counts.items()}
    s1 = json.loads(S1_SCORES.read_text()) if S1_SCORES.exists() else None
    fc, cat, cl = figure_counts(geo, end, s1)
    out["claims"] = cl
    out["catalog"] = cat
    out["figure_counts"] = fc
    out["integrity"] = integrity
    out["dropped"] = dropped
    out["figures"] = {w: figure(out, w, s1, geo) for w in TIERS}
    out["figure_window"] = FIGURE_WINDOW
    out["figure"] = out["figures"].get(FIGURE_WINDOW, {})
    return V.clean(out)


def vs_served(rows: dict, blocks: pd.DataFrame, n_boot: int) -> dict:
    """Candidate − served on identical rows (Part B 16: both arms built from the same entries and inputs), read
    from the served set's rows.csv.gz: {stage: {unit | pooled: {entry: {window: Δ}}}}; OUT's pooled rain-known and
    lead-1 cells add ΔMCB (CORP miscalibration, protocol §8's "pooled MCB not worse beyond its CI"). Skipped, with
    the reason, when the served set was not written or was built on other data, other code or another protocol."""
    d = STAGES_DIR / served_name()
    if not (d / "rows.csv.gz").exists():
        return {"skipped": "the served set's rows are not written: run --set served --write first"}
    theirs = json.loads((d / "manifest.json").read_text())
    stale = stale_files(theirs) if "code" in theirs else ["(no code stamp)"]
    if stale or theirs["protocol"] != S2.protocol_stamp():
        return {"skipped": f"the served set's rows were built on other inputs or code ({stale or 'protocol'}): rebuild it first"}
    srv = pd.read_csv(d / "rows.csv.gz", parse_dates=["date"], keep_default_na=False, na_values=[""], low_memory=False)
    out: dict = {"served": served_name(), "note": "candidate − served on the unit-days both scored, same entries and inputs "
                                                  "(Part B 16); both arms at rows.csv.gz's 6 significant digits"}
    for st, r in rows.items():
        if st == "s5":
            continue
        s = srv[(srv["stage"] == st) & (srv["excl"].fillna("") == "")]
        c = r[r["excl"] == ""]
        for (e, t), g in c.groupby(["entry", "tier"], sort=False):
            a = g.set_index(["unit", "date"])
            b = s[(s["entry"] == e) & (s["tier"] == t)].set_index(["unit", "date"])
            common = a.index.intersection(b.index)
            if not len(common):
                continue
            ya, yb = a.loc[common, "y"].to_numpy(dtype=float), b.loc[common, "y"].to_numpy(dtype=float)
            if not np.array_equal(ya, yb):
                raise AssertionError(f"vs_served {st} {e} {t}: the same unit-days carry different truths")
            blk, _ = _storm_blocks(common.get_level_values("date"), blocks)
            units = common.get_level_values("unit").to_numpy()
            for u in ["pooled"] + list(pd.unique(units)):
                m = np.ones(len(common), bool) if u == "pooled" else units == u
                pa, pb = _as_written(a.loc[common, "p"].to_numpy(dtype=float)[m]), b.loc[common, "p"].to_numpy(dtype=float)[m]
                cell = _delta(ya[m], pa, pb, blk[m], n_boot)
                if st == "out" and u == "pooled" and e in ("rain", "L1"):
                    cell["mcb"] = V.clean(V.paired_delta(ya[m], pa, pb, blk[m], metric=_mcb, n=n_boot, seed=SEED, level=LEVEL))
                out.setdefault(st, {}).setdefault(u, {}).setdefault(e, {})[t] = cell
    return out


def _as_written(x: np.ndarray) -> np.ndarray:
    """Values at rows.csv.gz's precision (FLOAT_FORMAT), so both arms of vs_served are compared alike: two sets with
    the same weights then differ by exactly 0, never by the file's rounding."""
    return np.array([float(FLOAT_FORMAT % v) for v in x], dtype=float)


def _mcb(y, p) -> float:
    """CORP miscalibration (a loss: lower is better), for paired_delta."""
    return V.corp(y, p)["mcb"]


def manifest(bundle: SetBundle, entries, tiers, steps, model: str, end, n_boot: int) -> dict:
    return {"schema": SCHEMA, "set": bundle.name, "root": bundle.root, "geography": bundle.geo.version,
            "pipeline": bundle.descriptor.get("pipeline", "two_stage_v1"), "components": components(bundle),
            "stage2": bundle.variant, "family": bundle.s2.family, "spec_version": SP.SPEC_VERSION,
            "protocol": S2.protocol_stamp(), "built_at": clock.utc_iso(),
            "windows": {"holdout_start": str(S2.HOLDOUT_START.date()), "post_start": str(S2.POST_START.date()),
                        "freeze": str(X.freeze_date().date()), "seasons": list(S2.T2_SEASONS), "data_end": str(end.date()),
                        "tiers": list(tiers), "t2_label": S2.T2_LABEL},
            "entries": list(entries), "steps": list(steps), "weather_model": model, "bootstrap": {"n": n_boot, "seed": SEED},
            "inputs": {str(p.relative_to(REPO)): _sha(p) for p in input_files(bundle, model)},
            "code": {str(p.relative_to(REPO)): _sha(p) for p in code_files()}}


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
    """manifest.json, scores.json and (served set only) rows.csv.gz under data/models/stages/<set>/."""
    if b.n_boot != B_PROTOCOL:
        raise ValueError(f"scores made with B = {b.n_boot}; the protocol's is {B_PROTOCOL} (protocol §6)")
    if not b.scores:
        raise ValueError("no scores to write: run the scores step")
    d = out_dir(b.bundle.name)
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    if b.bundle.is_served:
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
        print(f"  {node:6} " + "  ".join(f"{k} {x['v']:+.3f}" for k, x in v.items() if isinstance(x, dict) and x and x.get("v") is not None))
    if a.write:
        for p in write(b):
            print(f"wrote {p.relative_to(REPO)} ({p.stat().st_size / 1e6:.2f} MB)")
    if a.preview is not None and b.scores:
        print(f"preview {preview(b, a.preview or None)}")


if __name__ == "__main__":
    main()
