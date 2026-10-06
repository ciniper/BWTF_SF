"""vercel.json stays deployable: Vercel's schema caps a function's excludeFiles glob at 256 characters (a longer one
fails the whole deployment), and the offline-only forecast data stays out of the app function's bundle.
Run: venv/bin/python tests/test_vercel_config.py
"""
from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "vercel.json").read_text())


def test_exclude_files_fits_vercels_schema():
    for name, fn in CONFIG["functions"].items():
        for key in ("excludeFiles", "includeFiles"):
            assert len(fn.get(key, "")) <= 256, f"{name}.{key} is {len(fn[key])} characters; Vercel's limit is 256"


def test_the_skip_rule_compares_with_the_last_deployment():
    """A push of many commits must deploy when any of them changes the app, not only the last one: the skip rule diffs
    against the last deployed commit (VERCEL_GIT_PREVIOUS_SHA), falling back to the parent; a diff that cannot run
    (the previous commit outside Vercel's shallow clone) exits non-zero, which builds."""
    cmd = CONFIG["ignoreCommand"]
    assert cmd.startswith('git diff --quiet "${VERCEL_GIT_PREVIOUS_SHA:-HEAD^}" HEAD -- . '), cmd
    assert len(cmd) <= 256, len(cmd)


def test_offline_stage_data_is_not_bundled():
    glob = CONFIG["functions"]["app/wsgi.py"]["excludeFiles"]
    assert glob.startswith("{") and glob.endswith("}") and "{" not in glob[1:-1], "one flat brace list"
    parts = glob[1:-1].split(",")
    offline = ["features/forecast/data/models/stages/logit_v1_s2v2/scores.json",
               "features/forecast/data/models/stages_candidates/_bakeoff/results.json",
               "features/forecast/data/raw/openmeteo_prev_runs_icon_seamless.csv",
               "features/forecast/data/raw/openmeteo_hist_forecast_icon_seamless.csv"]
    served = ["features/forecast/data/models/served.json", "features/forecast/data/raw/openmeteo_forecast_archive.json",
              "reports/2026-10_forecast_stages.html", "features/forecast/src/models/stages_build.py"]
    for f in offline:
        assert any(fnmatch.fnmatch(f, g) for g in parts), f"{f} would be bundled"
    for f in served:
        assert not any(fnmatch.fnmatch(f, g) for g in parts), f"{f} would be left out of the app"


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
