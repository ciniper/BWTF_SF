"""A forecast's stage lineup in plain words (STAGES_DESIGN.md A8).

Stored set names stay identifiers (``served.json``, forecast history, the pins): ``logit_v1_s2v2``'s "s2" is
the old two-stage pipeline's stage 2, now S3 + S4. Wherever people read a set (the stages report, the Model
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
    "geography": {"geo_v1": "BWTF basins", "sfpuc4_v1": "SFPUC basins"},
    "s1": {"icon_seamless": "ICON", "ecmwf_ifs025": "ECMWF", "gfs_seamless": "GFS"},
    "s2": {"logit_v1": "38-weight", "gb_v1": "Trees", "shared8_nonneg_sfpuc4": "8-term SFPUC",
           # the shared-terms sets on BWTF basins (leaderboard.SHARED_DESIGNS; the count is their bands)
           "logit_v2_shared5": "5-term", "logit_v2_shared6": "6-term", "logit_v2_shared8": "8-term",
           "logit_v2_half": "19-term", "logit_v2_four": "Four-input"},
    "s3": {"basin_v1": "No split", "split_v2": "Outfall split", "split_v2_sfpuc4": "Outfall split SFPUC", "links_v1": "Size split"},
    "s4": {"impact_v1": "Linger table 1", "impact_v2": "Linger table 2", "impact_v2_zone": "Linger table 2 per zone",
           "zone_v3": "Rain curve"},
    # live_v2 is the replay's basin_swap (and, with its sample floors and caps, all_floors); link_zone_v1 serves the
    # replay's link_zone_swap (A7)
    "s5": {"live_v2": "Basin flags", "basin_swap": "Basin flags", "all_floors": "Basin flags + floors", "plain": "No correction",
           "link_swap": "Link flags", "zone_swap": "Zone flags", "link_zone_swap": "Link/zone flags", "link_zone_v1": "Link/zone flags",
           "sample_swap": "Lab-result rule", "downgrade": "No-flag downgrade"},
}


def words(col: str, cid: str) -> str:
    try:
        return WORDS[col][cid]
    except KeyError:
        raise KeyError(f"no plain words for {col} component {cid!r}: add them to shared/lineup.py WORDS") from None


def geo_v1_parts(stage1: str, variant: str) -> dict:
    """S2–S4 of a set on BWTF basins, from its two stored halves: the overflow model (stored as its
    "stage 1") and its stage 2 variant (v2 = Outfall split and Linger table 2; v1 = No split and Linger table 1)."""
    v2 = variant == "v2"
    return {"s2": stage1, "s3": "split_v2" if v2 else "basin_v1", "s4": "impact_v2" if v2 else "impact_v1"}
