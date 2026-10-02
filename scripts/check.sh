#!/usr/bin/env bash
# The whole test suite, served goldens included. Run it and see it pass before
# every push: there is no CI, and Vercel deploys whatever lands on main
# (STAGES_DESIGN.md Part B 12; Chase, 2026-10-01).
#
#   scripts/check.sh                      every tests/test_*.py
#   scripts/check.sh tests/test_x.py ...  just those
#
# One line per file; a failing file's output follows the summary. Exits 1 on
# any failure. A file fails on a non-zero exit or on any line starting "FAIL".
# Python: $PYTHON, else venv/bin/python here, else the main checkout's venv (a
# git worktree has no venv of its own).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 2

PY="${PYTHON:-}"
if [ -z "$PY" ] && [ -x "$ROOT/venv/bin/python" ]; then PY="$ROOT/venv/bin/python"; fi
if [ -z "$PY" ]; then
  common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [ -n "$common" ] && [ -x "$(dirname "$common")/venv/bin/python" ]; then PY="$(dirname "$common")/venv/bin/python"; fi
fi
if [ -z "$PY" ]; then echo "no venv python found (set PYTHON=...)" >&2; exit 2; fi

# No test holds the production service key (STAGES_DESIGN.md Part B 20). The
# .env loader (shared/supabase.py) never overrides a variable that is already
# set, so blank ones keep every test off the production database. A test's
# "Supabase configured" branch runs only when that file is run by hand.
export SUPABASE_URL="" SUPABASE_SERVICE_KEY=""

if [ "$#" -gt 0 ]; then files=("$@"); else files=(tests/test_*.py); fi
logs="$(mktemp -d)"
trap 'rm -rf "$logs"' EXIT

passed=0; failed=0; failures=()
start_all=$SECONDS
for f in "${files[@]}"; do
  log="$logs/$(basename "$f").log"
  t0=$SECONDS
  "$PY" "$f" >"$log" 2>&1
  rc=$?
  dt=$((SECONDS - t0))
  if [ "$rc" -eq 0 ] && ! grep -Eq '^[[:space:]]*FAIL([^[:alnum:]_]|$)' "$log"; then
    printf 'PASS  %-44s %4ss\n' "$f" "$dt"; passed=$((passed + 1))
  else
    printf 'FAIL  %-44s %4ss  (exit %s)\n' "$f" "$dt" "$rc"; failed=$((failed + 1)); failures+=("$f")
  fi
done

echo "$passed passed, $failed failed in $((SECONDS - start_all))s ($PY)"
if [ "$failed" -gt 0 ]; then
  for f in "${failures[@]}"; do
    echo; echo "──── $f (last 40 lines) ────"
    tail -n 40 "$logs/$(basename "$f").log"
  done
  exit 1
fi
