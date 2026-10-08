"""Stage 2 v2_d10 (stage2_variants.py fit --variant v2_d10): v2's split with its linger table fit on the stages' S4
truth samples, served since 2026-10-07 (first under the 38-weight model, then under the 9-term one). The spec is v2 on more samples, its sets
record the id v2_d10, the stages build refits it on those samples, the served stage2.json is the spec, and nothing
on the serving path reads the samples field. Offline. Run: venv/bin/python tests/test_stage2_d10.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "features/forecast/src/models"), str(ROOT / "features/forecast/src/collectors")):
    if p not in sys.path:
        sys.path.insert(0, p)

import candidates as CAND  # noqa: E402
import stage2_variants as SV  # noqa: E402
import stages_build as B  # noqa: E402
import train_v4 as T4  # noqa: E402
from shared import lineup as LU  # noqa: E402


def _raises(fn, *exc) -> bool:
    try:
        fn()
    except exc or Exception:
        return True
    return False


def test_the_d10_table_is_v2_on_more_samples():
    v2, d = SV.load_spec("v2"), SV.load_spec("v2_d10")
    assert d["variant"] == "v2" and d["impact_samples"] == "d10" and "impact_samples" not in v2
    for k in ("kind", "group_outfalls", "shares", "median_event_volume_mg"):
        assert d[k] == v2[k], k                                          # the split is v2's
    for g, t in v2["impact_table"].items():
        assert d["impact_table"][g]["n_sample_days"] > t["n_sample_days"] * 1.3, g


def test_the_id_a_set_records():
    assert CAND.stage2_variant_id(None) == "v1" and CAND.stage2_variant_id({"variant": "v2"}) == "v2"
    assert CAND.stage2_variant_id(SV.load_spec("v2_d10")) == "v2_d10"
    assert _raises(lambda: CAND.stage2_variant_id({"variant": "v1", "impact_samples": "d10"}), ValueError)
    assert _raises(lambda: CAND.stage2_variant_id({"variant": "v2", "impact_samples": "other"}), ValueError)
    assert LU.geo_v1_parts("x", "v2_d10") == {"s2": "x", "s3": "split_v2", "s4": "impact_v2_d10"}
    assert _raises(lambda: LU.geo_v1_parts("x", "v3"), KeyError)
    spec = SV.load_spec("v2_d10")
    served = json.loads((CAND.SERVE_DIR / "stage2.json").read_text())
    assert CAND.served_info()["stage2"] == "v2_d10" and served == spec          # promoted 2026-10-07
    for m in CAND.list_candidates():
        man = json.loads((CAND.candidate_dir(m["name"]) / "manifest.json").read_text())
        s2 = CAND.load_stage2(m["name"])
        assert man["stage2"]["variant"] == CAND.stage2_variant_id(s2), m["name"]
        if man["stage2"]["variant"] == "v2_d10":
            assert {k: v for k, v in s2.items() if k != "fitted_at"} == {k: v for k, v in spec.items() if k != "fitted_at"}, m["name"]


def test_the_build_refits_it_on_the_d10_samples():
    """On the full training window the build's per-fold fitter gives the saved spec (stage2_variants.fit's calls)."""
    bundle = B.load_set("served")
    train = T4.build_dataset(sources=sorted(set(bundle.s2.sources) | set(bundle.chosen.values()) | {"avg"}))[0]
    heads, _ = T4.stage2_from_served()
    fit = B.fit_s3_s4(train, bundle.chosen, heads, SV.impact_samples("d10"), T4.load_events(), "v2_d10")
    spec = SV.load_spec("v2_d10")
    for k in ("variant", "impact_samples", "shares", "impact_table"):
        assert fit["stage2"][k] == spec[k], k
    assert B.fit_s3_s4(train, bundle.chosen, heads, T4.load_samples(), T4.load_events(), "v2")["stage2"]["impact_table"] \
        == SV.load_spec("v2")["impact_table"]


def test_its_post_training_scores_are_tagged():
    """Built after sfpuc-icon-t8s-osplits-pickout-lzflags's post-training scores were seen (protocol §2): served since,
    its build carries the tag (promote.py carried it into served.json)."""
    name = "icon-w38-osplit-lt2more-bflags"               # served for part of 2026-10-07, a retired candidate since
    man = CAND.served_info() if CAND.served_info()["name"] == name else json.loads((CAND.candidate_dir(name) / "manifest.json").read_text())
    assert "sfpuc-icon-t8s-osplits-pickout-lzflags" in man["tags"]["post_seen"]
    p = B.STAGES_DIR / name / "scores.json"
    if p.exists():
        sc = json.loads(p.read_text())
        assert sc["windows"]["T1"]["post_seen"] == man["tags"]["post_seen"]


def test_nothing_served_reads_the_samples_field():
    for rel in ("features/forecast/live_dashboard.py", "features/forecast/src/models/compose_v2.py",
                "features/forecast/src/models/stage2.py", "features/forecast/src/models/impact.py"):
        assert "impact_samples" not in (ROOT / rel).read_text(), rel


if __name__ == "__main__":
    import traceback
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
