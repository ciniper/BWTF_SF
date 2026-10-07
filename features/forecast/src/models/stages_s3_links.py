#!/usr/bin/env python3
"""stages_s3_links — stage S3 under SFPUC4_V1: the Westside link shares, the
identity links, the co-firing matrix and the East union rule, fit per fold and
written as one s3_links.json (P8b; STAGES_DESIGN.md Part C §3.3, §2.4, §7
"s3_links.json", §6 change 6; Part B 1, 2; STAGES_PROTOCOL.md §2 windows, §3
entry rules, §6 blocks and Holm, §8 rows S3a / S3b).

Westside is SFPUC's only basin that reaches two zones, so it is the only S3
split. Given that Westside overflows on D, which zones does it reach?

    s_ℓ(v̂) = logistic(a_ℓ + b_ℓ · log1p v̂)      ℓ ∈ {westside>ocean, westside>baker_china}

with v̂ the basin's size predicted from rain (MG, S2's volume head) and one
size median per basin (``basin_median_mg``: the basin's median filed event
volume, which S4 reads for its size class; the share itself reads no median,
so the old 3.7 MG vs 13.93 MG double median cannot come back). The other three
links are identity (share 1, φ 1): north_shore>north, central>east,
south>east. East is fed by Central and South, so its p is a union of two link
p's, by one rule of compose_v2.UNION_RULES (max | noisy_or | cofire), every one
inside the Fréchet bounds [max p_ℓ, min(1, Σ p_ℓ)].

**Shares are fit on v̂, the way they are used (Part B 2; protocol §3).** On
a scored day v̂ comes from a head that never saw the day. The served heads are
gradient-boosted and overfit: on the 49 Westside overflow days through
2025-10-31 with a measured volume (as of the data end 2026-08-17), log1p of the
final head's in-sample v̂ correlates 0.97 with log1p of the filed volume, the
out-of-fold v̂ 0.83. A share fit on
in-sample v̂ is a share fit on (nearly) the true volume, which holds the
link's own discharge (Ocean Beach outfalls are a median 87% of Westside's
volume). Fit on the filed volume itself, Ocean Beach's slope is 3.96 in the
final fold against 1.10 on out-of-fold v̂, and two of the nine T2 folds
separate (no finite fit). So the v̂ of a fit day in season s comes from the
fold's head design refit without season s too (inner leave-one-season-out):
every fit day's v̂ is out of sample, like every scored day's. ``fit_table``
keeps no filed volume at all; the shares read its v̂ column only. A season
whose inner head would read fewer than 20 known-volume events is left out of
the fit (``fit_days_dropped`` in the fold's record): Westside has no declared
volume fallback (Part B 7), so that head is never fit, and an in-sample v̂
would break Part B 2. With the bake-off winner's heads (35 Westside
known-volume events through 2025-10-31, X-S2-VOLQ days out; as of the data end
2026-08-17) this drops season 2018-19 from the 2022-23 fold, 2022-23 from the
2018-19 fold, and both from the holdout fold.

**Fit rows = scored rows (the oracle's).** A link's fit days are the S3 oracle
rows of its zone that the protocol scores (exclusions.apply: X-S3-UNCOV →
X-LEDGER-SUSPECT → X-S3-CARRY → X-S3-QUIET), on the fold's training days. So
the fit sees exactly the kind of day it is scored on: Westside overflowed, the
zone's truth is known, no carry-over.

**Folds (protocol §2; Part B 1).** The folds are stages_s2's (``_plan``), as
sets of July–June training seasons on days through 2025-10-31: T1 (final) every
season, T1-holdout the seasons before 2023-07-01, each T2 season the other
eight of 2016-17 … 2024-25. Everything S3 fits is refit per fold on those
days only: the shares and their constant benchmark, φ, the basin medians, the
co-firing matrix (compose_v2.cofire on the fold's ledger events) and the
union rule.

**The East union rule is chosen nested (protocol §2, §8).** In fold F, for
each T2 season s of F's training seasons, S2 is refit on F's seasons without s
(and the co-firing π on the same days), and each rule composes East from that
S2's Central and South p on s's days (rain known, the chained entry, through
compose_v2.s3). On those inner rows, pooled over the inner seasons, ``pick_union``
applies §2.4's rule, fixed before any outer score is read: **max is the default**
(today's comonotone semantics, no parameter) and a challenger replaces it only
when it is *better*, the 90% storm-block CI of Δ = BS(challenger) − BS(max)
wholly below 0 (protocol §6's verdict words, B = 2,000, seed 0, whatever B the
scores use). Among challengers that are better, the lowest Brier score wins; if
the CI of its difference to the next includes 0, the one with fewer parameters
(noisy_or before cofire, whose π is fit). This is A5's rule for a nested pick
(a CI that includes 0 keeps the simpler arm) with the design's default in the
simpler arm's place: a Brier score lower by noise never moves the rule. The
final (T1) fold chooses on all nine T2 seasons, so the rule the spec carries is
the one a T2 score never saw choose itself; each T2 fold's choice never saw its
own season. Every rule's T2 score is reported, and so is the nested procedure's.

**What S2 is read.** S3 needs S2's Westside v̂, and Central's and South's p,
for any set of training seasons: a fold's S2 (``FoldS2``) offers ``name``,
``predict(basin, what, seasons, days)``, ``seen`` (the days a fit read: the
out-of-fold check), ``n_fit`` (the rows a fit would read, so a head under its
floor is never fit: ``fit_table``) and ``describe``.

**The assemble step (``candidate_s2``; Part B 2).** A stage candidate's own S2
(stages_s2_sfpuc4's winner, saved by stages_candidates) is read fold by fold
(``CandidateS2.for_fold``): each fold keeps the design its S2 was made with
(stages_s2_sfpuc4.fold_choices) — T1 and T1-holdout the final pick's (the
finals' and the holdout siblings'), each T2 season the choice its outer fold's
A5 procedure made inside that fold, whose rows are the build's T2 S2 — and its
weights and picked volume heads are refit on each season set the fold's S3 asks
for, with that design. So no inner fit reads a choice that saw the fold's
scored season, and the scored-day S2 is the build's: the refit on each fold's
own seasons must equal the saved finals and siblings and the bake-off's T2 rows
(``candidate_fidelity``, checked at load). ``run(s2=candidate_s2(name))`` and
``write(res.spec, name=name)`` complete the candidate's S3
(stages_candidates.assemble runs them).

**Scores and the pre-registered S3b (protocol §8).** Per window (T2 pooled
over its nine seasons, T1-holdout, T1): the Westside zones' oracle (the true
occurrence, v̂ from rain; X-S3-QUIET leaves the no-overflow days out) for the
fitted share and its two benchmarks, constant share and identity; East chained
(rain known) for each union rule and for the nested choice. Every score is
stages_build's (verify.scores_bundle against the fold's training climatology,
storm blocks from rain, B = 2,000, seed 0, 90% CIs). S3b = Δ = BS(s(v̂)) −
BS(constant) on the T2 oracle rows of both Westside zones, verify.paired_delta
on storm blocks; superiority when the 90% CI's upper bound is below 0. S3a
(the geography) is the assemble step's; S3a and S3b form one Holm family, so
``holm_inputs`` gives S3b's one-sided bootstrap p-value P*(Δ* ≥ 0) (the same
resample as the CI) and its CIs at both Holm steps (95% for α/2, 90% for α),
which stages_build.holm combines with S3a's.

**Identity links are checked, not scored (protocol §7 X-S3-ID).** On every
oracle row X-S3-ID leaves out (North and East on the days their basins
overflowed), each fold's spec under every union rule must give exactly the zone
truth: ``integrity`` counts the mismatches, which must be 0.

**Today's split on the city map (``served_split``, ``split_spec``; sfpuc-icon-t8s-osplits-lt2zone-lzflags).**
The second challenger keeps everything above but the shares: its Westside links
take the served stage 2 v2 split (compose_v2 share kind size_blend: stage2.
group_share's size classes around the group's median, φ 1), refit per fold by
the served set's own recipe exactly as the build refits the served adapter
(stages_build.fold_specs: T1 the served stage2.json, every other fold
stage2.fit_outfall_split on the fold's training days with the served set's
fold heads), served group "Ocean Beach" read as westside>ocean and
"Baker-China" as westside>baker_china (their outfalls asserted identical under
geo_v1 and sfpuc4_v1). The East union rule, the co-firing shares and the
constant-share benchmark S3b reads are a base spec's per-fold records, kept
exactly. Today's split is fit on the filed volumes (the served heads' v̂ only
where none is filed), not on the v̂ it is used with: Part B 2 does not hold
for it, as it does not for the served set; ``split_spec``'s note says so.

**s3_links.json (§7)** through compose_v2.check_s3_spec: geography, pipeline,
links (the final fold's shares, φ), union, cofire, and a fit block holding every
fold's coefficients, constants, φ, medians, co-firing shares and union choice
(with its inner Briers), the S2 it read, and the scores (compact: no CORP
curves or threshold tables; ``run`` returns them in full). ``fold_spec``
rebuilds any fold's full S3 spec from it (also its benchmarks; a recorded share of
any compose_v2 kind, size_blend included, is taken as recorded). ``write``
saves it through stages_candidates.save_component as the 's3_links' component
(``links_v1``) of a stage candidate, which stamps it with the set and the
component; compose_v2.check_s3_spec does not know those two stamps, so
``for_compose`` checks and drops them before compose_v2 reads a saved spec.
Nothing else in data/models/ is touched, and no module-level IO.

    venv/bin/python features/forecast/src/models/stages_s3_links.py --candidate NAME [--n-boot 2000] [--write]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
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

import compose_v2 as C  # noqa: E402
import csd_labels  # noqa: E402  (the CIWQS ledger compose_v2.cofire counts)
import exclusions as X  # noqa: E402
import stages_build as SB  # noqa: E402  (read only: the scorers, references and blocks every stage uses)
import stages_entries as E  # noqa: E402
import stages_s2 as S2  # noqa: E402
import train_v4 as T4  # noqa: E402  (the predict path: calibrated, predicted_volume; wet_season)
import truth as T  # noqa: E402
import verify as V  # noqa: E402
from shared import geography as G  # noqa: E402

GEOGRAPHY = "sfpuc4_v1"
PIPELINE = "stages_v1"
KIND = "links_v1"                              # manifests' components.s3 for this spec
SCHEMA = "bwtf.stages.s3_links/1"
SHARE = "logit_logvol"                         # s(v̂) = logistic(a + b · log1p v̂)
BENCHMARKS = ("constant", "identity")          # §3.3: constant share (no size term); share 1 (the v1 behaviour)
ARMS = (SHARE,) + BENCHMARKS
RULES = C.UNION_RULES                          # max (the default, no parameter) first: a tie keeps the earlier
UNION_DEFAULT = "max"                          # §2.4: "Default: max"; a challenger must be better to replace it
UNION_N_BOOT = SB.B_PROTOCOL                   # the choice's bootstrap: the protocol's, whatever B the scores use
TRAIN_END = S2.TRAINED_THROUGH                 # 2025-10-31: every fold's training days end here
T2_SEASONS = S2.T2_SEASONS                     # 2016 … 2024: the July–June seasons 2016-17 … 2024-25
WINDOWS = S2.TIERS                             # T1, T1-holdout, T2
ORACLE, CHAINED = "oracle", "rain"             # S3b's entry (true occurrence, v̂ from rain); the union's (rain known)
HEAD_MIN_EVENTS = S2.HEAD_MIN_EVENTS           # 20: a head on fewer known-volume events raises (Part B 7)
NEWTON_TOL, NEWTON_MAX_ITER = 1e-10, 100       # the share's two-parameter maximum likelihood
SEPARATION_LOGIT = 30.0                        # |a| + |b|·max log1p v̂ beyond this: the fit is separating, raise
HOLM_ALPHA = 0.05                              # protocol §6: one-sided α = 0.05, Holm over S3a and S3b
SPEC_FILE = "s3_links.json"
SAVER_STAMPS = ("set", "component")            # what save_component adds that compose_v2.check_s3_spec does not know
COMPACT_DROP = ("curve", "contingency")        # left out of the spec's scores: CORP curves, threshold tables
SPLIT_KIND = "split_v2_sfpuc4"                 # today's split on SFPUC4's links (split_spec): the spec kind and component
# the served split's legacy groups, read as the SFPUC4 links with the same outfalls (served_split asserts it)
SPLIT_GROUPS = {"westside>ocean": "Ocean Beach", "westside>baker_china": "Baker-China"}


def _geo() -> G.Geography:
    return G.get(GEOGRAPHY)


def split_links(geo) -> tuple:
    """The links whose basin reaches more than one zone (SFPUC4: Westside's two): the only fitted shares."""
    return tuple(lk for lk in geo.links if not lk.identity)


def split_basin(geo) -> str:
    basins = {lk.basin for lk in split_links(geo)}
    if len(basins) != 1:
        raise ValueError(f"{geo.version}: the split links come from {sorted(basins)}; this module fits one split basin")
    return basins.pop()


def union_zones(geo) -> tuple:
    """Zones fed by more than one link (SFPUC4: East)."""
    return tuple(z for z in dict.fromkeys(lk.zone for lk in geo.links) if len(geo.links_into(z)) > 1)


def identity_zones(geo) -> tuple:
    """Zones every link into which is identity (SFPUC4: North, East): their oracle is exact by construction."""
    return tuple(z for z in dict.fromkeys(lk.zone for lk in geo.links) if all(lk.identity for lk in geo.links_into(z)))


# ── folds (stages_s2's, as season sets) ─────────────────────────────────────

@dataclass(frozen=True)
class Fold:
    tier: str
    fold: str
    start: pd.Timestamp          # first scored day
    end: pd.Timestamp            # last scored day (T1: the freeze; the data end comes first)
    season: int | None           # T2: the held-out season
    seasons: frozenset           # the training seasons (days through TRAIN_END)

    @property
    def key(self) -> tuple:
        return (self.tier, self.fold)


def _calendar() -> pd.DataFrame:
    days = pd.date_range(T.TRUTH_START, TRAIN_END)
    return pd.DataFrame({"date": days, "season": T4.wet_season(pd.Series(days)).to_numpy()})


def train_days(seasons) -> pd.DatetimeIndex:
    """Every day a fit on these training seasons may read: the seasons' days through 2025-10-31."""
    cal = _calendar()
    return pd.DatetimeIndex(cal.loc[cal["season"].isin(set(seasons)), "date"])


def plan(tiers=WINDOWS) -> tuple:
    """The folds of stages_s2._plan with their training filter read as a set of seasons. Raises unless the season
    set selects exactly the days the filter keeps (so a fold here is stages_s2's fold, day for day)."""
    cal = _calendar()
    out = []
    for tier, fold, start, end, season, keep in S2._plan(tuple(tiers)):
        kept = cal if keep is None else cal[keep(cal)]
        seasons = frozenset(int(s) for s in kept["season"].unique())
        if not pd.DatetimeIndex(kept["date"]).equals(train_days(seasons)):
            raise AssertionError(f"{tier} {fold}: its training filter is not a set of whole seasons")
        if season is not None and season in seasons:
            raise AssertionError(f"T2 {fold} trains on its own season")
        out.append(Fold(tier, fold, pd.Timestamp(start), pd.Timestamp(end), season, seasons))
    return tuple(out)


def inner_seasons(fold: Fold) -> tuple:
    """The T2 seasons the fold's nested choices are scored on: its training seasons that are T2 seasons."""
    return tuple(s for s in T2_SEASONS if s in fold.seasons)


# ── the S2 that S3 reads: the stage candidate's own (the assemble step; Part B 2) ──

P, VHAT = "p", "v_hat"
P_FIDELITY_TOL = 5e-5          # a T2 fold's refit vs the bake-off's rows (its C-path solver, 6 significant digits)
V_FIDELITY_RTOL = 2e-5         # the same head refit vs the rows' 6 significant digits
EXACT_TOL = 1e-9               # the finals / siblings refit on the same rows by the same pipeline


def _s2c():
    import stages_s2_sfpuc4 as S2C  # noqa: PLC0415  (P8a: the bake-off's data, designs and fitters; read only when used)
    return S2C


@dataclass
class CandidateS2:
    """A stage candidate's S2 as S3 reads it, fold by fold (``for_fold``): the assemble step's S2 (Part B 2: the
    shares and the union rule are fit on the S2 they are used with). Each fold keeps the design its S2 was made with
    (stages_s2_sfpuc4.fold_choices): T1 and T1-holdout the final pick's (the finals' and the holdout siblings'),
    each T2 season the choice its outer fold's A5 procedure made inside that fold (whose rows are the build's T2
    S2). The fold's weights and volume heads are refit on whatever season set its S3 asks for — its own seasons, or
    those less one inner season — with that design and the picked volume recipe per basin (fit_final; fit_head,
    which takes the declared fallback where a Bay-side basin is under the 20-event floor and raises for Westside),
    so an inner fit never reads a choice made on the fold's scored season. Predictions come from the bake-off's own
    inputs (outage-masked gauges, the served rain source per basin), which the build's S2 reproduces
    (stages_build.bakeoff_fidelity). ``fidelity`` records each fold's refit on its own seasons against the saved
    finals and siblings (equal) and the bake-off's T2 rows (its C-path solver, within P_FIDELITY_TOL)."""
    name: str                  # the stage candidate: s3_links.json's sources.s2.name
    component: str             # its S2 component (the manifest's components.s2)
    data: object               # stages_s2_sfpuc4.Data
    choices: dict              # (tier, fold) → stages_s2_sfpuc4.Choice
    recipes: dict              # basin → the picked volume recipe
    served_head: tuple         # stages_s2_sfpuc4.served_head_recipe (the GBR recipe, should one be picked)
    note: str = ""
    fidelity: dict = field(default_factory=dict)
    _fits: dict = field(default_factory=dict, repr=False)

    def for_fold(self, fold) -> "FoldS2":
        key = (fold.tier, fold.fold)
        if key not in self.choices:
            raise KeyError(f"{self.name}: no S2 design for fold {key}")
        return FoldS2(self, key)

    def values(self, key: tuple, what: str, days) -> pd.DataFrame:
        """date × every basin: fold ``key``'s S2 refit on that fold's own seasons (plan()), on ``days``: what the
        build composes the fold with (its p, or v̂: the size an overflow with no measured volume takes)."""
        fold = next((f for f in plan() if f.key == tuple(key)), None)
        if fold is None:
            raise KeyError(f"{tuple(key)} is not a protocol §2 fold")
        fs, days = self.for_fold(fold), pd.DatetimeIndex(days)
        return pd.DataFrame({k: fs.predict(k, what, fold.seasons, days) for k in self.data.keys}, index=days)

    def describe(self) -> dict:
        keep = ("contender", "bends", "C", "south")
        return {"name": self.name, "component": self.component, "note": self.note,
                "designs": {f"{t} {fo}": {k: c.as_dict()[k] for k in keep} for (t, fo), c in self.choices.items()},
                "volume_recipes": dict(self.recipes), "fidelity": self.fidelity}


@dataclass(frozen=True)
class FoldS2:
    """One fold's S2 of a CandidateS2, as S3 reads it (name, n_fit, predict, seen, describe)."""
    src: CandidateS2
    key: tuple

    @property
    def name(self) -> str:
        return self.src.name

    @property
    def choice(self):
        return self.src.choices[self.key]

    def _mask(self, basin: str, what: str, seasons) -> np.ndarray:
        S2C, d = _s2c(), self.src.data
        if basin not in d.basins:
            raise KeyError(f"{self.name} has no basin {basin!r}")
        if what == VHAT:
            return S2C._vol_rows(d, basin, seasons=seasons)
        if what == P:
            return d.basins[basin].known & (d.days <= TRAIN_END) & np.isin(d.season, list(seasons))
        raise KeyError(f"what must be {P!r} or {VHAT!r}, not {what!r}")

    def n_fit(self, basin: str, what: str, seasons) -> int:
        return int(self._mask(basin, what, seasons).sum())

    def fitted(self, basin: str, what: str, seasons) -> dict:
        S2C, d = _s2c(), self.src.data
        seasons = frozenset(int(s) for s in seasons)
        ch = self.choice
        sig = (ch.contender, ch.knots, tuple(sorted(ch.C.items())), ch.south) if what == P else None
        key = (basin, what, sig, seasons)
        if key in self.src._fits:
            return self.src._fits[key]
        self._mask(basin, what, seasons)                       # unknown basin / what raise here
        if what == P:
            keys = (S2C.SOUTH_POOLS_WITH, basin) if basin == S2C.SOUTH and ch.south == "pooled" else (basin,)
            m = S2C.fit_final(d, ch, seasons=seasons, keys=keys)[basin]
        else:
            m = S2C.fit_head(d, basin, self.src.recipes[basin], self.src.served_head, seasons=seasons)
            keys = tuple(m.get("pooled_basins") or (basin,))      # the fallback's shared slopes read every pooled basin
        _saver().check_nonnegative(m["model"], f"{self.name} {self.key} {basin} {what}")   # "no odd weights"
        seen = pd.DatetimeIndex(d.days[np.logical_or.reduce([self._mask(k, what, seasons) for k in keys])])
        out = {**m, "seen": seen}
        self.src._fits[key] = out
        return out

    def seen(self, basin: str, what: str, seasons) -> pd.DatetimeIndex:
        return self.fitted(basin, what, seasons)["seen"]

    def predict(self, basin: str, what: str, seasons, days) -> np.ndarray:
        m = self.fitted(basin, what, seasons)
        F = self.src.data.basins[basin].F
        days = pd.DatetimeIndex(days)
        missing = days[~days.isin(F.index)]
        if len(missing):
            raise KeyError(f"{self.name} {basin} {what}: the bake-off's inputs lack {missing[0].date()}")
        X_ = F.loc[days, list(m["features"])]
        if not np.isfinite(X_.to_numpy(dtype=float)).all():
            raise ValueError(f"{self.name} {basin} {what}: a missing input between {days[0].date()} and {days[-1].date()}")
        out = m["model"].predict_proba(X_)[:, 1] if what == P else T4.predicted_volume(m, X_)
        if not np.isfinite(out).all():
            raise ValueError(f"{self.name} {basin} {what}: a non-finite prediction")
        return np.asarray(out, dtype=float)

    def describe(self) -> dict:
        return self.src.describe()


def _bakeoff_path(man: dict, name: str) -> Path:
    rel = (man.get("s2") or {}).get("bakeoff")
    if not rel:
        raise KeyError(f"{name}: the manifest's s2 section names no bake-off (s2.bakeoff)")
    p = Path(rel)
    return p if p.is_absolute() else REPO / p


def candidate_fidelity(src: CandidateS2, saved, rows: pd.DataFrame) -> dict:
    """Each fold's S2 refit on its own seasons (the S3 oracle's and the union's scored-day S2) against what the
    build scores: T1 / T1-holdout the saved finals / siblings (p and v̂ equal to EXACT_TOL), T2 the bake-off's
    procedure rows (p within P_FIDELITY_TOL: its C-path solver; v̂ within V_FIDELITY_RTOL). Raises past a
    tolerance; returns {fold: {n, p_max_abs, v_hat_max_rel}}."""
    d = src.data
    out = {}
    proc = rows[rows["arm"] == "procedure"]
    for f in plan():
        fs = src.for_fold(f)
        days = pd.DatetimeIndex(d.days[(d.days >= f.start) & (d.days <= min(f.end, d.end))])
        if f.tier == "T2":
            g = proc[(proc["tier"] == "T2") & (proc["fold"] == f.fold)]
            if not len(g):
                raise KeyError(f"{src.name}: the bake-off holds no procedure rows of {f.fold}")
        gp, gv, n = 0.0, 0.0, 0
        for k in d.keys:
            F = d.basins[k].F
            if f.tier == "T2":
                gk = g[g["basin"] == k].sort_values("date")
                dk = pd.DatetimeIndex(gk["date"])
                want_p, want_v = gk["p"].to_numpy(dtype=float), gk["v_hat"].to_numpy(dtype=float)
            else:
                models, heads = ((saved.models, saved.volume) if f.tier == "T1" else (saved.holdout_models, saved.holdout_volume))
                dk = days
                want_p = models[k]["model"].predict_proba(F.loc[dk, list(models[k]["features"])])[:, 1]
                want_v = T4.predicted_volume(heads[k], F.loc[dk])
            got_p, got_v = fs.predict(k, P, f.seasons, dk), fs.predict(k, VHAT, f.seasons, dk)
            gp = max(gp, float(np.abs(got_p - want_p).max()))
            gv = max(gv, float((np.abs(got_v - want_v) / np.maximum(np.abs(want_v), 1e-9)).max()))
            n += len(dk)
        ptol, vtol = (P_FIDELITY_TOL, V_FIDELITY_RTOL) if f.tier == "T2" else (EXACT_TOL, EXACT_TOL)
        if gp > ptol or gv > vtol:
            raise AssertionError(f"{src.name} {f.tier} {f.fold}: the refit S2 differs from what the build scores (p by "
                                 f"{gp:.1e}, v̂ by a relative {gv:.1e}): S3 would be fit on another S2 (Part B 2)")
        out[f"{f.tier} {f.fold}"] = {"n": n, "p_max_abs": gp, "v_hat_max_rel": gv,
                                     "against": "the bake-off's procedure rows" if f.tier == "T2" else
                                                ("the saved finals" if f.tier == "T1" else "the saved holdout siblings")}
    return out


def candidate_s2(name: str, root=None, data=None, check: bool = True) -> CandidateS2:
    """The S2 of stage candidate ``name`` (its S2 component saved by stages_s2_sfpuc4, or copied from the set that
    did: stages_candidates.copy_s2's ``s2_from``) as S3 reads it, fold by fold (``CandidateS2``): the bake-off its
    manifest names must have picked this set or the one its S2 is a copy of (results.winner.candidate) on the grid
    the manifest records, and, with ``check``, each fold's refit must reproduce what the build scores
    (``candidate_fidelity``: the set's own saved finals and siblings)."""
    sc = _saver()
    S2C = _s2c()
    st = sc.load_set(name, root=root)
    if st.geo.version != GEOGRAPHY:
        raise ValueError(f"{name} is a {st.geo.version} set; S3 links are fit under {GEOGRAPHY}")
    if "s2" not in st.components:
        raise FileNotFoundError(f"{name} holds no S2 component")
    path = _bakeoff_path(st.manifest, name)
    results = json.loads(path.read_text())
    if results["winner"]["candidate"] not in (name, st.manifest.get("s2_from")):
        raise ValueError(f"{name}: the bake-off {path.name} picked {results['winner']['candidate']!r}")
    if st.manifest["s2"].get("grid_sha256") != results["grid"]["sha256"]:
        raise ValueError(f"{name}: its S2 records grid {st.manifest['s2'].get('grid_sha256')}, the bake-off ran {results['grid']['sha256']}")
    data = S2C.load_data() if data is None else data
    if tuple(data.keys) != tuple(st.keys):
        raise KeyError(f"the bake-off's data holds basins {list(data.keys)}, {name} {list(st.keys)}")
    recipes = {k: results["volume"]["picked"][k] for k in st.keys}
    picked = {f["season"]: f["picked"] for f in results["nested"]["folds"]}
    note = (f"{name}'s own S2 ({st.components['s2']}), fold by fold: T1 and T1-holdout the final pick's design "
            f"({results['winner']['contender']}, {results['winner']['choice']['bends']} bends; chosen on all nine T2 seasons, so "
            "T1-holdout is development), each T2 season the design its outer fold's A5 procedure chose inside that fold "
            f"({picked}); weights and the picked volume heads ({recipes}) refit on each season set the fold's S3 asks for, "
            "with that fold's design. The volume recipe pick saw every season (development; stages_s2_sfpuc4).")
    src = CandidateS2(name, st.components["s2"], data, S2C.fold_choices(results), recipes, S2C.served_head_recipe(), note)
    if check:
        rows = SB.bakeoff_rows(path.parent / "rows.csv.gz", st.geo)
        src.fidelity = candidate_fidelity(src, st, rows)
    return src


# ── the truth rows S3 is fit and scored on ──────────────────────────────────

@dataclass(frozen=True)
class Truth:
    """Everything S3 reads from the ledger, once: the rules' context, the S3 rows of each entry with their
    first-match exclusion, the basin and link onsets (volumes for φ and the medians), the events, the blocks."""
    geo: G.Geography
    ctx: X.Context
    oracle: pd.DataFrame          # S3 oracle rows of the split zones: date, unit, y, excl (the whole span)
    chained: pd.DataFrame         # S3 rain-known rows of the union zones
    identity: pd.DataFrame        # S3 oracle rows of the zones every link of which is identity (X-S3-ID's)
    basin: pd.DataFrame           # truth.basin_onsets
    link: pd.DataFrame            # truth.link_onsets
    events: pd.DataFrame          # the CIWQS ledger (event_date, outfall_id, …)
    blocks: pd.DataFrame          # truth.blocks by date
    end: pd.Timestamp


def _s3_rows(ctx: X.Context, zones, entry: str) -> pd.DataFrame:
    sk = X.skeleton("s3", list(zones), ctx.start, ctx.end, entry=entry, tier="T2")
    sk["y"] = ctx.frames["zone"]["y"].reindex(pd.MultiIndex.from_arrays([sk["unit"], sk["date"]])).to_numpy(dtype=float)
    r = X.apply(sk, "s3", ctx)
    return pd.DataFrame({"date": pd.DatetimeIndex(r["date"]), "unit": r["unit"].to_numpy(), "y": r["y"].to_numpy(dtype=float),
                         "excl": r["excl"].to_numpy()})


def truth(end=None) -> Truth:
    geo = _geo()
    end = E.data_end() if end is None else pd.Timestamp(end)
    ctx = X.context(geo, end=end)
    zs = tuple(dict.fromkeys(lk.zone for lk in split_links(geo)))
    return Truth(geo, ctx, _s3_rows(ctx, zs, ORACLE), _s3_rows(ctx, union_zones(geo), CHAINED),
                 _s3_rows(ctx, identity_zones(geo), ORACLE), T.basin_onsets(geo, end=end), T.link_onsets(geo, end=end),
                 csd_labels.load_events(), T.blocks(end=end).set_index("date"), end)


# ── the shares ──────────────────────────────────────────────────────────────

def fit_logit(x, y) -> dict:
    """Maximum likelihood of P(y = 1) = logistic(a + b·x) by Newton's method: {a, b, n, n_pos, iterations}.
    Raises on NaNs, a single class, no spread in x, or a separating fit (the likelihood has no maximum)."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(x) != len(y) or not len(x):
        raise ValueError("a share fit needs rows (x and y of one length)")
    if not (np.isfinite(x).all() and np.isfinite(y).all()) or not np.isin(y, (0.0, 1.0)).all():
        raise ValueError("a share fit needs finite x and 0/1 outcomes")
    if y.min() == y.max():
        raise ValueError(f"every fit day is {int(y[0])}: no share can be fit")
    if np.ptp(x) == 0:
        raise ValueError("every fit day has the same size: the size term is not identified")
    A = np.column_stack([np.ones_like(x), x])
    theta = np.array([np.log(y.mean() / (1 - y.mean())), 0.0])
    for it in range(1, NEWTON_MAX_ITER + 1):
        p = 1.0 / (1.0 + np.exp(-(A @ theta)))
        g = A.T @ (y - p)
        H = (A * (p * (1 - p))[:, None]).T @ A
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:                      # the weights p(1 − p) all reached 0: a separating fit
            raise ValueError("the share fit separates the days (the likelihood has no maximum)") from None
        theta = theta + step
        if np.abs(step).max() < NEWTON_TOL:
            break
    else:
        raise ValueError(f"the share fit did not converge in {NEWTON_MAX_ITER} steps (it separates the days?)")
    a, b = (float(t) for t in theta)
    if abs(a) + abs(b) * float(np.abs(x).max()) > SEPARATION_LOGIT:
        raise ValueError(f"the share fit separates the days (a {a:.1f}, b {b:.1f}): no finite maximum to trust")
    return {"a": a, "b": b, "n": int(len(y)), "n_pos": int(y.sum()), "iterations": it}


def fit_table(fold: Fold, s2: FoldS2, tr: Truth, dropped: dict | None = None) -> pd.DataFrame:
    """The rows a fold's shares are fit on: the oracle's scored rows of the split zones on the fold's training days
    (Westside overflowed; X-S3-UNCOV, X-LEDGER-SUSPECT, X-S3-CARRY applied), each with the zone's link, its 0/1 truth
    and v̂ — from the fold's head design refit without the day's own season (inner leave-one-season-out), so every
    fit day's v̂ is out of sample, as on a scored day. Columns: date, season, unit, link, y, v_hat, head_seasons.
    No filed volume: the shares cannot read it.

    A season whose inner head would be fit on fewer than HEAD_MIN_EVENTS known-volume events (``s2.n_fit``) has
    no out-of-sample v̂: the split basin (Westside, Oceanside) has no declared fallback (Part B 7), so no such head
    is fit, and an in-sample v̂ would break Part B 2. Its fit days are left out of the share fit, recorded in
    ``dropped`` ({season: {n_rows, n_events, why}}); a fold left with no fit day raises."""
    geo = tr.geo
    wb = split_basin(geo)
    link_of = {lk.zone: lk.id for lk in split_links(geo)}
    o = tr.oracle
    days = train_days(fold.seasons)
    r = o[(o["excl"] == "") & o["date"].isin(days) & o["unit"].isin(list(link_of))].copy()
    if not len(r):
        raise ValueError(f"{fold.tier} {fold.fold}: no Westside overflow day to fit the shares on")
    r["season"] = T4.wet_season(r["date"]).to_numpy()
    r["link"] = r["unit"].map(link_of)
    r["v_hat"] = np.nan
    r["head_seasons"] = ""
    keep = np.ones(len(r), dtype=bool)
    for s in sorted(r["season"].unique()):
        m = (r["season"] == s).to_numpy()
        inner = fold.seasons - {int(s)}
        n = s2.n_fit(wb, VHAT, inner)
        if n < HEAD_MIN_EVENTS:
            keep &= ~m
            if dropped is not None:
                dropped[str(int(s))] = {"n_rows": int(m.sum()), "n_events": n,
                                        "why": (f"the {wb} head refit without season {int(s)} would read {n} known-volume "
                                                f"events, under the {HEAD_MIN_EVENTS}-event floor, and {wb} has no declared "
                                                "fallback (Part B 7): no out-of-sample v̂ for these days, so they are not fit on")}
            continue
        r.loc[m, "v_hat"] = s2.predict(wb, VHAT, inner, r.loc[m, "date"])
        r.loc[m, "head_seasons"] = ",".join(str(x) for x in sorted(inner))
    r = r[keep]
    if not len(r):
        raise ValueError(f"{fold.tier} {fold.fold}: every Westside fit day's inner head is under the floor")
    if r["v_hat"].isna().any():
        raise AssertionError("a fit day has no v̂")
    return r[["date", "season", "unit", "link", "y", "v_hat", "head_seasons"]].sort_values(["link", "date"]).reset_index(drop=True)


def fit_shares(table: pd.DataFrame) -> dict:
    """{link: {"logit_logvol": {a, b, …}, "constant": share}} from a fit table's v̂ and y only."""
    out = {}
    for lid, g in table.groupby("link", sort=True):
        y, x = g["y"].to_numpy(dtype=float), np.log1p(g["v_hat"].to_numpy(dtype=float))
        out[lid] = {SHARE: fit_logit(x, y), "constant": float(y.mean()), "n": int(len(y)), "n_pos": int(y.sum())}
    return out


def vol_shares(fold: Fold, tr: Truth) -> dict:
    """{link: φ_ℓ}: the link's median share of its basin's filed volume on the training days it fired, read where
    every event of the basin-day has a measured volume (no blank, no '<'). An identity link is its basin: 1."""
    bo = measured_days(fold, tr).set_index(["basin", "date"])
    days = train_days(fold.seasons)
    lo = tr.link[tr.link["date"].isin(days) & (tr.link["y"] == 1)]      # a NA y (not known) is no fired day
    out = {}
    for lk in tr.geo.links:
        if lk.identity:
            out[lk.id] = 1.0
            continue
        g = lo[lo["link"] == lk.id]
        bv = bo["volume_mg"].reindex(pd.MultiIndex.from_arrays([g["basin"], g["date"]])).to_numpy(dtype=float)
        lv = g["volume_mg"].to_numpy(dtype=float)
        ok = np.isfinite(bv) & (bv > 0) & np.isfinite(lv)
        if not ok.any():
            raise ValueError(f"{fold.tier} {fold.fold} {lk.id}: no measured day to read its volume share from")
        phi = float(np.median(lv[ok] / bv[ok]))
        if not 0 < phi <= 1:
            raise ValueError(f"{fold.tier} {fold.fold} {lk.id}: volume share {phi} outside (0, 1]")
        out[lk.id] = phi
    return out


def measured_days(fold: Fold, tr: Truth) -> pd.DataFrame:
    """The basin-days φ and the size medians read: event days on the fold's training days where every event of the
    basin-day has a measured volume (no blank, no '<': X-S2-VOLQ). A NaN volume left there raises."""
    days = train_days(fold.seasons)
    b = tr.basin[tr.basin["date"].isin(days) & (tr.basin["y"] == 1) & ~tr.basin["volq"]]   # a NA y is not an event
    if not np.isfinite(b["volume_mg"].to_numpy(dtype=float)).all():
        raise ValueError(f"{fold.tier} {fold.fold}: a measured event day without a volume")
    return b


def basin_medians(fold: Fold, tr: Truth) -> dict:
    """{basin: median filed event-day volume, MG} on the fold's training days (every event measured): one size
    median per basin (§3.3)."""
    b = measured_days(fold, tr)
    out = {}
    for k in tr.geo.keys:
        v = b.loc[b["basin"] == k, "volume_mg"].to_numpy(dtype=float)
        if not len(v):
            raise ValueError(f"{fold.tier} {fold.fold} {k}: no measured event day for its size median")
        out[k] = float(np.median(v))
    return out


def fold_events(tr: Truth, seasons) -> pd.DataFrame:
    """The ledger events on the training days of these seasons (compose_v2.cofire's input)."""
    d = pd.to_datetime(tr.events["event_date"]).dt.normalize()
    ev = tr.events[d.isin(train_days(seasons))]
    if not len(ev):
        raise ValueError(f"no ledger event on seasons {sorted(seasons)}")
    return ev


# ── specs ───────────────────────────────────────────────────────────────────

def _link_row(lk, share: dict, phi: float) -> dict:
    return {"basin": lk.basin, "zone": lk.zone, "outfalls": list(lk.outfalls), "evidence": C.link_evidence(lk),
            "identity": lk.identity, "share": share, "vol_share": float(phi)}


def build_spec(geo, shares: dict, arm: str, phi: dict, median_mg: float, cofire: dict, rule: str, kind: str = KIND) -> dict:
    """A checked S3 spec: the split links at ``arm`` (logit_logvol | constant | identity) from ``shares``
    (fit_shares; a share given whole, with its compose_v2 kind, is taken as it is: today's size_blend), identity
    links at 1, ``rule`` for every union zone (compose_v2.union_block, π from ``cofire``)."""
    if arm not in ARMS:
        raise KeyError(f"unknown share arm {arm!r}; known {ARMS}")
    links = {}
    for lk in geo.links:
        if lk.identity or arm == "identity":
            sh = {"kind": "identity"}
        elif arm == "constant":
            sh = {"kind": "constant", "p": shares[lk.id]["constant"]}
        else:
            c = shares[lk.id][SHARE]
            sh = dict(c) if "kind" in c else {"kind": SHARE, "coef": {"a": c["a"], "b": c["b"]}, "basin_median_mg": float(median_mg)}
        links[lk.id] = _link_row(lk, sh, phi[lk.id])
    spec = {"geography": geo.version, "pipeline": PIPELINE, "kind": kind if arm == SHARE else f"{kind}_benchmark_{arm}",
            "links": links, "union": C.union_block(geo, rule, cofire), "cofire": dict(cofire)}
    return C.check_s3_spec(spec, geo)


def _basin_inputs(geo, days: pd.DatetimeIndex, cols: dict) -> pd.DataFrame:
    """date × every basin key: the given columns, 0 elsewhere (S3 reads a basin only through its links)."""
    f = pd.DataFrame(0.0, index=days, columns=list(geo.keys))
    for k, v in cols.items():
        f[k] = np.asarray(v, dtype=float)
    return f


def east_p(geo, rule: str, cofire: dict, p_basin: dict, days: pd.DatetimeIndex) -> pd.DataFrame:
    """date × union zone: compose_v2.s3 under ``rule`` on S2's p of the basins feeding the union zones (consecutive
    days; the identity spec, as East's links are identity)."""
    spec = build_spec(geo, {}, "identity", {lk.id: 1.0 for lk in geo.links}, 1.0, cofire, rule)
    pb = _basin_inputs(geo, days, p_basin)
    out = C.s3(geo, spec, pb, pd.DataFrame(0.0, index=days, columns=list(geo.keys)))
    return out.zone_p[list(union_zones(geo))]


def _runs(days: pd.DatetimeIndex) -> list:
    return SB._runs(pd.DatetimeIndex(sorted(set(days))))


def occurrence(tr: Truth, rows: pd.DataFrame, run: pd.DatetimeIndex) -> pd.DataFrame:
    """run × basin key: the true basin occurrence (0/1) the S3 oracle feeds compose_v2 on a run of days. Every
    basin feeding a zone that has a row on the day must be known there (X-S3-UNCOV left the others out), else
    this raises; a basin no row of that day reads is unknown on some days, and only there it is set to 0, which
    no scored zone composes from."""
    geo = tr.geo
    y = tr.basin.pivot(index="date", columns="basin", values="y").astype(float).reindex(run)[list(geo.keys)]
    need = pd.DataFrame(False, index=run, columns=list(geo.keys))
    for z, g in rows.groupby("unit"):
        d = pd.DatetimeIndex(g["date"])
        need.loc[d[d.isin(run)], list(T.feeding_basins(geo, z))] = True
    hole = need.to_numpy() & ~np.isfinite(y.to_numpy())
    if hole.any():
        i, j = (int(a[0]) for a in np.nonzero(hole))
        raise ValueError(f"{run[i].date()} {geo.keys[j]}: a scored S3 oracle row reads a basin with no known occurrence")
    return y.fillna(0.0)


# ── the union rule, nested ──────────────────────────────────────────────────

def _feeders(geo) -> tuple:
    return tuple(dict.fromkeys(lk.basin for z in union_zones(geo) for lk in geo.links_into(z)))


def chained_rows(tr: Truth, s2: FoldS2, seasons, cofire: dict, days: pd.DatetimeIndex) -> pd.DataFrame:
    """The scored rain-known rows of the union zones on ``days``, with every rule's p from S2 refit on ``seasons``:
    date, unit, y, p_<basin> (S2's p of each feeding basin) and one column per rule."""
    geo = tr.geo
    c = tr.chained
    r = c[(c["excl"] == "") & c["date"].isin(days)].copy()
    cols = list(RULES) + [f"p_{b}" for b in _feeders(geo)]
    if not len(r):
        return r.assign(**{k: pd.Series(dtype=float) for k in cols})
    for k in cols:
        r[k] = np.nan
    for run in _runs(r["date"]):
        p = {b: s2.predict(b, P, seasons, run) for b in _feeders(geo)}
        m = r["date"].isin(run).to_numpy()
        for b, v in p.items():
            r.loc[m, f"p_{b}"] = pd.Series(v, index=run).reindex(r.loc[m, "date"]).to_numpy()
        for rule in RULES:
            ez = east_p(geo, rule, cofire, p, run)
            r.loc[m, rule] = ez.stack().reindex(pd.MultiIndex.from_arrays([r.loc[m, "date"], r.loc[m, "unit"]])).to_numpy()
    if r[cols].isna().any().any():
        raise AssertionError("a scored union-zone row has no p")
    return r


def n_params(geo, rule: str, cofire: dict) -> int:
    """The parameters a union rule fits per fold: its π (compose_v2.union_block's), none for max and noisy_or."""
    return sum(len(u.get("pi", {})) for u in C.union_block(geo, rule, cofire).values())


def pick_union(y, arms: dict, dates, blocks: pd.DataFrame, params: dict, n_boot: int = UNION_N_BOOT) -> dict:
    """§2.4's rule on identical rows (``arms``: rule → p): the default max stays unless a challenger is *better*,
    the 90% storm-block CI of Δ = BS(challenger) − BS(max) wholly below 0 (stages_build._delta, verify's verdict
    words). Among the challengers that are better, the lowest Brier score; if the CI of its difference to the next
    one includes 0, the one with fewer parameters (``params``). Returns {rule, brier, lowest, vs_default, why, n,
    n_blocks}; ``lowest`` is the lowest Brier score alone, which decides nothing."""
    if UNION_DEFAULT not in arms or set(arms) - set(RULES):
        raise KeyError(f"the union arms must be rules of {list(RULES)} including the default {UNION_DEFAULT!r}; got {sorted(arms)}")
    y = np.asarray(y, dtype=float)
    a = {r: np.asarray(p, dtype=float) for r, p in arms.items()}
    if not len(y) or not np.isfinite(y).all() or any(len(p) != len(y) or not np.isfinite(p).all() for p in a.values()):
        raise ValueError("the union choice needs finite outcomes and p on one set of rows")
    blk, _ = SB._storm_blocks(dates, blocks)
    bs = {r: float(np.mean((p - y) ** 2)) for r, p in a.items()}
    rank = {r: (bs[r], RULES.index(r)) for r in a}
    vs = {r: SB._delta(y, a[r], a[UNION_DEFAULT], blk, n_boot) for r in a if r != UNION_DEFAULT}
    better = sorted((r for r, d in vs.items() if d["verdict"] == "better"), key=rank.get)
    rule = UNION_DEFAULT
    why = "no challenger is better than max (each CI of Δ includes 0 or lies above it), so the default stays"
    if better:
        rule = better[0]
        why = f"{rule} is better than max (the CI of its Δ lies wholly below 0)"
        if len(better) > 1:
            d = SB._delta(y, a[better[0]], a[better[1]], blk, n_boot)
            if d["verdict"] != "better":
                rule = min(better[:2], key=lambda r: (params[r], better.index(r)))
                why += (f"; the CI of its Δ to {better[1]} includes 0, so the one with fewer parameters wins: {rule}"
                        if rule != better[0] else f"; the CI of its Δ to {better[1]} includes 0 and it has no more parameters")
    return {"rule": rule, "brier": bs, "lowest": min(a, key=rank.get), "vs_default": vs, "why": why,
            "n": int(len(y)), "n_blocks": int(len(np.unique(blk)))}


def choose_union(fold: Fold, s2: FoldS2, tr: Truth) -> dict:
    """The fold's union rule by inner leave-one-season-out over its T2 seasons: per inner season s, S2 and the
    co-firing π refit on the fold's seasons without s, every rule's East p on s's rows; ``pick_union`` on the inner
    rows pooled over the seasons (max unless a challenger is better). Returns {rule, brier {rule}, n, inner {season:
    {n, sse {rule}, train_seasons}}, pick (pick_union's lowest, vs_default, why), seen}; ``seen`` = what the choice
    read (each inner season's scored rows, events and S2 fits), for check_out_of_fold."""
    geo = tr.geo
    inner, parts, seen = {}, [], {}
    cof = None
    for s in inner_seasons(fold):
        ins = fold.seasons - {s}
        ev = fold_events(tr, ins)
        cof = C.cofire(geo, ev)
        rows = chained_rows(tr, s2, ins, cof, train_days({s}))
        e = {rule: float(((rows[rule] - rows["y"]) ** 2).sum()) for rule in RULES}
        inner[str(s)] = {"n": int(len(rows)), "sse": e, "train_seasons": sorted(int(x) for x in ins)}
        parts.append(rows)
        seen[f"union_rows_{s}"] = pd.DatetimeIndex(rows["date"])
        seen[f"union_events_{s}"] = pd.DatetimeIndex(pd.to_datetime(ev["event_date"]).dt.normalize())
        for b in _feeders(geo):
            seen[f"union_p_{b}_{s}"] = s2.seen(b, P, ins)
    pooled = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if not len(pooled):
        raise ValueError(f"{fold.tier} {fold.fold}: no inner row to choose the union rule on")
    pick = pick_union(pooled["y"], {r: pooled[r] for r in RULES}, pooled["date"], tr.blocks,
                      {r: n_params(geo, r, cof) for r in RULES})
    keep = ("delta", "lo", "hi", "se", "verdict", "n", "n_blocks")
    return {"rule": pick["rule"], "brier": pick["brier"], "n": pick["n"], "inner": inner,
            "pick": {"lowest": pick["lowest"], "why": pick["why"], "n_blocks": pick["n_blocks"],
                     "vs_default": {r: {k: d[k] for k in keep} for r, d in pick["vs_default"].items()}},
            "seen": seen}


# ── one fold ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FoldFit:
    fold: Fold
    table: pd.DataFrame            # the share fit rows (fit_table)
    shares: dict                   # fit_shares
    phi: dict
    medians: dict
    cofire: dict
    union: dict                    # choose_union
    seen: dict                     # what each fitted part read: name → DatetimeIndex
    dropped: dict = field(default_factory=dict)   # fit_table's seasons with no out-of-sample v̂ (head under its floor)


def fold_s2(s2, fold: Fold):
    """The S2 a fold reads: ``s2.for_fold(fold)`` when its design differs by fold (CandidateS2), else ``s2``."""
    return s2.for_fold(fold) if hasattr(s2, "for_fold") else s2


def fit_fold(fold: Fold, s2: CandidateS2, tr: Truth) -> FoldFit:
    geo = tr.geo
    s2 = fold_s2(s2, fold)
    dropped: dict = {}
    table = fit_table(fold, s2, tr, dropped)
    shares = fit_shares(table)
    want = {lk.id for lk in split_links(geo)}
    if set(shares) != want:
        raise ValueError(f"{fold.tier} {fold.fold}: shares fit for {sorted(shares)}, the geography splits {sorted(want)}")
    ev = fold_events(tr, fold.seasons)
    cof = C.cofire(geo, ev)
    union = choose_union(fold, s2, tr)
    wb = split_basin(geo)
    seen = {"shares": pd.DatetimeIndex(table["date"]), "events": pd.DatetimeIndex(pd.to_datetime(ev["event_date"]).dt.normalize()),
            "phi_and_medians": pd.DatetimeIndex(measured_days(fold, tr)["date"]).union(
                pd.DatetimeIndex(tr.link.loc[tr.link["date"].isin(train_days(fold.seasons)) & (tr.link["y"] == 1), "date"])),
            "head": s2.seen(wb, VHAT, fold.seasons),
            **{f"p_{b}": s2.seen(b, P, fold.seasons) for b in _feeders(geo)}, **union.pop("seen")}
    for s in sorted(table["season"].unique()):
        seen[f"head_inner_{s}"] = s2.seen(wb, VHAT, fold.seasons - {int(s)})
    return FoldFit(fold, table, shares, vol_shares(fold, tr), basin_medians(fold, tr), cof, union, seen, dropped)


def fold_spec_of(ff: FoldFit, geo, arm: str = SHARE, rule: str | None = None) -> dict:
    wb = split_basin(geo)
    return build_spec(geo, ff.shares, arm, ff.phi, ff.medians[wb], ff.cofire, rule or ff.union["rule"])


def scored_days(fold: Fold, end: pd.Timestamp) -> pd.DatetimeIndex:
    hi = min(fold.end, end)
    return pd.date_range(fold.start, hi) if fold.start <= hi else pd.DatetimeIndex([])


def oracle_rows(ff: FoldFit, s2: CandidateS2, tr: Truth) -> pd.DataFrame:
    """The fold's scored S3 oracle rows of the split zones (true occurrence, v̂ from the fold's head), one p column
    per arm, through compose_v2.s3 on the fold's specs: date, unit, y, tier, fold, v_hat, and the arms."""
    geo, fold = tr.geo, ff.fold
    s2 = fold_s2(s2, fold)
    wb = split_basin(geo)
    o = tr.oracle
    days = scored_days(fold, tr.end)
    r = o[(o["excl"] == "") & o["date"].isin(days)].copy()
    if not len(r):
        return r
    specs = {arm: fold_spec_of(ff, geo, arm) for arm in ARMS}
    for arm in ARMS:
        r[arm] = np.nan
    r["v_hat"] = np.nan
    for run in _runs(r["date"]):
        v = s2.predict(wb, VHAT, fold.seasons, run)
        y = occurrence(tr, r[r["date"].isin(run)], run)
        inputs = C.BasinInputs(_basin_inputs(geo, run, {}), _basin_inputs(geo, run, {wb: v})).oracle(y)
        m = r["date"].isin(run).to_numpy()
        idx = pd.MultiIndex.from_arrays([r.loc[m, "date"], r.loc[m, "unit"]])
        for arm, spec in specs.items():
            zp = C.s3(geo, spec, inputs.p, inputs.v_hat).zone_p
            r.loc[m, arm] = zp.stack().reindex(idx).to_numpy()
        r.loc[m, "v_hat"] = pd.Series(v, index=run).reindex(r.loc[m, "date"]).to_numpy()
    if r[list(ARMS)].isna().any().any():
        raise AssertionError("a scored oracle row has no p")
    r["tier"], r["fold"] = fold.tier, fold.fold
    return r


def union_rows(ff: FoldFit, s2: CandidateS2, tr: Truth) -> pd.DataFrame:
    """The fold's scored East rows (rain known): every rule's p (S2 and π fit on the fold's seasons) and the
    nested choice's ('nested' = the fold's chosen rule)."""
    fold = ff.fold
    r = chained_rows(tr, fold_s2(s2, fold), fold.seasons, ff.cofire, scored_days(fold, tr.end))
    if not len(r):
        return r
    r["nested"] = r[ff.union["rule"]]
    r["tier"], r["fold"] = fold.tier, fold.fold
    return r


def integrity(fits: dict, tr: Truth) -> dict:
    """X-S3-ID's check (protocol §7): on every oracle row it leaves out, in each fold's window, the fold's spec under
    every union rule gives exactly the zone truth. {s3_identity_mismatches (must be 0), n_checked, by_zone}."""
    geo = tr.geo
    rows = tr.identity[tr.identity["excl"] == "X-S3-ID"]
    if not len(rows):
        raise ValueError("no X-S3-ID row to check: the identity zones never overflowed?")
    bad, n, by_zone = 0, 0, {}
    for ff in fits.values():
        r = rows[rows["date"].isin(scored_days(ff.fold, tr.end))]
        for run in _runs(r["date"]):
            y = occurrence(tr, r[r["date"].isin(run)], run)
            inputs = C.BasinInputs(_basin_inputs(geo, run, {}), _basin_inputs(geo, run, {})).oracle(y)
            m = r["date"].isin(run).to_numpy()
            idx = pd.MultiIndex.from_arrays([r.loc[m, "date"], r.loc[m, "unit"]])
            truth_y = r.loc[m, "y"].to_numpy(dtype=float)
            for rule in RULES:
                zp = C.s3(geo, fold_spec_of(ff, geo, rule=rule), inputs.p, inputs.v_hat).zone_p
                miss = np.abs(zp.stack().reindex(idx).to_numpy() - truth_y) > 0
                bad += int(miss.sum())
                for z in np.unique(r.loc[m, "unit"]):
                    zm = (r.loc[m, "unit"] == z).to_numpy()
                    d = by_zone.setdefault(z, {"n_checked": 0, "mismatches": 0})
                    d["n_checked"] += int(zm.sum())
                    d["mismatches"] += int((miss & zm).sum())
            n += int(m.sum()) * len(RULES)
    return {"s3_identity_mismatches": bad, "n_checked": n, "rules": list(RULES), "by_zone": by_zone}


# ── the out-of-fold check ───────────────────────────────────────────────────

def check_out_of_fold(fits: dict, rows: dict, end: pd.Timestamp) -> None:
    """Raise unless no fitted part of a fold read a day it scores (X-ALL-INSAMPLE): shares, φ, medians, events,
    S2's head and p, every inner fit; inside the union choice, no inner fit (S2's p, the co-firing events) read
    a day of the inner season it is scored on, and the inner rows are that season's; every fit day's v̂ came
    from a head without that day's season; and every emitted row sits in its fold's window."""
    for key, ff in fits.items():
        scored = scored_days(ff.fold, end)
        for name, seen in ff.seen.items():
            hit = pd.DatetimeIndex(seen).isin(scored)
            if hit.any():
                raise ValueError(f"X-ALL-INSAMPLE: {key} {name} read {pd.DatetimeIndex(seen)[hit][0].date()}, a day it scores")
        for s in ff.union["inner"]:
            own = train_days({int(s)})
            if not pd.DatetimeIndex(ff.seen[f"union_rows_{s}"]).isin(own).all():
                raise ValueError(f"{key}: the union choice's rows for inner season {s} hold days of other seasons")
            for name in (n for n in ff.seen if n.endswith(f"_{s}") and n.startswith(("union_p_", "union_events_"))):
                hit = pd.DatetimeIndex(ff.seen[name]).isin(own)
                if hit.any():
                    raise ValueError(f"X-ALL-INSAMPLE: {key} the union choice's {name} read "
                                     f"{pd.DatetimeIndex(ff.seen[name])[hit][0].date()}, a day of the inner season it scores")
        hs = ff.table["head_seasons"].str.split(",")
        own = ff.table["season"].astype(int).astype(str)
        if any(o in h for o, h in zip(own, hs)):
            raise ValueError(f"{key}: a fit day's v̂ came from a head that saw its season")
    for name, r in rows.items():
        for key, g in r.groupby(["tier", "fold"]):
            sd = scored_days(fits[key].fold, end)
            if not g["date"].isin(sd).all():
                raise ValueError(f"{name} {key}: a row outside its fold's window")
        if r.duplicated(["date", "unit", "tier"]).any():
            raise ValueError(f"{name}: a unit-day emitted twice in one window")


# ── scores ──────────────────────────────────────────────────────────────────

def _with_ref(r: pd.DataFrame, pool: pd.DataFrame) -> pd.DataFrame:
    r = r.copy()
    r["ref"] = SB.references(r[["unit", "date", "tier"]], pool)
    if not np.isfinite(r["ref"]).all():
        raise ValueError("a scored row has no reference")
    return r


def _units(g: pd.DataFrame) -> list:
    """'pooled' and each unit; a window of one unit is that unit alone (pooled would be the same rows twice)."""
    units = sorted(g["unit"].unique())
    return units if len(units) == 1 else ["pooled"] + units


def score_arms(r: pd.DataFrame, arms, blocks: pd.DataFrame, n_boot: int) -> dict:
    """{arm: {unit | pooled: {window: bundle}}} (stages_build.bundle on each window's rows)."""
    out: dict = {}
    for t in WINDOWS:
        g = r[r["tier"] == t]
        if not len(g):
            continue
        blk, storm = SB._storm_blocks(g["date"], blocks)
        for arm in arms:
            for u in _units(g):
                m = np.ones(len(g), bool) if u == "pooled" else (g["unit"] == u).to_numpy()
                gu = g[m]
                out.setdefault(arm, {}).setdefault(u, {})[t] = SB.bundle(gu["y"], gu[arm], gu["ref"], blk[m], storm[m],
                                                                         gu["unit"].to_numpy(), n_boot, gu["date"])
    return out


def paired(r: pd.DataFrame, a: str, b: str, blocks: pd.DataFrame, n_boot: int) -> dict:
    """{unit | pooled: {window: Δ = BS(a) − BS(b)}} (stages_build._delta on the same rows)."""
    out: dict = {}
    for t in WINDOWS:
        g = r[r["tier"] == t]
        if not len(g):
            continue
        blk, _ = SB._storm_blocks(g["date"], blocks)
        for u in _units(g):
            m = np.ones(len(g), bool) if u == "pooled" else (g["unit"] == u).to_numpy()
            out.setdefault(u, {})[t] = SB._delta(g["y"].to_numpy()[m], g[a].to_numpy()[m], g[b].to_numpy()[m], blk[m], n_boot)
    return out


def holm_inputs(r: pd.DataFrame, blocks: pd.DataFrame, n_boot: int, a: str = SHARE, b: str = "constant") -> dict:
    """S3b's numbers for Holm with S3a (protocol §6, §8): on the T2 rows of both split zones, Δ = BS(a) − BS(b) by
    verify.paired_delta on storm blocks; p_one_sided = P*(Δ* ≥ 0) on the same resample (1 − P(Δ < 0)), so p < α
    exactly when that level's CI lies below 0; the CIs at Holm's two steps (one-sided α/2 = 0.025 → 95%, α → 90%)."""
    g = r[r["tier"] == "T2"]
    if not len(g):
        raise ValueError("S3b needs T2 rows")
    blk, _ = SB._storm_blocks(g["date"], blocks)
    y, pa, pb = g["y"].to_numpy(dtype=float), g[a].to_numpy(dtype=float), g[b].to_numpy(dtype=float)
    d90 = V.paired_delta(y, pa, pb, blk, n=n_boot, seed=SB.SEED, level=0.90)
    d95 = V.paired_delta(y, pa, pb, blk, n=n_boot, seed=SB.SEED, level=0.95)
    p = 1.0 - d90["p_neg"] if np.isfinite(d90["p_neg"]) else float("nan")
    return V.clean({"comparison": f"S3b: BS({a}) − BS({b}), S3 oracle (true occurrence, v̂ from rain), T2, both Westside zones",
                    "n": d90["n"], "n_blocks": d90["n_blocks"], "delta": d90["delta"], "se": d90["se"],
                    "ci90": [d90["lo"], d90["hi"]], "ci95": [d95["lo"], d95["hi"]], "p_one_sided": p,
                    "superior_unadjusted": bool(np.isfinite(d90["hi"]) and d90["hi"] < 0),
                    "mde": d90["mde"], "mde_pct": d90["mde_pct"], "verdict": d90["verdict"], "n_boot": n_boot,
                    "family": ["S3a", "S3b"], "alpha": HOLM_ALPHA,
                    "how": "Holm over S3a and S3b at one-sided α = 0.05: the smaller p is tested at 0.025 (the 95% CI's upper "
                           "bound below 0 for a superiority claim), the larger at 0.05 (the 90% CI's); p_one_sided = P*(Δ* ≥ 0)."})


# ── the whole fit ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Result:
    spec: dict
    fits: dict                     # (tier, fold) → FoldFit
    oracle: pd.DataFrame
    union: pd.DataFrame
    scores: dict


def run(s2: CandidateS2, tr: Truth | None = None, tiers=WINDOWS, n_boot: int = SB.B_PROTOCOL, log=print) -> Result:
    """Fit every fold on stage candidate S2 ``s2`` (``candidate_s2``), score it, check it never saw what it scores, and
    build the spec (``Result.spec``)."""
    t0 = time.time()
    tr = truth() if tr is None else tr
    geo = tr.geo
    folds = plan(tiers)
    fits = {f.key: fit_fold(f, s2, tr) for f in folds}
    log(f"  {len(fits)} folds fit in {time.time() - t0:.1f}s")
    orc = pd.concat([oracle_rows(ff, s2, tr) for ff in fits.values()], ignore_index=True)
    uni = pd.concat([union_rows(ff, s2, tr) for ff in fits.values()], ignore_index=True)
    check_out_of_fold(fits, {"oracle": orc, "union": uni}, tr.end)
    no_id = dataclasses.replace(tr.ctx, frames={**tr.ctx.frames, "zone": tr.ctx.frames["zone"].assign(identity=False)})
    orc = _with_ref(orc, SB._pool("s3", no_id, geo, ORACLE))
    uni = _with_ref(uni, SB._pool("s3", tr.ctx, geo, CHAINED))
    scores = {
        "s2": s2.name,
        "windows": {"T2": (f"cross-season, development: the union rule nested, the shares pre-registered; S2 is {s2.name} "
                           "(sources.s2 says whether its own choices saw these seasons: then selection-contaminated, protocol §2)"),
                    "T1-holdout": "development only (protocol §2)", "T1": "post-training, confirmation; low power"},
        "oracle": score_arms(orc, ARMS, tr.blocks, n_boot),
        "oracle_paired": {f"{SHARE} − {b}": paired(orc, SHARE, b, tr.blocks, n_boot) for b in BENCHMARKS},
        "union": score_arms(uni, RULES + ("nested",), tr.blocks, n_boot),
        "union_paired": {f"{x} − max": paired(uni, x, "max", tr.blocks, n_boot) for x in RULES[1:] + ("nested",)},
    }
    if "T2" in tiers:
        scores["s3b"] = holm_inputs(orc, tr.blocks, n_boot)
    scores["integrity"] = integrity(fits, tr)
    if scores["integrity"]["s3_identity_mismatches"]:
        raise AssertionError(f"identity links are not exact: {scores['integrity']}")
    log(f"  scored in {time.time() - t0:.1f}s")
    spec = make_spec(fits, s2, tr, scores, n_boot)
    return Result(spec, fits, orc, uni, scores)


def _fold_block(ff: FoldFit, geo) -> dict:
    f = ff.fold
    wb = split_basin(geo)
    links = {}
    for lk in geo.links:
        if lk.identity:
            links[lk.id] = {"share": {"kind": "identity"}, "vol_share": 1.0}
            continue
        s = ff.shares[lk.id]
        links[lk.id] = {"share": {"kind": SHARE, "coef": {"a": s[SHARE]["a"], "b": s[SHARE]["b"]},
                                  "basin_median_mg": ff.medians[wb]},
                        "constant": s["constant"], "vol_share": ff.phi[lk.id], "n_fit_days": s["n"], "n_fired": s["n_pos"],
                        "newton_iterations": s[SHARE]["iterations"]}
    d = pd.DatetimeIndex(ff.table["date"])
    out = {"tier": f.tier, "fold": f.fold, "scores": [str(f.start.date()), str(f.end.date())],
           "train_seasons": sorted(int(s) for s in f.seasons),
           "share_fit_span": [str(d.min().date()), str(d.max().date())], "links": links,
           "basin_median_mg": ff.medians, "cofire": ff.cofire,
           "union": {**C.union_block(geo, ff.union["rule"], ff.cofire),
                     "choice": {"brier": ff.union["brier"], "n": ff.union["n"], "inner": ff.union["inner"], **ff.union["pick"]}}}
    if ff.dropped:                     # fit_table: seasons with no out-of-sample v̂ (the inner head under its floor)
        out["fit_days_dropped"] = ff.dropped
    return out


def compact(obj):
    """The scores without CORP curves and threshold tables (COMPACT_DROP), for the spec file."""
    if isinstance(obj, dict):
        return {k: compact(v) for k, v in obj.items() if k not in COMPACT_DROP}
    if isinstance(obj, list):
        return [compact(v) for v in obj]
    return obj


def make_spec(fits: dict, s2: CandidateS2, tr: Truth, scores: dict, n_boot: int) -> dict:
    """s3_links.json (§7): the final fold's (T1, fit through 2025-10-31) links, union and co-firing shares on top,
    every fold in ``fit``. Checked by compose_v2.check_s3_spec; every fold's spec is too (``fold_spec``)."""
    geo = tr.geo
    final = fits.get(("T1", S2.FOLD_FINAL))
    if final is None:
        raise ValueError("the spec's top level is the final fold (T1): fit it")
    top = fold_spec_of(final, geo)
    spec = {
        "geography": geo.version, "pipeline": PIPELINE, "kind": KIND, "links": top["links"], "union": top["union"],
        "cofire": top["cofire"],
        "sources": {"s2": s2.describe(), "ledger": "truth.basin_onsets / link_onsets (CIWQS, ledger-known days)",
                    "rain": "stages_entries.frames('rain'): rain known, outage-masked"},
        "note": ("Westside's two links share its overflow by s(v̂) = logistic(a + b·log1p v̂), v̂ the basin size predicted "
                 "from rain, fit on v̂ out of sample (inner leave-one-season-out); the other links are identity; East is "
                 "the union of Central and South by the rule chosen nested on chained East Brier."),
        "fit": {
            "schema": SCHEMA, "protocol": X.protocol_stamp(), "as_of": str(tr.end.date()),
            "window": {"top": "T1 final: fit on every day through 2025-10-31",
                       "T1-holdout": "refit on days before 2023-07-01", "T2": "refit per held-out season on the other eight of 2016-17 … 2024-25"},
            "v_hat": ("a fit day's v̂ is the fold's head design refit without that day's season (inner leave-one-season-out); "
                      "a scored day's v̂ is the fold's head (Part B 2: shares fit on v̂ the same way they are used); a season "
                      f"whose inner head would read fewer than {HEAD_MIN_EVENTS} known-volume events is not fit on (no declared "
                      "Westside fallback, Part B 7; the fold's fit_days_dropped)"),
            "fit_rows": "the S3 oracle's scored rows of the split zones on the fold's training days (X-S3-UNCOV, X-LEDGER-SUSPECT, X-S3-CARRY, X-S3-QUIET)",
            "union_choice": (f"per fold, on East Brier (rain known) pooled over inner leave-one-season-out on its T2 seasons: "
                             f"the default {UNION_DEFAULT} stays unless a challenger is better (the 90% storm-block CI of "
                             f"Δ = BS(challenger) − BS({UNION_DEFAULT}) wholly below 0, B = {UNION_N_BOOT}, seed {SB.SEED}); "
                             "between two better challengers whose difference's CI includes 0, the one with fewer parameters"),
            "folds": [_fold_block(ff, geo) for ff in fits.values()],
            "bootstrap": {"n": n_boot, "seed": SB.SEED, "level": SB.LEVEL, "protocol": n_boot == SB.B_PROTOCOL},
            "scores": compact(scores),
        },
    }
    spec = V.clean(spec)
    C.check_s3_spec(spec, geo)
    for ff in fits.values():
        C.check_s3_spec(fold_spec(spec, *ff.fold.key), geo)
    return spec


def fold_spec(spec: dict, tier: str, fold: str, arm: str = SHARE) -> dict:
    """The full S3 spec of one fold (or its benchmark arm) rebuilt from s3_links.json's fit block, checked."""
    geo = G.stamped(spec)
    blocks = [b for b in spec["fit"]["folds"] if (b["tier"], b["fold"]) == (tier, fold)]
    if len(blocks) != 1:
        raise KeyError(f"s3_links.json holds {len(blocks)} folds {tier} {fold}")
    b = blocks[0]
    # a logit_logvol record holds its coefficients; any other kind (today's size_blend) is the share as recorded
    shares = {lid: {SHARE: dict(v["share"]["coef"]) if v["share"]["kind"] == SHARE else dict(v["share"]), "constant": v["constant"]}
              for lid, v in b["links"].items() if "constant" in v}
    phi = {lid: v["vol_share"] for lid, v in b["links"].items()}
    rules = {b["union"][z]["rule"] for z in union_zones(geo)}
    if len(rules) != 1:
        raise ValueError(f"s3_links.json {tier} {fold}: the union zones carry rules {sorted(rules)}; a fold chooses one")
    return build_spec(geo, shares, arm, phi, b["basin_median_mg"][split_basin(geo)], b["cofire"], rules.pop(),
                      kind=spec.get("kind", KIND))


# ── today's split on the city map (sfpuc-icon-t8s-osplits-lt2zone-lzflags) ──────────────────────

def split_pairs(geo) -> dict:
    """{split link id of ``geo``: the geo_v1 link of the served group SPLIT_GROUPS names}. Raises unless each pair
    holds the same basin, zone and outfalls (shared.geography), so the group's share is the link's."""
    g1 = G.get("geo_v1")
    out = {}
    for lk in split_links(geo):
        group = SPLIT_GROUPS[lk.id]                                 # a split link with no served group raises here
        hit = [x for x in g1.links if x.legacy_group == group]
        if len(hit) != 1:
            raise KeyError(f"geo_v1 holds {len(hit)} links of group {group!r}")
        if (hit[0].basin, hit[0].zone, set(hit[0].outfalls)) != (lk.basin, lk.zone, set(lk.outfalls)):
            raise ValueError(f"served group {group!r} ({hit[0].basin}>{hit[0].zone}, {sorted(hit[0].outfalls)}) is not "
                             f"{geo.version}'s {lk.id} ({sorted(lk.outfalls)}): its share cannot be the link's")
        out[lk.id] = hit[0].id
    if set(out) != set(SPLIT_GROUPS):
        raise KeyError(f"SPLIT_GROUPS maps {sorted(SPLIT_GROUPS)}; {geo.version} splits {sorted(out)}")
    return out


def served_split(bundle, folds, plan: dict, train: dict, events: pd.DataFrame, samples: pd.DataFrame, geo=None) -> dict:
    """{(tier, fold): {shares, vol_share, fit}}: the served stage 2 v2 split's shares of ``geo``'s split links per
    fold, as the build refits the served set's GEO_V1 adapter (stages_build.fold_specs on the served ``bundle`` and
    its S2 ``folds``, ``plan`` stages_s2._plan's by key: T1 the served stage2.json, every other fold
    stage2.fit_outfall_split on the fold's training days with the fold's own heads), read off the adapter's links
    through ``split_pairs``. A share that is not size_blend (a split with no size classes) raises."""
    pairs = split_pairs(geo or _geo())
    out = {}
    for f in folds:
        key = (f.tier, f.fold)
        specs, info = SB.fold_specs(bundle, f, plan[key][5], train, events, samples)
        links = specs["s3"]["links"]
        shares = {lid: dict(links[g]["share"]) for lid, g in pairs.items()}
        bad = {lid: s["kind"] for lid, s in shares.items() if s["kind"] != "size_blend"}
        if bad:
            raise ValueError(f"{key}: the served split's shares {bad} are not size_blend: no stage 2 v2 split to take")
        out[key] = {"shares": shares, "vol_share": {lid: float(links[g]["vol_share"]) for lid, g in pairs.items()},
                    "fit": {k: v for k, v in info.items() if k in ("fit", "fit_span", "n_fit_days", "n_events", "seasons")}}
    return out


def split_spec(base: dict, split: dict, name: str) -> dict:
    """s3_links.json of today's split on SFPUC4 (``SPLIT_KIND``): ``base``, a saved s3_links.json whose per-fold
    East union rule (with its choice), co-firing shares, constant-share benchmark and medians are kept exactly, with
    each fold's split links taking the served split's share and φ (``served_split``) and the fold's fit span the
    split's, inside the training seasons ``base`` records. Top level: the final fold's. ``name``: the stage candidate
    it is for (sources.s2; its S2 is the one ``base``'s union rule was chosen on). Every fold's spec and benchmark
    pass compose_v2; a fold either side lacks raises."""
    src = {k: v for k, v in base.items() if k not in SAVER_STAMPS}
    geo = G.stamped(src)
    recs = {(r["tier"], r["fold"]): r for r in src["fit"]["folds"]}
    if set(recs) != set(split):
        raise KeyError(f"{base['set']} records folds {sorted(recs)}; the split was fit on {sorted(split)}")
    folds = []
    for key, r in recs.items():
        s = split[key]
        if not set(s["fit"].get("seasons") or ()) <= set(r["train_seasons"]):    # T1's artifacts state a span only
            raise ValueError(f"{key}: the split was fit on seasons {s['fit']['seasons']}, outside the fold's {r['train_seasons']}")
        links = {lid: ({"share": s["shares"][lid], "constant": v["constant"], "vol_share": s["vol_share"][lid]}
                       if lid in s["shares"] else dict(v)) for lid, v in r["links"].items()}
        folds.append({**{k: r[k] for k in ("tier", "fold", "scores", "train_seasons", "basin_median_mg", "cofire", "union")},
                      "share_fit_span": s["fit"]["fit_span"], "links": links, "split_fit": s["fit"]})
    out = {"geography": geo.version, "pipeline": PIPELINE, "kind": SPLIT_KIND,
           "sources": {"s2": {**src["sources"]["s2"], "name": name, "records_from": base["set"],
                              "note": (f"{name}'s S2 is {base['set']}'s (its pickles and s2 section, no refit), so the union "
                                       f"rule {base['set']} chose on it per fold is this S2's. The Westside shares are not "
                                       "fit on any S2's v̂ (Part B 2 does not hold for them, as it does not for the served "
                                       "set): today's split reads the filed volume, the served set's fold heads only where "
                                       "none is filed")},
                       "split": ("the served stage 2 v2 split per fold (stages_build.fold_specs: stage2.fit_outfall_split by "
                                 "the served recipe), groups Ocean Beach and Baker-China as westside>ocean and "
                                 "westside>baker_china (split_pairs)"),
                       "union": f"{base['set']}'s per-fold records, kept exactly (union rule and choice, co-firing shares, "
                                "the constant-share benchmark)",
                       "ledger": src["sources"]["ledger"], "rain": src["sources"]["rain"]},
           "note": ("Today's beach split on the city map: Westside's two links share its overflow by stage 2 v2's size "
                    "classes, p·[w·large + (1 − w)·small] with w = v̂/(v̂ + the group's median filed volume) and φ 1, refit "
                    f"per fold the served way; the other links are identity; East is the union of Central and South by "
                    f"the rule {base['set']} chose nested per fold."),
           "fit": {"schema": SCHEMA, "protocol": X.protocol_stamp(), "as_of": src["fit"]["as_of"],
                   "window": {"top": "T1 final: the served stage2.json, fit on every day through 2025-10-31",
                              "T1-holdout": "refit on days before 2023-07-01",
                              "T2": "refit per held-out season on the other eight of 2016-17 … 2024-25"},
                   "split": "per fold: the served split's shares and φ (split_fit: what stages_build.fold_specs fit them on)",
                   "union_choice": src["fit"]["union_choice"], "folds": folds}}
    top = fold_spec(out, "T1", S2.FOLD_FINAL)
    out.update(links=top["links"], union=top["union"], cofire=top["cofire"])
    C.check_s3_spec(out, geo)
    for key in recs:
        for arm in (SHARE, "constant"):
            C.check_s3_spec(fold_spec(out, *key, arm=arm), geo)
    return V.clean(out)


# ── writing ─────────────────────────────────────────────────────────────────

def _saver():
    """stages_candidates (P8's geography-aware saver; imported on use, as it imports this module when it assembles)."""
    import stages_candidates as SC  # noqa: PLC0415
    return SC


def for_compose(saved: dict) -> dict:
    """A saved s3_links.json as compose_v2 reads it: the saver's stamps (``SAVER_STAMPS``: the set, the component)
    checked to be there and dropped, the rest checked by compose_v2.check_s3_spec."""
    missing = [k for k in SAVER_STAMPS if k not in saved]
    if missing:
        raise KeyError(f"a saved s3_links.json carries the saver's stamps; this one lacks {missing}")
    if saved["component"] != KIND:
        raise ValueError(f"s3_links component {saved['component']!r}, not {KIND!r}")
    spec = {k: v for k, v in saved.items() if k not in SAVER_STAMPS}
    return C.check_s3_spec(spec, G.stamped(spec))


def _protocol_scores(spec: dict) -> None:
    b = spec["fit"]["bootstrap"]
    if not b["protocol"]:
        raise ValueError(f"s3_links.json carries scores: B = {b['n']} < the protocol's {SB.B_PROTOCOL}")


def write(spec: dict, name: str, root=None) -> Path:
    """Save the spec as the 's3_links' component of stage candidate ``name`` through stages_candidates.save_component
    (``root`` overrides the candidates' directory: tests; any other directory of the repository raises there, Part
    B 13) and read it back through stages_candidates.load_set and compose_v2 (``for_compose``); returns the file.
    Without ``root``, scores under the protocol's bootstrap are refused."""
    C.check_s3_spec(spec, G.stamped(spec))
    if root is None:
        _protocol_scores(spec)
    SC = _saver()
    d = Path(SC.save_component(name, "s3_links", {**spec, "component": KIND}, root=root))
    if for_compose(SC.load_set(name, root=root).s3_links)["links"] != spec["links"]:
        raise AssertionError("the saved s3_links.json does not read back as written")
    return d / SPEC_FILE


def summary(res: Result) -> list:
    """Plain lines for the CLI."""
    sc, lines = res.scores, []
    top = res.spec["links"]
    for lid in sorted(top):
        sh = top[lid]["share"]
        if sh["kind"] == SHARE:
            lines.append(f"  {lid}: s(v̂) = logistic({sh['coef']['a']:+.3f} {sh['coef']['b']:+.3f}·log1p v̂), φ {top[lid]['vol_share']:.3f}")
    lines.append(f"  union: {res.spec['union']}")
    for arm in ARMS:
        c = sc["oracle"][arm]["pooled"]
        lines.append(f"  oracle {arm:13} " + "  ".join(f"{t} BSS {c[t]['bss']:+.3f} (BS {c[t]['bs']:.4f}, n {c[t]['n']})" for t in WINDOWS if t in c))
    for rule in RULES + ("nested",):
        c = sc["union"][rule][union_zones(_geo())[0]]
        lines.append(f"  east {rule:9} " + "  ".join(f"{t} BSS {c[t]['bss']:+.3f} (BS {c[t]['bs']:.5f}, n {c[t]['n']})" for t in WINDOWS if t in c))
    uz = union_zones(_geo())[0]
    choices = {f"{b['tier']} {b['fold']}": b["union"][uz]["rule"] for b in res.spec["fit"]["folds"]}
    lines.append(f"  union choice per fold: {choices}")
    if "s3b" in sc:
        s = sc["s3b"]
        lines.append(f"  S3b Δ {s['delta']:+.4f}, 90% CI [{s['ci90'][0]:+.4f}, {s['ci90'][1]:+.4f}], 95% [{s['ci95'][0]:+.4f}, "
                     f"{s['ci95'][1]:+.4f}], p one-sided {s['p_one_sided']:.3f}, n {s['n']} in {s['n_blocks']} blocks: {s['verdict']}")
    return lines


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n-boot", type=int, default=SB.B_PROTOCOL)
    ap.add_argument("--candidate", required=True, help="fit on this stage candidate's own S2 (candidate_s2) and save into it")
    ap.add_argument("--write", action="store_true", help=f"save {SPEC_FILE} as the set's s3_links component")
    a = ap.parse_args(argv)
    t0 = time.time()
    s2 = candidate_s2(a.candidate)
    print(f"  {s2.name}'s S2 read in {time.time() - t0:.1f}s; fidelity {s2.fidelity}")
    res = run(s2=s2, n_boot=a.n_boot)
    for line in summary(res):
        print(line)
    for b in res.spec["fit"]["folds"]:
        if b.get("fit_days_dropped"):
            print(f"  {b['tier']} {b['fold']}: fit days left out {b['fit_days_dropped']}")
    if a.write:
        print(f"  wrote {write(res.spec, name=a.candidate).relative_to(REPO)} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
