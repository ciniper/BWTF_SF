#!/usr/bin/env python3
"""Minimal Supabase (PostgREST) client — service-role only, stdlib + requests.

Phase 1 of the Supabase migration: subscribers, watcher state, and the alert
log live in Postgres instead of ``data/`` JSON files (which are wiped by any
redeploy without the volume). This module is deliberately tiny — a .env
loader, a configured() check, and four verbs against the REST Data API.

Config (Railway env vars in prod, ``.env`` at the repo root locally):
    SUPABASE_URL          e.g. https://<ref>.supabase.co
    SUPABASE_SERVICE_KEY  the service/secret key (bypasses RLS; server-side only)

Every caller must degrade gracefully when unconfigured — stores fall back to
their legacy JSON files, and the alert log becomes a no-op — so the app still
runs in a bare dev checkout.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import requests

from shared.paths import PROJECT_ROOT

REQUEST_TIMEOUT_SECONDS = 20


def _load_dotenv_once() -> None:
    """Populate os.environ from PROJECT_ROOT/.env (KEY=VALUE lines), without
    overriding variables that are already set (real env wins over the file)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_dotenv_once()


class SupabaseError(RuntimeError):
    """A Supabase REST call failed (non-2xx)."""


def _base_url() -> str:
    """The project origin. Tolerates the dashboard's Data API display value,
    which appends /rest/v1/ — we add that path ourselves per request."""
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    return url


def _service_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_KEY", "")


def is_configured() -> bool:
    return bool(_base_url() and _service_key())


def _request(method: str, table: str, *, params: Optional[dict] = None,
             json_body: Any = None, prefer: str = "") -> requests.Response:
    key = _service_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    response = requests.request(
        method,
        f"{_base_url()}/rest/v1/{table}",
        params=params or {},
        json=json_body,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 300:
        raise SupabaseError(f"{method} {table} -> {response.status_code}: {response.text[:300]}")
    return response


def select(table: str, params: dict) -> list[dict]:
    """GET rows. ``params`` are PostgREST filters, e.g. {"select": "*", "active": "eq.true"}."""
    return _request("GET", table, params=params).json()


def insert(table: str, rows: list[dict], returning: bool = False) -> list[dict]:
    prefer = "return=representation" if returning else "return=minimal"
    response = _request("POST", table, json_body=rows, prefer=prefer)
    return response.json() if returning else []


def upsert(table: str, rows: list[dict], on_conflict: str) -> None:
    """INSERT ... ON CONFLICT (on_conflict) DO UPDATE for every row."""
    _request("POST", table, params={"on_conflict": on_conflict},
             json_body=rows, prefer="resolution=merge-duplicates,return=minimal")


def update(table: str, filters: dict, patch: dict) -> list[dict]:
    """PATCH matching rows; returns the updated rows."""
    params = dict(filters)
    response = _request("PATCH", table, params=params, json_body=patch,
                        prefer="return=representation")
    return response.json()


def rpc(function: str, payload: dict):
    """POST /rest/v1/rpc/<function> — call a Postgres function; returns its JSON."""
    return _request("POST", f"rpc/{function}", json_body=payload).json()


def delete(table: str, filters: dict) -> int:
    """DELETE matching rows; returns how many were removed."""
    response = _request("DELETE", table, params=dict(filters),
                        prefer="return=representation")
    return len(response.json())
