#!/usr/bin/env python3
"""
Weather Underground Personal Weather Station (PWS) Data Collector

Scrapes rainfall data from Weather Underground PWS stations.
These are the stations Rubin recommended for hyperlocal SF rain data:
  - KCASANFR2075 (Marina Blvd & Fillmore)
  - KCASANFR1851 (1762 North Point)
  - KCASANFR2077 (Casa Sanchez, Mission/SOMA)
  - KCASANFR1317 (Outer Sunset)

WU provides daily and historical data via their web interface.
The API requires a key (free tier available), but we can also
scrape the daily history page.

NOTE: WU has rate limits. Be respectful — cache aggressively.
"""

import json
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "raw" / "rainfall"

# Stations recommended by Rubin, mapped to drainage basins
STATIONS = {
    "KCASANFR2075": {"name": "Marina Blvd & Fillmore", "basin": "North Shore"},
    "KCASANFR1851": {"name": "1762 North Point", "basin": "North Shore"},
    "KCASANFR2077": {"name": "Casa Sanchez", "basin": "Southeast"},
    "KCASANFR1317": {"name": "Outer Sunset", "basin": "Westside"},
}

# WU API base (requires API key — free tier: 1500 calls/day)
WU_API_BASE = "https://api.weather.com/v2/pws/history/hourly"
# WU daily history page (no key needed, but must parse HTML)
WU_HISTORY_URL = "https://www.wunderground.com/dashboard/pws/{station_id}/table/{date}/daily"


class WundergroundCollector:
    """
    Collects rainfall data from Weather Underground PWS stations.
    
    Two modes:
    1. API mode (requires WU_API_KEY env var) — structured JSON, hourly data
    2. Scrape mode (no key needed) — parse daily history HTML tables
    
    API mode is preferred for reliability and rate limits.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        import os
        self.api_key = api_key or os.environ.get("WU_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
    
    def fetch_daily_api(self, station_id: str, date: datetime) -> pd.DataFrame:
        """
        Fetch hourly data for a single day via WU API.
        
        Requires WU_API_KEY environment variable.
        Free tier: 1500 calls/day, 30 calls/minute.
        
        Args:
            station_id: WU PWS station ID (e.g., KCASANFR2075)
            date: Date to fetch
            
        Returns:
            DataFrame with hourly observations
        """
        if not self.api_key:
            print("WU API key not set. Set WU_API_KEY env var or use scrape mode.")
            return pd.DataFrame()
        
        params = {
            "stationId": station_id,
            "format": "json",
            "units": "e",  # English (inches, °F)
            "date": date.strftime("%Y%m%d"),
            "apiKey": self.api_key,
        }
        
        try:
            response = self.session.get(WU_API_BASE, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching WU API data for {station_id}: {e}")
            return pd.DataFrame()
        
        records = []
        for obs in data.get("observations", []):
            imperial = obs.get("imperial", {})
            records.append({
                "timestamp": pd.Timestamp(obs.get("obsTimeLocal")),
                "station_id": station_id,
                "precip_rate_in": imperial.get("precipRate"),
                "precip_total_in": imperial.get("precipTotal"),
                "temp_f": imperial.get("tempAvg"),
                "humidity_pct": obs.get("humidityAvg"),
                "wind_speed_mph": imperial.get("windspeedAvg"),
                "wind_gust_mph": imperial.get("windgustHigh"),
                "wind_dir_deg": obs.get("winddirAvg"),
                "pressure_in": imperial.get("pressureAvg"),
            })
        
        return pd.DataFrame(records)
    
    def fetch_daily_scrape(self, station_id: str, date: datetime) -> pd.DataFrame:
        """
        Scrape daily history from WU website (no API key needed).
        
        Parses the daily table at:
        https://www.wunderground.com/dashboard/pws/{station_id}/table/{date}/daily
        
        Args:
            station_id: WU PWS station ID
            date: Date to fetch
            
        Returns:
            DataFrame with daily observations (less granular than API)
        """
        url = WU_HISTORY_URL.format(
            station_id=station_id,
            date=date.strftime("%Y-%m-%d")
        )
        
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Error scraping WU for {station_id}: {e}")
            return pd.DataFrame()
        
        # WU loads data via JavaScript — the actual data is in a JSON blob
        # embedded in the page. Look for the lib/history pattern.
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")
            
            # WU embeds data in a script tag with specific patterns
            # This is fragile and may break if WU changes their page structure
            scripts = soup.find_all("script")
            for script in scripts:
                text = script.string or ""
                if "observations" in text and "precipRate" in text:
                    # Try to extract JSON from the script
                    import re
                    match = re.search(r'observations["\s]*:["\s]*(\[.*?\])', text, re.DOTALL)
                    if match:
                        obs_data = json.loads(match.group(1))
                        records = []
                        for obs in obs_data:
                            imperial = obs.get("imperial", obs)
                            records.append({
                                "timestamp": pd.Timestamp(obs.get("obsTimeLocal", date)),
                                "station_id": station_id,
                                "precip_rate_in": imperial.get("precipRate"),
                                "precip_total_in": imperial.get("precipTotal"),
                                "temp_f": imperial.get("tempAvg", imperial.get("temp")),
                            })
                        return pd.DataFrame(records)
        except ImportError:
            print("beautifulsoup4 required for scrape mode. Install: pip install beautifulsoup4")
        except Exception as e:
            print(f"Error parsing WU page for {station_id}: {e}")
        
        return pd.DataFrame()
    
    def fetch_range(self, station_id: str, start_date: datetime,
                    end_date: datetime, use_api: bool = True,
                    delay_seconds: float = 2.0) -> pd.DataFrame:
        """
        Fetch data for a date range.
        
        Args:
            station_id: WU PWS station ID
            start_date: Start date
            end_date: End date
            use_api: Use API (True) or scrape (False)
            delay_seconds: Delay between requests (respect rate limits)
            
        Returns:
            Combined DataFrame for the date range
        """
        all_dfs = []
        current = start_date
        
        while current <= end_date:
            print(f"  Fetching {station_id} for {current.strftime('%Y-%m-%d')}...")
            
            if use_api and self.api_key:
                df = self.fetch_daily_api(station_id, current)
            else:
                df = self.fetch_daily_scrape(station_id, current)
            
            if not df.empty:
                all_dfs.append(df)
            
            current += timedelta(days=1)
            time.sleep(delay_seconds)  # Rate limit
        
        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
        return pd.DataFrame()


def collect_all(days: int = 7, save: bool = True) -> dict:
    """
    Collect rainfall data from all WU stations.
    
    Args:
        days: Number of days of historical data
        save: Whether to save to data/raw/rainfall/
        
    Returns:
        Dict of DataFrames keyed by station_id
    """
    print("Collecting Weather Underground PWS data...")
    
    collector = WundergroundCollector()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    results = {}
    for station_id, info in STATIONS.items():
        print(f"\n  Station: {info['name']} ({station_id}) — {info['basin']} basin")
        df = collector.fetch_range(station_id, start_date, end_date)
        results[station_id] = df
        print(f"    Got {len(df)} records")
    
    if save:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        for station_id, df in results.items():
            if not df.empty:
                path = DATA_DIR / f"wu_{station_id}_{timestamp}.csv"
                df.to_csv(path, index=False)
                print(f"  Saved {path}")
    
    return results


if __name__ == "__main__":
    results = collect_all(days=7)
    
    print("\n" + "=" * 60)
    print("COLLECTION SUMMARY")
    print("=" * 60)
    for station_id, df in results.items():
        info = STATIONS[station_id]
        print(f"  {info['name']} ({station_id}): {len(df)} records")
        if not df.empty and "precip_total_in" in df.columns:
            max_precip = df["precip_total_in"].max()
            print(f"    Max daily precip total: {max_precip:.2f} inches")
