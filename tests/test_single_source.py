"""Single-source guards: facts that must live in exactly one place.

Each of these was once copied by hand into several files and drifted (or
nearly did): the DataSF dataset URL (six copies when the portal moved
domains, 2026-09), the model feature list, the rain-feature arithmetic.

    venv/bin/python tests/test_single_source.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
for p in (ROOT, FORECAST, FORECAST / "src" / "models", FORECAST / "src" / "collectors"):
    sys.path.insert(0, str(p))

SCAN_DIRS = ("app", "features", "shared", "db", "tests")


def _source_files():
    for d in SCAN_DIRS:
        for p in (ROOT / d).rglob("*"):
            if p.suffix in (".py", ".html", ".js", ".sql") and "__pycache__" not in p.parts:
                yield p


def test_datasf_url_has_one_home():
    from shared import datasf
    from features.alerts import monitoring
    from features.site_analysis import page as site_analysis
    import historical
    assert monitoring.SFWaterQualityMonitor.API_URL is datasf.BEACH_SAMPLES_URL
    assert historical.SFGOV_URL is datasf.BEACH_SAMPLES_URL
    assert site_analysis.SOCRATA_URL is datasf.BEACH_SAMPLES_URL
    strays = []
    for p in _source_files():
        if p.name in ("datasf.py", "test_single_source.py"):
            continue
        text = p.read_text(errors="ignore")
        if "data.sfgov.org" in text or re.search(r"https://data\.sf\.gov/[^\s\"']*v3fv-x3ux", text):
            strays.append(str(p.relative_to(ROOT)))
    assert not strays, f"DataSF URL copied outside shared/datasf.py: {strays}"


def test_feature_list_has_one_home():
    import rain_features as rf
    import train_v2
    assert train_v2.get_feature_columns() == rf.DAILY_FEATURES
    assert train_v2.get_feature_columns_v21() == rf.ALL_FEATURES
    assert train_v2.INTENSITY_FEATURES is rf.INTENSITY_FEATURES


def test_rain_arithmetic_is_not_reimplemented():
    """The tell-tale of a private copy: a rolling(14) antecedent-moisture
    line outside rain_features.py."""
    offenders = []
    for p in _source_files():
        if p.name in ("rain_features.py", "test_single_source.py") or p.parts[-3:-1] == ("forecast", "src") and p.name in ("train.py",):
            continue
        text = p.read_text(errors="ignore")
        if "antecedent_moisture" in text and re.search(r"rolling\(\s*window\s*=\s*14", text):
            offenders.append(str(p.relative_to(ROOT)))
    # train.py is the retired v1 trainer, kept for the record; anything else is a regression
    offenders = [o for o in offenders if not o.endswith("src/models/train.py")]
    assert not offenders, f"rain features re-implemented outside rain_features.py: {offenders}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"  FAIL {name}: {exc!r}")
    print("PASS" if not failures else f"{failures} FAILED")
    raise SystemExit(1 if failures else 0)
