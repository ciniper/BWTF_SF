#!/usr/bin/env python3
"""
Live CSO Forecast Dashboard

Real-time dashboard that shows:
1. Current CSO risk based on actual observed rainfall
2. 5-day forecast based on ECMWF/GFS weather model predictions
3. Current SFPUC beach status (what's actually posted right now)

Data flow:
  NWS observations (last 7 days) → compute rain features → run trained model → TODAY's probability
  Open-Meteo forecasts (next 5 days) → compute projected rain features → run model → FORECAST probabilities
  SFPUC LIMS API → current beach posting status

Run: python live_dashboard.py
Open: http://localhost:8091
"""

import json
import pickle
import time
import traceback
import http.server
import socketserver
import numpy as np
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from threading import Thread, Lock

MODEL_DIR = Path(__file__).parent / "data" / "models"
PORT = 8091

# ─── Data source URLs ────────────────────────────────────────────────────────

NWS_OBS_URL = "https://api.weather.gov/stations/KSFO/observations"
NWS_FORECAST_URL = "https://api.weather.gov/gridpoints/MTR/88,126/forecast/hourly"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SFPUC_API_URL = "https://infrastructure.sfwater.org/lims.asmx/getBeaches"

# Open-Meteo params for SF
METEO_PARAMS = {
    "latitude": 37.7749,
    "longitude": -122.4194,
    "hourly": "precipitation,temperature_2m,wind_speed_10m,wind_direction_10m",
    "past_days": 7,
    "forecast_days": 6,
    "timezone": "America/Los_Angeles",
}

# ─── Cached data with thread-safe refresh ─────────────────────────────────────

class LiveData:
    """Thread-safe container for cached live data"""

    def __init__(self):
        self.lock = Lock()
        self.last_refresh = None
        self.rain_history = None       # Last 7 days hourly rain
        self.rain_forecast = None      # Next 5 days hourly rain
        self.current_features = None   # Today's model features
        self.forecast_features = None  # Next 5 days model features
        self.predictions = None        # Model predictions
        self.beach_status = None       # SFPUC beach status
        self.error = None
        self.models = self._load_models()
        self.thresholds = self._load_thresholds()

    def _load_models(self):
        models = {}
        for name in ["citywide", "westside", "north_shore", "southeast"]:
            path = MODEL_DIR / f"{name}_model.pkl"
            if path.exists():
                with open(path, "rb") as f:
                    models[name] = pickle.load(f)
        return models

    def _predict_calibrated(self, features: dict) -> dict:
        """
        Run model prediction with rain-weighted calibration offset.
        
        The offset removes the model's dry-day noise floor. But when rain IS
        present, the model's prediction is real signal — so we scale the offset
        down proportionally to the 3-day cumulative rain:
          - 0" rain → full offset (suppress noise)
          - 0.25" rain → half offset
          - 0.5"+ rain → no offset (trust the model)
        """
        results = {}
        rain_3d = features.get("rain_3d_cum", 0) or 0
        # Scale: full offset at 0", zero at 0.5"+
        rain_factor = max(0.0, 1.0 - rain_3d * 2.0)
        
        for name, model_data in self.models.items():
            model = model_data["model"]
            feat_names = model_data["features"]
            offset = model_data.get("calibration_offset", 0)
            X = pd.DataFrame([{f: features.get(f, 0) for f in feat_names}])
            raw_prob = float(model.predict_proba(X)[0, 1])
            effective_offset = offset * rain_factor
            calibrated = max(0.0, raw_prob - effective_offset)
            results[name] = round(calibrated, 3)
        return results

    def _load_thresholds(self):
        path = MODEL_DIR / "thresholds.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    def refresh(self):
        """Fetch all live data and run predictions"""
        try:
            # 1. Fetch hourly rain (past 7 days + next 6 days) from Open-Meteo
            rain_df = self._fetch_open_meteo()

            # 2. Fetch SFPUC beach status
            beach_status = self._fetch_sfpuc()

            # 3. Compute features and run model for each day
            predictions = self._compute_predictions(rain_df)

            with self.lock:
                self.rain_history = rain_df[rain_df["timestamp"] <= datetime.now()].to_dict("records") if rain_df is not None else []
                self.rain_forecast = rain_df[rain_df["timestamp"] > datetime.now()].to_dict("records") if rain_df is not None else []
                self.predictions = predictions
                self.beach_status = beach_status
                self.last_refresh = datetime.now()
                self.error = None

        except Exception as e:
            with self.lock:
                self.error = str(e)
            traceback.print_exc()

    def _fetch_open_meteo(self) -> pd.DataFrame:
        """Fetch hourly precipitation from Open-Meteo (past 7 days + next 6 days)"""
        r = requests.get(OPEN_METEO_URL, params=METEO_PARAMS, timeout=30)
        r.raise_for_status()
        data = r.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        precip = hourly.get("precipitation", [])
        temp = hourly.get("temperature_2m", [])
        wind_speed = hourly.get("wind_speed_10m", [])
        wind_dir = hourly.get("wind_direction_10m", [])

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(times),
            "precip_mm": precip,
            "temp_c": temp,
            "wind_speed_kmh": wind_speed,
            "wind_dir_deg": wind_dir,
        })
        df["precip_inches"] = df["precip_mm"] / 25.4
        return df

    def _fetch_sfpuc(self) -> list:
        """Fetch current beach status from SFPUC"""
        try:
            r = requests.get(SFPUC_API_URL, timeout=15,
                             headers={"User-Agent": "SFSewageForecast/1.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            json_str = root.text
            if not json_str:
                return []
            data = json.loads(json_str)

            stations = []
            for item in data:
                p_color = item.get("p_color")
                cso_field = item.get("cso")
                has_cso = bool(cso_field and p_color and p_color.upper() == "R")

                if p_color is None:
                    status = "not_sampled"
                elif p_color.upper() == "G":
                    status = "safe"
                elif p_color.upper() == "R":
                    status = "cso" if has_cso else "posted"
                elif p_color.upper() == "Y":
                    status = "not_sampled"
                else:
                    status = "unknown"

                stations.append({
                    "name": item.get("stationname", ""),
                    "status": status,
                    "has_cso": has_cso,
                    "sample_date": item.get("sample_date"),
                })
            return stations
        except Exception as e:
            print(f"SFPUC fetch error: {e}")
            return []

    def _compute_predictions(self, rain_df: pd.DataFrame) -> dict:
        """
        Compute CSO predictions for today and next 5 days.

        For each day, aggregate hourly rain into daily features,
        compute cumulative windows, and run the trained model.
        """
        if rain_df is None or rain_df.empty:
            return {}

        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Aggregate to daily totals
        rain_df = rain_df.copy()
        rain_df["date"] = rain_df["timestamp"].dt.date
        daily = rain_df.groupby("date").agg(
            precip_inches=("precip_inches", "sum"),
            precip_max_hourly=("precip_inches", "max"),
            wind_dir_avg=("wind_dir_deg", "mean"),
        ).reset_index()
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.sort_values("date").reset_index(drop=True)

        # Compute cumulative features
        for w in [2, 3, 5, 7, 14, 30]:
            daily[f"rain_{w}d_cum"] = daily["precip_inches"].rolling(window=w, min_periods=1).sum()

        for lag in [1, 2, 3, 5, 7]:
            daily[f"rain_lag{lag}d"] = daily["precip_inches"].shift(lag).fillna(0)

        # Antecedent moisture
        half_life = 3
        weights = np.exp(-np.log(2) / half_life * np.arange(14))
        daily["antecedent_moisture"] = (
            daily["precip_inches"]
            .rolling(window=14, min_periods=1)
            .apply(lambda x: np.sum(x * weights[:len(x)][::-1]) / np.sum(weights[:len(x)]), raw=True)
        )

        daily["wet_prior_3d"] = (daily["rain_3d_cum"].shift(1).fillna(0) > 0.1).astype(int)
        daily["peak_3d"] = daily["precip_inches"].rolling(window=3, min_periods=1).max()

        is_dry = (daily["precip_inches"] < 0.05).astype(int)
        groups = is_dry.ne(is_dry.shift()).cumsum()
        daily["dry_spell_days"] = is_dry.groupby(groups).cumsum()

        # Run predictions for today + next 5 days
        results = {}
        for day_offset in range(-1, 6):
            target_date = (today + timedelta(days=day_offset)).date()
            row = daily[daily["date"].dt.date == target_date]

            if row.empty:
                continue

            row = row.iloc[0]

            features = {
                "precip_avg": row.get("precip_inches", 0) or 0,
                "precip_max": row.get("precip_max_hourly", 0) or 0,
                "rain_2d_cum": row.get("rain_2d_cum", 0) or 0,
                "rain_3d_cum": row.get("rain_3d_cum", 0) or 0,
                "rain_5d_cum": row.get("rain_5d_cum", 0) or 0,
                "rain_7d_cum": row.get("rain_7d_cum", 0) or 0,
                "rain_14d_cum": row.get("rain_14d_cum", 0) or 0,
                "rain_30d_cum": row.get("rain_30d_cum", 0) or 0,
                "rain_lag1d": row.get("rain_lag1d", 0) or 0,
                "rain_lag2d": row.get("rain_lag2d", 0) or 0,
                "rain_lag3d": row.get("rain_lag3d", 0) or 0,
                "rain_lag5d": row.get("rain_lag5d", 0) or 0,
                "rain_lag7d": row.get("rain_lag7d", 0) or 0,
                "antecedent_moisture": row.get("antecedent_moisture", 0) or 0,
                "wet_prior_3d": int(row.get("wet_prior_3d", 0) or 0),
                "peak_3d": row.get("peak_3d", 0) or 0,
                "dry_spell_days": row.get("dry_spell_days", 0) or 0,
            }

            day_predictions = self._predict_calibrated(features)

            is_forecast = day_offset > 0
            is_today = day_offset == 0

            label = target_date.strftime("%a %b %d")
            if is_today:
                label = "Today"
            elif day_offset == 1:
                label = "Tomorrow"
            elif day_offset == -1:
                label = "Yesterday"

            results[str(target_date)] = {
                "label": label,
                "date": str(target_date),
                "day_offset": day_offset,
                "is_forecast": is_forecast,
                "is_today": is_today,
                "rain_inches": round(features["precip_avg"], 3),
                "rain_2d_cum": round(features["rain_2d_cum"], 3),
                "rain_3d_cum": round(features["rain_3d_cum"], 3),
                "predictions": day_predictions,
                "features": features,
            }

        return results

    def get_historical(self, date_str: str) -> dict:
        """
        Get predictions for a historical date ± 5 days.
        Uses Open-Meteo archive API for past weather data.
        """
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return {"error": f"Invalid date: {date_str}"}

        # Need 14 days before target for antecedent features, plus 5 days after
        start = target - timedelta(days=14)
        end = min(target + timedelta(days=5), datetime.now())

        # Fetch from Open-Meteo archive
        params = {
            "latitude": 37.7749,
            "longitude": -122.4194,
            "hourly": "precipitation,temperature_2m,wind_speed_10m,wind_direction_10m",
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "timezone": "America/Los_Angeles",
        }

        try:
            # Use archive API for dates > 5 days ago, forecast API for recent
            if (datetime.now() - end).days > 5:
                url = "https://archive-api.open-meteo.com/v1/archive"
            else:
                url = OPEN_METEO_URL
                params["past_days"] = (datetime.now() - start).days
                params["forecast_days"] = max(1, (end - datetime.now()).days + 1)
                del params["start_date"]
                del params["end_date"]

            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return {"error": f"Failed to fetch weather data: {e}"}

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        precip = hourly.get("precipitation", [])

        if not times:
            return {"error": "No weather data returned"}

        rain_df = pd.DataFrame({
            "timestamp": pd.to_datetime(times),
            "precip_mm": precip,
        })
        rain_df["precip_inches"] = rain_df["precip_mm"] / 25.4

        # Add missing columns that the archive API might not return
        for col in ["wind_dir_deg", "wind_speed_kmh", "temp_c"]:
            if col not in rain_df.columns:
                rain_df[col] = 0

        # Compute daily features and predictions directly (not using _compute_predictions
        # which is tied to datetime.now())
        rain_df = rain_df.copy()
        rain_df["date"] = rain_df["timestamp"].dt.date
        daily = rain_df.groupby("date").agg(
            precip_inches=("precip_inches", "sum"),
            precip_max_hourly=("precip_inches", "max"),
        ).reset_index()
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.sort_values("date").reset_index(drop=True)

        for w in [2, 3, 5, 7, 14, 30]:
            daily[f"rain_{w}d_cum"] = daily["precip_inches"].rolling(window=w, min_periods=1).sum()
        for lag in [1, 2, 3, 5, 7]:
            daily[f"rain_lag{lag}d"] = daily["precip_inches"].shift(lag).fillna(0)

        half_life = 3
        weights = np.exp(-np.log(2) / half_life * np.arange(14))
        daily["antecedent_moisture"] = (
            daily["precip_inches"]
            .rolling(window=14, min_periods=1)
            .apply(lambda x: np.sum(x * weights[:len(x)][::-1]) / np.sum(weights[:len(x)]), raw=True)
        )
        daily["wet_prior_3d"] = (daily["rain_3d_cum"].shift(1).fillna(0) > 0.1).astype(int)
        daily["peak_3d"] = daily["precip_inches"].rolling(window=3, min_periods=1).max()
        is_dry = (daily["precip_inches"] < 0.05).astype(int)
        groups = is_dry.ne(is_dry.shift()).cumsum()
        daily["dry_spell_days"] = is_dry.groupby(groups).cumsum()

        # Run predictions for target ± days
        target_date = target.date()
        filtered = {}

        for _, row in daily.iterrows():
            row_date = row["date"].date()
            offset = (row_date - target_date).days
            if offset < -2 or offset > 5:
                continue

            features = {
                "precip_avg": row.get("precip_inches", 0) or 0,
                "precip_max": row.get("precip_max_hourly", 0) or 0,
                "rain_2d_cum": row.get("rain_2d_cum", 0) or 0,
                "rain_3d_cum": row.get("rain_3d_cum", 0) or 0,
                "rain_5d_cum": row.get("rain_5d_cum", 0) or 0,
                "rain_7d_cum": row.get("rain_7d_cum", 0) or 0,
                "rain_14d_cum": row.get("rain_14d_cum", 0) or 0,
                "rain_30d_cum": row.get("rain_30d_cum", 0) or 0,
                "rain_lag1d": row.get("rain_lag1d", 0) or 0,
                "rain_lag2d": row.get("rain_lag2d", 0) or 0,
                "rain_lag3d": row.get("rain_lag3d", 0) or 0,
                "rain_lag5d": row.get("rain_lag5d", 0) or 0,
                "rain_lag7d": row.get("rain_lag7d", 0) or 0,
                "antecedent_moisture": row.get("antecedent_moisture", 0) or 0,
                "wet_prior_3d": int(row.get("wet_prior_3d", 0) or 0),
                "peak_3d": row.get("peak_3d", 0) or 0,
                "dry_spell_days": row.get("dry_spell_days", 0) or 0,
            }

            day_predictions = self._predict_calibrated(features)

            label = row_date.strftime("%a %b %d")
            if offset == 0:
                label += " ★"

            filtered[str(row_date)] = {
                "label": label,
                "date": str(row_date),
                "day_offset": offset,
                "is_today": offset == 0,
                "is_forecast": False,
                "rain_inches": round(features["precip_avg"], 3),
                "rain_2d_cum": round(features["rain_2d_cum"], 3),
                "rain_3d_cum": round(features["rain_3d_cum"], 3),
                "predictions": day_predictions,
                "features": features,
            }

        return {
            "target_date": date_str,
            "predictions": filtered,
        }

    def get_bacteria_ground_truth(self, date_str: str) -> dict:
        """
        Fetch actual bacteria samples from SF Gov API for a date range
        matching the historical timeline (target ± 2 days).
        
        Returns per-station, per-date exceedance data grouped by basin,
        so we can overlay "what actually happened" on the model predictions.
        """
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return {"error": f"Invalid date: {date_str}"}

        # Match the historical timeline window: -2 to +5 days
        start = (target - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")
        end = (target + timedelta(days=5)).strftime("%Y-%m-%dT23:59:59")

        # Station metadata
        STATION_NAMES = {
            "OCEAN#15_SL": "Ocean Beach - Pacheco St",
            "OCEAN#15EAST_SL": "Ocean Beach - Pacheco East",
            "OCEAN#16_SL": "Ocean Beach - Rivera St",
            "OCEAN#17_SL": "Ocean Beach - Taraval St",
            "OCEAN#18_SL": "Ocean Beach - Sloat Blvd",
            "OCEAN#19_SL": "Ocean Beach - Zoo",
            "OCEAN#20_SL": "Fort Funston - North",
            "OCEAN#21_SL": "Fort Funston - South",
            "OCEAN#21.1_SL": "Thornton Beach",
            "OCEAN#22_SL": "Mussel Rock",
            "BAY#220_SL": "Baker Beach",
            "BAY#230_SL": "China Beach",
            "BAY#300.1_SL": "Candlestick Point SRA",
            "BAY#301.1_SL": "Windsurfer Circle",
            "BAY#301.2_SL": "Jackrabbit Beach",
            "BAY#202.4_SL": "Crissy Field - East",
            "BAY#202.5_SL": "Crissy Field - West",
            "BAY#210.1_SL": "Aquatic Park",
            "BAY#211_SL": "Hyde Street Pier",
            "BAY#320_SL": "Islais Creek / India Basin",
        }

        STATION_BASINS = {
            "OCEAN#15_SL": "Westside", "OCEAN#15EAST_SL": "Westside",
            "OCEAN#16_SL": "Westside", "OCEAN#17_SL": "Westside",
            "OCEAN#18_SL": "Westside", "OCEAN#19_SL": "Westside",
            "OCEAN#20_SL": "Westside", "OCEAN#21_SL": "Westside",
            "OCEAN#21.1_SL": "Westside", "OCEAN#22_SL": "Westside",
            "BAY#220_SL": "Westside", "BAY#230_SL": "Westside",
            "BAY#300.1_SL": "Westside", "BAY#301.1_SL": "Westside",
            "BAY#301.2_SL": "Westside",
            "BAY#202.4_SL": "North Shore", "BAY#202.5_SL": "North Shore",
            "BAY#210.1_SL": "North Shore", "BAY#211_SL": "North Shore",
            "BAY#320_SL": "Southeast",
        }

        THRESHOLDS = {
            "ENTERO": 104,
            "COLI_E": 235,
            "COLI_FECAL": 400,
            "COLI_TOTAL": 10000,
        }

        try:
            params = {
                "$limit": 5000,
                "$order": "sample_date ASC",
                "$where": f"sample_date >= '{start}' AND sample_date <= '{end}' AND analyte IS NOT NULL",
            }
            r = requests.get("https://data.sfgov.org/resource/v3fv-x3ux.json",
                             params=params, timeout=30)
            r.raise_for_status()
            raw = r.json()
        except Exception as e:
            return {"error": f"Failed to fetch bacteria data: {e}"}

        if not raw:
            return {
                "target_date": date_str,
                "has_data": False,
                "message": "No bacteria samples found for this date range",
                "days": {},
            }

        # Parse into structured records
        records = []
        for rec in raw:
            station = rec.get("source", "")
            analyte = rec.get("analyte", "")
            value_str = rec.get("data", "")
            sample_date = rec.get("sample_date", "")[:10]

            if not station or not analyte or not sample_date:
                continue

            # Parse value
            if not value_str:
                value = None
            elif value_str.startswith("<"):
                try:
                    value = float(value_str[1:]) / 2
                except ValueError:
                    value = None
            elif value_str.startswith(">"):
                try:
                    value = float(value_str[1:])
                except ValueError:
                    value = None
            else:
                try:
                    value = float(value_str)
                except ValueError:
                    value = None

            threshold = THRESHOLDS.get(analyte)
            exceeds = value > threshold if value is not None and threshold else False

            records.append({
                "date": sample_date,
                "station": station,
                "station_name": STATION_NAMES.get(station, station),
                "basin": STATION_BASINS.get(station, "Unknown"),
                "analyte": analyte,
                "value": value,
                "value_raw": value_str,
                "exceeds": exceeds,
                "threshold": threshold,
            })

        # Group by date → basin → station
        days = {}
        for rec in records:
            d = rec["date"]
            if d not in days:
                days[d] = {"date": d, "stations": {}, "summary": {}}

            station_key = rec["station"]
            if station_key not in days[d]["stations"]:
                days[d]["stations"][station_key] = {
                    "station": station_key,
                    "station_name": rec["station_name"],
                    "basin": rec["basin"],
                    "samples": [],
                    "has_exceedance": False,
                    "max_entero": None,
                }

            days[d]["stations"][station_key]["samples"].append({
                "analyte": rec["analyte"],
                "value": rec["value"],
                "value_raw": rec["value_raw"],
                "exceeds": rec["exceeds"],
                "threshold": rec["threshold"],
            })

            if rec["exceeds"]:
                days[d]["stations"][station_key]["has_exceedance"] = True

            if rec["analyte"] == "ENTERO" and rec["value"] is not None:
                current_max = days[d]["stations"][station_key]["max_entero"]
                if current_max is None or rec["value"] > current_max:
                    days[d]["stations"][station_key]["max_entero"] = rec["value"]

        # Compute per-day summaries
        for d, day_data in days.items():
            stations_list = list(day_data["stations"].values())
            total_stations = len(stations_list)
            elevated_stations = sum(1 for s in stations_list if s["has_exceedance"])
            total_samples = sum(len(s["samples"]) for s in stations_list)
            elevated_samples = sum(
                sum(1 for samp in s["samples"] if samp["exceeds"])
                for s in stations_list
            )

            # Per-basin breakdown
            basin_summary = {}
            for s in stations_list:
                b = s["basin"]
                if b not in basin_summary:
                    basin_summary[b] = {"total": 0, "elevated": 0, "stations": []}
                basin_summary[b]["total"] += 1
                if s["has_exceedance"]:
                    basin_summary[b]["elevated"] += 1
                basin_summary[b]["stations"].append({
                    "station": s["station"],
                    "name": s["station_name"],
                    "elevated": s["has_exceedance"],
                    "max_entero": s["max_entero"],
                    "sample_count": len(s["samples"]),
                    "exceedance_count": sum(1 for samp in s["samples"] if samp["exceeds"]),
                })

            elevated_basins = sum(1 for b in basin_summary.values() if b["elevated"] > 0)
            likely_cso = elevated_stations >= 3 and elevated_basins >= 2

            day_data["summary"] = {
                "total_stations": total_stations,
                "elevated_stations": elevated_stations,
                "total_samples": total_samples,
                "elevated_samples": elevated_samples,
                "elevated_basins": elevated_basins,
                "likely_cso": likely_cso,
                "basins": basin_summary,
            }

            # Remove the raw stations dict (we have it in basin_summary now)
            del day_data["stations"]

        return {
            "target_date": date_str,
            "has_data": True,
            "sample_count": len(records),
            "days": days,
        }

    def get_snapshot(self) -> dict:
        """Get current state as JSON-serializable dict"""
        with self.lock:
            return {
                "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
                "predictions": self.predictions or {},
                "beach_status": self.beach_status or [],
                "error": self.error,
                "thresholds": self.thresholds,
            }


# ─── Background refresh thread ───────────────────────────────────────────────

def refresh_loop(live_data: LiveData, interval_seconds: int = 1800):
    """Refresh data every 30 minutes"""
    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Refreshing live data...")
        live_data.refresh()
        if live_data.error:
            print(f"  ⚠️ Error: {live_data.error}")
        else:
            print(f"  ✅ Data refreshed. {len(live_data.predictions or {})} days of predictions.")
        time.sleep(interval_seconds)


# ─── HTTP Handler ─────────────────────────────────────────────────────────────

# Global live data instance
LIVE = LiveData()


class LiveDashboardHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_html(self.generate_html())
        elif parsed.path == "/api/data":
            self.send_json(LIVE.get_snapshot())
        elif parsed.path == "/api/refresh":
            LIVE.refresh()
            self.send_json(LIVE.get_snapshot())
        elif parsed.path == "/api/historical":
            date_str = params.get("date", [""])[0]
            if date_str:
                self.send_json(LIVE.get_historical(date_str))
            else:
                self.send_json({"error": "Missing ?date=YYYY-MM-DD parameter"})
        elif parsed.path == "/api/bacteria":
            date_str = params.get("date", [""])[0]
            if date_str:
                self.send_json(LIVE.get_bacteria_ground_truth(date_str))
            else:
                self.send_json({"error": "Missing ?date=YYYY-MM-DD parameter"})
        else:
            self.send_error(404)

    def send_html(self, html):
        data = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj):
        data = json.dumps(obj, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def generate_html(self):
        data_start = "2020-07-27"  # Earliest bacteria data in SF Gov API
        today_str = datetime.now().strftime("%Y-%m-%d")
        html = HTML_TEMPLATE.replace("__MIN_DATE__", data_start).replace("__MAX_DATE__", today_str).replace("__DEFAULT_DATE__", today_str)
        return html


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SF CSO Live Forecast</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Avenir Next','Trebuchet MS','Segoe UI', Roboto, sans-serif; background: #e2e8ee; color: #26272a; min-height: 100vh; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }

header { background: #1f6fb0; color: #fff; border-radius: 18px; padding: 28px 32px; margin-bottom: 20px; text-align: center; }
header h1 { font-size: 1.8em; color: #fff; -webkit-text-stroke: 2px #26272a; paint-order: stroke fill; }
header .subtitle { color: rgba(255,255,255,0.85); font-size: 0.9em; margin-top: 4px; }
header .live-dot { display: inline-block; width: 8px; height: 8px; background: #3c9160; border-radius: 50%; margin-right: 6px; animation: blink 2s infinite; }

.mode-bar { display: flex; justify-content: center; align-items: center; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }
.mode-btn { padding: 8px 20px; border-radius: 8px; border: 2px solid #dfe5ea; background: #ffffff; color: #5e6a71; cursor: pointer; font-size: 0.9em; font-weight: 600; transition: all 0.2s; }
.mode-btn.active { border-color: #1f6fb0; color: #fff; background: #1f6fb0; }
.mode-btn:hover { border-color: #2b7fbf; }
.date-picker { background: #ffffff; border: 2px solid #dfe5ea; border-radius: 8px; padding: 8px 14px; color: #26272a; font-size: 0.9em; cursor: pointer; }
.date-picker:focus { border-color: #2b7fbf; outline: none; }
.historical-label { color: #b97e00; font-size: 0.85em; font-weight: 600; }

.risk-banner { text-align: center; padding: 16px 22px; border-radius: 14px; margin-bottom: 20px; font-size: 1.2em; font-weight: 600; }
.risk-none { background: #3c9160; color: #fff; }
.risk-low { background: #b97e00; color: #fff; }
.risk-moderate { background: #d4763a; color: #fff; }
.risk-high { background: #d15c5c; color: #fff; }
.risk-extreme { background: #b5310a; color: #fff; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:0.7;} }

/* Forecast timeline */
.timeline { display: grid; grid-template-columns: repeat(7, 1fr); gap: 10px; margin-bottom: 20px; }
@media (max-width: 800px) { .timeline { grid-template-columns: repeat(4, 1fr); } }
.day-card { background: #ffffff; border-radius: 12px; padding: 14px; text-align: center; border: 2px solid transparent; transition: border-color 0.3s, transform 0.2s; cursor: pointer; }
.day-card.today { border-color: #2b7fbf; }
.day-card.selected { border-color: #b97e00; background: #fff7e6; }
.day-card:hover { transform: translateY(-2px); border-color: #8a949b; }
.day-card.selected:hover { border-color: #b97e00; }
.day-label { font-size: 0.8em; color: #5e6a71; margin-bottom: 4px; }
.day-label.today-label { color: #2b7fbf; font-weight: 700; }
.day-rain { font-size: 0.85em; color: #8a949b; margin-bottom: 8px; }
.day-rain .amount { color: #2b7fbf; font-weight: 600; }
.day-prob { font-size: 2em; font-weight: 700; }
.day-risk { font-size: 0.7em; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }
.day-bar { height: 4px; border-radius: 2px; background: #dfe5ea; margin-top: 8px; overflow: hidden; }
.day-bar-fill { height: 100%; border-radius: 2px; transition: width 0.5s; }
.day-source { font-size: 0.65em; color: #8a949b; margin-top: 6px; }

/* Basin detail */
.basins { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }
@media (max-width: 700px) { .basins { grid-template-columns: 1fr; } }
.basin-card { background: #ffffff; border-radius: 12px; padding: 16px; border-left: 4px solid #dfe5ea; }
.basin-name { font-size: 0.9em; color: #5e6a71; margin-bottom: 6px; }
.basin-prob { font-size: 1.8em; font-weight: 700; }
.basin-bar { height: 6px; border-radius: 3px; background: #dfe5ea; margin-top: 8px; overflow: hidden; }
.basin-bar-fill { height: 100%; border-radius: 3px; transition: width 0.5s; }
.basin-detail { font-size: 0.8em; color: #8a949b; margin-top: 8px; }

/* Beach status */
.beach-section { background: #ffffff; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
.beach-section h2 { color: #2b7fbf; font-size: 1.1em; margin-bottom: 12px; }
.beach-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.beach-item { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 6px; font-size: 0.85em; background: #eef1f4; }
.beach-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.beach-dot.safe { background: #3c9160; }
.beach-dot.posted { background: #d4763a; }
.beach-dot.cso { background: #d15c5c; animation: blink 1.5s infinite; }
.beach-dot.not_sampled { background: #8a949b; }

/* Ground truth overlay (historical mode) */
.ground-truth { background: #ffffff; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 2px solid #dfe5ea; }
.ground-truth.has-cso { border-color: #d15c5c; }
.ground-truth h2 { color: #b97e00; font-size: 1.1em; margin-bottom: 4px; }
.ground-truth .gt-subtitle { color: #5e6a71; font-size: 0.85em; margin-bottom: 14px; }
.ground-truth .gt-verdict { padding: 10px 16px; border-radius: 8px; margin-bottom: 14px; font-weight: 600; font-size: 0.95em; }
.gt-verdict.cso-yes { background: #d15c5c; color: #fff; }
.gt-verdict.cso-no { background: #3c9160; color: #fff; }
.gt-verdict.no-data { background: #eef1f4; color: #5e6a71; border: 1px dashed #dfe5ea; }

.gt-day-tabs { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
.gt-day-tab { padding: 6px 14px; border-radius: 6px; background: #eef1f4; color: #5e6a71; cursor: pointer; font-size: 0.8em; border: 1px solid #dfe5ea; transition: all 0.2s; }
.gt-day-tab:hover { border-color: #b97e00; }
.gt-day-tab.active { background: #e3eefb; color: #1f6fb0; border-color: #1f6fb0; }
.gt-day-tab .tab-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 4px; }

.gt-basin-group { margin-bottom: 12px; }
.gt-basin-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px solid #dfe5ea; }
.gt-basin-name { font-size: 0.9em; font-weight: 600; color: #26272a; }
.gt-basin-count { font-size: 0.75em; color: #5e6a71; }
.gt-basin-model { font-size: 0.75em; padding: 2px 8px; border-radius: 4px; font-weight: 600; }

.gt-station-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 6px; }
.gt-station { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 6px; background: #eef1f4; font-size: 0.82em; }
.gt-station-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.gt-station-dot.elevated { background: #d15c5c; }
.gt-station-dot.clean { background: #3c9160; }
.gt-station-name { flex: 1; color: #3a4a52; }
.gt-station-value { color: #8a949b; font-size: 0.9em; }
.gt-station-value.over { color: #d15c5c; font-weight: 600; }

.gt-no-samples { color: #8a949b; font-style: italic; font-size: 0.85em; padding: 10px; }

footer { text-align: center; padding: 20px; color: #8a949b; font-size: 0.8em; }
footer a { color: #2b7fbf; text-decoration: none; }
.refresh-info { font-size: 0.8em; color: #8a949b; text-align: center; margin-bottom: 15px; }
</style>
</head>
<body>
<div class="container">
<header>
    <h1><span class="live-dot"></span> SF CSO Live Forecast</h1>
    <div class="subtitle">Combined Sewer Overflow risk — updated every 30 minutes</div>
</header>

<div class="mode-bar">
    <button class="mode-btn active" id="btnLive" onclick="setMode('live')">📡 Live Forecast</button>
    <button class="mode-btn" id="btnHistorical" onclick="setMode('historical')">📅 Historical</button>
    <div id="datePickerWrap" style="display:none;">
        <input type="date" class="date-picker" id="datePicker" min="__MIN_DATE__" max="__MAX_DATE__" value="__DEFAULT_DATE__">
        <span class="historical-label" id="histLabel"></span>
    </div>
</div>

<div id="riskBanner" class="risk-banner risk-none">Loading...</div>
<div class="refresh-info" id="refreshInfo"></div>

<!-- 7-day timeline (yesterday + today + 5 forecast days) -->
<div class="timeline" id="timeline"></div>

<!-- Basin detail for today -->
<div class="basins" id="basins"></div>

<!-- Ground truth overlay (historical mode only) -->
<div id="groundTruth" class="ground-truth" style="display:none;">
    <h2>🔬 Ground Truth — Actual Bacteria Results</h2>
    <div class="gt-subtitle">SF Gov water quality samples compared to model predictions</div>
    <div id="gtVerdict" class="gt-verdict no-data"></div>
    <div class="gt-day-tabs" id="gtDayTabs"></div>
    <div id="gtContent"></div>
</div>

<!-- Current beach status -->
<div class="beach-section">
    <h2>🏖️ Current Beach Status (SFPUC Real-Time)</h2>
    <div class="beach-grid" id="beachGrid"></div>
</div>

<footer>
    <p>📞 1-877-SFBEACH (1-877-732-3224) or 415-242-2214</p>
    <p>⚠️ Avoid water contact during and 72 hours after rain</p>
    <p style="margin-top:8px;">
        <a href="https://webapps.sfpuc.org/sapps/beachesandbay.html">SFPUC Beach Map</a> ·
        Model: GBM trained on 6 years of rain + bacteria data (AUC 0.870)
    </p>
</footer>
</div>

<script>
function riskInfo(prob) {
    if (prob >= 0.75) return { label: 'EXTREME', color: '#d15c5c', bg: '#fbe0e0', cls: 'extreme' };
    if (prob >= 0.50) return { label: 'HIGH', color: '#c2521a', bg: '#fbe9dd', cls: 'high' };
    if (prob >= 0.25) return { label: 'MODERATE', color: '#b97e00', bg: '#fbf0d6', cls: 'moderate' };
    if (prob >= 0.10) return { label: 'LOW', color: '#6b8e23', bg: '#eef6dc', cls: 'low' };
    return { label: 'MINIMAL', color: '#3c9160', bg: '#e3f2ea', cls: 'none' };
}

const basinLabels = {
    citywide: { icon: '🏙️', name: 'City-wide CSO', desc: '3+ stations elevated across 2+ basins' },
    westside: { icon: '🌊', name: 'Westside', desc: 'Ocean Beach, Baker Beach, China Beach' },
    north_shore: { icon: '🏖️', name: 'North Shore', desc: 'Crissy Field, Aquatic Park' },
    southeast: { icon: '⚓', name: 'Southeast', desc: 'Islais Creek, Candlestick Point' },
};

let currentMode = 'live';
let currentData = null;  // Store last rendered data for day selection

function setMode(mode) {
    currentMode = mode;
    document.getElementById('btnLive').className = 'mode-btn' + (mode === 'live' ? ' active' : '');
    document.getElementById('btnHistorical').className = 'mode-btn' + (mode === 'historical' ? ' active' : '');
    document.getElementById('datePickerWrap').style.display = mode === 'historical' ? 'inline-flex' : 'none';
    loadData();
}

document.getElementById('datePicker').addEventListener('change', () => { if (currentMode === 'historical') loadData(); });

async function loadData() {
    try {
        let data;
        if (currentMode === 'historical') {
            const date = document.getElementById('datePicker').value;
            if (!date) return;
            document.getElementById('histLabel').textContent = '⏳ Loading...';

            // Fetch predictions and bacteria data in parallel
            const [predResp, bacteriaResp] = await Promise.all([
                fetch('/api/historical?date=' + date),
                fetch('/api/bacteria?date=' + date),
            ]);
            const predData = await predResp.json();
            const bacteriaData = await bacteriaResp.json();

            if (predData.error) {
                document.getElementById('riskBanner').textContent = '⚠️ ' + predData.error;
                document.getElementById('histLabel').textContent = '❌ ' + predData.error;
                return;
            }
            // Wrap in same format as live data
            data = {
                predictions: predData.predictions,
                beach_status: [],
                historical_date: date,
                bacteria: bacteriaData,
            };
            document.getElementById('histLabel').textContent = '📅 Showing ' + date;
        } else {
            const resp = await fetch('/api/data');
            data = await resp.json();
        }
        render(data);
    } catch(e) {
        document.getElementById('riskBanner').textContent = '⚠️ Error loading data: ' + e.message;
    }
}

function render(data) {
    const preds = data.predictions || {};
    const beaches = data.beach_status || [];
    const isHistorical = !!data.historical_date;

    // Sort days
    const days = Object.values(preds).sort((a, b) => a.day_offset - b.day_offset);

    // Find today
    const today = days.find(d => d.is_today);
    const todayMax = today ? Math.max(...Object.values(today.predictions)) : 0;
    const overallMax = Math.max(...days.map(d => Math.max(...Object.values(d.predictions))));

    // Risk banner
    const banner = document.getElementById('riskBanner');
    const bannerRisk = riskInfo(todayMax);
    banner.className = 'risk-banner risk-' + bannerRisk.cls;
    const prefix = isHistorical ? '📅 ' + data.historical_date + ': ' : '';
    if (todayMax >= 0.50) {
        banner.innerHTML = prefix + '🚨 ' + bannerRisk.label + ' CSO RISK — Avoid water contact at affected beaches';
    } else if (todayMax >= 0.25) {
        banner.innerHTML = prefix + '⚠️ ' + bannerRisk.label + ' CSO RISK — Monitor conditions closely';
    } else if (todayMax >= 0.10) {
        banner.innerHTML = prefix + '🟡 ' + bannerRisk.label + ' CSO RISK — Some rain, worth watching';
    } else {
        banner.innerHTML = prefix + '✅ MINIMAL CSO RISK — Conditions favorable for beach recreation';
    }

    if (!isHistorical) {
        // Check if any forecast day is worse
        const futureDays = days.filter(d => d.is_forecast);
        if (futureDays.length > 0) {
            const futureMax = Math.max(...futureDays.map(d => Math.max(...Object.values(d.predictions))));
            if (futureMax > todayMax + 0.15 && futureMax >= 0.25) {
                const futureRisk = riskInfo(futureMax);
                banner.innerHTML += '<br><span style="font-size:0.8em;opacity:0.9;">⚠️ Higher risk coming: ' + futureRisk.label + ' (' + Math.round(futureMax*100) + '%) in forecast</span>';
            }
        }
    }

    // Refresh info
    const refreshEl = document.getElementById('refreshInfo');
    if (data.last_refresh) {
        const t = new Date(data.last_refresh);
        refreshEl.textContent = 'Last updated: ' + t.toLocaleTimeString() + ' · Auto-refreshes every 30 min';
    }

    // Store data for day selection
    currentData = { days, preds, isHistorical, data };

    // Timeline
    const timeline = document.getElementById('timeline');
    timeline.innerHTML = '';
    days.forEach(day => {
        const maxProb = Math.max(...Object.values(day.predictions));
        const risk = riskInfo(maxProb);
        const pct = Math.round(maxProb * 100);

        const card = document.createElement('div');
        card.className = 'day-card' + (day.is_today ? ' today' : '');
        card.dataset.date = day.date;
        card.innerHTML = `
            <div class="day-label ${day.is_today ? 'today-label' : ''}">${day.label}</div>
            <div class="day-rain">☔ <span class="amount">${day.rain_inches.toFixed(2)}"</span></div>
            <div class="day-prob" style="color:${risk.color}">${pct}%</div>
            <div class="day-risk" style="color:${risk.color}">${risk.label}</div>
            <div class="day-bar"><div class="day-bar-fill" style="width:${pct}%;background:${risk.color}"></div></div>
            <div class="day-source">${day.is_forecast ? '📡 forecast' : '📊 observed'}</div>
        `;
        card.onclick = () => selectDay(day);
        timeline.appendChild(card);
    });

    // Show basin detail for "today" by default
    selectDay(today || days[0]);

    // Beach status
    const beachGrid = document.getElementById('beachGrid');
    beachGrid.innerHTML = '';
    if (isHistorical) {
        beachGrid.innerHTML = '<div style="color:#8a949b;">Real-time beach status not available for historical dates — see Ground Truth section below for actual bacteria results</div>';
    } else if (beaches.length === 0) {
        beachGrid.innerHTML = '<div style="color:#8a949b;">Unable to fetch beach status</div>';
    }
    // Sort: CSO first, then posted, then safe
    const order = { cso: 0, posted: 1, safe: 2, not_sampled: 3, unknown: 4 };
    beaches.sort((a, b) => (order[a.status] || 9) - (order[b.status] || 9));
    beaches.forEach(b => {
        const item = document.createElement('div');
        item.className = 'beach-item';
        const statusLabel = b.status === 'cso' ? '🚨 CSO' : b.status === 'posted' ? '⚠️ Posted' : b.status === 'safe' ? '✅ Safe' : '⚪ No data';
        item.innerHTML = `<span class="beach-dot ${b.status}"></span><span>${b.name}</span><span style="margin-left:auto;font-size:0.75em;color:#8a949b;">${statusLabel}</span>`;
        beachGrid.appendChild(item);
    });

    // Ground truth overlay (historical mode only)
    renderGroundTruth(data, preds);
}

function selectDay(day) {
    if (!day || !currentData) return;

    // Update timeline selection highlight
    document.querySelectorAll('.day-card').forEach(c => {
        c.classList.remove('selected');
        if (c.dataset.date === day.date) c.classList.add('selected');
    });

    // Update basin detail for selected day
    const basinsEl = document.getElementById('basins');
    basinsEl.innerHTML = '';
    for (const [key, prob] of Object.entries(day.predictions)) {
        const info = basinLabels[key] || { icon: '📍', name: key, desc: '' };
        const risk = riskInfo(prob);
        const pct = Math.round(prob * 100);

        const card = document.createElement('div');
        card.className = 'basin-card';
        card.style.borderLeftColor = risk.color;
        card.innerHTML = `
            <div class="basin-name">${info.icon} ${info.name}</div>
            <div class="basin-prob" style="color:${risk.color}">${pct}%</div>
            <div class="basin-bar"><div class="basin-bar-fill" style="width:${pct}%;background:${risk.color}"></div></div>
            <div class="basin-detail">${info.desc} · 2d rain: ${day.rain_2d_cum.toFixed(2)}" · 3d: ${day.rain_3d_cum.toFixed(2)}"</div>
        `;
        basinsEl.appendChild(card);
    }

    // Update ground truth to show the selected day's bacteria data
    if (currentData.isHistorical && currentData.data.bacteria && currentData.data.bacteria.has_data) {
        const bDays = currentData.data.bacteria.days;
        if (bDays[day.date]) {
            // Auto-click the matching ground truth tab
            document.querySelectorAll('.gt-day-tab').forEach(tab => {
                if (tab.textContent.includes(day.date)) tab.click();
            });
        }
    }
}

// Basin key mapping for matching predictions to ground truth
const basinKeyMap = { 'Westside': 'westside', 'North Shore': 'north_shore', 'Southeast': 'southeast' };

function renderGroundTruth(data, preds) {
    const gtEl = document.getElementById('groundTruth');
    const gtVerdict = document.getElementById('gtVerdict');
    const gtTabs = document.getElementById('gtDayTabs');
    const gtContent = document.getElementById('gtContent');

    if (!data.bacteria || !data.bacteria.has_data || !data.historical_date) {
        gtEl.style.display = data.historical_date ? 'block' : 'none';
        if (data.historical_date) {
            gtEl.className = 'ground-truth';
            gtVerdict.className = 'gt-verdict no-data';
            gtVerdict.textContent = '📭 No bacteria samples found for this date range — SFPUC samples weekly, so data may not be available for every day.';
            gtTabs.innerHTML = '';
            gtContent.innerHTML = '';
        }
        return;
    }

    gtEl.style.display = 'block';
    const bacteria = data.bacteria;
    const bDays = bacteria.days;
    const sortedDates = Object.keys(bDays).sort();

    // Find the target date's data (or closest)
    const targetDate = data.historical_date;
    let hasCso = false;
    for (const d of sortedDates) {
        if (bDays[d].summary.likely_cso) hasCso = true;
    }
    gtEl.className = 'ground-truth' + (hasCso ? ' has-cso' : '');

    // Verdict banner
    const targetDay = bDays[targetDate];
    if (targetDay) {
        const s = targetDay.summary;
        if (s.likely_cso) {
            gtVerdict.className = 'gt-verdict cso-yes';
            gtVerdict.innerHTML = '🚨 CSO EVENT CONFIRMED — ' + s.elevated_stations + '/' + s.total_stations +
                ' stations elevated across ' + s.elevated_basins + ' basin(s) · ' +
                s.elevated_samples + '/' + s.total_samples + ' samples exceeded standards';
        } else if (s.elevated_stations > 0) {
            gtVerdict.className = 'gt-verdict cso-no';
            gtVerdict.innerHTML = '⚠️ Some elevated readings — ' + s.elevated_stations + '/' + s.total_stations +
                ' stations elevated, but not enough for CSO classification (' + s.elevated_basins + ' basin)';
        } else {
            gtVerdict.className = 'gt-verdict cso-no';
            gtVerdict.innerHTML = '✅ All clear — 0/' + s.total_stations + ' stations exceeded standards';
        }
    } else {
        // No samples on exact target date, show closest
        const closestDate = sortedDates.length > 0 ? sortedDates[0] : null;
        if (closestDate) {
            const s = bDays[closestDate].summary;
            const label = s.likely_cso ? '🚨 CSO EVENT' : s.elevated_stations > 0 ? '⚠️ Elevated' : '✅ Clear';
            gtVerdict.className = 'gt-verdict' + (s.likely_cso ? ' cso-yes' : ' cso-no');
            gtVerdict.innerHTML = label + ' on nearest sample date (' + closestDate + ') — ' +
                s.elevated_stations + '/' + s.total_stations + ' stations elevated · No samples on ' + targetDate;
        } else {
            gtVerdict.className = 'gt-verdict no-data';
            gtVerdict.textContent = '📭 No samples found near ' + targetDate;
        }
    }

    // Day tabs
    gtTabs.innerHTML = '';
    let activeDate = sortedDates.includes(targetDate) ? targetDate : (sortedDates[0] || null);
    sortedDates.forEach(d => {
        const s = bDays[d].summary;
        const dotColor = s.likely_cso ? '#d15c5c' : s.elevated_stations > 0 ? '#b97e00' : '#3c9160';
        const isTarget = d === targetDate;
        const tab = document.createElement('div');
        tab.className = 'gt-day-tab' + (d === activeDate ? ' active' : '');
        tab.innerHTML = '<span class="tab-dot" style="background:' + dotColor + '"></span>' +
            d + (isTarget ? ' ★' : '') +
            ' <span style="color:#8a949b;">(' + s.elevated_stations + '/' + s.total_stations + ')</span>';
        tab.onclick = () => {
            document.querySelectorAll('.gt-day-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            renderGroundTruthDay(bDays[d], d, preds);
        };
        gtTabs.appendChild(tab);
    });

    // Render the active day
    if (activeDate) {
        renderGroundTruthDay(bDays[activeDate], activeDate, preds);
    }
}

function renderGroundTruthDay(dayData, dateStr, preds) {
    const gtContent = document.getElementById('gtContent');
    const summary = dayData.summary;
    const basins = summary.basins;

    // Get model prediction for this date if available
    const dayPred = preds[dateStr];

    let html = '';

    // Order basins: Westside, North Shore, Southeast
    const basinOrder = ['Westside', 'North Shore', 'Southeast'];
    for (const basinName of basinOrder) {
        const basin = basins[basinName];
        if (!basin) continue;

        const basinKey = basinKeyMap[basinName];
        const modelProb = dayPred ? dayPred.predictions[basinKey] : null;

        html += '<div class="gt-basin-group">';
        html += '<div class="gt-basin-header">';
        html += '<span class="gt-basin-name">' + basinName + '</span>';
        html += '<span class="gt-basin-count">' + basin.elevated + '/' + basin.total + ' stations elevated</span>';

        // Model prediction badge
        if (modelProb !== null && modelProb !== undefined) {
            const pct = Math.round(modelProb * 100);
            const risk = riskInfo(modelProb);
            const actual = basin.elevated > 0;
            const predicted = modelProb >= 0.10;
            const match = actual === predicted;
            html += '<span class="gt-basin-model" style="background:' + risk.bg + ';color:' + risk.color + ';">' +
                'Model: ' + pct + '%</span>';
            html += '<span style="font-size:0.75em;">' + (match ? '✅' : '❌') + '</span>';
        }

        html += '</div>';

        // Station grid
        html += '<div class="gt-station-grid">';
        // Sort: elevated first
        const stations = basin.stations.sort((a, b) => (b.elevated ? 1 : 0) - (a.elevated ? 1 : 0));
        for (const st of stations) {
            const dotClass = st.elevated ? 'elevated' : 'clean';
            const enteroStr = st.max_entero !== null ?
                (st.max_entero > 104 ?
                    '<span class="gt-station-value over">Entero: ' + Math.round(st.max_entero) + '</span>' :
                    '<span class="gt-station-value">Entero: ' + Math.round(st.max_entero) + '</span>') :
                '<span class="gt-station-value">—</span>';
            const countStr = '<span class="gt-station-value">' + st.exceedance_count + '/' + st.sample_count + '</span>';

            html += '<div class="gt-station">' +
                '<span class="gt-station-dot ' + dotClass + '"></span>' +
                '<span class="gt-station-name">' + st.name + '</span>' +
                enteroStr + countStr +
                '</div>';
        }
        html += '</div></div>';
    }

    gtContent.innerHTML = html;
}

// Initial load
loadData();

// Auto-refresh every 5 minutes (data itself refreshes every 30 min server-side)
setInterval(loadData, 300000);
</script>
</body>
</html>"""


def main():
    # Initial data fetch
    print("Fetching initial data...")
    LIVE.refresh()

    # Start background refresh thread
    thread = Thread(target=refresh_loop, args=(LIVE, 1800), daemon=True)
    thread.start()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  SF CSO Live Forecast Dashboard                              ║
╠══════════════════════════════════════════════════════════════╣
║  🌐 Open: http://localhost:{PORT}                               ║
║  📡 Data: Open-Meteo (ECMWF) + SFPUC real-time               ║
║  🔄 Auto-refreshes every 30 minutes                          ║
║  Press Ctrl+C to stop                                        ║
╚══════════════════════════════════════════════════════════════╝
""")

    with socketserver.TCPServer(("", PORT), LiveDashboardHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Shutting down...")


if __name__ == "__main__":
    main()
