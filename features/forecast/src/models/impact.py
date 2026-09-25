"""Stage 2 — discharge → beach-impact composition, shared by training
(scorecard hindcasts) and serving (live_dashboard) so the two can never drift.

The stage-1 models predict P(discharge TODAY) per basin. Beaches stay
contaminated for days after a discharge, so the displayed risk for day D
composes the discharge probabilities of D and the prior week with how often
the group's beaches were still elevated k days after a discharge of that
size (the empirical impact table fit by train_v4.fit_impact_table):

    risk(D) = 1 - ∏_{k=0..7} (1 - p_discharge(D-k) · x(k, size(D-k)))

with x(0, ·) ≡ 1: a discharge day is itself a "stay out" day (the alert
system treats an active CSO exactly like a posting), so only k ≥ 1 uses the
bacteria-measured decay.

x(k, size) is the discharge-ATTRIBUTABLE elevation probability: the impact
table's P(elevated) with the dry-weather background removed via the
independent-OR identity p = 1-(1-baseline)(1-x) → x = (p-baseline)/(1-baseline).
"""
from __future__ import annotations

import copy

BUCKET_ORDER = ["0", "1", "2", "3", "4-5", "6-7"]


def bucket_index(days_since: int) -> int:
    return min(days_since if days_since <= 3 else (4 if days_since <= 5 else 5), len(BUCKET_ORDER) - 1)


def pava_nonincreasing(values: list, weights: list) -> list:
    """Weighted pool-adjacent-violators fit of a non-increasing sequence."""
    blocks = [[v, w] for v, w in zip(values, weights)]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i + 1][0] - 1e-12:  # violation
            v1, w1 = blocks[i]
            v2, w2 = blocks[i + 1]
            blocks[i] = [(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out, bi, consumed = [], 0, 0
    for v, w in zip(values, weights):
        out.append(blocks[bi][0])
        consumed += w
        if consumed >= blocks[bi][1] - 1e-9:
            bi, consumed = bi + 1, 0
    return out


def smooth_table(table: dict) -> dict:
    """Return a copy of the raw impact table with each group's small/large
    decay curve made non-increasing over days-since-discharge (weighted PAVA,
    weights = bucket sample counts). Raw small-n buckets (down to n=3) are
    noisy enough to read day-3 above day-1; contamination physically decays."""
    table = copy.deepcopy(table)
    for data in table.values():
        buckets = data.get("buckets", {})
        for size in ("small", "large"):
            keys = [f"d{k}_{size}" for k in BUCKET_ORDER if f"d{k}_{size}" in buckets]
            if len(keys) < 2:
                continue
            vals = [buckets[k]["p_elevated"] for k in keys]
            wts = [max(buckets[k].get("n", 1), 1) for k in keys]
            for k, v in zip(keys, pava_nonincreasing(vals, wts)):
                buckets[k]["p_elevated"] = round(v, 3)
    return table


def impact_fraction(table: dict, group: str, days_since: int, volume_mg: float) -> float:
    """x(k, size): attributable P(group's beaches elevated) k days after a
    discharge of `volume_mg`, blending the small/large curves by size (weight
    0.5 at the group's median event). A missing bucket takes the nearest
    EARLIER bucket's value — the curve is non-increasing, so this errs high,
    the safe direction."""
    gt = table.get(group, {})
    buckets = gt.get("buckets", {})
    if not buckets:
        return 1.0 if days_since == 0 else 0.0  # degraded: same-day only
    baseline = buckets.get("baseline_no_recent_discharge", {}).get("p_elevated", 0.0)
    bi = bucket_index(days_since)

    def attributable(size):
        for j in range(bi, -1, -1):
            p = buckets.get(f"d{BUCKET_ORDER[j]}_{size}", {}).get("p_elevated")
            if p is not None:
                return max(0.0, (p - baseline) / (1 - baseline)) if baseline < 1 else 0.0
        return None

    x_small, x_large = attributable("small"), attributable("large")
    if x_small is None and x_large is None:
        return 0.0
    if x_small is None:
        return x_large
    if x_large is None:
        return x_small
    median = gt.get("median_event_volume_mg", 1.0) or 1.0
    w_large = volume_mg / (volume_mg + median)
    return w_large * x_large + (1 - w_large) * x_small


def compose(table: dict, groups_by_basin: dict, day_probs: list, day_volumes: list, idx: int,
            day_dates: list = None, observed: dict = None, split=None) -> tuple:
    """Composed beach-impact risk for day `idx` of a daily table.

    day_probs[j]   {basin_key: P(discharge on day j)}
    day_volumes[j] {basin_key: expected discharge volume (MG) on day j}
    observed       {date: {basin_key}} — days a discharge was OBSERVED there;
                   certainty (p=1) replaces the prediction for those days.
    split          optional callable (group, p_basin, volume) → p_group: a
                   stage 2 variant's way of turning the basin probability into
                   the group's (src/models/stage2.py). None = the served v4
                   composition, byte-identical to before this hook existed.

    Returns (per_basin, per_group): per_basin[basin_key] = worst group in the
    basin, plus "citywide" = worst basin.
    """
    observed = observed or {}
    per_basin, per_group = {}, {}
    for basin_key, groups in groups_by_basin.items():
        vals = {}
        for group in groups:
            no_impact = 1.0
            for k in range(0, 8):
                j = idx - k
                if j < 0 or j >= len(day_probs):
                    continue
                p = day_probs[j].get(basin_key)
                if day_dates is not None and basin_key in observed.get(day_dates[j], ()):
                    p = 1.0
                if not p:
                    continue
                vol = day_volumes[j].get(basin_key, 0.0) if j < len(day_volumes) else 0.0
                if split is not None:
                    p = split(group, p, vol)
                x = 1.0 if k == 0 else impact_fraction(table, group, k, vol)
                no_impact *= 1.0 - p * x
            vals[group] = round(1.0 - no_impact, 3)
        per_basin[basin_key] = max(vals.values()) if vals else 0.0
        per_group.update(vals)
    per_basin["citywide"] = max(per_basin.values()) if per_basin else 0.0
    return per_basin, per_group
