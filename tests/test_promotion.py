"""The served set is whatever data/models/served.json says (promote.py), and the
serving path applies its stage 2 split. Written for the 2026-09-28 promotion of
icon-w38-osplit-lt2-bflags over gb_v1; holds for any later promotion."""
import gzip
import json
import pathlib
import pickle
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "features/forecast/src/models"))

import candidates as C  # noqa: E402
import stage2 as S2  # noqa: E402

SERVE = ROOT / "features/forecast/data/models"


def test_served_descriptor_matches_the_bundle_on_disk():
    sv = C.served_info()
    assert C.SERVED_FILE.exists(), "served.json missing — promote.py writes it"
    assert sv["name"] == "icon-w38-osplit-lt2-bflags" and sv["stage1"] == "logit_v1" and sv["stage2"] == "v2" and sv["family"] == "logit" and sv["line"] == 0.25
    assert sv["replaced"] == "icon-trees-nosplit-lt1-bflags" and sv["from_candidate"] == sv["name"]
    assert sv["renamed_from"] == "logit_v1_s2v2" and sv["artifact"] == "logit_v1_s2v2"     # the pickles keep their stamps
    import leaderboard  # noqa: F401
    for key in ("citywide", "westside", "north_shore", "central", "southeast"):
        pk = pickle.load(open(SERVE / f"{key}_model.pkl", "rb"))
        assert pk["version"] == sv["artifact"] and pk.get("family") == sv["family"], key
        assert pk["rain_source"] == sv["rain_sources"][key], key
    spec = json.loads((SERVE / "stage2.json").read_text())
    assert spec["variant"] == sv["stage2"] and spec.get("impact_table"), "served stage2.json must be the variant with its refit table"
    with gzip.open(SERVE / "scorecard.json.gz", "rt") as f:
        sc = json.load(f)
    assert (sc.get("stage2") or {}).get("variant") == sv["stage2"] and sc.get("candidate") == sv["name"]
    assert sc.get("trained_through") == sv["trained_through"] and sc.get("input_rules_post") == sv["input_rules_post"]


def test_retired_set_is_a_candidate_and_the_served_one_is_not():
    retired = "icon-trees-nosplit-lt1-bflags"            # gb_v1 until the 2026-10-07 rename
    names = {m["name"] for m in C.list_candidates()}
    assert retired in names and "icon-w38-osplit-lt2-bflags" not in names, names
    man = json.loads((C.candidate_dir(retired) / "manifest.json").read_text())
    assert man["family"] == "gb" and man["stage1"] == {"name": "gb_v1", "from": "retired-served", "family": "gb"} and man["stage2"]["variant"] == "v1"
    assert man.get("retired_at") and man["per_basin"] and man["trained_through"] == "2025-10-31"
    assert man["renamed_from"] == "gb_v1" and man["lineup"]["s2"] == "gb_v1"
    for key in ("citywide", "westside", "north_shore", "central", "southeast"):
        assert (C.candidate_dir(retired) / f"{key}_model.pkl").exists(), key
    assert (C.candidate_dir(retired) / "scorecard.json.gz").exists()
    # candidates built on the retired stage 1 now name it
    m2 = json.loads((C.candidate_dir("icon-trees-osplit-lt2-bflags") / "manifest.json").read_text())
    assert m2["stage1"]["from"] == retired and m2["stage1_source"] == retired


def test_engine_serves_the_split_and_labels_the_served_set():
    from features.forecast import live_dashboard as ld
    E = ld.LIVE
    sv = C.served_info()
    assert (E.stage2 or {}).get("variant") == "v2" and E.split is not None
    assert all(m.get("family") == "logit" for m in E.models.values())
    assert E.list_models()[0]["label"] == f'{sv["name"]} (served)' and E.list_models()[0]["line"] == 0.25
    stamp = E.model_stamp()
    assert stamp["name"] == sv["name"] and stamp["stage2"] == "v2" and stamp["line"] == 0.25 and stamp["family"] == "logit"
    # the split bites on the Westside groups only
    probs = [{"westside": 0.9, "southeast": 0.9, "north_shore": 0.0, "central": 0.0, "citywide": 0.9},
             {"westside": 0.0, "southeast": 0.0, "north_shore": 0.0, "central": 0.0, "citywide": 0.0}]
    vols = [{"westside": 1.0, "southeast": 1.0, "north_shore": 0.0, "central": 0.0}, {b: 0.0 for b in ("westside", "southeast", "north_shore", "central")}]
    dates = ["2026-01-01", "2026-01-02"]
    with_split = E._compose_impact(probs, vols, 1, dates)["_groups"]
    saved = E.specs   # the composition reads the stage specs (compose_v2's adapter); the same table with identity links = no split
    E.specs = ld._C.geo_v1_adapter_specs(stage2=None, impact_table=E._raw_impact_table())
    try:
        no_split = E._compose_impact(probs, vols, 1, dates)["_groups"]
    finally:
        E.specs = saved
    assert with_split["Ocean Beach"] < no_split["Ocean Beach"] and with_split["Southeast"] == no_split["Southeast"]
    assert abs(with_split["Ocean Beach"] / no_split["Ocean Beach"] - S2.group_share(E.stage2, "Ocean Beach", 1.0)) < 0.02
    # the Model check payload names the served set and carries its per-basin picks, not gb_v1's training report
    sc = E.get_scorecard("2024-01-13")
    assert sc["served"]["name"] == sv["name"] and sc["served"]["line"] == 0.25 and sc.get("backtest") is None
    assert set(sc["targets"]) >= {"westside", "southeast"} and sc["targets"]["westside"].get("C") == 0.1


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failed else f"{failed} FAILED"); sys.exit(1 if failed else 0)
