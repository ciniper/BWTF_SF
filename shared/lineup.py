"""A forecast's stage lineup in plain words (STAGES_DESIGN.md A8).

Stored set names stay identifiers (``served.json``, forecast history, the pins): ``logit_v1_s2v2``'s "s2" is
the old two-stage pipeline's stage 2, today S3 + S4. Wherever people read a set (the stages report, the Model
check, the flowchart) they read its lineup instead: basins · S1 weather model · S2 overflow model · S3 beach
split · S4 lingering table · S5 live correction rule, each part in plain words.

WORDS is the one hand-typed map, component id → words. ``words`` raises for a component with no words, so a
new part is named here before anything can show it (tests/test_lineup.py checks every set on disk). Standard
library only, no IO: the web app, the stage build and the stages report all import it.
"""
from __future__ import annotations

COLUMNS = (("geography", "Basins"), ("s1", "S1 · weather model"), ("s2", "S2 · overflow model"),
           ("s3", "S3 · beach split"), ("s4", "S4 · lingering table"), ("s5", "S5 · live correction rule"))

WORDS = {
    "geography": {"geo_v1": "today's 4 basins (Islais in Southeast)", "sfpuc4_v1": "the city's 4 basins"},
    "s1": {"icon_seamless": "ICON", "ecmwf_ifs025": "ECMWF", "gfs_seamless": "GFS"},
    "s2": {"logit_v1": "38-weight model", "gb_v1": "boosted-trees model", "shared8_nonneg_sfpuc4": "8-term model (shared8)",
           # the shared-terms sets on today's basins (leaderboard.SHARED_DESIGNS; the count is their bands)
           "logit_v2_shared5": "5-term model (shared5)", "logit_v2_shared6": "6-term model (shared6)",
           "logit_v2_shared8": "8-term model (shared8)", "logit_v2_half": "19-term model (half)",
           "logit_v2_four": "9-term model (four inputs)"},
    "s3": {"split_v2": "today's split", "basin_v1": "no split", "links_v1": "split by overflow size",
           "split_v2_sfpuc4": "today's split, on the city's basins"},
    "s4": {"impact_v2": "today's lingering table", "impact_v1": "the first lingering table", "zone_v3": "per-zone curve with rain",
           "impact_v2_zone": "today's lingering table, per zone"},
    # link_zone_swap is the replay's name for the rule that serves as link_zone_v1 (A7)
    "s5": {"live_v2": "today's rule (live_v2)", "link_zone_swap": "link/zone", "link_zone_v1": "link/zone"},
}


def words(col: str, cid: str) -> str:
    try:
        return WORDS[col][cid]
    except KeyError:
        raise KeyError(f"no plain words for {col} component {cid!r}: add them to shared/lineup.py WORDS") from None


def geo_v1_parts(stage1: str, variant: str) -> dict:
    """S2–S4 of a set on today's basins, from its two stored halves: the overflow model (stored as its
    "stage 1") and its stage 2 variant (v2 = today's split and lingering table; v1 = no split, the first table)."""
    v2 = variant == "v2"
    return {"s2": stage1, "s3": "split_v2" if v2 else "basin_v1", "s4": "impact_v2" if v2 else "impact_v1"}
