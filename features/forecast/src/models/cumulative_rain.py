#!/usr/bin/env python3
"""
Cumulative Rainfall Calculator

Computes rolling cumulative rainfall metrics that are the primary
input features for the CSO prediction model.

Key metrics (from Rubin's domain expertise):
- 3-hour total: ~0.5" with preceding rain → interesting
- Running 24-hour total: ~0.75" → of interest, >1.0" → definitely interesting
- Antecedent moisture: prior 48-72 hours of rain affects soil saturation
  and sewer system capacity

This module also handles:
- Multi-station aggregation (different gauges for different basins)
- Missing data interpolation
- Conversion between mm and inches
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, List, Dict


# Rolling window sizes (hours) for cumulative calculations
STANDARD_WINDOWS = [1, 3, 6, 12, 24, 48, 72]

# Rubin's thresholds for the Marina District reference
# These are starting points — the model should learn basin-specific thresholds
REFERENCE_THRESHOLDS = {
    "3h_with_antecedent": 0.5,   # 3-hour total with preceding rain
    "24h_caution": 0.75,          # Running 24-hour → of interest
    "24h_likely_cso": 1.0,        # Running 24-hour → definitely interesting
}


def compute_cumulative(df: pd.DataFrame,
                       precip_col: str = "precip_1h_inches",
                       windows: List[int] = None) -> pd.DataFrame:
    """
    Compute rolling cumulative rainfall for multiple time windows.
    
    Args:
        df: DataFrame with timestamp and precipitation column
        precip_col: Name of the hourly precipitation column (in inches)
        windows: List of window sizes in hours (default: standard set)
        
    Returns:
        DataFrame with added cumulative columns
    """
    if windows is None:
        windows = STANDARD_WINDOWS
    
    result = df.copy()
    
    # Ensure sorted by time
    if "timestamp" in result.columns:
        result = result.sort_values("timestamp").reset_index(drop=True)
    
    # Fill NaN precipitation with 0 (no rain = 0)
    if precip_col in result.columns:
        result[precip_col] = result[precip_col].fillna(0)
    else:
        print(f"Warning: column '{precip_col}' not found in DataFrame")
        return result
    
    for window in windows:
        col_name = f"cumulative_{window}h_in"
        result[col_name] = result[precip_col].rolling(
            window=window, min_periods=1
        ).sum()
    
    return result


def compute_antecedent_moisture(df: pd.DataFrame,
                                 precip_col: str = "precip_1h_inches",
                                 lookback_hours: int = 72) -> pd.DataFrame:
    """
    Compute antecedent moisture index (AMI).
    
    Antecedent moisture matters because:
    - Rain on already-saturated ground → faster runoff → earlier CSO
    - Rain on dry ground → more absorption → delayed/no CSO
    
    Uses an exponential decay model: recent rain counts more than older rain.
    Half-life of ~24 hours (soil dries out over ~1-2 days).
    
    Args:
        df: DataFrame with timestamp and precipitation
        precip_col: Precipitation column name
        lookback_hours: Hours to look back for antecedent conditions
        
    Returns:
        DataFrame with antecedent_moisture_index column
    """
    result = df.copy()
    
    if precip_col not in result.columns:
        return result
    
    precip = result[precip_col].fillna(0).values
    
    # Exponential decay weights (half-life = 24 hours)
    half_life = 24
    decay_rate = np.log(2) / half_life
    weights = np.exp(-decay_rate * np.arange(lookback_hours))
    weights = weights / weights.sum()  # Normalize
    
    # Compute weighted sum using convolution
    ami = np.convolve(precip, weights, mode="full")[:len(precip)]
    result["antecedent_moisture_index"] = ami
    
    # Also compute simple binary: "was there significant rain in last 48h?"
    result["had_recent_rain_48h"] = result[precip_col].rolling(
        window=48, min_periods=1
    ).sum() > 0.1  # More than 0.1" in 48h
    
    return result


def compute_intensity_metrics(df: pd.DataFrame,
                               precip_col: str = "precip_1h_inches") -> pd.DataFrame:
    """
    Compute rainfall intensity metrics.
    
    Intensity matters for CSO prediction:
    - Slow, steady rain → sewer system can handle more total volume
    - Intense bursts → overwhelm system faster, even with less total rain
    
    Args:
        df: DataFrame with precipitation data
        precip_col: Precipitation column name
        
    Returns:
        DataFrame with intensity metrics
    """
    result = df.copy()
    
    if precip_col not in result.columns:
        return result
    
    precip = result[precip_col].fillna(0)
    
    # Peak intensity (max hourly rate in rolling windows)
    result["peak_intensity_3h"] = precip.rolling(window=3, min_periods=1).max()
    result["peak_intensity_6h"] = precip.rolling(window=6, min_periods=1).max()
    
    # Average intensity over windows
    result["avg_intensity_6h"] = precip.rolling(window=6, min_periods=1).mean()
    result["avg_intensity_12h"] = precip.rolling(window=12, min_periods=1).mean()
    
    # Is it currently raining? (> 0.01" in last hour)
    result["is_raining"] = precip > 0.01
    
    # Consecutive hours of rain
    rain_mask = (precip > 0.01).astype(int)
    groups = rain_mask.ne(rain_mask.shift()).cumsum()
    result["consecutive_rain_hours"] = rain_mask.groupby(groups).cumsum()
    
    return result


def compute_all_features(df: pd.DataFrame,
                          precip_col: str = "precip_1h_inches") -> pd.DataFrame:
    """
    Compute all rainfall features for model input.
    
    This is the main function to call — it computes:
    - Rolling cumulative totals (1h, 3h, 6h, 12h, 24h, 48h, 72h)
    - Antecedent moisture index
    - Intensity metrics
    - Threshold flags (based on Rubin's guidance)
    
    Args:
        df: DataFrame with timestamp and hourly precipitation
        precip_col: Precipitation column name (in inches)
        
    Returns:
        Feature-enriched DataFrame ready for model input
    """
    result = compute_cumulative(df, precip_col)
    result = compute_antecedent_moisture(result, precip_col)
    result = compute_intensity_metrics(result, precip_col)
    
    # Add threshold flags (Rubin's reference thresholds)
    if "cumulative_3h_in" in result.columns:
        result["flag_3h_interesting"] = (
            (result["cumulative_3h_in"] >= 0.5) & 
            result.get("had_recent_rain_48h", True)
        )
    
    if "cumulative_24h_in" in result.columns:
        result["flag_24h_caution"] = result["cumulative_24h_in"] >= 0.75
        result["flag_24h_likely_cso"] = result["cumulative_24h_in"] >= 1.0
    
    return result


def aggregate_stations(station_dfs: Dict[str, pd.DataFrame],
                        precip_col: str = "precip_1h_inches") -> pd.DataFrame:
    """
    Aggregate rainfall data from multiple stations.
    
    Different stations represent different drainage basins.
    We compute per-station features and also city-wide aggregates.
    
    Args:
        station_dfs: Dict of {station_id: DataFrame}
        precip_col: Precipitation column name
        
    Returns:
        Merged DataFrame with per-station and aggregate features
    """
    all_features = []
    
    for station_id, df in station_dfs.items():
        if df.empty or precip_col not in df.columns:
            continue
        
        features = compute_all_features(df, precip_col)
        
        # Rename columns to include station ID
        rename_cols = {}
        for col in features.columns:
            if col not in ("timestamp", "station_id"):
                rename_cols[col] = f"{station_id}_{col}"
        
        features = features.rename(columns=rename_cols)
        all_features.append(features[["timestamp"] + list(rename_cols.values())])
    
    if not all_features:
        return pd.DataFrame()
    
    # Merge all stations on timestamp
    merged = all_features[0]
    for df in all_features[1:]:
        merged = merged.merge(df, on="timestamp", how="outer")
    
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    
    # Compute city-wide aggregates (mean across stations)
    cum_cols = [c for c in merged.columns if "cumulative_24h_in" in c]
    if cum_cols:
        merged["city_avg_24h_in"] = merged[cum_cols].mean(axis=1)
        merged["city_max_24h_in"] = merged[cum_cols].max(axis=1)
    
    return merged


if __name__ == "__main__":
    # Demo with synthetic data
    print("Cumulative Rainfall Calculator — Demo")
    print("=" * 60)
    
    # Create synthetic hourly rainfall data (simulating a storm)
    hours = 72
    timestamps = pd.date_range(end=datetime.now(), periods=hours, freq="h")
    
    # Simulate a storm: dry → building → peak → tapering
    np.random.seed(42)
    rain = np.zeros(hours)
    # Storm arrives at hour 24, peaks at hour 36, tapers by hour 48
    storm_hours = range(24, 48)
    for h in storm_hours:
        intensity = 0.3 * np.exp(-0.5 * ((h - 36) / 4) ** 2)  # Gaussian peak
        rain[h] = max(0, intensity + np.random.normal(0, 0.02))
    
    df = pd.DataFrame({
        "timestamp": timestamps,
        "precip_1h_inches": rain,
    })
    
    # Compute all features
    features = compute_all_features(df)
    
    print(f"\nTotal rainfall: {features['precip_1h_inches'].sum():.2f} inches")
    print(f"Peak hourly: {features['precip_1h_inches'].max():.3f} inches")
    print(f"Max 3h cumulative: {features['cumulative_3h_in'].max():.2f} inches")
    print(f"Max 24h cumulative: {features['cumulative_24h_in'].max():.2f} inches")
    print(f"Max antecedent moisture: {features['antecedent_moisture_index'].max():.4f}")
    
    # Check threshold flags
    caution_hours = features["flag_24h_caution"].sum()
    cso_hours = features["flag_24h_likely_cso"].sum()
    print(f"\nHours at 24h caution (≥0.75\"): {caution_hours}")
    print(f"Hours at 24h likely CSO (≥1.0\"): {cso_hours}")
