"""Which build is this? Shown in page footers and at /api/build so a deploy
can be verified at a glance (and so "is production on the new commit yet?"
is one curl, not a guess).

On Vercel the Git metadata arrives as system environment variables
(VERCEL_GIT_COMMIT_SHA, VERCEL_GIT_COMMIT_MESSAGE, VERCEL_ENV — exposed when
the project's "Automatically expose System Environment Variables" is on).
Locally it comes from git. Neither present → "unknown", never an error.
"""
from __future__ import annotations

import functools
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_STARTED = datetime.now(timezone.utc)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001  (no git, no repo, sandboxed)
        return None


@functools.lru_cache(maxsize=1)
def build_info() -> dict:
    on_vercel = bool(os.environ.get("VERCEL"))
    sha = os.environ.get("VERCEL_GIT_COMMIT_SHA") or (None if on_vercel else _git("rev-parse", "HEAD"))
    message = os.environ.get("VERCEL_GIT_COMMIT_MESSAGE") or (None if on_vercel else _git("log", "-1", "--format=%s"))
    committed_at = None if on_vercel else _git("log", "-1", "--format=%cI")
    dirty = None if on_vercel else bool(_git("status", "--porcelain", "--untracked-files=no"))
    env = os.environ.get("VERCEL_ENV") or ("vercel" if on_vercel else "local")
    short = (sha or "unknown")[:7]
    return {
        "sha": short,
        "sha_full": sha,
        "message": (message or "").splitlines()[0][:100] if message else None,
        "env": env,
        "committed_at": committed_at,
        "dirty": dirty,
        "process_started": _STARTED.isoformat(timespec="minutes"),
        "label": f"build {short}" + ("+" if dirty else "") + (f" · {env}" if env not in ("production",) else ""),
    }
