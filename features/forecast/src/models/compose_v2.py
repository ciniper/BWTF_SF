"""compose_v2 — the stage-by-stage composition S3 → S4 → OUT, with the hooks
live corrections inject through (STAGES_DESIGN.md Part C §3.3–§3.6, §6 changes
1–2, §7 spec files, §8 "P6 · compose_v2"; Part A A1–A2; Part B 2, 5, 6, 9, 14, 18).

Today's src/models/impact.compose does S3, S4 and OUT in one loop per legacy
group, so no stage can be scored on its own and an observation can only enter
at the basin. This module splits it into the three stages the stages build
scores, each a pure function of frames (rows = consecutive days, columns =
units), the same algebra for the oracle and the chained entry:

    S3  basin → zone. Per link ℓ (basin b → zone z):
            p_ℓ(D) = p_b(D) · s_ℓ(v̂_b(D))        v̂_ℓ(D) = φ_ℓ · v̂_b(D)
        a zone is the union of its links (a one-link zone is its link; East
        under SFPUC4 takes the spec's rule: max | noisy_or | cofire, every one
        inside the Fréchet bounds max p_ℓ ≤ p_z ≤ min(1, Σ p_ℓ)).
    S4  zone overflow history → water quality:
            q_z(D) = 1 − (1 − b_z(D)) · Π_{k=0..7} (1 − o_z(D−k) · x_z(k, s))
        o_z is 0/1 for the oracle and S3's p_z for chained: ``lingering`` is the
        one algebra, the inputs are all that differ. b_z is the background
        (constant for the GEO_V1 adapter, a rain logistic for zone_v3; never
        n_sampled, Part B 5).
    OUT the public number (A1: a sewer overflow or one still lingering):
            r_z(D) = 1 − (1 − p_z(D)) · Π_{k=1..7} (1 − p_z(D−k) · x_z(k, s))
        S4's algebra under the policy x(0) ≡ 1 (the overflow day counts as bad)
        and b ≡ 0 (no rain-runoff term).

S4 fit = S4 use (Part B 6): a fitter of an S4 spec maximizes the likelihood of
this same q — ``lingering`` over ``x_curves`` — never a table bucketed by "days
since the last event".

The S3 oracle (Part B 2) is the TRUE basin occurrence with the size still
predicted from rain: ``BasinInputs.oracle(y)`` swaps p for y and keeps v̂.
Nothing here takes a filed volume at S3 — it holds the link's own discharge
(Ocean Beach outfalls are a median 87% of Westside volume).

Injections (``Inject``; S5, §3.5 and §6 change 2):
    basin   p_b(D) = 1, then the split: live_v2's basin_swap, exactly what
            impact.compose does with ``observed``.
    link    a named outfall: its link p = 1, AFTER the split; every sibling
            link of the same basin rises to its co-firing share
            P(sibling fires | this link fires, same day) from the ledger.
    zone    a flagged station: its zone p_z = 1. Its basin comes only from
            geography.station_basin (§2.5); when that names one, the station's
            link is 1 and its siblings take their co-firing shares as above;
            when it is None the flag speaks for the zone alone.
    sample  a lab result: q_z(D) = the result (S4 only; A1 keeps it out of OUT).

GEO_V1 adapter (``geo_v1_adapter_specs``): the served set's stage 2 split
(stage2.json, shares by legacy group) as an S3 spec and its impact table as an
S4 spec whose units are GEO_V1's six links (one per legacy group). S4 and OUT
compose per link and the zone is the max over links, so with the policy the
adapter equals impact.compose + groups.zone_risks exactly on the same inputs
(tests/test_compose_v2.py, on every served-scorecard day and on the served
payload golden). The table is the one serving loads (live_dashboard.
_load_impact_table): stage2.json's refit table when it carries one, else
impact_table.json; smoothed by impact.smooth_table. Both specs are stamped
geo_v1 / two_stage_v1, so a reader keyed by GEO_V1 can tell them from a stages
bundle (Part B 14).

Spec files (§7) are read by ``load_s3_spec`` / ``load_s4_spec`` and checked
against the geography (stamp, link ids, outfalls, units, monotone tails); a
mismatch raises, nothing defaults. No IO at import; files are read only by the
two loaders and the adapter, and nothing is ever written into data/models/.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parents[3]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from impact import BUCKET_ORDER, bucket_index, smooth_table  # noqa: E402  (the served buckets and PAVA tail)
from shared import geography as G  # noqa: E402
from shared.outfalls import OBSERVED, OUTFALLS  # noqa: E402
from shared.zones import ZONE_OF_STATION, ZONES  # noqa: E402

SERVE_DIR = HERE.parents[1] / "data" / "models"   # read only (the adapter); never written here
LAGS = tuple(range(0, 8))     # k = 0..7: the overflow day and the week after (impact.compose's window)
SIZES = ("small", "large")
BUCKET_KEYS = tuple(f"{b}_{s}" for s in SIZES for b in BUCKET_ORDER)   # "0_small" … "6-7_large"
UNION_RULES = ("max", "noisy_or", "cofire")
SHARE_KINDS = ("identity", "constant", "logit_logvol", "size_blend")
S4_KINDS = ("impact_v2_adapter", "zone_v3")
ADAPTER_GEO = "max_of_link_risk"   # Geography.zone_union of geo_v1: compose per link, zone = max
# S4 background vocabulary for zone_v3 (§3.4): rain3 = two-gauge rain D−2…D (inches);
# hinge_rain3_<t> = max(0, rain3 − t); wet_season = October–April, the rainy season.
# n_sampled is not a predictor: it is unknown at issue time (Part B 5).
WET_SEASON_MONTHS = (10, 11, 12, 1, 2, 3, 4)
ISSUE_TIME_UNKNOWN = ("n_sampled", "log_n_sampled")
EVIDENCE_CODE = {True: "O", False: "G"}   # §2.4: O = observed in the 2016-17 feed, G = permit + geography

_S3_KEYS = {"geography", "pipeline", "kind", "links", "union", "cofire", "fit", "sources", "note"}
_S3_LINK_KEYS = {"basin", "zone", "outfalls", "evidence", "identity", "share", "vol_share", "legacy_group"}
_S4_KEYS = {"geography", "pipeline", "kind", "unit", "background", "buckets", "zone_median_mg", "median_mg",
            "monotone", "sources", "fit", "legacy", "legacy_group", "note"}


# ── inputs, injections, outputs ────────────────────────────────────────────

def _day(d) -> pd.Timestamp:
    return pd.Timestamp(d).normalize()


@dataclass(frozen=True)
class Inject:
    """Observations that replace a prediction (S5). Days may be dates, Timestamps
    or ISO strings. Every id is checked by the stage that reads it (unknown
    basin / outfall / station / zone, or a day outside the run, raises).

        basin   {day: {basin key}}            p_b(D) = 1, then the split (live_v2's basin_swap)
        link    {day: {outfall id}}           the outfall's link p = 1, siblings at their co-firing share
        zone    {day: {SFPUC station id}}     the station's zone p_z = 1; basin only via station_basin
        sample  {day: {zone key: result}}     q_z(D) = the result (0 or 1, or a probability)
    """
    basin: dict = field(default_factory=dict)
    link: dict = field(default_factory=dict)
    zone: dict = field(default_factory=dict)
    sample: dict = field(default_factory=dict)

    def __post_init__(self):
        for name in ("basin", "link", "zone"):
            norm: dict = {}
            for d, ids in dict(getattr(self, name) or {}).items():
                if isinstance(ids, str):
                    raise TypeError(f"Inject.{name}[{d}] must be a collection of ids, not the string {ids!r}")
                norm.setdefault(_day(d), set()).update(ids)
            object.__setattr__(self, name, {d: frozenset(v) for d, v in sorted(norm.items()) if v})
        smp: dict = {}
        for d, res in dict(self.sample or {}).items():
            for zk, val in dict(res).items():
                val = float(val)
                if not 0.0 <= val <= 1.0:
                    raise ValueError(f"Inject.sample[{d}][{zk}] = {val}: a result is 0, 1 or a probability")
                day = smp.setdefault(_day(d), {})
                if zk in day and day[zk] != val:   # two spellings of one day with different results: never keep the last
                    raise ValueError(f"Inject.sample: {zk} on {_day(d).date()} holds two results, {day[zk]} and {val}")
                day[zk] = val
        object.__setattr__(self, "sample", dict(sorted(smp.items())))

    @property
    def empty(self) -> bool:
        return not (self.basin or self.link or self.zone or self.sample)

    def only(self, *names) -> "Inject":
        """A copy holding just the named kinds (compose routes each kind to its stage)."""
        return Inject(**{n: getattr(self, n) for n in names})


def _inject(inject) -> Inject:
    if inject is None:
        return Inject()
    if not isinstance(inject, Inject):
        raise TypeError(f"inject must be an Inject, not {type(inject).__name__}")
    return inject


@dataclass(frozen=True)
class BasinInputs:
    """What S2 hands S3 (and the rain S4's background reads), for a run of days.

        p      date × basin key: P(an overflow starts on D), or the true 0/1 occurrence
        v_hat  date × basin key: the size if it overflows, MG, predicted from rain.
               Always S2's prediction, also for the oracle (Part B 2).
        rain   two-gauge daily rain (inches, outage-masked) indexed by date, from two
               days before the run; only an S4 spec with a rain background reads it."""
    p: pd.DataFrame
    v_hat: pd.DataFrame
    rain: pd.Series | None = None

    def oracle(self, y: pd.DataFrame) -> "BasinInputs":
        """The S3 oracle entry: the true basin occurrence y_b(D) ∈ {0, 1} with the
        size still v̂ from rain — never the filed volume, which holds the answer."""
        y = pd.DataFrame(y)
        vals = y.to_numpy(dtype=float)
        if np.isnan(vals).any() or not np.isin(vals, (0.0, 1.0)).all():
            raise ValueError("the S3 oracle input is the true occurrence: every value must be 0 or 1")
        if not (y.index.equals(self.p.index) and list(y.columns) == list(self.p.columns)):
            raise ValueError("the oracle occurrence must cover the same days and basins as the S2 output it replaces")
        return BasinInputs(y.astype(float), self.v_hat, self.rain)


@dataclass(frozen=True)
class History:
    """S4 / OUT input at the S4 spec's unit (zone, or link for the GEO_V1 adapter):
    p = P(the unit overflowed on D) — 0/1 for the oracle, S3's output for chained —
    and v = its size, MG."""
    p: pd.DataFrame
    v: pd.DataFrame


@dataclass(frozen=True)
class S3Out:
    link_p: pd.DataFrame
    link_v: pd.DataFrame
    zone_p: pd.DataFrame
    zone_v: pd.DataFrame

    def history(self, s4_spec: dict) -> History:
        """The overflow history S4 and OUT read: per link under the GEO_V1 adapter
        (it composes per legacy group), per zone otherwise."""
        if s4_spec["unit"] == "link":
            return History(self.link_p, self.link_v)
        return History(self.zone_p, self.zone_v)


@dataclass(frozen=True)
class Levels:
    """An S4 or OUT result: ``unit`` at the spec's unit, ``zone`` per zone
    (= unit for zone units; the max over the zone's links for the adapter)."""
    unit: pd.DataFrame
    zone: pd.DataFrame


@dataclass(frozen=True)
class Composition:
    s3: S3Out
    s4: Levels
    out: Levels

    def frames(self) -> dict:
        return {"s3_link_p": self.s3.link_p, "s3_link_v": self.s3.link_v, "s3_zone_p": self.s3.zone_p,
                "s3_zone_v": self.s3.zone_v, "s4_unit_q": self.s4.unit, "s4_q": self.s4.zone,
                "out_unit_r": self.out.unit, "out_r": self.out.zone}

    def full_history(self) -> pd.DatetimeIndex:
        """The days whose whole lag window D−7…D lies inside the run. S4 and OUT on the
        run's first 7 days leave out the overflows before it (as impact.compose does at
        the start of its table), so a scorer starts the run 7 days before its first
        scored day, or scores only these days."""
        return self.out.zone.index[len(LAGS) - 1:]


# ── frame checks ───────────────────────────────────────────────────────────

def _matrix(frame, columns, name: str, index: pd.DatetimeIndex | None = None,
            lo: float = 0.0, hi: float = 1.0) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """(values as a float copy in `columns` order, the index). Raises on missing or
    extra columns, a gap in the days, NaN or ±inf (a volume head that overflowed is
    not "very large"), or a value outside [lo, hi]."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{name} must be a DataFrame (date × unit), not {type(frame).__name__}")
    columns = list(columns)
    missing = [c for c in columns if c not in frame.columns]
    extra = [c for c in frame.columns if c not in columns]
    if missing or extra:
        raise KeyError(f"{name}: columns must be exactly {columns}; missing {missing}, unexpected {extra}")
    idx = pd.DatetimeIndex(frame.index)
    if len(idx) == 0:
        raise ValueError(f"{name} holds no days")
    if index is None:
        if not (idx == idx.normalize()).all() or (len(idx) > 1 and not ((idx[1:] - idx[:-1]) == pd.Timedelta(days=1)).all()):
            raise ValueError(f"{name}: rows must be consecutive whole days (the lags are row offsets)")
    elif not idx.equals(index):
        raise ValueError(f"{name}: its days differ from the run's ({idx[0].date()}…{idx[-1].date()} vs "
                         f"{index[0].date()}…{index[-1].date()})")
    m = frame[columns].to_numpy(dtype=float, copy=True)
    bad = ~np.isfinite(m) | (m < lo) | (m > hi)
    if bad.any():
        r, c = np.argwhere(bad)[0]
        raise ValueError(f"{name}: {m[r, c]} on {idx[r].date()} for {columns[c]} is not a finite value in [{lo}, {hi}]")
    return m, idx


def _row(idx: pd.DatetimeIndex, d: pd.Timestamp, kind: str) -> int:
    try:
        return int(idx.get_loc(d))
    except KeyError:
        raise KeyError(f"{kind} injection on {d.date()} is outside the run "
                       f"({idx[0].date()} … {idx[-1].date()})") from None


# ── spec checks and loaders (§7) ───────────────────────────────────────────

def _stamp(spec: dict, geo, what: str) -> None:
    stamp = spec["geography"] if "geography" in spec else G.MISSING_STAMP   # §2.6: no stamp = geo_v1
    if stamp != geo.version:
        raise ValueError(f"{what} spec is stamped {stamp!r} but the geography in use is {geo.version!r}")


def _prob(x, what: str) -> float:
    x = float(x)
    if not 0.0 <= x <= 1.0:
        raise ValueError(f"{what} = {x} is not a probability")
    return x


def _share_is_one(sh: dict) -> bool:
    """True when a share spec is 1 for every size (identity, a constant 1, or both size classes at 1)."""
    kind = sh["kind"]
    if kind == "identity":
        return True
    if kind == "constant":
        return float(sh["p"]) == 1.0
    if kind == "size_blend":
        return float(sh["large"]) == 1.0 and float(sh["small"]) == 1.0
    return False


def link_evidence(link) -> dict:
    """{outfall: 'O' | 'G'} — the registry's evidence tag for each of a link's outfalls (§2.4)."""
    return {o: EVIDENCE_CODE[OUTFALLS[o].evidence == OBSERVED] for o in link.outfalls}


def check_s3_spec(spec: dict, geo) -> dict:
    """Raise unless `spec` is an S3 links spec for `geo` (§7 s3_links.json): the stamp,
    exactly the geography's links with its basins, zones and outfalls, known share kinds
    with valid parameters, a union rule for every multi-link zone, and co-firing shares
    between links that exist. Returns the spec."""
    _stamp(spec, geo, "S3")
    unknown = set(spec) - _S3_KEYS
    if unknown:
        raise KeyError(f"S3 spec: unknown keys {sorted(unknown)}")
    links = spec["links"]
    want = {lk.id: lk for lk in geo.links}
    if set(links) != set(want):
        raise KeyError(f"S3 spec links must be {geo.version}'s {sorted(want)}; missing {sorted(set(want) - set(links))}, "
                       f"unexpected {sorted(set(links) - set(want))}")
    for lid, row in links.items():
        lk = want[lid]
        extra = set(row) - _S3_LINK_KEYS
        if extra:
            raise KeyError(f"S3 spec link {lid}: unknown keys {sorted(extra)}")
        got = (row["basin"], row["zone"], tuple(row["outfalls"]), bool(row["identity"]))
        if got != (lk.basin, lk.zone, lk.outfalls, lk.identity):
            raise ValueError(f"S3 spec link {lid} is {got}; {geo.version} has {(lk.basin, lk.zone, lk.outfalls, lk.identity)}")
        if dict(row["evidence"]) != link_evidence(lk):   # §7: every link carries its evidence tags
            raise ValueError(f"S3 spec link {lid}: evidence {row['evidence']} disagrees with the registry {link_evidence(lk)}")
        if "legacy_group" in row and row["legacy_group"] != lk.legacy_group:
            raise ValueError(f"S3 spec link {lid}: legacy group {row['legacy_group']!r}, the geography says {lk.legacy_group!r}")
        sh = row["share"]
        kind = sh["kind"]
        if kind not in SHARE_KINDS:
            raise ValueError(f"S3 spec link {lid}: unknown share kind {kind!r}; known {SHARE_KINDS}")
        if kind == "constant":
            _prob(sh["p"], f"{lid} constant share")
        elif kind == "size_blend":
            _prob(sh["large"], f"{lid} large share")
            _prob(sh["small"], f"{lid} small share")
            if not float(sh["median_mg"]) > 0:
                raise ValueError(f"S3 spec link {lid}: median_mg must be > 0")
        elif kind == "logit_logvol":
            a, b = float(sh["coef"]["a"]), float(sh["coef"]["b"])
            if not (math.isfinite(a) and math.isfinite(b)):
                raise ValueError(f"S3 spec link {lid}: non-finite share coefficients")
        # an identity link is its basin's only link (§2.4): its share is 1 on every day.
        # The served v2 split writes Mission Creek / Southeast as size classes at 1.0, so
        # those pass; anything that can scale the basin's p down is an integrity error.
        if lk.identity and not _share_is_one(sh):
            raise ValueError(f"S3 spec link {lid} is its basin's only link, so its share must be 1; got {sh}")
        phi = float(row["vol_share"])   # φ_ℓ: the link's share of its basin's volume (§3.3)
        if not (math.isfinite(phi) and 0 < phi <= 1) or (lk.identity and phi != 1.0):
            raise ValueError(f"S3 spec link {lid}: vol_share must be in (0, 1], and 1 for a basin's only link; got {phi}")
    union = spec.get("union", {})
    for zk, u in union.items():
        if zk not in ZONES:
            raise KeyError(f"S3 spec union: {zk!r} is not a zone")
        into = geo.links_into(zk)
        if len(into) < 2:
            raise ValueError(f"S3 spec union: zone {zk} has one link, so a union rule there does nothing")
        if u["rule"] not in UNION_RULES:
            raise ValueError(f"S3 spec union {zk}: unknown rule {u['rule']!r}; known {UNION_RULES}")
        if geo.zone_union == ADAPTER_GEO and u["rule"] != "max":
            raise ValueError(f"{geo.version} composes per link and takes the zone max; union {zk} must be 'max'")
        if u["rule"] == "cofire":
            ids = [lk.id for lk in into]
            if u["base"] not in ids or set(u["pi"]) != set(ids) - {u["base"]}:
                raise KeyError(f"S3 spec union {zk}: cofire needs a base among {ids} and π for each other link")
            for o, pi in u["pi"].items():
                _prob(pi, f"union {zk} π[{o}]")
    for zk in ZONES:
        if len(geo.links_into(zk)) > 1 and zk not in union and geo.zone_union != ADAPTER_GEO:
            raise KeyError(f"S3 spec: zone {zk} has {len(geo.links_into(zk))} links and no union rule")
    for key, p in spec.get("cofire", {}).items():
        a, _, b = key.partition("|")
        if a not in want or b not in want or a == b:
            raise KeyError(f"S3 spec cofire {key!r}: both sides must be different links of {geo.version}")
        _prob(p, f"cofire {key}")
    return spec


def _feature_ok(name: str) -> bool:
    if name in ("rain3", "wet_season"):
        return True
    if name.startswith("hinge_rain3_"):
        try:
            return float(name[len("hinge_rain3_"):]) >= 0
        except ValueError:
            return False
    return False


def s4_units(geo, spec: dict) -> tuple:
    """The S4 / OUT units: GEO_V1's links for the adapter, the zones otherwise."""
    if spec["unit"] == "link":
        return tuple(lk.id for lk in geo.links)
    return tuple(ZONES)


def check_s4_spec(spec: dict, geo) -> dict:
    """Raise unless `spec` is an S4 quality spec for `geo` (§7 s4_quality.json): the stamp,
    the unit matching the geography (link for geo_v1's per-group composition, zone
    otherwise), complete buckets with probabilities, non-increasing tails when it claims
    ``monotone``, sizes, and a background with no issue-time-unknown predictor. Returns it."""
    _stamp(spec, geo, "S4")
    unknown = set(spec) - _S4_KEYS
    if unknown:
        raise KeyError(f"S4 spec: unknown keys {sorted(unknown)}")
    kind = spec["kind"]
    if kind not in S4_KINDS:
        raise ValueError(f"S4 spec: unknown kind {kind!r}; known {S4_KINDS}")
    want_unit = "link" if geo.zone_union == ADAPTER_GEO else "zone"
    if spec["unit"] != want_unit:
        raise ValueError(f"S4 spec unit {spec['unit']!r}: {geo.version} composes S4 per {want_unit}")
    if (kind == "impact_v2_adapter") != (want_unit == "link"):
        raise ValueError(f"S4 spec kind {kind!r} does not fit {geo.version} (the adapter is geo_v1's, zone_v3 is per zone)")
    units = s4_units(geo, spec)
    if set(spec["buckets"]) != set(units):
        raise KeyError(f"S4 spec buckets must be keyed by {list(units)}; got {sorted(spec['buckets'])}")
    sizes_key = "median_mg" if kind == "impact_v2_adapter" else "zone_median_mg"
    if set(spec[sizes_key]) != set(units) or not all(float(m) > 0 for m in spec[sizes_key].values()):
        raise ValueError(f"S4 spec {sizes_key}: a median > 0 for each of {list(units)}")
    for u in units:
        bk = spec["buckets"][u]
        if set(bk) != set(BUCKET_KEYS):
            raise KeyError(f"S4 spec buckets[{u}] must hold {list(BUCKET_KEYS)}")
        for key, x in bk.items():
            if x is None:
                if kind == "zone_v3":
                    raise ValueError(f"S4 spec buckets[{u}][{key}] is empty; zone_v3 fits every bucket")
                continue
            _prob(x, f"buckets[{u}][{key}]")
        if spec.get("monotone"):
            for s in SIZES:
                xs = [bk[f"{b}_{s}"] for b in BUCKET_ORDER if bk[f"{b}_{s}"] is not None]
                if any(b > a + 1e-12 for a, b in zip(xs, xs[1:])):
                    raise ValueError(f"S4 spec buckets[{u}] {s}: the tail rises ({xs}) but the spec says monotone")
        elif kind == "zone_v3":
            raise ValueError("a zone_v3 spec is monotone non-increasing by construction (§3.4); say monotone: true")
    bg = spec["background"]
    if bg["kind"] == "constant":
        if set(bg["p"]) != set(units):
            raise KeyError(f"S4 spec background p must be keyed by {list(units)}")
        for u, b in bg["p"].items():
            if _prob(b, f"background[{u}]") >= 1.0:
                raise ValueError(f"S4 spec background[{u}] = 1 leaves nothing to compose")
    elif bg["kind"] == "logistic":
        feats = list(bg["features"])
        bad = [f for f in feats if f in ISSUE_TIME_UNKNOWN or "n_sampled" in f]
        if bad:
            raise ValueError(f"S4 background predictor(s) {bad}: unknown at issue time, a stratum only (Part B 5)")
        unknown_f = [f for f in feats if not _feature_ok(f)]
        if unknown_f:
            raise ValueError(f"S4 background feature(s) {unknown_f} not in the vocabulary (rain3, hinge_rain3_<t>, wet_season)")
        if set(bg["coef"]) != set(units):
            raise KeyError(f"S4 spec background coef must be keyed by {list(units)}")
        for u, c in bg["coef"].items():
            if set(c) != {"intercept", *feats} or not all(math.isfinite(float(v)) for v in c.values()):
                raise ValueError(f"S4 background coef[{u}] must hold finite intercept + {feats}")
    else:
        raise ValueError(f"S4 spec background kind {bg['kind']!r}: constant | logistic")
    if "legacy_group" in spec:
        want_lg = {lk.id: lk.legacy_group for lk in geo.links} if want_unit == "link" else None
        if spec["legacy_group"] != want_lg:
            raise ValueError(f"S4 spec legacy_group {spec['legacy_group']} is not {geo.version}'s {want_lg}")
    rnd = spec.get("legacy", {}).get("round_out")
    if rnd is not None and (not isinstance(rnd, int) or rnd < 0):
        raise ValueError(f"S4 spec legacy round_out must be a whole number of decimals, got {rnd!r}")
    return spec


def load_s3_spec(path, geo) -> dict:
    """Read and check an s3_links.json (§7)."""
    return check_s3_spec(json.loads(Path(path).read_text()), geo)


def load_s4_spec(path, geo) -> dict:
    """Read and check an s4_quality.json (§7)."""
    return check_s4_spec(json.loads(Path(path).read_text()), geo)


# ── co-firing shares and union rules from the ledger ───────────────────────

def link_fire_days(geo, events: pd.DataFrame, end=None) -> dict:
    """{link id: set of days} on which an outfall of the link started an event
    (``event_date`` of the CIWQS ledger rows, sf_csd_events.csv), through `end`.
    An outfall the geography does not know raises (geography.link_of_outfall)."""
    if events is None or len(events) == 0:
        raise ValueError("no ledger events to count co-firing from")
    ev = events[["event_date", "outfall_id"]].copy()
    ev["event_date"] = pd.to_datetime(ev["event_date"]).dt.normalize()
    if end is not None:
        ev = ev[ev["event_date"] <= _day(end)]
    days = {lk.id: set() for lk in geo.links}
    for d, oid in zip(ev["event_date"], ev["outfall_id"]):
        days[geo.link_of_outfall(oid).id].add(d)
    return days


def cofire(geo, events: pd.DataFrame, end=None) -> dict:
    """{"a|b": P(link a fires | link b fires, same day)} for every ordered pair of
    links sharing a basin (S5 siblings) or a zone (the cofire union's π). A link
    that never fired leaves its conditionals undefined, which raises."""
    days = link_fire_days(geo, events, end)
    out = {}
    for a in geo.links:
        for b in geo.links:
            if a.id == b.id or (a.basin != b.basin and a.zone != b.zone):
                continue
            if not days[b.id]:
                raise ValueError(f"link {b.id} never fired in the ledger window; P(·|{b.id}) is undefined")
            out[f"{a.id}|{b.id}"] = len(days[a.id] & days[b.id]) / len(days[b.id])
    return out


def union_block(geo, rule: str, shares: dict | None = None) -> dict:
    """The ``union`` block for every multi-link zone under one rule. For ``cofire``
    the base is the zone's first link in geography order (Central for East) and
    π_o = P(no base | o) = 1 − P(base | o), from `shares` (``cofire``)."""
    if rule not in UNION_RULES:
        raise ValueError(f"unknown union rule {rule!r}; known {UNION_RULES}")
    if geo.zone_union == ADAPTER_GEO and rule != "max":
        raise ValueError(f"{geo.version} composes per link and takes the zone max")
    out = {}
    for zk in ZONES:
        into = geo.links_into(zk)
        if len(into) < 2:
            continue
        if rule == "cofire":
            if shares is None:
                raise ValueError("the cofire union needs the co-firing shares (compose_v2.cofire)")
            base = into[0]
            out[zk] = {"rule": "cofire", "base": base.id, "pi": {o.id: 1.0 - shares[f"{base.id}|{o.id}"] for o in into[1:]}}
        else:
            out[zk] = {"rule": rule}
    return out


def benchmark_s3_spec(geo, share: str = "identity", constants: dict | None = None, union: str = "max",
                      shares: dict | None = None, vol_share: dict | None = None) -> dict:
    """An S3 spec for §3.3's benchmarks: ``identity`` (every link at share 1, the
    v1 behaviour) or ``constant`` (a non-identity link at its constant share from
    `constants`, no size term). `shares` = co-firing shares (``cofire``), used by
    the cofire union and by S5 sibling injections. `vol_share` = φ for every link,
    or None for φ = 1 throughout (a link's size is its basin's)."""
    if share not in ("identity", "constant"):
        raise ValueError(f"benchmark share {share!r}: identity | constant")
    if vol_share is not None and set(vol_share) != {lk.id for lk in geo.links}:
        raise KeyError(f"vol_share must hold every link of {geo.version}; got {sorted(vol_share)}")
    links = {}
    for lk in geo.links:
        if share == "identity" or lk.identity:
            sh = {"kind": "identity"}
        else:
            if constants is None or lk.id not in constants:
                raise KeyError(f"constant-share benchmark: no share for {lk.id}")
            sh = {"kind": "constant", "p": float(constants[lk.id])}
        links[lk.id] = {"basin": lk.basin, "zone": lk.zone, "outfalls": list(lk.outfalls), "evidence": link_evidence(lk),
                        "identity": lk.identity, "share": sh, "vol_share": 1.0 if vol_share is None else float(vol_share[lk.id])}
    spec = {"geography": geo.version, "kind": f"benchmark_{share}", "links": links,
            "union": union_block(geo, union, shares), "cofire": dict(shares or {})}
    return check_s3_spec(spec, geo)


# ── S3 · basin → zone ──────────────────────────────────────────────────────

def _apply_share(sh: dict, pb: np.ndarray, vb: np.ndarray) -> np.ndarray:
    kind = sh["kind"]
    if kind == "identity":
        return pb.copy()
    if kind == "constant":
        return pb * float(sh["p"])
    if kind == "size_blend":   # stage2.group_share: w = v/(v + median), w·g_large + (1 − w)·g_small
        w = vb / (vb + float(sh["median_mg"]))
        return pb * (w * float(sh["large"]) + (1 - w) * float(sh["small"]))
    if kind == "logit_logvol":
        z = float(sh["coef"]["a"]) + float(sh["coef"]["b"]) * np.log1p(vb)
        return pb * (1.0 / (1.0 + np.exp(-z)))
    raise ValueError(f"unknown share kind {kind!r}")


def _union(rule: dict, cols: np.ndarray, ids: list) -> np.ndarray:
    """p_z from the zone's link columns, held inside the Fréchet bounds
    [max p_ℓ, min(1, Σ p_ℓ)]. max and noisy-OR lie inside them exactly (the clip
    only absorbs rounding: 1 − (1 − 0.1) is 0.0999…98 in floating point); cofire,
    p_base + Σ π_o·p_o, leaves them when the link probabilities disagree with
    its π (p_S above p_C / (1 − π)), and the clip is the bound it breaks."""
    lo, hi = cols.max(axis=1), np.minimum(1.0, cols.sum(axis=1))
    r = rule["rule"]
    if r == "max":
        return lo
    if r == "noisy_or":
        u = 1.0 - np.prod(1.0 - cols, axis=1)
    elif r == "cofire":
        base = ids.index(rule["base"])
        u = cols[:, base] + sum(float(rule["pi"][o]) * cols[:, i] for i, o in enumerate(ids) if i != base)
    else:
        raise ValueError(f"unknown union rule {r!r}")
    return np.clip(u, lo, hi)


def _station_carrier(geo, sid: str, basin: str | None, link_units: bool):
    """The link a station flag sets to 1: under link units the one link posting the
    station (the zone has no level of its own there); otherwise the link from the
    station's basin into its zone, or None when station_basin named no basin."""
    zk = ZONE_OF_STATION[sid]
    if link_units:
        posting = [lk for lk in geo.links_into(zk) if sid in lk.stations]
        if len(posting) != 1:
            raise ValueError(f"station {sid} is posted by {len(posting)} links of {geo.version}; a flag needs one carrier")
        return posting[0]
    if basin is None:
        return None
    hit = [lk for lk in geo.links_into(zk) if lk.basin == basin]
    if len(hit) != 1:
        raise ValueError(f"station {sid}: basin {basin} has {len(hit)} links into zone {zk}")
    return hit[0]


def s3(geo, spec: dict, p_basin: pd.DataFrame, v_hat: pd.DataFrame, inject: Inject | None = None) -> S3Out:
    """S3 for a run of days. `p_basin` = S2's P(overflow) per basin (the true 0/1
    occurrence for the oracle), `v_hat` = S2's size predicted from rain (also for
    the oracle; Part B 2). Order: basin injections → the split → link and zone
    injections (after the split) → the zone union → zone injections at the zone.
    A zone's size is the sum over its feeding basins of that basin's largest link
    size into the zone — independent of p, so raising a p never flips a size class."""
    check_s3_spec(spec, geo)
    inj = _inject(inject)
    if inj.sample:
        raise ValueError("a lab result enters at S4 (q_z), not S3")
    keys = list(geo.keys)
    P, idx = _matrix(p_basin, keys, "p_basin")
    V, _ = _matrix(v_hat, keys, "v_hat", index=idx, hi=math.inf)
    col = {k: i for i, k in enumerate(keys)}
    for d, basins in inj.basin.items():
        r = _row(idx, d, "basin")
        for b in basins:
            if b not in col:
                raise KeyError(f"basin injection: {b!r} is not a basin of {geo.version}")
            P[r, col[b]] = 1.0                       # p_b = 1, then the split (live_v2's basin_swap)

    links = list(geo.links)
    lcol = {lk.id: j for j, lk in enumerate(links)}
    T = len(idx)
    LP, LV = np.empty((T, len(links))), np.empty((T, len(links)))
    for j, lk in enumerate(links):
        row = spec["links"][lk.id]
        pb, vb = P[:, col[lk.basin]], V[:, col[lk.basin]]
        LP[:, j] = _apply_share(row["share"], pb, vb)
        LV[:, j] = vb * float(row["vol_share"])

    # observations after the split (§6 change 2): the carrier link = 1, its basin's other links ≥ their co-firing share
    fired: dict = {}
    sib_from: dict = {}
    zone_hit: dict = {}
    for d, oids in inj.link.items():
        r = _row(idx, d, "link")
        for o in oids:
            lk = geo.link_of_outfall(o)
            fired.setdefault(r, set()).add(lk.id)
            sib_from.setdefault(r, set()).add(lk.id)
    link_units = geo.zone_union == ADAPTER_GEO
    for d, sids in inj.zone.items():
        r = _row(idx, d, "zone")
        for sid in sids:
            if sid not in ZONE_OF_STATION:
                raise KeyError(f"zone injection: {sid!r} is not an SFPUC station id")
            basin = geo.station_basin(sid)
            zone_hit.setdefault(r, set()).add(ZONE_OF_STATION[sid])
            carrier = _station_carrier(geo, sid, basin, link_units)
            if carrier is not None:
                fired.setdefault(r, set()).add(carrier.id)
                if basin is not None and carrier.basin == basin:
                    sib_from.setdefault(r, set()).add(carrier.id)
    cof = spec.get("cofire", {})
    by_id = {lk.id: lk for lk in links}
    for r, ids in fired.items():
        for lid in ids:
            LP[r, lcol[lid]] = 1.0
    for r, ids in sib_from.items():
        for lid in ids:
            for sib in geo.links_from(by_id[lid].basin):
                if sib.id in fired[r]:
                    continue
                key = f"{sib.id}|{lid}"
                if key not in cof:
                    raise KeyError(f"no co-firing share {key!r} in the S3 spec; fit it with compose_v2.cofire(geo, ledger)")
                LP[r, lcol[sib.id]] = max(LP[r, lcol[sib.id]], float(cof[key]))

    zones = list(ZONES)
    ZP, ZV = np.empty((T, len(zones))), np.zeros((T, len(zones)))
    union = spec.get("union", {})
    for zi, zk in enumerate(zones):
        into = geo.links_into(zk)
        if not into:
            raise ValueError(f"zone {zk} has no link in {geo.version}")
        cols = [lcol[lk.id] for lk in into]
        if len(into) == 1:
            ZP[:, zi] = LP[:, cols[0]]
        else:
            rule = union.get(zk, {"rule": "max"}) if link_units else union[zk]
            ZP[:, zi] = _union(rule, LP[:, cols], [lk.id for lk in into])
        for b in dict.fromkeys(lk.basin for lk in into):
            ZV[:, zi] += LV[:, [lcol[lk.id] for lk in into if lk.basin == b]].max(axis=1)
    zcol = {zk: i for i, zk in enumerate(zones)}
    for r, zks in zone_hit.items():
        for zk in zks:
            ZP[r, zcol[zk]] = 1.0
    lids = [lk.id for lk in links]
    return S3Out(pd.DataFrame(LP, idx, lids), pd.DataFrame(LV, idx, lids),
                 pd.DataFrame(ZP, idx, zones), pd.DataFrame(ZV, idx, zones))


# ── S4 and OUT: one algebra ────────────────────────────────────────────────

def lingering(p: np.ndarray, x: np.ndarray, b: np.ndarray | None = None) -> np.ndarray:
    """1 − (1 − b(D)) · Π_k (1 − p(D−k) · x(k, D−k)) for every row D: p is (days, units),
    x is (lags, days, units) with x[k, j] the weight of an overflow on day j at lag k,
    b the background (None = 0). Lags before the first day are left out, as
    impact.compose does; the factors multiply in lag order, so with x(0) = 1 and
    b = None this is impact.compose's arithmetic bit for bit."""
    p = np.asarray(p, dtype=float)
    x = np.asarray(x, dtype=float)
    T = p.shape[0]
    acc = np.ones_like(p)
    for k in range(min(x.shape[0], T)):
        acc[k:] = acc[k:] * (1.0 - p[: T - k] * x[k, : T - k])
    return 1.0 - acc if b is None else 1.0 - (1.0 - np.asarray(b, dtype=float)) * acc


def x_curves(spec: dict, units, v: np.ndarray, day_of: float | None = None) -> np.ndarray:
    """(lags, days, units): x_u(k, size of the overflow on day j). The adapter blends
    its small and large curves by w = v/(v + median) (impact.impact_fraction, one
    side alone when the other is empty, 0 when both are); zone_v3 takes the large
    curve when v ≥ the zone median. ``day_of`` overrides x(0) (OUT's policy: 1)."""
    v = np.asarray(v, dtype=float)
    T = v.shape[0]
    X = np.empty((len(LAGS), T, len(units)))
    adapter = spec["kind"] == "impact_v2_adapter"
    for ui, u in enumerate(units):
        bk = spec["buckets"][u]
        vu = v[:, ui]
        if adapter:
            w = vu / (vu + float(spec["median_mg"][u]))
        else:
            large = vu >= float(spec["zone_median_mg"][u])
        for k in LAGS:
            if k == 0 and day_of is not None:
                X[0, :, ui] = float(day_of)
                continue
            b = BUCKET_ORDER[bucket_index(k)]
            xs, xl = bk[f"{b}_small"], bk[f"{b}_large"]
            if not adapter:
                X[k, :, ui] = np.where(large, float(xl), float(xs))
            elif xs is None and xl is None:
                X[k, :, ui] = 0.0
            elif xs is None or xl is None:
                X[k, :, ui] = float(xl if xs is None else xs)
            else:
                X[k, :, ui] = w * float(xl) + (1 - w) * float(xs)
    return X


def background(spec: dict, units, index: pd.DatetimeIndex, rain: pd.Series | None) -> np.ndarray:
    """(days, units) b_u(D): the adapter's constant baseline, or zone_v3's logistic in
    the rain features (rain3 = two-gauge rain D−2…D, which needs `rain` from two days
    before the run; hinges; the wet-season flag)."""
    bg = spec["background"]
    T = len(index)
    if bg["kind"] == "constant":
        return np.tile(np.array([float(bg["p"][u]) for u in units]), (T, 1))
    if rain is None:
        raise ValueError("this S4 spec's background reads rain; pass the two-gauge daily rain")
    s = pd.Series(rain, dtype=float)
    s.index = pd.DatetimeIndex(s.index).normalize()
    need = pd.date_range(index[0] - pd.Timedelta(days=2), index[-1])
    got = s.reindex(need)
    if got.isna().any():
        raise ValueError(f"rain must cover {need[0].date()} … {need[-1].date()} with no gaps "
                         f"(first gap {got[got.isna()].index[0].date()})")
    rain3 = got.rolling(3).sum().to_numpy()[2:]
    feats = {"rain3": rain3, "wet_season": np.isin(index.month, WET_SEASON_MONTHS).astype(float)}
    out = np.empty((T, len(units)))
    for ui, u in enumerate(units):
        c = bg["coef"][u]
        z = np.full(T, float(c["intercept"]))
        for f in bg["features"]:
            val = feats[f] if f in feats else np.maximum(0.0, rain3 - float(f[len("hinge_rain3_"):]))
            z = z + float(c[f]) * val
        out[:, ui] = 1.0 / (1.0 + np.exp(-z))
    return out


def _zone_level(geo, spec: dict, units, M: np.ndarray, index) -> pd.DataFrame:
    if spec["unit"] == "zone":   # its own copy: a sample injection writes the zone frame, never the unit frame
        return pd.DataFrame(M.copy(), index, list(units))
    ucol = {u: i for i, u in enumerate(units)}
    return pd.DataFrame({zk: M[:, [ucol[lk.id] for lk in geo.links_into(zk)]].max(axis=1) for zk in ZONES}, index=index)


def _history(geo, spec: dict, hist: History):
    units = s4_units(geo, spec)
    Hp, idx = _matrix(hist.p, units, "history p")
    Hv, _ = _matrix(hist.v, units, "history v", index=idx, hi=math.inf)
    return units, Hp, Hv, idx


def s4(geo, spec: dict, hist: History, rain: pd.Series | None = None, inject: Inject | None = None) -> Levels:
    """S4: q per unit and per zone. The oracle and chained entries call this same
    function; only ``hist`` differs (0/1 truth vs S3's p). A sample injection sets
    q_z(D) to the result at the zone (the unit frame keeps the model's value)."""
    check_s4_spec(spec, geo)
    inj = _inject(inject)
    if inj.basin or inj.link or inj.zone:
        raise ValueError("basin, link and zone injections act at S3; pass S3's output as the history")
    units, Hp, Hv, idx = _history(geo, spec, hist)
    q = lingering(Hp, x_curves(spec, units, Hv), background(spec, units, idx, rain))
    zone = _zone_level(geo, spec, units, q, idx)
    for d, res in inj.sample.items():
        r = _row(idx, d, "sample")
        for zk, val in res.items():
            if zk not in ZONES:
                raise KeyError(f"sample injection: {zk!r} is not a zone")
            zone.iloc[r, zone.columns.get_loc(zk)] = val
    return Levels(pd.DataFrame(q, idx, list(units)), zone)


def _round_legacy(M: np.ndarray, decimals: int) -> np.ndarray:
    """impact.compose's round(·, 3) per group: Python's correctly rounded float
    round, not numpy's scaled rint, so the adapter matches it bit for bit."""
    return np.array([[round(float(v), decimals) for v in row] for row in M]).reshape(M.shape)


def out(geo, spec: dict, hist: History, inject: Inject | None = None) -> Levels:
    """OUT: r per unit and per zone, S4's algebra under the policy x(0) ≡ 1 and no
    background (A1). The GEO_V1 adapter rounds each group's r as impact.compose does
    (spec legacy.round_out) before the zone max. Observations reach OUT only through
    the history S3 hands it, so any injection here raises."""
    check_s4_spec(spec, geo)
    if not _inject(inject).empty:
        raise ValueError("OUT reads the overflow history only: basin, link and zone injections act at S3, "
                         "and a lab result is an S4 observation the public number does not claim (A1)")
    units, Hp, Hv, idx = _history(geo, spec, hist)
    r = lingering(Hp, x_curves(spec, units, Hv, day_of=1.0))
    rnd = spec.get("legacy", {}).get("round_out")
    if rnd is not None:
        r = _round_legacy(r, rnd)
    return Levels(pd.DataFrame(r, idx, list(units)), _zone_level(geo, spec, units, r, idx))


def compose(geo, specs: dict, inputs: BasinInputs, inject: Inject | None = None) -> Composition:
    """S3 → S4 → OUT for a run of days. `specs` = {"s3": ..., "s4": ...} (e.g.
    ``geo_v1_adapter_specs()``); `inputs` = S2's output (``BasinInputs``; its
    ``oracle(y)`` gives the S3 oracle entry). Basin, link and zone injections go
    to S3; sample injections to S4; OUT reads S3's injected history."""
    if set(specs) != {"s3", "s4"}:
        raise KeyError(f"specs must be {{'s3', 's4'}}, got {sorted(specs)}")
    if not isinstance(inputs, BasinInputs):
        raise TypeError("inputs must be BasinInputs (S2's p and v̂, and rain for a rain background)")
    inj = _inject(inject)
    r3 = s3(geo, specs["s3"], inputs.p, inputs.v_hat, inj.only("basin", "link", "zone"))
    hist = r3.history(specs["s4"])
    q = s4(geo, specs["s4"], hist, inputs.rain, inj.only("sample"))
    return Composition(r3, q, out(geo, specs["s4"], hist))


def payload_blocks(geo, s4_spec: dict, comp: Composition, day) -> dict:
    """One day's OUT as the live payload carries it (Part B 18): ``zones`` {zone: r}
    (the scalar Today and the home page read), ``impact_groups`` (legacy group names
    under the GEO_V1 adapter, as served; zone-keyed under a stages geography) and
    ``predictions`` {basin: its worst unit, "citywide": the worst basin}. Under the
    adapter this is the plain block live_dashboard._day_payload builds."""
    d = _day(day)
    r = comp.out.unit.loc[d]
    if s4_spec["unit"] == "link":
        by_id = {lk.id: lk for lk in geo.links}
        per_unit = {by_id[u].legacy_group: float(r[u]) for u in r.index}
        fed = {b.key: [lk.id for lk in geo.links_from(b.key)] for b in geo.basins}
    else:
        per_unit = {zk: float(r[zk]) for zk in r.index}
        fed = {b.key: list(dict.fromkeys(lk.zone for lk in geo.links_from(b.key))) for b in geo.basins}
    preds = {bk: max(float(r[u]) for u in units) for bk, units in fed.items()}
    preds["citywide"] = max(preds.values())
    return {"predictions": preds, "impact_groups": per_unit,
            "zones": {zk: float(comp.out.zone.at[d, zk]) for zk in ZONES}}


# ── the GEO_V1 adapter: the served set as stage specs ──────────────────────

def _v2_share(stage2: dict, group: str) -> dict:
    """stage2.group_share for one legacy group, as an S3 share: the same None
    fills (a missing size takes the other; neither → the all-days share, else 1)."""
    s = stage2["shares"].get(group)
    if not s:
        return {"kind": "identity"}
    gl, gs, ga = ((s.get(k) or {}).get("p") for k in ("large", "small", "all"))
    if gl is None and gs is None:
        return {"kind": "identity"} if ga is None else {"kind": "constant", "p": ga}
    gl = gs if gl is None else gl
    gs = gl if gs is None else gs
    meds = stage2.get("median_event_volume_mg") or {}
    if group not in meds:   # group_share would blend around 1.0 MG; a split with size classes and no median is broken
        raise KeyError(f"stage 2 spec: shares by size for {group!r} but no median_event_volume_mg")
    med = meds[group] or 1.0   # group_share's own fill for a 0 median, kept for parity
    return {"kind": "size_blend", "large": gl, "small": gs, "median_mg": med, "n_basin_days": s.get("n_basin_days")}


def _attributable(buckets: dict, size: str, bi: int, base: float):
    """impact.impact_fraction's x for one size and bucket: the nearest bucket at or
    before `bi` that holds a value, with the background removed; None if none does."""
    for j in range(bi, -1, -1):
        p = buckets.get(f"d{BUCKET_ORDER[j]}_{size}", {}).get("p_elevated")
        if p is not None:
            return max(0.0, (p - base) / (1 - base)) if base < 1 else 0.0
    return None


def geo_v1_adapter_specs(stage2: dict | None = None, impact_table: dict | None = None, serve_dir=None,
                         cofire_shares: dict | None = None, round_out: bool = True) -> dict:
    """{"s3", "s4"} for the served set under geo_v1, in memory. Read (never written)
    from `serve_dir` (default data/models/) unless `stage2` / `impact_table` are given:
    stage2.json's group shares (v2; v1 or no file = identity links) and the table
    serving uses — stage2.json's refit ``impact_table`` when present, else
    impact_table.json — smoothed by impact.smooth_table. S4 x(0) is the measured d0
    bucket, b is the group's ``baseline_no_recent_discharge``; OUT's policy gives
    x(0) = 1, b = 0 and, with `round_out`, impact.compose's 3-dp group rounding.
    `cofire_shares` (``cofire(geo_v1, ledger)``) feeds sibling injections; without
    it a link or station injection with siblings raises."""
    geo = G.get("geo_v1")
    sources = {}
    if stage2 is None and impact_table is None:
        sd = Path(serve_dir) if serve_dir is not None else SERVE_DIR
        # Part B 14: a reader of a set's directory by GEO_V1 key branches on the set's stamps.
        # After a stages promotion the served directory holds an SFPUC4 bundle, whose stage2.json /
        # impact_table.json (if any are left) are not what it serves; refuse instead of adapting them.
        for name in ("served.json", "manifest.json"):
            if (sd / name).exists():
                m = json.loads((sd / name).read_text())
                stamps = (m["geography"] if "geography" in m else G.MISSING_STAMP,
                          m["pipeline"] if "pipeline" in m else "two_stage_v1")
                if stamps != ("geo_v1", "two_stage_v1"):
                    raise ValueError(f"{sd / name} is stamped {stamps}; the geo_v1 adapter reads two_stage_v1 sets only")
        s2p = sd / "stage2.json"
        stage2 = json.loads(s2p.read_text()) if s2p.exists() else None
        sources["stage2"] = str(s2p.name) if stage2 else None
        if stage2 and stage2.get("impact_table"):
            impact_table, sources["impact_table"] = stage2["impact_table"], "stage2.json impact_table"
        else:
            impact_table, sources["impact_table"] = json.loads((sd / "impact_table.json").read_text()), "impact_table.json"
    else:
        sources = {"stage2": "passed in" if stage2 else None}
        if impact_table is None:
            if not (stage2 and stage2.get("impact_table")):
                raise ValueError("pass the impact table: this stage 2 spec carries none")
            impact_table, sources["impact_table"] = stage2["impact_table"], "passed-in stage 2 impact_table"
        else:
            sources["impact_table"] = "passed in"
    variant = (stage2 or {}).get("variant", "v1")
    if variant not in ("v1", "v2"):
        raise ValueError(f"the geo_v1 adapter reproduces stage 2 v1 and v2, not {variant!r}")
    groups = {lk.legacy_group for lk in geo.links}
    if variant == "v2" and not set(stage2["shares"]) <= groups:   # group_share would ignore a misspelt group: its link would go identity
        raise KeyError(f"stage 2 spec shares for unknown groups {sorted(set(stage2['shares']) - groups)}; geo_v1 has {sorted(groups)}")
    table = smooth_table(impact_table)
    links, buckets, base, median, group = {}, {}, {}, {}, {}
    for lk in geo.links:
        g = lk.legacy_group
        share = _v2_share(stage2, g) if variant == "v2" else {"kind": "identity"}
        links[lk.id] = {"basin": lk.basin, "zone": lk.zone, "outfalls": list(lk.outfalls), "evidence": link_evidence(lk),
                        "identity": lk.identity, "legacy_group": g, "share": share, "vol_share": 1.0}
        gt = table.get(g) or {}
        bks = gt.get("buckets") or {}
        # impact_fraction would fall back to x = 1 on the day / 0 after, a 0 background and a 1 MG
        # median; every served and candidate table has all three, so a gap here is a broken table
        if not bks or "p_elevated" not in bks.get("baseline_no_recent_discharge", {}) or "median_event_volume_mg" not in gt:
            raise ValueError(f"impact table: legacy group {g!r} lacks its buckets, baseline p_elevated or median volume")
        b0 = bks["baseline_no_recent_discharge"]["p_elevated"]
        buckets[lk.id] = {f"{b}_{s}": _attributable(bks, s, bi, b0) for s in SIZES for bi, b in enumerate(BUCKET_ORDER)}
        base[lk.id] = b0
        median[lk.id] = gt["median_event_volume_mg"] or 1.0   # impact_fraction's fill for a 0 median, kept for parity
        group[lk.id] = g
    s3_spec = {"geography": geo.version, "pipeline": "two_stage_v1", "kind": f"stage2_{variant}_adapter", "links": links,
               "union": union_block(geo, "max"), "cofire": dict(cofire_shares or {}), "sources": sources,
               "note": "the served stage 2 split per legacy group (stage2.group_share); zone = max over groups"}
    s4_spec = {"geography": geo.version, "pipeline": "two_stage_v1", "kind": "impact_v2_adapter", "unit": "link",
               "background": {"kind": "constant", "p": base}, "buckets": buckets, "median_mg": median,
               "monotone": True, "legacy_group": group, "legacy": {"round_out": 3} if round_out else {},
               "sources": sources,
               "note": "the served impact table per legacy group: x = (p − baseline)/(1 − baseline), small/large blended "
                       "by w = v/(v + the group's median); zone = max over groups"}
    return {"s3": check_s3_spec(s3_spec, geo), "s4": check_s4_spec(s4_spec, geo)}
