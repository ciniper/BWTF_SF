#!/usr/bin/env python3
"""stages_s4_v3 — stage S4 v3: zone overflow history → water quality, per zone, with a
rain background (P8c; STAGES_DESIGN.md Part C §3.4 in full, its "Why S4 needs a rain
term" included, §6 change 3, §7 s4_quality.json; Part A A1, A2; Part B 4, 5, 6, 7;
STAGES_PROTOCOL.md stages_v3 §2 windows, §4 metrics, §6 blocks, §7 the S4 rules,
§8 the S4 row).

**Why.** The served table (impact v2 per GEO_V1 link) pools dry and wet days into
one baseline per link and has no rain term. Fed the true overflows it scored worse
than the chained composition (phase 1, post-training Brier 0.182 vs 0.150): the
upstream probabilities, which track rain, stood in for the missing term. On sampled
zone-days the exceedance rate rises with rain even with no overflow in the week
(design §3.4: Ocean 0.04 dry → 0.09 wet, East 0.32 → 0.60). The served artifacts'
table also breaks "a bigger overflow never lowers the impact": its large curve sits
below its small one in some bucket of five of its six links. v3 fixes all three, at
the zone, the unit the public claim and the S4 truth are stated in (A2).

**The model** (design §3.4; compose_v2's zone_v3 spec, the same algebra for oracle
and chained, so the two differ only in their inputs):

    q_z(D) = 1 − (1 − b_z(D)) · Π_{k=0..7} (1 − o_z(D−k) · x_z(k, s))
    b_z(D) = logistic(α_z + δ1·max(0, rain3 − 0.1″) + δ2·max(0, rain3 − 0.5″) + ω·wet_season)

- o_z(D−k): the zone overflowed on D−k (0/1 for the oracle; S3's p_z chained), with
  its size s: large when v ≥ the zone median (compose_v2.x_curves). Sizes follow
  compose_v2's zone rule (a zone's size is Σ over its feeding basins of the basin's
  largest link size into it) at vol_share φ = 1, so a zone's size is its feeding
  basins' filed volume that day: the same outfalls under geo_v1 and sfpuc4_v1, so the
  fit serves both geographies (the zones are the same; ``oracle_history`` asserts the
  invariance). The median is the zone's overflow-day sizes on the fold's training days.
  **The size rule is the S3 spec's (fit = use across stages).** An S3 spec with a fitted
  φ ≠ 1 (stages_s3_links' Westside links) sizes a zone by the link's own volume (oracle)
  or φ·v̂ (chained), another scale: ``run(s3_set=NAME)`` fits each fold on that stage
  candidate's per-fold φ (``size_rules``), every fold record stores the φ it was fit on,
  and ``write`` refuses to put a spec into a set whose s3_links.json sizes zones with
  other φ (or a φ ≠ 1 spec into a set with no S3 to pin it). **So is the v̂.** An
  overflow with no measured volume is sized φ·v̂; by default from the served set's fold
  heads through its geography. ``run(s2_source=stages_s3_links.candidate_s2(NAME))``
  reads that stage candidate's own fold v̂ and its history under sfpuc4_v1, the way
  the build composes the candidate (in the finals' fold, as of 2026-08-17, five overflow
  zone-days with no measured volume differ: East 2017-02-03 is 0.97 MG on the served
  heads, 14.1 MG on the candidate's), and ``write`` refuses a set holding its own S2 unless the study read it.
- x_z(k, s): buckets 0, 1, 2, 3, 4–5, 6–7 × small / large. Cumulative
  parameterisation: x(j) = Σ_{i ≥ j} d_i with every d_i ≥ 0, so each curve is
  non-increasing in k by construction; large ≥ small in every bucket and x ≤ 1 − 1e−6
  are linear constraints of the fit (SLSQP). L2 toward the zone-pooled curve: the
  same model with one x shared by the four zones is fit first, then each zone's x
  pays λ·Σ (x_z − x̄)². λ is chosen inside each fold by nested leave-one-season-out
  (the fold's own training seasons; lowest inner Brier, the larger λ on a tie), never
  on scored days (protocol §2).
- b_z: α_z per zone; δ1, δ2 and ω shared by the four zones, as §3.4 writes them (no
  zone subscript). rain3 = the two-gauge masked rain D−2…D (truth.gauge_rain's mean:
  gauge_outage_v1 on every day, 'T' = 0, one gauge when the other is out), the series
  compose_v2.background reads. wet_season = October–April (compose_v2.WET_SEASON_MONTHS),
  SF's rainy season, kept as the design's default: those months hold 109 of the 112
  zone overflow days on record (3 in May, none June–September; as of 2026-08-17), and
  choosing other months from the data would be a design search. More rain never
  lowers the background: δ1 ≥ 0 and δ1 + δ2 ≥ 0 (each segment's slope ≥ 0). ω is
  free. **No n_sampled predictor** (Part B 5): the number of stations sampled is
  unknown at issue time, so it is a stratum only (compose_v2.check_s4_spec refuses it).

**Fit = use (Part B 6).** Maximum likelihood of compose_v2's own q. The objective
calls compose_v2.lingering — the noisy-OR over the prior event days that
compose_v2.s4 applies — on the x tensor and background of the parameters
(``q_parts``); its gradient is that product's derivative (o is 0/1 on the oracle
inputs, which ``layout`` asserts). Every fit then builds its spec, runs compose_v2.s4
on it over the fit's days and raises unless that reproduces the objective's q on
every fit row (to 1e−10). Never a table bucketed by "days since the last event".

**Rows.** Sampled zone-days on the oracle's inputs (the true zone history and its true
size), through exclusions.apply under sfpuc4_v1 in the protocol's S4 order
(X-S4-UNSAMPLED → X-S4-HISTUNK → X-LEDGER-SUSPECT → X-S4-RESAMPLE → X-ALL-INSAMPLE):
the scored rows are first-look samples (Part B 4). The fit takes the scored rows of its
training days: X-S4-RESAMPLE rows are out of the fit and of the primary score (they are
scored as their own stratum). X-S4-FOLLOWUP (fit only): OCEAN#20 / 21 / 22 are sampled
only after overflows, so on the rows where q is the background alone — no zone overflow
on D−7…D — the fit's truth leaves their results out (a row sampled only there leaves
the fit); with an overflow in the week every station counts. Truth: samples.D10_SOURCES
(DataSF 2020-07 →, STARDB 2016-10 → 2020-07, Poo Bot 2015-12 → 2017-01, de-duplicated).

**Folds (protocol §2).** One fit per S2 fold of the served set (stages_s2._plan):
T2 — each season 2016-17 … 2024-25 held out, fit on the other eight; T1-holdout — fit
on days before 2023-07-01, scored 2023-07-01 → 2025-10-31; finals (T1) — fit through
2025-10-31, scored 2025-11-01 → the data end. A fit row is a day of the fold's
training days whose whole read span misses the scored window, so no fold reads a day of
its season: its history D−7…D, and the span its own S4 rules read, D−11…D+4
(``EXCL_REACH``: X-LEDGER-SUSPECT flags a row from triggers on D−8…D+3, each read over
its D−3…D+1; a trigger inside the window would otherwise decide which training rows are
fit). The zone medians are the fold's. The finals are the spec written.

**Benchmarks** (design §3.4), on the same rows:
    climatology   the reference itself (zone × month ±1, the fold's training days:
                  stages_build.references); BSS 0 by construction, its Brier shown
    persistence   SFPUC's practice: the zone's newest sample in D−7…D−1 (the feed shows
                  a sample the day after collection), its result 0/1; none → climatology
    background    v3's background alone, refit with x ≡ 0 (the same function, the same
                  rows): does the overflow history add skill beyond rain?
    served table  the GEO_V1 adapter refit per fold by its own recipe on DataSF + Poo Bot
                  (stages_build.fold_specs: stage 2 v2's shares, train_v4.fit_impact_table),
                  on the true history (stages_build.true_history), zone = max over links
    rain logistic per zone, an L2 logistic (C = 1) on log1p rain on D, D−1, D−2 and the
                  7-day total, on the fit's rows and truth
The chained arms read the served chain — the set's S2 out of fold (rain known) →
its S3 (the fold's GEO_V1 adapter) — under both S4s: S4 v3 at the zone, the table per
link; that is what "usable under geo_v1 zones" buys, and what the defect is measured on.

**Primary (protocol §8, S4 row).** S4 v3 − the served table, zone-pooled ΔBS on
first-look samples, oracle entry, T2, 2,000 storm-block resamples (seed 0, 90% CI).
Passes on superiority (the CI's upper bound < 0), or on non-inferiority at +5% (upper
bound < 0.05 × the table's BS) together with the fix of the oracle-worse-than-chained
defect: S4 v3's oracle is not worse than its own chained composition on T2 (Δ = BS
oracle − BS chained ≤ 0, and its CI not wholly above 0). Both S4s' Δ are reported, on
T2 and on T1, with whether the table shows the defect on these rows (its oracle − chained
CI wholly above 0: protocol §6's honesty rule, never the point estimate). When it does
not, the verdict says the fix was not tested rather than "fixed". Beside it (A2):
the Candlestick-station sensitivity score, East's forecasts scored on the Candlestick
stations only (the East stations SFPUC4's station_basin puts in South: Jackrabbit,
Windsurfer, Sunnydale), first looks read on those stations. And honest power (Part B
4): per zone, the first-look rows in an overflow's tail and the overflow days behind them.

**Artifact.** ``write`` → s4_quality.json (design §7: kind zone_v3, unit zone,
background.kind logistic, buckets, zone_median_mg, monotone true, sources, fit) for
the finals, checked by compose_v2.check_s4_spec, saved as the 's4_quality' component
of stage candidate ``SET_NAME`` through stages_candidates.save_component (read back
through stages_candidates.load_set and ``for_compose``), or, without that module,
under data/models/stages_candidates/_s4_v3/. Its fit block holds every fold's spec (so
each fold's monotone tails and the φ it was sized with are on file), the λ choices and
the scores. Nothing is written into data/models/ outside stages_candidates/, nothing
under stages_candidates/ carries a bootstrap below the protocol's 2,000, and nothing on
the served path changes. No module-level IO.

    venv/bin/python features/forecast/src/models/stages_s4_v3.py [--write] [--set NAME] [--s3-set NAME] [--s2-set NAME] [--n-boot 2000]
"""
from __future__ import annotations

import argparse
import copy
import json
import math
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

import compose_v2 as C  # noqa: E402  (the S4 algebra, the spec check, the vocabulary)
import exclusions as X  # noqa: E402  (the S4 rules, the context, X-S4-FOLLOWUP's stations)
import samples as SMP  # noqa: E402  (D10_SOURCES, zone_sample_days)
import stages_build as SB  # noqa: E402  (the served set, its fold specs, true_history, references, scores)
import stages_entries as E  # noqa: E402  (the rain-known frames, the data end)
import stages_s2 as S2  # noqa: E402  (the served set's S2 folds)
import train_v4 as T4  # noqa: E402  (read only: training frames, events, the served table's samples)
import truth as T  # noqa: E402
import verify as V  # noqa: E402
from impact import BUCKET_ORDER, bucket_index  # noqa: E402  (the served buckets: 0, 1, 2, 3, 4-5, 6-7)
from shared import clock  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

NAME = "s4_v3"
GEOGRAPHY = "sfpuc4_v1"
PIPELINE = "stages_v1"
KIND = "zone_v3"                                     # the spec kind, and the component's name in a candidate manifest
SET_NAME = "sfpuc4_s4_v3"                            # the stage candidate write() saves to by default
KNOTS = (0.1, 0.5)                                   # §3.4: hinge(rain3, 0.1″), hinge(rain3, 0.5″)
FEATURES = tuple(f"hinge_rain3_{k}" for k in KNOTS) + ("wet_season",)   # compose_v2's vocabulary; never n_sampled
WET_SEASON_MONTHS = C.WET_SEASON_MONTHS              # October–April
LAGS = C.LAGS                                        # k = 0…7
NB = len(BUCKET_ORDER)                               # 6 buckets
BK = tuple(bucket_index(k) for k in LAGS)            # lag → bucket
X_MAX = 1.0 - 1e-6                                   # x < 1, so a clean sample after an overflow is never impossible
LAMBDA_GRID = (0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0)   # L2 toward the pooled curve: 0.1 ≈ free, 10,000 ≈ pooled
FIT_USE_TOL = 1e-10                                  # compose_v2.s4 on the spec vs the objective's q
B_PROTOCOL, SEED, LEVEL = SB.B_PROTOCOL, SB.SEED, SB.LEVEL   # protocol §6: 2,000 resamples, seed 0, 90%
NI_MARGIN = 0.05                                     # protocol §8: non-inferiority at +5%
TIERS = S2.TIERS                                     # T1 (finals), T1-holdout, T2
FINAL = (S2.TIERS[0], S2.FOLD_FINAL)                 # ("T1", "final"): the spec s4_quality.json holds
CANDIDATES_ROOT = FORECAST / "data" / "models" / "stages_candidates"
OUT_DIR = CANDIDATES_ROOT / f"_{NAME}"               # the fallback without stages_candidates (a working directory)
SPEC_FILE = "s4_quality.json"
SAVER_STAMPS = ("set", "component")                  # what save_component adds that compose_v2.check_s4_spec does not know
RAIN_LOGIT_C = 1.0                                   # the rain-only logistic: sklearn's default L2, never tuned
PERSIST_DAYS = 7                                     # persistence: the newest sample in D−7…D−1
ARMS = ("v3", "served", "background", "rain_logit", "persistence", "climatology")
# The days a fit row's inputs and its S4 rules read, around its D: the history D−7…D, widened by X-LEDGER-SUSPECT
# (a basin suspect on D−7…D comes from a trigger on D−8…D+3, whose wet days and ledger are read over its D−3…D+1).
EXCL_REACH = (len(LAGS) - 1 + X.SUSPECT_BEFORE + X.SUSPECT_AFTER, X.SUSPECT_BEFORE + X.SUSPECT_AFTER)   # (11, 4)
PHI_TOL = 1e-12                                      # write(): the fit's φ vs the set's S3 φ


# ── the oracle input: true zone history and size ───────────────────────────

def unit_phi(phi: dict | None = None) -> dict:
    """{sfpuc4_v1 link id: φ}: 1 on every link but where ``phi`` says otherwise (an unknown link id raises)."""
    ids = [lk.id for lk in G.get(GEOGRAPHY).links]
    phi = dict(phi or {})
    unknown = set(phi) - set(ids)
    if unknown:
        raise KeyError(f"vol_share for links {GEOGRAPHY} lacks: {sorted(unknown)}")
    out = {lid: float(phi.get(lid, 1.0)) for lid in ids}
    bad = {lid: v for lid, v in out.items() if not (0.0 < v <= 1.0) or not math.isfinite(v)}
    if bad:
        raise ValueError(f"vol_share must lie in (0, 1]: {bad}")
    return out


def phi_on(geo, phi: dict | None) -> dict | None:
    """``phi`` (sfpuc4_v1 link ids) as {link id of ``geo``: φ} for compose_v2.benchmark_s3_spec, or None when every
    φ is 1. The history is read through ``geo`` (the served set's), so a link with φ ≠ 1 must be a link of ``geo``
    too — same id, basin, zone and outfalls, and its zone fed by the same links — or the zone's size would read
    other outfalls; anything else raises."""
    full = unit_phi(phi)
    if all(v == 1.0 for v in full.values()):
        return None
    g4 = G.get(GEOGRAPHY)
    mine = {lk.id: lk for lk in geo.links}
    for lid, v in full.items():
        if v == 1.0:
            continue
        a, b = next(lk for lk in g4.links if lk.id == lid), mine.get(lid)
        if b is None or (a.basin, a.zone, tuple(a.outfalls)) != (b.basin, b.zone, tuple(b.outfalls)) or \
                {lk.id for lk in g4.links_into(a.zone)} != {lk.id for lk in geo.links_into(a.zone)}:
            raise ValueError(f"vol_share {v} on {lid}: {geo.version} has no link with its outfalls and zone, so the "
                             f"history read through {geo.version} cannot size {a.zone} by it")
    return {lk.id: full.get(lk.id, 1.0) for lk in geo.links}


def zone_size_spec(geo, phi: dict | None = None) -> dict:
    """The S3 spec whose zone-size rule S4 v3's sizes follow (compose_v2.benchmark_s3_spec): every link at φ = 1,
    so a zone's size is its feeding basins' volume, or at the S3 candidate's φ (``phi``, sfpuc4_v1 link ids:
    ``phi_on``). Identity shares; only vol_share and the unit are read."""
    return {"s3": C.benchmark_s3_spec(geo, "identity", vol_share=phi_on(geo, phi)), "s4": {"unit": "zone"}}


def size_rules(saved_s3: dict, keys) -> dict:
    """{(tier, fold): {sfpuc4_v1 link id: φ}} from a saved s3_links.json (stages_candidates' payload, its saver
    stamps set aside): the vol_share each fold's S3 spec (stages_s3_links.fold_spec) sizes a link with. A fold the
    file does not hold raises (Part B 1: every fold's S3 is its own)."""
    import stages_s3_links as S3L  # noqa: PLC0415  (P8b; read only when sizes follow a saved S3 candidate)
    spec = {k: v for k, v in saved_s3.items() if k not in SAVER_STAMPS}
    if spec.get("geography") != GEOGRAPHY:
        raise ValueError(f"an s3_links.json of {spec.get('geography')!r}; S4 v3 sizes zones under {GEOGRAPHY}")
    return {tuple(k): unit_phi({lid: lk["vol_share"] for lid, lk in S3L.fold_spec(spec, *k)["links"].items()})
            for k in keys}


def oracle_history(geo, tr: SB.Truths, days: pd.DatetimeIndex, v_hat: pd.DataFrame, phi: dict | None = None) -> tuple:
    """(compose_v2.History per zone, unknown mask): the true zone overflow (0/1) and its true size on `days`, by
    stages_build.true_history on ``zone_size_spec`` (φ = 1: the feeding basins' filed volume; a fitted φ: the
    link's own volume; an overflow with no measured volume takes φ·v̂, as the served oracle does). The 0/1
    history must equal truth.zone_overflow under sfpuc4_v1 (zones are geography-invariant, §3.3)."""
    hist, unknown = SB.true_history(geo, zone_size_spec(geo, phi), tr, days, v_hat)
    zo = T.zone_overflow(GEOGRAPHY, days[0], days[-1]).pivot(index="date", columns="zone", values="y")
    zo = zo.reindex(days)[list(ZONES)]
    if not np.array_equal(zo.fillna(0.0).to_numpy(dtype=float), hist.p[list(ZONES)].to_numpy(dtype=float)):
        raise AssertionError(f"the zone history under {geo.version} differs from {GEOGRAPHY}'s zone_overflow")
    return C.History(hist.p[list(ZONES)], hist.v[list(ZONES)]), unknown[list(ZONES)]


# ── the fit's layout: one run of days, the rows, the features ──────────────

def rain_features(rain: pd.Series, index: pd.DatetimeIndex) -> np.ndarray:
    """(days, 3): hinge(rain3, 0.1), hinge(rain3, 0.5), wet_season on `index`, as compose_v2.background reads them
    (rain3 = the rolling 3-day sum of the two-gauge rain ending on D). A gap in the rain raises."""
    s = pd.Series(rain, dtype=float)
    s.index = pd.DatetimeIndex(s.index).normalize()
    need = pd.date_range(index[0] - pd.Timedelta(days=2), index[-1])
    got = s.reindex(need)
    if got.isna().any():
        raise ValueError(f"rain must cover {need[0].date()} … {need[-1].date()} (first gap {got[got.isna()].index[0].date()})")
    rain3 = got.rolling(3).sum().to_numpy()[2:]
    cols = [np.maximum(0.0, rain3 - k) for k in KNOTS] + [np.isin(index.month, WET_SEASON_MONTHS).astype(float)]
    return np.column_stack(cols)


@dataclass(frozen=True)
class Layout:
    """A fit's inputs: the run of days, the 0/1 zone history, its sizes, the size class by the fold's medians,
    the background features, and the fit rows (positions, truth)."""
    index: pd.DatetimeIndex
    zones: tuple
    O: np.ndarray             # (days, zones) 0/1
    V: np.ndarray             # (days, zones) MG
    medians: dict             # zone → median overflow size on the training days (MG)
    large: np.ndarray         # (days, zones) bool: V ≥ the zone median
    F: np.ndarray             # (days, 3) FEATURES
    rt: np.ndarray            # fit rows: day positions
    rz: np.ndarray            # fit rows: zone positions
    y: np.ndarray             # fit rows: truth (0/1)
    rain: pd.Series

    @property
    def n(self) -> int:
        return len(self.y)


def zone_medians(hist: C.History, train_days: pd.DatetimeIndex) -> dict:
    """{zone: the median size of its overflow days among `train_days`} (compose_v2's zone-size rule, φ = 1)."""
    out = {}
    p, v = hist.p.reindex(train_days), hist.v.reindex(train_days)
    for z in ZONES:
        sizes = v.loc[p[z] == 1, z]
        if len(sizes) == 0 or not (sizes > 0).any():
            raise ValueError(f"zone {z}: no overflow with a size on the training days; its median is undefined")
        out[z] = float(np.median(sizes.to_numpy(dtype=float)))
    return out


def layout(hist: C.History, rain: pd.Series, rows: pd.DataFrame, medians: dict) -> Layout:
    """The fit's layout on hist's days. ``rows``: zone, date, y_fit (one per fit row; every date inside the days
    and at least 7 days after their first). The history must be the oracle's 0/1 (the gradient reads it so)."""
    index = pd.DatetimeIndex(hist.p.index)
    O = hist.p[list(ZONES)].to_numpy(dtype=float)
    if not np.isin(O, (0.0, 1.0)).all():
        raise ValueError("the fit reads the oracle's 0/1 zone history (S3's probabilities are for use, not fit)")
    Vv = hist.v[list(ZONES)].to_numpy(dtype=float)
    if not np.isfinite(Vv).all():
        raise ValueError("a zone size is not a finite number")
    med = np.array([medians[z] for z in ZONES])
    pos = index.get_indexer(pd.DatetimeIndex(rows["date"]))
    if (pos < len(LAGS) - 1).any():
        raise ValueError("a fit row's history D−7…D starts before the layout's days")
    unknown = set(rows["zone"]) - set(ZONES)
    if unknown:
        raise KeyError(f"fit rows in zones shared/zones.py lacks: {sorted(unknown)}")
    zpos = np.array([list(ZONES).index(z) for z in rows["zone"]], dtype=int)
    if pd.DataFrame({"t": pos, "z": zpos}).duplicated().any():
        raise ValueError("a zone-day appears twice among the fit rows")
    y = rows["y_fit"].to_numpy(dtype=float)
    if not np.isin(y, (0.0, 1.0)).all():
        raise ValueError("a fit row's truth is not 0 or 1")
    return Layout(index, tuple(ZONES), O, Vv, dict(medians), Vv >= med[None, :], rain_features(rain, index),
                  pos, zpos, y, rain)


# ── parameters ↔ curves ────────────────────────────────────────────────────

MODES = ("zone", "pooled", "background")


def _n_params(mode: str, Z: int) -> int:
    return Z + 3 + {"zone": Z * 2 * NB, "pooled": 2 * NB, "background": 0}[mode]


def _curves(theta: np.ndarray, mode: str, Z: int) -> tuple:
    """(xs, xl), each (zones, buckets): x(j) = Σ_{i ≥ j} d_i (cumulative from the tail)."""
    if mode == "background":
        z = np.zeros((Z, NB))
        return z, z.copy()
    d = theta[Z + 3:].reshape(-1, 2, NB)            # (blocks, small|large, buckets)
    x = np.cumsum(d[:, :, ::-1], axis=2)[:, :, ::-1]
    if mode == "pooled":
        x = np.repeat(x, Z, axis=0)
    return x[:, 0, :], x[:, 1, :]


def _decrements(xs: np.ndarray, xl: np.ndarray) -> np.ndarray:
    """(2, buckets) decrements d_j = x(j) − x(j + 1) (x past the last bucket = 0) of two non-increasing
    curves: the inverse of the cumulative map in ``_curves``."""
    x = np.stack([xs, xl])
    return x - np.concatenate([x[:, 1:], np.zeros((2, 1))], axis=1)


def _x_tensor(xs: np.ndarray, xl: np.ndarray, large: np.ndarray) -> np.ndarray:
    """(lags, days, zones): x_z(k, size of the overflow on day j), compose_v2.x_curves' layout."""
    return np.stack([np.where(large, xl[:, BK[k]][None, :], xs[:, BK[k]][None, :]) for k in LAGS])


def _eta(theta: np.ndarray, L: Layout) -> np.ndarray:
    Z = len(L.zones)
    a, s1, s2, om = theta[:Z], theta[Z], theta[Z + 1], theta[Z + 2]
    # δ1 = s1 (the slope on 0.1–0.5″), δ2 = s2 − s1 (s2 = the slope above 0.5″)
    return a[None, :] + s1 * L.F[:, [0]] + (s2 - s1) * L.F[:, [1]] + om * L.F[:, [2]]


def background_coef(theta: np.ndarray, Z: int) -> dict:
    """{zone: {intercept, hinge_rain3_0.1, hinge_rain3_0.5, wet_season}}: compose_v2's logistic coefficients."""
    s1, s2, om = (float(v) for v in theta[Z:Z + 3])
    return {z: {"intercept": float(theta[i]), FEATURES[0]: s1, FEATURES[1]: s2 - s1, FEATURES[2]: om}
            for i, z in enumerate(ZONES)}


# ── the objective: the likelihood of compose_v2's q ────────────────────────

def q_parts(theta: np.ndarray, L: Layout, mode: str) -> tuple:
    """(q, Π_k (1 − o·x), b, xs, xl) on the layout's days: q = compose_v2.lingering(o, x, b), the S4 algebra
    compose_v2.s4 applies, and the same product without the background (for the gradient)."""
    B = 1.0 / (1.0 + np.exp(-_eta(theta, L)))
    xs, xl = _curves(theta, mode, len(L.zones))
    Xt = _x_tensor(xs, xl, L.large)
    return C.lingering(L.O, Xt, B), 1.0 - C.lingering(L.O, Xt), B, xs, xl


def objective(theta: np.ndarray, L: Layout, mode: str, lam: float = 0.0, xbar=None, grad: bool = True):
    """(mean negative log-likelihood + λ·Σ(x_z − x̄)²/n, its gradient) of q = compose_v2.lingering(o, x, b)
    on the fit rows. ``xbar`` = (xs, xl) of the pooled curve (zone mode, λ > 0)."""
    Z = len(L.zones)
    q, acc, B, xs, xl = q_parts(theta, L, mode)
    qr = np.clip(q[L.rt, L.rz], 1e-15, 1 - 1e-15)
    y = L.y
    nll = -float(np.sum(y * np.log(qr) + (1 - y) * np.log(1 - qr)))
    pen = 0.0
    if mode == "zone" and lam > 0:
        pen = lam * float(np.sum((xs - xbar[0]) ** 2) + np.sum((xl - xbar[1]) ** 2))
    f = (nll + pen) / L.n
    if not grad:
        return f
    g_q = -(y / qr - (1 - y) / (1 - qr))             # dNLL/dq per row
    T_ = L.O.shape[0]
    G = np.zeros_like(q)
    np.add.at(G, (L.rt, L.rz), g_q * acc[L.rt, L.rz])          # dNLL/db = g·Π(1 − o x)
    dEta = G * B * (1 - B)
    g = np.zeros_like(theta)
    g[:Z] = dEta.sum(axis=0)
    g[Z] = float((dEta * (L.F[:, [0]] - L.F[:, [1]])).sum())
    g[Z + 1] = float((dEta * L.F[:, [1]]).sum())
    g[Z + 2] = float((dEta * L.F[:, [2]]).sum())
    if mode != "background":
        H = np.zeros_like(q)
        np.add.at(H, (L.rt, L.rz), g_q * (1 - B[L.rt, L.rz]) * acc[L.rt, L.rz])
        gxs, gxl = np.zeros((Z, NB)), np.zeros((Z, NB))
        for k in LAGS:
            w = H[k:] * L.O[:T_ - k]                 # an overflow on t' = t − k, read by row t (o ∈ {0, 1})
            lg = L.large[:T_ - k]
            j = BK[k]
            gxl[:, j] += (w * lg).sum(axis=0) / (1 - xl[:, j])
            gxs[:, j] += (w * ~lg).sum(axis=0) / (1 - xs[:, j])
        if mode == "zone" and lam > 0:
            gxs += 2 * lam * (xs - xbar[0])
            gxl += 2 * lam * (xl - xbar[1])
        gx = np.stack([gxs, gxl], axis=1)            # (zones, 2, buckets)
        if mode == "pooled":
            gx = gx.sum(axis=0, keepdims=True)
        g[Z + 3:] = np.cumsum(gx, axis=2).ravel()    # d_i feeds x(j) for every j ≤ i
    return f, g / L.n


def _constraints(mode: str, Z: int):
    """large ≥ small in every bucket, and x_large(0) ≤ X_MAX, per curve block (linear in the decrements)."""
    from scipy.optimize import LinearConstraint
    if mode == "background":
        return []
    blocks = Z if mode == "zone" else 1
    npar = _n_params(mode, Z)
    rows, lo, hi = [], [], []
    for b in range(blocks):
        o = Z + 3 + b * 2 * NB
        for j in range(NB):
            a = np.zeros(npar)
            a[o + NB + j:o + 2 * NB] = 1.0           # Σ_{i ≥ j} dl_i
            a[o + j:o + NB] -= 1.0                   # − Σ_{i ≥ j} ds_i
            rows.append(a)
            lo.append(0.0)
            hi.append(np.inf)
        a = np.zeros(npar)
        a[o + NB:o + 2 * NB] = 1.0
        rows.append(a)
        lo.append(-np.inf)
        hi.append(X_MAX)
    return [LinearConstraint(np.array(rows), np.array(lo), np.array(hi))]


def _bounds(mode: str, Z: int) -> list:
    b = [(-20.0, 20.0)] * Z + [(0.0, 50.0), (0.0, 50.0), (-10.0, 10.0)]
    return b + [(0.0, 1.0)] * (_n_params(mode, Z) - len(b))


START_X = (np.array([0.5, 0.3, 0.2, 0.1, 0.05, 0.02]), np.array([0.7, 0.45, 0.3, 0.15, 0.08, 0.03]))


def start(L: Layout, mode: str, from_theta: np.ndarray | None = None, from_mode: str | None = None) -> np.ndarray:
    """A feasible start: the zones' no-history base rates and a gentle default curve, or another fit's
    parameters (the zone fit starts at the pooled one)."""
    Z = len(L.zones)
    if from_theta is not None:
        head = from_theta[:Z + 3].copy()
        xs, xl = _curves(from_theta, from_mode, Z)
        if mode == "zone":
            tail = np.concatenate([_decrements(xs[i], xl[i]).ravel() for i in range(Z)])
        elif mode == "pooled":
            tail = _decrements(xs[0], xl[0]).ravel()
        else:
            tail = np.zeros(0)
        return np.concatenate([head, np.clip(tail, 0.0, 1.0)])
    hist = np.stack([L.O[L.rt - k, L.rz] for k in LAGS]).max(axis=0) if L.n else np.zeros(0)
    alpha = []
    for i in range(Z):
        m = (L.rz == i) & (hist == 0)
        r = float(np.clip(L.y[m].mean() if m.any() else 0.2, 0.02, 0.98))
        alpha.append(math.log(r / (1 - r)))
    head = np.r_[alpha, 0.5, 0.5, 0.0]
    d = _decrements(*START_X).ravel()
    tail = {"zone": np.tile(d, Z), "pooled": d, "background": np.zeros(0)}[mode]
    return np.concatenate([head, tail])


def _minimize(L: Layout, mode: str, lam: float = 0.0, xbar=None, x0=None, record: list | None = None) -> np.ndarray:
    """SLSQP on ``objective``; ``record`` (a list) gets the solver's {mode, status, nit}, so a fit that stopped on
    status 8 (the line search cannot improve) is on file, never silent."""
    from scipy.optimize import minimize
    Z = len(L.zones)
    x0 = start(L, mode) if x0 is None else x0
    res = minimize(objective, x0, args=(L, mode, lam, xbar), jac=True, method="SLSQP", bounds=_bounds(mode, Z),
                   constraints=_constraints(mode, Z), options={"maxiter": 2000, "ftol": 1e-12})
    th = np.asarray(res.x, dtype=float)
    if not np.isfinite(objective(th, L, mode, lam, xbar, grad=False)):
        raise ValueError(f"S4 v3 {mode} fit: a non-finite likelihood ({res.message})")
    if not res.success and res.status != 8:          # 8: the line search cannot improve (at the optimum)
        raise ValueError(f"S4 v3 {mode} fit did not converge: {res.message} (status {res.status})")
    if record is not None:
        record.append({"mode": mode, "status": int(res.status), "nit": int(res.nit)})
    return th


# ── a spec from parameters, and its check ──────────────────────────────────

def sources_block() -> dict:
    return {"truth": [{"source": s, "from": lo, "to": hi} for s, lo, hi in SMP.D10_SOURCES],
            "truth_rule": "samples.D10_SOURCES, de-duplicated (DataSF over STARDB over Poo Bot)",
            "served_table_fit_on": list(SMP.DEFAULT_SOURCES),
            "history": "the CIWQS ledger (truth.zone_overflow); sizes Σ filed volume_MG of the zone's feeding basins"}


def spec_from(theta: np.ndarray, mode: str, medians: dict, fit: dict | None = None) -> dict:
    """The zone_v3 s4_quality.json spec of a fit (``check_v3``'d). Buckets "0_small" … "6-7_large"."""
    Z = len(ZONES)
    xs, xl = _curves(theta, mode, Z)
    buckets = {z: {**{f"{b}_small": float(xs[i, j]) for j, b in enumerate(BUCKET_ORDER)},
                   **{f"{b}_large": float(xl[i, j]) for j, b in enumerate(BUCKET_ORDER)}} for i, z in enumerate(ZONES)}
    spec = {"geography": GEOGRAPHY, "pipeline": PIPELINE, "kind": KIND, "unit": "zone",
            "background": {"kind": "logistic", "features": list(FEATURES), "coef": background_coef(theta, Z),
                           "rain": "two-gauge masked daily rain (truth.gauge_rain mean), rain3 = D−2…D",
                           "wet_season_months": list(WET_SEASON_MONTHS)},
            "buckets": buckets, "zone_median_mg": {z: float(medians[z]) for z in ZONES}, "monotone": True,
            "sources": sources_block(), "fit": dict(fit or {})}
    return check_v3(spec)


def check_v3(spec: dict) -> dict:
    """compose_v2.check_s4_spec under sfpuc4_v1, plus what v3 adds: large ≥ small in every bucket, the
    background features exactly FEATURES (no n_sampled), δ1 ≥ 0 and δ1 + δ2 ≥ 0 (more rain never lowers b)."""
    C.check_s4_spec(spec, G.get(GEOGRAPHY))
    if spec["kind"] != KIND or spec["unit"] != "zone":
        raise ValueError(f"an S4 v3 spec is kind {KIND!r} per zone")
    if tuple(spec["background"]["features"]) != FEATURES:
        raise ValueError(f"S4 v3 background features must be {FEATURES}, got {spec['background']['features']}")
    for z, bk in spec["buckets"].items():
        for b in BUCKET_ORDER:
            if bk[f"{b}_large"] < bk[f"{b}_small"] - 1e-12:
                raise ValueError(f"S4 v3 {z} bucket {b}: large {bk[f'{b}_large']} < small {bk[f'{b}_small']}")
    for z, c in spec["background"]["coef"].items():
        d1, d2 = c[FEATURES[0]], c[FEATURES[1]]
        if d1 < -1e-9 or d1 + d2 < -1e-9:
            raise ValueError(f"S4 v3 {z}: rain slopes δ1 = {d1}, δ1 + δ2 = {d1 + d2}; more rain may never lower b")
    return spec


def q_use(spec: dict, hist: C.History, rain: pd.Series) -> pd.DataFrame:
    """q per zone: compose_v2.s4 on the spec (the one function the fit and every use call)."""
    return C.s4(G.get(GEOGRAPHY), spec, hist, rain).zone


def check_fit_is_use(theta: np.ndarray, mode: str, L: Layout, spec: dict) -> float:
    """compose_v2.s4 on the spec vs the objective's q on every fit row; raises above FIT_USE_TOL."""
    q_fit = q_parts(theta, L, mode)[0][L.rt, L.rz]
    hist = C.History(pd.DataFrame(L.O, L.index, list(ZONES)), pd.DataFrame(L.V, L.index, list(ZONES)))
    q = q_use(spec, hist, L.rain).to_numpy()[L.rt, L.rz]
    gap = float(np.abs(q - q_fit).max()) if L.n else 0.0
    if gap > FIT_USE_TOL:
        raise AssertionError(f"S4 v3 fit ≠ use: compose_v2.s4 differs from the fit's q by {gap:.2e}")
    return gap


# ── one fit, and the nested choice of λ ────────────────────────────────────

@dataclass(frozen=True)
class FitResult:
    spec: dict
    theta: np.ndarray
    pooled: np.ndarray
    lam: float
    n_rows: dict
    gap: float


def fit(hist: C.History, rain: pd.Series, rows: pd.DataFrame, train_days: pd.DatetimeIndex, lam: float,
        mode: str = "zone", fit_info: dict | None = None) -> FitResult:
    """Fit S4 v3 (mode 'zone': the pooled curve first, then zone curves with L2 λ toward it; 'background':
    x ≡ 0) on ``rows`` (zone, date, y_fit) with medians from ``train_days``. The spec's q is compose_v2.s4's."""
    if mode not in ("zone", "background"):
        raise ValueError(f"mode {mode!r}: zone | background")
    if not len(rows):
        raise ValueError("no fit rows")
    med = zone_medians(hist, train_days)
    L = layout(hist, rain, rows, med)
    solver: list = []
    if mode == "background":
        th = _minimize(L, "background", record=solver)
        pooled = th
    else:
        pooled = _minimize(L, "pooled", record=solver)
        xbar = _curves(pooled, "pooled", len(ZONES))
        th = _minimize(L, "zone", lam, xbar, x0=start(L, "zone", pooled, "pooled"), record=solver)
    info = dict(fit_info or {})
    n_rows = {z: int((rows["zone"] == z).sum()) for z in ZONES}
    info.update({"objective": "maximum likelihood of compose_v2.lingering's q on first-look zone-days, oracle inputs",
                 "mode": mode, "lambda": None if mode == "background" else float(lam), "n_rows": n_rows,
                 "n_pos": {z: int(rows.loc[rows["zone"] == z, "y_fit"].sum()) for z in ZONES},
                 "neg_loglik_per_row": float(objective(th, L, mode, 0.0, None, grad=False)), "solver": solver})
    spec = spec_from(th, mode, med, info)
    gap = check_fit_is_use(th, mode, L, spec)
    return FitResult(spec, th, pooled, float(lam), n_rows, gap)


def _season(d) -> np.ndarray:
    return T4.wet_season(pd.Series(pd.DatetimeIndex(d))).to_numpy()


def window_clear(dates, lo: pd.Timestamp, hi: pd.Timestamp) -> np.ndarray:
    """True where a row's read span D−11…D+4 (``EXCL_REACH``: its history D−7…D and what its S4 rules read) misses
    [lo, hi]: a fit row never reads a day of the scored window, not even to decide whether it is a first look."""
    d = pd.DatetimeIndex(dates)
    before, after = EXCL_REACH
    return np.asarray((d + pd.Timedelta(days=after) < lo) | (d - pd.Timedelta(days=before) > hi))


def choose_lambda(hist: C.History, rain: pd.Series, rows: pd.DataFrame, train_days: pd.DatetimeIndex,
                  grid=LAMBDA_GRID) -> tuple:
    """(λ, {λ: inner Brier}): nested leave-one-season-out inside the fold's training rows. Each inner fold is
    fit (medians included) on the other seasons' rows whose read span (``window_clear``) misses the inner season,
    and scores the inner season's rows on the primary truth (``y``). Lowest pooled inner Brier; the larger λ on a
    tie."""
    seasons = sorted(set(_season(rows["date"])))
    sse = {lam: 0.0 for lam in grid}
    n = 0
    td_season = _season(train_days)
    row_season = _season(rows["date"])
    for s in seasons:
        held = row_season == s
        keep = ~held & window_clear(rows["date"], pd.Timestamp(s, 7, 1), pd.Timestamp(s + 1, 6, 30))
        tr_rows, te_rows = rows[keep], rows[held]
        if not len(te_rows) or tr_rows["y_fit"].sum() < 1:
            continue
        med = zone_medians(hist, train_days[td_season != s])
        L = layout(hist, rain, tr_rows, med)
        pooled = _minimize(L, "pooled")
        xbar = _curves(pooled, "pooled", len(ZONES))
        pos = hist.p.index.get_indexer(pd.DatetimeIndex(te_rows["date"]))
        zpos = np.array([list(ZONES).index(z) for z in te_rows["zone"]])
        y = te_rows["y"].to_numpy(dtype=float)
        n += len(y)
        x0 = start(L, "zone", pooled, "pooled")
        for lam in grid:
            th = _minimize(L, "zone", lam, xbar, x0=x0)
            q = q_use(spec_from(th, "zone", med), hist, rain).to_numpy()[pos, zpos]
            sse[lam] += float(np.sum((q - y) ** 2))
    if not n:
        raise ValueError("no inner season to choose λ on")
    brier = {lam: sse[lam] / n for lam in grid}
    best = min(brier.values())
    return max(lam for lam, b in brier.items() if b <= best + 1e-12), brier


# ── inputs: truth, the served chain, the rows ──────────────────────────────

@dataclass
class Inputs:
    """Everything the study reads once: the sfpuc4_v1 context (exclusions), the sampled S4 pool, the fit's
    follow-up-free truth, rain, and the served set's S2 folds, frames and S3 / S4 fitters' inputs."""
    end: pd.Timestamp
    ctx: X.Context
    days: pd.DatetimeIndex
    rain: pd.Series
    pool: pd.DataFrame            # sampled S4 zone-days (oracle entry): zone, date, y, excl, stratum, y_bg
    samples_zone: pd.DataFrame    # zone_elevated (D10): zone, date, y, sources (every sampled day)
    bundle: object                # stages_build.SetBundle of the served set
    fitted: object                # stages_s2.Fitted
    plan: dict
    ef: dict                      # rain-known entry frames by rain source
    train: dict
    events: pd.DataFrame
    samples_v1: pd.DataFrame      # train_v4.load_samples(): DataSF + Poo Bot, the served table's recipe
    tr_v1: object                 # stages_build.Truths in the served set's geography
    blocks: pd.DataFrame
    geo_hist: object = None       # the geography the true history is read through (the served set's)
    cache: dict = field(default_factory=dict)


def followup_free_truth(sources=SMP.D10_SOURCES) -> pd.Series:
    """(zone, date) → any exceedance among the stations X-S4-FOLLOWUP keeps (OCEAN#20 / 21 / 22 out); a
    zone-day sampled only at those stations is absent."""
    smp = T._samples(tuple(sources))
    keep = smp[~X.followup_mask(smp)]
    z = SMP.zone_sample_days(keep)
    return z.set_index(["zone", "date"])["any_exceedance"].astype(float)


def s4_pool(ctx: X.Context, end) -> pd.DataFrame:
    """Every sampled zone-day of the span with its S4 first-match rule (exclusions.apply on a skeleton, oracle
    entry): zone, date, y (the S4 truth), excl, stratum."""
    sk = X.skeleton("s4", list(ZONES), ctx.start, end, entry="oracle", tier="T2")
    z = ctx.frames["zone"]
    key = pd.MultiIndex.from_arrays([sk["unit"], sk["date"]])
    sk["y"] = z["s4_y"].reindex(key).to_numpy(dtype=float)
    a = X.apply(sk, "s4", ctx)
    a = a[a["excl"] != "X-S4-UNSAMPLED"]
    return pd.DataFrame({"zone": a["unit"].to_numpy(), "date": pd.DatetimeIndex(a["date"]), "y": a["y"].to_numpy(dtype=float),
                         "excl": a["excl"].to_numpy(), "stratum": a["stratum"].to_numpy()}).reset_index(drop=True)


def load_inputs(tiers=TIERS, log=print) -> Inputs:
    t0 = time.time()
    geo = G.get(GEOGRAPHY)
    end = E.data_end()
    ctx = X.context(geo, end=end)
    pool = s4_pool(ctx, end)
    fu = followup_free_truth(ctx.sources)
    pool["y_bg"] = fu.reindex(pd.MultiIndex.from_arrays([pool["zone"], pool["date"]])).to_numpy(dtype=float)
    el = T.zone_elevated(geo, ctx.sources, end=end)
    bundle = SB.load_set("served")                    # the served set's name comes from served.json
    s2set = bundle.s2
    need = sorted(set(s2set.sources) | set(bundle.chosen.values()) | {"avg"})
    train, _ = T4.build_dataset(sources=need)
    fitted = S2.fit(bundle.name, "served", tiers, train_frames=train)
    frames = E.frames("oracle", need, model=E.served_weather_model(), end=end)
    ef = S2._entry_frames(s2set, "oracle", frames)
    days = pd.DatetimeIndex(next(iter(ef.values())).index)
    rain = T.gauge_rain(end=end)[T.MEAN_SERIES]
    log(f"S4 v3 inputs: {bundle.name}'s S2 ({len(fitted.folds)} folds), {len(pool)} sampled zone-days, "
        f"{days[0].date()} → {days[-1].date()} ({time.time() - t0:.1f}s)")
    return Inputs(end, ctx, days, rain, pool, el[["zone", "date", "y", "sources"]].copy(), bundle, fitted,
                  {(p[0], p[1]): p for p in S2._plan(tiers)}, ef, train, T4.load_events(), T4.load_samples(),
                  SB.truths(bundle.geo, end), T.blocks(end=end).set_index("date"), bundle.geo)


def train_days(inp: Inputs, fold) -> pd.DatetimeIndex:
    """The fold's training days (stages_s2._plan's rule): T1 finals through 2025-10-31; T1-holdout before
    2023-07-01; a T2 season the other T2 seasons."""
    d = inp.days
    if fold.tier == "T1":
        return d[d <= S2.TRAINED_THROUGH]
    if fold.tier == "T1-holdout":
        return d[d < S2.HOLDOUT_START]
    if fold.tier != "T2":
        raise ValueError(f"no S4 v3 fold for tier {fold.tier!r}")
    s = _season(d)
    return d[np.isin(s, S2.T2_SEASONS) & (s != fold.season)]


def scored_window(inp: Inputs, fold) -> pd.DatetimeIndex:
    return pd.date_range(fold.start, min(fold.end, inp.end))


def fit_rows(inp: Inputs, fold, hist: C.History, drop: bool = True) -> pd.DataFrame:
    """The fold's fit rows: scored S4 pool rows (first looks: X-S4-RESAMPLE, -HISTUNK, -LEDGER-SUSPECT out) on its
    training days whose read span D−11…D+4 (``window_clear``) misses the scored window, with y_fit = y, or on a
    row with no zone overflow on D−7…D the follow-up-free truth (X-S4-FOLLOWUP); a row sampled only at those
    stations is out."""
    win = scored_window(inp, fold)
    td = train_days(inp, fold)
    p = inp.pool
    m = (p["excl"] == "").to_numpy() & p["date"].isin(td).to_numpy() & window_clear(p["date"], win[0], win[-1])
    r = p[m].copy()
    o = hist.p.astype(float).rolling(len(LAGS), min_periods=1).max()      # any zone overflow on D−7…D
    r["quiet"] = o.stack().reindex(pd.MultiIndex.from_arrays([r["date"], r["zone"]])).to_numpy() == 0
    r["y_fit"] = np.where(r["quiet"], r["y_bg"], r["y"])
    if drop:                                         # ``drop`` False: the rows X-S4-FOLLOWUP leaves out kept, y_fit NaN (counts)
        r = r[np.isfinite(r["y_fit"])]
    r = r.reset_index(drop=True)
    if r["date"].isin(win).any() or td.isin(win).any():
        raise AssertionError(f"{fold.tier} {fold.fold}: the fit's days hold a scored day")
    return r


def candidate_truths(inp: Inputs) -> SB.Truths:
    """stages_build.truths under sfpuc4_v1 (once per Inputs): the ledger a stage candidate's oracle is read from."""
    if "truths_sfpuc4" not in inp.cache:
        inp.cache["truths_sfpuc4"] = SB.truths(G.get(GEOGRAPHY), inp.end)
    return inp.cache["truths_sfpuc4"]


def served_chain(inp: Inputs, fold, phi: dict | None = None, v_hat: pd.DataFrame | None = None, v_from: str | None = None) -> dict:
    """The served set's fold: S2 p and v̂ (rain known) on every day, its S3 / S4 specs refit by the served recipe
    on the fold's training days (stages_build.fold_specs; T1: its own artifacts), the S4 v3 oracle input (zone
    history and sizes at the size rule ``phi``), the table's oracle and chained zone q, and the chained zone
    history S4 v3 reads: the served S3's zone p, with zone sizes at ``phi`` (compose_v2.s3's rule: Σ_b max φ·v̂;
    the served table keeps its own link sizes).

    ``v_hat`` (date × sfpuc4_v1 basin, every day; ``v_from`` names it): a stage candidate's own fold S2 v̂. The S4 v3
    oracle input is then read the way the build composes that candidate (stages_build.true_history under sfpuc4_v1,
    the candidate's v̂ sizing an overflow with no measured volume), so S4 fit = S4 use (Part B 6); the served table's
    arms and the chained history keep the served chain."""
    phi = unit_phi(phi)
    key = (fold.tier, fold.fold, tuple(sorted(phi.items())), v_from)
    if key in inp.cache:
        return inp.cache[key]
    if (v_hat is None) != (v_from is None):
        raise ValueError("a candidate's v̂ comes with its name (v_from)")
    b = inp.bundle
    p, v = SB._s2_values(b.s2, fold, inp.ef, inp.days)
    specs, info = SB.fold_specs(b, fold, inp.plan[key[:2]][5], inp.train, inp.events, inp.samples_v1)
    geo = inp.geo_hist
    if v_hat is None:
        zhist, unknown = oracle_history(geo, inp.tr_v1, inp.days, v, phi)
    else:
        g4 = G.get(GEOGRAPHY)
        if list(v_hat.columns) != list(g4.keys) or not inp.days.isin(v_hat.index).all():
            raise KeyError(f"the candidate's v̂ must hold {list(g4.keys)} on every day of the study")
        vv = v_hat.loc[inp.days]
        if not np.isfinite(vv.to_numpy(dtype=float)).all():
            raise ValueError(f"{v_from} {fold.tier} {fold.fold}: a missing v̂")
        zhist, unknown = oracle_history(g4, candidate_truths(inp), inp.days, vv, phi)
    lhist, _ = SB.true_history(geo, specs, inp.tr_v1, inp.days, v)
    comp = C.compose(geo, specs, C.BasinInputs(p, v))
    if any(float(lk["vol_share"]) != 1.0 for lk in specs["s3"]["links"].values()):
        raise ValueError(f"{b.name} {fold.tier} {fold.fold}: the served S3 sizes links at φ ≠ 1; the chained history "
                         "would not follow the oracle's size rule")
    zone_v = comp.s3.zone_v
    on = phi_on(geo, phi)
    if on is not None:                               # the same S3, its links sized at φ: only the sizes move
        s3 = copy.deepcopy(specs["s3"])
        for lid, f in on.items():
            s3["links"][lid]["vol_share"] = float(f)
        r3 = C.s3(geo, s3, p, v)
        if not np.array_equal(r3.zone_p.to_numpy(), comp.s3.zone_p.to_numpy()):
            raise AssertionError("re-sizing the served S3's links moved its zone p")
        zone_v = r3.zone_v
    out = {"p": p, "v": v, "specs": specs, "info": info, "hist": zhist, "unknown": unknown, "phi": phi,
           "served_oracle": C.s4(geo, specs["s4"], lhist).zone[list(ZONES)],
           "served_chained": comp.s4.zone[list(ZONES)],
           "chain_hist": C.History(comp.s3.zone_p[list(ZONES)], zone_v[list(ZONES)])}
    inp.cache[key] = out
    return out


# ── benchmarks ─────────────────────────────────────────────────────────────

def rain_matrix(rain: pd.Series, days: pd.DatetimeIndex) -> pd.DataFrame:
    """The rain-only logistic's inputs per day: log1p rain on D, D−1, D−2 and the 7-day total D−6…D."""
    r = pd.Series(rain, dtype=float).reindex(pd.date_range(days[0] - pd.Timedelta(days=6), days[-1]))
    if r.isna().any():
        raise ValueError("the rain-only logistic needs rain on every day of D−6…D")
    f = pd.DataFrame({"rain_d": r, "rain_d1": r.shift(1), "rain_d2": r.shift(2), "rain_7d": r.rolling(7).sum()}).loc[days]
    return np.log1p(f)


def fit_rain_logit(rain: pd.Series, rows: pd.DataFrame, days: pd.DatetimeIndex) -> dict:
    """{zone: fitted sklearn LogisticRegression} on the fit rows (y_fit)."""
    from sklearn.linear_model import LogisticRegression
    Xr = rain_matrix(rain, days)
    out = {}
    for z in ZONES:
        r = rows[rows["zone"] == z]
        y = r["y_fit"].to_numpy(dtype=int)
        if len(np.unique(y)) < 2:
            raise ValueError(f"rain-only logistic {z}: one class in {len(y)} rows")
        out[z] = LogisticRegression(C=RAIN_LOGIT_C, max_iter=1000).fit(Xr.loc[pd.DatetimeIndex(r["date"])].to_numpy(), y)
    return out


def predict_rain_logit(models: dict, rain: pd.Series, days: pd.DatetimeIndex) -> pd.DataFrame:
    Xr = rain_matrix(rain, days).to_numpy()
    return pd.DataFrame({z: models[z].predict_proba(Xr)[:, 1] for z in ZONES}, index=days)


def persistence(samples_zone: pd.DataFrame, zones, dates, fallback) -> tuple:
    """(p, seen): the result (0/1) of the zone's newest sample in D−7…D−1, else the fallback (climatology)."""
    by = {z: g.set_index("date")["y"].astype(float).sort_index() for z, g in samples_zone.groupby("zone")}
    out = np.asarray(fallback, dtype=float).copy()
    seen = np.zeros(len(out), dtype=bool)
    for i, (z, d) in enumerate(zip(zones, pd.DatetimeIndex(dates))):
        s = by.get(z)
        if s is None:
            continue
        w = s.loc[d - pd.Timedelta(days=PERSIST_DAYS):d - pd.Timedelta(days=1)]
        if len(w):
            out[i], seen[i] = float(w.iloc[-1]), True
    return out, seen


# ── the study: every fold fit, every arm on every row ──────────────────────

@dataclass
class Study:
    rows: pd.DataFrame            # oracle S4 rows (exclusions.COLUMNS + fold + one column per arm)
    chained: pd.DataFrame         # rain-known chained S4 rows (v3 and served)
    folds: dict                   # (tier, fold) → {"spec", "background", "lam", "inner", "fit_rows", ...}
    inputs: Inputs
    n_boot: int
    s3_set: str | None = None     # the stage candidate whose per-fold S3 φ sized the zones (None: φ = 1)
    s2_set: str | None = None     # the stage candidate whose fold S2 v̂ the oracle sizes read (None: the served set's)


def _rows(inp: Inputs, fold, arms: dict, entry: str) -> pd.DataFrame:
    """exclusions.COLUMNS rows of the fold's scored window on its sampled zone-days (every other zone-day is
    X-S4-UNSAMPLED), each arm's forecast in its own column; a missing forecast raises."""
    win = scored_window(inp, fold)
    sk = X.skeleton("s4", list(ZONES), win[0], win[-1], entry=entry, tier=fold.tier)
    key = pd.MultiIndex.from_arrays([sk["unit"], sk["date"]])
    sk["y"] = inp.ctx.frames["zone"]["s4_y"].reindex(key).to_numpy(dtype=float)
    sk = sk[np.isfinite(sk["y"])].reset_index(drop=True)
    sk["fold"] = fold.fold
    d = pd.DatetimeIndex(sk["date"])
    for name, frame in arms.items():
        sk[name] = frame.stack().reindex(pd.MultiIndex.from_arrays([d, sk["unit"]])).to_numpy(dtype=float)
        bad = ~np.isfinite(sk[name].to_numpy())
        if bad.any():
            raise ValueError(f"{fold.tier} {fold.fold} {entry}: no {name} forecast on {int(bad.sum())} zone-days "
                             f"(e.g. {sk['unit'][bad].iloc[0]} {d[bad][0].date()})")
    return sk


def run(tiers=TIERS, seasons=None, lam=None, n_boot: int = B_PROTOCOL, inputs: Inputs | None = None,
        log=print, s3_set: str | None = None, s3_root=None, phis: dict | None = None, s2_source=None) -> Study:
    """Fit S4 v3 per fold and put every arm on the same rows. ``lam`` None: nested choice per fold (protocol
    §2); a number fixes it (tests). ``seasons`` keeps those T2 seasons (tests). Zone sizes follow φ = 1, or
    stage candidate ``s3_set``'s per-fold S3 φ (``size_rules``; ``s3_root`` its directory), or ``phis``
    ({(tier, fold): {link: φ}}, tests). ``s2_source`` (stages_s3_links.CandidateS2): a stage candidate's own S2,
    whose fold v̂ sizes the oracle's overflows with no measured volume, the history read under sfpuc4_v1 as the
    build composes that candidate (``served_chain``); default the served set's v̂ through its geography."""
    t_all = time.time()
    inp = inputs or load_inputs(tiers, log)
    folds = [f for f in inp.fitted.folds if f.tier in tiers and (f.tier != "T2" or seasons is None or f.season in set(seasons))]
    if not folds:
        raise ValueError(f"no fold in tiers {tiers} (seasons {seasons})")
    if s3_set is not None and phis is not None:
        raise ValueError("sizes follow one rule: s3_set or phis")
    if s3_set is not None:
        sc_mod = _saver()
        if sc_mod is None:
            raise ImportError("sizing zones by a stage candidate's S3 needs stages_candidates")
        saved = sc_mod.load_set(s3_set, root=s3_root).s3_links
        if saved is None:
            raise FileNotFoundError(f"stage candidate {s3_set!r} holds no s3_links.json to size zones by")
        phis = size_rules(saved, [(f.tier, f.fold) for f in folds])
    if phis is None:
        phis = {(f.tier, f.fold): None for f in folds}      # φ = 1 on every link
    missing = [(f.tier, f.fold) for f in folds if (f.tier, f.fold) not in phis]
    if missing:
        raise KeyError(f"no size rule (φ) for folds {missing}")
    phis = {(f.tier, f.fold): unit_phi(phis[(f.tier, f.fold)]) for f in folds}
    s2_set = None if s2_source is None else s2_source.name
    out_rows, out_chain, info = [], [], {}
    for f in folds:
        t0 = time.time()
        phi = phis[(f.tier, f.fold)]
        vc = None if s2_source is None else s2_source.values((f.tier, f.fold), "v_hat", inp.days)
        ch = served_chain(inp, f, phi, vc, s2_set)
        td = train_days(inp, f)
        rows = fit_rows(inp, f, ch["hist"])
        if lam is None:
            lam_f, inner = choose_lambda(ch["hist"], inp.rain, rows, td)
        else:
            lam_f, inner = float(lam), None
        win = scored_window(inp, f)
        base = {"tier": f.tier, "fold": f.fold, "scores": [str(win[0].date()), str(win[-1].date())],
                "fit_span": [str(td.min().date()), str(td.max().date())],
                "fit_seasons": sorted(int(s) for s in set(_season(rows["date"]))),
                "lambda_inner_brier": None if inner is None else {str(k): v for k, v in inner.items()},
                "followup_rule": "no zone overflow on D−7…D: OCEAN#20/21/22 left out of the truth",
                "n_followup_changed": int((rows["quiet"] & (rows["y_fit"] != rows["y"])).sum()),
                "n_followup_dropped": int((fit_rows(inp, f, ch["hist"], drop=False)["y_fit"].isna()).sum()),
                "vol_share": dict(phi), "size_rule_from": s3_set or ("given" if any(v != 1.0 for v in phi.values()) else None),
                "v_hat_from": s2_set or inp.bundle.name,
                "history_geography": GEOGRAPHY if s2_set else inp.geo_hist.version}
        v3 = fit(ch["hist"], inp.rain, rows, td, lam_f, "zone", base)
        bg = fit(ch["hist"], inp.rain, rows, td, 0.0, "background", {**base, "benchmark": "background only (x ≡ 0)"})
        rl = fit_rain_logit(inp.rain, rows, inp.days)
        arms = {"v3": q_use(v3.spec, ch["hist"], inp.rain),
                "b_v3": pd.DataFrame(C.background(v3.spec, list(ZONES), inp.days, inp.rain), inp.days, list(ZONES)),
                "served": ch["served_oracle"], "background": q_use(bg.spec, ch["hist"], inp.rain),
                "rain_logit": predict_rain_logit(rl, inp.rain, inp.days)}
        out_rows.append(_rows(inp, f, arms, "oracle"))
        chained = {"v3": q_use(v3.spec, ch["chain_hist"], inp.rain), "served": ch["served_chained"]}
        out_chain.append(_rows(inp, f, chained, "rain"))
        info[(f.tier, f.fold)] = {"spec": v3.spec, "background": bg.spec, "lam": lam_f, "inner": inner, "fit_rows": rows,
                                  "gap": max(v3.gap, bg.gap), "served_info": ch["info"], "train_days": td,
                                  "vol_share": dict(phi)}
        log(f"  {f.tier} {f.fold}: {len(rows)} fit rows, λ {lam_f:g}, {len(out_rows[-1])} sampled zone-days "
            f"({time.time() - t0:.1f}s)")
    rows = _apply(pd.concat(out_rows, ignore_index=True), inp)
    chained = _apply(pd.concat(out_chain, ignore_index=True), inp)
    ref_pool = SB._pool("s4", inp.ctx, G.get(GEOGRAPHY), "oracle")
    for fr in (rows, chained):
        fr["ref"] = SB.references(fr, ref_pool)
    rows["climatology"] = rows["ref"]
    rows["persistence"], rows["persist_seen"] = persistence(inp.samples_zone, rows["unit"], rows["date"], rows["ref"])
    rows["q"], rows["b"], rows["p"] = rows["v3"], rows["b_v3"], rows["v3"]
    chained["q"], chained["p"] = chained["v3"], chained["v3"]
    key = ["unit", "date", "tier"]
    if not rows[key + ["excl", "y"]].equals(chained[key + ["excl", "y"]]):
        raise AssertionError("the oracle and chained entries carry different S4 rows")
    log(f"S4 v3: {len(folds)} folds, {int((rows['excl'] == '').sum())} first-look rows scored ({time.time() - t_all:.1f}s)")
    return Study(rows, chained, info, inp, n_boot, s3_set, s2_set)


def _apply(rows: pd.DataFrame, inp: Inputs) -> pd.DataFrame:
    """exclusions.apply per tier (a window's rows in one call, so X-POWER counts the whole window)."""
    out = pd.concat([X.apply(g, "s4", inp.ctx) for _, g in rows.groupby("tier", sort=False)], ignore_index=True)
    if (out["excl"] == "X-ALL-INSAMPLE").any():
        raise AssertionError("an S4 v3 row is in-sample")
    return out


# ── scores ─────────────────────────────────────────────────────────────────

def _scored(rows: pd.DataFrame, tier: str, excl: str = "") -> pd.DataFrame:
    return rows[(rows["tier"] == tier) & (rows["excl"] == excl)]


def _blocks(study: Study, dates) -> tuple:
    return SB._storm_blocks(dates, study.inputs.blocks)


FULL_ARMS = ("v3", "served")                         # pooled bundles kept whole: reliability curve, risk-level tables


def _lean(b: dict) -> dict:
    """A bundle without its reliability curve and risk-level tables (MCB / DSC / UNC kept): the spec file
    carries those only for the pooled primary arms."""
    out = {k: v for k, v in b.items() if k != "contingency"}
    out["corp"] = {k: v for k, v in b["corp"].items() if k != "curve"} if isinstance(b.get("corp"), dict) else b.get("corp")
    return out


def arm_scores(study: Study, rows: pd.DataFrame, arm: str) -> dict:
    """{pooled | zone: verify bundle} for one arm on the rows (BSS against the rows' reference)."""
    out = {}
    blk, storm = _blocks(study, rows["date"])
    for u in ["pooled"] + [z for z in ZONES if (rows["unit"] == z).any()]:
        m = np.ones(len(rows), bool) if u == "pooled" else (rows["unit"] == u).to_numpy()
        g = rows[m]
        b = SB.bundle(g["y"], g[arm], g["ref"], blk[m], storm[m], g["unit"].to_numpy(), study.n_boot, g["date"])
        out[u] = b if (u == "pooled" and arm in FULL_ARMS) else _lean(b)
    return out


def delta(study: Study, rows: pd.DataFrame, a, b, per_zone: bool = True) -> dict:
    """{pooled | zone: Δ = BS(a) − BS(b)} on the rows; ``a`` / ``b`` a column of the rows or an array on them."""
    blk, _ = _blocks(study, rows["date"])
    pa = rows[a].to_numpy(dtype=float) if isinstance(a, str) else np.asarray(a, dtype=float)
    pb = rows[b].to_numpy(dtype=float) if isinstance(b, str) else np.asarray(b, dtype=float)
    y = rows["y"].to_numpy(dtype=float)
    out = {"pooled": SB._delta(y, pa, pb, blk, study.n_boot)}
    if per_zone:
        for z in ZONES:
            m = (rows["unit"] == z).to_numpy()
            if m.any():
                out[z] = SB._delta(y[m], pa[m], pb[m], blk[m], study.n_boot)
    return out


def oracle_minus_chained(study: Study, tier: str, arm: str, excl: str = "") -> dict:
    """Δ = BS(oracle) − BS(chained) for one S4 on the tier's rows of one rule (both entries hold the same rows)."""
    o = _scored(study.rows, tier, excl)
    c = _scored(study.chained, tier, excl)
    if not (o["unit"].to_numpy() == c["unit"].to_numpy()).all() or not (o["date"].to_numpy() == c["date"].to_numpy()).all():
        raise AssertionError(f"{tier}: oracle and chained rows differ")
    return delta(study, o.reset_index(drop=True), arm, c[arm].to_numpy(dtype=float))


def strata(rows: pd.DataFrame, ctx: X.Context) -> np.ndarray:
    """§3.4's strata per row: dayof, tail (an overflow on D−7…D−1), wet (rain3 ≥ 0.1″, no overflow in the week), dry."""
    z = ctx.frames["zone"]
    key = pd.MultiIndex.from_arrays([rows["unit"], pd.DatetimeIndex(rows["date"])])
    dayof = z["dayof"].reindex(key).to_numpy(dtype=bool)
    ov = z["y"].unstack(0).fillna(0.0)
    tail7 = ov.shift(1).rolling(len(LAGS) - 1, min_periods=1).max().fillna(0.0)
    tail = tail7.stack().reindex(pd.MultiIndex.from_arrays([pd.DatetimeIndex(rows["date"]), rows["unit"]])).to_numpy() > 0
    wet = ctx.rain3.reindex(pd.DatetimeIndex(rows["date"])).to_numpy() >= X.RUNOFF_RAIN_IN
    return np.select([dayof, tail, wet], ["dayof", "tail", "wet_no_overflow"], default="dry")


def source_of(rows: pd.DataFrame, samples_zone: pd.DataFrame) -> np.ndarray:
    """§3.4's source stratum per row: the lab record its zone-day's truth came from (samples.D10_SOURCES' names,
    ';'-joined when stations of one day differ). A row with no sampled zone-day raises."""
    s = samples_zone.set_index(["zone", "date"])["sources"]
    out = s.reindex(pd.MultiIndex.from_arrays([rows["unit"], pd.DatetimeIndex(rows["date"])])).to_numpy()
    if pd.isna(out).any():
        raise AssertionError("an S4 row without a sampled zone-day")
    return out.astype(str)


def power(study: Study, tier: str = "T2") -> dict:
    """Part B 4's honest power: per zone, scored first looks, positives, the first looks in an overflow's tail
    (D−7…D−1, no day-of) and the distinct overflow days behind them (each tail row's newest prior overflow)."""
    rows = _scored(study.rows, tier)
    st = strata(rows, study.inputs.ctx)
    ov = study.inputs.ctx.frames["zone"]["y"].unstack(0).fillna(0.0)
    out = {}
    for z in ZONES:
        m = (rows["unit"] == z).to_numpy()
        tail = m & (st == "tail")
        behind = set()
        for d in pd.DatetimeIndex(rows["date"][tail]):
            w = ov.loc[d - pd.Timedelta(days=len(LAGS) - 1):d - pd.Timedelta(days=1), z]
            behind.add(w[w == 1].index.max())
        out[z] = {"n": int(m.sum()), "n_pos": int(rows["y"][m].sum()), "tail_first_looks": int(tail.sum()),
                  "tail_positives": int(rows["y"][tail].sum()), "overflow_days_behind_tail": len(behind),
                  "dayof": int((m & (st == "dayof")).sum())}
    return out


def candlestick_stations() -> tuple:
    """East's stations whose SFPUC4 station_basin is South (A2): the Candlestick stations, registry keys."""
    geo = G.get(GEOGRAPHY)
    sids = [s for s in ZONES["east"].station_ids if geo.station_basin(s) == "south"]
    keys = tuple(k for k, st in STATIONS.items() if st.sfpuc_id in sids)
    if not keys:
        raise KeyError("no East station reads as South under sfpuc4_v1")
    return keys


def candlestick(study: Study, tiers=("T2", "T1-holdout", "T1")) -> dict:
    """A2's sensitivity check: East's forecasts (v3, the served table) scored on the Candlestick stations only —
    first looks read on those stations, East's history known and not ledger-suspect — against a Candlestick
    climatology from each window's training days (stages_build.references)."""
    inp = study.inputs
    smp = T._samples(tuple(inp.ctx.sources))
    keys = candlestick_stations()
    zd = SMP.zone_sample_days(smp[smp["station"].isin(keys)])     # first looks read on these stations alone
    zd = zd[(zd["zone"] == "east") & (zd["date"] >= inp.ctx.start) & (zd["date"] <= inp.end)]
    z = inp.ctx.frames["zone"]
    key = pd.MultiIndex.from_arrays([zd["zone"], zd["date"]])
    hist_known = z["hist_known"].reindex(key).to_numpy()
    suspect = z["suspect_hist"].reindex(key).to_numpy()
    if pd.isna(hist_known).any() or pd.isna(suspect).any():
        raise AssertionError("a Candlestick sample-day outside the context")
    ok = zd["first_look"].to_numpy(dtype=bool) & hist_known.astype(bool) & ~suspect.astype(bool)
    cs = pd.DataFrame({"unit": "east", "date": pd.DatetimeIndex(zd["date"]),
                       "y": zd["any_exceedance"].astype(float).to_numpy()})[ok].reset_index(drop=True)
    east = study.rows[study.rows["unit"] == "east"].set_index(["date", "tier"])
    out = {"stations": [STATIONS[k].sfpuc_id for k in keys], "names": [STATIONS[k].name for k in keys],
           "rule": "East's forecasts against 'any Candlestick station over standard', first looks on those stations, "
                   "X-S4-HISTUNK and X-LEDGER-SUSPECT read from East's history"}
    for t in tiers:
        if t not in east.index.get_level_values("tier"):
            continue
        e = east.xs(t, level="tier")
        r = cs[cs["date"].isin(e.index)].copy()
        if not len(r):
            continue
        r["tier"] = t
        r["ref"] = SB.references(r, cs)          # each window's training days only, never the scored window
        for arm in ("v3", "served"):
            r[arm] = e.loc[pd.DatetimeIndex(r["date"]), arm].to_numpy(dtype=float)
        blk, storm = _blocks(study, r["date"])
        st = r["unit"].to_numpy()
        out[t] = {"n": int(len(r)), "n_pos": int(r["y"].sum()),
                  "v3": _lean(SB.bundle(r["y"], r["v3"], r["ref"], blk, storm, st, study.n_boot, r["date"])),
                  "served": _lean(SB.bundle(r["y"], r["served"], r["ref"], blk, storm, st, study.n_boot, r["date"])),
                  "delta_v3_minus_served": SB._delta(r["y"].to_numpy(), r["v3"].to_numpy(), r["served"].to_numpy(),
                                                     blk, study.n_boot)}
    return out


def nowcast(rows: pd.DataFrame) -> dict:
    """Protocol §4.3: sensitivity, specificity and accuracy at q ≥ 0.5, S4 v3 and the table against persistence."""
    out = {}
    for arm in ("v3", "served", "persistence"):
        c = V.contingency_at(rows[arm], rows["y"], 0.5)
        out[arm] = V.clean({"sensitivity": c["pod"], "specificity": c["spec"], "accuracy": c["acc"], "n": c["n"]})
    return out


def primary(study: Study) -> dict:
    """Protocol §8 S4: v3 − the served table, zone-pooled ΔBS, first looks, oracle, T2; superiority, or
    non-inferiority at +5% plus the oracle-worse-than-chained fix (v3's oracle not worse than its chained)."""
    rows = _scored(study.rows, "T2")
    if not len(rows):
        raise ValueError("the S4 primary is decided on T2: run the T2 folds")
    d = delta(study, rows, "v3", "served")
    pooled = d["pooled"]
    sup = bool(pooled["hi"] is not None and pooled["hi"] < 0)
    ni = bool(pooled["hi"] is not None and pooled["hi"] < NI_MARGIN * pooled["b"])
    fix = {}
    for t in ("T2", "T1"):
        if len(_scored(study.rows, t)):
            v3, srv = oracle_minus_chained(study, t, "v3")["pooled"], oracle_minus_chained(study, t, "served")["pooled"]
            # "shows the defect" is a claim, so the CI decides it (protocol §6): oracle − chained wholly above 0
            fix[t] = {"v3_oracle_minus_chained": v3, "served_oracle_minus_chained": srv,
                      "v3_oracle_not_worse": bool(v3["delta"] <= 0 and v3["verdict"] != "worse"),
                      "served_shows_defect": bool(srv["verdict"] == "worse")}
    fixed = fix["T2"]["v3_oracle_not_worse"]
    tested = fix["T2"]["served_shows_defect"]
    passes = sup or (ni and fixed)
    if sup:
        verdict = "passes: superior"
    elif passes and tested:
        verdict = "passes: non-inferior and the defect fixed"
    elif passes:
        verdict = ("passes: non-inferior, and v3's oracle is not worse than its chained; the fix is not tested "
                   "(the table does not show the defect on these rows either)")
    else:
        verdict = "does not pass"
    return {"comparison": "S4 v3 − the served table (GEO_V1 adapter), zone-pooled ΔBS, first-look samples, oracle, T2",
            "rule": "superiority (CI upper bound < 0), or non-inferiority at +5% (upper bound < 0.05 × the table's BS) "
                    "plus the fix: S4 v3's oracle no worse than its chained composition on T2 (Δ ≤ 0, CI not wholly above 0)",
            "delta": d, "superior": sup, "noninferior_5pct": ni, "fix": fix, "fixed": fixed, "fix_tested": tested,
            "passes": passes, "verdict": verdict}


def scores(study: Study) -> dict:
    """Every score the spec's fit block carries: the primary, each arm per tier and zone, the paired deltas
    against each benchmark, the strata (§3.4: dry / wet with no overflow / day-of / tail, few stations sampled,
    source, analyte era), resamples as their own stratum, the nowcast table, power, Candlestick."""
    out = {"protocol": S2.protocol_stamp(), "n_boot": study.n_boot, "seed": SEED, "level": LEVEL,
           "as_of": str(study.inputs.end.date()), "primary": primary(study), "tiers": {}}
    for t in TIERS:
        rows = _scored(study.rows, t)
        if not len(rows):
            continue
        allr = study.rows[study.rows["tier"] == t]
        cell = {"n": int(len(rows)), "n_pos": int(rows["y"].sum()),
                "excluded": {k: int(v) for k, v in allr["excl"].value_counts().items() if k},
                "low_power_rows": int(rows["tags"].str.contains("X-POWER").sum()),
                "arms": {a: arm_scores(study, rows, a) for a in ARMS},
                "chained": {a: arm_scores(study, _scored(study.chained, t), a) for a in ("v3", "served")},
                "v3_minus": {a: delta(study, rows, "v3", a) for a in ARMS if a != "v3"},
                "persistence_seen": float(rows["persist_seen"].mean())}
        st = strata(rows, study.inputs.ctx)
        cell["strata"] = {s: {"n": int((st == s).sum()), "n_pos": int(rows["y"][st == s].sum()),
                              **{a: V.clean(V.brier(rows["y"][st == s], rows[a][st == s])) for a in ("v3", "served", "background")}}
                          for s in ("dry", "wet_no_overflow", "dayof", "tail") if (st == s).any()}
        few = rows["stratum"].str.contains("X-S4-FEW").to_numpy()
        cell["strata"]["few_stations"] = {"n": int(few.sum()), **{a: V.clean(V.brier(rows["y"][few], rows[a][few]))
                                                                  for a in ("v3", "served")}}
        # §3.4's source and analyte-era strata (X-S4-ANALYTE is a tag: its rows stay in the score)
        src = source_of(rows, study.inputs.samples_zone)
        era = rows["tags"].str.contains("X-S4-ANALYTE").to_numpy()
        for name, parts in (("source", {s: src == s for s in sorted(set(src))}),
                            ("analyte_era", {"e_coli_era": era, "other": ~era})):
            cell["strata"][name] = {g: {"n": int(m.sum()), "n_pos": int(rows["y"][m].sum()),
                                        **{a: V.clean(V.brier(rows["y"][m], rows[a][m])) for a in ("v3", "served")}}
                                    for g, m in parts.items() if m.any()}
        res = _scored(study.rows, t, "X-S4-RESAMPLE")
        if len(res):
            cell["resamples"] = {"n": int(len(res)), "n_pos": int(res["y"].sum()),
                                 **{a: V.clean(V.brier(res["y"], res[a])) for a in ("v3", "served")},
                                 "oracle_minus_chained": {a: oracle_minus_chained(study, t, a, "X-S4-RESAMPLE")["pooled"]
                                                          for a in ("v3", "served")}}
        cell["nowcast"] = nowcast(rows)
        out["tiers"][t] = cell
    out["power"] = power(study)
    out["candlestick"] = candlestick(study)
    return V.clean(out)


# ── the artifact ───────────────────────────────────────────────────────────

def _fold_record(key, f: dict) -> dict:
    sp = f["spec"]
    return {**sp["fit"], "tier": key[0], "fold": key[1], "lambda": f["lam"], "zone_median_mg": sp["zone_median_mg"],
            "background": sp["background"]["coef"], "buckets": sp["buckets"], "monotone": True,
            "fit_use_gap": f["gap"], "served_table": f["served_info"]}


def artifact(study: Study, sc: dict | None = None) -> dict:
    """The finals spec (T1 fold: fit through 2025-10-31) with every fold's record and the scores in ``fit``."""
    if FINAL not in study.folds:
        raise KeyError("the artifact is the finals' spec: run the T1 tier")
    fin = study.folds[FINAL]["spec"]
    fit_block = {"window": f"first-look sampled zone-days through {S2.TRAINED_THROUGH.date()} (the finals; T1 scores "
                           f"{S2.POST_START.date()} → {study.inputs.end.date()})",
                 "built_at": clock.utc_iso(), "served_set": study.inputs.bundle.name,
                 "objective": fin["fit"]["objective"],
                 "lambda": study.folds[FINAL]["lam"], "lambda_grid": list(LAMBDA_GRID),
                 "lambda_rule": "nested leave-one-season-out inside each fold, lowest inner Brier, the larger λ on a tie",
                 "parameterisation": "x(j) = Σ_{i ≥ j} d_i, d_i ≥ 0; large ≥ small and x ≤ 1 − 1e−6 as linear "
                                     "constraints (SLSQP); background α_z per zone, δ1, δ2, ω shared; δ1 ≥ 0, δ1 + δ2 ≥ 0",
                 "size_rule": "compose_v2's zone size: Σ over the zone's feeding basins of the largest φ-sized link into it, "
                              "at each fold's vol_share (folds[].vol_share; φ = 1: the basins' filed volume, geography-invariant; "
                              "a fitted φ: the link's own volume, φ·v̂ chained); large = v ≥ zone_median_mg",
                 "s3_set": study.s3_set,
                 "s2_set": study.s2_set,
                 "v_hat": ("an overflow with no measured volume is sized by φ·v̂ from the S2 named in folds[].v_hat_from, the "
                           "history read under folds[].history_geography (a stage candidate's own S2: the way the build "
                           "composes it, Part B 6)"),
                 "fit_rows": "exclusions' scored S4 rows (first looks) of the training days whose read span D−11…D+4 (the "
                             "history D−7…D and X-LEDGER-SUSPECT's reach) misses the scored window; X-S4-FOLLOWUP: "
                             "OCEAN#20/21/22 out of the truth on rows with no zone overflow on D−7…D",
                 "folds": [_fold_record(k, v) for k, v in study.folds.items()],
                 "scores": sc if sc is not None else scores(study)}
    spec = {k: v for k, v in fin.items() if k != "fit"}
    spec["fit"] = V.clean(fit_block)
    spec["note"] = ("S4 v3 (P8c): zone-level water quality with a rain background; compose_v2.s4 applies it under "
                    "sfpuc4_v1, to a zone history sized by the S3 spec its folds were fit on (fit.folds[].vol_share; "
                    "at φ = 1 geo_v1's zone history is the same, zones and sizes)")
    return check_v3(spec)


def for_compose(saved: dict) -> dict:
    """A saved s4_quality.json as compose_v2 reads it: the saver's stamps (``SAVER_STAMPS``) checked and dropped,
    the rest checked by ``check_v3`` (compose_v2.check_s4_spec included)."""
    missing = [k for k in SAVER_STAMPS if k not in saved]
    if missing:
        raise KeyError(f"a saved s4_quality.json carries the saver's stamps; this one lacks {missing}")
    if saved["component"] != KIND:
        raise ValueError(f"s4_quality component {saved['component']!r}, not {KIND!r}")
    return check_v3({k: v for k, v in saved.items() if k not in SAVER_STAMPS})


def _saver():
    """stages_candidates (P8's geography-aware saver), or None while it is missing."""
    try:
        import stages_candidates as SC  # noqa: PLC0415
    except ImportError:
        return None
    return SC if hasattr(SC, "save_component") and hasattr(SC, "load_set") else None


def check_s2_rule(study: Study, name: str, root=None) -> None:
    """Raise unless the oracle sizes S4 v3 was fit on read the v̂ that stage candidate ``name`` composes with (fit =
    use, Part B 6): a set holding an S2 component takes a study whose v̂ came from that set's own S2
    (``run(s2_source=stages_s3_links.candidate_s2(name))``), and a study fit on a stage candidate's v̂ goes into
    that candidate only."""
    SC = _saver()
    has_s2 = (SC.set_dir(name, root) / "manifest.json").exists() and "s2" in SC.load_set(name, root=root).components
    if has_s2 and study.s2_set != name:
        raise ValueError(f"{name} holds its own S2, but S4 v3's oracle sizes read v̂ from "
                         f"{study.s2_set or study.inputs.bundle.name!r}: refit with --s2-set {name}")
    if study.s2_set is not None and study.s2_set != name:
        raise ValueError(f"S4 v3 was fit on {study.s2_set!r}'s v̂: save it into that set, not {name!r}")


def check_size_rule(study: Study, name: str, root=None) -> None:
    """Raise unless stage candidate ``name`` sizes zones the way the study's folds were fit (fit = use across
    stages, Part B 6): its s3_links.json's per-fold φ (``size_rules``) equal each fold's vol_share, or — a set
    with no S3 yet — every fold was fit at φ = 1 (a fitted φ needs the S3 that pins it in the same set)."""
    SC = _saver()
    want = {k: unit_phi(f.get("vol_share")) for k, f in study.folds.items()}
    have = None
    if (SC.set_dir(name, root) / "manifest.json").exists():
        s3 = SC.load_set(name, root=root).s3_links
        have = None if s3 is None else size_rules(s3, list(want))
    if have is None:
        off = sorted(f"{k[0]} {k[1]}" for k, p in want.items() if any(v != 1.0 for v in p.values()))
        if off:
            raise ValueError(f"{name} holds no s3_links.json, but S4 v3 was sized at φ ≠ 1 in folds {off} "
                             f"(from {study.s3_set!r}): save it into the set whose S3 sizes zones that way")
        return
    bad = {f"{k[0]} {k[1]}": {lid: (want[k][lid], have[k][lid]) for lid in want[k] if abs(want[k][lid] - have[k][lid]) > PHI_TOL}
           for k in want}
    bad = {k: v for k, v in bad.items() if v}
    if bad:
        raise ValueError(f"{name}'s s3_links.json sizes zones at other φ than S4 v3 was fit on ((fit, S3) per link: {bad}); "
                         f"refit with run(s3_set={name!r}) (CLI --s3-set {name})")


def write(study: Study, name: str = SET_NAME, root=None, out_dir: Path | None = None, sc: dict | None = None) -> Path:
    """Save s4_quality.json as the 's4_quality' component of stage candidate ``name`` through
    stages_candidates.save_component (``root`` overrides its directory: tests) and read it back through
    stages_candidates.load_set and ``for_compose``; without that module, or with ``out_dir``, write it to
    data/models/stages_candidates/_s4_v3/ (or ``out_dir``) and read it back with compose_v2.load_s4_spec.
    Returns the file. Refuses: a bootstrap below the protocol's 2,000 anywhere under stages_candidates/ (a
    smaller one only to a test directory), any directory of data/models/ outside stages_candidates/ (Part B
    13), and a set whose S3 sizes zones with other φ than the fit (``check_size_rule``)."""
    models = (FORECAST / "data" / "models").resolve()
    for d in (root, out_dir):
        if d is not None and Path(d).resolve().is_relative_to(models) and not Path(d).resolve().is_relative_to(CANDIDATES_ROOT.resolve()):
            raise ValueError(f"{d}: stage candidates are written under data/models/stages_candidates/ only (Part B 13)")
    SC = None if out_dir is not None else _saver()
    dest = Path(SC.set_dir(name, root)) if SC is not None else (OUT_DIR if out_dir is None else Path(out_dir))
    if study.n_boot < B_PROTOCOL and dest.resolve().is_relative_to(CANDIDATES_ROOT.resolve()):
        raise ValueError(f"s4_quality.json carries scores: B = {study.n_boot} < the protocol's {B_PROTOCOL} "
                         f"(a smaller bootstrap goes to a test directory only, never {dest})")
    if SC is not None:
        check_size_rule(study, name, root)
        check_s2_rule(study, name, root)
    spec = artifact(study, sc)
    if SC is not None:
        d = Path(SC.save_component(name, "s4_quality", {**spec, "component": KIND}, root=root))
        saved = SC.load_set(name, root=root).s4_quality
        if for_compose(saved)["buckets"] != spec["buckets"]:
            raise AssertionError("the saved s4_quality.json does not read back as written")
        return d / SPEC_FILE
    out = OUT_DIR if out_dir is None else Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / SPEC_FILE
    path.write_text(json.dumps(spec, indent=1, ensure_ascii=False, allow_nan=False) + "\n")
    C.load_s4_spec(path, G.get(GEOGRAPHY))
    return path


def summary(sc: dict) -> str:
    p = sc["primary"]
    lines = [f"S4 v3 primary (T2, oracle, first looks): {p['verdict']}"]
    d = p["delta"]["pooled"]
    lines.append(f"  ΔBS v3 − served {d['delta']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}] (BS {d['a']:.4f} vs {d['b']:.4f}, "
                 f"n {d['n']}, {d['n_blocks']} blocks; +5% margin {NI_MARGIN * d['b']:.4f})")
    for t, f in p["fix"].items():
        a, b = f["v3_oracle_minus_chained"], f["served_oracle_minus_chained"]
        lines.append(f"  {t} oracle − chained: v3 {a['delta']:+.4f} [{a['lo']:+.4f}, {a['hi']:+.4f}], "
                     f"served {b['delta']:+.4f} [{b['lo']:+.4f}, {b['hi']:+.4f}]")
    for t, c in sc["tiers"].items():
        lines.append(f"  {t}: n {c['n']} ({c['n_pos']} over) BSS " + ", ".join(
            f"{a} {c['arms'][a]['pooled']['bss']:+.3f}" for a in ARMS if c["arms"][a]["pooled"]["bss"] is not None))
    for t in ("T2", "T1"):
        cs = sc["candlestick"].get(t)
        if cs:
            dd = cs["delta_v3_minus_served"]
            lines.append(f"  Candlestick {t}: n {cs['n']} ({cs['n_pos']} over) BSS v3 {cs['v3']['bss']:+.3f}, served "
                         f"{cs['served']['bss']:+.3f}; Δ {dd['delta']:+.4f} [{dd['lo']:+.4f}, {dd['hi']:+.4f}]")
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="save s4_quality.json")
    ap.add_argument("--set", default=SET_NAME, help="the stage candidate it joins (stages_candidates)")
    ap.add_argument("--n-boot", type=int, default=B_PROTOCOL)
    ap.add_argument("--lam", type=float, default=None, help="fix λ (development only; the artifact chooses it nested)")
    ap.add_argument("--s3-set", default=None, help="size zones by this stage candidate's per-fold S3 φ (default φ = 1)")
    ap.add_argument("--s2-set", default=None, help="size unmeasured overflows by this stage candidate's own fold v̂ "
                    "(stages_s3_links.candidate_s2; default the served set's)")
    a = ap.parse_args(argv)
    if a.write and a.lam is not None:
        raise SystemExit("--write chooses λ nested per fold; drop --lam")
    src = None
    if a.s2_set:
        import stages_s3_links as S3L  # noqa: PLC0415  (P8b: a stage candidate's S2 per fold)
        src = S3L.candidate_s2(a.s2_set)
    st = run(lam=a.lam, n_boot=a.n_boot, s3_set=a.s3_set, s2_source=src)
    sc = scores(st)
    print(summary(sc))
    if a.write:
        print(f"wrote {write(st, name=a.set, sc=sc)}")


if __name__ == "__main__":
    main()
