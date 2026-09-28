"""Stage 2 v3 — the outfall split with shares fitted from the water (2026-09-28).

The fitter exists and the spec on disk records the evidence; no candidate set
carries v3 because the water cannot yet adjudicate the contested shares (one
Ocean Beach sample in ten years of Sea Cliff-only tails), so v3's numbers equal
v2's. Re-run `stage2_variants.py fit --variant v3` after each samples refresh.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "features" / "forecast" / "src" / "models"))
sys.path.insert(0, str(ROOT))

import stage2 as S2  # noqa: E402


def test_water_share_math():
    K = S2.WATER_PRIOR_WEIGHT
    assert S2.water_share(0.5, 0.0, 30, 0.05, 0.4, identity=True) == (1.0, 1.0, None)          # identity groups stay 1
    p, water, f = S2.water_share(0.5, 0.05, 30, 0.05, 0.4, identity=False)                       # pure tails at baseline → f 0 → water = map
    assert f == 0.0 and water == 0.4 and abs(p - 0.4) < 1e-12
    p, water, f = S2.water_share(0.5, 0.5, 30, 0.05, 0.4, identity=False)                        # pure tails as bad as attributed → f 1 → water 1
    assert f == 1.0 and water == 1.0 and abs(p - (30 * 1.0 + K * 0.4) / (30 + K)) < 1e-12
    assert S2.water_share(0.5, 0.5, 2, 0.05, 0.4, identity=False) == (0.4, None, None)           # fewer than 3 pure samples: map only
    assert S2.water_share(0.08, 0.5, 30, 0.05, 0.4, identity=False) == (0.4, None, None)         # attributed tails not above baseline: map only
    assert S2.water_share(0.5, 0.9, 30, 0.05, None, identity=False) == (None, None, 1.0)         # no map share to blend with
    p, water, f = S2.water_share(0.5, 0.275, 3, 0.05, 0.4615, identity=False)                    # f = (0.275−0.05)/(0.5−0.05) = 0.5
    assert abs(f - 0.5) < 1e-9 and abs(water - (0.4615 + 0.5385 * 0.5)) < 1e-9 and abs(p - (3 * water + K * 0.4615) / (3 + K)) < 1e-12


def test_v3_spec_on_disk_is_v2_with_water_annotations_and_no_candidate_uses_it_yet():
    assert "v3" in S2.VARIANTS and "water" in S2.KINDS["v3"]
    v3, v2 = S2.load_variant("v3"), S2.load_variant("v2")
    assert v3["variant"] == "v3" and v3["kind"] == "outfall_split_water" and v3["impact_table"] == v2["impact_table"]
    assert v3["tail_days"] == 3 and v3["prior_weight"] == S2.WATER_PRIOR_WEIGHT
    for g, sh in v3["shares"].items():
        for size in ("large", "small", "all"):
            x = sh[size]
            assert x["p"] is None or 0.0 <= x["p"] <= 1.0, (g, size)
            assert x["map"] == v2["shares"][g][size]["p"], (g, size)
        if sh["identity"]:
            assert sh["all"]["p"] == 1.0 and sh["n_non_attributed_days"] == 0, g
    # the finding: too few pure non-attributed tail samples → every non-identity share fell back to the map
    for g in ("Ocean Beach", "Baker-China"):
        for size in ("large", "small", "all"):
            x = v3["shares"][g][size]
            assert x["n_tail_pure_non_attributed"] < 3 and x["p"] == x["map"], (g, size, x)
    ap = v3["shares"]["Aquatic Park"]["small"]
    assert ap["f_non_attributed"] == 0.0 and ap["p"] == ap["map"] == 0.5    # 5 clean pure-tail samples: the water agrees with the map
    assert S2.make_split(v3) is not None and S2.make_split(v3)("Ocean Beach", 0.5, 1.0) == 0.5 * S2.group_share(v3, "Ocean Beach", 1.0)
    import candidates as C
    assert not any(((m.get("stage2") or {}).get("variant") == "v3") for m in C.list_candidates()), "no v3 candidate set until the water can speak"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failed else f"{failed} FAILED"); sys.exit(1 if failed else 0)
