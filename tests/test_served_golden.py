"""Nothing served changes before a promotion (STAGES_DESIGN.md Part C
"Invariant"; Part B 12, 16, 17, 23; Chase, 2026-10-01).

Three kinds of pin, all written by tests/fixtures/make_stages_goldens.py. That
script is the only way to move a pin, and only a promotion commit may run it:
  (a) sha256 of every served file in data/models/ (served.json, the five
      stage-1 pickles, the four volume heads, stage2.json, impact_table.json,
      thresholds.json, eval_report.json). scorecard.json.gz is deliberately
      not pinned: the quarterly rescore rewrites it;
  (b) the live day payloads LiveData builds from the committed fixture rain
      (tests/fixtures/stages_golden_rain.csv), with no network: corrections
      off (_day_payload(observed={}, live=None)), and on, through the
      refresh's own _compute_predictions, with fixed flags and samples.
      zones, predictions, impact_groups, discharge_probs and every other
      pinned key must match to 1e-12;
  (c) hashes of csd_labels.build_daily_labels(), of
      train_v4.build_dataset(end=2026-08-17) per rain source, and of the
      served build_scorecard days (labels and risks), on days up to the
      as-of date, so a refresh that only adds later days stays green.

The served path is everything live_dashboard imports (Part B 12). An edit
there must keep every pin here green. If one fails, the served forecast
moved. Find out why, and don't re-pin to make the test pass.

    venv/bin/python tests/test_served_golden.py
"""
from __future__ import annotations

import functools
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
import make_stages_goldens as G  # noqa: E402

TOL = 1e-12
HASHES = json.loads(G.HASHES_JSON.read_text())
PAYLOAD = json.loads(G.PAYLOAD_JSON.read_text())
REGEN = "tests/fixtures/make_stages_goldens.py (a promotion commit only)"


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _diff(got, want, path="", out=None, limit=12) -> list:
    """Differences between a fresh value and its pin: same keys, same lengths,
    floats within TOL, everything else equal."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(want, dict):
        if not isinstance(got, dict):
            out.append(f"{path}: {type(got).__name__} vs pinned dict")
            return out
        if set(got) != set(want):
            out.append(f"{path}: keys +{sorted(set(got) - set(want))} -{sorted(set(want) - set(got))}")
        for k in want:
            if k in got:
                _diff(got[k], want[k], f"{path}.{k}", out, limit)
    elif isinstance(want, list):
        if not isinstance(got, list) or len(got) != len(want):
            out.append(f"{path}: {got!r:.80} vs pinned {want!r:.80}")
            return out
        for i, (a, b) in enumerate(zip(got, want)):
            _diff(a, b, f"{path}[{i}]", out, limit)
    elif _num(want) and _num(got):   # a number is a number in JSON: 0 and 0.0 are the same value
        # written as "not within TOL": a NaN on either side fails, where `abs(...) > TOL` would pass it
        if not (abs(got - want) <= TOL or (got != got and want != want)):
            out.append(f"{path}: {got!r} vs pinned {want!r}")
    elif got != want or type(got) is not type(want):
        out.append(f"{path}: {got!r:.80} vs pinned {want!r:.80}")
    return out


def _inputs_moved() -> list:
    now = G.input_hashes()
    return [f for f, h in HASHES["inputs_at_pin"].items() if now.get(f) != h]


def _frame_failure(name: str, got: dict, want: dict) -> str:
    cols = sorted(c for c in set(want["columns"]) | set(got["columns"]) if got["columns"].get(c) != want["columns"].get(c))
    years = sorted(y for y in set(want["years"]) | set(got["years"]) if got["years"].get(y) != want["years"].get(y))
    return (f"{name} moved: rows {got['rows']} vs {want['rows']}, span {got['span']} vs {want['span']}; columns {cols[:12]}; "
            f"years {years}; inputs changed since the pin: {_inputs_moved() or 'none (so the code moved)'}")


@functools.lru_cache(maxsize=None)
def _fresh_payloads() -> dict:
    return G.live_payloads()


# ── (a) served files ────────────────────────────────────────────────────────

def test_served_files_are_byte_identical_to_the_pins():
    got = G.served_file_hashes()
    moved = [f for f in HASHES["served_files"] if got.get(f) != HASHES["served_files"][f]]
    assert not moved, f"served files changed: {moved}. Only a promotion may change them (re-pin with {REGEN})"
    assert set(HASHES["served_files"]) == set(G.SERVED_FILES), "the pinned file list and the generator's disagree"


def test_no_served_pickle_was_added_or_removed():
    assert G.served_pickles_on_disk() == HASHES["served_pickles_on_disk"], \
        f"pickles in data/models/: {G.served_pickles_on_disk()} vs pinned {HASHES['served_pickles_on_disk']}"


# ── (b) the live payload ────────────────────────────────────────────────────

def test_live_payload_without_corrections_matches_the_golden():
    got = _fresh_payloads()
    assert list(got["plain"]) == list(PAYLOAD["plain"]) and len(PAYLOAD["plain"]) == 6, (list(got["plain"]), list(PAYLOAD["plain"]))
    problems = []
    for d, want in PAYLOAD["plain"].items():
        _diff({k: got["plain"][d].get(k) for k in want}, want, d, problems)   # every pinned key; a new additive key is not a change
    assert not problems, "plain payload moved:\n  " + "\n  ".join(problems)
    for key in ("outage_days", "discharge_probs_by_day", "volumes_by_day"):
        p = _diff(got[key], PAYLOAD[key], key)
        assert not p, f"{key} moved:\n  " + "\n  ".join(p)
    for src, want in PAYLOAD["frames"].items():
        assert got["frames"][src]["sha256"] == want["sha256"], _frame_failure(f"live frame {src!r}", got["frames"][src], want)


def test_live_payload_with_corrections_matches_the_golden():
    got = _fresh_payloads()["live"]
    assert list(got) == list(PAYLOAD["live"]), (list(got), list(PAYLOAD["live"]))
    problems = []
    for d, want in PAYLOAD["live"].items():
        _diff({k: got[d].get(k) for k in want}, want, d, problems)
    assert not problems, "payload with live corrections moved:\n  " + "\n  ".join(problems)


def test_the_two_cases_pin_different_things():
    """The plain composition rides along unchanged in the corrected payload, the
    corrections move at least one zone-day, and the fixture runs the gauge-outage rule."""
    p = _fresh_payloads()
    for d, live in p["live"].items():
        plain = p["plain"][d]
        assert live["plain"] == {k: plain[k] for k in ("predictions", "impact_groups", "zones", "discharge_probs")}, d
        assert live["live_corrections"]["enabled"] is True and plain["live_corrections"] is None, d
    moved = [(d, z) for d, day in p["live"].items() for z in day["zones"] if day["zones"][z] != day["plain"]["zones"][z]]
    assert len(moved) >= 6, moved
    assert len(p["outage_days"]) >= 2 and all(v == ["SF Oceanside"] for v in p["outage_days"].values()), p["outage_days"]


# ── (c) labels, frames, scorecard ───────────────────────────────────────────

def test_daily_labels_hash():
    got, want = G.label_hashes(), HASHES["daily_labels"]
    assert got["sha256"] == want["sha256"], _frame_failure("csd_labels.build_daily_labels()", got, want)


def test_build_dataset_frames_hash_per_rain_source():
    got = G.dataset_hashes()
    assert set(got) == set(HASHES["build_dataset"]), (sorted(got), sorted(HASHES["build_dataset"]))
    for name, want in HASHES["build_dataset"].items():
        if isinstance(want, str):
            assert got[name] == want, f"build_dataset {name} moved; inputs changed since the pin: {_inputs_moved() or 'none'}"
        else:
            assert got[name]["sha256"] == want["sha256"], _frame_failure(f"build_dataset(end={G.AS_OF}) {name!r}", got[name], want)


def test_scorecard_labels_and_risks_hash():
    got, want = G.scorecard_hashes(), HASHES["scorecard"]
    for key in want:
        assert got[key] == want[key], f"served build_scorecard {key} moved; inputs changed since the pin: {_inputs_moved() or 'none (so the code moved)'}"
    assert want["labels"] == want["labels|gauge_outage_v1"], "labels never depend on the rain input rule"


def test_served_scorecard_artifact_is_what_the_code_path_builds():
    """The stored scorecard.json.gz (not pinned) holds, through the as-of date, exactly
    the days the code path builds: training days from the raw record, post-training
    days under the artifact's input_rules_post."""
    stored, trained_through, rules_post = G.stored_scorecard_days()
    built = G.code_path_artifact_days()
    assert [d["date"] for d in stored] == [d["date"] for d in built]
    assert G.digest([G.scorecard_day_labels(d) for d in stored]) == G.digest([G.scorecard_day_labels(d) for d in built]), "stored labels drifted from the code path"
    assert G.digest([G.scorecard_day_risks(d) for d in stored]) == G.digest([G.scorecard_day_risks(d) for d in built]), "stored risks drifted from the code path"
    assert trained_through == "2025-10-31" and rules_post == ("gauge_outage_v1",), (trained_through, rules_post)


def test_pins_carry_an_as_of_date():
    assert HASHES["as_of"] == PAYLOAD["as_of"] == G.AS_OF == "2026-08-17"
    assert HASHES["scorecard"]["days"] == ["2016-03-01", G.AS_OF, 3822]
    assert all(v["span"][1] <= G.AS_OF for v in HASHES["build_dataset"].values() if isinstance(v, dict))


PROTOCOL_SHA_PLACEHOLDER = "protocol sha256: <filled at commit>"
PROTOCOL = ROOT / "features" / "forecast" / "STAGES_PROTOCOL.md"
PROTOCOLS = ROOT / "features" / "forecast" / "protocols"
# Every protocol version that has been replaced, moved to protocols/ unedited, with the digest its sha line holds.
ARCHIVED_PROTOCOLS = {"stages_v1": "3dea312e3e939645cb906a7e01f632375ec28aadd9cdd76db3a2ae0eff117a6d"}
PROTOCOL_MUST = ("Freeze date: 2026-10-01", "0.205", "0.505", "0.805", "T0", "T1-holdout", "T2", "T3",
                 "90%", "MDE", "first-match", "oracle", "rain known", "optimistic", "as served")
RETIRED_WORDS = r"\b(?:[Cc]ost\w*|King|cheapest|Platt|alarm line|[Gg]roups?|Southeast)\b"


def _protocol_version(text: str) -> str:
    import re
    m = re.match(r"# .*scoring protocol `([^`]+)`\n", text)
    assert m, "a protocol's first line names its version: '# … scoring protocol `stages_vN`'"
    return m.group(1)


def _protocol_digest(text: str) -> tuple:
    """(the digest its sha line holds, or None while it reads the placeholder; the sha256 of the text with that
    line reading the placeholder). Exactly one sha line."""
    import hashlib
    import re
    filled = re.findall(r"^protocol sha256: ([0-9a-f]{64})$", text, re.M)
    open_lines = re.findall(rf"^{re.escape(PROTOCOL_SHA_PLACEHOLDER)}$", text, re.M)
    assert len(filled) + len(open_lines) == 1, "a protocol needs exactly one sha line"
    body = text.replace(f"protocol sha256: {filled[0]}", PROTOCOL_SHA_PLACEHOLDER, 1) if filled else text
    return (filled[0] if filled else None), hashlib.sha256(body.encode()).hexdigest()


def test_the_scoring_protocol_is_frozen_beside_the_design():
    """The current protocol is the version the code implements (stages_spec.PROTOCOL_VERSION), its sections are
    there, it names no retired word, and once its sha line holds a digest (filled at commit) any later edit fails:
    a change is a new version, never an edit in place."""
    import re
    sys.path.insert(0, str(ROOT / "features" / "forecast" / "src" / "models"))
    import stages_spec as SP
    text = PROTOCOL.read_text()
    assert _protocol_version(text) == SP.PROTOCOL_VERSION == "stages_v2", (_protocol_version(text), SP.PROTOCOL_VERSION)
    assert SP.PROTOCOL_VERSION not in ARCHIVED_PROTOCOLS, "the current version is not an archived one"
    for must in PROTOCOL_MUST:
        assert must in text, must
    retired = re.findall(RETIRED_WORDS, text)
    assert not retired, f"the protocol ranks and sets nothing by cost and uses no retired word (Part A): {retired}"
    filled, digest = _protocol_digest(text)
    assert filled is None or digest == filled, \
        f"STAGES_PROTOCOL.md changed after its freeze: write the next version instead of editing {SP.PROTOCOL_VERSION}"


def test_archived_protocols_never_change():
    """Every replaced version sits in protocols/ unedited: its sha line holds a digest, that digest verifies and is
    the one pinned here, its title is its file name, and it names no retired word either."""
    import re
    files = {f.stem: f for f in PROTOCOLS.iterdir() if f.is_file()}
    assert set(files) == set(ARCHIVED_PROTOCOLS) and all(f.suffix == ".md" for f in files.values()), sorted(files)
    for version, f in files.items():
        text = f.read_text()
        assert _protocol_version(text) == version, (f.name, _protocol_version(text))
        filled, digest = _protocol_digest(text)
        assert filled is not None, f"protocols/{f.name}: an archived protocol's sha line holds its digest"
        assert digest == filled == ARCHIVED_PROTOCOLS[version], f"protocols/{f.name} changed: archived versions never do"
        assert not re.findall(RETIRED_WORDS, text), f.name


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
