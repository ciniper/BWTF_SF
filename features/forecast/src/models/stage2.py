"""Stage 2 variants — named alternatives to the served composition that a
candidate set can carry, selectable in the Model check next to its stage 1.
Nothing here changes what is served: the live forecast composes with the served gb_v1
impact table and no split until a variant is promoted.

    v1                 group risk composes the BASIN discharge probability
                       directly (impact.compose with split=None) — what the served bundle
                       ships. Ocean Beach and Baker-China get the same p.
    v2 (outfall split) p_group(D−k) = p_basin(D−k) · g_group(size(D−k)) where
                       g_group = share of the basin's CIWQS discharge days (by
                       size class) on which at least one outfall that posts
                       the group's beaches discharged; the impact table is
                       refit on the group-attributed discharge days only.
                       Identity for a group whose outfalls are the basin's
                       (Mission Creek, Southeast); Westside and North Shore
                       each have two groups fed by disjoint outfall sets.
    v3 (shares from    the same transformation as v2 with the shares fitted
        the water)     from the samples: do the basin's discharges that did NOT
                       come through the group's outfalls foul it anyway? f =
                       the group's over-standard rate on pure non-attributed
                       tails, above its dry-weather rate, relative to the same
                       after attributed discharges; share = map + (1 − map)·f,
                       blended toward the map share where such tails are few.
                       Impact table copied from v2 — v3 changes the shares only.

A fitted variant is a JSON spec (data/models/stage2/<variant>.json), copied
into any candidate directory that uses it (stage2.json) so the candidate is
self-contained. Everything numeric comes from CIWQS per-outfall rows and the
shared/outfalls.py registry — nothing is retyped.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from groups import SITE_GROUPS  # noqa: E402
from shared.outfalls import OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402

SERVE_DIR = HERE.parents[1] / "data" / "models"
VARIANTS_DIR = SERVE_DIR / "stage2"
VARIANTS = ("v1", "v2", "v3")
KINDS = {"v1": "basin composition", "v2": "outfall split", "v3": "outfall split, shares from the water"}


def outfalls_posting(group: str) -> list[str]:
    """Registry: the outfalls whose postings cover any of the group's stations."""
    sfpuc = {STATIONS[s].sfpuc_id for s in SITE_GROUPS[group][1]}
    return sorted(o.id for o in OUTFALLS.values() if set(o.stations) & sfpuc)


def fit_outfall_split(frames: dict, chosen: dict, heads: dict, impact_raw: dict, events: pd.DataFrame) -> dict:
    """The share of each basin's discharge days on which the group's own
    outfalls took part, by size class. Only CIWQS-labelled days carry outfall
    detail, so 2016-17 feed-archive days are left out of the shares (they are
    kept as events for the impact refit, unattributed, via group_event_days)."""
    import train_v4 as T
    shares, group_outfalls, medians = {}, {}, {}
    for group, (basin, _stations) in SITE_GROUPS.items():
        outs = outfalls_posting(group)
        group_outfalls[group] = outs
        med = float(impact_raw[group]["median_event_volume_mg"])
        medians[group] = med
        df = frames[chosen[basin]]
        sub = df[(df[f"{basin}_covered"] == 1) & (df[f"{basin}_csd"] == 1) & (df[f"{basin}_label_source"] != "poobot")].copy()
        if basin in heads:
            pred = T.predicted_volume(heads[basin], sub)
            sub["vol"] = np.where(sub[f"{basin}_volume_known"] == 1, sub[f"{basin}_volume_mg"], pred)
        else:
            sub["vol"] = sub[f"{basin}_volume_mg"]
        bev = events[events["app_basin"] == basin]
        hit_days = set(pd.to_datetime(bev[bev["outfall_id"].isin(outs)]["event_date"]).dt.normalize())
        hit = sub["date"].isin(hit_days).to_numpy()
        large = (sub["vol"].to_numpy() >= med)
        row = {"n_basin_days": int(len(sub))}
        for size, mask in (("large", large), ("small", ~large), ("all", np.ones(len(sub), bool))):
            n = int(mask.sum())
            row[size] = {"p": round(float(hit[mask].mean()), 4) if n else None, "n": n}
        shares[group] = row
    return {"variant": "v2", "kind": "outfall_split", "fitted_at": datetime.now().isoformat(timespec="seconds"),
            "group_outfalls": group_outfalls, "shares": shares, "median_event_volume_mg": medians,
            "definition": "p_group(D−k) = p_basin(D−k) · [w·g_large + (1−w)·g_small], w = V/(V+median), "
                          "g_size = share of the basin's CIWQS discharge days of that size on which ≥1 of the group's outfalls discharged"}


# ── v3: shares from the water ────────────────────────────────────────────────
WATER_TAIL_DAYS = 3        # tail days 0–3 after a discharge carry the signal and most of the samples
WATER_PRIOR_WEIGHT = 10    # the v2 map share counts as this many tail sample-days in the blend
WATER_MIN_SIGNAL = 0.05    # the attributed tail rate must clear the dry-weather rate by this much to be divided by


def tail_rows(frames: dict, chosen: dict, heads: dict, samples: pd.DataFrame, group: str,
              event_days: set | None, median_mg: float) -> pd.DataFrame:
    """One row per sampled day at the group's stations inside the basin's
    covered window: elevated (any station over standard), days_since the last
    discharge in ``event_days`` (None = the basin's every discharge day) within
    7 days (None when there was none), large (that discharge's volume, known or
    predicted by the volume head, ≥ ``median_mg``). The same joins
    train_v4.fit_impact_table makes, kept as rows so rates can be pooled."""
    import train_v4 as T
    basin, stations = SITE_GROUPS[group]
    df = frames[chosen[basin]]
    sub = df[df[f"{basin}_covered"] == 1]
    ev = sub[sub[f"{basin}_csd"] == 1].copy()
    if event_days is not None:
        ev = ev[ev["date"].isin(event_days)]
    if basin in heads and len(ev):
        pred = T.predicted_volume(heads[basin], ev)
        ev["vol"] = np.where(ev[f"{basin}_volume_known"] == 1, ev[f"{basin}_volume_mg"], pred)
    else:
        ev["vol"] = ev[f"{basin}_volume_mg"]
    events = ev.set_index("date")["vol"]
    smp = samples[samples["station"].isin(stations)]
    daily = smp.groupby("sample_date").agg(elevated=("exceeds_standard", "max")).reset_index()
    daily = daily[daily["sample_date"].isin(sub["date"])]
    rows = []
    for _, r in daily.iterrows():
        d = r["sample_date"]
        past = events[(events.index <= d) & (events.index >= d - pd.Timedelta(days=7))]
        if past.empty:
            rows.append({"date": d, "elevated": bool(r["elevated"]), "days_since": None, "large": None})
        else:
            last = past.index.max()
            rows.append({"date": d, "elevated": bool(r["elevated"]), "days_since": (d - last).days,
                         "large": bool(float(past.loc[last]) >= median_mg)})
    return pd.DataFrame(rows, columns=["date", "elevated", "days_since", "large"])


def water_share(r_att, r_pure, n_pure: int, baseline: float, g_map, identity: bool) -> tuple:
    """(blended share, water-only share, f). The question the water can answer:
    do the basin's discharges that did NOT come through the group's own
    outfalls foul the group anyway? f = the group's over-standard rate on
    PURE non-attributed tails (a non-attributed discharge in the last 3 days
    and no attributed one in the last 7), above the dry-weather rate, as a
    fraction of the same after attributed discharges. The water share is then
    map + (1 − map)·f: the attributed days plus the effective fraction of the
    others. Blended toward the map share with WATER_PRIOR_WEIGHT sample-days
    of weight; identity groups (map share 1 at every size) stay 1."""
    if identity:
        return 1.0, 1.0, None
    f = None
    if r_att is not None and r_pure is not None and n_pure >= 3 and (r_att - baseline) > WATER_MIN_SIGNAL:
        f = min(1.0, max(0.0, (r_pure - baseline) / (r_att - baseline)))
    if f is None or g_map is None:
        return g_map, None, f
    water = g_map + (1.0 - g_map) * f
    return (n_pure * water + WATER_PRIOR_WEIGHT * g_map) / (n_pure + WATER_PRIOR_WEIGHT), water, f


def fit_water_split(frames: dict, chosen: dict, heads: dict, impact_raw: dict, events: pd.DataFrame,
                    samples: pd.DataFrame, map_spec: dict) -> dict:
    """Stage 2 v3: v2's structure, shares from the water (see water_share)."""
    attributed = group_event_days(frames, chosen, events)
    shares, medians, group_outfalls = {}, {}, {}
    for group, (basin, _stations) in SITE_GROUPS.items():
        med = float(impact_raw[group]["median_event_volume_mg"])
        medians[group] = med
        group_outfalls[group] = outfalls_posting(group)
        df = frames[chosen[basin]]
        all_days = set(df[(df[f"{basin}_covered"] == 1) & (df[f"{basin}_csd"] == 1)]["date"])
        non_att = all_days - attributed[group]
        att_rows = tail_rows(frames, chosen, heads, samples, group, attributed[group], med)
        non_rows = tail_rows(frames, chosen, heads, samples, group, non_att, med)
        base_rows = tail_rows(frames, chosen, heads, samples, group, None, med)
        base = base_rows[base_rows["days_since"].isna()]
        b = float(base["elevated"].mean()) if len(base) else 0.0
        # pure non-attributed tails: within 3 days of a non-attributed discharge, none attributed in the last 7
        att_by_date = att_rows.set_index("date")["days_since"] if len(att_rows) else pd.Series(dtype=float)
        pure = non_rows[non_rows["days_since"].notna() & (non_rows["days_since"] <= WATER_TAIL_DAYS)].copy()
        if len(pure):
            pure = pure[pure["date"].map(lambda d: pd.isna(att_by_date.get(d, np.nan)))]
        att_tail = att_rows[att_rows["days_since"].notna() & (att_rows["days_since"] <= WATER_TAIL_DAYS)]
        map_sh = map_spec["shares"][group]
        identity = all(((map_sh.get(sz) or {}).get("p") or 0) >= 1.0 for sz in ("large", "small"))
        row = {"n_basin_days": map_sh.get("n_basin_days"), "n_attributed_days": len(attributed[group] & all_days),
               "n_non_attributed_days": len(non_att), "baseline": round(b, 4), "n_baseline_sample_days": int(len(base)), "identity": identity}
        for size in ("large", "small", "all"):
            pick = (lambda t: t) if size == "all" else (lambda t: t[t["large"] == (size == "large")])
            ta, tp = pick(att_tail), pick(pure)
            r_att = float(ta["elevated"].mean()) if len(ta) else None
            r_pure = float(tp["elevated"].mean()) if len(tp) else None
            g_map = (map_sh.get(size) or {}).get("p")
            p, water, f = water_share(r_att, r_pure, len(tp), b, g_map, identity)
            row[size] = {"p": None if p is None else round(float(p), 4), "n": (map_sh.get(size) or {}).get("n"),
                         "water": None if water is None else round(float(water), 4), "map": g_map,
                         "f_non_attributed": None if f is None else round(float(f), 4),
                         "tail_rate_attributed": None if r_att is None else round(r_att, 4), "n_tail_attributed": int(len(ta)),
                         "tail_rate_pure_non_attributed": None if r_pure is None else round(r_pure, 4), "n_tail_pure_non_attributed": int(len(tp))}
        shares[group] = row
    return {"variant": "v3", "kind": "outfall_split_water", "fitted_at": datetime.now().isoformat(timespec="seconds"),
            "group_outfalls": group_outfalls, "shares": shares, "median_event_volume_mg": medians,
            "tail_days": WATER_TAIL_DAYS, "prior_weight": WATER_PRIOR_WEIGHT, "min_signal": WATER_MIN_SIGNAL,
            "definition": "p_group(D−k) = p_basin(D−k) · [w·g_large + (1−w)·g_small], w = V/(V+median) — as v2; "
                          "g_size = blend of the water share, map + (1 − map)·f, with f = the group's over-standard rate on pure "
                          "non-attributed tails (a non-attributed basin discharge in the last 3 days, no attributed one in the last 7) "
                          "above its dry-weather rate, over the same after attributed discharges; blended with the v2 map share at "
                          f"{WATER_PRIOR_WEIGHT} pure-tail sample-days of prior weight; identity groups stay 1"}


def group_event_days(frames: dict, chosen: dict, events: pd.DataFrame) -> dict:
    """{group: set of dates} of discharge days attributed to the group for the
    impact refit: CIWQS days where one of its outfalls discharged, plus the
    basin's feed-archive days (no outfall detail — kept, unattributed)."""
    out = {}
    for group, (basin, _stations) in SITE_GROUPS.items():
        outs = outfalls_posting(group)
        df = frames[chosen[basin]]
        ev_days = df[(df[f"{basin}_covered"] == 1) & (df[f"{basin}_csd"] == 1)]
        arch = set(ev_days[ev_days[f"{basin}_label_source"] == "poobot"]["date"])
        bev = events[events["app_basin"] == basin]
        hit = set(pd.to_datetime(bev[bev["outfall_id"].isin(outs)]["event_date"]).dt.normalize())
        out[group] = (set(ev_days["date"]) & hit) | arch
    return out


def group_share(spec: dict, group: str, vol: float) -> float:
    """Blended share for a discharge of predicted volume `vol` (MG)."""
    s = spec["shares"].get(group)
    if not s:
        return 1.0
    med = spec.get("median_event_volume_mg", {}).get(group, 1.0) or 1.0
    gl = (s.get("large") or {}).get("p")
    gs = (s.get("small") or {}).get("p")
    ga = (s.get("all") or {}).get("p")
    if gl is None and gs is None:
        return 1.0 if ga is None else ga
    if gl is None:
        gl = gs
    if gs is None:
        gs = gl
    w = vol / (vol + med)
    return w * gl + (1 - w) * gs


def make_split(spec: dict | None):
    """The `split` callable impact.compose takes, or None for stage 2 v1 (the served composition)."""
    if not spec or spec.get("variant", "v1") == "v1":
        return None
    if spec["variant"] not in ("v2", "v3"):   # v3 = v2's arithmetic with water-fitted shares
        raise ValueError(f"unknown stage 2 variant {spec['variant']!r}")

    def split(group: str, p: float, vol: float) -> float:
        return p * group_share(spec, group, vol)
    return split


def variant_path(name: str) -> Path:
    if name not in VARIANTS or name == "v1":
        raise ValueError(f"no fitted file for stage 2 variant {name!r}")
    return VARIANTS_DIR / f"{name}.json"


def load_variant(name: str) -> dict:
    return json.loads(variant_path(name).read_text())


def save_variant(spec: dict) -> Path:
    VARIANTS_DIR.mkdir(parents=True, exist_ok=True)
    p = variant_path(spec["variant"])
    p.write_text(json.dumps(spec, indent=1, default=str))
    return p
