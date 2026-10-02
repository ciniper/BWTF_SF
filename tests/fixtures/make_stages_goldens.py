#!/usr/bin/env python3
"""Pins for the served forecast: the invariant that nothing served changes
before a promotion (STAGES_DESIGN.md Part C "Invariant", Part B 12, 16, 17,
23; Chase, 2026-10-01). tests/test_served_golden.py checks every pin written
here, and imports the functions below so the test and the pins can't drift.

RE-RUNNING THIS SCRIPT IS THE ONLY WAY TO MOVE A PIN, AND ONLY A PROMOTION
COMMIT MAY DO SO. Work on the forecast-stages branch (P1-P10) adds modules
next to the served path and never moves these files. If a pin fails, the
served forecast changed: find out why; don't re-pin to make it pass.

What it writes (all under tests/fixtures/):
  stages_golden_hashes.json   sha256 of every served file in data/models/
                              (served.json, the five stage-1 pickles, the four
                              volume heads, stage2.json, impact_table.json,
                              thresholds.json, eval_report.json); hashes of
                              csd_labels.build_daily_labels(), of
                              train_v4.build_dataset(end=AS_OF) per rain source
                              (raw record and the gauge-outage rule), and of
                              the served build_scorecard days (labels and
                              risks), all cut at the as-of date
  stages_golden_payload.json  the live day payloads LiveData builds from the
                              committed fixture rain, with no network: live
                              corrections off, and on with fixed flags and samples
  stages_golden_rain.csv      the fixture rain. Written only when missing or
                              with --rain: it is an input, committed once

As-of date (Part B 23): the label, frame and scorecard hashes cover days on or
before AS_OF, so a quarterly refresh that only adds later days leaves them
green. A refresh that rewrites a day on or before AS_OF (a CIWQS correction,
an ACIS or ERA5 revision, a DataSF retraction) fails by design, and the test
names the inputs that changed since the pin. That changes the record the
served models are graded on, so it goes to the owner the same way a
promotion does. Before writing anything the script also checks that the
stored scorecard.json.gz (not pinned) is exactly what the code path builds
through AS_OF, and refuses to pin otherwise.

The fixture rain runs 2026-01-08 → 2026-02-22, refreshed at NOW = 2026-02-17
08:00 Pacific. It covers the February 2026 storms and the Oceanside gauge
outage (0.00 from Jan 29 while Downtown logged real rain), so the gauge-outage
rule runs on the live path. Past hours come from the ERA5 hourly record
(data/raw/hourly_rain_openmeteo.csv), standing in for the KSFO overlay
('observed'). Later hours come from the archived ICON forecast
(openmeteo_hist_forecast_icon_seamless.csv, 'forecast'). The daily gauge
columns are the ACIS totals (historical_rain.csv), served through a stubbed
ACIS POST so LiveData._fetch_acis_daily parses them the way it parses the
real response.

Run:  venv/bin/python tests/fixtures/make_stages_goldens.py [--rain]
"""
from __future__ import annotations

import functools
import gzip
import hashlib
import json
import math
import pickle
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"
MODELS_SRC = ROOT / "features" / "forecast" / "src" / "models"
for p in (ROOT, ROOT / "features" / "forecast", MODELS_SRC, ROOT / "features" / "forecast" / "src" / "collectors"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

SERVE_DIR = ROOT / "features" / "forecast" / "data" / "models"
RAW_DIR = ROOT / "features" / "forecast" / "data" / "raw"
RAIN_CSV = FIX / "stages_golden_rain.csv"
PAYLOAD_JSON = FIX / "stages_golden_payload.json"
HASHES_JSON = FIX / "stages_golden_hashes.json"

AS_OF = "2026-08-17"   # the last day of the committed record the stages design measured (3,822 days from 2016-03-01)
SERVED_FILES = ["served.json",
                "citywide_model.pkl", "westside_model.pkl", "north_shore_model.pkl", "central_model.pkl", "southeast_model.pkl",
                "westside_volume.pkl", "north_shore_volume.pkl", "central_volume.pkl", "southeast_volume.pkl",
                "stage2.json", "impact_table.json", "thresholds.json", "eval_report.json"]
# inputs the label / frame / scorecard hashes read; their hashes are recorded only so a failure can name what moved
INPUT_FILES = ["features/forecast/data/csd/sf_csd_events.csv", "features/forecast/data/csd/sf_csd_monthly_coverage.csv",
               "features/forecast/data/raw/historical_rain.csv", "features/forecast/data/raw/hourly_rain_openmeteo.csv",
               "features/forecast/data/raw/historical_bacteria.csv", "features/forecast/data/poobot/feed_status.csv",
               "features/forecast/data/poobot/discharge_onsets.csv", "features/forecast/data/poobot/samples.csv",
               "shared/outfalls.py", "shared/stations.py", "shared/zones.py", "shared/standards.py"]

# ── the live payload fixture ────────────────────────────────────────────────
WINDOW = ("2026-01-08", "2026-02-22")      # 46 days: 40 before "today" so the 30-day features are whole
NOW = datetime(2026, 2, 17, 8, 0)          # the refresh moment (naive Pacific, like LiveData's clock)
TODAY = NOW.date()
GAUGE_COLS = {"SF Downtown": "gauge_downtown", "SF Oceanside": "gauge_oceanside"}
ACIS_SID = {"047772": "SF Downtown", "047767": "SF Oceanside"}

# live corrections on: fixed observations (the shapes LiveData's fetchers return). A sample acts on the
# days after it (live_rules: known a day later, for three days), so every one here is dated 02-15..02-17
# and each source moves a pinned risk (build_payload_golden checks). DataSF's Baker-China result
# contradicts the feed's on the same day, so live_v2's order (DataSF overrides the map) is pinned too.
D = lambda s: date.fromisoformat(s)  # noqa: E731
LIVE_ONSETS = {D("2026-02-15"): {"central"}, D("2026-02-16"): {"westside", "southeast"}}
LIVE_FLAGS = {D("2026-02-15"): {"central"}, D("2026-02-16"): {"westside", "southeast"}, D("2026-02-17"): {"westside"}}
LIVE_DATASF = {("Ocean Beach", D("2026-02-16")): False, ("Baker-China", D("2026-02-16")): False,
               ("Mission Creek", D("2026-02-17")): True}
LIVE_FEED = {("Crissy Field", D("2026-02-15")): True, ("Mission Creek", D("2026-02-16")): False, ("Baker-China", D("2026-02-16")): True}
LIVE_WATCHER_SINCE = WINDOW[0]             # so the no-flag downgrade can act on the fixture's past days
LIVE_HEALTH = {"ok": True, "mode": "live", "last_processed_at": None, "reason": "ok"}
# input rules the frames and scorecard are hashed under: the raw record (as the served models were trained) and
# gauge_outage_v1 (rain_features.INPUT_RULES_LIVE today: what serving and post-training rescoring apply). Written out,
# not imported, so a change to the served rules fails the payload pin instead of silently re-keying these.
RULE_SETS = ((), ("gauge_outage_v1",))


# ── canonical hashing ───────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canon(v):
    """One canonical JSON value: numbers as floats rounded to 10 decimals (so
    1 and 1.0, or an int column turning float, hash the same), dates as ISO,
    missing as null."""
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.isoformat() if (v.hour or v.minute or v.second) else v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (bool, np.bool_, int, float, np.integer, np.floating)):
        x = float(v)
        if math.isnan(x):
            return None
        x = round(x, 10)
        return 0.0 if x == 0 else x
    if isinstance(v, dict):
        return {str(k): _canon(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(_canon(x) for x in v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(_canon(obj), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def frame_digest(df: pd.DataFrame, upto: str | None = AS_OF) -> dict:
    """{rows, span, sha256, columns{col: 12-hex}, years{yyyy: 12-hex}} of every
    column, rows on or before `upto`. The per-column and per-year hashes only
    say WHERE a frame moved when the whole-frame hash fails."""
    if upto is not None and "date" in df:
        df = df[df["date"] <= pd.Timestamp(upto)]
    df = df.reset_index(drop=True)
    cols = {str(c): digest(df[c].tolist()) for c in df.columns}
    years = {}
    if "date" in df:
        for y, part in df.groupby(df["date"].dt.year):
            years[str(y)] = digest({str(c): part[c].tolist() for c in df.columns})[:12]
    return {"rows": len(df), "span": [_canon(df["date"].iloc[0]), _canon(df["date"].iloc[-1])] if "date" in df and len(df) else None,
            "sha256": digest([[c, h] for c, h in cols.items()]), "columns": {c: h[:12] for c, h in cols.items()}, "years": years}


# ── (a) served files ────────────────────────────────────────────────────────

def served_file_hashes() -> dict:
    return {name: sha256_file(SERVE_DIR / name) for name in SERVED_FILES}


def served_pickles_on_disk() -> list:
    return sorted(p.name for p in SERVE_DIR.glob("*.pkl"))


# ── (b) the live payload, no network ────────────────────────────────────────

def load_fixture_rain() -> tuple:
    """(hourly rain_df the way the refresh hands it to _daily_frames, {gauge: {Timestamp: inches}})."""
    df = pd.read_csv(RAIN_CSV, parse_dates=["timestamp"])
    rain_df = df[["timestamp", "precip_inches", "rain_source"]].copy()
    day = df["timestamp"].dt.normalize()
    gauges = {name: df.assign(day=day).groupby("day")[col].first().dropna().to_dict() for name, col in GAUGE_COLS.items()}
    return rain_df, gauges


class _AcisResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fake_acis_post(gauges: dict, acis_url: str):
    """requests.post stand-in: the fixture's gauge totals in ACIS's own shape
    (strings, 'M' for missing), so _fetch_acis_daily's parsing runs as served."""
    def post(url, json=None, timeout=None, **kw):  # noqa: A002  (requests' keyword)
        if url != acis_url:
            raise AssertionError(f"golden build tried to POST {url}")
        series = gauges[ACIS_SID[json["sid"]]]
        rows = []
        for d in pd.date_range(json["sdate"], json["edate"]):
            v = series.get(d)
            rows.append([str(d.date()), "M" if v is None or pd.isna(v) else f"{float(v):.2f}"])
        return _AcisResponse({"data": rows})
    return post


def _no_get(*a, **kw):
    raise AssertionError(f"golden build tried a network GET: {a[:1]}")


def _jsonable(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"not JSON: {type(o)}")


def as_json(obj):
    """The payload as a visitor's browser gets it (a JSON round trip)."""
    return json.loads(json.dumps(obj, default=_jsonable))


def live_payloads() -> dict:
    """The served day payloads for the fixture, built by LiveData itself:

    plain  _daily_frames → _score_frames → _day_payload(observed={}, live=None)
           for today and the five forecast days (STAGES_DESIGN P0);
    live   _compute_predictions — the refresh's own loop — with live
           corrections on and the fetchers stubbed to the fixed onsets, CSO
           flags, DataSF and feed samples above.

    No network: requests.get raises, requests.post answers only ACIS from the
    fixture, Supabase is switched off for the duration."""
    from features.forecast import live_dashboard as ld
    rain_df, gauges = load_fixture_rain()
    out = {}
    with patch.object(ld.requests, "post", _fake_acis_post(gauges, ld.ACIS_URL)), \
         patch.object(ld.requests, "get", _no_get), \
         patch.object(ld, "_supabase", None), \
         patch.object(ld, "_now_local", lambda: NOW):
        eng = ld.LiveData()
        frames = eng._daily_frames(rain_df, TODAY)
        feats, probs, vols, dates = eng._score_frames(frames)
        idxs = [dates.index(d) for d in dates if d >= TODAY][:6]
        out["plain"] = {str(dates[i]): as_json(eng._day_payload(frames, i, feats, probs, vols, dates, {}, None)) for i in idxs}
        out["outage_days"] = as_json(getattr(eng, "_outage_days", {}))
        out["frames"] = {src: frame_digest(frames[src], upto=None) for src in sorted(frames)}
        out["discharge_probs_by_day"] = {str(d): as_json(probs[i]) for i, d in enumerate(dates)}
        out["volumes_by_day"] = {str(d): as_json(vols[i]) for i, d in enumerate(dates)}

        live = ld.LiveData()
        live.WATCHER_SINCE = LIVE_WATCHER_SINCE
        live._live_corrections_enabled = lambda: (True, "golden")
        live._watcher_health = lambda: dict(LIVE_HEALTH)
        live._fetch_observed_cso = lambda window_start: {d: set(b) for d, b in LIVE_ONSETS.items()}
        live._cso_flag_days = lambda start, end, today: {d: set(b) for d, b in LIVE_FLAGS.items() if start <= d <= end}
        live._sample_flags = lambda start, end: {k: v for k, v in LIVE_DATASF.items() if start <= k[1] <= end}
        live._feed_sample_flags = lambda start, end: {k: v for k, v in LIVE_FEED.items() if start <= k[1] <= end}
        out["live"] = as_json(live._compute_predictions(rain_df))
    return out


# ── (c) labels, frames, scorecard ───────────────────────────────────────────

def label_hashes() -> dict:
    import csd_labels
    return frame_digest(csd_labels.build_daily_labels())


@functools.lru_cache(maxsize=None)
def dataset(rules: tuple = ()) -> tuple:
    """train_v4.build_dataset(end=AS_OF, input_rules=rules or None), built once
    per process (build_scorecard only reads the frames)."""
    import train_v4
    return train_v4.build_dataset(end=pd.Timestamp(AS_OF), input_rules=list(rules) or None)


def _tag(rules: tuple) -> str:
    return "|" + "+".join(rules) if rules else ""


def dataset_hashes() -> dict:
    """build_dataset(end=AS_OF) per rain source: the raw record (as the served
    models were trained) and with the served input rule (gauge_outage_v1, as
    post-training days are rescored)."""
    out = {}
    for rules in RULE_SETS:
        frames, notes = dataset(rules)
        for src in sorted(frames):
            out[src + _tag(rules)] = frame_digest(frames[src])
        out["archive_notes" + _tag(rules)] = digest({k: notes.get(k) for k in ("archive_used", "archive_recall", "archive_labels")})
    return out


def served_bundle() -> tuple:
    """(finals, chosen, heads, impact_raw, stage2 spec) loaded exactly as
    train_v4.rescore loads the served set."""
    import leaderboard  # noqa: F401  (weights pipelines unpickle leaderboard.add_hinges)
    import train_v4
    from groups import BASIN_KEYS
    finals, chosen, heads = {}, {}, {}
    for basin in train_v4.APP_BASINS:
        key = BASIN_KEYS[basin]
        with open(SERVE_DIR / f"{key}_model.pkl", "rb") as f:
            m = pickle.load(f)
        finals[key] = {"model": m["model"], "features": m["features"], "calibration_offset": m["calibration_offset"]}
        chosen[basin] = m.get("rain_source", "avg")
        vp = SERVE_DIR / f"{key}_volume.pkl"
        if vp.exists():
            with open(vp, "rb") as f:
                heads[basin] = pickle.load(f)
    with open(SERVE_DIR / "citywide_model.pkl", "rb") as f:
        m = pickle.load(f)
    finals["citywide"] = {"model": m["model"], "features": m["features"], "calibration_offset": m["calibration_offset"]}
    chosen["citywide"] = "avg"
    impact_raw = json.loads((SERVE_DIR / "impact_table.json").read_text())
    s2 = SERVE_DIR / "stage2.json"
    return finals, chosen, heads, impact_raw, (json.loads(s2.read_text()) if s2.exists() else None)


def scorecard_day_labels(day: dict) -> dict:
    """What actually happened, as one scorecard day records it."""
    return {"date": day["date"],
            "basins": {b: {k: v.get(k) for k in ("y", "vol", "outfalls", "src")} for b, v in day["basins"].items()},
            "groups": {g: {k: v.get(k) for k in ("elevated", "n_samples")} for g, v in day["groups"].items()},
            "zones": {z: {k: v.get(k) for k in ("discharge", "elevated")} for z, v in day["zones"].items()}}


def scorecard_day_risks(day: dict) -> dict:
    """What the served models said that day (final fit, no holdout sibling)."""
    return {"date": day["date"], "rain": day["rain"], "rain_by_gauge": day["rain_by_gauge"], "citywide_p": day["citywide_p"],
            "basins": {b: v["p"] for b, v in day["basins"].items()},
            "groups": {g: v["risk"] for g, v in day["groups"].items()},
            "zones": {z: v["risk"] for z, v in day["zones"].items()}}


@functools.lru_cache(maxsize=None)
def scorecard_days(rules: tuple = ()) -> list:
    """The served scorecard-building path: train_v4.build_scorecard with the
    served pickles, heads, impact table and stage 2 spec (as rescore runs it),
    days on or before AS_OF. About 1.5 s per rule set, so the code path is
    hashed, not only the stored artifact (scorecard.json.gz is rewritten by
    every quarterly rescore)."""
    import train_v4
    finals, chosen, heads, impact_raw, st2 = served_bundle()
    frames, notes = dataset(rules)
    sc = train_v4.build_scorecard(frames, chosen, finals, {}, heads, impact_raw, train_v4.load_samples(), train_v4.archive_tables(),
                                  notes["archive_used"], finals["citywide"]["features"], stage2=st2)
    return [d for d in sc["days"] if d["date"] <= AS_OF]


def scorecard_hashes() -> dict:
    """One hash of the labels (basins y/vol/outfalls/src, groups elevated/n,
    zones discharge/elevated) and one of the risks, per input rule set."""
    out = {}
    for rules in RULE_SETS:
        days = scorecard_days(rules)
        out["labels" + _tag(rules)] = digest([scorecard_day_labels(d) for d in days])
        out["risks" + _tag(rules)] = digest([scorecard_day_risks(d) for d in days])
        out["days" + _tag(rules)] = [days[0]["date"], days[-1]["date"], len(days)]
    return out


def stored_scorecard_days() -> tuple:
    """(days on or before AS_OF, trained_through, input_rules_post) of the served artifact."""
    with gzip.open(SERVE_DIR / "scorecard.json.gz", "rt") as f:
        sc = json.load(f)
    return [d for d in sc["days"] if d["date"] <= AS_OF], sc.get("trained_through") or sc["span"][1], tuple(sc.get("input_rules_post") or ())


def code_path_artifact_days() -> list:
    """The days the served artifact should hold through AS_OF, rebuilt: training
    days from the raw record, post-training days with the artifact's own
    input_rules_post (train_v4.rescore --replace-post)."""
    _, trained_through, rules_post = stored_scorecard_days()
    pre = [d for d in scorecard_days(()) if d["date"] <= trained_through]
    post = [d for d in scorecard_days(rules_post) if d["date"] > trained_through]
    return pre + post


def input_hashes() -> dict:
    return {rel: sha256_file(ROOT / rel)[:16] for rel in INPUT_FILES if (ROOT / rel).exists()}


# ── writers ─────────────────────────────────────────────────────────────────

def write_rain_fixture() -> None:
    """The committed fixture: ERA5 hourly up to NOW ('observed'), the archived
    ICON forecast after it ('forecast'), the ACIS daily gauge totals per day."""
    t0, t1 = pd.Timestamp(WINDOW[0]), pd.Timestamp(WINDOW[1]) + pd.Timedelta(hours=23)
    era5 = pd.read_csv(RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"])
    icon = pd.read_csv(RAW_DIR / "openmeteo_hist_forecast_icon_seamless.csv", parse_dates=["timestamp"])
    hours = pd.DataFrame({"timestamp": pd.date_range(t0, t1, freq="h")})
    past = hours["timestamp"] <= pd.Timestamp(NOW)
    e = hours.merge(era5[["timestamp", "precip_inches"]], on="timestamp", how="left")["precip_inches"]
    i = hours.merge(icon[["timestamp", "precip_inches"]], on="timestamp", how="left")["precip_inches"]
    hours["precip_inches"] = np.where(past, e, i)
    hours["rain_source"] = np.where(past, "observed", "forecast")
    assert hours["precip_inches"].notna().all(), "fixture window not covered by the hourly archives"
    daily = pd.read_csv(RAW_DIR / "historical_rain.csv", parse_dates=["date"])
    rp = daily.pivot_table(index="date", columns="rain_station_name", values="precip_inches", aggfunc="first")
    day = hours["timestamp"].dt.normalize()
    for name, col in GAUGE_COLS.items():
        hours[col] = day.map(rp[name])
    hours.to_csv(RAIN_CSV, index=False)
    print(f"wrote {RAIN_CSV.relative_to(ROOT)}: {len(hours)} hours {WINDOW[0]} → {WINDOW[1]}")


def _risks(live: dict) -> dict:
    return {d: (day["zones"], day["impact_groups"]) for d, day in live.items()}


def check_live_inputs_bite(live: dict) -> None:
    """Each fixed observation source must move a zone or group risk, or the live
    case pins nothing about it (a count in sample_sources is not a risk)."""
    base, g = _risks(live), globals()
    for name in ("LIVE_ONSETS", "LIVE_FLAGS", "LIVE_DATASF", "LIVE_FEED"):
        saved, g[name] = g[name], {}
        try:
            assert _risks(live_payloads()["live"]) != base, f"{name} moves no zone or group risk, so it pins nothing"
        finally:
            g[name] = saved
    assert any(LIVE_FEED.get(k, v) != v for k, v in LIVE_DATASF.items()), \
        "a DataSF result must contradict the feed's on the same group-day, or live_v2's order is unpinned"


def build_payload_golden() -> dict:
    p = live_payloads()
    changed = [(d, z) for d, day in p["live"].items() for z in day["zones"] if day["zones"][z] != day["plain"]["zones"][z]]
    assert changed, "the live case must move at least one zone-day, or it pins nothing the plain case doesn't"
    assert any(p["outage_days"]), "the fixture is chosen to run the gauge-outage rule"
    check_live_inputs_bite(p["live"])
    return {"as_of": AS_OF, "window": list(WINDOW), "now": NOW.isoformat(), "today": str(TODAY),
            "live_inputs": as_json({"onsets": {str(k): v for k, v in LIVE_ONSETS.items()},
                                    "flags": {str(k): v for k, v in LIVE_FLAGS.items()},
                                    "datasf": [[g, str(d), v] for (g, d), v in LIVE_DATASF.items()],
                                    "feed": [[g, str(d), v] for (g, d), v in LIVE_FEED.items()],
                                    "watcher_since": LIVE_WATCHER_SINCE, "health": LIVE_HEALTH}),
            **p}


def build_hash_golden() -> dict:
    return {"as_of": AS_OF,
            "served_files": served_file_hashes(),
            "served_pickles_on_disk": served_pickles_on_disk(),
            "daily_labels": label_hashes(),
            "build_dataset": dataset_hashes(),
            "scorecard": scorecard_hashes(),
            "inputs_at_pin": input_hashes()}


def main(argv: list) -> None:
    if "--rain" in argv or not RAIN_CSV.exists():
        write_rain_fixture()
    stored, built = stored_scorecard_days()[0], code_path_artifact_days()
    assert digest([scorecard_day_labels(d) for d in stored]) == digest([scorecard_day_labels(d) for d in built]) and \
        digest([scorecard_day_risks(d) for d in stored]) == digest([scorecard_day_risks(d) for d in built]), \
        "the served scorecard.json.gz is not what the code path builds — find out why before pinning"
    hashes = build_hash_golden()
    HASHES_JSON.write_text(json.dumps(hashes, indent=1, sort_keys=False) + "\n")
    print(f"wrote {HASHES_JSON.relative_to(ROOT)} (as of {AS_OF})")
    # sorted keys: LiveData iterates a set of rain sources, so some payload dicts come out in hash-seed order
    PAYLOAD_JSON.write_text(json.dumps(build_payload_golden(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {PAYLOAD_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
