"""Compare recorded results with a fresh computation: the same structure exactly, floats to a tolerance.

A recorded score, weight or spec (leaderboard.json, a candidate's manifest, a stage candidate's s3_links.json) was
written by one numeric runtime. Another runtime (a different BLAS, CPU or library build) refits the same rows to
the same answer only up to its last bits: about 1e-15 for a season-CV score, up to about 1e-8 relative after an
iterative solver and a bootstrap. Exact equality fails on that noise and so checks nothing. These helpers keep
every key, length, string, int and bool exact and let only floats differ, by ``rel`` relative and ``abs_``
absolute (math.isclose), so any change that moves a forecast still fails.
"""
from __future__ import annotations

import math

SCORE_REL = 1e-9        # season-CV scores and other single-pass numbers
SPEC_REL = 1e-6         # solver- and bootstrap-derived numbers (s3 links spec)
ABS = 1e-12


def mismatches(got, want, rel: float = SCORE_REL, abs_: float = ABS, path: str = "") -> list:
    """Every place ``got`` and ``want`` differ, as "path: got != want"; empty when they agree."""
    out = []
    if isinstance(got, dict) and isinstance(want, dict):
        if set(got) != set(want):
            out.append(f"{path or '/'}: keys {sorted(map(str, set(got) ^ set(want)))} in one only")
        for k in got.keys() & want.keys():
            out += mismatches(got[k], want[k], rel, abs_, f"{path}/{k}")
    elif isinstance(got, (list, tuple)) and isinstance(want, (list, tuple)):
        if len(got) != len(want):
            out.append(f"{path}: length {len(got)} != {len(want)}")
        else:
            for i, (a, b) in enumerate(zip(got, want)):
                out += mismatches(a, b, rel, abs_, f"{path}[{i}]")
    elif _is_float_pair(got, want):
        if not math.isclose(got, want, rel_tol=rel, abs_tol=abs_):
            out.append(f"{path}: {got!r} != {want!r}")
    elif type(got) is not type(want) or got != want:
        out.append(f"{path}: {got!r} != {want!r}")
    return out


def assert_close(got, want, what="", rel: float = SCORE_REL, abs_: float = ABS) -> None:
    bad = mismatches(got, want, rel, abs_)
    assert not bad, f"{what}: {len(bad)} differ, e.g. " + "; ".join(bad[:5])


def _is_float_pair(a, b) -> bool:
    """Two numbers, at least one a float (an int that a writer stored as 3 vs 3.0 still compares); bools never."""
    num = (int, float)
    return (isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool)
            and (isinstance(a, float) or isinstance(b, float)))
