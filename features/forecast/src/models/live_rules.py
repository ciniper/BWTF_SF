"""Live corrections (live_v2) — what observed CSO flags and bacteria results do
to the forecast once it is running. Not a third stage: two sets of conditional
edits, one before stage 2 (on the discharge probabilities) and one after it (on
the composed beach risk); on most days they do nothing at all. Serving-side only: the stage-1 models, the
impact table and the stored hindcasts are untouched. Design, evidence and the
numbers behind every constant: features/forecast/LIVE_COMPOSITION_DESIGN.md.

Stage 1 (per basin-day, before composition — ``adjust_stage1``):
  cso_onset               the watcher saw a CSO onset that day → p = 1
  cso_anchor_prev_day     the feed's onset usually lands the day after the
                          filed discharge; when the day before had rain or a
                          real model probability, anchor it too → p = 1
  no_flag_downgrade       an expected discharge the feed never flagged: Bayes,
                          p' = p(1-R)/(p(1-R)+1-p), where R grows with every
                          quiet day observed after the storm day (Chase,
                          2026-09-26): conservative the morning after (R = 0.60,
                          silence through the storm day only), the archive's
                          same-or-next-day recall after one quiet day (0.87),
                          and 0.95 after two or more — farther from the event,
                          more proof there was no CSO. Bayside basins only; the
                          Westside's recall is unmeasured. Only while the
                          watcher is live and healthy — absence of a flag is
                          evidence only if someone was watching.
  large_when_flag_persists  a flag up for ≥ 2 days means a large event: the
                          onset day's volume is raised to the large curve

Stage 2 (per group-day, after composition — ``adjust_groups``):
  sample_elevated_floor   an elevated sample in a discharge tail floors the
                          group's risk at the zone's empirical P(elevated next
                          | elevated now) for the next-sample horizon — ONLY
                          where that rate is ≥ 0.5 (today: the East, 0.80).
                          Floors below 0.5 never change a 50% call but turned
                          20% days into alarms at the 25% line (both replays,
                          2026-09-27), so they are zero = no floor.
  sample_clean_cap        a clean sample in a discharge tail caps the
                          PERSISTENCE term at P(elevated next | clean now) —
                          never zero: in the East a clean bottle is followed
                          by an elevated one two times in three — and
                          recombines it with the group's own day term (the
                          basin's p after the stage 2 split). It never raises
                          a risk (until 2026-10-01 it recombined with the
                          whole basin p, which lifted Westside groups whose
                          split share is under 1)
  sample_dry_floor        an elevated sample with no recent discharge would
                          floor the risk at the dry-weather repeat rate — every
                          zone's is under 0.5, so today this rule is off (the
                          rates stay in SAMPLE_RATES for the record)
  flag_hold               while the feed's CSO flag stays up after the onset,
                          the group's risk holds at the large-event curve

Where the samples come from (live_v2, 2026-09-29): SFPUC's beach map, as the
pg_cron watcher records it (Supabase ``feed_station_days``) — a station posted
for bacteria says its latest sample, dated by the map, was over standard; a
station clear all day with a new sample date says that sample was clean. The
map shows a result one to two days after sampling; DataSF's lab record follows
about five days after and overrides the map wherever it has published the same
station-day. live_v1 read DataSF alone, which reached the three-day horizon too
late to move a forecast day. The arithmetic is unchanged, so both replays grade
live_v2 exactly as they graded live_v1 (``live_dashboard._feed_sample_flags``).

Every adjustment is recorded (rule, from, to) so the page can say why a
number moved; with no watcher data and no samples the output is byte-identical
to the plain composition (tests/test_live_rules.py). Switch: env
LIVE_CORRECTIONS=off, or the watcher_config key live_corrections = 'off'
(live_dashboard._live_corrections_enabled); the payload always carries the
plain composition beside the corrected one so the page can show either.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

VERSION = "live_v2"

# Zone-level next-sample transition rates from the scorecard's sample record
# (pairs of sampled days ≤ 3 days apart; "tail" = a discharge in the zone within
# the prior 7 days). Fit 2026-09-26 by ``fit_sample_rates``; see the design doc.
# These are the EVIDENCE; RULES["samples"] below is the POLICY made from them.
SAMPLE_RATES = {
    "floor_elevated_tail": {"ocean": 0.40, "baker_china": 0.14, "north": 0.35, "east": 0.80},
    "cap_clean_tail":      {"ocean": 0.42, "baker_china": 0.36, "north": 0.67, "east": 0.67},
    "floor_elevated_dry":  {"ocean": 0.06, "baker_china": 0.20, "north": 0.16, "east": 0.38},
}
FLOOR_MIN = 0.5   # a floor below the 50% line never changes a 50% call but hurts every lower line (Chase, 2026-09-27)


def _policy_floors(rates: dict) -> dict:
    """Floors at or above FLOOR_MIN; anything lower is zero = no floor."""
    return {z: (r if r >= FLOOR_MIN else 0.0) for z, r in rates.items()}


RULES = {
    "version": VERSION,
    "cso": {
        "onset_p": 1.0,
        "anchor_prev_day": {"min_p": 0.25, "min_rain_in": 0.25},
        "large_when_flag_persists_days": 2,
        "large_volume_mg": 1e6,        # far above any group's median → the large curve
        "hold_while_flagged": True,
        "hold_max_days": 7,
    },
    "downgrade": {
        # R[k] = P(the feed has flagged a real discharge on D by the end of day D+k). Index k = quiet days fully
        # observed after D: 0 = the morning after (silence through D only — conservative, the flag can lag),
        # 1 = the archive's same-or-next-day recall (13 of 15 bayside days, 2016-17), 2+ = assumed 0.95 (the
        # archive's two misses were never flagged at all; to be measured in the watcher era). Westside: unmeasured → off.
        "recall_by_quiet_days": {"westside": [0.0, 0.0, 0.0], "north_shore": [0.60, 0.87, 0.95],
                                 "central": [0.60, 0.87, 0.95], "southeast": [0.60, 0.87, 0.95]},
        "min_p": 0.15,                 # only days the model actually called; tiny days are left alone
    },
    "samples": {
        "known_lag_days": 1,           # results reach DataSF / the feed a day or two after sampling
        "horizon_days": 3,             # the next-sample horizon; the model resumes after it
        "tail_days": 7,
        "tail_min_p": 0.5,             # a sample day counts as "in a discharge tail" when an onset was
                                       # observed, or the (adjusted) basin probability reached this, within tail_days
        "sources": ("feed", "datasf"), # live_v2: the SFPUC map's postings and sample dates first (1–2 days), DataSF (≈5 days) overriding where published
        "floor_elevated_tail": _policy_floors(SAMPLE_RATES["floor_elevated_tail"]),   # {east: 0.80}, the rest off
        "cap_clean_tail":      dict(SAMPLE_RATES["cap_clean_tail"]),                    # caps are kept everywhere
        "floor_elevated_dry":  _policy_floors(SAMPLE_RATES["floor_elevated_dry"]),    # all under 0.5 → off
    },
}


def bayes_downgrade(p: float, recall: float) -> float:
    """P(discharge on D | no flag by the end of D+1) from the model's p and the feed's recall."""
    if p <= 0.0 or p >= 1.0 or recall <= 0.0:
        return p
    miss = p * (1.0 - recall)
    return miss / (miss + (1.0 - p))


# ── stage 1 ────────────────────────────────────────────────────────────────

def adjust_stage1(probs: list, vols: list, dates: list, onsets: dict, flags_active: dict, rain_by_date: dict,
                  today: date, rules: dict = RULES, watcher_from: date | None = None, watcher_ok: bool = False):
    """(probs', vols', notes). ``probs[j]`` = {basin_key: p} for ``dates[j]``;
    ``onsets`` = {date: {basin_key}} the watcher's CSO onsets; ``flags_active``
    = {date: {basin_key}} days the flag was up; ``rain_by_date`` = {date: inches}.
    ``watcher_from`` / ``watcher_ok`` gate the downgrade: only days the watcher
    was live, and only while it is healthy now."""
    probs2 = [dict(p) for p in probs]
    vols2 = [dict(v) for v in vols]
    notes: dict = {}
    cso, dg = rules["cso"], rules["downgrade"]
    onsets = onsets or {}
    flags_active = flags_active or {}

    def note(d, b, rule, before, after):
        notes.setdefault(str(d), {})[b] = {"rule": rule, "from": round(float(before), 3), "to": round(float(after), 3)}

    for j, d in enumerate(dates):
        for b, p in probs[j].items():
            p = float(p or 0.0)
            if b in onsets.get(d, ()):
                probs2[j][b] = cso["onset_p"]
                note(d, b, "cso_onset", p, cso["onset_p"])
                run = 0
                while b in flags_active.get(d + timedelta(days=run), ()):
                    run += 1
                if run >= cso["large_when_flag_persists_days"] and vols2[j].get(b, 0.0) < cso["large_volume_mg"]:
                    vols2[j][b] = cso["large_volume_mg"]
                    notes.setdefault(str(d), {}).setdefault("_volume", {})[b] = "large_when_flag_persists"
                continue
            nxt = d + timedelta(days=1)
            if b in onsets.get(nxt, ()) and (p >= cso["anchor_prev_day"]["min_p"] or float(rain_by_date.get(d, 0.0) or 0.0) >= cso["anchor_prev_day"]["min_rain_in"]):
                probs2[j][b] = cso["onset_p"]
                note(d, b, "cso_anchor_prev_day", p, cso["onset_p"])
                continue
            sched = dg["recall_by_quiet_days"].get(b) or []
            quiet = (today - d).days - 1          # quiet days fully observed after d (today itself is not over)
            if (sched and quiet >= 0 and p >= dg["min_p"] and watcher_ok and watcher_from is not None and d >= watcher_from
                    and not any(b in onsets.get(d + timedelta(days=k), ()) for k in range(0, min(quiet, len(sched) - 1) + 2))):
                k = min(quiet, len(sched) - 1)
                r = sched[k]
                if r > 0.0:
                    p2 = bayes_downgrade(p, r)
                    if abs(p2 - p) >= 0.0005:
                        probs2[j][b] = round(p2, 4)
                        note(d, b, "no_flag_downgrade", p, p2)
                        notes[str(d)][b]["quiet_days"] = k
    return probs2, vols2, notes


# ── stage 2 ────────────────────────────────────────────────────────────────

def in_tail(basin: str, day: date, probs_by_date: dict, onsets: dict, rules: dict = RULES) -> bool:
    """Was there (probably) a discharge in the basin within tail_days before ``day``?"""
    s = rules["samples"]
    for k in range(0, s["tail_days"] + 1):
        d = day - timedelta(days=k)
        if basin in (onsets or {}).get(d, ()):
            return True
        if float((probs_by_date.get(d) or {}).get(basin, 0.0) or 0.0) >= s["tail_min_p"]:
            return True
    return False


def adjust_groups(group_risks: dict, persist_risks: dict, today_terms: dict, day: date, samples: dict,
                  probs_by_date: dict, onsets: dict, flags_active: dict, zone_of_group: dict, basin_of_group: dict,
                  large_curve, rules: dict = RULES):
    """(risks', notes) for one day. ``group_risks`` = the composed risk per group;
    ``persist_risks`` = the same with the day's own discharge term removed;
    ``today_terms`` = {group: that day's own discharge term as the composition
    used it — the basin's p after the stage 2 split, impact.day_terms};
    ``samples`` = {(group, date): elevated};
    ``large_curve(group, k)`` = x(k) on the large-event curve. Notes:
    {group: {"rule", "from", "to"}} for every group whose number moved."""
    s, cso = rules["samples"], rules["cso"]
    out = dict(group_risks)
    notes: dict = {}
    onsets = onsets or {}
    flags_active = flags_active or {}

    def apply(g, rule, value):
        if abs(value - out[g]) >= 0.0005:
            notes[g] = {"rule": rule, "from": round(out[g], 3), "to": round(value, 3)}
            out[g] = round(value, 3)

    for g in list(out):
        zone, basin = zone_of_group.get(g), basin_of_group.get(g)
        # the latest sample whose result is known by ``day`` and still inside the horizon
        cands = [(sd, el) for (gg, sd), el in samples.items()
                 if gg == g and sd + timedelta(days=s["known_lag_days"]) <= day <= sd + timedelta(days=s["horizon_days"])]
        if cands and zone:
            sd, elevated = max(cands)
            tail = in_tail(basin, sd, probs_by_date, onsets, rules)
            if elevated and tail:
                if s["floor_elevated_tail"].get(zone, 0.0) > 0.0:   # a zero rate = no floor for that zone
                    apply(g, "sample_elevated_floor", max(out[g], s["floor_elevated_tail"][zone]))
            elif elevated:
                if s["floor_elevated_dry"].get(zone, 0.0) > 0.0:
                    apply(g, "sample_dry_floor", max(out[g], s["floor_elevated_dry"][zone]))
            elif tail:
                # recombine with the group's own day term (after the stage 2 split), not the basin's p, and never
                # above the composed risk: a clean bottle can only lower a number
                persist = min(float(persist_risks.get(g, out[g])), s["cap_clean_tail"][zone])
                today_p = float(today_terms.get(g, 0.0) or 0.0)
                apply(g, "sample_clean_cap", min(out[g], 1.0 - (1.0 - persist) * (1.0 - today_p)))
        # the feed's flag still up after the onset: hold at the large-event curve
        if cso["hold_while_flagged"] and basin in flags_active.get(day, ()) and basin not in onsets.get(day, ()):
            k = next((k for k in range(1, cso["hold_max_days"] + 1) if basin in onsets.get(day - timedelta(days=k), ())), None)
            if k is not None:
                apply(g, "flag_hold", max(out[g], float(large_curve(g, k))))
    return out, notes


# ── refit the sample rates from the scorecard record ──────────────────────

def fit_sample_rates(days: list, zone_groups: dict, max_gap_days: int = 3, tail_days: int = 7) -> dict:
    """{zone: {"floor_elevated_tail", "cap_clean_tail", "floor_elevated_dry", "n": {...}}}
    from the hindcast artifact's zone-level labels — the same arithmetic that
    produced RULES["samples"] on 2026-09-26, so a refit after a CIWQS/DataSF
    refresh is one call."""
    from datetime import datetime as _dt
    out = {}
    for zk in zone_groups:
        dis = {_dt.strptime(d["date"], "%Y-%m-%d").date() for d in days if d["zones"][zk]["discharge"]}
        smp = [(_dt.strptime(d["date"], "%Y-%m-%d").date(), bool(d["zones"][zk]["elevated"])) for d in days if d["zones"][zk]["elevated"] is not None]
        tr = {"tail": {"EE": 0, "EC": 0, "CE": 0, "CC": 0}, "dry": {"EE": 0, "EC": 0, "CE": 0, "CC": 0}}
        for (d0, e0), (d1, e1) in zip(smp, smp[1:]):
            if (d1 - d0).days > max_gap_days:
                continue
            t = "tail" if any((d0 - timedelta(days=k)) in dis for k in range(0, tail_days + 1)) else "dry"
            tr[t][("E" if e0 else "C") + ("E" if e1 else "C")] += 1
        rate = lambda a, b: round(a / (a + b), 2) if a + b else None  # noqa: E731
        out[zk] = {"floor_elevated_tail": rate(tr["tail"]["EE"], tr["tail"]["EC"]),
                   "cap_clean_tail": rate(tr["tail"]["CE"], tr["tail"]["CC"]),
                   "floor_elevated_dry": rate(tr["dry"]["EE"], tr["dry"]["EC"]),
                   "n": {k: dict(v) for k, v in tr.items()}}
    return out


def rules_with_fitted_rates(rates: dict, rules: dict = RULES) -> dict:
    """A copy of ``rules`` with the sample caps and floors rebuilt from
    ``fit_sample_rates`` output, floors filtered by FLOOR_MIN as the policy says."""
    r = copy.deepcopy(rules)
    for zk, v in rates.items():
        if v.get("cap_clean_tail") is not None:
            r["samples"]["cap_clean_tail"][zk] = v["cap_clean_tail"]
        for key in ("floor_elevated_tail", "floor_elevated_dry"):
            if v.get(key) is not None:
                r["samples"][key][zk] = v[key] if v[key] >= FLOOR_MIN else 0.0
    return r
