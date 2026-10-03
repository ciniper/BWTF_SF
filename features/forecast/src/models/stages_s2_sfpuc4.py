"""stages_s2_sfpuc4 — stage S2 on the city's four basins: one term-set bake-off,
chosen nested, every weight ≥ 0 (P8a; STAGES_DESIGN.md Part A A5, Part B 1, 7
and 13, Part C §3.2 and §6 changes 4, 5, 7 and 9; STAGES_PROTOCOL.md stages_v3
§2 (the nested rule), §4.2, §6 and the §8 S2 rows).

**What it decides (A5, Chase 2026-10-02).** Which set of terms stage S2 uses on
SFPUC's four basins (sfpuc4_v1: westside, north_shore, central with Islais
Creek, south). Four contenders, each with the same terms in every basin and its
own weights per basin, every weight ≥ 0 (leaderboard.NonNegLogit's objective):

    logit_v1   the served recipe's terms: the 19 rain features + max(0, x − knot)
               hinges on nine of them (leaderboard.HINGES), 38 terms
    half       19 terms (leaderboard.SHARED_DESIGNS['half']): nine inputs in bands
    shared8    8 terms: today's rain in 5 bands, yesterday's in 3
    four       9 terms: today (4 bands), yesterday (3), 2 days ago, the wettest 3 hours

Bends and C are chosen inside each fold, never on the days a fold scores, from a
grid declared here and written out (``Grid.declared``, _bakeoff/grid.json) before
anything is fit: each contender's recorded bends, every bend one step lower, or
every bend one step higher, on round numbers (KNOT_GRID; the number of bends per
input never changes, so a contender always has its own number of terms); C from
C_GRID, per basin. South is fit standalone or pooled with Central — Central's
terms and weights with a South intercept and one scaled slope ≥ 0 (§6 change 5) —
and that choice is made the same way.

**Selection, nested (protocol §2; Part B 1).** Outer folds hold out one July–June
season of 2016-17 … 2024-25 (Westside's first scored season is 2017-18, the
coverage rule). In each outer fold the inner folds hold out each other season
in turn (leave one season out on the eight left); every contender's bends, C per
basin and South mode minimise the inner basin-pooled Brier score, then the
contender is picked by the A5 rule on those inner predictions; the picked one,
refit on all eight seasons, predicts the held-out season. Those outer
predictions are the procedure's T2 rows: what choosing this way scores on
seasons no choice saw. The final pick uses all nine seasons the same way
(leave-one-season-out over nine; development, selection-contaminated, since the
choices maximised these very scores). **The A5 rule, fixed before running:** the
lowest basin-pooled Brier wins; if the 90% storm-block CI (verify.paired_delta,
truth.blocks) of its difference to the next contender includes 0, the one of the
two with fewer terms wins. Every contender is reported nested (its own bends and
C chosen in the inner folds) and development (nine-season), per basin. What the
nesting cannot clean is stated beside it (T2_CAVEAT, TERM_SET_PROVENANCE): each
term set and its recorded bends were chosen before the bake-off, on the record
(half and four on pre-holdout season CV; logit_v1's and shared8's provenance is
unrecorded, so every season), so T1 post-training and T0 confirm. How often a
choice sat on an edge of the declared grid is reported too (``grid_edges``).

**Rows, labels, inputs.** train_v4.build_dataset(geo=sfpuc4_v1) with
``gauge_outage_v1`` on every day (change 9), cross-checked against truth.py
(ledger_known and basin_onsets agree day by day, or it raises). A model is fit
on ledger_known days only: the Poo Bot feed's onsets are never labels (protocol
§1: the archive is not truth), so no basin fits archive days — unlike the served
set's Bay-side models. Scores read S2's first-match exclusions
(exclusions.apply: X-S2-ARCHIVE → X-S2-UNCOV → X-LEDGER-SUSPECT → X-S2-CARRY);
those rules leave days out of scores, not out of fits, as for the served set.
The rain source per basin is the served set's (served.json rain_sources) mapped
to the city's basins by key, and a basin with no served key (South) takes the
served basin all its outfalls were in (GEO_V1 'southeast', SF Downtown — the
gauge Central reads too); every GEO_V1 basin a city basin's outfalls came from
must read the same source, or it raises (``rain_sources``).

**Finals and siblings.** The winner is refit on every ledger_known day before
2025-11-01 (finals, like the served set) and on days before 2023-07-01 (holdout
siblings), through leaderboard's pipelines (bands → standardise → NonNegLogit; the
hinge contender through ``add_hinges_at``), and saved as sfpuc4_<winner>_v1 by
stages_candidates. The bake-off's own fits use the same objective, solved along
the C grid with warm starts (``_nonneg_path``; within 1e-5 of NonNegLogit's p,
tests/test_stages_s2_sfpuc4.py).

**Volume heads (§6 change 7; Part B 7; protocol §8 S2 volume).** Per basin, a
log-linear head (log1p MG | event on log1p of today's rain, yesterday's and the
wettest 3 hours, weights ≥ 0) against the served GBR head recipe, fit on event
days with a measured volume (X-S2-VOLQ rows dropped: a blank or '<' volume on
any of the day's events). §6 change 7's rule: the log-linear head replaces the
GBR recipe unless it is worse on T2 — the 90% storm-block CI of the paired
Δ log-MAE (log-linear − GBR) on nine-season leave-one-season-out event days
wholly above 0 (``pick_recipe``; development: a nested pick would need Westside
heads on six seasons, under the 20-event floor, where no fallback is declared).
A basin under 20 known-volume events in a fit takes the declared fallback: a
pooled Bay-side log-linear head (shared slopes ≥ 0) with a basin offset. A
basin under the floor with no declared fallback (Westside) raises. Protocol
§8's S2 volume comparison — the declared head against the fallback, on the same
folds — is reported for every Bay-side basin (``vs_fallback``). The procedure's
T2 rows carry v̂ from each outer fold's head of the picked recipe, fit without
that season (the recipe pick itself saw every season: development).

**T1 (protocol §8 S2 row) and the South floor.** Every contender's finals and
the served S2 recipe refit on the same rows (the served pickles cloned:
logit_v1 as served, unconstrained) are scored on post-training days; the winner
must be non-inferior at +5% to the served recipe. The served recipe is also
refit leave one season out (its C as served) for protocol §8's T2 superiority
test against the procedure (results["s2_primary"]). South's T2 BSS (the
procedure's rows) must have a 90% CI lower bound above 0. All are reported
pass / fail, never forced. T1-holdout (siblings fit before 2023-07-01, scored
to 2025-10-31) is reported as development: the choices saw those seasons.

No module-level IO. ``run`` returns everything; ``--write`` writes
_bakeoff/grid.json (before fitting), _bakeoff/results.json, _bakeoff/rows.csv.gz
and the candidate. Inside the repository it writes nowhere else, and only A5's
full grid at the protocol's B.

    venv/bin/python features/forecast/src/models/stages_s2_sfpuc4.py [--write] [--n-boot N]
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.base import clone
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (REPO, FORECAST, HERE, HERE.parent / "collectors"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import candidates as CAND  # noqa: E402  (served.json: the served rain sources and recipe)
import exclusions as X  # noqa: E402  (S2's first-match exclusions, the data end, the post-training start)
import leaderboard as L  # noqa: E402  (the terms, NonNegLogit, the pipelines the finals pickle through)
import stages_build as SB  # noqa: E402  (read only: references, bundles, storm blocks, paired deltas)
import stages_candidates as SC  # noqa: E402  (the geography-aware saver)
import stages_s2 as S2  # noqa: E402  (the T2 seasons, the floors, the protocol stamp)
import train_v4 as T  # noqa: E402  (frames, labels, the predict path for volume)
import truth as TR  # noqa: E402
import verify as V  # noqa: E402
from rain_features import GAUGE_OUTAGE_RULE  # noqa: E402
from shared import clock  # noqa: E402
from shared import geography as G  # noqa: E402

GEOGRAPHY = "sfpuc4_v1"
INPUT_RULES = (GAUGE_OUTAGE_RULE["name"],)        # §6 change 9: outage-masked gauges on every day
AS_OF = X.AS_OF                                   # the committed data's end at the freeze (2026-08-17)
TRAIN_END = T.TRAIN_END                           # 2025-10-31: finals, like the served set
HOLDOUT_START = T.HOLDOUT_START                   # 2023-07-01: holdout siblings fit before it
POST_START = X.POST_START                         # 2025-11-01: T1
T2_SEASONS = S2.T2_SEASONS                        # 2016-17 … 2024-25
FEATS = list(L.FEATS)
WEIGHTS_MIN_POSITIVES = S2.WEIGHTS_MIN_POSITIVES  # a fit on fewer discharge days raises
HEAD_MIN_EVENTS = S2.HEAD_MIN_EVENTS              # Part B 7: 20 known-volume events, else the declared fallback
N_BOOT, SEED, LEVEL = SB.B_PROTOCOL, SB.SEED, SB.LEVEL   # protocol §6
NONINFERIOR_MARGIN = 0.05                         # protocol §8: +5% of the incumbent's Brier score
OUT_DIR = SC.ROOT / "_bakeoff"
SCHEMA = "bwtf.stages.s2_sfpuc4/1"
T2_NESTED = "cross-season, nested: every choice made in the inner folds (protocol §2)"
T2_DEVELOPMENT = "development (selection-contaminated): choices made on these nine seasons' own scores"
# What the nested score cannot clean (protocol §2's decision log): the bake-off chooses among four term sets and
# their bends inside the folds, but each term set and its recorded bends were chosen before it, on the record.
TERM_SET_PROVENANCE = {
    "logit_v1": "the served recipe's 19 features and hinge knots (leaderboard.HINGES): provenance unrecorded, so "
                "assume every season (protocol §2's decision log)",
    "half": "leaderboard.SHARED_DESIGNS['half']: shared_logit.backward_select on pre-holdout season-CV log loss "
            "(the seasons through 2022-23, on the GEO_V1 basins)",
    "shared8": "leaderboard.SHARED_DESIGNS['shared8']: shared_logit's analysis of the record (2026-09-30), seasons not "
               "recorded, so assume every season; its notes weigh the holdout against the forward (post-training) test",
    "four": "leaderboard.SHARED_DESIGNS['four']: Chase's four inputs (2026-10-01), bends by shared_logit.choose_bends "
            "on pre-holdout season CV (the seasons through 2022-23, on the GEO_V1 basins)",
}
T2_CAVEAT = ("nested for the bake-off's own choices (contender, bends from the declared grid, C, South mode); the "
             "contenders' term sets and recorded bends were chosen before it on seasons these folds score "
             "(TERM_SET_PROVENANCE), so the nested T2 is not clean of those earlier choices: confirmation is T1 "
             "post-training and T0 (protocol §2)")
T1_CAVEAT = ("the GEO_V1 sets of these term sets (the served logit_v1, logit_v2_half, logit_v2_shared8, "
             "logit_v2_four) were scored on the post-training days before A5 (the Model check), and shared8's notes "
             "weigh the forward test, so T1 is not unseen for the term sets; T0 is the only window no choice saw "
             "(protocol §2)")

# ── the declared grid (A5) ─────────────────────────────────────────────────

CONTENDERS = ("logit_v1", "half", "shared8", "four")       # A5's order
TERM_KIND = {"logit_v1": "hinges", "half": "bands", "shared8": "bands", "four": "bands"}
KNOT_OPTIONS = ("recorded", "low", "high")                 # an exact tie goes to the first
C_GRID = (0.01, 0.03, 0.1, 0.3, 1.0)                       # ascending; an exact tie goes to the smaller C
SOUTH, SOUTH_POOLS_WITH = "south", "central"
SOUTH_MODES = ("standalone", "pooled")                     # an exact tie goes to standalone
POOLED_SLOPE_MAX = 10.0                                    # the scaled slope's bound (a separable fold cannot diverge)
_RECORDED = {"logit_v1": {f: tuple(k) for f, k in L.HINGES.items()},
             "half": dict(L.SHARED_DESIGNS["half"]), "shared8": dict(L.SHARED_DESIGNS["shared8"]),
             "four": dict(L.SHARED_DESIGNS["four"])}
# every bend one step lower / higher on round numbers; inputs not listed keep their (empty) bends
_SHIFTED = {
    "logit_v1": {
        "low": {"precip_avg": (0.1, 0.25, 0.5), "rain_2d_cum": (0.25, 0.5, 1.0), "rain_3d_cum": (0.5, 1.0),
                "rain_7d_cum": (1.0,), "rain_max1h": (0.05, 0.1), "rain_max3h": (0.1, 0.25), "rain_max6h": (0.25, 0.5),
                "antecedent_moisture": (0.1, 0.25), "peak_3d": (0.25, 0.5)},
        "high": {"precip_avg": (0.5, 1.0, 1.5), "rain_2d_cum": (1.0, 1.5, 3.0), "rain_3d_cum": (1.5, 3.0),
                 "rain_7d_cum": (3.0,), "rain_max1h": (0.2, 0.3), "rain_max3h": (0.5, 1.0), "rain_max6h": (1.0, 1.5),
                 "antecedent_moisture": (0.5, 1.0), "peak_3d": (1.0, 1.5)}},
    "half": {
        "low": {"precip_avg": (0.1, 0.25, 0.5), "rain_2d_cum": (0.25, 0.5, 1.0), "rain_3d_cum": (0.5, 1.0), "peak_3d": (0.25, 0.5)},
        "high": {"precip_avg": (0.5, 1.0, 1.5), "rain_2d_cum": (1.0, 1.5, 3.0), "rain_3d_cum": (1.5, 3.0), "peak_3d": (1.0, 1.5)}},
    "shared8": {
        "low": {"precip_avg": (0.25, 0.5, 0.75, 1.0), "rain_lag1d": (0.1, 0.25)},
        "high": {"precip_avg": (0.75, 1.0, 1.5, 2.0), "rain_lag1d": (0.5, 1.0)}},
    "four": {
        "low": {"precip_avg": (0.1, 0.25, 0.5), "rain_lag1d": (0.1, 0.25)},
        "high": {"precip_avg": (0.5, 1.0, 1.5), "rain_lag1d": (0.5, 1.0)}},
}
KNOT_GRID = {c: {"recorded": _RECORDED[c], **{o: {f: _SHIFTED[c][o].get(f, k) for f, k in _RECORDED[c].items()}
                                              for o in ("low", "high")}}
             for c in CONTENDERS}

VOLUME_RECIPES = ("loglinear", "gbr")                      # an exact tie goes to the first (fewer parameters)
VOLUME_TERMS = ("precip_avg", "rain_lag1d", "rain_max3h")  # the log-linear head: log1p of each, weights ≥ 0
FALLBACK_KIND = SC.FALLBACK_KIND                           # "pooled_bayside_loglinear": the saver enforces it under the floor
FALLBACK_FACILITY = "Bayside"                              # Part B 7: the fallback pools the Bay-side basins
FLOAT_FORMAT = "%.6g"


# ── terms ──────────────────────────────────────────────────────────────────

def add_hinges_at(X, hinges: dict):
    """leaderboard.add_hinges with the knots as an argument: the 19 features (a DataFrame, or an ndarray in
    FEATS order) then max(0, x − knot) for every (feature, knot) of ``hinges`` in order. At module level so
    a fitted pipeline pickles (through this module, never __main__; see the entry point)."""
    A = X[FEATS].to_numpy(dtype=float) if hasattr(X, "columns") else np.asarray(X, dtype=float)
    cols = [A]
    for f, ks in hinges.items():
        j = FEATS.index(f)
        for k in ks:
            cols.append(np.maximum(0.0, A[:, j] - k)[:, None])
    return np.hstack(cols)


def design(contender: str, option: str) -> dict:
    if contender not in KNOT_GRID:
        raise KeyError(f"unknown contender {contender!r}; known: {CONTENDERS}")
    if option not in KNOT_GRID[contender]:
        raise KeyError(f"unknown bend option {option!r}; known: {KNOT_OPTIONS}")
    return KNOT_GRID[contender][option]


def term_names(contender: str, option: str) -> list:
    d = design(contender, option)
    if TERM_KIND[contender] == "hinges":
        return FEATS + [f"{f}>{k:g}" for f, ks in d.items() for k in ks]
    return [L.band_name(*c) for c in L.band_columns(d)]


def n_terms(contender: str) -> int:
    return len(term_names(contender, KNOT_OPTIONS[0]))


def design_matrix(contender: str, option: str, F: pd.DataFrame) -> np.ndarray:
    d = design(contender, option)
    return add_hinges_at(F, d) if TERM_KIND[contender] == "hinges" else L.add_bands(F, d)


def make_pipeline(contender: str, option: str, C: float) -> Pipeline:
    """The contender's sklearn pipeline on the 19-input frame serving passes: terms → standardise →
    NonNegLogit(C). Bands through leaderboard.make_shared_model; hinges through add_hinges_at."""
    d = design(contender, option)
    if TERM_KIND[contender] == "bands":
        return L.make_shared_model(C, d)
    return Pipeline([("hinges", FunctionTransformer(add_hinges_at, kw_args={"hinges": d}, validate=False)),
                     ("scale", StandardScaler()), ("lr", L.NonNegLogit(C=C))])


def check_grid_terms() -> dict:
    """{contender: n terms}; raises unless every bend option keeps the recorded inputs, the number of bends
    per input and strictly rising round knots (the contender's terms are its own in every option)."""
    out = {}
    for c in CONTENDERS:
        rec = KNOT_GRID[c]["recorded"]
        for o in KNOT_OPTIONS:
            d = KNOT_GRID[c][o]
            if list(d) != list(rec) or any(len(d[f]) != len(rec[f]) for f in rec):
                raise AssertionError(f"{c} {o}: the bends must keep the recorded inputs and counts")
            for f, ks in d.items():
                if any(b <= a for a, b in zip(ks, ks[1:])) or any(k <= 0 for k in ks):
                    raise AssertionError(f"{c} {o} {f}: knots {ks} must rise and be positive")
        counts = {len(term_names(c, o)) for o in KNOT_OPTIONS}
        if len(counts) != 1:
            raise AssertionError(f"{c}: term counts {counts} differ across bend options")
        out[c] = counts.pop()
    return out


# ── the fast solver (NonNegLogit's objective along the C grid) ─────────────

def _nonneg_path(Z: np.ndarray, y: np.ndarray, Cs) -> np.ndarray:
    """θ = (w, b) per C, rows in Cs order: ½‖w‖² + C·Σ log(1 + e^{−s(Zw + b)}), w ≥ 0, b free — exactly
    leaderboard.NonNegLogit's objective and solver settings, each C started from the previous solution."""
    s = 2.0 * y - 1.0
    k = Z.shape[1]
    out, x0 = [], np.zeros(k + 1)
    for C in Cs:
        def objective(theta, C=C):
            w, b = theta[:k], theta[k]
            m = -s * (Z @ w + b)
            g = -s * expit(m)
            return (0.5 * w @ w + C * np.logaddexp(0.0, m).sum(),
                    np.concatenate([w + C * (Z.T @ g), [C * g.sum()]]))
        res = minimize(objective, x0, jac=True, method="L-BFGS-B", bounds=[(0.0, None)] * k + [(None, None)],
                       options={"maxiter": 5000, "ftol": 1e-12, "gtol": 1e-8})
        out.append(res.x)
        x0 = res.x
    return np.array(out)


def _scale(Xtr: np.ndarray) -> tuple:
    """StandardScaler's mean and scale (population sd; a constant column scales by 1)."""
    mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
    sd = np.where(sd < 10 * np.finfo(float).eps, 1.0, sd)
    return mu, sd


def _fit_pooled(eta: np.ndarray, y: np.ndarray) -> np.ndarray:
    """(a, λ): logit p = a + λ·η on the pooled basin's rows, λ ∈ [0, POOLED_SLOPE_MAX], no penalty."""
    s = 2.0 * y - 1.0

    def objective(t):
        m = -s * (t[0] + t[1] * eta)
        g = -s * expit(m)
        return np.logaddexp(0.0, m).sum(), np.array([g.sum(), (g * eta).sum()])
    rate = min(max(y.mean(), 1e-4), 1 - 1e-4)
    res = minimize(objective, np.array([np.log(rate / (1 - rate)), 1.0]), jac=True, method="L-BFGS-B",
                   bounds=[(None, None), (0.0, POOLED_SLOPE_MAX)], options={"maxiter": 5000, "ftol": 1e-12, "gtol": 1e-8})
    return res.x


# ── data ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Basin:
    """One city basin on every day of the span (the frames' days)."""
    key: str
    name: str
    facility: str
    source: str
    F: pd.DataFrame           # the 19 rain features (outage-masked inputs), index = days
    known: np.ndarray         # bool: ledger_known with a CIWQS label (a fit may read the day)
    y: np.ndarray             # float: the onset label on known days, NaN elsewhere
    excl: np.ndarray          # object: S2's first-match exclusion, '' = scored
    vol: np.ndarray           # float: log1p Σ measured MG on event days with no blank or '<' volume, else NaN
    vol_excl: np.ndarray      # object: the volume table's first match on event days ('' = scored), NaN elsewhere

    @property
    def scored(self) -> np.ndarray:
        return self.excl == ""


@dataclass(frozen=True)
class Data:
    geo: G.Geography
    days: pd.DatetimeIndex
    season: np.ndarray
    basins: dict              # key → Basin, in the geography's order
    blocks: pd.DataFrame      # truth.blocks, date-indexed (block, block_kind)
    pool: pd.DataFrame        # stages_build._pool: the scored unit-days a reference climatology is fit on
    end: pd.Timestamp
    sources: dict
    source_rule: dict

    @property
    def keys(self) -> tuple:
        return tuple(self.basins)


def rain_sources(geo: G.Geography) -> tuple:
    """({city basin: rain source}, {city basin: how it was read}) from served.json's rain_sources: the same key
    when the served set has it, else the one GEO_V1 basin every outfall of the city basin was in. Every GEO_V1
    basin the city basin's outfalls came from must read that source too."""
    served = CAND.served_info()
    if "rain_sources" not in served:
        raise KeyError("served.json states no rain_sources")
    rs = served["rain_sources"]
    v1 = G.get("geo_v1")
    out, rule = {}, {}
    for b in geo.basins:
        parents = sorted({v1.basin_of_outfall(o) for lk in geo.links_from(b.key) for o in lk.outfalls})
        if b.key in rs:
            key = b.key
        elif len(parents) == 1:
            key = parents[0]
        else:
            raise KeyError(f"{b.key}: no served rain source by key, and its outfalls were in {parents}")
        missing = [p for p in parents + [key] if p not in rs]
        if missing:
            raise KeyError(f"served.json has no rain source for {missing}")
        srcs = {rs[p] for p in parents} | {rs[key]}
        if len(srcs) != 1:
            raise ValueError(f"{b.key}: its outfalls' GEO_V1 basins {parents} read different sources {sorted(srcs)}")
        out[b.key] = rs[key]
        rule[b.key] = {"source": rs[key], "served_key": key, "outfalls_were_in": parents,
                       "how": "the same basin key" if key == b.key else f"the served basin all its outfalls were in ({key})"}
    return out, rule


def load_data(end=AS_OF) -> Data:
    """Every city basin's features (outage-masked, the served source), labels, exclusions and volumes on every
    day 2016-03-01 → ``end``; the build_dataset labels must equal truth.py's on every day, or it raises."""
    geo = G.get(GEOGRAPHY)
    end = pd.Timestamp(end)
    sources, rule = rain_sources(geo)
    frames, notes = T.build_dataset(end=end, sources=sorted(set(sources.values())), input_rules=list(INPUT_RULES), geo=geo)
    if notes["input_rules"] != list(INPUT_RULES) or notes.get("geography") != geo.version:
        raise ValueError(f"build_dataset applied {notes['input_rules']} on {notes.get('geography')}")
    days = None
    for src, f in frames.items():
        d = pd.DatetimeIndex(f["date"])
        if days is None:
            days = d
        elif not d.equals(days):
            raise ValueError(f"rain source {src!r} holds other days")
    ctx = X.context(geo, start=days[0], end=end)
    if ctx.start != days[0] or ctx.end != days[-1]:
        raise ValueError(f"the exclusions context spans {ctx.start.date()} → {ctx.end.date()}, the frames "
                         f"{days[0].date()} → {days[-1].date()}")
    ons = TR.basin_onsets(geo, start=days[0], end=end).set_index(["basin", "date"])
    sk = X.skeleton("s2", geo.keys, days[0], days[-1], entry="oracle", tier="T2")
    key = pd.MultiIndex.from_arrays([sk["unit"], pd.DatetimeIndex(sk["date"])])
    sk["y"] = ctx.frames["basin"]["y"].reindex(key).to_numpy(dtype=float)
    excl = X.apply(sk, "s2", ctx).set_index(["unit", "date"])["excl"]
    ev = sk[sk["y"] == 1]
    vexcl = X.apply(ev, "s2", ctx, table="volume").set_index(["unit", "date"])["excl"]
    basins = {}
    for b in geo.basins:
        f = frames[sources[b.key]]
        F = f[FEATS].set_axis(days)
        if not np.isfinite(F.to_numpy(dtype=float)).all():
            raise ValueError(f"{b.key}: a rain feature is missing")
        known = ((f[f"{b.name}_covered"] == 1) & (f[f"{b.name}_label_source"] != "poobot")).to_numpy()
        lab = f[f"{b.name}_csd"].to_numpy(dtype=float)
        o = ons.loc[b.key].reindex(days)
        if o["known"].isna().any():
            raise ValueError(f"{b.key}: truth.basin_onsets lacks days of the frames")
        tk = o["known"].to_numpy(dtype=bool)
        if not np.array_equal(known, tk):
            i = np.flatnonzero(known != tk)[0]
            raise ValueError(f"{b.key}: build_dataset and truth.ledger_known disagree on {days[i].date()}")
        ty = o["y"].astype(float).to_numpy()
        if not np.array_equal(lab[known], ty[known]):
            i = np.flatnonzero(known & (lab != np.nan_to_num(ty, nan=-1)))[0]
            raise ValueError(f"{b.key}: build_dataset and truth.basin_onsets disagree on {days[i].date()}")
        y = np.where(known, ty, np.nan)
        vm = o["volume_mg"].astype(float).to_numpy()
        vok = known & (ty == 1) & ~o["volq"].astype(bool).to_numpy() & np.isfinite(vm)
        e = excl.loc[b.key].reindex(days).to_numpy(dtype=object)
        ve = vexcl.loc[b.key].reindex(days).to_numpy(dtype=object) if b.key in vexcl.index.get_level_values(0) \
            else np.full(len(days), None, dtype=object)
        if pd.isna(e).any():
            raise ValueError(f"{b.key}: a day has no exclusion verdict")
        basins[b.key] = Basin(b.key, b.name, b.facility, sources[b.key], F, known, y, e,
                              np.where(vok, np.log1p(np.where(vok, vm, 0.0)), np.nan), ve)
    return Data(geo, days, T.wet_season(pd.Series(days)).to_numpy(), basins, ctx.blocks,
                SB._pool("s2", ctx, geo, "oracle"), end, sources, rule)


def first_scored_season(data: Data, key: str) -> int:
    """The first T2 season with a ledger_known day of the basin (Westside: 2017-18, its ledger opening 2017-12)."""
    s = data.season[data.basins[key].known]
    return int(max(T2_SEASONS[0], s.min())) if len(s) else None


def train_mask(data: Data, key: str, seasons=None, before=None) -> np.ndarray:
    """Days a fit of `key` reads: known, on or before 2025-10-31, and in ``seasons`` (T2 folds) or before
    ``before`` (holdout siblings). Raises under WEIGHTS_MIN_POSITIVES discharge days."""
    b = data.basins[key]
    m = b.known & (data.days <= TRAIN_END)
    if seasons is not None:
        m &= np.isin(data.season, list(seasons))
    if before is not None:
        m &= data.days < pd.Timestamp(before)
    pos = int(np.nansum(b.y[m]))
    if pos < WEIGHTS_MIN_POSITIVES:
        raise ValueError(f"{key}: {pos} discharge days to fit on in {sorted(seasons) if seasons is not None else before}")
    return m


# ── the grid and the fitted folds ──────────────────────────────────────────

@dataclass(frozen=True)
class Grid:
    """What the bake-off searches. The defaults are A5's grid; tests pass smaller ones."""
    contenders: tuple = CONTENDERS
    knots: tuple = KNOT_OPTIONS
    Cs: tuple = C_GRID
    seasons: tuple = T2_SEASONS
    south_modes: tuple = SOUTH_MODES

    def check(self) -> None:
        if not self.contenders or set(self.contenders) - set(CONTENDERS):
            raise KeyError(f"contenders must be some of {CONTENDERS}")
        if not self.knots or set(self.knots) - set(KNOT_OPTIONS):
            raise KeyError(f"bend options must be some of {KNOT_OPTIONS}")
        if not self.Cs or list(self.Cs) != sorted(set(self.Cs)) or min(self.Cs) <= 0:
            raise ValueError("Cs must be positive and strictly ascending")
        if list(self.seasons) != sorted(set(self.seasons)) or set(self.seasons) - set(T2_SEASONS) or len(self.seasons) < 3:
            raise ValueError(f"seasons: at least three of {T2_SEASONS}, ascending (an outer fold needs an inner LOSO)")
        if not self.south_modes or set(self.south_modes) - set(SOUTH_MODES) or self.south_modes[0] != "standalone":
            raise ValueError(f"south modes must start with 'standalone' and be some of {SOUTH_MODES}")

    def declared(self) -> dict:
        """The grid as written before any fit (_bakeoff/grid.json; the candidate's manifest copies it)."""
        terms = check_grid_terms()
        return {"contenders": {c: {"kind": TERM_KIND[c], "n_terms": terms[c],
                                   "bends": {o: {f: list(k) for f, k in design(c, o).items()} for o in self.knots},
                                   "terms": {o: term_names(c, o) for o in self.knots}} for c in self.contenders},
                "bend_options": list(self.knots), "C_grid": list(self.Cs),
                "seasons": [S2.season_label(s) for s in self.seasons], "south": {"modes": list(self.south_modes),
                "pooled": f"{SOUTH_POOLS_WITH}'s terms and weights, a South intercept and one scaled slope in "
                          f"[0, {POOLED_SLOPE_MAX:g}], unpenalised"},
                "choices": "per contender: bends shared by every basin, C per basin, the South mode; each minimises the "
                           "inner leave-one-season-out basin-pooled Brier score (exact ties: the first option, the "
                           "smaller C, standalone)",
                "rule": "A5: the lowest basin-pooled Brier score wins; if the 90% storm-block CI of its difference to the "
                        "next contender includes 0, the one of the two with fewer terms wins (a CI that cannot be drawn "
                        "counts as including 0)",
                "bootstrap": {"B": N_BOOT, "seed": SEED, "level": LEVEL, "blocks": "truth.blocks (protocol §6)"},
                "volume": {"recipes": list(VOLUME_RECIPES), "loglinear_terms": [f"log1p({t})" for t in VOLUME_TERMS],
                           "floor": HEAD_MIN_EVENTS, "fallback": FALLBACK_KIND, "fallback_facility": FALLBACK_FACILITY,
                           "pick": "per basin, design §6 change 7: log-linear replaces the GBR recipe unless it is worse "
                                   "(the 90% storm-block CI of Δ log-MAE, log-linear − GBR, on nine-season "
                                   "leave-one-season-out event days wholly above 0); development",
                           "vs_fallback": "protocol §8 S2 volume: the picked head − the declared fallback on the same "
                                          "folds, every Bay-side basin; reported, never decided"},
                "input_rules": list(INPUT_RULES), "geography": GEOGRAPHY}


def plan(seasons) -> dict:
    """{(outer, held): training seasons}: outer None = the nine-season LOSO (also each outer fold's refit)."""
    seasons = tuple(seasons)
    out = {}
    for o in (None,) + seasons:
        for s in seasons:
            if s != o:
                out[(o, s)] = frozenset(x for x in seasons if x not in (o, s))
    for (o, s), tr in out.items():
        if s in tr or (o is not None and o in tr):
            raise AssertionError(f"fold ({o}, {s}) trains on a season it holds out")
    return out


@dataclass
class Store:
    """Every fold's predictions on its held-out season, for every (basin, contender, bend option) and C."""
    grid: Grid
    plan: dict
    days_of: dict             # season → day indices
    p: dict = field(default_factory=dict)       # (basin, contender, option) → {(outer, held): (n C, n days)}
    pool: dict = field(default_factory=dict)    # (contender, option) → {(outer, held): (n C of Central, n days)}
    pooled_coef: dict = field(default_factory=dict)   # (contender, option) → {(outer, held): (n C, 2): a, λ}
    seconds: float = 0.0


def fit_store(data: Data, grid: Grid, log=print) -> Store:
    """Fit every fold once (vectorised along C) and keep its held-out season's predictions in memory."""
    grid.check()
    pl = plan(grid.seasons)
    store = Store(grid, pl, {s: np.flatnonzero(data.season == s) for s in grid.seasons})
    t0 = time.time()
    masks = {(k, key): train_mask(data, k, seasons=tr) for k in data.keys for key, tr in pl.items()}
    central = {}
    for k in sorted(data.keys, key=lambda k: k == SOUTH):      # Central before South: the pooled mode reads Central's fits
        b = data.basins[k]
        for c in grid.contenders:
            for o in grid.knots:
                Xd = design_matrix(c, o, b.F)
                out = {}
                for key in pl:
                    m = masks[(k, key)]
                    mu, sd = _scale(Xd[m])
                    th = _nonneg_path((Xd[m] - mu) / sd, b.y[m], grid.Cs)
                    Zs = (Xd[store.days_of[key[1]]] - mu) / sd
                    out[key] = expit(Zs @ th[:, :-1].T + th[:, -1]).T
                    if k == SOUTH_POOLS_WITH:
                        central[(c, o, key)] = (mu, sd, th)
                    if (th[:, :-1] < 0).any():
                        raise AssertionError(f"{k} {c} {o} {key}: a negative weight")
                store.p[(k, c, o)] = out
                if k == SOUTH and SOUTH_POOLS_WITH in data.basins and "pooled" in grid.south_modes:
                    pp, coef = {}, {}
                    for key in pl:
                        mu, sd, th = central[(c, o, key)]
                        m = masks[(k, key)]
                        Z = (Xd - mu) / sd
                        eta = Z @ th[:, :-1].T                   # Central's score (no intercept) per C
                        ab = np.array([_fit_pooled(eta[m, i], b.y[m]) for i in range(len(grid.Cs))])
                        s_idx = store.days_of[key[1]]
                        pp[key] = expit(ab[:, :1] + ab[:, 1:] * eta[s_idx].T)
                        coef[key] = ab
                    store.pool[(c, o)], store.pooled_coef[(c, o)] = pp, coef
        log(f"   {k:12} {len(grid.contenders)} contenders × {len(grid.knots)} bends × {len(pl)} folds × "
            f"{len(grid.Cs)} C fit ({time.time() - t0:.0f}s)")
    store.seconds = time.time() - t0
    return store


# ── choices ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Choice:
    contender: str
    knots: str
    C: dict                   # basin → C (South pooled: Central's C, whose fit it rescales)
    south: str | None         # 'standalone' | 'pooled' (None without a South basin)
    outer: int | None         # the outer fold it was chosen in (None = all seasons)
    brier: float              # the inner basin-pooled Brier score it minimised
    n: int
    basin_brier: dict

    def as_dict(self) -> dict:
        return {"contender": self.contender, "bends": self.knots, "C": dict(self.C), "south": self.south,
                "inner_brier": self.brier, "inner_n": self.n, "inner_basin_brier": dict(self.basin_brier)}

    @classmethod
    def from_dict(cls, d: dict, outer=None) -> "Choice":
        """The Choice an ``as_dict`` record (results.json) holds; an unknown contender or bend option raises."""
        if d["contender"] not in CONTENDERS or d["bends"] not in KNOT_OPTIONS:
            raise KeyError(f"a choice of {d['contender']!r} at bends {d['bends']!r}: not A5's grid")
        return cls(d["contender"], d["bends"], {k: float(v) for k, v in d["C"].items()}, d["south"], outer,
                   float(d["inner_brier"]), int(d["inner_n"]), dict(d["inner_basin_brier"]))


def fold_choices(results: dict) -> dict:
    """{(tier, fold): Choice}: the design the candidate's S2 uses in each protocol §2 fold, as results.json records
    it — T1 (the finals) and T1-holdout (the holdout siblings) the final pick's choice (``winner``, made on all nine
    seasons), each T2 season the choice its outer fold's A5 pick made inside that fold (``nested.folds``), whose
    rows are the procedure's. A fold's later refits (stages_s3_links.candidate_s2) re-use its design."""
    win = results["winner"]
    if win["choice"]["contender"] != win["contender"]:
        raise ValueError("results.json's winner and its choice name different contenders")
    out = {("T1", S2.FOLD_FINAL): Choice.from_dict(win["choice"]),
           ("T1-holdout", S2.FOLD_HOLDOUT): Choice.from_dict(win["choice"])}
    for f in results["nested"]["folds"]:
        if f["choice"]["contender"] != f["picked"]:
            raise ValueError(f"outer fold {f['season']}: picked {f['picked']!r}, its choice is {f['choice']['contender']!r}")
        out[("T2", f["season"])] = Choice.from_dict(f["choice"], outer=S2._season_of(f["season"]))
    want = {S2.season_label(s) for s in T2_SEASONS}
    if {fo for t, fo in out if t == "T2"} != want:
        raise KeyError(f"results.json's outer folds are not the nine T2 seasons {sorted(want)}")
    return out


def _avail(grid: Grid, outer) -> list:
    return [s for s in grid.seasons if s != outer]


def _scored_pos(data: Data, store: Store, key: str, s: int) -> np.ndarray:
    """Positions inside season s's days where the basin's row is scored."""
    return np.flatnonzero(data.basins[key].scored[store.days_of[s]])


def _sse(data: Data, store: Store, P: np.ndarray, key: str, s: int) -> tuple:
    pos = _scored_pos(data, store, key, s)
    y = data.basins[key].y[store.days_of[s][pos]]
    return ((P[:, pos] - y) ** 2).sum(axis=1), len(pos)


def choose(data: Data, store: Store, outer, contender: str) -> Choice:
    """The contender's bends, C per basin and South mode, by the inner leave-one-season-out basin-pooled Brier
    score: the folds (outer, s) for every other season s, scored on s only. ``outer`` None = all seasons."""
    grid = store.grid
    seasons = _avail(grid, outer)
    best = None
    for o in grid.knots:
        sse, n, ci = {}, {}, {}
        for k in data.keys:
            tot, cnt = np.zeros(len(grid.Cs)), 0
            for s in seasons:
                a, m = _sse(data, store, store.p[(k, contender, o)][(outer, s)], k, s)
                tot, cnt = tot + a, cnt + m
            i = int(np.argmin(tot))
            sse[k], n[k], ci[k] = float(tot[i]), cnt, i
        south = None
        if SOUTH in data.keys:
            south = "standalone"
            if "pooled" in grid.south_modes and (contender, o) in store.pool:
                i = ci[SOUTH_POOLS_WITH]
                tot = sum(_sse(data, store, store.pool[(contender, o)][(outer, s)][i:i + 1], SOUTH, s)[0][0] for s in seasons)
                if tot < sse[SOUTH]:
                    south, sse[SOUTH], ci[SOUTH] = "pooled", float(tot), i
        total, N = sum(sse.values()), sum(n.values())
        if not N:
            raise ValueError(f"no scored inner row for outer fold {outer}")
        if best is None or total < best[0]:
            best = (total, N, o, {k: grid.Cs[ci[k]] for k in data.keys}, south,
                    {k: sse[k] / n[k] if n[k] else None for k in data.keys})
    total, N, o, Cb, south, bb = best
    return Choice(contender, o, Cb, south, outer, total / N, N, bb)


def _pred(store: Store, choice: Choice, key: str, fold: tuple) -> np.ndarray:
    """The choice's predictions for one basin on the held-out season of fold (outer, held)."""
    i = store.grid.Cs.index(choice.C[key])
    if key == SOUTH and choice.south == "pooled":
        return store.pool[(choice.contender, choice.knots)][fold][i]
    return store.p[(key, choice.contender, choice.knots)][fold][i]


def inner_rows(data: Data, store: Store, outer) -> pd.DataFrame:
    """The scored rows the inner folds of ``outer`` predict: every other season, every basin (date, unit, y)."""
    parts = []
    for s in _avail(store.grid, outer):
        for k in data.keys:
            pos = _scored_pos(data, store, k, s)
            idx = store.days_of[s][pos]
            parts.append(pd.DataFrame({"date": data.days[idx], "unit": k, "season": s, "y": data.basins[k].y[idx], "pos": pos}))
    return pd.concat(parts, ignore_index=True)


def inner_p(store: Store, rows: pd.DataFrame, choice: Choice) -> np.ndarray:
    out = np.empty(len(rows))
    for (s, k), g in rows.groupby(["season", "unit"], sort=False):
        out[g.index.to_numpy()] = _pred(store, choice, k, (choice.outer, s))[g["pos"].to_numpy()]
    return out


def a5_pick(y, arms: dict, dates, blocks: pd.DataFrame, n_terms_of: dict, n_boot: int = N_BOOT) -> dict:
    """A5's rule on identical rows: {contender: p}. The lowest basin-pooled Brier score wins; if the 90%
    storm-block CI of its difference to the next contender includes 0 (or cannot be drawn), the one of the two
    with fewer terms wins. Every other contender's difference to the lowest is reported beside it."""
    y = np.asarray(y, dtype=float)
    if not len(y) or not np.isfinite(y).all():
        raise ValueError("A5 needs finite outcomes on at least one row")
    bs = {c: float(np.mean((np.asarray(p, dtype=float) - y) ** 2)) for c, p in arms.items()}
    order = sorted(arms, key=lambda c: (bs[c], n_terms_of[c], c))
    ranking = [{"contender": c, "brier": bs[c], "n_terms": n_terms_of[c]} for c in order]
    best = order[0]
    out = {"n": int(len(y)), "ranking": ranking, "lowest": best, "next": None, "delta": None, "ci_includes_0": None,
           "winner": best, "vs_lowest": {}}
    if len(order) == 1:
        out["why"] = "the only contender"
        return out
    blk, _ = SB._storm_blocks(dates, blocks)
    for c in order[1:]:
        out["vs_lowest"][c] = SB._delta(y, np.asarray(arms[c], float), np.asarray(arms[best], float), blk, n_boot)
    nxt = order[1]
    d = SB._delta(y, np.asarray(arms[best], float), np.asarray(arms[nxt], float), blk, n_boot)
    lo, hi = d["lo"], d["hi"]
    includes = lo is None or hi is None or not (np.isfinite(lo) and np.isfinite(hi)) or (lo <= 0 <= hi)
    winner = min((best, nxt), key=lambda c: (n_terms_of[c], order.index(c))) if includes else best
    if not includes:
        why = "excludes 0, so it wins"
    elif winner == best:
        why = f"includes 0, and {best} has the fewer terms ({n_terms_of[best]} vs {n_terms_of[nxt]}), so it wins"
    else:
        why = f"includes 0, so the one with fewer terms wins: {winner} ({n_terms_of[winner]} vs {n_terms_of[best]})"
    out.update(next=nxt, delta=d, ci_includes_0=bool(includes), winner=winner,
               why=f"{best} has the lowest Brier score; the CI of its difference to {nxt} {why}")
    return out


def grid_edges(grid: Grid, choices: list) -> dict:
    """How often a choice sat on an edge of the declared grid ({contender: {basin: {'C_min', 'C_max'}, 'bends':
    {option: n}, 'n'}}) over the given folds' {contender: Choice.as_dict()}: an optimum on an edge may lie past it.
    Descriptive; it decides nothing (the grid was declared before running)."""
    out = {}
    for c in grid.contenders:
        got = [f[c] for f in choices if c in f]
        per = {"n": len(got), "bends": {o: sum(g["bends"] == o for g in got) for o in grid.knots}}
        for k in (got[0]["C"] if got else {}):
            per[k] = {"C_min": sum(g["C"][k] == min(grid.Cs) for g in got), "C_max": sum(g["C"][k] == max(grid.Cs) for g in got)}
        out[c] = per
    return out


# ── the nested procedure ───────────────────────────────────────────────────

def season_rows(data: Data, store: Store, choice: Choice, s: int, arm: str) -> pd.DataFrame:
    """The choice refit on every season but s (fold (None, s)) predicting every day of s, every basin."""
    idx = store.days_of[s]
    parts = []
    for k in data.keys:
        b = data.basins[k]
        parts.append(pd.DataFrame({"arm": arm, "contender": choice.contender, "date": data.days[idx], "unit": k,
                                   "tier": "T2", "fold": S2.season_label(s), "p": _pred(store, choice, k, (None, s)),
                                   "y": b.y[idx], "excl": b.excl[idx]}))
    return pd.concat(parts, ignore_index=True)


def nested(data: Data, store: Store, n_boot: int = N_BOOT, log=print) -> dict:
    """Per outer fold: every contender's inner choice, the A5 pick on the inner rows, and the outer rows of the
    procedure and of each contender on its own (its bends and C chosen inside)."""
    grid = store.grid
    terms = {c: n_terms(c) for c in grid.contenders}
    folds, proc, own = [], [], {c: [] for c in grid.contenders}
    for o in grid.seasons:
        rows = inner_rows(data, store, o)
        ch = {c: choose(data, store, o, c) for c in grid.contenders}
        arms = {c: inner_p(store, rows, ch[c]) for c in grid.contenders}
        pick = a5_pick(rows["y"].to_numpy(), arms, rows["date"], data.blocks, terms, n_boot)
        w = pick["winner"]
        folds.append({"season": S2.season_label(o), "picked": w, "choice": ch[w].as_dict(),
                      "inner": {c: ch[c].as_dict() for c in grid.contenders}, "rule": pick,
                      "inner_seasons": [S2.season_label(s) for s in _avail(grid, o)]})
        proc.append(season_rows(data, store, ch[w], o, "procedure"))
        for c in grid.contenders:
            own[c].append(season_rows(data, store, ch[c], o, f"nested:{c}"))
        log(f"   outer {S2.season_label(o)}: {w} ({ch[w].knots} bends, C {ch[w].C}, South {ch[w].south}) — {pick['why']}")
    return {"folds": folds, "procedure": pd.concat(proc, ignore_index=True),
            "by_contender": {c: pd.concat(v, ignore_index=True) for c, v in own.items()}}


def development(data: Data, store: Store, n_boot: int = N_BOOT) -> dict:
    """Every contender chosen on all nine seasons (leave one out over nine) and A5's final pick on those rows."""
    grid = store.grid
    terms = {c: n_terms(c) for c in grid.contenders}
    rows = inner_rows(data, store, None)
    ch = {c: choose(data, store, None, c) for c in grid.contenders}
    arms = {c: inner_p(store, rows, ch[c]) for c in grid.contenders}
    pick = a5_pick(rows["y"].to_numpy(), arms, rows["date"], data.blocks, terms, n_boot)
    out_rows = {c: pd.concat([season_rows(data, store, ch[c], s, f"dev:{c}") for s in grid.seasons], ignore_index=True)
                for c in grid.contenders}
    return {"choices": ch, "pick": pick, "rows": out_rows}


def check_out_of_fold(rows: pd.DataFrame, store: Store, data: Data) -> None:
    """Raise unless every T2 row's fit never read its season: its fold (None, s) trains on seasons without s,
    and its date lies in s; T1 rows lie after the training end, T1-holdout rows from 2023-07-01."""
    pl = store.plan
    t2 = rows[rows["tier"] == "T2"]
    season = T.wet_season(pd.Series(pd.DatetimeIndex(t2["date"]))).to_numpy()
    folds = t2["fold"].to_numpy()
    for fold in pd.unique(folds):
        s = S2._season_of(fold)
        if (None, s) not in pl:
            raise ValueError(f"T2 rows of fold {fold}, which was never fit")
        if s in pl[(None, s)]:
            raise ValueError(f"X-ALL-INSAMPLE: fold {fold} trained on its own season")
        if (season[folds == fold] != s).any():
            raise ValueError(f"T2 fold {fold}: a row outside its season")
    for k in data.keys:
        for (o, s), tr in pl.items():
            m = train_mask(data, k, seasons=tr)
            if np.isin(data.season[m], [s] + ([o] if o is not None else [])).any():
                raise ValueError(f"X-ALL-INSAMPLE: {k} fold ({o}, {s}) reads a day of a season it holds out")
    t1 = rows[rows["tier"] == "T1"]
    if len(t1) and (pd.DatetimeIndex(t1["date"]) <= TRAIN_END).any():
        raise ValueError("a T1 row on or before the training end")
    th = rows[rows["tier"] == "T1-holdout"]
    if len(th) and ((pd.DatetimeIndex(th["date"]) < HOLDOUT_START) | (pd.DatetimeIndex(th["date"]) > TRAIN_END)).any():
        raise ValueError("a T1-holdout row outside 2023-07-01 → 2025-10-31")
    if "T3" in set(rows["tier"]):
        raise ValueError("X-ALL-INSAMPLE: a T3 row")


def check_fit_spans(finals: dict, holdouts: dict) -> None:
    """Raise unless every final model or head ({what: {basin: dict}}) was fit on days through 2025-10-31 and every
    holdout sibling on days before 2023-07-01: T1 and T1-holdout rows come from fits that never read them."""
    for fits, last, what in ((finals, TRAIN_END, "on or before the training end"),
                             (holdouts, HOLDOUT_START - pd.Timedelta(days=1), "before the holdout start")):
        for name, per in fits.items():
            for k, d in per.items():
                if not d.get("span") or pd.Timestamp(d["span"][1]) > last:
                    raise ValueError(f"X-ALL-INSAMPLE: {name} {k} was fit on {d.get('span')}, not only {what}")


# ── scores ─────────────────────────────────────────────────────────────────

def score(rows: pd.DataFrame, data: Data, n_boot: int = N_BOOT) -> dict:
    """{basin | 'pooled': stages_build.bundle} on the scored rows (excl ''): BSS against the training fold's
    climatology (protocol §4.2, stages_build.references), storm blocks, 90% CIs, X-POWER."""
    sc = rows[rows["excl"] == ""].reset_index(drop=True)
    if not len(sc):
        raise ValueError("no scored row")
    if not np.isfinite(sc["y"].to_numpy(dtype=float)).all():
        raise ValueError("a scored row has no truth")
    ref = SB.references(sc, data.pool)
    blk, storm = SB._storm_blocks(sc["date"], data.blocks)
    out = {}
    for u in ["pooled"] + [k for k in data.keys if (sc["unit"] == k).any()]:
        m = np.ones(len(sc), bool) if u == "pooled" else (sc["unit"] == u).to_numpy()
        out[u] = SB.bundle(sc["y"][m], sc["p"][m], ref[m], blk[m], storm[m], sc["unit"].to_numpy()[m], n_boot,
                           sc["date"][m])
    return out


def paired(a: pd.DataFrame, b: pd.DataFrame, data: Data, n_boot: int = N_BOOT) -> dict:
    """{basin | 'pooled': Δ = BS(a) − BS(b)} on the unit-days both score, + non-inferiority at +5%."""
    ka = a[a["excl"] == ""].set_index(["unit", "date"])
    kb = b[b["excl"] == ""].set_index(["unit", "date"])
    common = ka.index.intersection(kb.index)
    if not len(common):
        raise ValueError("no unit-day both arms score")
    A, B = ka.loc[common], kb.loc[common]
    if not np.array_equal(A["y"].to_numpy(dtype=float), B["y"].to_numpy(dtype=float)):
        raise AssertionError("the same unit-days carry different truths")
    units = common.get_level_values("unit").to_numpy()
    blk, _ = SB._storm_blocks(common.get_level_values("date"), data.blocks)
    out = {}
    for u in ["pooled"] + list(pd.unique(units)):
        m = np.ones(len(common), bool) if u == "pooled" else units == u
        out[u] = SB._delta(A["y"].to_numpy(dtype=float)[m], A["p"].to_numpy(dtype=float)[m],
                           B["p"].to_numpy(dtype=float)[m], blk[m], n_boot)
    return out


# ── finals, siblings, the served recipe ────────────────────────────────────

def fit_final(data: Data, choice: Choice, before=None, seasons=None, keys=None) -> dict:
    """{basin: model dict} for the choice through leaderboard's pipelines on every known day on or before
    2025-10-31 (``before`` None: finals) or before ``before`` (holdout siblings). South pooled: Central's
    pipeline with its weights scaled by λ ≥ 0 and South's intercept a, fit on South's rows. ``seasons`` (July
    years, as train_mask reads them) fits on those seasons' days only and ``keys`` only those basins (South
    pooled needs Central among them): a fold's design refit on the season sets its S3 asks for
    (stages_s3_links.candidate_s2). The defaults are the finals and siblings, unchanged."""
    keys = tuple(data.keys) if keys is None else tuple(keys)
    unknown = [k for k in keys if k not in data.basins]
    if unknown or not keys:
        raise KeyError(f"fit_final: basins {unknown or list(keys)} are not {list(data.keys)}")
    if SOUTH in keys and choice.south == "pooled" and SOUTH_POOLS_WITH not in keys:
        raise ValueError(f"pooled {SOUTH} rescales {SOUTH_POOLS_WITH}'s fit: fit both")
    out = {}
    terms = term_names(choice.contender, choice.knots)
    for k in sorted(keys, key=lambda k: k == SOUTH):
        b = data.basins[k]
        m = train_mask(data, k, seasons=seasons, before=before)
        info = {"features": list(FEATS), "calibration_offset": 0.0, "rain_source": b.source, "family": "logit",
                "label": "csd_event_reported", "contender": choice.contender, "bends": choice.knots,
                "design": {f: list(v) for f, v in design(choice.contender, choice.knots).items()}, "terms": terms,
                "n_rows": int(m.sum()), "n_events": int(np.nansum(b.y[m])),
                "span": [str(data.days[m].min().date()), str(data.days[m].max().date())]}
        if k == SOUTH and choice.south == "pooled":
            cen = out[SOUTH_POOLS_WITH]["model"]
            Z = cen[:-1].transform(b.F[m])
            w = cen.named_steps["lr"].coef_[0]
            a, lam = _fit_pooled(Z @ w, b.y[m])
            lr = L.NonNegLogit(C=cen.named_steps["lr"].C)
            lr.classes_, lr.coef_, lr.intercept_, lr.n_iter_ = np.array([0, 1]), (lam * w)[None, :], np.array([a]), np.array([0])
            model = Pipeline([(n, copy.deepcopy(s)) for n, s in cen.steps[:-1]] + [("lr", lr)])
            out[k] = {"model": model, **info, "C": choice.C[SOUTH_POOLS_WITH], "south": "pooled",
                      "pooled_with": SOUTH_POOLS_WITH, "pooled_intercept": float(a), "pooled_slope": float(lam)}
        else:
            model = make_pipeline(choice.contender, choice.knots, choice.C[k]).fit(b.F[m], b.y[m].astype(int))
            out[k] = {"model": model, **info, "C": choice.C[k], **({"south": "standalone"} if k == SOUTH else {})}
        sc = out[k]["model"].named_steps["scale"]
        out[k]["slope_per_unit"] = {t: round(float(c / s), 5) for t, c, s in
                                    zip(terms, out[k]["model"].named_steps["lr"].coef_[0], sc.scale_)}
    return out


def served_recipe(data: Data) -> dict:
    """{city basin: the served pickle dict} by the rain-source mapping (rain_sources): the recipe a refit clones."""
    import pickle
    out = {}
    for k in data.keys:
        sk = data.source_rule[k]["served_key"]
        p = T.SERVE_DIR / f"{sk}_model.pkl"
        if not p.exists():
            raise FileNotFoundError(f"the served bundle has no {p.name}")
        with open(p, "rb") as f:
            m = pickle.load(f)
        if m["rain_source"] != data.sources[k]:
            raise ValueError(f"{k}: the served {sk} pickle reads {m['rain_source']!r}, served.json {data.sources[k]!r}")
        if m["calibration_offset"] != 0.0:
            raise ValueError(f"{k}: the served {sk} model carries a fitted dry-day offset; a refit would score days it saw")
        out[k] = {**m, "served_key": sk}
    return out


def served_fit(data: Data, recipe: dict, seasons=None, before=None) -> dict:
    """{basin: fitted clone of the served recipe} on the same rows the contenders fit (identical inputs)."""
    return {k: clone(recipe[k]["model"]).fit(data.basins[k].F[recipe[k]["features"]][m], data.basins[k].y[m].astype(int))
            for k in data.keys for m in [train_mask(data, k, seasons=seasons, before=before)]}


def window_rows(data: Data, models: dict, lo, hi, tier: str, arm: str, contender: str, fold: str) -> pd.DataFrame:
    """Rows of every basin on days lo → hi from fitted models ({basin: estimator or model dict})."""
    sel = (data.days >= pd.Timestamp(lo)) & (data.days <= pd.Timestamp(hi))
    parts = []
    for k in data.keys:
        b = data.basins[k]
        m = models[k]["model"] if isinstance(models[k], dict) else models[k]
        p = m.predict_proba(b.F[FEATS][sel])[:, 1]
        parts.append(pd.DataFrame({"arm": arm, "contender": contender, "date": data.days[sel], "unit": k, "tier": tier,
                                   "fold": fold, "p": p, "y": b.y[sel], "excl": b.excl[sel]}))
    return pd.concat(parts, ignore_index=True)


def served_t2_rows(data: Data, recipe: dict, seasons) -> pd.DataFrame:
    """The served recipe, leave one season out (its C as served), on every day of each held-out season."""
    parts = []
    for s in seasons:
        fit = served_fit(data, recipe, seasons=[x for x in seasons if x != s])
        lo, hi = pd.Timestamp(s, 7, 1), pd.Timestamp(s + 1, 6, 30)
        parts.append(window_rows(data, fit, lo, hi, "T2", "served_recipe", "served_recipe", S2.season_label(s)))
    return pd.concat(parts, ignore_index=True)


# ── volume heads ───────────────────────────────────────────────────────────

def loglinear_head() -> Pipeline:
    return Pipeline([("log1p", FunctionTransformer(np.log1p, validate=True)), ("ols", LinearRegression(positive=True))])


def served_head_recipe() -> tuple:
    """(regressor, features) of the served GBR heads; every served head must share one recipe."""
    heads, _ = T.stage2_from_served()
    if not heads:
        raise FileNotFoundError("the served bundle has no volume heads")
    recipes = {(json.dumps(h["model"].get_params(), sort_keys=True, default=str), tuple(h["features"])) for h in heads.values()}
    if len(recipes) != 1:
        raise ValueError(f"the served heads use {len(recipes)} recipes; 'the served GBR head recipe' would be ambiguous")
    h = next(iter(heads.values()))
    return h["model"], list(h["features"])


def _vol_rows(data: Data, key: str, seasons=None, before=None) -> np.ndarray:
    b = data.basins[key]
    m = np.isfinite(b.vol) & (data.days <= TRAIN_END)
    if seasons is not None:
        m &= np.isin(data.season, list(seasons))
    if before is not None:
        m &= data.days < pd.Timestamp(before)
    return m


def _fixed_linear(coef, intercept, Xfit) -> Pipeline:
    """A log-linear head with given weights (the pooled fallback's per-basin member): log1p → linear."""
    lr = LinearRegression(positive=True)
    lr.coef_, lr.intercept_, lr.n_features_in_ = np.asarray(coef, dtype=float), float(intercept), len(coef)
    ft = FunctionTransformer(np.log1p, validate=True).fit(Xfit)
    return Pipeline([("log1p", ft), ("ols", lr)])


def fit_head(data: Data, key: str, recipe: str, served: tuple, seasons=None, before=None,
             force_fallback: bool = False) -> dict:
    """One basin's volume head on its known-volume event days (seasons / before as for train_mask): the recipe
    when it has ≥ 20 events, else the declared fallback — a pooled Bay-side log-linear head with a basin offset
    (Part B 7) — and a basin under the floor with no declared fallback raises. ``force_fallback``: the fallback
    whatever the count (protocol §8's S2 volume row compares the declared head with it on the same folds)."""
    if recipe not in VOLUME_RECIPES:
        raise KeyError(f"unknown volume recipe {recipe!r}; known: {VOLUME_RECIPES}")
    b = data.basins[key]
    m = _vol_rows(data, key, seasons, before)
    n = int(m.sum())
    base = {"target": SC.HEAD_TARGET, "rain_source": b.source, "n_events": n, "recipe": recipe,
            "fit_seasons": sorted(int(x) for x in seasons) if seasons is not None else None,
            "fit_before": str(pd.Timestamp(before).date()) if before is not None else None,
            "span": [str(data.days[m].min().date()), str(data.days[m].max().date())] if n else []}
    if n >= HEAD_MIN_EVENTS and not force_fallback:
        if recipe == "loglinear":
            feats, model = list(VOLUME_TERMS), loglinear_head()
        else:
            feats, model = served[1], clone(served[0])
        model.fit(b.F[feats][m], b.vol[m])
        resid = b.vol[m] - model.predict(b.F[feats][m])
        return {**base, "model": model, "features": feats, "kind": recipe, "resid_std": round(float(resid.std()), 3)}
    if b.facility != FALLBACK_FACILITY:
        if force_fallback:
            raise ValueError(f"{key}: no declared fallback for a {b.facility} basin (Part B 7)")
        raise ValueError(f"{key}: {n} known-volume events, under the {HEAD_MIN_EVENTS}-event floor, and no declared "
                         f"fallback for a {b.facility} basin (Part B 7): the build fails")
    if n < 1:
        raise ValueError(f"{key}: no known-volume event to set the fallback's basin offset")
    members = [k for k in data.keys if data.basins[k].facility == FALLBACK_FACILITY]
    Xc, yc, means, seen = [], [], {}, []
    for k in members:
        mk = _vol_rows(data, k, seasons, before)
        if not mk.any():
            continue
        seen.append(data.days[mk])
        lx = np.log1p(data.basins[k].F[list(VOLUME_TERMS)][mk].to_numpy(dtype=float))
        ly = data.basins[k].vol[mk]
        means[k] = (lx.mean(axis=0), ly.mean())
        Xc.append(lx - means[k][0])
        yc.append(ly - means[k][1])
    Xc, yc = np.vstack(Xc), np.concatenate(yc)
    w = LinearRegression(positive=True, fit_intercept=False).fit(Xc, yc).coef_
    mx, my = means[key]
    model = _fixed_linear(w, my - mx @ w, b.F[list(VOLUME_TERMS)][m])
    resid = b.vol[m] - model.predict(b.F[list(VOLUME_TERMS)][m])
    seen = pd.DatetimeIndex(np.concatenate(seen))
    # the shared slopes read every pooled basin's events: the head's span is theirs, not only its own basin's
    return {**base, "model": model, "features": list(VOLUME_TERMS), "kind": FALLBACK_KIND, "pooled_basins": sorted(means),
            "pooled_events": int(len(yc)), "basin_offset": float(my - mx @ w), "resid_std": round(float(resid.std()), 3),
            "own_span": base["span"], "span": [str(seen.min().date()), str(seen.max().date())]}


def head_log1p(head: dict, F: pd.DataFrame) -> np.ndarray:
    """log1p of v̂ through train_v4.predicted_volume (the predict path every stage reads)."""
    return np.log1p(T.predicted_volume(head, F))


def log_mae(t, p) -> float:
    """Mean |log1p v̂ − log1p v| (protocol §4.3's S2 volume score); a loss, so verify.paired_delta's metric."""
    return float(np.mean(np.abs(np.asarray(p, dtype=float) - np.asarray(t, dtype=float))))


def pick_recipe(delta: dict) -> tuple:
    """(recipe, why) by Part C §6 change 7: the log-linear head replaces the served GBR recipe unless it is worse,
    i.e. unless the 90% storm-block CI of Δ = log-MAE(log-linear) − log-MAE(GBR) lies wholly above 0 (the
    protocol's verdict words, §6). A CI that cannot be drawn is no evidence: log-linear."""
    lo, hi = delta.get("lo"), delta.get("hi")
    worse = lo is not None and hi is not None and np.isfinite(lo) and np.isfinite(hi) and lo > 0
    if worse:
        return "gbr", "log-linear is worse (the CI of its Δ log-MAE is wholly above 0), so the GBR recipe stays"
    return "loglinear", ("log-linear is not worse (the CI of its Δ log-MAE includes 0 or lies below it), so it "
                         "replaces the GBR recipe")


def _loso_volume(data: Data, key: str, recipe: str, served: tuple, seasons, force_fallback: bool = False) -> dict:
    """One recipe leave one season out on the basin's scored known-volume event days: per fold the head fit
    without that season, and the held-out events' log1p v̂ beside the truth (rows in date order)."""
    b = data.basins[key]
    pred, true, dates, kinds, heads = [], [], [], {}, {}
    for s in seasons:
        h = fit_head(data, key, recipe, served, seasons=[x for x in seasons if x != s], force_fallback=force_fallback)
        kinds[S2.season_label(s)] = {"kind": h["kind"], "n_events": h["n_events"]}
        ev = (data.season == s) & np.isfinite(b.vol) & np.array([e == "" for e in b.vol_excl])
        if ev.any():
            pred.append(head_log1p(h, b.F[h["features"]][ev]))
            true.append(b.vol[ev])
            dates.append(data.days[ev])
        heads[s] = h
    if not pred:
        raise ValueError(f"{key}: no scored known-volume event in {sorted(seasons)}")
    return {"pred": np.concatenate(pred), "true": np.concatenate(true), "dates": pd.DatetimeIndex(np.concatenate(dates)),
            "kinds": kinds, "heads": heads}


def volume(data: Data, seasons, served: tuple, n_boot: int = N_BOOT) -> dict:
    """Both recipes leave one season out per basin (fallback where a fold is under the floor), the pick per basin
    by ``pick_recipe`` on the paired storm-block Δ log-MAE, each fold's head of the picked recipe (the procedure's
    v̂), and protocol §8's S2 volume comparison: the picked head against the declared fallback on the same folds
    (every Bay-side basin; reported, never decided)."""
    out, heads_by_fold = {}, {}
    for k in data.keys:
        b = data.basins[k]
        res, runs = {}, {}
        for r in VOLUME_RECIPES:
            run_ = runs[r] = _loso_volume(data, k, r, served, seasons)
            if not np.array_equal(run_["true"], runs[VOLUME_RECIPES[0]]["true"]):
                raise AssertionError(f"{k}: the volume recipes score different event days")
            blk, _ = SB._storm_blocks(run_["dates"], data.blocks)
            est, lo, hi = V.block_bootstrap(log_mae, (run_["true"], run_["pred"]), blk, n=n_boot, seed=SEED, level=LEVEL)
            res[r] = {"n": int(len(run_["true"])), "log_mae": est, "ci": [lo, hi], "folds": run_["kinds"],
                      "fallback_folds": sorted(f for f, v in run_["kinds"].items() if v["kind"] == FALLBACK_KIND)}
            for s, h in run_["heads"].items():
                heads_by_fold[(k, r, s)] = h
        ll, gb = runs["loglinear"], runs["gbr"]
        blk, _ = SB._storm_blocks(ll["dates"], data.blocks)
        delta = V.clean(V.paired_delta(ll["true"], ll["pred"], gb["pred"], blk, metric=log_mae, n=n_boot, seed=SEED,
                                       level=LEVEL))
        picked, why = pick_recipe(delta)
        if b.facility == FALLBACK_FACILITY:
            fb = _loso_volume(data, k, "loglinear", served, seasons, force_fallback=True)
            pk = runs[picked]
            vs_fb = {"delta": V.clean(V.paired_delta(pk["true"], pk["pred"], fb["pred"], blk, metric=log_mae, n=n_boot,
                                                     seed=SEED, level=LEVEL)),
                     "arms": f"the picked {picked} heads − the declared fallback, the same folds and event days",
                     "head_is_fallback_in": res[picked]["fallback_folds"]}
        else:
            vs_fb = {"delta": None, "why": f"no declared fallback for a {b.facility} basin (Part B 7)"}
        out[k] = {"recipes": res, "loglinear_vs_gbr": delta, "picked": picked, "why": why, "vs_fallback": vs_fb,
                  "window": T2_DEVELOPMENT}
    return out, heads_by_fold


def volume_t1(data: Data, heads: dict, lo, hi) -> dict:
    """log-MAE of the given heads on scored event days with a measured volume in lo → hi (descriptive)."""
    out = {}
    for k in data.keys:
        b = data.basins[k]
        ev = (data.days >= pd.Timestamp(lo)) & (data.days <= pd.Timestamp(hi)) & np.isfinite(b.vol) \
            & np.array([e == "" for e in b.vol_excl])
        out[k] = {"n": int(ev.sum()), "log_mae": float(np.mean(np.abs(head_log1p(heads[k], b.F[heads[k]["features"]][ev]) - b.vol[ev])))
                  if ev.any() else None, "kind": heads[k]["kind"]}
    return out


# ── the run ────────────────────────────────────────────────────────────────

def _rel(path: Path) -> str:
    """A path relative to the repository when it is inside it (artifacts), else as given (tests' temp dirs)."""
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def candidate_name(winner: str) -> str:
    return f"sfpuc4_{winner}_v1"


def _sha_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def write_grid(declared: dict, out_dir: Path = OUT_DIR) -> dict:
    """_bakeoff/grid.json, before any fit: the grid, its sha256 and when it was declared."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = {"schema": f"{SCHEMA}.grid", "declared_at": clock.utc_iso(), "sha256": _sha_json(declared), "grid": declared}
    (out_dir / "grid.json").write_text(json.dumps(rec, indent=1) + "\n")
    return rec


def run(grid: Grid = Grid(), data: Data | None = None, n_boot: int = N_BOOT, write: bool = False, save: bool | None = None,
        out_dir: Path = OUT_DIR, root=None, log=print) -> dict:
    """The whole bake-off. Returns {'results', 'rows', 'winner', 'finals', 'holdouts', 'heads', ...}.
    ``write`` writes _bakeoff/{grid,results}.json and rows.csv.gz; ``save`` (default: write) saves the winner
    as a stage candidate. Writing refuses a grid other than A5's or a bootstrap under the protocol's B."""
    t_all = time.time()
    grid.check()
    save = write if save is None else save
    if write and Path(out_dir).resolve().is_relative_to(REPO.resolve()) and Path(out_dir).resolve() != OUT_DIR.resolve():
        raise ValueError(f"{out_dir}: inside the repository the bake-off writes to {_rel(OUT_DIR)}/ only")
    real = (write and Path(out_dir).resolve() == OUT_DIR.resolve()) or \
        (save and (root is None or Path(root).resolve() == SC.ROOT.resolve()))
    if real and (grid != Grid() or n_boot != N_BOOT):
        raise ValueError("only A5's full grid at the protocol's B = 2,000 is written to _bakeoff/ or saved as a candidate")
    declared = grid.declared()
    grid_rec = write_grid(declared, out_dir) if write else {"sha256": _sha_json(declared), "declared_at": None, "grid": declared}
    data = load_data() if data is None else data
    log(f"sfpuc4 S2 bake-off: {len(grid.contenders)} contenders, bends {grid.knots}, C {grid.Cs}, "
        f"{len(grid.seasons)} seasons; data to {data.end.date()}")
    store = fit_store(data, grid, log)
    t0 = time.time()
    nest = nested(data, store, n_boot, log)
    dev = development(data, store, n_boot)
    winner = dev["pick"]["winner"]
    wchoice = dev["choices"][winner]
    log(f"   final pick on all {len(grid.seasons)} seasons: {winner} — {dev['pick']['why']} ({time.time() - t0:.0f}s)")
    # T1 and T1-holdout: every contender's finals / siblings and the served recipe on the same rows
    recipe = served_recipe(data)
    t1_lo, t1_hi = POST_START, data.end
    finals = {c: fit_final(data, dev["choices"][c]) for c in grid.contenders}
    holdouts = {c: fit_final(data, dev["choices"][c], before=HOLDOUT_START) for c in grid.contenders}
    srv_final, srv_hold = served_fit(data, recipe), served_fit(data, recipe, before=HOLDOUT_START)
    t1 = {c: window_rows(data, finals[c], t1_lo, t1_hi, "T1", f"t1:{c}", c, "final") for c in grid.contenders}
    t1["served_recipe"] = window_rows(data, srv_final, t1_lo, t1_hi, "T1", "t1:served_recipe", "served_recipe", "final")
    th = {c: window_rows(data, holdouts[c], HOLDOUT_START, TRAIN_END, "T1-holdout", f"t1h:{c}", c, "pre_holdout")
          for c in grid.contenders}
    th["served_recipe"] = window_rows(data, srv_hold, HOLDOUT_START, TRAIN_END, "T1-holdout", "t1h:served_recipe",
                                      "served_recipe", "pre_holdout")
    srv_t2 = served_t2_rows(data, recipe, grid.seasons)
    # volume
    served_head = served_head_recipe()
    vol, heads_by_fold = volume(data, grid.seasons, served_head, n_boot)
    picked_heads = {k: fit_head(data, k, vol[k]["picked"], served_head) for k in data.keys}
    holdout_heads = {k: fit_head(data, k, vol[k]["picked"], served_head, before=HOLDOUT_START) for k in data.keys}
    check_fit_spans({**{f"{c} final": finals[c] for c in grid.contenders}, "volume heads": picked_heads},
                    {**{f"{c} holdout sibling": holdouts[c] for c in grid.contenders}, "holdout heads": holdout_heads})
    # the procedure's v̂: each outer fold's head of the picked recipe, fit without that season
    proc = nest["procedure"].copy()
    proc["v_hat"] = np.nan
    for (fold, k), g in proc.groupby(["fold", "unit"]):
        h = heads_by_fold[(k, vol[k]["picked"], S2._season_of(fold))]
        if h["fit_seasons"] is None or S2._season_of(fold) in h["fit_seasons"]:
            raise ValueError(f"X-ALL-INSAMPLE: {k} fold {fold}'s volume head was fit on its own season")
        proc.loc[g.index, "v_hat"] = T.predicted_volume(h, data.basins[k].F.loc[pd.DatetimeIndex(g["date"]), h["features"]])
    all_rows = pd.concat([proc] + list(nest["by_contender"].values()) + list(dev["rows"].values())
                         + [srv_t2] + list(t1.values()) + list(th.values()), ignore_index=True)
    check_out_of_fold(all_rows, store, data)
    log(f"   finals, siblings, served recipe, volume heads ({time.time() - t0:.0f}s); scoring …")
    # scores
    t0 = time.time()
    sc_proc = score(proc, data, n_boot)
    sc_nested = {c: score(r, data, n_boot) for c, r in nest["by_contender"].items()}
    sc_dev = {c: score(r, data, n_boot) for c, r in dev["rows"].items()}
    sc_srv_t2 = score(srv_t2, data, n_boot)
    sc_t1 = {c: score(r, data, n_boot) for c, r in t1.items()}
    sc_th = {c: score(r, data, n_boot) for c, r in th.items()}
    t1_vs = {c: paired(t1[c], t1["served_recipe"], data, n_boot) for c in grid.contenders}
    th_vs = {c: paired(th[c], th["served_recipe"], data, n_boot) for c in grid.contenders}
    t2_vs = {"procedure": paired(proc, srv_t2, data, n_boot),
             **{c: paired(nest["by_contender"][c], srv_t2, data, n_boot) for c in grid.contenders}}
    log(f"   scored ({time.time() - t0:.0f}s)")
    south = sc_proc.get(SOUTH)
    south_floor = None
    if south is not None:
        lo = (south.get("ci") or {}).get("bss", [None, None])[0]
        south_floor = {"window": "T2", "rows": "the procedure's outer-fold rows", "bss": south.get("bss"),
                       "ci": (south.get("ci") or {}).get("bss"), "n": south.get("n"), "n_pos": south.get("n_pos"),
                       "rule": "the lower bound of the 90% CI of South's BSS is above 0 (protocol §8, Part B 7)",
                       "pass": bool(lo is not None and lo > 0)}
    wt1 = t1_vs[winner]["pooled"]
    t1_check = {"winner": winner, "vs": f"the served S2 recipe ({CAND.served_info()['stage1']} as served: its pickled "
                "pipelines cloned, unconstrained, refit on sfpuc4_v1 labels through 2025-10-31)",
                "window": f"T1 post-training {POST_START.date()} → {data.end.date()}", "delta": wt1,
                "rule": f"non-inferior at +{NONINFERIOR_MARGIN:.0%}: the 90% CI's upper bound of Δ = BS(winner) − "
                        "BS(served recipe) is below 5% of the served recipe's BS (protocol §8)",
                "noninferior_5pct": wt1.get("noninferior_5pct")}
    terms = check_grid_terms()
    name = candidate_name(winner)
    t2p = t2_vs["procedure"]["pooled"]
    primary = {"rule": "protocol §8 S2: candidate vs the served component within one geography, basin-pooled ΔBS, "
                       "rain known; superiority on T2 (nested for new choices), non-inferiority at +5% on T1 post",
               "T2": {"candidate": "the procedure's outer-fold rows (A5 applied in every outer fold)",
                      "incumbent": "the served recipe, leave one season out, its C as served", "delta": t2p,
                      "superior": bool(t2p.get("hi") is not None and t2p["hi"] < 0)},
               "T1": {"candidate": f"{winner}'s finals", "incumbent": "the served recipe's refit", "delta": wt1,
                      "noninferior_5pct": wt1.get("noninferior_5pct")}}
    primary["passes"] = bool(primary["T2"]["superior"] and primary["T1"]["noninferior_5pct"])
    results = {
        "schema": SCHEMA, "built_at": clock.utc_iso(), "protocol": X.protocol_stamp(), "geography": GEOGRAPHY,
        "data_end": str(data.end.date()), "as_of": str(AS_OF.date()), "decision": "STAGES_DESIGN.md Part A A5",
        "grid": {"sha256": grid_rec["sha256"], "declared_at": grid_rec["declared_at"], **declared},
        "inputs": {
            "rain_sources": data.sources, "rain_source_rule": data.source_rule, "input_rules": list(INPUT_RULES),
            "south_source": f"South reads {data.source_rule[SOUTH]['source']} — {data.source_rule[SOUTH]['how']}; the "
                            f"gauge Central reads too" if SOUTH in data.keys else None,
            "labels": "train_v4.build_dataset(geo=sfpuc4_v1) / target_frame on ledger_known days, equal to "
                      "truth.basin_onsets on every day; the Poo Bot feed's onsets are never labels (protocol §1)",
            "fit_rows": "every ledger_known day (exclusions leave days out of scores, not fits, as for the served set)",
            "scored_rows": "S2's first-match exclusions (X-S2-ARCHIVE → X-S2-UNCOV → X-LEDGER-SUSPECT → X-S2-CARRY)",
            "first_scored_season": {k: S2.season_label(first_scored_season(data, k)) for k in data.keys},
            "fit_days": {k: {"n": int((b.known & (data.days <= TRAIN_END)).sum()),
                             "events": int(np.nansum(b.y[b.known & (data.days <= TRAIN_END)])),
                             "known_volume_events": int((np.isfinite(b.vol) & (data.days <= TRAIN_END)).sum())}
                         for k, b in data.basins.items()},
            "as_of_note": f"counts as of the data end {data.end.date()}"},
        "contenders": {c: {"n_terms": terms[c], "kind": TERM_KIND[c], "provenance": TERM_SET_PROVENANCE[c]}
                       for c in grid.contenders},
        "nested": {"window": T2_NESTED, "caveat": T2_CAVEAT, "folds": nest["folds"], "procedure": sc_proc,
                   "grid_edges": grid_edges(grid, [f["inner"] for f in nest["folds"]]),
                   "picked_by_fold": {f["season"]: f["picked"] for f in nest["folds"]},
                   "by_contender": sc_nested, "vs_served_recipe": t2_vs},
        "development": {"window": T2_DEVELOPMENT, "choices": {c: v.as_dict() for c, v in dev["choices"].items()},
                        "grid_edges": grid_edges(grid, [{c: v.as_dict() for c, v in dev["choices"].items()}]),
                        "scores": sc_dev, "final_pick": dev["pick"]},
        "winner": {"contender": winner, "candidate": name, "choice": wchoice.as_dict(), "n_terms": terms[winner]},
        "served_recipe": {"stage1": CAND.served_info()["stage1"], "C": {k: float(recipe[k]["model"].named_steps["lr"].C)
                          if hasattr(recipe[k]["model"], "named_steps") else None for k in data.keys},
                          "served_keys": {k: recipe[k]["served_key"] for k in data.keys}, "T2": sc_srv_t2,
                          "T2_note": "its C as served (chosen on pre-holdout LOSO: development)"},
        "T1": {"window": f"post-training {POST_START.date()} → {data.end.date()}", "caveat": T1_CAVEAT, "scores": sc_t1,
               "vs_served_recipe": t1_vs,
               "check": t1_check},
        "T1_holdout": {"window": f"holdout {HOLDOUT_START.date()} → {TRAIN_END.date()}, siblings fit before "
                       f"{HOLDOUT_START.date()}; development (the choices saw these seasons)", "scores": sc_th,
                       "vs_served_recipe": th_vs},
        "south_floor": south_floor,
        "s2_primary": primary,
        "volume": {"rule": declared["volume"]["pick"], "per_basin": vol, "picked": {k: v["picked"] for k, v in vol.items()},
                   "procedure_v_hat": "each outer fold's head of the picked recipe, fit without that season; the "
                                      "recipe pick saw every season (development)",
                   "finals": {k: {"kind": h["kind"], "n_events": h["n_events"]} for k, h in picked_heads.items()},
                   "holdout_siblings": {k: {"kind": h["kind"], "n_events": h["n_events"]} for k, h in holdout_heads.items()},
                   "T1": volume_t1(data, picked_heads, t1_lo, t1_hi)},
        "timing": {"fit_seconds": round(store.seconds, 1), "total_seconds": None},
    }
    results["timing"]["total_seconds"] = round(time.time() - t_all, 1)
    results = V.clean(results)
    out = {"results": results, "rows": all_rows, "winner": winner, "choice": wchoice, "finals": finals[winner],
           "holdouts": holdouts[winner], "heads": picked_heads, "holdout_heads": holdout_heads, "data": data, "store": store}
    if write:
        write_results(results, all_rows, out_dir)
    if save:
        results["candidate"] = save_candidate(name, out, results, root, out_dir)
        if write:
            write_results(results, all_rows, out_dir)
    log(f"   winner {winner} → {name}; T1 non-inferior at +5%: {t1_check['noninferior_5pct']}; South floor: "
        f"{(south_floor or {}).get('pass')}; {results['timing']['total_seconds']}s")
    return out


def write_results(results: dict, rows: pd.DataFrame, out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=1, allow_nan=False) + "\n")
    cols = ["arm", "contender", "date", "unit", "tier", "fold", "p", "v_hat", "y", "excl"]
    r = rows.reindex(columns=cols).rename(columns={"unit": "basin"})
    buf = io.StringIO()
    r.to_csv(buf, index=False, float_format=FLOAT_FORMAT, date_format="%Y-%m-%d")
    with gzip.GzipFile(out_dir / "rows.csv.gz", "wb", mtime=0) as f:
        f.write(buf.getvalue().encode())


def save_candidate(name: str, out: dict, results: dict, root=None, out_dir: Path = OUT_DIR) -> dict:
    """The winner as a stage candidate (stages_candidates, kind 's2'): finals, heads, holdout siblings, spec."""
    data, choice = out["data"], out["choice"]
    spec = {"contender": choice.contender, "bends": choice.knots, "n_terms": n_terms(choice.contender),
            "design": {f: list(v) for f, v in design(choice.contender, choice.knots).items()},
            "terms": term_names(choice.contender, choice.knots), "C": choice.C, "south": choice.south,
            "rain_sources": data.sources, "rain_source_rule": data.source_rule, "input_rules": list(INPUT_RULES),
            "trained_through": str(TRAIN_END.date()), "holdout_start": str(HOLDOUT_START.date()),
            "fit_rows": results["inputs"]["fit_rows"], "labels": results["inputs"]["labels"],
            "volume": {k: {"kind": out["heads"][k]["kind"], "recipe": out["heads"][k]["recipe"],
                           "n_events": out["heads"][k]["n_events"],
                           "holdout_kind": out["holdout_heads"][k]["kind"]} for k in data.keys},
            "grid_sha256": results["grid"]["sha256"], "grid": {k: v for k, v in results["grid"].items() if k != "sha256"},
            "bakeoff": _rel(Path(out_dir) / "results.json"),
            "t2": T2_NESTED, "t2_caveat": T2_CAVEAT, "t1_caveat": T1_CAVEAT,
            "term_set_provenance": TERM_SET_PROVENANCE[choice.contender],
            "volume_t2": f"{T2_DEVELOPMENT}: the recipe per basin was picked on nine-season leave-one-season-out "
                         "event days, so the T2 rows' v̂ is development",
            "t1_noninferior_5pct": results["T1"]["check"]["noninferior_5pct"],
            "south_floor_pass": (results["south_floor"] or {}).get("pass"), "built_at": results["built_at"]}
    payload = {"geography": GEOGRAPHY, "component": f"{choice.contender}_nonneg_sfpuc4", "models": out["finals"],
               "volume": out["heads"], "holdout_models": out["holdouts"], "holdout_volume": out["holdout_heads"],
               "spec": V.clean(spec)}
    d = SC.save_component(name, "s2", payload, root=root)
    return {"name": name, "dir": _rel(d),
            "files": sorted(SC.load_set(name, root).manifest["files"])}


def main(argv: list) -> None:
    n_boot = int(argv[argv.index("--n-boot") + 1]) if "--n-boot" in argv else N_BOOT
    write = "--write" in argv
    out = run(n_boot=n_boot, write=write)
    r = out["results"]
    print(f"\nwinner {out['winner']} ({r['winner']['choice']['bends']} bends, C {r['winner']['choice']['C']}, "
          f"South {r['winner']['choice']['south']}); picked by outer fold: {r['nested']['picked_by_fold']}")
    p = r["nested"]["procedure"]["pooled"]
    print(f"procedure T2 pooled BSS {p['bss']:.3f} [{p['ci']['bss'][0]:.3f}, {p['ci']['bss'][1]:.3f}], Brier {p['bs']:.5f}")
    if write:
        print(f"wrote {OUT_DIR.relative_to(REPO)}/{{grid,results}}.json, rows.csv.gz and the candidate "
              f"{r.get('candidate', {}).get('dir')}")


if __name__ == "__main__":
    # dispatch through the importable module so pickles reference stages_s2_sfpuc4.add_hinges_at, not __main__
    import stages_s2_sfpuc4 as _m
    _m.main(sys.argv[1:])
