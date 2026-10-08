"""A forecast's stage lineup in plain words and in its name (STAGES_DESIGN.md A8; Part B 36).

A set is its lineup: basins · S1 weather model · S2 overflow model · S3 beach split · S4 lingering table · S5 live
correction rule. Its full name is each stage's name at the time it was made, in that order (Chase, 2026-10-07: "the
full model name should have a combo of each stage at the time's name"):

    words   ``full_words``: "ICON · 38-weight · Outfall split · Linger table 2 · Basin flags"
    id      ``set_id``: each part's CODE joined by "-", basins first and only when not BWTF basins
            ("icon-w38-osplit-lt2-bflags", "sfpuc-icon-t8s-osplits-lt2zone-lzflags")

Every set records its lineup in its manifest (served.json for the live one), and its stored name is set_id of it:
the savers refuse any other (candidates.save_candidate, stages_candidates). Names before 2026-10-07 were the
overflow model's name plus a stage 2 suffix (``logit_v1_s2v2``); RENAMED maps each to its id, so forecast history,
old links and dated notes still resolve (``current_name``).

WORDS and CODES are the hand-typed maps, component id → words, component id → code. ``words`` / ``code`` raise for
a component with none, so a new part is named here before anything can show it (tests/test_lineup.py checks every
set on disk). Standard library only, no IO: the web app, the stage build and the stages report all import it.
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
           "logit_v2_half": "19-term", "logit_v2_four": "Four-input",
           # the 38-weight design refit on the older SFPUC discharge reports too (train_older_reports.py): the years
           # added, and which days of a multi-day discharge count (the first of each run, or all of them)
           "logit_v1_older16_first": "38-weight + 2016–17 reports (first days)",
           "logit_v1_older16_every": "38-weight + 2016–17 reports (all days)",
           "logit_v1_older11_first": "38-weight + 2011–17 reports (first days)",
           "logit_v1_older11_every": "38-weight + 2011–17 reports (all days)",
           # a short named term list with the south wind, picked nested (train_terms.py), on the 2011 record
           "logit_wind8_older11": "8 terms + south wind", "logit_wind9_older11": "9 terms + south wind",
           "logit_wind10_older11": "10 terms + south wind",
           # the 8-term set's terms plus the day's 3-hour peak, forced (Chase, 2026-10-07: "the current 8 term set ...
           # PLUS the 3 hour peak"): 9 terms
           "logit_wind8_max3h_older11": "8 terms + south wind + 3-hour peak"},
    "s3": {"basin_v1": "No split", "split_v2": "Outfall split", "split_v2_sfpuc4": "Outfall split SFPUC", "links_v1": "Size split"},
    "s4": {"impact_v1": "Linger table 1", "impact_v2": "Linger table 2", "impact_v2_zone": "Linger table 2 per zone",
           "impact_v2_d10": "Linger table 2, more samples",
           "zone_v3": "Rain curve", "zone_choice_v1": "Rain curve or table, picked on S4",
           "zone_choice_v2": "Rain curve or table, picked on the public number"},
    # live_v2 is the replay's basin_swap (and, with its sample floors and caps, all_floors); link_zone_v1 serves the
    # replay's link_zone_swap (A7)
    "s5": {"live_v2": "Basin flags", "basin_swap": "Basin flags", "all_floors": "Basin flags + floors", "plain": "No correction",
           "link_swap": "Link flags", "zone_swap": "Zone flags", "link_zone_swap": "Link/zone flags", "link_zone_v1": "Link/zone flags",
           "sample_swap": "Lab-result rule", "downgrade": "No-flag downgrade"},
}


# One short code per component, for set ids: lowercase letters and digits only (the id joins them with "-"). Two
# components share a code only when they share their words (the same part under two ids).
CODES = {
    "geography": {"geo_v1": "", "sfpuc4_v1": "sfpuc"},
    "s1": {"icon_seamless": "icon", "ecmwf_ifs025": "ecmwf", "gfs_seamless": "gfs"},
    "s2": {"logit_v1": "w38", "gb_v1": "trees", "shared8_nonneg_sfpuc4": "t8s",
           "logit_v2_shared5": "t5", "logit_v2_shared6": "t6", "logit_v2_shared8": "t8", "logit_v2_half": "t19",
           "logit_v2_four": "four",
           "logit_v1_older16_first": "w38r16f", "logit_v1_older16_every": "w38r16a",
           "logit_v1_older11_first": "w38r11f", "logit_v1_older11_every": "w38r11a",
           "logit_wind8_older11": "t8wind", "logit_wind9_older11": "t9wind", "logit_wind10_older11": "t10wind",
           "logit_wind8_max3h_older11": "t8wind3h"},
    "s3": {"basin_v1": "nosplit", "split_v2": "osplit", "split_v2_sfpuc4": "osplits", "links_v1": "ssplit"},
    "s4": {"impact_v1": "lt1", "impact_v2": "lt2", "impact_v2_zone": "lt2zone", "impact_v2_d10": "lt2more",
           "zone_v3": "rain", "zone_choice_v1": "pick4", "zone_choice_v2": "pickout"},
    "s5": {"live_v2": "bflags", "basin_swap": "bflags", "all_floors": "bfloors", "plain": "nocorr",
           "link_swap": "lflags", "zone_swap": "zflags", "link_zone_swap": "lzflags", "link_zone_v1": "lzflags",
           "sample_swap": "labrule", "downgrade": "noflagdown"},
}
NAME_COLS = ("geography", "s1", "s2", "s3", "s4", "s5")        # a set's id, in order
STAGE_COLS = ("s1", "s2", "s3", "s4", "s5")                    # a set's full name in words

# Every set's name before 2026-10-07 (the overflow model's name plus a stage 2 suffix) → its id. History: never
# edited, only added to. current_name reads it, so a stamp in forecast_history or an old link still finds its set.
RENAMED = {
    "logit_v1_s2v2": "icon-w38-osplit-lt2-bflags",
    "gb_v1": "icon-trees-nosplit-lt1-bflags",
    "gb_v1_s2v2": "icon-trees-osplit-lt2-bflags",
    "logit_v1": "icon-w38-nosplit-lt1-bflags",
    "logit_v1_s2v2d10": "icon-w38-osplit-lt2more-bflags",
    "logit_v1_older16_first_s2v2": "icon-w38r16f-osplit-lt2-bflags",
    "logit_v1_older16_every_s2v2": "icon-w38r16a-osplit-lt2-bflags",
    "logit_v1_older11_first_s2v2": "icon-w38r11f-osplit-lt2-bflags",
    "logit_v1_older11_every_s2v2": "icon-w38r11a-osplit-lt2-bflags",
    "logit_v2_four": "icon-four-nosplit-lt1-bflags",
    "logit_v2_four_s2v2": "icon-four-osplit-lt2-bflags",
    "logit_v2_half": "icon-t19-nosplit-lt1-bflags",
    "logit_v2_half_s2v2": "icon-t19-osplit-lt2-bflags",
    "logit_v2_shared5": "icon-t5-nosplit-lt1-bflags",
    "logit_v2_shared5_s2v2": "icon-t5-osplit-lt2-bflags",
    "logit_v2_shared6": "icon-t6-nosplit-lt1-bflags",
    "logit_v2_shared6_s2v2": "icon-t6-osplit-lt2-bflags",
    "logit_v2_shared8": "icon-t8-nosplit-lt1-bflags",
    "logit_v2_shared8_s2v2": "icon-t8-osplit-lt2-bflags",
    "logit_wind8_older11_s2v2": "icon-t8wind-osplit-lt2-bflags",
    "logit_wind8_older11_s2v2d10": "icon-t8wind-osplit-lt2more-bflags",
    "logit_wind9_older11_s2v2": "icon-t9wind-osplit-lt2-bflags",
    "logit_wind9_older11_s2v2d10": "icon-t9wind-osplit-lt2more-bflags",
    "logit_wind10_older11_s2v2": "icon-t10wind-osplit-lt2-bflags",
    "logit_wind10_older11_s2v2d10": "icon-t10wind-osplit-lt2more-bflags",
    "sfpuc4_shared8_v1": "sfpuc-icon-t8s-ssplit-rain-lzflags",
    "sfpuc4_shared8_v2": "sfpuc-icon-t8s-osplits-lt2zone-lzflags",
    "sfpuc4_shared8_v3": "sfpuc-icon-t8s-osplits-pick4-lzflags",
    "sfpuc4_shared8_v3b": "sfpuc-icon-t8s-osplits-pickout-lzflags",
}


def code(col: str, cid: str) -> str:
    try:
        return CODES[col][cid]
    except KeyError:
        raise KeyError(f"no code for {col} component {cid!r}: add one to shared/lineup.py CODES") from None


def set_id(lineup: dict) -> str:
    """A set's stored name: the code of each part of ``lineup`` ({geography, s1 … s5}), joined by "-" in NAME_COLS
    order, an empty code (BWTF basins) left out."""
    missing = [c for c in NAME_COLS if c not in lineup]
    if missing:
        raise KeyError(f"a lineup names every part; this one lacks {missing}")
    return "-".join(c for c in (code(col, lineup[col]) for col in NAME_COLS) if c)


def full_words(lineup: dict) -> str:
    """A set's full name in words: its five stages' words in order, "ICON · 38-weight · … · Basin flags"."""
    return " · ".join(words(c, lineup[c]) for c in STAGE_COLS)


def current_name(name: str) -> str:
    """A set's name today: its id, for a name it carried before 2026-10-07 (RENAMED); any other name as given."""
    return RENAMED.get(name, name)


def geo_v1_lineup(stage1: str, variant: str, s1: str, s5: str) -> dict:
    """The full lineup of a set on BWTF basins: its weather model and correction rule (the ones it was made with),
    its overflow model, and the split and table its stage 2 id names (geo_v1_parts)."""
    return {"geography": "geo_v1", "s1": s1, **geo_v1_parts(stage1, variant), "s5": s5}


def words(col: str, cid: str) -> str:
    try:
        return WORDS[col][cid]
    except KeyError:
        raise KeyError(f"no plain words for {col} component {cid!r}: add them to shared/lineup.py WORDS") from None


GEO_V1_STAGE2 = {"v1": ("basin_v1", "impact_v1"), "v2": ("split_v2", "impact_v2"),
                 # v2 with its linger table fit on the stages' S4 truth samples (STARDB 2016-10 → 2020-07 too)
                 "v2_d10": ("split_v2", "impact_v2_d10")}


def geo_v1_parts(stage1: str, variant: str) -> dict:
    """S2–S4 of a set on BWTF basins, from its two stored halves: the overflow model (stored as its
    "stage 1") and its stage 2 id (v2 = Outfall split and Linger table 2; v1 = No split and Linger table 1;
    v2_d10 = Outfall split and Linger table 2 fit on more samples). An unknown id raises."""
    if variant not in GEO_V1_STAGE2:
        raise KeyError(f"no S3 / S4 for stage 2 {variant!r}: add it to shared/lineup.py GEO_V1_STAGE2")
    s3, s4 = GEO_V1_STAGE2[variant]
    return {"s2": stage1, "s3": s3, "s4": s4}
