"""stages_s2 — stage S2 out of fold: P(an overflow starts on D) and its size v̂ per
basin, on every day of every scored window, each from weights and volume heads
that never saw the day (P7a; STAGES_DESIGN.md Part B 1, Part C §3.2 and §8 P7
step 3; STAGES_PROTOCOL.md §2 windows and refit rules, §3 entries, §7
X-ALL-INSAMPLE and X-SEL).

A stage score is honest only if no fitted component saw the scored day. The
served artifacts' own holdout numbers fail that for S2's size (their volume
heads were fit through 2025-10-31), so every window refits everything S2 fits:

    tier        fold          weights                                  volume heads
    T1          final         the set's finals (fit through            the bundle's heads (same span)
                              2025-10-31), on 2025-11-01 → the freeze
    T1-holdout  pre_holdout   refit on days before 2023-07-01,         refit on events before 2023-07-01
                              scored 2023-07-01 → 2025-10-31
    T2          2016-17 …     leave one July–June season out of        refit per fold, same seasons
                2024-25       2016-17 … 2024-25, fit on the other
                              eight (never 2025-26, never the
                              Mar–Jun 2016 rows before the span)
    T0          final         T1's finals, not refit, on every day     T1's heads
                              after the freeze (protocol §2's
                              prospective window, shadow-run)

"The set's own design." A refit is sklearn.clone of the set's pickled pipeline
(its features, hinge or band design, family and C) fit on the set's own rows:
target_frame of the basin's rain source with the per-basin archive-label decision
(eval_report.stage1_archive_labels: Westside fits without the 2016-17 feed
labels), exactly as stage2_variants._refit_holdouts fits holdout siblings. A
volume head is clone of the bundle's head (its regressor, features and rain
source) on the event days with a filed volume, train_v4.fit_volume_heads' rows;
a fold under that function's 20-event floor raises (no head silently means v̂ = 0;
the declared fallback of Part B 7 is P8f's). The training record is the raw one
the sets were fit on (train_v4.build_dataset with no input rules); the entries'
inputs are whatever the caller passes. ``check_training_record`` proves it before
any fold is fit: each basin's rows match the set's recorded counts (events; the
pre-holdout season-CV n and positives), and for a refit family a clone fit on all
of them reproduces the set's final, so the rows, the archive-label rule, the
training end and the unmasked inputs are the set's own. p goes through
train_v4.calibrated, the scorecard's predict path with the final's dry-day offset.
A refit keeps that offset only where it is 0 by construction (every logit set):
a fitted offset is a mean over the final's training days, the scored ones
included, so a refit carrying one raises. v̂ goes through train_v4.predicted_volume
on the head's own rain source (a set's basin may read another gauge than its
head: GEO_V1's `southeast` key reads Downtown, its head the two-gauge mean).

GBM sets (family gb, e.g. gb_v1) score their finals only, T1 and T0: no LOSO, no
holdout refit (§8 P7). Asking for more raises.

Entries (protocol §3) are frames, built elsewhere (stages_entries): ``frames_by_entry``
= {entry id (exclusions.ENTRIES): {rain source: frame}}, a frame holding a date
column and the 19 features for every source the set reads. Each (tier, fold) is fit
once and predicts every entry. An entry predicts the days its frames hold inside a
window (a lead archive that starts in 2024 has no T2 rows before it); every source of
an entry must hold the same days there, and a missing feature value raises.

Rows: date, basin, entry, tier, fold, sel, p, v_hat, for the basin keys of the set's
geography (GEO_V1: westside, north_shore, central, southeast). The citywide model is
left out: compose_v2 (S3 → S4 → OUT, the GEO_V1 adapter included) reads basin p and
v̂ only, and citywide_p is a scorecard display. ``sel`` is exclusions.selection
(X-SEL, by date): holdout_selected 2023-07-01 → 2025-10-31 (T1-holdout, and the T2
seasons 2023-24 and 2024-25 inside it), post_selected 2025-11-01 → the freeze (T1).
T1 stops at the freeze: a later day is T0's, the same finals on the same kind of
inputs (stages_build also grades the served set's T0 as served, from forecast_history).

Westside's first scored season is 2017-18 (protocol §2: CIWQS Oceanside opens its
ledger in 2017-12, truth.ledger_start). Its 2016-17 fold is still fit and emitted, so
compose_v2 has every basin on every T2 day; none of those days is ledger_known, so
X-S2-ARCHIVE / X-S2-UNCOV leave all of them out of the score. The stamp records each
basin's first scored season.

Every set this module reads (served, candidates/) made its S2 choices (C grids,
design searches) on pre-holdout leave-one-season-out scores, so its T2 is stamped
"development (selection-contaminated)" (protocol §2, Part B 1). New candidates choose
by nested LOSO and live in stages_candidates/, which is not read here.

No T3 row: ``check_out_of_fold`` runs on every result and raises on a T3 tier, a row
outside its tier's window or season, a (basin, day) its fold's weights or head was
fit on, or a (date, basin, entry, tier) emitted twice. No module-level IO.
"""
from __future__ import annotations

import json
import pickle
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import clone

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import candidates as CAND  # noqa: E402  (served.json, candidate manifests and pickles)
import exclusions as X  # noqa: E402  (ENTRIES, X-SEL, the protocol's freeze date)
import train_v4 as T  # noqa: E402  (frames, rows, the predict path, the bundle's volume heads)
import truth as TR  # noqa: E402  (ledger_start: each basin's first scored T2 season)
from shared import clock  # noqa: E402
from shared import geography as G  # noqa: E402

TIERS = ("T1", "T1-holdout", "T2")       # protocol §2's fitted windows (what a spec is fit for); T3 never emitted
T0 = "T0"                                # protocol §2's prospective window: T1's finals on every day after the freeze
ALL_TIERS = TIERS + (T0,)
FINAL_TIERS = ("T1", T0)                 # scored by the finals, never refit: all a gb set has
ROOTS = ("served", "candidates")
FAMILIES = ("logit", "gb")
REFIT_FAMILIES = ("logit",)              # gb scores its finals only (§8 P7: no LOSO for the GBM)
TRAINED_THROUGH = T.TRAIN_END            # 2025-10-31: every scored set's finals and heads end here (T1)
HOLDOUT_START = T.HOLDOUT_START          # 2023-07-01
POST_START = X.POST_START                # 2025-11-01
T2_SEASONS = tuple(range(2016, 2025))    # July–June seasons 2016-17 … 2024-25 (train_v2.wet_season labels)
T2_LABEL = "development (selection-contaminated)"
FOLD_FINAL, FOLD_HOLDOUT = "final", "pre_holdout"
WEIGHTS_MIN_POSITIVES = 5                # _refit_holdouts / season_cv: fewer discharge days fit nothing
HEAD_MIN_EVENTS = 20                     # train_v4.fit_volume_heads: fewer known-volume events, no head
RECORD_TOLERANCE = 1e-9                  # check_training_record: a refit on the set's rows is its final (0.0 measured)
COLUMNS = ("date", "basin", "entry", "tier", "fold", "sel", "p", "v_hat")
SCHEMA = "bwtf.stages.s2/1"


def season_label(season: int) -> str:
    """2016 → '2016-17', the T2 fold that holds that July–June season out."""
    return f"{season}-{(season + 1) % 100:02d}"


def _season_of(label: str) -> int:
    m = re.fullmatch(r"(\d{4})-(\d{2})", label)
    if not m or (int(m.group(1)) + 1) % 100 != int(m.group(2)):
        raise ValueError(f"{label!r} is not a season fold like '2016-17'")
    return int(m.group(1))


# ── the set ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class S2Set:
    """A scored set's S2 components as its artifacts hold them."""
    name: str
    root: str
    family: str
    stage1: str
    geo: G.Geography
    trained_through: pd.Timestamp
    models: dict          # basin key → pickle dict: model, features, calibration_offset, rain_source, …
    heads: dict           # basin key → volume head dict: model, features, rain_source, n_events, …
    use_archive: dict     # basin name → the trainer's archive-label decision
    record: dict          # basin key → the descriptor's per_basin entry (n_events, season_cv_pre_holdout, …)

    @property
    def keys(self) -> tuple:
        return self.geo.keys

    @property
    def sources(self) -> tuple:
        """Every rain source the set reads: its basins' and its heads'."""
        return tuple(sorted({self.models[k]["rain_source"] for k in self.keys}
                            | {self.heads[k]["rain_source"] for k in self.keys}))

    def features(self, source: str) -> list:
        """Columns an entry frame of `source` must hold: the features of every model and
        head that reads it, plus rain_3d_cum (train_v4.calibrated's dry-day factor)."""
        cols = []
        for k in self.keys:
            if self.models[k]["rain_source"] == source:
                cols += list(self.models[k]["features"]) + ["rain_3d_cum"]
            if self.heads[k]["rain_source"] == source:
                cols += list(self.heads[k]["features"])
        return list(dict.fromkeys(cols))


def load_set(set_name: str, root: str = "served") -> S2Set:
    """The set's basin models (finals) and the volume heads S2 shares, checked against
    its descriptor: served.json for root 'served' (the name must be the one it names),
    the candidate's manifest for root 'candidates'. Raises on an unknown root or set, a
    family outside FAMILIES or a pickled estimator of another family, a set not trained
    through 2025-10-31 or with another holdout start, a missing pickle or head, a rain
    source the pickle omits or the descriptor reads otherwise, a basin with no recorded
    per_basin entry, or a basin with no archive-label decision (never a default)."""
    if root not in ROOTS:
        raise ValueError(f"unknown root {root!r}; known: {ROOTS} (stage candidates are P8's)")
    import leaderboard  # noqa: F401  (logit pipelines pickle leaderboard.add_hinges / add_bands)
    if root == "served":
        desc = CAND.served_info()
        if set_name != desc["name"]:
            raise ValueError(f"the served set is {desc['name']!r} (served.json), not {set_name!r}")
        geo = G.stamped(desc)
        models = {}
        for key in geo.keys:
            path = T.SERVE_DIR / f"{key}_model.pkl"
            if not path.exists():
                raise FileNotFoundError(f"served bundle has no {path.name}")
            with open(path, "rb") as f:
                models[key] = pickle.load(f)
        stage1 = desc.get("stage1") or set_name
    else:
        mpath = CAND.candidate_dir(set_name) / "manifest.json"
        if not mpath.exists():
            raise KeyError(f"no candidate {set_name!r} under {CAND.CANDIDATES_DIR}")
        desc = json.loads(mpath.read_text())
        geo = G.stamped(desc)
        loaded = CAND.load_models(set_name)
        missing = [k for k in geo.keys if k not in loaded]
        if missing:
            raise FileNotFoundError(f"candidate {set_name!r} has no model for {missing}")
        models = {k: loaded[k] for k in geo.keys}
        stage1 = (desc.get("stage1") or {}).get("name") or set_name
    if geo.version != "geo_v1":
        raise ValueError(f"{set_name} is a {geo.version} set; served and candidates/ hold GEO_V1 sets only (Part B 13)")
    family = desc.get("family")
    fams = {m.get("family", family) for m in models.values()}      # gb_v1's pickles predate the family field
    if family not in FAMILIES or fams != {family}:
        raise ValueError(f"{set_name}: family {family!r} with pickles of {sorted(map(str, fams))}; known: {FAMILIES}")
    for key, m in models.items():                                   # the pickled estimator is the family it claims
        is_logit = hasattr(m["model"], "named_steps") and "lr" in m["model"].named_steps
        if is_logit != (family == "logit"):
            raise ValueError(f"{set_name} {key}: a {type(m['model']).__name__} in a {family} set")
    for field in ("trained_through", "holdout_start", "rain_sources", "per_basin"):
        if not desc.get(field):
            raise ValueError(f"{set_name}: the descriptor states no {field}")
    tt = pd.Timestamp(desc["trained_through"])
    if tt != TRAINED_THROUGH:
        raise ValueError(f"{set_name} was trained through {tt.date()}; T1 (protocol §2) needs {TRAINED_THROUGH.date()}")
    if pd.Timestamp(desc["holdout_start"]) != HOLDOUT_START:
        raise ValueError(f"{set_name}: holdout_start {desc['holdout_start']} is not {HOLDOUT_START.date()}")
    for key in geo.keys:
        if "rain_source" not in models[key]:
            raise KeyError(f"{set_name} {key}: the pickle states no rain_source")
        mine, theirs = models[key]["rain_source"], desc["rain_sources"].get(key)
        if theirs != mine:
            raise ValueError(f"{set_name} {key}: the descriptor reads {theirs!r}, the pickle {mine!r}")
        if key not in desc["per_basin"]:
            raise KeyError(f"{set_name}: the descriptor records no per_basin entry for {key}")
    by_name, _ = T.stage2_from_served()          # the volume heads every GEO_V1 set shares
    heads = {}
    for key in geo.keys:
        name = geo.basin(key).name
        if name not in by_name:
            raise FileNotFoundError(f"no volume head for {key} in the served bundle: v̂ has no model")
        heads[key] = by_name[name]
    decisions = json.loads((T.SERVE_DIR / "eval_report.json").read_text()).get("stage1_archive_labels") or {}
    unknown = [b.name for b in geo.basins if b.name not in decisions]
    if unknown:
        raise KeyError(f"eval_report.json states no archive-label decision for {unknown}")
    use_archive = {b.name: bool(decisions[b.name]) for b in geo.basins}
    record = {k: desc["per_basin"][k] for k in geo.keys}
    return S2Set(set_name, root, family, stage1, geo, tt, models, heads, use_archive, record)


def training_frames(s2set: S2Set) -> dict:
    """{source: frame}: the raw record the set was trained on (no input rules), through 2025-10-31."""
    return T.build_dataset(sources=list(s2set.sources))[0]


def check_training_record(s2set: S2Set, train: dict) -> None:
    """Raise unless ``train`` holds the record the set was fit on, basin by basin: set_rows
    carries the set's recorded discharge days (per_basin n_events) and, where recorded,
    its pre-holdout season-CV rows and positives (the archive-label rule and the coverage
    show there); for a refit family a clone fit on all of set_rows reproduces the final
    (the training end and the unmasked inputs show there). Every refit starts from these
    rows, so a fold is the set's design on fewer days only if they are the set's own."""
    for key in s2set.keys:
        rows, rec, m = set_rows(s2set, train, key), s2set.record[key], s2set.models[key]
        pre = rows[rows["date"] < HOLDOUT_START]
        got = {"n_events": int(rows["y"].sum())}
        want = {"n_events": rec.get("n_events")}
        cv = rec.get("season_cv_pre_holdout") or {}
        if cv:
            got |= {"n": len(pre), "pos": int(pre["y"].sum())}
            want |= {"n": cv.get("n"), "pos": cv.get("pos")}
        if got != want:
            raise ValueError(f"{s2set.name} {key}: the training record gives {got}, the set recorded {want}")
        if s2set.family in REFIT_FAMILIES:
            Xr = rows[m["features"]]
            again = clone(m["model"]).fit(Xr, rows["y"]).predict_proba(Xr)[:, 1]
            gap = float(np.abs(again - m["model"].predict_proba(Xr)[:, 1]).max())
            if not gap <= RECORD_TOLERANCE:
                raise ValueError(f"{s2set.name} {key}: a refit on the training record misses the final by {gap:.1e} "
                                 "(not the rows, the end or the inputs the set was fit on)")


def _frame(frames: dict, source: str, what: str) -> pd.DataFrame:
    if source not in frames:
        raise KeyError(f"{what} lacks rain source {source!r} (has {sorted(frames)})")
    return frames[source]


def set_rows(s2set: S2Set, train: dict, key: str) -> pd.DataFrame:
    """The set's own training rows for one basin, as stage2_variants._refit_holdouts takes
    them: covered days of the basin's rain-source frame, without archive labels where the
    trainer dropped them, through the set's training end."""
    name = s2set.geo.basin(key).name
    sub = T.target_frame(_frame(train, s2set.models[key]["rain_source"], "the training record"), name)
    if not s2set.use_archive[name]:
        sub = sub[sub[f"{name}_label_source"] != "poobot"]
    return sub[sub["date"] <= s2set.trained_through]


def head_rows(s2set: S2Set, train: dict, key: str) -> pd.DataFrame:
    """The volume head's training rows, train_v4.fit_volume_heads': event days with a filed volume."""
    name = s2set.geo.basin(key).name
    sub = T.target_frame(_frame(train, s2set.heads[key]["rain_source"], "the training record"), name)
    ev = sub[(sub["y"] == 1) & (sub[f"{name}_volume_known"] == 1)]
    return ev[ev["date"] <= s2set.trained_through]


# ── folds ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Fold:
    """One fitted (tier, fold): the window it scores, its models, the days they saw."""
    tier: str
    fold: str
    start: pd.Timestamp
    end: pd.Timestamp
    season: int | None    # T2: the held-out season
    weights: dict         # basin key → model dict for train_v4.calibrated
    heads: dict           # basin key → head dict for train_v4.predicted_volume
    seen: dict            # basin key → DatetimeIndex: every day its weights or head was fit on
    info: dict            # basin key → counts and spans for the stamp


def _plan(tiers: tuple) -> list:
    """(tier, fold, first scored day, last scored day, held-out season, training-row filter). T1 stops at the freeze;
    T0 is T1's fold, not refit, from the day after it to whatever the inputs reach (no last day of its own)."""
    out = []
    for tier in ALL_TIERS:
        if tier not in tiers:
            continue
        if tier == "T1":
            out.append((tier, FOLD_FINAL, POST_START, X.freeze_date(), None, None))
        elif tier == T0:
            out.append((tier, FOLD_FINAL, X.freeze_date() + pd.Timedelta(days=1), pd.Timestamp.max, None, None))
        elif tier == "T1-holdout":
            out.append((tier, FOLD_HOLDOUT, HOLDOUT_START, TRAINED_THROUGH, None,
                        lambda f: f["date"] < HOLDOUT_START))
        else:
            for s in T2_SEASONS:
                others = [x for x in T2_SEASONS if x != s]
                out.append((tier, season_label(s), pd.Timestamp(s, 7, 1), pd.Timestamp(s + 1, 6, 30), s,
                            lambda f, o=tuple(others): f["season"].isin(o)))
    return out


def _check_tiers(s2set: S2Set, tiers) -> tuple:
    tiers = tuple(tiers)
    if not tiers:
        raise ValueError("no tiers asked for")
    if X.IN_SAMPLE in tiers:
        raise ValueError("X-ALL-INSAMPLE: T3 (the scored weights saw the day) is never emitted")
    bad = [t for t in tiers if t not in ALL_TIERS]
    if bad:
        raise ValueError(f"unknown tiers {bad}; known: {ALL_TIERS}")
    if s2set.family not in REFIT_FAMILIES and set(tiers) - set(FINAL_TIERS):
        raise ValueError(f"{s2set.name} is a {s2set.family} set: its finals only, {FINAL_TIERS} (no LOSO, no holdout refit "
                         "for the GBM; §8 P7)")
    return tiers


def _span(d) -> list:
    d = pd.DatetimeIndex(d)
    return [str(d.min().date()), str(d.max().date())] if len(d) else []


def fit_fold(s2set: S2Set, train: dict, tier: str, fold: str, start, end, season, keep) -> Fold:
    """Fit one (tier, fold). ``keep`` filters the set's rows to the fold's training days
    (None = the set's own finals and the bundle's heads, T1). A refit needs a refit
    family (the GBM is T1 only) and a calibration offset that is 0 by construction:
    the final's fitted offset would carry its whole training span into the fold."""
    if keep is not None and s2set.family not in REFIT_FAMILIES:
        raise ValueError(f"{s2set.name} is a {s2set.family} set: no refit for {tier} {fold} (T1 only; §8 P7)")
    weights, heads, seen, info = {}, {}, {}, {}
    for key in s2set.keys:
        m, h = s2set.models[key], s2set.heads[key]
        name = s2set.geo.basin(key).name
        rows, ev = set_rows(s2set, train, key), head_rows(s2set, train, key)
        if keep is None:
            weights[key], heads[key] = m, h
        else:
            if m["calibration_offset"] != 0.0:
                raise ValueError(f"{s2set.name} {key}: dry-day offset {m['calibration_offset']} was fit on the final's "
                                 f"span; {tier} {fold} would score days it saw")
            rows, ev = rows[keep(rows)], ev[keep(ev)]
            if rows["y"].sum() < WEIGHTS_MIN_POSITIVES:
                raise ValueError(f"{s2set.name} {tier} {fold} {key}: {int(rows['y'].sum())} discharge days to fit on")
            if len(ev) < HEAD_MIN_EVENTS:
                raise ValueError(f"{s2set.name} {tier} {fold} {key}: {len(ev)} known-volume events, under the head's "
                                 f"{HEAD_MIN_EVENTS}-event floor (no declared fallback here; Part B 7)")
            weights[key] = {**m, "model": clone(m["model"]).fit(rows[m["features"]], rows["y"])}
            yv = np.log1p(ev[f"{name}_volume_mg"])
            hm = clone(h["model"]).fit(ev[h["features"]], yv)
            heads[key] = {**h, "model": hm, "n_events": len(ev),
                          "resid_std": round(float((yv - hm.predict(ev[h["features"]])).std()), 3)}
        seen[key] = pd.DatetimeIndex(rows["date"]).union(pd.DatetimeIndex(ev["date"]))
        info[key] = {"rows": int(len(rows)), "positives": int(rows["y"].sum()), "head_events": int(len(ev)),
                     "span": _span(seen[key]), "seasons": sorted(int(s) for s in set(rows["season"]) | set(ev["season"]))}
    return Fold(tier, fold, pd.Timestamp(start), pd.Timestamp(end), season, weights, heads, seen, info)


# ── fit, predict, check ─────────────────────────────────────────────────────

def first_scored_season(s2set: S2Set, key: str) -> int:
    """The first T2 season holding a ledger_known day of the basin (protocol §2: Westside 2017-18)."""
    start = TR.ledger_start(s2set.geo.basin(key).facility)
    return max(T2_SEASONS[0], int(start.year if start.month >= 7 else start.year - 1))


_TIER_TEXT = {
    "T1": {"name": "post-training", "weights": "the set's finals, fit through 2025-10-31",
           "volume_heads": "the bundle's heads, fit through 2025-10-31", "sel": "post_selected",
           "use": "confirmation (non-inferiority)"},
    "T1-holdout": {"name": "holdout", "weights": "refit on days before 2023-07-01 (the set's rows, design, family and C)",
                   "volume_heads": "refit on events before 2023-07-01", "sel": "holdout_selected", "use": "development only"},
    "T2": {"name": "cross-season", "weights": "refit per held-out season on the other seasons of 2016-17 … 2024-25",
           "volume_heads": "refit per fold on the same seasons", "label": T2_LABEL,
           "use": "development, calibration reference, power"},
    T0: {"name": "prospective", "weights": "T1's finals, not refit, on every day after the freeze (shadow-run)",
         "volume_heads": "T1's heads", "use": "confirmation: OUT's window is T1 post ∪ T0 (protocol §8)"},
}


@dataclass(frozen=True)
class Fitted:
    """A set fit for its tiers: predict any number of entries with the same folds."""
    set: S2Set
    tiers: tuple
    folds: tuple
    stamp: dict

    def predict(self, frames_by_entry: dict) -> pd.DataFrame:
        return _predict(self, frames_by_entry)


def fit(set_name: str, root: str = "served", tiers=TIERS, train_frames: dict | None = None) -> Fitted:
    """Load the set and fit every (tier, fold) once. ``train_frames`` = {source: frame} of
    the training record (default: training_frames, the raw record through 2025-10-31);
    check_training_record raises unless it is the record the set was fit on."""
    s2set = load_set(set_name, root)
    tiers = _check_tiers(s2set, tiers)
    train = training_frames(s2set) if train_frames is None else train_frames
    check_training_record(s2set, train)
    t0 = time.time()
    folds = tuple(fit_fold(s2set, train, *p) for p in _plan(tiers))
    stamp = {"schema": SCHEMA, "stage": "s2", "set": s2set.name, "root": s2set.root, "stage1": s2set.stage1,
             "family": s2set.family, "geography": s2set.geo.version, "basins": list(s2set.keys),
             "citywide": "not a stage row: compose_v2 reads basin p and v_hat only; citywide_p is a scorecard display",
             "protocol": X.protocol_stamp(), "built_at": clock.utc_iso(),
             "trained_through": str(s2set.trained_through.date()), "holdout_start": str(HOLDOUT_START.date()),
             "post_start": str(POST_START.date()), "freeze": str(X.freeze_date().date()),
             "train_input_rules": [], "rain_sources": {k: s2set.models[k]["rain_source"] for k in s2set.keys},
             "head_sources": {k: s2set.heads[k]["rain_source"] for k in s2set.keys},
             "tiers": {t: dict(_TIER_TEXT[t]) for t in tiers},
             "folds": [{"tier": f.tier, "fold": f.fold, "scores": [str(f.start.date()), str(f.end.date())], "train": f.info}
                       for f in folds],
             "fit_seconds": round(time.time() - t0, 1)}
    if "T2" in tiers:
        stamp["tiers"]["T2"]["seasons"] = [season_label(s) for s in T2_SEASONS]
        stamp["first_scored_season"] = {k: season_label(first_scored_season(s2set, k)) for k in s2set.keys}
    return Fitted(s2set, tiers, folds, stamp)


def _entry_frames(s2set: S2Set, entry: str, frames) -> dict:
    """{source: frame indexed by date, the columns the set reads} for one entry."""
    if entry not in X.ENTRIES:
        raise KeyError(f"unknown entry {entry!r}; known: {X.ENTRIES}")
    if not isinstance(frames, dict) or not frames:
        raise TypeError(f"entry {entry!r} must map rain sources to frames")
    out = {}
    for src in s2set.sources:
        f = _frame(frames, src, f"entry {entry!r}")
        if not isinstance(f, pd.DataFrame) or "date" not in f.columns:
            raise TypeError(f"entry {entry!r} {src!r}: a DataFrame with a date column")
        cols = s2set.features(src)
        missing = [c for c in cols if c not in f.columns]
        if missing:
            raise KeyError(f"entry {entry!r} {src!r} lacks {missing}")
        d = pd.DatetimeIndex(pd.to_datetime(f["date"]))
        if d.isna().any() or not (d == d.normalize()).all() or d.duplicated().any():
            raise ValueError(f"entry {entry!r} {src!r}: dates must be unique whole days")
        out[src] = pd.DataFrame(f[cols].to_numpy(dtype=float), index=d, columns=cols).sort_index()
    return out


def _predict(fitted: Fitted, frames_by_entry: dict) -> pd.DataFrame:
    s2set = fitted.set
    if not isinstance(frames_by_entry, dict) or not frames_by_entry:
        raise ValueError("frames_by_entry: {entry: {rain source: frame}}, at least one entry")
    chunks = []
    for entry, frames in frames_by_entry.items():
        ef = _entry_frames(s2set, entry, frames)
        n_entry = 0
        for f in fitted.folds:
            days = None
            for src, fr in ef.items():
                d = fr.index[(fr.index >= f.start) & (fr.index <= f.end)]
                if days is None:
                    days = d
                elif not d.equals(days):
                    raise ValueError(f"entry {entry!r}: its sources hold different days in {f.tier} {f.fold}")
            if not len(days):
                continue
            for key in s2set.keys:
                m, h = f.weights[key], f.heads[key]
                Xp = ef[m["rain_source"]].loc[days]
                Xv = ef[h["rain_source"]].loc[days]
                for what, Z, cols in (("features", Xp, m["features"] + ["rain_3d_cum"]), ("head features", Xv, h["features"])):
                    bad = ~np.isfinite(Z[cols].to_numpy())
                    if bad.any():
                        r, c = np.argwhere(bad)[0]
                        raise ValueError(f"entry {entry!r} {key} {what}: {cols[c]} is missing on {days[r].date()}")
                p = T.calibrated(m, Xp)
                v = T.predicted_volume(h, Xv)
                if not (np.isfinite(p).all() and np.isfinite(v).all()):
                    raise ValueError(f"{s2set.name} {f.tier} {f.fold} {key} {entry}: a non-finite p or v_hat")
                chunks.append(pd.DataFrame({"date": days, "basin": key, "entry": entry, "tier": f.tier, "fold": f.fold,
                                            "p": p, "v_hat": v}))
                n_entry += len(days)
        if not n_entry:
            raise ValueError(f"entry {entry!r} holds no day of {fitted.tiers}")
    out = pd.concat(chunks, ignore_index=True)
    out["sel"] = X.selection(out["date"], s2set.geo)
    out = out[list(COLUMNS)]
    check_out_of_fold(out, fitted)
    out.attrs["stamp"] = {**fitted.stamp, "entries": list(frames_by_entry)}
    return out


def check_out_of_fold(rows: pd.DataFrame, fitted: Fitted) -> None:
    """Raise unless every row is out of fold: tier in ALL_TIERS (never T3), fold one that was
    fit, date inside the fold's window (T1 and T0 after the training end, T2 in its season),
    and no (basin, date) its fold's weights or volume head was fit on; sel = X-SEL's."""
    missing = [c for c in COLUMNS if c not in rows.columns]
    if missing:
        raise ValueError(f"rows lack {missing}")
    tiers = set(rows["tier"].astype(str))
    if X.IN_SAMPLE in tiers:
        raise ValueError(f"X-ALL-INSAMPLE: {int((rows['tier'] == X.IN_SAMPLE).sum())} rows are T3; T3 is never emitted")
    if tiers - set(ALL_TIERS):
        raise ValueError(f"unknown tiers {sorted(tiers - set(ALL_TIERS))}; known: {ALL_TIERS}")
    dup = rows.duplicated(["date", "basin", "entry", "tier"])
    if dup.any():                                   # one fold per day within a tier (protocol §2)
        r = rows[dup].iloc[0]
        raise ValueError(f"{r['tier']} {r['basin']} {r['entry']}: {pd.Timestamp(r['date']).date()} is emitted twice")
    by = {(f.tier, f.fold): f for f in fitted.folds}
    for (tier, fold, basin), g in rows.groupby(["tier", "fold", "basin"], sort=False):
        f = by.get((tier, fold))
        if f is None:
            raise ValueError(f"rows of {tier} fold {fold!r}, which was never fit")
        if basin not in f.seen:
            raise KeyError(f"{basin!r} is not a basin of {fitted.set.name} ({fitted.set.geo.version})")
        d = pd.DatetimeIndex(g["date"])
        if (d < f.start).any() or (d > f.end).any():
            raise ValueError(f"{tier} {fold} {basin}: a row on {d[(d < f.start) | (d > f.end)][0].date()}, outside "
                             f"{f.start.date()} → {f.end.date()}")
        if tier in FINAL_TIERS and (d <= fitted.set.trained_through).any():
            raise ValueError(f"{tier} {basin}: a row on or before the training end {fitted.set.trained_through.date()}")
        if tier == "T2":
            s = _season_of(fold)
            if s != f.season or (T.wet_season(pd.Series(d)) != s).any():
                raise ValueError(f"T2 fold {fold} {basin}: a row outside season {fold}")
        hit = d.isin(f.seen[basin])
        if hit.any():
            raise ValueError(f"X-ALL-INSAMPLE: {tier} fold {fold} {basin} scores {d[hit][0].date()}, a day its weights "
                             "or volume head was fit on")
    sel = X.selection(rows["date"], fitted.set.geo)
    if not (rows["sel"].astype(str).to_numpy() == sel).all():
        raise ValueError("a row's sel disagrees with X-SEL (exclusions.selection)")
