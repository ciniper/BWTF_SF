"""Public risk levels: the one table of words and colours every page puts on a forecast percent.

Chase, 2026-10-01 (STAGES_DESIGN.md A3): fixed levels on the whole percent the page shows,
the same everywhere and chosen by no cost rule:

    Low 0–20 · Medium 21–50 · High 51–80 · Extreme 81–100

The level is read off the *displayed* whole percent, not the raw probability, so the word can
never disagree with the number beside it: 0.2049 shows "20%" and Low, 0.205 shows "21%" and
Medium. The percent is rounded the way the pages round it, JavaScript's ``Math.round`` (halves
go up: the forecast page's ``pct()``), not Python's ``round()`` (halves to even), which would
show 0.205 as 20% on the server and 21% in the browser.

``edges()`` are the probabilities where each level above Low begins (0.205, 0.505, 0.805): the
lines threshold scores (POD, FAR, POFD, CSI, PSS, bias) are reported at.

Colours come from the site palette: brand.css's safe green, posted orange and discharge red, and the
forecast page's old dark red. Amber (#c99a12, brand.css's caution) is left out: 2.6:1 on white is
too faint for a number. Each colour here is at least 3:1 on white (large text, bars, borders);
tests/test_risk_levels.py pins it.

Templates get ``RISK_LEVELS`` = ``export()`` (a Jinja global set in app/wsgi.py); page JS takes
it as one injected constant (``const RISK_LEVELS = {{ RISK_LEVELS|tojson }}``). Standard library only,
no IO: the served path and the page shells can both read it.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Level:
    key: str      # CSS class suffix and JS key: stable, never shown
    label: str    # the word on the page
    lo: int       # whole percent, inclusive
    hi: int       # whole percent, inclusive
    color: str    # number, word, border, bar, map dot
    tint: str     # a light background in the same hue


LEVELS: tuple[Level, ...] = (
    Level("low", "Low", 0, 20, "#237059", "#e0f0ea"),
    Level("medium", "Medium", 21, 50, "#d4763a", "#fbe9d9"),
    Level("high", "High", 51, 80, "#b5310a", "#f8dcd6"),
    Level("extreme", "Extreme", 81, 100, "#8f2508", "#f8dcd6"),
)


def whole_percent(p) -> int:
    """A probability (0–1) as the whole percent a page shows: ``Math.round((p || 0) * 100)``.
    floor + the exact fractional part, so 0.49999999999999994 stays 0 as it does in JavaScript."""
    if p is None or p != p:            # pct() reads a missing value as 0
        return 0
    x = float(p) * 100
    w = math.floor(x)
    return int(w + (x - w >= 0.5))


def level_of_percent(w: int) -> Level:
    """The level of a whole percent (what the Today board's tiles carry)."""
    return next((lv for lv in LEVELS if w <= lv.hi), LEVELS[-1])


def level_of(p) -> Level:
    """The level of a probability, through the percent the page shows."""
    return level_of_percent(whole_percent(p))


def edges() -> tuple[float, ...]:
    """Probability where each level above Low begins: (0.205, 0.505, 0.805). p ≥ edge ⇔ shown at or
    above it, except one float step under 0.805, whose p * 100 already rounds to 80.5 (moot)."""
    return tuple((lv.lo - 0.5) / 100 for lv in LEVELS[1:])


def export() -> dict:
    """JSON-able, for templates and page JS: the levels in order (each with its probability edge,
    0.0 for Low) and the edges alone."""
    e = (0.0,) + edges()
    return {"levels": [{**asdict(lv), "edge": e[i]} for i, lv in enumerate(LEVELS)], "edges": list(edges())}
