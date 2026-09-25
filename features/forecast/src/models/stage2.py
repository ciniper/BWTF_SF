"""Stage 2 variants — named alternatives to the served composition that a
candidate set can carry, selectable in the Model check next to its stage 1.
Nothing here changes what is served: the live forecast composes with the v4
impact table and no split until a variant is promoted.

    v4                 group risk composes the BASIN discharge probability
                       directly (impact.compose with split=None). Ocean Beach
                       and Baker-China get the same p on every day.
    outfall_split_v1   p_group(D−k) = p_basin(D−k) · g_group(size(D−k)) where
                       g_group = share of the basin's CIWQS discharge days (by
                       size class) on which at least one outfall that posts
                       the group's beaches discharged; the impact table is
                       refit on the group-attributed discharge days only.
                       Identity for a group whose outfalls are the basin's
                       (Mission Creek, Southeast); Westside and North Shore
                       each have two groups fed by disjoint outfall sets.

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
VARIANTS = ("v4", "outfall_split_v1")


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
    return {"variant": "outfall_split_v1", "fitted_at": datetime.now().isoformat(timespec="seconds"),
            "group_outfalls": group_outfalls, "shares": shares, "median_event_volume_mg": medians,
            "definition": "p_group(D−k) = p_basin(D−k) · [w·g_large + (1−w)·g_small], w = V/(V+median), "
                          "g_size = share of the basin's CIWQS discharge days of that size on which ≥1 of the group's outfalls discharged"}


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
    """The `split` callable impact.compose takes, or None for the v4 composition."""
    if not spec or spec.get("variant", "v4") == "v4":
        return None
    if spec["variant"] != "outfall_split_v1":
        raise ValueError(f"unknown stage 2 variant {spec['variant']!r}")

    def split(group: str, p: float, vol: float) -> float:
        return p * group_share(spec, group, vol)
    return split


def variant_path(name: str) -> Path:
    if name not in VARIANTS or name == "v4":
        raise ValueError(f"no fitted file for stage 2 variant {name!r}")
    return VARIANTS_DIR / f"{name}.json"


def load_variant(name: str) -> dict:
    return json.loads(variant_path(name).read_text())


def save_variant(spec: dict) -> Path:
    VARIANTS_DIR.mkdir(parents=True, exist_ok=True)
    p = variant_path(spec["variant"])
    p.write_text(json.dumps(spec, indent=1, default=str))
    return p
