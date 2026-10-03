"""stages_candidates — the geography-aware saver and loader of stage candidates
(P8; STAGES_DESIGN.md Part B 13, Part C §7 "SFPUC4 stage candidates", §2.6
stamps and loader asserts).

Why a second saver. ``candidates.save_candidate`` writes GEO_V1 sets: it drops
the geography stamp, gives a basin key it does not know the 'avg' rain source,
and puts the set in data/models/candidates/, which ``candidates.list_candidates``
scans and the Model check draws with GEO_V1's hard-coded basins (an SFPUC4 set
has no `southeast` model and a `central` that means something else). Stage
candidates live under data/models/stages_candidates/<name>/ instead, a directory
nothing on the served path reads, until P9 makes the Model check read basin keys
and names from the artifact (design §7).

    stages_candidates/<name>/
        manifest.json                   schema, name, geography, pipeline, basins, components{s1..s5},
                                        stamps{stage: {component, protocol, saved_at}}, files{file: sha256},
                                        s2 / s3 / s4 sections (what each component's writer recorded)
        {basin}_model.pkl               S2 finals, one per basin of the geography (+ citywide, optional)
        {basin}_volume.pkl              S2 volume heads
        {basin}_model.pre_holdout.pkl   S2 holdout siblings (optional; fit on days before 2023-07-01)
        {basin}_volume.pre_holdout.pkl
        s3_links.json                   S3 spec (design §7 schema)
        s4_quality.json                 S4 spec (design §7 schema)
    stages_candidates/_<anything>/      working directories (the S2 bake-off's results); never a set

Writers go through ``save_component(name, kind, payload)``, one stage component
at a time, so the S2, S3 and S4 pieces of one set can be built by different
programs: kind 's2' (pickles), 's3_links', 's4_quality' (spec files), 's1' and
's5' (a component name only; their parts are code, not fitted files). Every
pickle dict and spec file is stamped with the set's geography and pipeline
(``stages_v1``), the set's name and its component; the manifest is rewritten
last, with the sha256 of every file, so a half-written save is caught by the
loader. The first component saved fixes the set's geography; a later one of
another geography raises.

``load_set(name)`` reads it back and asserts, with no ``.get(default)`` on a
stamp: the manifest's schema and pipeline; every listed file present with its
sha256 and no unlisted file; every pickle and spec stamped with the manifest's
geography, pipeline and name; model keys == volume keys == the geography's basin
keys; the S3/S4 specs pass compose_v2.check_s3_spec / check_s4_spec (the readers
S3 and S4 compose through: the geography's links, zones and units), with the
saver's own stamps (set, component) set aside. Chase's rule for new S2 models holds at save time:
every fitted weight (any ``coef_`` in a model or a head) is ≥ 0 ("no odd
weights"). An S2 section states the fits' windows (``trained_through``, and
``holdout_start`` with holdout siblings), and every model or head that records
the days it was fit on (``span``) must end inside them: a final on or before
trained_through, a sibling before holdout_start (Part B 1). Part B 7 holds at
save time too: a head fit on fewer than 20 known-volume events must be the
declared fallback (``FALLBACK_KIND``), never a basin's own head.

**Assembling a candidate (``assemble``; P8).** Once stages_s2_sfpuc4 has saved a
set's S2, ``assemble(name)`` completes it, each stage fit per fold on the set's own
parts: S3 links on its S2 (stages_s3_links.candidate_s2 → run → write), S4 v3 at
that S3's link shares φ with its own fold v̂ (stages_s4_v3.run(s3_set=name,
s2_source=…)), S1 the served weather model (S1 is set-independent), and S5 by
``s5_choice``: the correction variant the set's geography can replay that is
better than no correction on the perfect feed (a comparison that reads the feed's
own silence never counts, Part B 9) and on every degraded seed, read from the
served set's current written S5 scores on S5's window before post-training (its
T1-holdout days, 2023-07-01 → 2025-10-31: protocol §2, a design choice never reads
the T1 post-training days that confirm it); else link_zone_swap, protocol §8's S5
primary winner. stages_build then scores it (``--root stages_candidates``).

    venv/bin/python features/forecast/src/models/stages_candidates.py --assemble NAME [--served-scores PATH] [--s5-only]

Unpickling imports what the pickles reference (leaderboard for the bands and
NonNegLogit, stages_s2_sfpuc4 for its hinge transform), so this module puts its
own directory on sys.path. No module-level IO; ``root`` overrides the directory
(tests write to a temporary one, outside the repository); inside the repository
only stages_candidates/ itself is a root, so a stage candidate never lands
beside the served bundle, in candidates/, or inside another set or a '_…'
working directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (REPO, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import candidates as CAND  # noqa: E402  (SERVE_DIR; its CANDIDATES_DIR is the directory this one is not)
from shared import clock  # noqa: E402
from shared import geography as G  # noqa: E402

ROOT = CAND.SERVE_DIR / "stages_candidates"
SCHEMA = "bwtf.stages_candidates/1"
PIPELINE = "stages_v1"                       # design §2.6: two_stage_v1 (served) | stages_v1
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")   # '_…' directories are working space, never a set
KINDS = ("s1", "s2", "s3_links", "s4_quality", "s5")
STAGE_OF_KIND = {"s1": "s1", "s2": "s2", "s3_links": "s3", "s4_quality": "s4", "s5": "s5"}
SPEC_FILES = {"s3_links": "s3_links.json", "s4_quality": "s4_quality.json"}
FOLDS = {"final": "", "pre_holdout": ".pre_holdout"}   # file suffix of each S2 fit: finals, holdout siblings
S2_PARTS = {"models": ("model", "final"), "volume": ("volume", "final"),
            "holdout_models": ("model", "pre_holdout"), "holdout_volume": ("volume", "pre_holdout")}
MODEL_FIELDS = ("model", "features", "rain_source", "calibration_offset", "family")
HEAD_FIELDS = ("model", "features", "rain_source", "target", "n_events", "kind")
HEAD_TARGET = "log1p_volume_mg"              # train_v4.fit_volume_heads' target; train_v4.predicted_volume inverts it
CITYWIDE = "citywide"
# design §7 spec schemas: the keys each file must hold; compose_v2.check_s3_spec / check_s4_spec rule the rest
S3_KEYS = ("geography", "component", "links", "union", "cofire", "fit")
S4_KEYS = ("geography", "component", "kind", "background", "buckets", "monotone", "sources", "fit")
SAVER_KEYS = ("set", "component")            # the saver's stamps, which compose_v2's readers do not know
WEIGHT_TOL = 1e-12                           # a solver's −0.0 / round-off is not a negative weight
FALLBACK_KIND = "pooled_bayside_loglinear"   # Part B 7's declared volume fallback (stages_s2_sfpuc4 fits it)


# ── plumbing ───────────────────────────────────────────────────────────────

def _root(root) -> Path:
    """ROOT, or the override: a directory outside the repository (tests' temporary ones) or ROOT itself. Any other
    directory of the repository raises (Part B 13: stage candidates live under stages_candidates/ only, never in
    data/models/ beside the served bundle or in candidates/, which the Model check lists, and never nested inside
    a set or a '_…' working directory, where list_sets would not see them)."""
    if root is None:
        return ROOT
    r = Path(root).resolve()
    if r.is_relative_to(REPO.resolve()) and r != ROOT.resolve():
        raise ValueError(f"{root}: stage candidates are saved under {ROOT.relative_to(REPO)}/ only (Part B 13)")
    return Path(root)


def valid_name(name: str) -> bool:
    return isinstance(name, str) and bool(NAME_RE.match(name))


def set_dir(name: str, root=None) -> Path:
    """The set's directory; a bad name (or a '_' working directory) raises."""
    if not valid_name(name):
        raise ValueError(f"bad stage candidate name {name!r} (lower case, digits, '_' or '-', not starting with '_')")
    return _root(root) / name


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic(path: Path, data: bytes) -> None:
    """Write through a temporary file in the same directory, then rename: a crash leaves the old file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)          # mkstemp's 0600 → what a plain write would have made
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _json_bytes(obj) -> bytes:
    try:
        return (json.dumps(obj, indent=1, allow_nan=False) + "\n").encode()
    except (TypeError, ValueError) as e:
        raise TypeError(f"not JSON-safe (NaN, inf or a non-JSON type): {e}") from None


def _geo(version) -> G.Geography:
    if not isinstance(version, str):
        raise TypeError(f"geography must be a version string, not {type(version).__name__}")
    return G.get(version)            # KeyError on an unknown version: there is no default


def _weights(est) -> list:
    """(name, coef array) of every fitted linear step inside a model: a Pipeline's steps, recursively."""
    out = []
    steps = getattr(est, "steps", None)
    if steps:
        for n, s in steps:
            out += [(f"{n}.{m}", c) for m, c in _weights(s)]
    elif hasattr(est, "coef_"):
        out.append(("coef_", np.asarray(est.coef_, dtype=float)))
    return out


def check_nonnegative(est, what: str) -> None:
    """Chase's rule for new S2 models (2026-09-30, "no odd weights"): every fitted weight ≥ 0, so more rain
    never lowers the risk or the size. A NaN weight raises too."""
    for name, c in _weights(est):
        if not np.isfinite(c).all():
            raise ValueError(f"{what}: {name} holds a non-finite weight")
        if (c < -WEIGHT_TOL).any():
            raise ValueError(f"{what}: {name} holds a negative weight ({float(c.min()):.3g}); new S2 models keep every "
                             "weight ≥ 0")


# ── the manifest ───────────────────────────────────────────────────────────

def _new_manifest(name: str, geo: G.Geography) -> dict:
    now = clock.utc_iso()
    return {"schema": SCHEMA, "name": name, "geography": geo.version, "pipeline": PIPELINE,
            "basins": list(geo.keys), "basin_names": {b.key: b.name for b in geo.basins},
            "components": {}, "stamps": {}, "files": {}, "created_at": now, "updated_at": now}


def _read_manifest(d: Path, name: str) -> dict:
    p = d / "manifest.json"
    if not p.exists():
        raise FileNotFoundError(f"no stage candidate {name!r} under {d.parent}")
    m = json.loads(p.read_text())
    for field in ("schema", "name", "geography", "pipeline", "components", "stamps", "files", "basins"):
        if field not in m:
            raise ValueError(f"{name}: manifest.json has no {field!r} (a missing stamp is never defaulted)")
    if m["schema"] != SCHEMA:
        raise ValueError(f"{name}: manifest schema {m['schema']!r}, not {SCHEMA!r}")
    if m["name"] != name:
        raise ValueError(f"{name}: the manifest names {m['name']!r}")
    if m["pipeline"] != PIPELINE:
        raise ValueError(f"{name}: pipeline {m['pipeline']!r}; stage candidates are {PIPELINE!r}")
    geo = _geo(m["geography"])
    if list(m["basins"]) != list(geo.keys):
        raise ValueError(f"{name}: manifest basins {m['basins']} are not {geo.version}'s {list(geo.keys)}")
    return m


def _stamp(obj: dict, name: str, geo: G.Geography, component: str, extra: dict | None = None) -> dict:
    """The dict with the set's stamps added; a stamp it already holds must agree."""
    want = {"geography": geo.version, "pipeline": PIPELINE, "set": name, "component": component, **(extra or {})}
    for k, v in want.items():
        if k in obj and obj[k] != v:
            raise ValueError(f"{name} {component}: the payload is stamped {k}={obj[k]!r}, the set is {v!r}")
    return {**obj, **want}


# ── schema checks ──────────────────────────────────────────────────────────

def _check_model(d: dict, what: str) -> None:
    if not isinstance(d, dict):
        raise TypeError(f"{what}: a model dict, not {type(d).__name__}")
    missing = [f for f in MODEL_FIELDS if f not in d]
    if missing:
        raise KeyError(f"{what}: the model dict lacks {missing}")
    if not hasattr(d["model"], "predict_proba"):
        raise TypeError(f"{what}: the model has no predict_proba")
    if not (isinstance(d["features"], list) and d["features"] and all(isinstance(f, str) for f in d["features"])):
        raise TypeError(f"{what}: features must be a non-empty list of column names")
    if not isinstance(d["rain_source"], str) or not d["rain_source"]:
        raise TypeError(f"{what}: rain_source must name a train_v4.rain_series source")
    if d["calibration_offset"] != 0.0:
        raise ValueError(f"{what}: dry-day offset {d['calibration_offset']}; a fitted offset is a mean over the "
                         "final's training span, so stage candidates carry none")
    check_nonnegative(d["model"], what)


def _check_head(d: dict, what: str) -> None:
    if not isinstance(d, dict):
        raise TypeError(f"{what}: a volume head dict, not {type(d).__name__}")
    missing = [f for f in HEAD_FIELDS if f not in d]
    if missing:
        raise KeyError(f"{what}: the volume head lacks {missing}")
    if not hasattr(d["model"], "predict"):
        raise TypeError(f"{what}: the volume head has no predict")
    if d["target"] != HEAD_TARGET:
        raise ValueError(f"{what}: target {d['target']!r}, not {HEAD_TARGET!r} (train_v4.predicted_volume inverts log1p)")
    if not isinstance(d["n_events"], int) or d["n_events"] <= 0:
        raise ValueError(f"{what}: n_events must be the positive count the head was fit on")
    from stages_s2 import HEAD_MIN_EVENTS   # lazy, as for the protocol stamp: Part B 7's 20-event floor
    if d["n_events"] < HEAD_MIN_EVENTS and d["kind"] != FALLBACK_KIND:
        raise ValueError(f"{what}: a {d['kind']!r} head on {d['n_events']} known-volume events, under the "
                         f"{HEAD_MIN_EVENTS}-event floor; only the declared fallback {FALLBACK_KIND!r} may be (Part B 7)")
    check_nonnegative(d["model"], what)


def _iso_day(v, what: str) -> date:
    if not isinstance(v, str):
        raise TypeError(f"{what}: an ISO date string, not {v!r}")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{what}: {v!r} is not an ISO date") from None


def _check_span(d: dict, last: date, what: str) -> None:
    """A model or head that records the days it was fit on (``span``: [first, last] ISO days) must end by ``last``."""
    if "span" not in d:
        return
    s = d["span"]
    if not (isinstance(s, (list, tuple)) and len(s) == 2):
        raise ValueError(f"{what}: span {s!r} is not [first, last]")
    if _iso_day(s[1], f"{what} span") > last:
        raise ValueError(f"{what}: fit on {s[0]} → {s[1]}, after {last} (X-ALL-INSAMPLE: its scored days would be "
                         "days it saw)")


def _compose_view(spec: dict) -> dict:
    """The spec as compose_v2 reads it: without the saver's own stamps (set, component)."""
    return {k: v for k, v in spec.items() if k not in SAVER_KEYS}


def check_s3_links(spec: dict, geo: G.Geography) -> None:
    """design §7 s3_links.json: the keys it names (geography, links, union, cofire, fit) and the component, then
    compose_v2.check_s3_spec — the reader S3 composes through — on the rest: exactly the geography's links with
    their basins, zones, outfalls and evidence, valid shares (1 on a basin's only link), a union rule for every
    zone two or more links feed."""
    missing = [k for k in S3_KEYS if k not in spec]
    if missing:
        raise KeyError(f"s3_links lacks {missing}")
    if spec["geography"] != geo.version:
        raise ValueError(f"s3_links is for {spec['geography']!r}, the set is {geo.version!r}")
    import compose_v2   # lazy: the composition module; only a spec save or load needs it
    compose_v2.check_s3_spec(_compose_view(spec), geo)


def check_s4_quality(spec: dict, geo: G.Geography) -> None:
    """design §7 s4_quality.json: the keys it names (geography, kind, background, buckets, monotone, sources, fit)
    and the component, then compose_v2.check_s4_spec on the rest: the unit the geography composes S4 per,
    complete buckets of probabilities, non-increasing tails, sizes, an issue-time background."""
    missing = [k for k in S4_KEYS if k not in spec]
    if missing:
        raise KeyError(f"s4_quality lacks {missing}")
    if spec["geography"] != geo.version:
        raise ValueError(f"s4_quality is for {spec['geography']!r}, the set is {geo.version!r}")
    if spec["monotone"] is not True:
        raise ValueError("s4_quality: monotone must be true (more overflow history never lowers the risk)")
    import compose_v2
    compose_v2.check_s4_spec(_compose_view(spec), geo)


def _s2_last_days(spec, payload: dict, name: str) -> dict:
    """{fold: the last day its fits may read} from the S2 section: trained_through for the finals, the day before
    holdout_start for holdout siblings. Neither is ever defaulted."""
    if not isinstance(spec, dict):
        raise KeyError(f"{name} s2: the payload needs 'spec' (the manifest's s2 section), a dict stating trained_through")
    if "trained_through" not in spec:
        raise KeyError(f"{name} s2: the spec states no trained_through (a missing stamp is never defaulted)")
    out = {"final": _iso_day(spec["trained_through"], f"{name} s2 trained_through")}
    if "holdout_models" in payload:
        if "holdout_start" not in spec:
            raise KeyError(f"{name} s2: holdout siblings, and the spec states no holdout_start")
        out["pre_holdout"] = _iso_day(spec["holdout_start"], f"{name} s2 holdout_start") - timedelta(days=1)
    return out


def _check_s2(payload: dict, geo: G.Geography, name: str) -> dict:
    """{file name: pickle dict} for an S2 payload, every part checked."""
    if "models" not in payload or "volume" not in payload:
        raise KeyError(f"{name} s2: the payload needs 'models' and 'volume' (finals and their heads)")
    if ("holdout_models" in payload) != ("holdout_volume" in payload):
        raise KeyError(f"{name} s2: holdout siblings come with their heads (holdout_models and holdout_volume together)")
    last = _s2_last_days(payload.get("spec"), payload, name)
    out = {}
    for part, (what, fold) in S2_PARTS.items():
        if part not in payload:
            continue
        got = payload[part]
        if not isinstance(got, dict) or set(got) != set(geo.keys):
            raise KeyError(f"{name} s2 {part}: keys {sorted(got) if isinstance(got, dict) else got!r} are not "
                           f"{geo.version}'s basins {list(geo.keys)}")
        for key in geo.keys:
            label = f"{name} s2 {part} {key}"
            (_check_model if what == "model" else _check_head)(got[key], label)
            _check_span(got[key], last[fold], label)
            out[f"{key}_{what}{FOLDS[fold]}.pkl"] = (got[key], fold, key)
    if CITYWIDE in payload:
        _check_model(payload[CITYWIDE], f"{name} s2 citywide")
        _check_span(payload[CITYWIDE], last["final"], f"{name} s2 citywide")
        out[f"{CITYWIDE}_model.pkl"] = (payload[CITYWIDE], "final", CITYWIDE)
    return out


# ── write ──────────────────────────────────────────────────────────────────

def save_component(name: str, kind: str, payload: dict, root=None) -> Path:
    """Write one stage component of set ``name`` and update its manifest; returns the set's directory.

    Every payload holds 'geography' (a version string) and 'component' (the component's name, e.g. the S2
    term set). By kind:
      s2          'models' and 'volume' ({basin: dict} for every basin of the geography; model dicts hold
                  model, features, rain_source, calibration_offset 0.0, family; heads hold model, features,
                  rain_source, target 'log1p_volume_mg', n_events, kind), optional 'holdout_models' +
                  'holdout_volume' (siblings fit on days before 2023-07-01) and 'citywide', and 'spec' (required:
                  the manifest's s2 section, JSON-safe: how it was chosen and fit, with 'trained_through' and,
                  with siblings, 'holdout_start' as ISO days; a model or head recording its 'span' must end
                  inside them; a head under 20 known-volume events must be the declared fallback)
      s3_links    the s3_links.json dict (design §7)
      s4_quality  the s4_quality.json dict (design §7)
      s1, s5      nothing else but an optional 'spec': the component is code, so only its name is recorded
    A component saved again replaces the previous one (its stale files are removed)."""
    if kind not in KINDS:
        raise KeyError(f"unknown component kind {kind!r}; known: {KINDS}")
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    for f in ("geography", "component"):
        if f not in payload:
            raise KeyError(f"{name} {kind}: the payload states no {f!r} (never defaulted)")
    component = payload["component"]
    if not isinstance(component, str) or not component:
        raise TypeError(f"{name} {kind}: component must name the component")
    geo = _geo(payload["geography"])
    d = set_dir(name, root)
    if (d / "manifest.json").exists():
        man = _read_manifest(d, name)
        if man["geography"] != geo.version:
            raise ValueError(f"{name} is a {man['geography']} set; a {geo.version} {kind} cannot join it")
    else:
        man = _new_manifest(name, geo)
    stage = STAGE_OF_KIND[kind]
    from stages_s2 import protocol_stamp   # lazy: stages_s2 imports the scoring stack; the stamp checks the frozen text
    stamp = {"component": component, "protocol": protocol_stamp(), "saved_at": clock.utc_iso()}
    files: dict = {}
    if kind == "s2":
        for fname, (obj, fold, key) in _check_s2(payload, geo, name).items():
            files[fname] = pickle.dumps(_stamp(dict(obj), name, geo, component, {"fold": fold, "basin": key}))
        section = payload["spec"]                              # required: _check_s2 read its training windows
    elif kind in SPEC_FILES:
        spec = {k: v for k, v in payload.items()}
        (check_s3_links if kind == "s3_links" else check_s4_quality)(spec, geo)
        files[SPEC_FILES[kind]] = _json_bytes(_stamp(spec, name, geo, component))
        section = {"file": SPEC_FILES[kind]}
    else:
        section = payload.get("spec", {})
    _json_bytes(section)                                       # the manifest section must be JSON-safe before anything is written
    d.mkdir(parents=True, exist_ok=True)
    owned = _owned(man, stage)
    for fname, data in files.items():
        _write_atomic(d / fname, data)
    for fname in owned - set(files):                           # a re-save drops what the old component wrote
        (d / fname).unlink(missing_ok=True)
        man["files"].pop(fname, None)
    for fname in files:
        man["files"][fname] = _sha(d / fname)
    man["components"][stage] = component
    man["stamps"][stage] = {**stamp, "files": sorted(files)}
    man[stage] = section
    man["updated_at"] = clock.utc_iso()
    _write_atomic(d / "manifest.json", _json_bytes(man))
    return d


def _owned(man: dict, stage: str) -> set:
    return set((man["stamps"].get(stage) or {}).get("files") or ())


# ── read ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StageSet:
    """A stage candidate as saved: its manifest, its S2 pickles (finals, heads, holdout siblings) and specs."""
    name: str
    path: Path
    manifest: dict
    models: dict            # basin key → model dict (finals)
    volume: dict            # basin key → volume head dict
    holdout_models: dict    # basin key → model dict fit on days before 2023-07-01 ({} if none saved)
    holdout_volume: dict
    citywide: dict | None
    s3_links: dict | None
    s4_quality: dict | None

    @property
    def geo(self) -> G.Geography:
        return G.get(self.manifest["geography"])

    @property
    def keys(self) -> tuple:
        return self.geo.keys

    @property
    def components(self) -> dict:
        return dict(self.manifest["components"])


def _load_pickle(path: Path, name: str, geo: G.Geography, component: str, fold: str, key: str) -> dict:
    with open(path, "rb") as f:
        obj = pickle.load(f)
    if not isinstance(obj, dict):
        raise TypeError(f"{name} {path.name}: not a stamped dict")
    want = {"geography": geo.version, "pipeline": PIPELINE, "set": name, "component": component, "fold": fold, "basin": key}
    for k, v in want.items():
        if k not in obj:
            raise KeyError(f"{name} {path.name}: no {k!r} stamp (a missing stamp is never defaulted)")
        if obj[k] != v:
            raise ValueError(f"{name} {path.name}: stamped {k}={obj[k]!r}, the manifest says {v!r}")
    return obj


def load_set(name: str, root=None) -> StageSet:
    """The set ``name`` as saved, every stamp and key asserted (see the module notes)."""
    d = set_dir(name, root)
    man = _read_manifest(d, name)
    geo = G.get(man["geography"])
    listed = set(man["files"])
    on_disk = {p.name for p in d.iterdir() if p.is_file() and p.name != "manifest.json" and not p.name.startswith(".")}
    if on_disk - listed:
        raise ValueError(f"{name}: files the manifest does not list: {sorted(on_disk - listed)}")
    for fname, sha in man["files"].items():
        p = d / fname
        if not p.exists():
            raise FileNotFoundError(f"{name}: the manifest lists {fname}, which is missing")
        if _sha(p) != sha:
            raise ValueError(f"{name}: {fname} changed after it was saved (sha256 differs from the manifest)")
    owner = {f: s for s, st in man["stamps"].items() for f in (st.get("files") or ())}
    if set(owner) != listed:
        raise ValueError(f"{name}: files no component owns: {sorted(listed - set(owner))}")
    parts: dict = {p: {} for p in S2_PARTS}
    citywide = None
    if "s2" in man["components"]:
        comp = man["components"]["s2"]
        for part, (what, fold) in S2_PARTS.items():
            for key in geo.keys:
                fname = f"{key}_{what}{FOLDS[fold]}.pkl"
                if fname in listed:
                    parts[part][key] = _load_pickle(d / fname, name, geo, comp, fold, key)
            if parts[part] and set(parts[part]) != set(geo.keys):
                raise KeyError(f"{name} s2 {part}: basins {sorted(parts[part])} are not {list(geo.keys)}")
        if not parts["models"] or not parts["volume"]:
            raise FileNotFoundError(f"{name}: an s2 component without its finals and heads")
        if bool(parts["holdout_models"]) != bool(parts["holdout_volume"]):
            raise FileNotFoundError(f"{name}: holdout siblings without their heads, or the reverse")
        if f"{CITYWIDE}_model.pkl" in listed:
            citywide = _load_pickle(d / f"{CITYWIDE}_model.pkl", name, geo, comp, "final", CITYWIDE)
        last = _s2_last_days(man.get("s2"), {"holdout_models": 1} if parts["holdout_models"] else {}, name)
        for part in S2_PARTS:
            for key, obj in parts[part].items():
                (_check_model if S2_PARTS[part][0] == "model" else _check_head)(obj, f"{name} {part} {key}")
                _check_span(obj, last[S2_PARTS[part][1]], f"{name} {part} {key}")
        if citywide is not None:
            _check_span(citywide, last["final"], f"{name} citywide")
    elif any(f.endswith(".pkl") for f in listed):
        raise ValueError(f"{name}: pickles with no s2 component in the manifest")
    specs = {}
    for kind, fname in SPEC_FILES.items():
        stage = STAGE_OF_KIND[kind]
        if fname in listed:
            if stage not in man["components"]:
                raise ValueError(f"{name}: {fname} with no {stage} component in the manifest")
            spec = json.loads((d / fname).read_text())
            for k, v in {"geography": geo.version, "pipeline": PIPELINE, "set": name, "component": man["components"][stage]}.items():
                if k not in spec:
                    raise KeyError(f"{name} {fname}: no {k!r} stamp")
                if spec[k] != v:
                    raise ValueError(f"{name} {fname}: stamped {k}={spec[k]!r}, the manifest says {v!r}")
            (check_s3_links if kind == "s3_links" else check_s4_quality)(spec, geo)
            specs[kind] = spec
        elif stage in man["components"]:
            raise FileNotFoundError(f"{name}: the manifest names a {stage} component but {fname} is missing")
    return StageSet(name, d, man, parts["models"], parts["volume"], parts["holdout_models"], parts["holdout_volume"],
                    citywide, specs.get("s3_links"), specs.get("s4_quality"))


def list_sets(root=None) -> list[dict]:
    """Manifests of every stage candidate under ``root`` (working directories '_…' skipped), by name."""
    r = _root(root)
    out = []
    if r.exists():
        for d in sorted(r.iterdir()):
            if d.is_dir() and valid_name(d.name) and (d / "manifest.json").exists():
                out.append(_read_manifest(d, d.name))
    return out


# ── assembling a stage candidate (P8: the challenger) ──────────────────────

S5_DEFAULT = "link_zone_swap"                # protocol §8's S5 primary: link/zone injection, the winner over basin_swap
S5_DEGRADED = 5                              # the degraded feed's seeds (protocol §8 S5: 5 seeds)
S5_CHOICE_WINDOW = "T1-holdout"              # S5's window (2023-07-01 → the data end) less post-training: 2023-07-01 →
                                             # 2025-10-31; protocol §2: no design choice reads T1, which confirms it
TIE_TOL = 1e-12                              # two variants' mean Δ equal: the same rows (link, zone and link/zone swaps
                                             # coincide when every observation names an outfall); the §8 name, else VARIANTS order


def s5_choice(served_scores: dict, geo: G.Geography, served: str | None = None) -> dict:
    """The S5 component a stage candidate takes, from the served set's scored S5 rows (stages_build scores['s5'],
    the conditional set on OUT's label: protocol §4.1's S5 metric, B = 2,000, seed 0, observation-event blocks) on
    ``S5_CHOICE_WINDOW`` (S5's window before post-training; a cell of any other window, T1 or 'S5' itself, is never
    read): the correction variant ``geo`` can replay (stages_build.s5_variants) that beats doing nothing — 'better'
    than no correction on the perfect feed and on every degraded seed, a perfect-feed comparison that reads the
    feed's own silence never counting (stages_s5.circular_on_perfect: Part B 9) — the lowest mean Δ of the two
    feeds if several do (a tie, ``TIE_TOL``: ``S5_DEFAULT`` when it is among them, else the first in
    stages_s5.VARIANTS); else ``S5_DEFAULT``, the §8 primary's winner. Returns {component, why, rule, window,
    table}."""
    import stages_build as SB  # noqa: PLC0415  (the variants a geography replays)
    import stages_s5 as S5  # noqa: PLC0415
    W = S5_CHOICE_WINDOW
    pooled = ((served_scores.get("s5") or {}).get("pooled") or {})
    feeds = ["oracle"] + [f"degraded:{i}" for i in range(1, S5_DEGRADED + 1)]
    missing = [f for f in feeds if W not in (pooled.get(f) or {})]
    if missing:
        raise KeyError(f"the served set's scores hold no {W} S5 cell for {missing}: rebuild it first")
    table, winners = {}, []
    keep = ("delta", "lo", "hi", "verdict", "n")
    for v in SB.s5_variants(geo):
        if v == S5.NO_CORRECTION:
            continue
        cells = {f: ((pooled[f][W].get(v) or {}).get("delta_vs_plain")) for f in feeds}
        circ = S5.circular_on_perfect(v, S5.NO_CORRECTION)
        if any(c is None for c in cells.values()) and not circ:
            raise KeyError(f"the served set's {W} S5 cells have no {v} − no correction on {[f for f, c in cells.items() if c is None]}")
        # a perfect-feed comparison that reads the feed's own silence never counts, so such a variant cannot qualify
        # whatever its cells say (they are shown when the build holds them)
        beats = (not circ) and all(c.get("verdict") == "better" for c in cells.values())
        table[v] = {"perfect": {k: cells["oracle"].get(k) for k in keep} if cells["oracle"] else None,
                    "degraded": {f: ({k: c.get(k) for k in keep} if c else None) for f, c in cells.items() if f != "oracle"},
                    "perfect_circular": circ, "beats_no_correction": beats}
        if beats:
            winners.append((float(np.mean([cells["oracle"]["delta"], np.mean([cells[f]["delta"] for f in feeds[1:]])])), v))
    if winners:
        best = min(m for m, _ in winners)
        tied = [v for m, v in winners if m - best <= TIE_TOL]      # identical rows (every observation names an outfall)
        comp = S5_DEFAULT if S5_DEFAULT in tied else min(tied, key=S5.VARIANTS.index)
        why = (f"{comp} beats no correction on the perfect feed and on every degraded seed in S5's window before "
               "post-training" + (f" (the lowest mean Δ of {[v for _, v in sorted(winners)]}"
                                  + (f"; {tied} tie, so {comp}" if len(tied) > 1 else "") + ")" if len(winners) > 1 else ""))
    else:
        comp = S5_DEFAULT
        why = ("no correction variant beats doing nothing on both the perfect feed and every degraded seed in S5's window "
               "before post-training (each CI includes 0, or a perfect-feed comparison is circular), so S5 takes "
               f"{S5_DEFAULT}, protocol §8's S5 primary winner over basin_swap")
    return {"component": comp, "why": why, "table": table, "served_set": served, "window": W,
            "rule": ("the variant better than no correction on the perfect feed (non-circular) and on all "
                     f"{S5_DEGRADED} degraded seeds, OUT's label (conditional set), on S5's window before post-training "
                     f"({W}, 2023-07-01 → 2025-10-31: no choice reads T1, protocol §2); else {S5_DEFAULT}")}


def served_s5_scores(served_scores: Path | None = None) -> dict:
    """The served set's written scores.json S5 is chosen on: ``served_scores`` as given (tests), else
    data/models/stages/<served>/scores.json, whose build must be current (its inputs, code and protocol), so a
    stale build never picks a component."""
    import stages_build as SB  # noqa: PLC0415  (the served set's written build and its staleness)
    served = Path(served_scores) if served_scores is not None else SB.STAGES_DIR / SB.served_name() / "scores.json"
    if not served.exists():
        raise FileNotFoundError(f"{served}: build the served set first (S5 is chosen on its written scores)")
    if served_scores is None:
        theirs = json.loads((served.parent / "manifest.json").read_text())
        stale = SB.stale_files(theirs)
        if stale or theirs["protocol"] != SB.S2.protocol_stamp():
            raise ValueError(f"the served set's build is stale ({stale or 'protocol'}): rebuild it before choosing S5 on it")
    return json.loads(served.read_text())


def save_s5(name: str, root=None, served_scores: Path | None = None, scores: dict | None = None, log=print) -> dict:
    """``s5_choice`` for stage candidate ``name`` on the served set's written S5 scores (``scores``, else
    ``served_s5_scores``), saved as its 's5' component with the table and the reason. Returns the choice."""
    import stages_build as SB  # noqa: PLC0415
    st = load_set(name, root)
    sc = scores if scores is not None else served_s5_scores(served_scores)
    s5 = s5_choice(sc, st.geo, SB.served_name())
    save_component(name, "s5", {"geography": st.geo.version, "component": s5["component"], "spec": s5}, root=root)
    log(f"{name}: S5 {s5['component']} — {s5['why']}")
    return s5


def assemble(name: str, root=None, n_boot: int | None = None, served_scores: Path | None = None, log=print) -> dict:
    """Complete stage candidate ``name`` (its S2 already saved by stages_s2_sfpuc4) with every other stage, each fit
    per fold on this set's own parts (Part B 1, 2, 6): S3 links on its S2 (stages_s3_links.candidate_s2: the
    shares on its fold v̂, the union rule nested on its Central / South p), S4 v3 at that S3's link shares φ with
    its fold v̂ sizing unmeasured overflows (stages_s4_v3.run(s3_set, s2_source)), S1 the served weather model (S1 is
    set-independent) and S5 by ``s5_choice`` on the served set's written build (``served_scores``: its scores.json,
    default data/models/stages/<served>/, which must be current: read before anything is fit). Returns what each
    step recorded."""
    import stages_build as SB  # noqa: PLC0415  (the protocol's B)
    import stages_entries as E  # noqa: PLC0415  (the served weather model)
    import stages_s3_links as S3L  # noqa: PLC0415
    import stages_s4_v3 as S4V3  # noqa: PLC0415
    n_boot = SB.B_PROTOCOL if n_boot is None else n_boot
    st = load_set(name, root)
    if "s2" not in st.components:
        raise FileNotFoundError(f"{name} holds no S2: run stages_s2_sfpuc4.py --write first")
    served = served_s5_scores(served_scores)
    geo = st.geo
    t0 = time.time()
    src = S3L.candidate_s2(name, root=root)
    res = S3L.run(s2=src, n_boot=n_boot, log=log)
    s3_path = S3L.write(res.spec, name=name, root=root)
    t3 = time.time() - t0
    log(f"{name}: S3 links on its own S2 ({t3:.0f}s) → {s3_path}")
    t1 = time.time()
    study = S4V3.run(n_boot=n_boot, s3_set=name, s3_root=root, s2_source=src, log=log)
    sc4 = S4V3.scores(study)
    s4_path = S4V3.write(study, name=name, root=root, sc=sc4)
    t4 = time.time() - t1
    log(f"{name}: S4 v3 at its S3's sizes and its v̂ ({t4:.0f}s) → {s4_path}")
    model = E.served_weather_model()
    save_component(name, "s1", {"geography": geo.version, "component": model, "spec": {
        "weather_model": model, "why": ("S1 is set-independent (stages_s1): the candidate reads the served weather model, "
                                        "so its S1 is the served set's (protocol §8 S1: a weather model is chosen on S1 only)")}},
                   root=root)
    s5 = save_s5(name, root=root, scores=served, log=log)
    return {"set": name, "s3": res, "s4": study, "s4_scores": sc4, "s5": s5, "seconds": {"s3": round(t3, 1), "s4": round(t4, 1),
                                                                                          "total": round(time.time() - t0, 1)}}


def main(argv=None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="assemble a stage candidate: S3, S4, S1 and S5 on its own saved S2")
    ap.add_argument("--assemble", required=True, metavar="NAME", help="the stage candidate (its S2 saved by stages_s2_sfpuc4)")
    ap.add_argument("--served-scores", default=None, help="the served set's scores.json S5 is chosen on (default: its build)")
    ap.add_argument("--s5-only", action="store_true", help="redo only S5's choice (S1–S4 as saved)")
    a = ap.parse_args(argv)
    if a.s5_only:
        save_s5(a.assemble, served_scores=a.served_scores)
        return
    out = assemble(a.assemble, served_scores=a.served_scores)
    print(f"assembled {out['set']} in {out['seconds']}")


if __name__ == "__main__":
    # dispatch through the importable module, so the saver every stage module imports is this same one
    import stages_candidates as _m
    _m.main()
