"""db/scripts/sandbox_db.py applies the migrations to a database of one's own, in order and
only once, and never hands a sandbox the production URLs or a send path.
Run: venv/bin/python tests/test_sandbox_db.py
"""
from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "db" / "scripts"))

import sandbox_db as S  # noqa: E402


def test_migrations_are_every_numbered_file_in_order_with_no_gaps():
    files = S.migrations()
    names = [p.name for p in files]
    assert names == sorted(names)
    numbers = [int(n[:3]) for n in names]
    assert numbers == list(range(1, len(numbers) + 1)), numbers
    assert set(p.name for p in S.MIGRATIONS.glob("0*.sql")) == set(names)


def test_pending_skips_applied_and_honours_through():
    all_names = [p.name for p in S.migrations()]
    applied = set(all_names[:3])
    todo = [p.name for p in S.pending(applied)]
    assert todo == all_names[3:]
    assert [p.name for p in S.pending(set(), through=2)] == all_names[:2]
    assert S.pending(set(all_names)) == []


def test_the_overrides_touch_only_the_two_urls_never_mode_or_a_key():
    keys = re.findall(r"values \('([a-z_]+)'", S.OVERRIDES_SQL)
    assert sorted(keys) == ["forecast_refresh_url", "site_url"]
    assert "mode" not in S.OVERRIDES_SQL and "brevo" not in S.OVERRIDES_SQL
    src = (ROOT / "db" / "scripts" / "sandbox_db.py").read_text()
    assert "SUPABASE_SERVICE_KEY" not in src.split('"""', 2)[2], "the script works through psql, never the service key"


def test_refusals_name_production():
    try:
        S.refuse_production("postgresql://x@sandbox.example/postgres", f"https://{S.PRODUCTION_HOST}/forecast/api/refresh")
        assert False, "a refresh URL on production must be refused"
    except SystemExit as e:
        assert S.PRODUCTION_HOST in str(e)
    os.environ["BWTF_PROD_SUPABASE_REF"] = "abcdefprodref"
    try:
        try:
            S.refuse_production("postgresql://postgres.abcdefprodref@pooler/postgres", "")
            assert False, "the production ref must be refused"
        except SystemExit as e:
            assert "production" in str(e)
        S.refuse_production("postgresql://postgres.sandboxref@pooler/postgres", "")   # fine
    finally:
        del os.environ["BWTF_PROD_SUPABASE_REF"]


def test_no_url_is_a_clear_message_and_psql_stops_on_error():
    saved = os.environ.pop("SANDBOX_DB_URL", None)
    try:
        try:
            S.sandbox_url(None)
            assert False
        except SystemExit as e:
            assert "SANDBOX_DB_URL" in str(e)
    finally:
        if saved is not None:
            os.environ["SANDBOX_DB_URL"] = saved
    import inspect
    assert "ON_ERROR_STOP=1" in inspect.getsource(S.psql)


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
