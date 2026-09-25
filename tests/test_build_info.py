"""The build stamp shown in page footers and at /api/build.

    venv/bin/python tests/test_build_info.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_build_info_from_git_locally():
    from app.build_info import build_info
    build_info.cache_clear()
    b = build_info()
    assert b["env"] == "local" and len(b["sha"]) == 7 and b["sha"] != "unknown", b
    assert b["label"].startswith("build " + b["sha"]) and b["label"].endswith("· local"), b["label"]
    assert b["committed_at"] and b["message"], b


def test_build_info_from_vercel_env():
    from app.build_info import build_info
    saved = {k: os.environ.get(k) for k in ("VERCEL", "VERCEL_ENV", "VERCEL_GIT_COMMIT_SHA", "VERCEL_GIT_COMMIT_MESSAGE")}
    try:
        os.environ.update({"VERCEL": "1", "VERCEL_ENV": "production", "VERCEL_GIT_COMMIT_SHA": "b257a38deadbeef",
                           "VERCEL_GIT_COMMIT_MESSAGE": "Gauge outage rule\n\nlong body"})
        build_info.cache_clear()
        b = build_info()
        assert b["sha"] == "b257a38" and b["env"] == "production" and b["message"] == "Gauge outage rule", b
        assert b["label"] == "build b257a38", b["label"]           # production: no env suffix
        assert b["committed_at"] is None and b["dirty"] is None     # no git on Vercel — never an error
        # Vercel without the system env vars exposed: still no error, just "unknown"
        for k in ("VERCEL_GIT_COMMIT_SHA", "VERCEL_GIT_COMMIT_MESSAGE"):
            os.environ.pop(k)
        build_info.cache_clear()
        assert build_info()["sha"] == "unknown"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        build_info.cache_clear()


def test_api_build_and_footers():
    from app.wsgi import app
    c = app.test_client()
    r = c.get("/api/build")
    assert r.status_code == 200
    b = json.loads(r.data)
    assert set(b) >= {"sha", "env", "label", "process_started"}, b
    page = c.get("/forecast").data.decode()
    assert b["label"] in page and 'href="/api/build"' in page, "forecast footer lacks the build stamp"


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
