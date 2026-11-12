"""Which zone a site outside the station registry belongs to — a Surfrider BWTF site, which has no SFPUC id.

Kept apart from shared/zones.py on purpose: the stages build pins a hash of that file (features/forecast/src/models/
stages_build.py manifest), and a helper for the Today board and the forecast record must not force a model rebuild.
"""
from __future__ import annotations

from typing import Optional

from shared.zones import ZONE_OF_SOURCE, ZONES


def zone_for_site(key: str, lat, lon) -> Optional[str]:
    """The zone of the city station the site shares a beach with when ``key`` is a registry key (the dual sites of
    features/comparison/comparison.BWTF_TO_SFPUC_NAME), else the zone of the nearest registry station (Bayview
    Hunters Point has no city twin). None without coordinates."""
    z = ZONE_OF_SOURCE.get(key)
    if z:
        return z
    if lat is None or lon is None:
        return None
    return min(((st.lat - lat) ** 2 + (st.lon - lon) ** 2, zz.key) for zz in ZONES.values() for st in zz.stations)[1]
