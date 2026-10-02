"""Verification library for the five-stage forecast (P3, 2026-10-01).

One home for every score a stage report prints, so the stages build, the report
and the Model check never carry their own arithmetic. The names are the
WWRP/JWGFVR ones: FAR = b/(a+b), the share of alerts that were false; the rate
the Model check used to call "far", fp/(fp+tn), is POFD (design Part B 21).

Pure functions on array-likes: no repo imports, no IO, no clock. A NaN is a
missing value, never a zero (the X-S1-NWPGAP lesson): each function drops rows
where a forecast or an observation is NaN, and paired functions drop a row from
both arms at once so the comparison stays on identical rows. A NaN skill
reference on a scored row raises instead: dropping it would score the skill on
fewer rows than the Brier score printed beside it.

What is here, and why (features/forecast/STAGES_DESIGN.md §5, Parts A and B):

- ``brier`` is the primary score. It is strictly proper, it is the mean
  cost-loss expense over every cost ratio at once (so no ratio has to be built
  in), and it is finite on the exact 0s and 1s the composition and the live
  injections emit. Ranks and decisions use paired Brier differences on
  identical rows (``paired_delta``).
- ``bss`` is skill against a per-row reference: a training-fold climatology per
  unit × calendar month, smoothed over ±1 month (``climatology_ref``). The
  scored window's own base rate is never the reference. Pooling across strata
  is Hamill & Juras (2006): 1 − Σ n·BS / Σ n·BS_ref, never a ratio against a
  pooled base rate (``pooled_bss``, ``pool_bss``).
- ``corp`` decomposes the Brier score itself (Dimitriadis, Gneiting & Jordan
  2021): BS = MCB − DSC + UNC, with the PAV (isotonic) recalibration as the
  reliability curve.
- Threshold scores (``contingency*``) are reported at the fixed public risk
  edges, Medium p ≥ 0.205, High p ≥ 0.505, Extreme p ≥ 0.805. No line is chosen
  by cost and there is no cost basis or Platt alert line (Chase, 2026-10-01).
- ``murphy`` and ``rev_curve`` (Richardson 2000) show every cost ratio at once.
  They are reported, never used to choose anything.
- Uncertainty is a seeded storm-block bootstrap (``storm_blocks``,
  ``block_bootstrap``): rain storms, padded one day before and a week after,
  are the independent units; quiet stretches fall into ISO-week blocks. 90%
  percentile CIs (one-sided α = 0.05), B = 2,000. A difference whose CI
  includes 0 is "no clear difference", and ``mde`` states what the window could
  have detected.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import norm, rankdata
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score

NAN = float("nan")
# Medium, High, Extreme edges on the shown whole percent (Chase, 2026-10-01). The home of the
# levels is shared/risk_levels.py; this module imports nothing from the repo, so callers pass
# that module's edges and this default only mirrors them.
EDGES = (0.205, 0.505, 0.805)


# ── plumbing ───────────────────────────────────────────────────────────────

def _f(x) -> np.ndarray:
    return np.asarray(x, dtype=float).ravel()


def _rows(*arrays) -> list[np.ndarray]:
    """Float arrays with every row that is NaN in any of them dropped (identical rows for paired scores)."""
    a = [_f(x) for x in arrays]
    n = len(a[0])
    if any(len(x) != n for x in a):
        raise ValueError(f"length mismatch: {[len(x) for x in a]}")
    keep = np.ones(n, bool)
    for x in a:
        keep &= np.isfinite(x)
    return [x[keep] for x in a]


def _div(num, den) -> float:
    return float(num) / float(den) if den else NAN


def _mean(x) -> float:
    return float(np.mean(x)) if len(x) else NAN


def _ref(ref, n: int) -> np.ndarray:
    """A per-row reference; a scalar is broadcast (a constant climatology)."""
    r = _f(ref)
    return np.full(n, r[0]) if r.size == 1 and n != 1 else r


def _scored_ref(y, p, ref) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(y, p, ref) on the rows where y and p are finite; a NaN reference on such a row raises.

    Dropping it instead would score the skill on fewer rows than the Brier
    score beside it, and nothing on the page would say so.
    """
    y, p = _f(y), _f(p)
    r = _ref(ref, len(y))
    if not (len(y) == len(p) == len(r)):
        raise ValueError(f"length mismatch: {len(y)}, {len(p)}, {len(r)}")
    keep = np.isfinite(y) & np.isfinite(p)
    if not np.isfinite(r[keep]).all():
        raise ValueError("ref is NaN on a scored row")
    return y[keep], p[keep], r[keep]


def clean(obj):
    """JSON-safe copy: numpy scalars → Python, NaN/inf → None (browsers reject a bare NaN)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())                 # a 0-d array becomes its scalar
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return float(obj) if math.isfinite(obj) else None
    return obj


# ── continuous (S1 rain amounts) ───────────────────────────────────────────

def wet_masks(fc, obs, wet: float = 0.1) -> dict[str, np.ndarray]:
    """The four S1 subsets (§3.1): all days, observed-wet, forecast-wet, either-wet (≥ ``wet`` inches).

    Either-wet is the primary: conditioning on observed rain alone rewards a
    model that never forecasts rain on dry days it got wrong.
    """
    f, o = _f(fc), _f(obs)
    fw, ow = f >= wet, o >= wet          # NaN compares False; continuous() drops those rows anyway
    return {"all": np.ones(len(f), bool), "obs_wet": ow, "fc_wet": fw, "either_wet": fw | ow}


def continuous(fc, obs, wet=None) -> dict:
    """JWGFVR continuous scores: ME, MAE, RMSE, Pearson r, multiplicative bias, n.

    ``wet``: None = every row; a boolean mask = those rows; a number = the
    either-wet subset at that threshold (the S1 primary, ``wet=0.1``).
    Multiplicative bias = mean forecast / mean observed (1 = unbiased in total).
    """
    f, o = _f(fc), _f(obs)
    keep = np.isfinite(f) & np.isfinite(o)
    if wet is not None:
        keep &= wet_masks(f, o, float(wet))["either_wet"] if np.ndim(wet) == 0 else np.asarray(wet, bool).ravel()
    f, o = f[keep], o[keep]
    e = f - o
    r = NAN
    if len(f) >= 2 and f.std() > 0 and o.std() > 0:
        r = float(np.corrcoef(f, o)[0, 1])
    return {"n": int(len(f)), "me": _mean(e), "mae": _mean(np.abs(e)),
            "rmse": math.sqrt(_mean(e * e)) if len(e) else NAN, "r": r,
            "mbias": _div(f.sum(), o.sum()) if len(f) else NAN}


# ── 2×2 contingency (threshold scores) ─────────────────────────────────────

def contingency_table(a, b, c, d) -> dict:
    """Every threshold score from the four counts (JWGFVR names, Jolliffe & Stephenson 2012).

    a hits, b false alarms, c misses, d correct negatives. A score whose
    denominator is zero is NaN, never 0 or 1. SEDI (Ferro & Stephenson 2011)
    is NaN when the hit rate or POFD is exactly 0 or 1 (its logs diverge).
    """
    a, b, c, d = (int(v) for v in (a, b, c, d))
    n = a + b + c + d
    pod, pofd = _div(a, a + c), _div(b, b + d)
    a_r = _div((a + b) * (a + c), n) if n else NAN             # hits expected by chance
    sedi = NAN
    if 0 < pod < 1 and 0 < pofd < 1:
        lf, lh, lf1, lh1 = math.log(pofd), math.log(pod), math.log(1 - pofd), math.log(1 - pod)
        sedi = _div(lf - lh - lf1 + lh1, lf + lh + lf1 + lh1)
    return {"a": a, "b": b, "c": c, "d": d, "n": n,
            "pod": pod,                                   # hit rate, sensitivity
            "far": _div(b, a + b),                        # share of alerts that were false
            "pofd": pofd,                                 # share of quiet days alerted
            "fbi": _div(a + b, a + c),                    # frequency bias
            "csi": _div(a, a + b + c),                    # threat score
            "ets": _div(a - a_r, a + b + c - a_r) if n else NAN,   # Gilbert skill score
            "pss": pod - pofd if (a + c) and (b + d) else NAN,     # Peirce / Hanssen–Kuipers
            "hss": _div(2 * (a * d - b * c), (a + c) * (c + d) + (a + b) * (b + d)),
            "sedi": sedi,
            "acc": _div(a + d, n),                        # proportion correct (S4 vs persistence)
            "spec": _div(d, b + d),                       # specificity = 1 − POFD
            "base_rate": _div(a + c, n)}


def contingency(fc_yes, obs_yes) -> dict:
    """The 2×2 table and its scores from two yes/no arrays (rows NaN in either are dropped)."""
    f, o = _rows(fc_yes, obs_yes)
    f, o = f > 0.5, o > 0.5
    return contingency_table((f & o).sum(), (f & ~o).sum(), (~f & o).sum(), (~f & ~o).sum())


def contingency_at(fc, obs, threshold: float, obs_threshold: float | None = None) -> dict:
    """Threshold scores with yes = value ≥ threshold, for the forecast and the observation.

    The observation uses the same threshold unless ``obs_threshold`` is given:
    right for S1 rain (forecast and gauge both ≥ 0.5"), and a 0/1 label passes
    through unchanged for any threshold in (0, 1] (risk edges on OUT, S2–S4).
    """
    f, o = _rows(fc, obs)
    t_o = threshold if obs_threshold is None else obs_threshold
    return contingency(f >= threshold, o >= t_o)


def grid_with(grid, *lines) -> tuple:
    """A line grid with extra lines added exactly (a served or per-unit line never falls between grid points)."""
    return tuple(sorted({float(x) for x in (*grid, *lines) if x is not None}))


# ── probability scores ─────────────────────────────────────────────────────

def brier(y, p) -> float:
    """Mean squared error of a probability against a 0/1 outcome; lower is better."""
    y, p = _rows(y, p)
    return _mean((p - y) ** 2)


def bss(y, p, ref) -> float:
    """Brier skill score against a per-row reference probability (a scalar is broadcast).

    With a per-row stratified ``ref`` this is already the Hamill–Juras pooled
    skill (1 − Σ(p−y)² / Σ(ref−y)²). NaN when the reference is perfect. A NaN
    reference on a row with a forecast and an outcome raises (``_scored_ref``).
    """
    y, p, r = _scored_ref(y, p, ref)
    return 1.0 - _div(((p - y) ** 2).sum(), ((r - y) ** 2).sum())


def _unit_month(keys) -> tuple[np.ndarray, np.ndarray]:
    """(units, months 1–12) from a tuple of two columns or a DataFrame/sequence of (unit, month) rows.

    The month column may be month numbers or dates (the month is taken).
    """
    if isinstance(keys, tuple) and len(keys) == 2:
        units, months = keys
    else:
        df = pd.DataFrame(keys).iloc[:, :2]
        units, months = df.iloc[:, 0], df.iloc[:, 1]
    u = np.asarray(units, dtype=object).ravel()
    m = np.asarray(months).ravel()
    if not np.issubdtype(m.dtype, np.number):
        m = pd.to_datetime(pd.Series(m)).dt.month.to_numpy()
    m = np.asarray(m, dtype=int)
    if len(m) and (m.min() < 1 or m.max() > 12):
        raise ValueError("months must be 1–12")
    if len(u) != len(m):
        raise ValueError("unit and month columns differ in length")
    return u, m


def climatology_ref(y_train, keys_train, keys_score, smooth: int = 1, shrink: float = 0.0) -> np.ndarray:
    """The BSS reference: a training-fold base rate per unit × calendar month, smoothed over ±``smooth`` months.

    Counts are pooled over the circular window (December's window is Nov–Jan),
    so a month is weighted by its days. A unit-month window with no training
    rows falls back to the unit's rate, an unseen unit to the overall rate.
    ``shrink`` adds that many pseudo-days at the unit's rate (0 = none, the
    design default). For post-training and prospective days the caller passes
    every T2 season as ``y_train`` (§5.3); the scored window is never its own
    reference.
    """
    if not 0 <= smooth <= 5:
        raise ValueError("smooth must be 0–5 months (a wider window counts a month twice)")
    y = _f(y_train)
    u_tr, m_tr = _unit_month(keys_train)
    u_sc, m_sc = _unit_month(keys_score)
    if len(u_tr) != len(y):
        raise ValueError("keys_train and y_train differ in length")
    ok = np.isfinite(y)
    y, u_tr, m_tr = y[ok], u_tr[ok], m_tr[ok]
    overall = _mean(y)
    out = np.full(len(u_sc), overall)
    offsets = np.arange(-smooth, smooth + 1)
    for unit in pd.unique(u_sc):
        rows = u_tr == unit
        at = u_sc == unit
        if not rows.any():
            continue
        n_m = np.bincount(m_tr[rows] - 1, minlength=12).astype(float)
        p_m = np.bincount(m_tr[rows] - 1, weights=y[rows], minlength=12)
        unit_rate = p_m.sum() / n_m.sum()
        win = (np.arange(12)[:, None] + offsets[None, :]) % 12      # month index → its window
        n_w, p_w = n_m[win].sum(axis=1), p_m[win].sum(axis=1)
        rate = np.full(12, unit_rate)
        has = n_w > 0
        rate[has] = (p_w[has] + shrink * unit_rate) / (n_w[has] + shrink)
        out[at] = rate[m_sc[at] - 1]
    return out


def sample_ref(y, strata=None) -> np.ndarray:
    """The window's own base rate per row (overall or per stratum): the old reports' reference, shown for comparison only."""
    y = _f(y)
    if strata is None:
        return np.full(len(y), np.nanmean(y) if np.isfinite(y).any() else NAN)
    s = pd.Series(y).groupby(np.asarray(strata, dtype=object)).transform("mean")
    return s.to_numpy(dtype=float)


def pool_bss(parts) -> float:
    """Hamill–Juras pooled skill from stratum sums: parts = iterable of (n, bs, bs_ref)."""
    num = den = 0.0
    for n, bs_, bs_r in parts:
        if n and np.isfinite(bs_) and np.isfinite(bs_r):
            num += n * bs_
            den += n * bs_r
    return 1.0 - _div(num, den)


def pooled_bss(y, p, ref, strata) -> dict:
    """Per-stratum Brier and BSS, and the pooled skill computed from the stratum sums (Hamill & Juras 2006).

    Pooling against one base rate across strata with different climatologies
    credits a forecast for knowing which stratum it is in; pooling the sums
    against each stratum's own reference does not.
    """
    s = np.asarray(strata, dtype=object).ravel()
    if len(s) != len(_f(y)):
        raise ValueError("strata and y differ in length")
    s = s[np.isfinite(_f(y)) & np.isfinite(_f(p))]
    y, p, r = _scored_ref(y, p, ref)
    by = {}
    for k in pd.unique(s):
        m = s == k
        b_, br = _mean((p[m] - y[m]) ** 2), _mean((r[m] - y[m]) ** 2)
        by[k.item() if hasattr(k, "item") else k] = {"n": int(m.sum()), "bs": b_, "bs_ref": br, "bss": 1.0 - _div(b_, br)}
    return {"n": int(len(y)), "bss": pool_bss((v["n"], v["bs"], v["bs_ref"]) for v in by.values()), "by": by}


def log_score(y, p, eps: float = 0.001) -> tuple[float, int]:
    """Mean ignorance in bits (−log2 of the probability given to what happened; lower is better), and how many p were clipped.

    p is clipped to [eps, 1 − eps] so the exact 0s and 1s of an injection or
    a quiet basin stay finite; the clip count is reported beside the score.
    """
    y, p = _rows(y, p)
    clipped = int(((p < eps) | (p > 1 - eps)).sum())
    pc = np.clip(p, eps, 1 - eps)
    return _mean(-np.log2(np.where(y > 0.5, pc, 1 - pc))), clipped


def corp(y, p) -> dict:
    """CORP reliability (Dimitriadis, Gneiting & Jordan 2021): the isotonic recalibration and BS = MCB − DSC + UNC.

    The PAV fit (increasing, ties pooled, clipped outside the observed range)
    is the reliability curve. MCB = BS − BS(recalibrated) is miscalibration,
    DSC = UNC − BS(recalibrated) is discrimination, UNC = BS of the window's
    base rate. The identity is exact, not approximate as in binned diagrams.
    ``curve`` is the knot list [[forecast, recalibrated], ...] of the fit.
    """
    y, p = _rows(y, p)
    if not len(y):
        return {"n": 0, "bs": NAN, "mcb": NAN, "dsc": NAN, "unc": NAN, "curve": []}
    iso = IsotonicRegression(increasing=True, out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(p, y)
    cal = iso.predict(p)
    bs_, bs_cal, unc = _mean((p - y) ** 2), _mean((cal - y) ** 2), _mean((y.mean() - y) ** 2)
    curve = [[float(x), float(v)] for x, v in zip(iso.X_thresholds_, iso.y_thresholds_)]
    return {"n": int(len(y)), "bs": bs_, "mcb": bs_ - bs_cal, "dsc": unc - bs_cal, "unc": unc, "curve": curve}


def corp_bands(p, xs=None, n: int = 200, seed: int = 0, level: float = 0.9) -> dict:
    """Consistency bands for a CORP curve: where the recalibrated curve of a perfectly calibrated forecast falls.

    Draw y* ~ Bernoulli(p), refit the isotonic curve, evaluate at ``xs``;
    a curve outside the band is miscalibrated beyond sampling noise.
    """
    p = _f(p)
    p = p[np.isfinite(p)]
    xs = np.linspace(0, 1, 51) if xs is None else _f(xs)
    rng = np.random.default_rng(seed)
    sims = np.empty((n, len(xs)))
    for i in range(n):
        ys = (rng.random(len(p)) < p).astype(float)
        sims[i] = IsotonicRegression(increasing=True, out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(p, ys).predict(xs)
    q = 100 * (1 - level) / 2
    return {"x": xs.tolist(), "lo": np.percentile(sims, q, axis=0).tolist(), "hi": np.percentile(sims, 100 - q, axis=0).tolist()}


def roc_auc(y, p) -> float:
    """ROC area by ranks (Mann–Whitney; a tie counts one half). NaN with one class only."""
    y, p = _rows(y, p)
    pos = y > 0.5
    n1, n0 = int(pos.sum()), int((~pos).sum())
    if not n1 or not n0:
        return NAN
    return float((rankdata(p)[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def pr_auc(y, p) -> tuple[float, float]:
    """(average precision, prevalence). AP is read against the prevalence, its no-skill value (lift = AP / prevalence)."""
    y, p = _rows(y, p)
    pos = y > 0.5
    prev = _mean(pos)
    if not pos.any() or pos.all():
        return NAN, prev
    return float(average_precision_score(pos, p)), prev


def murphy(y, p, thetas=None) -> np.ndarray:
    """Mean elementary scores over thresholds θ (Ehm, Gneiting, Jordan & Krüger 2016): the Murphy diagram.

    At θ an alert is p ≥ θ; a miss costs 1 − θ and a false alarm θ. Every
    cost ratio is one θ, so a forecast lower at every θ is better for every
    user, and BS = 2∫ S_θ dθ. Reported only, never used to pick a line.
    """
    y, p = _rows(y, p)
    th = np.arange(1, 100) / 100 if thetas is None else _f(thetas)
    if not len(y):
        return np.full(len(th), NAN)
    alert = p[:, None] >= th[None, :]
    ev = (y > 0.5)[:, None]
    s = np.where(ev & ~alert, 1 - th[None, :], 0.0) + np.where(~ev & alert, th[None, :], 0.0)
    return s.mean(axis=0)


def rev_curve(y, p, alphas=None, thresholds=(0.5,), base_rate: float | None = None) -> dict:
    """Relative economic value V(α) (Richardson 2000) for alerting at each threshold, plus the envelope over thresholds.

    α = cost of acting / loss avoided. V = 1 is a perfect forecast, 0 is
    climatology (always or never act, whichever is cheaper), negative is worse
    than climatology. At α = base rate, V = H − F = PSS. Reported, never used
    to choose a line (Chase, 2026-10-01).
    """
    y, p = _rows(y, p)
    al = np.arange(1, 100) / 100 if alphas is None else _f(alphas)
    s = _mean(y > 0.5) if base_rate is None else float(base_rate)
    value = []
    for t in thresholds:
        ct = contingency(p >= t, y)
        h, f = ct["pod"], ct["pofd"]
        num = np.minimum(al, s) - f * al * (1 - s) + h * s * (1 - al) - s
        den = np.minimum(al, s) - s * al
        with np.errstate(divide="ignore", invalid="ignore"):
            value.append(np.where(den != 0, num / np.where(den != 0, den, 1.0), NAN))
    v = np.array(value) if value else np.empty((0, len(al)))
    env = np.full(len(al), NAN)
    if len(v) and np.isfinite(v).any(axis=0).any():
        ok = np.isfinite(v).any(axis=0)
        env[ok] = np.nanmax(v[:, ok], axis=0)
    return {"alphas": al.tolist(), "thresholds": [float(t) for t in thresholds], "base_rate": s,
            "value": v.tolist(), "envelope": env.tolist()}


# ── blocks and the bootstrap ───────────────────────────────────────────────

def storm_spans(dates, rain, wet: float = 0.1, gap: int = 2) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Rain storms as (first wet day, last wet day): wet days (≥ ``wet``) at most ``gap`` dry days apart are one storm.

    Built from RAIN, not from the ledger, so rainy days with no overflow are
    storms too (design Part B 10). A NaN rain day counts as dry; with several
    rows per date the day's wettest value is used.
    """
    d = pd.to_datetime(pd.Series(np.asarray(dates).ravel())).dt.normalize()
    daily = pd.Series(_f(rain)).groupby(d.to_numpy()).max().sort_index()
    spans: list[list[pd.Timestamp]] = []
    for day in daily.index[daily.to_numpy() >= wet]:
        if spans and (day - spans[-1][1]).days <= gap + 1:
            spans[-1][1] = day
        else:
            spans.append([day, day])
    return [(s, e) for s, e in spans]


def storm_blocks(dates, rain, wet: float = 0.1, gap: int = 2, pad: tuple[int, int] = (1, 7)) -> np.ndarray:
    """Bootstrap block id per row: padded storms are blocks; the quiet days between fall into ISO-week blocks.

    A storm block is [first wet day − pad[0], last wet day + pad[1]] (default
    one day before, a week after, §5.4), overlapping blocks merged: a storm
    and its tail are one independent unit. A quiet block is the days of one
    ISO week between the same two storms, so every block is one stretch of
    time. Ids run 0, 1, … in time order; rows sharing a date share a block.
    """
    d = pd.to_datetime(pd.Series(np.asarray(dates).ravel())).dt.normalize()
    if len(d) != len(_f(rain)):
        raise ValueError("dates and rain differ in length")
    windows: list[list[pd.Timestamp]] = []
    for s, e in storm_spans(d, rain, wet, gap):
        lo, hi = s - pd.Timedelta(days=pad[0]), e + pd.Timedelta(days=pad[1])
        if windows and lo <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], hi)
        else:
            windows.append([lo, hi])
    days = pd.DatetimeIndex(d.unique()).sort_values()
    key_of, ids, w = {}, {}, 0
    for day in days:
        while w < len(windows) and windows[w][1] < day:
            w += 1                                   # w = storms wholly before this day
        if w < len(windows) and windows[w][0] <= day:
            key = ("storm", w)
        else:
            iso = day.isocalendar()
            key = ("quiet", iso[0], iso[1], w)       # same ISO week, same gap between storms
        key_of[day] = ids.setdefault(key, len(ids))
    return d.map(key_of).to_numpy(dtype=int)


def _block_index(blocks, m: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(row order grouped by block, each block's start in that order, each block's size)."""
    b = np.arange(m) if blocks is None else np.asarray(blocks).ravel()
    if len(b) != m:
        raise ValueError("blocks and arrays differ in length")
    if not m:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0, int)
    _, inv = np.unique(b, return_inverse=True)
    sizes = np.bincount(inv)
    return np.argsort(inv, kind="stable"), np.concatenate([[0], np.cumsum(sizes)[:-1]]), sizes


def _replicates(stat_fn, arrays, blocks, n: int, seed: int) -> np.ndarray:
    """stat_fn on n block resamples (each draws as many blocks as observed, with replacement). Seeded: same seed, same draws."""
    arrays = [np.asarray(a) for a in arrays]
    order, starts, sizes = _block_index(blocks, len(arrays[0]))
    k = len(sizes)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        pick = rng.integers(0, k, size=k)
        ln, st = sizes[pick], starts[pick]
        idx = order[np.repeat(st - np.cumsum(ln) + ln, ln) + np.arange(ln.sum())]   # the picked blocks' rows, concatenated
        out.append(np.asarray(stat_fn(*[a[idx] for a in arrays]), dtype=float))
    return np.array(out)


def _ci(reps: np.ndarray, level: float):
    q = 100 * (1 - level) / 2
    with np.errstate(all="ignore"):
        ok = np.isfinite(reps)
        if reps.ndim == 1:
            return (np.percentile(reps[ok], [q, 100 - q]) if ok.any() else np.array([NAN, NAN]))
        return np.array([np.percentile(c[np.isfinite(c)], [q, 100 - q]) if np.isfinite(c).any() else [NAN, NAN]
                         for c in reps.T]).T


def block_bootstrap(stat_fn, arrays, blocks, n: int = 2000, seed: int = 0, level: float = 0.9):
    """(estimate, lo, hi): stat_fn on the data and its block-bootstrap percentile interval.

    ``arrays`` are row-aligned and passed to stat_fn resampled together;
    ``blocks`` gives each row's block (None = every row its own block). A
    vector-valued stat_fn gets vector bounds. Fewer than two blocks → NaN
    bounds (one block resampled is itself).
    """
    est = np.asarray(stat_fn(*[np.asarray(a) for a in arrays]), dtype=float)
    k = len(_block_index(blocks, len(np.asarray(arrays[0])))[2])
    if k < 2:
        nan = np.full(est.shape, NAN)
        return (float(est), NAN, NAN) if est.ndim == 0 else (est, nan, nan.copy())
    lo, hi = _ci(_replicates(stat_fn, arrays, blocks, n, seed), level)
    return (float(est), float(lo), float(hi)) if est.ndim == 0 else (est, lo, hi)


def mde(se: float, level: float = 0.9, power: float = 0.8) -> float:
    """Minimum detectable effect for a paired difference: (z_{1−α} + z_power) × SE, one-sided α = (1 − level)/2.

    At the 90% convention and 80% power this is ≈ 2.49 × SE (§5.4): a true
    difference smaller than this will usually read "no clear difference".
    """
    return float((norm.ppf(1 - (1 - level) / 2) + norm.ppf(power)) * se) if np.isfinite(se) else NAN


def paired_se(y, p_a, p_b, blocks=None) -> float:
    """Closed-form SE of the paired Brier difference from the observed variance of block sums (cluster-robust).

    For power planning without a bootstrap: SE² = K/(K−1) · Σ_k (D_k − n_k·Δ)² / N².
    """
    y, a, b = _f(y), _f(p_a), _f(p_b)
    bl = np.arange(len(y)) if blocks is None else np.asarray(blocks, dtype=object).ravel()
    keep = np.isfinite(y) & np.isfinite(a) & np.isfinite(b)
    d = (a[keep] - y[keep]) ** 2 - (b[keep] - y[keep]) ** 2
    g = pd.Series(d).groupby(pd.Series(bl[keep]).to_numpy())
    sums, counts = g.sum().to_numpy(), g.size().to_numpy()
    k, n = len(sums), len(d)
    if k < 2:
        return NAN
    delta = d.mean()
    return math.sqrt(k / (k - 1) * ((sums - counts * delta) ** 2).sum()) / n


def _metric(metric, eps):
    if callable(metric):
        return metric
    if metric == "brier":
        return lambda y, p: _mean((p - y) ** 2)
    if metric == "log":
        return lambda y, p: log_score(y, p, eps)[0]
    raise ValueError(f"unknown metric {metric!r}")


def verdict(lo: float, hi: float) -> str:
    """Words for a loss difference a − b: "better" only when the whole CI is below 0 (§5.8)."""
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return "not scored"
    return "better" if hi < 0 else "worse" if lo > 0 else "no clear difference"


def paired_delta(y, p_a, p_b, blocks, metric="brier", n: int = 2000, seed: int = 0, level: float = 0.9,
                 eps: float = 0.001) -> dict:
    """Paired difference Δ = metric(a) − metric(b) on identical rows, with a block-bootstrap CI.

    Arm b is the reference (the incumbent); Δ < 0 means a is better, so a
    callable ``metric`` must be a loss (lower is better: pass 1 − ETS, not
    ETS). Both arms are scored on the same resample, so storm-to-storm
    variation cancels. Rows NaN in either arm are dropped from both.
    ``blocks`` has no default: None (every row its own block) must be asked
    for, because row resampling hides storm-shared errors and narrows the CI.
    Returns the scores, Δ, the 90% CI, the bootstrap SE, P(Δ < 0), the MDE
    (absolute and as a share of b's score) and the verdict words.
    """
    y, a, b = _f(y), _f(p_a), _f(p_b)
    bl = np.arange(len(y)) if blocks is None else np.asarray(blocks).ravel()
    keep = np.isfinite(y) & np.isfinite(a) & np.isfinite(b)
    y, a, b, bl = y[keep], a[keep], b[keep], bl[keep]
    fn = _metric(metric, eps)
    stat = lambda y_, a_, b_: fn(y_, a_) - fn(y_, b_)  # noqa: E731
    m_a, m_b = fn(y, a), fn(y, b)
    k = len(np.unique(bl)) if len(bl) else 0
    out = {"metric": metric if isinstance(metric, str) else getattr(metric, "__name__", "custom"),
           "n": int(len(y)), "n_blocks": int(k), "a": m_a, "b": m_b, "delta": m_a - m_b,
           "lo": NAN, "hi": NAN, "se": NAN, "p_neg": NAN, "mde": NAN, "mde_pct": NAN, "level": level}
    if k >= 2:
        reps = _replicates(stat, (y, a, b), bl, n, seed)
        lo, hi = _ci(reps, level)
        ok = reps[np.isfinite(reps)]
        se = float(ok.std(ddof=1)) if len(ok) > 1 else NAN
        out.update(lo=float(lo), hi=float(hi), se=se, p_neg=_mean(ok < 0), mde=mde(se, level),
                   mde_pct=_div(mde(se, level), m_b))
    out["verdict"] = verdict(out["lo"], out["hi"])
    return out


def noninferior(d: dict, margin: float = 0.05) -> bool:
    """Non-inferiority of arm a to arm b: the CI's upper bound on Δ is below margin × b's score (§5.5, +5%)."""
    return bool(np.isfinite(d["hi"]) and d["hi"] < margin * d["b"])


# ── one unit × window ──────────────────────────────────────────────────────

def scores_bundle(y, p, ref, blocks, edges=EDGES, n: int = 2000, seed: int = 0, level: float = 0.9,
                  eps: float = 0.001) -> dict:
    """Everything a stage report needs for one unit × window, JSON-safe (NaN → None).

    Rows with a NaN outcome or forecast are dropped; ``ref`` (per-row
    reference, e.g. ``climatology_ref``) must be finite on the rows kept.
    ``ref`` and ``blocks`` have no defaults, so neither can be forgotten: an
    explicit None leaves BSS unscored / resamples rows. Bootstrap CIs on BS
    and BSS share one resample. Threshold scores are at each risk-level edge
    (keys "0.205", …).
    """
    y_, p_ = _f(y), _f(p)
    r_ = np.full(len(y_), NAN) if ref is None else _ref(ref, len(y_))
    bl = np.arange(len(y_)) if blocks is None else np.asarray(blocks).ravel()
    if not (len(p_) == len(r_) == len(bl) == len(y_)):
        raise ValueError("y, p, ref and blocks differ in length")
    keep = np.isfinite(y_) & np.isfinite(p_)
    y_, p_, r_, bl = y_[keep], p_[keep], r_[keep], bl[keep]
    if ref is not None and not np.isfinite(r_).all():
        raise ValueError("ref is NaN on a scored row")
    pos = y_ > 0.5
    k = len(np.unique(bl)) if len(bl) else 0
    bs_, bs_ref = _mean((p_ - y_) ** 2), (_mean((r_ - y_) ** 2) if ref is not None else NAN)
    logs, clipped = log_score(y_, p_, eps)
    ap, prev = pr_auc(y_, p_)
    c = corp(y_, p_)

    def stat(yy, pp, rr):
        b1 = _mean((pp - yy) ** 2)
        return [b1, 1.0 - _div(((pp - yy) ** 2).sum(), ((rr - yy) ** 2).sum()) if ref is not None else NAN]

    ci_bs = ci_bss = [NAN, NAN]
    if k >= 2:
        lo, hi = _ci(_replicates(stat, (y_, p_, r_), bl, n, seed), level)
        ci_bs, ci_bss = [lo[0], hi[0]], [lo[1], hi[1]]
    return clean({
        "n": int(len(y_)), "n_pos": int(pos.sum()), "n_blocks": int(k),
        "n_pos_blocks": int(len(np.unique(bl[pos]))) if pos.any() else 0,
        "prev": prev, "bs": bs_, "bs_ref": bs_ref,
        "bss": 1.0 - _div(bs_, bs_ref) if ref is not None else NAN,
        "logs": logs, "clipped": clipped,
        "roc": roc_auc(y_, p_), "pr": ap, "lift": _div(ap, prev) if np.isfinite(ap) else NAN,
        "corp": {"mcb": c["mcb"], "dsc": c["dsc"], "unc": c["unc"], "curve": c["curve"]},
        "ci": {"level": level, "bs": ci_bs, "bss": ci_bss},
        "contingency": {f"{e:g}": contingency_at(p_, y_, e) for e in edges},
    })
