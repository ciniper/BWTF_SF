"""The site's own static files, cacheable without ever going stale (2026-10-01).

Every page template links its stylesheet, scripts and images through ``asset("brand.css")``,
which returns ``/static/brand.css?v=<first 10 hex of the file's SHA-1>``. A request that
carries the CURRENT stamp gets a year-long ``immutable`` cache, in the browser and at
Vercel's edge; change a file and its stamp changes, so every page from then on points at
the new address. Nothing else changes:

  * an unstamped request (alert emails' images, a script loading another script, the
    exported reports) keeps Flask's default ``no-cache``;
  * a stale or made-up stamp also keeps ``no-cache``, so an address is only ever cached
    while it names the file's actual contents.

Pages themselves are never cached by this, so beach status is always built fresh.
Stamps are computed once per file and recomputed when its modification time moves, so a
local edit shows up on the next server start without a stale browser copy.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

STATIC_DIR = (Path(__file__).resolve().parent / "static").resolve()
LONG_CACHE = "public, max-age=31536000, s-maxage=31536000, immutable"

_STAMPS: dict[str, tuple[float, str]] = {}


def stamp(rel: str) -> str | None:
    """The content stamp of app/static/<rel>, or None for a path outside it or a missing file."""
    path = (STATIC_DIR / rel).resolve()
    if STATIC_DIR not in path.parents or not path.is_file():
        return None
    mtime = path.stat().st_mtime
    hit = _STAMPS.get(rel)
    if hit and hit[0] == mtime:
        return hit[1]
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:10]
    _STAMPS[rel] = (mtime, digest)
    return digest


def asset(rel: str) -> str:
    """``/static/<rel>?v=<stamp>`` for templates; a missing file falls back to the plain address."""
    rel = str(rel).lstrip("/")
    s = stamp(rel)
    return f"/static/{rel}" + (f"?v={s}" if s else "")


def cache_stamped_static(response):
    """Flask after_request hook: the year-long cache for a static file asked for by its current stamp."""
    from flask import request
    if response.status_code == 200 and request.path.startswith("/static/"):
        v = request.args.get("v", "")
        if v and v == stamp(request.path[len("/static/"):]):
            response.headers["Cache-Control"] = LONG_CACHE
    return response
