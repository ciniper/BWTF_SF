#!/usr/bin/env python3
"""
Weather Forecast Model Data Collector

Collects forecast data from multiple weather models for comparison:
1. GFS (Global Forecast System) — NOAA, free API
2. ECMWF (via Open-Meteo) — European model, most reliable at 3-7 days
3. ICON (DWD) — German model, good for Pacific storms

Per Rubin's guidance:
- 1-2 days: Models should agree. If they DON'T, that's a signal.
- 3-7 days: ECMWF is most reliable.
- 7+ days: Pure guesswork.

We use Open-Meteo as the unified API — it provides GFS, ECMWF, and ICON
data through a single free API with no key required.

Windy publishes ECMWF but their license does NOT allow API access.
Open-Meteo provides the same ECMWF data legally.
"""

import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "raw" / "forecasts"

# Open-Meteo API (free, no key, provides multiple models)
OPEN_METEO_BASE = "https://api.open-meteo.com/v1"

# San Francisco coordinates
SF_LAT = 37.7749
SF_LON = -122.4194

# Forecast models available via Open-Meteo
MODELS = {
    "gfs": {
        "url": f"{OPEN_METEO_BASE}/gfs",
        "name": "GFS (NOAA)",
        "reliability": "High at 1-2 days, moderate at 3-5 days",
    },
    "ecmwf": {
        "url": f"{OPEN_METEO_BASE}/ecmwf",
        "name": "ECMWF (European)",
        "reliability": "Most reliable at 3-7 days (per Rubin)",
    },
    "icon": {
        "url": f"{OPEN_METEO_BASE}/dwd-icon",
        "name": "ICON (DWD Germany)",
        "reliability": "Good for Pacific storms, 1-5 days",
    },
}

# Hourly variables we need for CSO prediction
HOURLY_VARS = [
    "precipitation",           # Total precipitation (mm)
    "rain",                    # Rain only, no snow (mm)
    "temperature_2m",          # Temperature at 2m (°C)
    "wind_speed_10m",          # Wind speed at 10m (km/h)
    "wind_direction_10m",      # Wind direction at 10m (degrees)
    "wind_gusts_10m",          # Wind gusts (km/h)
    "pressure_msl",            # Sea level pressure (hPa)
]


class ForecastCollector:
    """
    Collects forecast data from multiple weather models via Open-Meteo.
    
    Open-Meteo provides free access to GFS, ECMWF, and ICON model data
    with no API key required. This is the legal alternative to scraping
    Windy for ECMWF data.
    
    Rate limit: 10,000 requests/day (generous for our use case).
    """
    
    def __init__(self):
        self.session = requests.Session()
    
    def fetch_model(self, model: str = "ecmwf",
                    forecast_days: int = 7) -> pd.DataFrame:
        """
        Fetch hourly forecast from a specific model.
        
        Args:
            model: Model name ("gfs", "ecmwf", "icon")
            forecast_days: Number of days to forecast (max varies by model)
            
        Returns:
            DataFrame with hourly forecast data
        """
        if model not in MODELS:
            print(f"Unknown model: {model}. Available: {list(MODELS.keys())}")
            return pd.DataFrame()
        
        model_info = MODELS[model]
        
        # Build request — ECMWF endpoint has slightly different params
        params = {
            "latitude": SF_LAT,
            "longitude": SF_LON,
            "hourly": ",".join(HOURLY_VARS),
            "timezone": "America/Los_Angeles",
            "forecast_days": forecast_days,
        }
        
        # ECMWF via Open-Meteo uses different variable names for some fields
        if model == "ecmwf":
            # ECMWF doesn't have all the same variables
            ecmwf_vars = ["precipitation", "temperature_2m", "wind_speed_10m",
                          "wind_direction_10m", "pressure_msl"]
            params["hourly"] = ",".join(ecmwf_vars)
        
        try:
            response = self.session.get(model_info["url"], params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching {model_info['name']} forecast: {e}")
            return pd.DataFrame()
        
        # Parse hourly data
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        
        if not times:
            print(f"No data returned from {model_info['name']}")
            return pd.DataFrame()
        
        records = []
        for i, time_str in enumerate(times):
            record = {
                "timestamp": pd.Timestamp(time_str),
                "model": model,
            }
            for var in HOURLY_VARS:
                values = hourly.get(var, [])
                record[var] = values[i] if i < len(values) else None
            
            # Convert precipitation from mm to inches
            if record.get("precipitation") is not None:
                record["precipitation_inches"] = record["precipitation"] / 25.4
            if record.get("rain") is not None:
                record["rain_inches"] = record["rain"] / 25.4
            
            records.append(record)
        
        return pd.DataFrame(records)
    
    def fetch_all_models(self, forecast_days: int = 7) -> dict:
        """
        Fetch forecasts from all models for comparison.
        
        Args:
            forecast_days: Number of days to forecast
            
        Returns:
            Dict of DataFrames keyed by model name
        """
        results = {}
        for model in MODELS:
            print(f"  Fetching {MODELS[model]['name']}...")
            df = self.fetch_model(model, forecast_days)
            results[model] = df
            print(f"    Got {len(df)} hourly records")
        return results
    
    def compare_models(self, forecast_days: int = 3) -> pd.DataFrame:
        """
        Compare precipitation forecasts across models.
        
        This is key for confidence assessment:
        - Models agree → high confidence
        - Models disagree → flag for attention (per Rubin)
        
        Args:
            forecast_days: Days to compare
            
        Returns:
            DataFrame with side-by-side model comparison
        """
        all_data = self.fetch_all_models(forecast_days)
        
        # Merge on timestamp
        merged = None
        for model, df in all_data.items():
            if df.empty:
                continue
            
            # Select key columns and rename
            cols = df[["timestamp", "precipitation_inches"]].copy()
            cols = cols.rename(columns={"precipitation_inches": f"precip_{model}_in"})
            
            if merged is None:
                merged = cols
            else:
                merged = merged.merge(cols, on="timestamp", how="outer")
        
        if merged is None:
            return pd.DataFrame()
        
        merged = merged.sort_values("timestamp").reset_index(drop=True)
        
        # Calculate agreement metrics
        precip_cols = [c for c in merged.columns if c.startswith("precip_") and c.endswith("_in")]
        if len(precip_cols) >= 2:
            merged["precip_mean_in"] = merged[precip_cols].mean(axis=1)
            merged["precip_std_in"] = merged[precip_cols].std(axis=1)
            merged["models_agree"] = merged["precip_std_in"] < 0.05  # Within 0.05" = agreement
        
        return merged
    
    def get_cumulative_forecast(self, model: str = "ecmwf",
                                 windows: List[int] = [3, 6, 12, 24]) -> pd.DataFrame:
        """
        Calculate cumulative rainfall forecasts over rolling windows.
        
        This is the key metric for CSO prediction:
        - Running 24-hour total approaching 0.75" → of interest
        - Running 24-hour total > 1.0" → definitely interesting
        
        Args:
            model: Which model to use
            windows: Rolling window sizes in hours
            
        Returns:
            DataFrame with cumulative rainfall columns
        """
        df = self.fetch_model(model)
        if df.empty:
            return pd.DataFrame()
        
        # Calculate rolling sums
        for window in windows:
            col_name = f"cumulative_{window}h_in"
            df[col_name] = df["precipitation_inches"].rolling(
                window=window, min_periods=1
            ).sum()
        
        return df


def collect_all(save: bool = True) -> dict:
    """
    Collect forecasts from all models and save.
    
    Args:
        save: Whether to save to data/raw/forecasts/
        
    Returns:
        Dict with model DataFrames and comparison
    """
    print("Collecting weather forecast model data...")
    
    collector = ForecastCollector()
    results = collector.fetch_all_models(forecast_days=7)
    
    # Model comparison
    print("\n  Comparing models (next 3 days)...")
    comparison = collector.compare_models(forecast_days=3)
    results["comparison"] = comparison
    
    # Cumulative forecast (ECMWF — most reliable per Rubin)
    print("  Calculating cumulative rainfall forecast (ECMWF)...")
    cumulative = collector.get_cumulative_forecast("ecmwf")
    results["ecmwf_cumulative"] = cumulative
    
    if save:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        for name, df in results.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                path = DATA_DIR / f"forecast_{name}_{timestamp}.csv"
                df.to_csv(path, index=False)
                print(f"  Saved {path}")
    
    return results


if __name__ == "__main__":
    results = collect_all()
    
    print("\n" + "=" * 60)
    print("FORECAST SUMMARY")
    print("=" * 60)
    
    # Show cumulative forecast highlights
    cum = results.get("ecmwf_cumulative")
    if cum is not None and not cum.empty:
        for window in [3, 6, 12, 24]:
            col = f"cumulative_{window}h_in"
            if col in cum.columns:
                max_val = cum[col].max()
                max_time = cum.loc[cum[col].idxmax(), "timestamp"]
                print(f"  Max {window}h cumulative: {max_val:.2f}\" at {max_time}")
        
        # Flag interesting periods (Rubin's thresholds)
        if "cumulative_24h_in" in cum.columns:
            interesting = cum[cum["cumulative_24h_in"] >= 0.75]
            if not interesting.empty:
                print(f"\n  ⚠️ 24h cumulative ≥ 0.75\" in {len(interesting)} hours")
                print(f"     Peak: {interesting['cumulative_24h_in'].max():.2f}\"")
            
            definitely = cum[cum["cumulative_24h_in"] >= 1.0]
            if not definitely.empty:
                print(f"  🚨 24h cumulative ≥ 1.0\" in {len(definitely)} hours")
    
    # Show model agreement
    comp = results.get("comparison")
    if comp is not None and not comp.empty and "models_agree" in comp.columns:
        agreement_pct = comp["models_agree"].mean() * 100
        print(f"\n  Model agreement: {agreement_pct:.0f}%")
        if agreement_pct < 80:
            print("  ⚠️ Models disagree significantly — uncertainty is high")
