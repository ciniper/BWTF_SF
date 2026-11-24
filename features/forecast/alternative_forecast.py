"""Request-scoped ECMWF forecasts. No result cache, persistence or promotion.

Reuse the scored composition without editing the pinned promoted engine. The
candidate owns its S2 weights and S3/S4 spec; its shared volume heads and risk
thresholds are pinned here so a later promotion cannot silently change it.
"""
import hashlib
import json

import numpy as np
import pandas as pd
import live_dashboard as ld

from .live_options import ECMWF_ID, ECMWF_LINEUP, MODELS

SHARED_ARTIFACTS = {
    "westside_volume.pkl": "0cc354a4c934e59beaaf7d37fb90b4322ef348d291aa47fe4877390e39620b0e",
    "north_shore_volume.pkl": "6d57407c2191faeed66a19bf606d5f0eedf3ef6489bd19f8b5742878d4f1325d",
    "central_volume.pkl": "4bdb2243e3fe553cc52aa5a2eb31b07a56806ef29ec3eefae044b87f588d3582",
    "southeast_volume.pkl": "8c6b50c475013380007d44af6bdd129cf4e44b6579b2f6f630113eea88cc706f",
    "thresholds.json": "b1d2d1a52abb1d7a8c4742a9e2e93068630a53bfdd5187b6f8d14d80cc998f11",
}


class ECMWFForecast(ld.LiveData):
    def __init__(self):
        self.manifest = json.loads((MODELS / "candidates" / ECMWF_ID / "manifest.json").read_text())
        if self.manifest["lineup"] != ECMWF_LINEUP:
            raise ValueError("ECMWF candidate configuration changed; review its live support")
        for name, expected in SHARED_ARTIFACTS.items():
            if hashlib.sha256((MODELS / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"ECMWF shared artifact changed: {name}")
        super().__init__()

    def _load_models(self):
        models = ld._cand.load_models(ECMWF_ID)
        if set(models) != {"westside", "north_shore", "central", "southeast", "citywide"}:
            raise ValueError("ECMWF candidate is missing discharge models")
        return models

    def _load_stage2(self):
        spec = ld._cand.load_stage2(ECMWF_ID)
        if not spec or spec.get("variant") != "v2" or not spec.get("impact_table"):
            raise ValueError("ECMWF candidate is missing its split or lingering table")
        return spec

    def _load_corrections(self):
        return None  # This saved candidate explicitly uses live_v2 basin flags.

    def _fetch_open_meteo(self):
        # A new dict per request: never change ICON's module-level parameters.
        params = {**ld.METEO_PARAMS, "models": ECMWF_LINEUP["s1"]}
        response = ld.requests.get(ld.OPEN_METEO_URL, params=params, timeout=15)
        response.raise_for_status()
        hourly = response.json()["hourly"]
        frame = pd.DataFrame({
            "timestamp": pd.to_datetime(hourly["time"]),
            "precip_mm": hourly["precipitation"], "temp_c": hourly["temperature_2m"],
            "wind_speed_kmh": hourly["wind_speed_10m"], "wind_dir_deg": hourly["wind_direction_10m"],
        })
        if frame.empty or frame.isna().any().any() or not np.isfinite(frame.iloc[:, 1:].to_numpy(dtype=float)).all():
            raise ValueError("ECMWF weather inputs are incomplete")
        if not frame.timestamp.diff().iloc[1:].eq(pd.Timedelta(hours=1)).all():
            raise ValueError("ECMWF weather inputs have missing or duplicate hours")
        today = pd.Timestamp(ld._now_local().date())
        expected = pd.date_range(today - pd.Timedelta(days=7), today + pd.Timedelta(days=6), freq="h", inclusive="left")
        if not expected.difference(frame.timestamp).empty:
            raise ValueError("ECMWF weather inputs do not cover the complete forecast window")
        if (frame[["precip_mm", "wind_speed_kmh"]] < 0).any().any():
            raise ValueError("ECMWF weather inputs contain negative rain or wind speed")
        frame["precip_inches"] = frame.precip_mm / 25.4
        return frame

    def model_stamp(self):
        from app.build_info import build_info
        return {
            "name": ECMWF_ID, "artifact": ECMWF_ID, "family": self.manifest["family"],
            "stage1": ECMWF_LINEUP["s2"], "stage2": "v2_d10",
            "lineup": dict(ECMWF_LINEUP), "weather_model": ECMWF_LINEUP["s1"],
            "live_corrections": "live_v2", "input_rules": list(ld.INPUT_RULES_LIVE),
            "trained_through": self.manifest["trained_through"], "build": build_info().get("sha"),
            "alternative": True,
        }


def compute():
    engine = ECMWFForecast()
    engine.refresh()
    if engine.error or not engine.predictions or len(engine.predictions) != 6:
        raise RuntimeError(engine.error or "ECMWF could not produce all six forecast days")
    snapshot = engine.get_snapshot()
    snapshot["on_demand"] = True
    return snapshot
