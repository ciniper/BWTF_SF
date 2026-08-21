#!/usr/bin/env python3
"""
Historical Data Collector

Pulls the two key historical datasets and aligns them:
1. ACIS daily precipitation (SF Oceanside + SF Downtown, 2020-present)
2. SF Gov bacteria samples (all stations, 2020-present)

This gives us ~6 years of daily rain → bacteria correlation data,
which is more than enough to train the CSO threshold model.

Data sources:
- ACIS (Applied Climate Information System): https://data.rcc-acis.org
  Free, no API key, daily precip going back decades
- SF Gov Open Data: https://data.sfgov.org/resource/v3fv-x3ux.json
  Free, 19,840 bacteria samples from 2020-present

Key finding from exploratory analysis:
- Dry periods: ~3% of samples exceed Enterococcus standard
- After heavy rain (>1"): 50-90%+ of samples exceed standards
- Elevated readings persist 3-5 days after heavy rain
- Baker Beach (BAY#220) and Candlestick (BAY#320) are most sensitive
"""

import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# ACIS API
ACIS_URL = "https://data.rcc-acis.org/StnData"

# SF Gov API
SFGOV_URL = "https://data.sfgov.org/resource/v3fv-x3ux.json"

# Rain stations to collect (covering different basins)
RAIN_STATIONS = {
    "047767": {"name": "SF Oceanside", "basin": "Westside"},
    "047772": {"name": "SF Downtown", "basin": "North Shore / Southeast"},
}

# AB 411 single sample maximums
BACTERIA_THRESHOLDS = {
    "ENTERO": 104,
    "COLI_E": 235,
    "COLI_FECAL": 400,
    "COLI_TOTAL": 10000,
}

# Basin assignments for bacteria stations
STATION_BASINS = {
    "OCEAN#15_SL": "Westside", "OCEAN#15EAST_SL": "Westside",
    "OCEAN#16_SL": "Westside", "OCEAN#17_SL": "Westside",
    "OCEAN#18_SL": "Westside", "OCEAN#19_SL": "Westside",
    "OCEAN#20_SL": "Westside", "OCEAN#21_SL": "Westside",
    "OCEAN#21.1_SL": "Westside", "OCEAN#22_SL": "Westside",
    "BAY#300.1_SL": "Westside", "BAY#220_SL": "Westside",
    "BAY#230_SL": "Westside", "BAY#301.1_SL": "Westside",
    "BAY#301.2_SL": "Westside",
    "BAY#202.4_SL": "North Shore", "BAY#202.5_SL": "North Shore",
    "BAY#210.1_SL": "North Shore", "BAY#211_SL": "North Shore",
    "BAY#320_SL": "Southeast",
}


def fetch_historical_rain(start_date: str = "2016-01-01",
                           end_date: str = None) -> pd.DataFrame:
    # Start 2016-01-01: ground-truth CSD labels (data/csd/) begin Oct 2016 and
    # the 30-day antecedent windows need runway. ACIS has both gauges well
    # before this. Bacteria (SF Gov) still starts 2020-07 — that's the source's
    # own start, not ours.
    """
    Fetch daily precipitation from ACIS for all configured stations.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (default: today)
        
    Returns:
        DataFrame with date, station, precip_inches columns
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    
    all_records = []
    
    for station_id, info in RAIN_STATIONS.items():
        print(f"  Fetching rain data: {info['name']} ({station_id})...")
        
        params = {
            "sid": station_id,
            "sdate": start_date,
            "edate": end_date,
            "elems": [{"name": "pcpn", "interval": "dly", "duration": "dly"}],
            "output": "json",
        }
        
        try:
            r = requests.post(ACIS_URL, json=params, timeout=60)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    Error: {e}")
            continue
        
        for row in data.get("data", []):
            date_str = row[0]
            precip_str = row[1]
            
            # Handle missing/trace values
            if precip_str in ("M", "S"):
                precip = None
            elif precip_str == "T":
                precip = 0.001  # Trace
            else:
                try:
                    precip = float(precip_str)
                except ValueError:
                    precip = None
            
            all_records.append({
                "date": date_str,
                "rain_station": station_id,
                "rain_station_name": info["name"],
                "basin": info["basin"],
                "precip_inches": precip,
            })
        
        print(f"    Got {len(data.get('data', []))} days")
    
    df = pd.DataFrame(all_records)
    df["date"] = pd.to_datetime(df["date"])
    return df


def fetch_hourly_rain(start_date: str = "2016-01-01") -> pd.DataFrame:
    """Hourly precipitation from the Open-Meteo archive (ERA5), for the
    peak-intensity features (train_v2.build_hourly_features). Same source
    family the dashboard uses at inference — keep it that way (the v1
    precip_max lesson). Saves to data/raw/hourly_rain_openmeteo.csv."""
    frames = []
    start = pd.Timestamp(start_date)
    end_all = pd.Timestamp.now() - pd.Timedelta(days=6)  # archive lags ~5 days
    for y0 in range(start.year, end_all.year + 1, 3):
        chunk_start = max(start, pd.Timestamp(f"{y0}-01-01"))
        chunk_end = min(pd.Timestamp(f"{y0 + 2}-12-31"), end_all)
        if chunk_start > chunk_end:
            continue
        r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
            "latitude": 37.7749, "longitude": -122.4194,
            "hourly": "precipitation",
            "start_date": chunk_start.strftime("%Y-%m-%d"),
            "end_date": chunk_end.strftime("%Y-%m-%d"),
            "timezone": "America/Los_Angeles",
        }, timeout=120)
        r.raise_for_status()
        h = r.json()["hourly"]
        frames.append(pd.DataFrame({"timestamp": pd.to_datetime(h["time"]),
                                    "precip_mm": h["precipitation"]}))
        print(f"  hourly chunk {chunk_start.date()} → {chunk_end.date()}: {len(frames[-1])} hours")
        time.sleep(1)
    df = pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp")
    df["precip_inches"] = df["precip_mm"] / 25.4
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(RAW_DIR / "hourly_rain_openmeteo.csv", index=False)
    return df


def fetch_historical_bacteria(start_date: str = "2020-07-01",
                                end_date: str = None) -> pd.DataFrame:
    """
    Fetch all bacteria samples from SF Gov API.
    
    The API has a 50,000 row limit per request, but we have ~20,000 total.
    
    Args:
        start_date: Start date
        end_date: End date (default: today)
        
    Returns:
        DataFrame with sample_date, station, analyte, value, exceeds columns
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    
    print(f"  Fetching bacteria data from SF Gov API...")
    
    all_records = []
    offset = 0
    batch_size = 5000
    
    while True:
        params = {
            "$limit": batch_size,
            "$offset": offset,
            "$order": "sample_date ASC",
            "$where": f"sample_date >= '{start_date}T00:00:00' AND sample_date IS NOT NULL",
        }
        
        try:
            r = requests.get(SFGOV_URL, params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    Error at offset {offset}: {e}")
            break
        
        if not data:
            break
        
        for rec in data:
            if not isinstance(rec, dict):
                continue
            
            station = rec.get("source", "")
            analyte = rec.get("analyte", "")
            value_str = rec.get("data", "")
            sample_date = rec.get("sample_date", "")
            
            if not sample_date or not analyte:
                continue
            
            # Parse value
            if not value_str:
                value = None
            elif value_str.startswith("<"):
                value = float(value_str[1:]) / 2  # Half detection limit
            elif value_str.startswith(">"):
                value = float(value_str[1:])
            else:
                try:
                    value = float(value_str)
                except ValueError:
                    value = None
            
            # Check if exceeds standard
            threshold = BACTERIA_THRESHOLDS.get(analyte)
            exceeds = value > threshold if value is not None and threshold else False
            
            all_records.append({
                "sample_date": sample_date[:10],
                "station": station,
                "analyte": analyte,
                "value": value,
                "value_raw": value_str,
                "exceeds_standard": exceeds,
                "threshold": threshold,
                "basin": STATION_BASINS.get(station, "Unknown"),
            })
        
        offset += batch_size
        print(f"    Fetched {offset} records...")
        
        if len(data) < batch_size:
            break
    
    df = pd.DataFrame(all_records)
    df["sample_date"] = pd.to_datetime(df["sample_date"])
    print(f"    Total: {len(df)} bacteria samples")
    return df


def build_training_dataset(rain_df: pd.DataFrame,
                            bacteria_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the aligned training dataset: daily rain → bacteria exceedance rate.
    
    For each day with bacteria samples, compute:
    - Same-day and prior-day rainfall (0, 1, 2, 3 day lag)
    - Cumulative rainfall (2-day, 3-day, 5-day, 7-day windows)
    - Antecedent moisture (14-day and 30-day cumulative)
    - Bacteria exceedance rate (% of samples exceeding standards)
    - Whether this looks like a CSO event (multiple stations elevated)
    
    Returns:
        DataFrame ready for model training
    """
    print("Building training dataset...")
    
    # ── Pivot rain data to get daily precip per station ──
    rain_pivot = rain_df.pivot_table(
        index="date", columns="rain_station_name",
        values="precip_inches", aggfunc="first"
    ).reset_index()
    rain_pivot.columns.name = None
    
    # Average across stations for city-wide metric
    precip_cols = [c for c in rain_pivot.columns if c != "date"]
    rain_pivot["precip_avg"] = rain_pivot[precip_cols].mean(axis=1)
    rain_pivot["precip_max"] = rain_pivot[precip_cols].max(axis=1)
    
    # Compute rolling cumulative rainfall
    rain_pivot = rain_pivot.sort_values("date").reset_index(drop=True)
    for window in [2, 3, 5, 7, 14, 30]:
        rain_pivot[f"rain_{window}d_cum"] = (
            rain_pivot["precip_avg"].rolling(window=window, min_periods=1).sum()
        )
    
    # Lagged rainfall (yesterday, 2 days ago, etc.)
    for lag in [1, 2, 3]:
        rain_pivot[f"rain_lag{lag}d"] = rain_pivot["precip_avg"].shift(lag)
    
    # ── Aggregate bacteria data by date ──
    # For each sample date, compute exceedance rate
    bacteria_daily = bacteria_df.groupby("sample_date").agg(
        total_samples=("value", "count"),
        elevated_samples=("exceeds_standard", "sum"),
        max_entero=("value", lambda x: x[bacteria_df.loc[x.index, "analyte"] == "ENTERO"].max()),
        stations_sampled=("station", "nunique"),
    ).reset_index()
    
    bacteria_daily["exceedance_rate"] = (
        bacteria_daily["elevated_samples"] / bacteria_daily["total_samples"]
    )
    
    # Count elevated stations (proxy for CSO event)
    elevated_stations = bacteria_df[bacteria_df["exceeds_standard"]].groupby("sample_date").agg(
        elevated_station_count=("station", "nunique"),
        elevated_basins=("basin", lambda x: x.nunique()),
    ).reset_index()
    
    bacteria_daily = bacteria_daily.merge(elevated_stations, on="sample_date", how="left")
    bacteria_daily["elevated_station_count"] = bacteria_daily["elevated_station_count"].fillna(0)
    bacteria_daily["elevated_basins"] = bacteria_daily["elevated_basins"].fillna(0)
    
    # Define CSO proxy: 3+ stations elevated across 2+ basins = likely CSO
    bacteria_daily["likely_cso"] = (
        (bacteria_daily["elevated_station_count"] >= 3) &
        (bacteria_daily["elevated_basins"] >= 2)
    ).astype(int)
    
    # Also flag: any elevated = water quality impacted
    bacteria_daily["any_elevated"] = (bacteria_daily["elevated_samples"] > 0).astype(int)
    
    # ── Merge rain + bacteria ──
    bacteria_daily = bacteria_daily.rename(columns={"sample_date": "date"})
    merged = bacteria_daily.merge(rain_pivot, on="date", how="left")
    
    # Sort and fill
    merged = merged.sort_values("date").reset_index(drop=True)
    
    print(f"  Training dataset: {len(merged)} days with bacteria + rain data")
    print(f"  Date range: {merged['date'].min()} to {merged['date'].max()}")
    print(f"  Days with elevated bacteria: {merged['any_elevated'].sum()}")
    print(f"  Days with likely CSO: {merged['likely_cso'].sum()}")
    
    return merged


def collect_and_build(save: bool = True) -> pd.DataFrame:
    """
    Full pipeline: fetch historical data and build training dataset.
    
    Args:
        save: Whether to save intermediate and final datasets
        
    Returns:
        Training dataset DataFrame
    """
    print("=" * 60)
    print("HISTORICAL DATA COLLECTION & ALIGNMENT")
    print("=" * 60)
    
    # Fetch rain data
    print("\n📊 Step 1: Fetching historical rainfall (ACIS)...")
    rain_df = fetch_historical_rain()
    print(f"  Total rain records: {len(rain_df)}")
    
    # Fetch bacteria data
    print("\n🦠 Step 2: Fetching historical bacteria (SF Gov)...")
    bacteria_df = fetch_historical_bacteria()
    print(f"  Total bacteria samples: {len(bacteria_df)}")
    
    # Build training dataset
    print("\n🔧 Step 3: Building training dataset...")
    training_df = build_training_dataset(rain_df, bacteria_df)
    
    if save:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        
        rain_path = RAW_DIR / "historical_rain.csv"
        bacteria_path = RAW_DIR / "historical_bacteria.csv"
        training_path = PROCESSED_DIR / "training_dataset.csv"
        
        rain_df.to_csv(rain_path, index=False)
        bacteria_df.to_csv(bacteria_path, index=False)
        training_df.to_csv(training_path, index=False)
        
        print(f"\n💾 Saved:")
        print(f"  {rain_path} ({len(rain_df)} records)")
        print(f"  {bacteria_path} ({len(bacteria_df)} records)")
        print(f"  {training_path} ({len(training_df)} records)")
    
    # Print summary statistics
    print("\n" + "=" * 60)
    print("DATASET SUMMARY")
    print("=" * 60)
    
    if "precip_avg" in training_df.columns:
        rainy_days = training_df[training_df["precip_avg"] > 0.01]
        dry_days = training_df[training_df["precip_avg"] <= 0.01]
        
        print(f"\n  Total days with samples: {len(training_df)}")
        print(f"  Rainy days (>0.01\"): {len(rainy_days)}")
        print(f"  Dry days: {len(dry_days)}")
        
        if len(rainy_days) > 0 and "exceedance_rate" in training_df.columns:
            print(f"\n  Avg exceedance rate on dry days: {dry_days['exceedance_rate'].mean():.1%}")
            print(f"  Avg exceedance rate on rainy days: {rainy_days['exceedance_rate'].mean():.1%}")
        
        heavy_rain = training_df[training_df["precip_avg"] >= 0.5]
        if len(heavy_rain) > 0:
            print(f"\n  Heavy rain days (≥0.5\"): {len(heavy_rain)}")
            print(f"  Avg exceedance rate on heavy rain days: {heavy_rain['exceedance_rate'].mean():.1%}")
            print(f"  Likely CSO events on heavy rain days: {heavy_rain['likely_cso'].sum()}")
    
    return training_df


if __name__ == "__main__":
    training_df = collect_and_build()
