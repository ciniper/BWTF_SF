#!/usr/bin/env python3
"""
CSO Event Collector & Logger

Collects Combined Sewer Overflow event data from multiple sources:
1. SFPUC Real-Time API (same as BWTF repo) — current CSO status
2. Historical polling — logs events over time to build training dataset
3. Friends of Mission Creek — community CSO tracking

The key challenge: there's no public historical CSO event database.
We need to BUILD one by:
  a) Polling the SFPUC API regularly and logging state changes
  b) Scraping/requesting historical data from SFPUC annual reports
  c) Cross-referencing with Friends of Mission Creek data

This collector is designed to run on a schedule (e.g., every 15 minutes)
to capture CSO events as they happen.
"""

import json
import csv
import requests
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, asdict

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "raw" / "cso_events"
EVENT_LOG = DATA_DIR / "cso_event_log.csv"

# SFPUC Real-Time API (same endpoint as BWTF repo)
SFPUC_API_URL = "https://infrastructure.sfwater.org/lims.asmx/getBeaches"


@dataclass
class CSOSnapshot:
    """A point-in-time snapshot of CSO status across all stations"""
    timestamp: str
    station_id: str
    station_name: str
    status: str          # "safe", "posted", "not_sampled", etc.
    has_cso: bool
    cso_station_id: Optional[str]
    raw_color: Optional[str]
    cso_color: Optional[str]
    sample_date: Optional[str]
    latitude: float
    longitude: float


class CSOEventCollector:
    """
    Polls SFPUC API and logs CSO events over time.
    
    Run this on a schedule (every 15 minutes recommended) to build
    a historical dataset of when CSO events start and end.
    
    The log file tracks state transitions:
    - Station goes from safe → posted (CSO) = event START
    - Station goes from posted (CSO) → safe = event END
    """
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (SFSewageForecast)"
        })
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    def fetch_current_status(self) -> List[CSOSnapshot]:
        """
        Fetch current CSO status from SFPUC API.
        
        Returns:
            List of CSOSnapshot objects for all stations
        """
        try:
            response = self.session.get(SFPUC_API_URL, timeout=30)
            response.raise_for_status()
            
            root = ET.fromstring(response.content)
            json_str = root.text
            if not json_str:
                return []
            
            data = json.loads(json_str)
        except Exception as e:
            print(f"Error fetching SFPUC data: {e}")
            return []
        
        now = datetime.now().isoformat()
        snapshots = []
        
        for item in data:
            p_color = item.get("p_color")
            cso_field = item.get("cso")
            has_cso = bool(cso_field and p_color and p_color.upper() == "R")
            
            # Determine status
            if p_color is None:
                status = "not_routinely_sampled"
            elif p_color.upper() == "G":
                status = "safe"
            elif p_color.upper() == "R":
                status = "cso" if has_cso else "posted"
            elif p_color.upper() == "Y":
                status = "not_sampled"
            else:
                status = "unknown"
            
            snapshots.append(CSOSnapshot(
                timestamp=now,
                station_id=item.get("stationid", ""),
                station_name=item.get("stationname", ""),
                status=status,
                has_cso=has_cso,
                cso_station_id=cso_field,
                raw_color=p_color,
                cso_color=item.get("s_color"),
                sample_date=item.get("sample_date"),
                latitude=float(item.get("lat", 0)),
                longitude=float(item.get("lon", 0)),
            ))
        
        return snapshots
    
    def log_snapshot(self, snapshots: Optional[List[CSOSnapshot]] = None) -> int:
        """
        Log current CSO status to the event log CSV.
        
        Call this on a schedule to build the historical dataset.
        
        Args:
            snapshots: Optional pre-fetched snapshots (fetches if None)
            
        Returns:
            Number of records logged
        """
        if snapshots is None:
            snapshots = self.fetch_current_status()
        
        if not snapshots:
            return 0
        
        # Write header if file doesn't exist
        write_header = not EVENT_LOG.exists()
        
        with open(EVENT_LOG, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "timestamp", "station_id", "station_name", "status",
                "has_cso", "cso_station_id", "raw_color", "cso_color",
                "sample_date", "latitude", "longitude"
            ])
            
            if write_header:
                writer.writeheader()
            
            for snapshot in snapshots:
                writer.writerow(asdict(snapshot))
        
        return len(snapshots)
    
    def get_active_cso_events(self) -> List[CSOSnapshot]:
        """Get currently active CSO events"""
        snapshots = self.fetch_current_status()
        return [s for s in snapshots if s.has_cso]
    
    def get_posted_stations(self) -> List[CSOSnapshot]:
        """Get currently posted stations (CSO or elevated bacteria)"""
        snapshots = self.fetch_current_status()
        return [s for s in snapshots if s.status in ("cso", "posted")]
    
    def load_event_log(self) -> pd.DataFrame:
        """
        Load the historical event log as a DataFrame.
        
        Returns:
            DataFrame with all logged snapshots
        """
        if not EVENT_LOG.exists():
            print(f"No event log found at {EVENT_LOG}")
            print("Run `python -m src.collectors.cso_events` on a schedule to start collecting.")
            return pd.DataFrame()
        
        df = pd.read_csv(EVENT_LOG, parse_dates=["timestamp"])
        return df
    
    def extract_cso_events(self) -> pd.DataFrame:
        """
        Extract discrete CSO events from the log (start/end times).
        
        Looks for state transitions:
        - safe/not_sampled → cso = event START
        - cso → safe/not_sampled = event END
        
        Returns:
            DataFrame with event_start, event_end, station_id, duration_hours
        """
        log = self.load_event_log()
        if log.empty:
            return pd.DataFrame()
        
        events = []
        
        for station_id in log["station_id"].unique():
            station_log = log[log["station_id"] == station_id].sort_values("timestamp")
            
            event_start = None
            prev_status = None
            
            for _, row in station_log.iterrows():
                current_status = row["status"]
                
                if current_status == "cso" and prev_status != "cso":
                    # CSO event started
                    event_start = row["timestamp"]
                elif current_status != "cso" and prev_status == "cso" and event_start:
                    # CSO event ended
                    events.append({
                        "station_id": station_id,
                        "station_name": row["station_name"],
                        "event_start": event_start,
                        "event_end": row["timestamp"],
                        "duration_hours": (row["timestamp"] - event_start).total_seconds() / 3600,
                    })
                    event_start = None
                
                prev_status = current_status
            
            # Handle ongoing events
            if event_start is not None:
                events.append({
                    "station_id": station_id,
                    "station_name": station_log.iloc[-1]["station_name"],
                    "event_start": event_start,
                    "event_end": None,  # Still ongoing
                    "duration_hours": None,
                })
        
        return pd.DataFrame(events)


def main():
    """Log current CSO status and print summary"""
    collector = CSOEventCollector()
    
    print("Fetching current SFPUC CSO status...")
    snapshots = collector.fetch_current_status()
    
    if not snapshots:
        print("No data received from SFPUC API")
        return
    
    # Log to CSV
    count = collector.log_snapshot(snapshots)
    print(f"Logged {count} station records to {EVENT_LOG}")
    
    # Print current status
    cso_active = [s for s in snapshots if s.has_cso]
    posted = [s for s in snapshots if s.status == "posted"]
    safe = [s for s in snapshots if s.status == "safe"]
    
    print(f"\n{'='*60}")
    print(f"CSO STATUS — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    if cso_active:
        print(f"\n🚨 ACTIVE CSO ({len(cso_active)}):")
        for s in cso_active:
            print(f"   • {s.station_name} (CSO station: {s.cso_station_id})")
    else:
        print("\n✅ No active CSO events")
    
    if posted:
        print(f"\n⚠️ POSTED ({len(posted)}):")
        for s in posted:
            print(f"   • {s.station_name}")
    
    print(f"\n🟢 Safe: {len(safe)} stations")
    print(f"📊 Total: {len(snapshots)} stations")
    
    # Show event log stats if available
    log = collector.load_event_log()
    if not log.empty:
        print(f"\n📁 Event log: {len(log)} total records")
        print(f"   Date range: {log['timestamp'].min()} to {log['timestamp'].max()}")
        cso_records = log[log["status"] == "cso"]
        print(f"   CSO records: {len(cso_records)}")


if __name__ == "__main__":
    main()
