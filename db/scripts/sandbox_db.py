"""A database of your own: apply db/migrations/ in order to a Supabase project
(or local Postgres) that is NOT production, so migrations and half-built
features can be tried without the SQL editor and without touching the live site.

Setup, once:
  1. Create a free Supabase project (supabase.com → New project). Any region.
  2. Project settings → Database → "Connect" → Session pooler URI (IPv4; the
     direct connection is IPv6-only on the free tier). Put it in .env as
       SANDBOX_DB_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
     and the project's API URL + service_role key as SUPABASE_URL / SUPABASE_SERVICE_KEY
     so the app, the backfills and db/scripts read and write the sandbox too.
  3. venv/bin/python db/scripts/sandbox_db.py apply

Then, for every later migration: `apply` again. It keeps public.applied_migrations
(name, applied_at) in the sandbox and runs only the files not yet listed, in
filename order, each under ON_ERROR_STOP so a failing file stops the run and
is named. The production migrations are applied by hand in the SQL editor and
never track themselves; this table exists only in sandboxes.

After the migrations it writes the sandbox's own values over the production
URLs the migrations bake in (watcher_config):
  forecast_refresh_url  ← SANDBOX_REFRESH_URL  (default '': the half-hourly
                          pg_cron job then does nothing — set it to your own
                          deployment's /forecast/api/refresh to have the sandbox
                          compute its own forecast on the clock)
  site_url              ← SANDBOX_SITE_URL     (default http://localhost:8080)
`mode` stays 'shadow' (migration 002's default): the tick polls SFPUC and
logs would-send decisions, but nothing is ever emailed from a sandbox unless
you flip it yourself. No Brevo key is ever copied here.

Commands:
  apply   [--through NNN]   apply pending migrations (optionally only up to a number), then the overrides
  status                    applied vs pending, and the cron jobs the sandbox is running
  shell                     open psql on the sandbox

Needs `psql` on PATH (brew install libpq). Two refusals: a SANDBOX_REFRESH_URL
on the production site, and — if you set BWTF_PROD_SUPABASE_REF=<ref> in .env —
a SANDBOX_DB_URL that names the production project.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import shared.supabase  # noqa: E402,F401  (loads .env into os.environ)

MIGRATIONS = ROOT / "db" / "migrations"
TRACKING_TABLE = "public.applied_migrations"
PRODUCTION_HOST = "bwtf-sf.vercel.app"   # the site the migrations' URLs point at; never a sandbox

OVERRIDES_SQL = """
insert into public.watcher_config (key, value) values ('forecast_refresh_url', :'refresh_url')
  on conflict (key) do update set value = excluded.value, updated_at = now();
insert into public.watcher_config (key, value) values ('site_url', :'site_url')
  on conflict (key) do update set value = excluded.value, updated_at = now();
"""


def migrations() -> list[Path]:
    """Every db/migrations/NNN_*.sql, in filename (= number) order."""
    files = sorted(p for p in MIGRATIONS.glob("*.sql") if re.match(r"^\d{3}_", p.name))
    numbers = [int(p.name[:3]) for p in files]
    if numbers != sorted(set(numbers)):
        raise SystemExit(f"two migrations share a number: {numbers}")
    return files


def pending(applied: set[str], through: int | None = None) -> list[Path]:
    out = [p for p in migrations() if p.name not in applied]
    if through is not None:
        out = [p for p in out if int(p.name[:3]) <= through]
    return out


def sandbox_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("SANDBOX_DB_URL", "")
    if not url:
        raise SystemExit("no database: pass --url or put SANDBOX_DB_URL=postgresql://... in .env (see the docstring)")
    host = urlparse(url).hostname or ""
    if not host:
        raise SystemExit("SANDBOX_DB_URL does not look like postgresql://user:password@host:port/db")
    return url


def refuse_production(url: str, refresh_url: str) -> None:
    """A sandbox never points pg_cron at the production site, and the production
    database is never the target (its ref is whatever SUPABASE_URL names when
    DEPLOY.md's site is the one configured — the only production marker we have)."""
    if PRODUCTION_HOST in refresh_url:
        raise SystemExit(f"SANDBOX_REFRESH_URL points at production ({PRODUCTION_HOST}); use your own deployment or leave it empty")
    prod_ref = os.environ.get("BWTF_PROD_SUPABASE_REF", "")
    if prod_ref and prod_ref in url:
        raise SystemExit("SANDBOX_DB_URL names the production project (BWTF_PROD_SUPABASE_REF); refusing")


def psql(url: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["psql", url, "-v", "ON_ERROR_STOP=1", "-q", "-X", *args]
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def query(url: str, sql: str) -> list[list[str]]:
    out = psql(url, "-At", "-F", "\t", "-c", sql).stdout
    return [line.split("\t") for line in out.splitlines() if line]


def applied_names(url: str) -> set[str]:
    psql(url, "-c", f"create table if not exists {TRACKING_TABLE} (name text primary key, applied_at timestamptz not null default now())")
    return {row[0] for row in query(url, f"select name from {TRACKING_TABLE} order by name")}


def cmd_apply(url: str, through: int | None) -> int:
    todo = pending(applied_names(url), through)
    host = urlparse(url).hostname
    if not todo:
        print(f"{host}: nothing pending")
    for path in todo:
        print(f"applying {path.name} ...", end=" ", flush=True)
        try:
            psql(url, "-f", str(path))
        except subprocess.CalledProcessError as e:
            print("FAILED")
            print(e.stderr.strip() or e.stdout.strip())
            print(f"\nstopped at {path.name}; fix and run apply again (the earlier files are recorded as applied)")
            return 1
        psql(url, "-c", f"insert into {TRACKING_TABLE} (name) values ('{path.name}') on conflict do nothing")
        print("ok")

    refresh_url = os.environ.get("SANDBOX_REFRESH_URL", "")
    site_url = os.environ.get("SANDBOX_SITE_URL", "http://localhost:8080")
    refuse_production(url, refresh_url)
    psql(url, "-v", f"refresh_url={refresh_url}", "-v", f"site_url={site_url}", "-c", OVERRIDES_SQL)
    print(f"watcher_config: forecast_refresh_url={refresh_url or '(empty: the refresh cron does nothing)'} site_url={site_url}")
    return 0


def cmd_status(url: str) -> int:
    applied = applied_names(url)
    for path in migrations():
        print(f"{'applied' if path.name in applied else 'PENDING'}  {path.name}")
    print("\ncron jobs:")
    for row in query(url, "select jobname, schedule, active from cron.job order by jobname"):
        print("  " + "  ".join(row))
    print("\nwatcher_config (keys only, plus the URLs):")
    for row in query(url, "select key, case when key like '%url%' or key in ('mode') then value else '…' end from public.watcher_config order by key"):
        print("  " + "  ".join(row))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["apply", "status", "shell"], nargs="?", default="apply")
    ap.add_argument("--url", help="connection string (default: SANDBOX_DB_URL from .env)")
    ap.add_argument("--through", type=int, help="apply only migrations up to this number")
    a = ap.parse_args(argv)
    url = sandbox_url(a.url)
    refuse_production(url, os.environ.get("SANDBOX_REFRESH_URL", ""))
    if a.command == "shell":
        os.execvp("psql", ["psql", url])
    if a.command == "status":
        return cmd_status(url)
    return cmd_apply(url, a.through)


if __name__ == "__main__":
    sys.exit(main())
