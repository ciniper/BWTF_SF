"""The posting label — was one of a zone's beaches posted (signs up) that day?

The scorecard's ``discharge`` label marks only the day sewage was reported
entering the water, so the week of elevated risk stage 2 holds afterwards
grades as false alarms even when the beach stayed posted. This label grades
against the signs themselves.

Sources, in time order:
  * BeachWatch (California State Water Board) — every SF advisory the county
    filed, 1999 → its last filing, per SFPUC station, expanded to zone-days
    by ``collectors/beachwatch.py`` (``data/beachwatch/sf_posted_zone_days.csv``)
    with a cause class: ``cso`` (combined-sewer overflow), ``rain`` (the
    72-hour rule) or ``other`` (unexplained / bacterial standards violation —
    dry-weather postings a rain model is not expected to see).
  * The watcher's alert_log (Aug 2026 →) can extend it: pass zone-days derived
    from the feed's posted / CSO windows to ``extend``. Not wired yet — the
    hindcast artifact ends before the watcher era matters (TODO).

A day outside every source's coverage is UNKNOWN and is not graded. Inside
coverage, no advisory means "not posted" — a known negative.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

UNKNOWN = "unknown"
IN_SCOPE = ("cso", "rain")          # posted for a reason rain can explain → the model is expected to catch it
OUT_OF_SCOPE = ("other",)           # reported separately, never a miss or a hit


class PostingLabel:
    """``cls(zone, day)`` → 'cso' | 'rain' | 'other' | None (known, not posted) | UNKNOWN."""

    def __init__(self, by_zone: dict, known: list[tuple[str, str]], source: str, notes: dict | None = None):
        self.by_zone = {z: dict(m) for z, m in by_zone.items()}
        self.known_ranges = sorted(known)
        self.source = source
        self.notes = notes or {}

    def known(self, day: str) -> bool:
        return any(lo <= day <= hi for lo, hi in self.known_ranges)

    def cls(self, zone: str, day: str):
        if not self.known(day):
            return UNKNOWN
        return self.by_zone.get(zone, {}).get(day)

    def known_through(self) -> str | None:
        return max((hi for _, hi in self.known_ranges), default=None)

    def coverage(self, start: str, end: str, days: list[str] | None = None) -> dict:
        """How much of [start, end] the label can grade."""
        from datetime import date, timedelta
        if days is None:
            d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
            days = [str(d0 + timedelta(days=i)) for i in range((d1 - d0).days + 1)]
        known = sum(1 for d in days if start <= d <= end and self.known(d))
        total = sum(1 for d in days if start <= d <= end)
        return {"source": self.source, "known_days": known, "unknown_days": total - known,
                "known_through": self.known_through(), "known_ranges": self.known_ranges,
                "in_scope": list(IN_SCOPE), "out_of_scope": list(OUT_OF_SCOPE)}

    def extend(self, by_zone: dict, known: tuple[str, str], source: str) -> "PostingLabel":
        """A new label with another source's zone-days and coverage merged in
        (later source wins on a day both cover)."""
        merged = {z: dict(m) for z, m in self.by_zone.items()}
        for z, m in by_zone.items():
            merged.setdefault(z, {}).update(m)
        return PostingLabel(merged, self.known_ranges + [known], f"{self.source} + {source}", self.notes)


def from_beachwatch() -> PostingLabel | None:
    """The BeachWatch-backed label, or None when data/beachwatch/ is absent."""
    try:
        import beachwatch as BW  # collectors/beachwatch.py
    except Exception:  # noqa: BLE001
        return None
    if not (BW.POSTED_DAYS_CSV.exists() and BW.MANIFEST.exists()):
        return None
    import json
    man = json.loads(BW.MANIFEST.read_text())
    by_zone: dict = {}
    for r in BW.load_posted_zone_days().itertuples(index=False):
        by_zone.setdefault(r.zone, {})[r.date.strftime("%Y-%m-%d")] = r.cause_class
    lo, hi = man["span"]
    return PostingLabel(by_zone, [(lo, hi)], "beachwatch",
                        {"fetched_at": man.get("fetched_at"), "sf_rows": man.get("sf_rows"), "convention": man.get("convention")})
