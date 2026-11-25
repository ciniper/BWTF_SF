"""Interpret the one confirmed getBeaches carrier-station exception (#44).

SFPUC draws 4613 / BAY#211_SL at the 20th/22nd Street outfalls in Dogpatch.
The outfall registry assigns those structures to Crane Cove (4620). Other
flags, including Hyde Street's, retain their existing interpretation.
Keep this pure function in parity with public.bwtf_effective_cso.
"""

DOGPATCH_CSO = "BAY#211_SL"
DOGPATCH_NOTE = (
    "Discharge reported at the 20th/22nd Street outfalls in Dogpatch "
    "(SFPUC files this flag under Aquatic Park)."
)


def dogpatch_active(payload: list[dict]) -> bool:
    return any(str(row.get("stationid")) == "4613" and row.get("cso") == DOGPATCH_CSO
               for row in payload)


def effective_cso(row: dict, payload: list[dict]):
    """Return a station's interpreted flag without mutating the source rows.

    A native Crane Cove flag takes precedence; either flag keeps it active.
    A missing recipient row is not synthesized from Aquatic Park's lab data.
    """
    sid, raw = str(row.get("stationid")), row.get("cso")
    if sid == "4613" and raw == DOGPATCH_CSO:
        return None
    if sid == "4620" and not raw and dogpatch_active(payload):
        return DOGPATCH_CSO
    return raw
