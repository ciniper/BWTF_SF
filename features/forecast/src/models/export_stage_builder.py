#!/usr/bin/env python3
"""The Model check's stage builder, as data: every part of every stage with its own-stage score per window, and
every scored chain's public number (Chase, 2026-10-04: "a module for dropping in different modules to different
stages").

    venv/bin/python features/forecast/src/models/export_stage_builder.py   # writes data/models/stage_builder.json

The stage artifacts stay out of the Vercel bundle (vercel.json), so the page cannot read them at request time.
This distils what the builder shows into one small file the app serves at /forecast/api/stage-builder. Run it
after rebuilding the stage scores (SWAPS.md) and commit the JSON; tests/test_stage_builder.py fails when it is stale.

It reads committed artifacts only and never recomputes a number:

    data/models/served.json                      the served set (the LIVE badge)
    data/models/stages/<set>/manifest.json       each scored set's basins and parts: its chain
    data/models/stages/<set>/scores.json         S2–S4 oracle skill, S5 per correction rule, the public number
    data/models/stages/_s1/s1_scores.json        S1 one day ahead, and the floor
    data/models/candidates/<set>/manifest.json   overflow models and lineups with no stage score yet

Every number is one field of one of those files; ``PATHS`` (copied into the output) says which, and the test checks
each one there. Choosing which set a part's number comes from is the only judgement (``home``). Names are
shared/lineup.py's WORDS, the one hand-typed map. The output is a pure function of those files, with no clock:
rebuilding unchanged artifacts gives the same bytes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (str(REPO), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stages_spec as SP  # noqa: E402  (each stage's question and what its oracle is fed, in words)
from shared import geography as G  # noqa: E402
from shared import lineup as LU  # noqa: E402

MODELS = FORECAST / "data" / "models"
SERVED = MODELS / "served.json"
STAGES_DIR = MODELS / "stages"
S1_SCORES = STAGES_DIR / "_s1" / "s1_scores.json"
CANDIDATES_DIR = MODELS / "candidates"
OUT = MODELS / "stage_builder.json"      # NOT under stages/: that folder is left out of the app bundle
SCHEMA = "bwtf.stage_builder/1"
ROOTS = ("served", "candidates", "stages_candidates")   # the stages report's order: the served set, then the challengers

WINDOWS = (("T2", "Nine seasons"), ("T1", "Post-training"), ("T1-holdout", "Holdout"), ("T0", "Live season"))
STAGES = ("s1", "s2", "s3", "s4", "s5")
TITLES = dict(LU.COLUMNS)
# What S1 and S5 show under each window: neither is refit season by season. S1 has one window, the weather model's
# archive (weights-clean throughout: s1_scores' tier). S5's nine-seasons view is its own window, every observation
# day since the holdout began (the realistic feeds are drawn from then on), as the stages report's figure shows it.
SHOWN = {"T2": {"s1": "previous_runs", "s5": "S5"}, "T1": {"s1": "previous_runs", "s5": "T1"},
         "T1-holdout": {"s1": "previous_runs", "s5": "T1-holdout"}, "T0": {"s1": None, "s5": None}}
S1_LEAD, S1_SERIES, S1_SUBSET = "1", "avg", "either_wet"   # one day ahead, the two-gauge mean: S1's own test (checked below)
# S5's served rules are the replay's rules under another name (shared/lineup.py names them alike; stages_s5.LIVE_V2, A7)
S5_ALIASES = {"live_v2": "basin_swap", "link_zone_v1": "link_zone_swap"}
S5_PRIMARY = ("link_zone_swap", "basin_swap")          # the two rules a figure pill can carry (stages_s5.PRIMARY)
S5_FIGURE_WORDS = {"basin_swap": "basin_swap = live_v2", "link_zone_swap": "link/zone injection"}   # stages_build.S5_RULE_WORDS
S5_NOTES = {"plain": {"short": "the baseline", "long": "No correction is the baseline every other rule is measured against."},
            "sample_swap": {"short": "0 by construction", "long": "The lab-result rule moves the water-quality stage only; the public number never reads a lab result, so its change here is 0 by construction."}}

# field → path inside a stage cell (scores.json <stage>/pooled/<entry>/<window>)
CELL = (("bss", ("bss",)), ("lo", ("ci", "bss", 0)), ("hi", ("ci", "bss", 1)), ("pr", ("pr",)), ("roc", ("roc",)),
        ("n", ("n",)), ("n_pos", ("n_pos",)), ("storms", ("n_storm_blocks",)), ("low_power", ("low_power",)), ("span", ("span",)))
# … inside an S1 model or floor block (s1_scores.json by_lead/1/avg/models/<model>, floor/<name>)
S1_CELL = (("mae", ("continuous", S1_SUBSET, "mae")), ("lo", ("ci", "continuous", S1_SUBSET, "mae", 0)),
           ("hi", ("ci", "continuous", S1_SUBSET, "mae", 1)), ("n", (f"n_{S1_SUBSET}",)), ("first", ("first",)), ("last", ("last",)))
# … inside an S5 rule's cell (scores.json s5/pooled/<feed>/<window>/<rule>)
S5_CELL = (("delta", ("delta_vs_plain", "delta")), ("lo", ("delta_vs_plain", "lo")), ("hi", ("delta_vs_plain", "hi")),
           ("verdict", ("delta_vs_plain", "verdict")), ("n", ("n",)), ("low_power", ("low_power",)), ("span", ("span",)))
S5_DRAW = (("delta", ("delta_vs_plain", "delta")), ("verdict", ("delta_vs_plain", "verdict")))
# … inside a figure pill: the realistic feeds' mean (scores.json figure(s)/…/m.s5/chained[/also/0])
S5_MEAN = (("delta", ("v",)), ("lo", ("lo",)), ("hi", ("hi",)), ("n", ("n",)), ("seeds", ("seeds",)),
           ("low_power", ("low_power",)), ("span", ("span",)))

PATHS = {
    "s1": f"stages/_s1/s1_scores.json by_lead/{S1_LEAD}/{S1_SERIES}/models/<part>: " + ", ".join(f"{k} = {'/'.join(map(str, p))}" for k, p in S1_CELL)
          + " (span = [first, last]); floor: floor/<floor.name>, the same fields; vs_live: primary/vs_served/<part>/verdict",
    "s2-s4": "stages/<from>/scores.json <stage>/pooled/oracle/<window>: " + ", ".join(f"{k} = {'/'.join(map(str, p))}" for k, p in CELL),
    "s5": "stages/<home[basins]>/scores.json s5/pooled/oracle/<window>/<part>: " + ", ".join(f"{k} = {'/'.join(map(str, p))}" for k, p in S5_CELL)
          + "; realistic.draws: s5/pooled/<feed>/<window>/<part>/delta_vs_plain; realistic.mean: <mean.src>: "
          + ", ".join(f"{k} = {'/'.join(map(str, p))}" for k, p in S5_MEAN),
    "chains": "stages/<set>/scores.json out/pooled/<L1|rain>/<window>, the s2-s4 fields; parts: manifest.json components",
}


def get(d, path):
    for k in path:
        d = d[int(k)] if isinstance(d, list) else d[k]
    return d


def pick(d: dict, fields) -> dict:
    return {k: get(d, p) for k, p in fields}


def cell(scores: dict, blk: str, entry: str, w: str) -> dict | None:
    """A stage cell's fields, or None when the window holds no score (not scored, or the live season not graded)."""
    c = (((scores.get(blk) or {}).get("pooled") or {}).get(entry) or {}).get(w)
    return pick(c, CELL) if isinstance(c, dict) and c.get("bss") is not None else None


# ── loading ──────────────────────────────────────────────────────────────────

def load() -> dict:
    """Every artifact the builder reads, checked: each set's manifest and scores name it, every score is under the
    served set's rules (scores under different rules are never compared), the served set is there."""
    served = json.loads(SERVED.read_text())["name"]
    sets = []
    for d in sorted(p for p in STAGES_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if not (d / "scores.json").exists():
            continue
        man, sc = json.loads((d / "manifest.json").read_text()), json.loads((d / "scores.json").read_text())
        if man.get("set") != d.name or sc.get("set") != d.name:
            raise ValueError(f"{d.name}: its manifest or scores name another set")
        if man.get("root") not in ROOTS:
            raise ValueError(f"{d.name}: unknown root {man.get('root')!r}")
        sets.append({"name": d.name, "manifest": man, "scores": sc})
    if served not in [s["name"] for s in sets]:
        raise KeyError(f"the served set {served!r} has no stage scores")
    sets.sort(key=lambda s: (s["name"] != served, ROOTS.index(s["manifest"]["root"]), s["name"]))
    s1 = json.loads(S1_SCORES.read_text())
    proto = sets[0]["scores"]["protocol"]
    for who, stamp in [(s["name"], s["scores"]["protocol"]) for s in sets] + [(s["name"] + "/manifest", s["manifest"]["protocol"]) for s in sets] + [("_s1", s1["protocol"])]:
        if stamp != proto:
            raise ValueError(f"{who}: scored under {stamp}, not the served set's rules {proto}")
    cands = [json.loads(p.read_text()) for p in sorted(CANDIDATES_DIR.glob("*/manifest.json"))]
    return {"served": served, "sets": sets, "s1": s1, "candidates": cands}


def geography_of(manifest: dict) -> str:
    return manifest.get("geography") or G.MISSING_STAMP     # a candidate without a stamp was built on the served basins


# ── the stages ───────────────────────────────────────────────────────────────

def stage_head(sid: str) -> dict:
    st = SP.STAGE[sid]
    return {"id": sid, "title": TITLES[sid], "question": st["question"], "oracle": st["oracle"]}


def s1_cell(block: dict) -> dict:
    c = pick(block, S1_CELL)
    return {**{k: c[k] for k in ("mae", "lo", "hi", "n")}, "span": [c["first"], c["last"]]}


def stage_s1(D: dict, geos: list) -> dict:
    """The weather models one day ahead on the two-gauge mean (S1's own test), and the floor: one gauge as a perfect
    forecast of the other (the first of the two directions, as the stages report shows it)."""
    S = D["s1"]
    live = D["sets"][0]["manifest"]["components"]["s1"]
    prim = S["primary"]
    if S["served_model"] != live or str(prim["lead"]) != S1_LEAD or prim["series"] != S1_SERIES or prim["subset"] != S1_SUBSET:
        raise ValueError("S1's artifact tests another model, lead or series than the builder shows")
    parts = []
    for m in S["models"]:
        part = {"id": m, "name": LU.words("s1", m), "basins": geos, "live": m == live,
                "score": s1_cell(get(S, ("by_lead", S1_LEAD, S1_SERIES, "models", m)))}
        if m != live:
            part["vs_live"] = get(S, ("primary", "vs_served", m, "verdict"))
        parts.append(part)
    name = next(iter(S["floor"]))
    return {**stage_head("s1"), "window": S["windows"]["previous_runs"], "parts": parts,
            "floor": {"name": name, **s1_cell(S["floor"][name])}}


def home(users: list, st: str, D: dict) -> dict:
    """The set a part's own-stage number comes from: the served set when it uses the part, else the set that scores
    it on the most windows, then the stages report's order (the oracle feeds every set the same truth, so they agree
    where they overlap)."""
    order = [s["name"] for s in D["sets"]]
    return min(users, key=lambda s: (s["name"] != D["served"], -sum(cell(s["scores"], st, "oracle", w) is not None for w, _ in WINDOWS),
                                     order.index(s["name"])))


def stage_parts(D: dict, st: str, geos: list) -> dict:
    """S2–S4: every part a scored set uses, with its oracle skill per window; S2 adds the overflow models on disk with
    no stage score (data/models/candidates/), "not scored yet". Ordered as shared/lineup.py lists them."""
    users = {}
    for s in D["sets"]:
        users.setdefault(s["manifest"]["components"][st], []).append(s)
    unscored = {}
    if st == "s2":
        for c in D["candidates"]:
            pid = c["stage1"]["name"]
            if pid not in users:
                unscored.setdefault(pid, set()).add(geography_of(c))
    live = D["sets"][0]["manifest"]["components"][st]
    order = list(LU.WORDS[st])
    parts = []
    for pid in sorted(set(users) | set(unscored), key=lambda i: (order.index(i) if i in order else len(order), i)):
        name = LU.words(st, pid)                               # raises for a part with no plain words
        if pid in users:
            src = home(users[pid], st, D)
            basins = {s["manifest"]["geography"] for s in users[pid]}
            scores = {w: cell(src["scores"], st, "oracle", w) for w, _ in WINDOWS}
            parts.append({"id": pid, "name": name, "basins": [g for g in geos if g in basins], "live": pid == live,
                          "from": src["name"], "scores": scores})
        else:
            parts.append({"id": pid, "name": name, "basins": [g for g in geos if g in unscored[pid]], "live": False,
                          "from": None, "scores": None})
    return {**stage_head(st), "parts": parts}


def s5_mean(scores: dict, sw: str, rule: str, own: str, perfect: dict | None) -> dict | None:
    """The realistic feeds' mean for ``rule`` on window ``sw``, where the stage build stored one: the figure pill of
    the set's own correction rule, and (on the served basins) link/zone injection beside it. Checked against the
    perfect-feed pill so the number read is the rule named."""
    base = ("figure", "m.s5") if sw == scores["figure_window"]["m.s5"] else ("figures", sw, "m.s5")
    try:
        fig = get(scores, base)
    except KeyError:
        return None
    if not isinstance(fig, dict) or not fig.get("chained") or not fig.get("oracle"):
        return None
    alt = next(r for r in S5_PRIMARY if r != own)
    if rule == own:
        path, pill, opill = base + ("chained",), fig["chained"], fig["oracle"]
        if S5_FIGURE_WORDS[rule] not in pill["window"] or S5_FIGURE_WORDS[rule] not in opill["window"]:
            raise ValueError(f"{'/'.join(base)}: the pill is not {rule}'s ({pill['window']!r})")
    elif rule == alt and fig["chained"].get("also") and fig["oracle"].get("also"):
        path, pill, opill = base + ("chained", "also", 0), fig["chained"]["also"][0], fig["oracle"]["also"][0]
        if S5_FIGURE_WORDS[rule] != pill["what"] or S5_FIGURE_WORDS[rule] != opill["what"]:
            raise ValueError(f"{'/'.join(path)}: the pill is {pill['what']!r}, not {rule}")
    else:
        return None
    if perfect is None or opill["v"] != perfect["delta"]:
        raise ValueError(f"{'/'.join(base)}: its perfect-feed pill is not {rule}'s on {sw}")
    return {**pick(pill, S5_MEAN), "src": "/".join(map(str, path))}


def stage_s5(D: dict, geos: list) -> dict:
    """S5: each correction rule's change in Brier against no correction (lower is better), on the perfect feed and the
    realistic feeds, per window. A rule is measured on a chain, so each basins' numbers come from one set: the served
    set on its basins, elsewhere the first scored set in the report's order (``home``)."""
    homes = {}
    for g in geos:
        homes[g] = next(s for s in D["sets"] if s["manifest"]["geography"] == g)
    windows = list(dict.fromkeys(v["s5"] for v in SHOWN.values() if v["s5"]))
    rules = []
    for g in geos:
        for r in homes[g]["scores"]["s5"]["pooled"]["oracle"][windows[0]]:
            if r not in rules:
                rules.append(r)
    live = S5_ALIASES.get(D["sets"][0]["manifest"]["components"]["s5"], D["sets"][0]["manifest"]["components"]["s5"])
    parts = []
    for r in rules:
        name = LU.words("s5", r)
        scores = {}
        for g in geos:
            sc = homes[g]["scores"]
            own = S5_ALIASES.get(homes[g]["manifest"]["components"]["s5"], homes[g]["manifest"]["components"]["s5"])
            pooled = sc["s5"]["pooled"]
            if r not in pooled["oracle"][windows[0]]:
                continue
            per = {}
            for sw in windows:
                c = (pooled["oracle"].get(sw) or {}).get(r)
                perfect = pick(c, S5_CELL) if c else None
                feeds = sorted((f for f in pooled if f.startswith("degraded:")), key=lambda f: int(f.split(":")[1]))
                draws = [{"feed": f, **pick(pooled[f][sw][r], S5_DRAW)} for f in feeds if r in (pooled[f].get(sw) or {})]
                mean = s5_mean(sc, sw, r, own, perfect)
                per[sw] = {"perfect": perfect, "realistic": {"mean": mean, "draws": draws} if draws else None}
            scores[g] = per
        part = {"id": r, "name": name, "basins": [g for g in geos if g in scores], "live": r == live, "scores": scores}
        same = sorted(a for a, v in S5_ALIASES.items() if v == r)
        if same:
            part["same_as"] = same
        if r in S5_NOTES:
            part["note"] = S5_NOTES[r]
        parts.append(part)
    return {**stage_head("s5"), "home": {g: homes[g]["name"] for g in geos}, "windows": windows, "parts": parts}


# ── chains, windows, the whole file ──────────────────────────────────────────

def lineup_name(parts: dict) -> str:
    """A whole forecast is named by its parts: S1 · S2 · S3 · S4 · S5 (Part B 36)."""
    return " · ".join(LU.words(c, parts[c]) for c in LU.STAGE_COLS)


def chains(D: dict) -> tuple[list, list]:
    """Every scored set as a chain (basins + S1–S5 → the public number one day ahead and with rain known, per
    window), then every lineup on disk with a day-by-day scorecard but no stage score."""
    cand_names = {c["name"] for c in D["candidates"]}
    out, seen = [], set()
    for s in D["sets"]:
        man, sc = s["manifest"], s["scores"]
        parts = {c: man["components"][c] for c in STAGES}
        out.append({"set": s["name"], "live": s["name"] == D["served"], "name": lineup_name(parts), "basins": man["geography"],
                    "parts": parts, "scorecard": s["name"] == D["served"] or s["name"] in cand_names,
                    "scores": {w: {"L1": cell(sc, "out", "L1", w), "rain": cell(sc, "out", "rain", w)} for w, _ in WINDOWS}})
        seen.add(LU.set_id({"geography": man["geography"], **parts}))
    unscored = []
    for c in D["candidates"]:
        lineup = dict(c["lineup"])                             # as it was made; its name is this lineup's id
        if LU.set_id(lineup) not in seen:
            seen.add(LU.set_id(lineup))
            parts = {k: lineup[k] for k in LU.STAGE_COLS}
            unscored.append({"set": c["name"], "name": lineup_name(parts), "basins": lineup["geography"], "parts": parts})
    return out, unscored


def windows(D: dict) -> list:
    """The four windows, with the served set's days for each; the live season says whether anything is graded yet."""
    sc, man = D["sets"][0]["scores"], D["sets"][0]["manifest"]
    out = []
    for key, name in WINDOWS:
        w = {"key": key, "name": name, "shows": SHOWN[key]}
        if key == "T0":
            graded = any(cell(s["scores"], "out", e, key) for s in D["sets"] for e in ("L1", "rain"))
            w.update(graded=graded, after=man["windows"]["freeze"], data_end=sc["as_of"])
        else:
            c = sc["s2"]["pooled"]["oracle"][key]
            w["span"] = c["span"]
            if key == "T2":
                w["seasons"] = c["n_seasons"]
        out.append(w)
    return out


def build(D: dict | None = None) -> dict:
    D = load() if D is None else D
    sv = D["sets"][0]["scores"]
    geos = list(dict.fromkeys([s["manifest"]["geography"] for s in D["sets"]] + [geography_of(c) for c in D["candidates"]]))
    chain_list, unscored = chains(D)
    stages = [stage_s1(D, geos), stage_parts(D, "s2", geos), stage_parts(D, "s3", geos), stage_parts(D, "s4", geos), stage_s5(D, geos)]
    return {"schema": SCHEMA, "served": D["served"], "protocol": sv["protocol"], "as_of": sv["as_of"],
            "bootstrap": {"n": sv["bootstrap"]["n"], "level": sv["bootstrap"]["level"]}, "paths": PATHS,
            "windows": windows(D), "basins": [{"id": g, "name": LU.words("geography", g)} for g in geos],
            "aliases": {"s5": S5_ALIASES}, "stages": stages, "chains": chain_list, "lineups": unscored}


def render(D: dict | None = None) -> str:
    return json.dumps(build(D), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def write(path: Path = OUT) -> Path:
    path.write_text(render(), encoding="utf-8")
    return path


def main(argv=None) -> None:
    """``export_stage_builder.py [PATH]``: write the builder's data (to data/models/stage_builder.json by default)."""
    argv = sys.argv[1:] if argv is None else argv
    p = write(Path(argv[0]) if argv else OUT)
    print(f"{p} ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
