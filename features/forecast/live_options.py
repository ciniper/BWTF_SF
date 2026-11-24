"""Allowlisted live choices. Importing the page's options does not load ML."""
import json
from pathlib import Path

from shared import lineup

MODELS = Path(__file__).parent / "data" / "models"
ECMWF_ID = "ecmwf-t9wind3h-osplit-lt2more-bflags"
ECMWF_LINEUP = {
    "geography": "geo_v1", "s1": "ecmwf_ifs025",
    "s2": "logit_wind8_max3h_older11", "s3": "split_v2",
    "s4": "impact_v2_d10", "s5": "live_v2",
}
STAGE_LABELS = ("Rain", "Discharge model", "Beach mapping", "Lingering effects", "Live corrections")


def options():
    served = json.loads((MODELS / "served.json").read_text())
    return [
        describe("", "Promoted forecast · " + lineup.words("s1", served["lineup"]["s1"]), served["lineup"]),
        describe(ECMWF_ID, "Alternative · ECMWF", ECMWF_LINEUP),
    ]


def describe(model_id, label, parts):
    return {"id": model_id, "label": label, "weather": lineup.words("s1", parts["s1"]),
            "stages": [{"label": f"S{i} · {title}", "name": lineup.words(f"s{i}", parts[f"s{i}"])}
                       for i, title in enumerate(STAGE_LABELS, 1)]}
