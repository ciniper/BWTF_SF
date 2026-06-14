#!/usr/bin/env python3
"""
NWS / NOAA Rainfall Data Collector

Collects rainfall observations and forecasts from free government APIs:
1. NWS API — recent observations from airport/cooperative stations
2. NOAA CO-OPS — meteorological observations including precipitation
3. MesoWest — aggregated station data (SFOC1 and others)

These are the most reliable, long-term data sources for building
the historical rainfall dataset.
"""

import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# NWS API endpoints
NWS_BASE = "https://api.weather.gov"
NWS_STATIONS = {
    "KSFO": "SFO Airport",
    "KOAK": "Oakland Airport",
}

# NOAA CO-OPS (Tides & Currents) — includes met observations
NOAA_COOPS_BASE = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NOAA_SF_STATION = "9414290"

# MesoWest API (University of Utah)
MESOWEST_BASE = "https://api.mesowest.net/v2/stations/timeseries"
MESOWEST_STATIONS = ["SFOC1"]

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "raw" / "rainfall"


class NWSRainfallCollector:
    """
    Collects precipitation data from NWS observation stations.
    
    NWS API is free, no key required, just needs a User-Agent header.
    Returns hourly observations including precipitation amounts.
    """
    
    def __init__(self, contact_email: str = "bwtf@surfrider.org"):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f"(SFSewageForecast, {contact_email})",
            "Accept": "application/geo+json"
        })
    
    def fetch_observations(self, station_id: str = "KSFO",
                           hours: int = 72) -> pd.DataFrame:
        """
        Fetch recent observations from an NWS station.
        
        Args:
            station_id: NWS station identifier (e.g., KSFO, KOAK)
            hours: Number of hours of data to fetch
            
        Returns:
            DataFrame with timestamp, precip_mm, temp_c, wind_speed_kmh, wind_dir
        """
        url = f"{NWS_BASE}/stations/{station_id}/observations"
        params = {
            "limit": min(hours * 2, 500),  # ~2 obs per hour max
        }
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching NWS observations for {station_id}: {e}")
            return pd.DataFrame()
        
        records = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            
            timestamp = props.get("timestamp")
            if not timestamp:
                continue
            
            # Precipitation in last hour (mm)
            precip_1h = props.get("precipitationLastHour", {})
            precip_mm = precip_1h.get("value") if precip_1h else None
            
            # Precipitation in last 6 hours (mm)
            precip_6h = props.get("precipitationLast6Hours", {})
            precip_6h_mm = precip_6h.get("value") if precip_6h else None
            
            # Temperature (C)
            temp = props.get("temperature", {})
            temp_c = temp.get("value") if temp else None
            
            # Wind
            wind_speed = props.get("windSpeed", {})
            wind_speed_kmh = wind_speed.get("value") if wind_speed else None
            wind_dir = props.get("windDirection", {})
            wind_dir_deg = wind_dir.get("value") if wind_dir else None
            
            records.append({
                "timestamp": pd.Timestamp(timestamp),
                "station_id": station_id,
                "precip_1h_mm": precip_mm,
                "precip_6h_mm": precip_6h_mm,
                "precip_1h_inches": precip_mm / 25.4 if precip_mm is not None else None,
                "temp_c": temp_c,
                "wind_speed_kmh": wind_speed_kmh,
                "wind_dir_deg": wind_dir_deg,
            })
        
        df = pd.DataFrame(records)
        if not df.empty:
            df = df.sort_values("timestamp").reset_index(drop=True)
        return df
    
    def fetch_forecast_hourly(self) -> pd.DataFrame:
        """
        Fetch hourly forecast for SF (next 156 hours / ~6.5 days).
        
        Returns:
            DataFrame with timestamp, precip_probability, temperature, wind_speed, short_forecast
        """
        url = f"{NWS_BASE}/gridpoints/MTR/88,126/forecast/hourly"
        
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching NWS hourly forecast: {e}")
            return pd.DataFrame()
        
        records = []
        for period in data.get("properties", {}).get("periods", []):
            precip_prob = period.get("probabilityOfPrecipitation", {}).get("value", 0)
            
            records.append({
                "timestamp": pd.Timestamp(period["startTime"]),
                "precip_probability_pct": precip_prob or 0,
                "temperature_f": period.get("temperature"),
                "wind_speed": period.get("windSpeed"),
                "wind_direction": period.get("windDirection"),
                "short_forecast": period.get("shortForecast"),
                "detailed_forecast": period.get("detailedForecast", ""),
            })
        
        return pd.DataFrame(records)


class NOAACoopsCollector:
    """
    Collects meteorological observations from NOAA CO-OPS (Tides & Currents).
    
    Station 9414290 (San Francisco) provides hourly met data including
    air temperature, wind, and barometric pressure. Precipitation is
    available from some stations.
    
    Free API, no key required.
    """
    
    def __init__(self):
        self.session = requests.Session()
    
    def fetch_met_data(self, station_id: str = NOAA_SF_STATION,
                       days: int = 30) -> pd.DataFrame:
        """
        Fetch meteorological observations from NOAA CO-OPS.
        
        Args:
            station_id: NOAA station ID
            days: Number of days of data
            
        Returns:
            DataFrame with timestamp, wind_speed, wind_dir, air_temp, pressure
        """
        end = datetime.now()
        start = end - timedelta(days=days)
        
        params = {
            "station": station_id,
            "product": "wind",
            "begin_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "time_zone": "lst_ldt",
            "units": "english",
            "format": "json",
        }
        
        try:
            response = self.session.get(NOAA_COOPS_BASE, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching NOAA CO-OPS data: {e}")
            return pd.DataFrame()
        
        records = []
        for obs in data.get("data", []):
            records.append({
                "timestamp": pd.Timestamp(obs.get("t")),
                "station_id": station_id,
                "wind_speed_kts": float(obs.get("s", 0)),
                "wind_dir_deg": float(obs.get("d", 0)),
                "wind_gust_kts": float(obs.get("g", 0)) if obs.get("g") else None,
            })
        
        return pd.DataFrame(records)


def collect_all(hours: int = 72, save: bool = True) -> dict:
    """
    Collect rainfall data from all NWS sources.
    
    Args:
        hours: Hours of historical data to collect
        save: Whether to save to data/raw/rainfall/
        
    Returns:
        Dict of DataFrames keyed by source
    """
    print("Collecting NWS rainfall data...")
    
    nws = NWSRainfallCollector()
    results = {}
    
    # NWS observations
    for station_id in NWS_STATIONS:
        print(f"  Fetching {station_id} observations...")
        df = nws.fetch_observations(station_id, hours=hours)
        results[f"nws_{station_id}"] = df
        print(f"    Got {len(df)} records")
    
    # NWS hourly forecast
    print("  Fetching NWS hourly forecast...")
    forecast_df = nws.fetch_forecast_hourly()
    results["nws_forecast"] = forecast_df
    print(f"    Got {len(forecast_df)} forecast periods")
    
    # NOAA CO-OPS wind data (for wind direction analysis)
    print("  Fetching NOAA CO-OPS wind data...")
    noaa = NOAACoopsCollector()
    wind_df = noaa.fetch_met_data(days=max(hours // 24, 1))
    results["noaa_wind"] = wind_df
    print(f"    Got {len(wind_df)} wind records")
    
    if save:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        for name, df in results.items():
            if not df.empty:
                path = DATA_DIR / f"{name}_{timestamp}.csv"
                df.to_csv(path, index=False)
                print(f"  Saved {path}")
    
    return results


if __name__ == "__main__":
    results = collect_all()
    
    # Print summary
    print("\n" + "=" * 60)
    print("COLLECTION SUMMARY")
    print("=" * 60)
    for name, df in results.items():
        print(f"  {name}: {len(df)} records")
        if not df.empty and "precip_1h_inches" in df.columns:
            total = df["precip_1h_inches"].sum()
            if total > 0:
                print(f"    Total precipitation: {total:.2f} inches")
