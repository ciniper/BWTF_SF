"""Pending results require a known collection date, never a posting transition.

SFPUC can post a beach as a precaution without taking a sample. Comparing the
map's collection date with available lab dates is evidence; a safe/posted/CSO
transition by itself is not. Missing access to data is not proof of nonpublication.
"""
from datetime import date


def pending_results(known_dates, available_dates):
    """Return only stations with a documented newer collection date."""
    out = {}
    for station, day in known_dates.items():
        try:
            date.fromisoformat(day)
            available = available_dates.get(station)
            if available:
                date.fromisoformat(available)
            if not available or day > available:
                out[station] = {'sample_date': day}
        except (ValueError, TypeError):
            continue
    return out
