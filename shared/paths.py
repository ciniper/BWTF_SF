"""Shared filesystem paths for the BWTF SF app.

Centralizes the repo-root and data directory so feature modules don't each
re-derive them with brittle ``parents[N]`` arithmetic.
"""
from pathlib import Path

# shared/paths.py -> shared/ -> repo root
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
