#!/usr/bin/env python3
"""
SFPUC Beach Water Quality Real-Time Data

Fetches real-time beach water quality data from SFPUC's internal API:
- CSO (Combined Sewer Overflow) events  
- Current station status (safe, posted, not sampled)
- More up-to-date information than the SF Gov API

Data Source:
- SFPUC LIMS API: https://infrastructure.sfwater.org/lims.asmx/getBeaches
- This is the same data source used by the SFPUC map at:
  https://webapps.sfpuc.org/sapps/beachesandbay.html

Status logic mirrors the SFPUC public map:
- `s_color == R` means posted
- `s_color == Y` means not sampled
- `s_color == W` means not routinely sampled
- otherwise the station defaults to safe/green
- a populated `cso` field indicates the map should show the CSO icon
"""

import json
import sys
import requests
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from enum import Enum

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.stations import STATIONS
from shared.outfalls import OUTFALLS, STATION_OUTFALLS


# ─── CSO Outfall-to-Beach Mapping ────────────────────────────────────────────
# CSO outfall → affected stations, from the canonical registry (shared/outfalls.py —
# station mapping measured from SFPUC's own 2016-17 feed flags, coordinates from
# the NPDES permits). Kept in the shapes older callers expect.
CSO_OUTFALLS = {
    o.id: {
        "name": o.name,
        "feed_name": o.feed_name,
        "basin": o.basin,
        "affected_beaches": o.station_names,
        "affected_station_ids": list(o.stations),
        "description": f"{o.receiving_water} — {o.evidence}",
    }
    for o in OUTFALLS.values()
}

# Beach display name → outfall ids whose discharge gets it posted
BEACH_CSO_OUTFALLS = {}
for outfall_id, info in CSO_OUTFALLS.items():
    for beach in info["affected_beaches"]:
        BEACH_CSO_OUTFALLS.setdefault(beach, []).append(outfall_id)

# Exact feed station name → basin, from the canonical registry
# (shared/stations.py covers all 20 getBeaches stations).
STATION_NAME_BASINS = {s.sfpuc_name: s.basin for s in STATIONS.values()}

# Keyword fallback for names that don't match a feed string exactly.
# Mission Creek and Crane Cove sit on the Central bayside waterfront, which
# the app folds into Southeast (they were wrongly North Shore before 2026-09).
BEACH_DRAINAGE_BASINS = {
    "Ocean Beach": "Westside",
    "Fort Funston": "Westside",
    "China Beach": "Westside",
    "Baker Beach": "Westside",
    "Aquatic Park": "North Shore",
    "Hyde Street": "North Shore",
    "Crissy Field": "North Shore",
    "Crane Cove": "Southeast",
    "Mission Creek": "Southeast",
    "Islais": "Southeast",
    "Candlestick": "Southeast",
    "Sunnydale": "Southeast",
    "Windsurfer": "Southeast",
    "Jackrabbit": "Southeast",
}


class StationStatus(Enum):
    """Station status based on SFPUC color codes"""
    SAFE = "safe"                    # G - Green - meets CA standards
    POSTED = "posted"                # R - Red/Orange - elevated bacteria or recent CSO  
    NOT_SAMPLED = "not_sampled"      # Y - Yellow - not sampled
    NOT_ROUTINELY_SAMPLED = "not_routinely_sampled"  # null - gray circle
    UNKNOWN = "unknown"


@dataclass
class SFPUCStation:
    """Real-time status of a monitoring station from SFPUC API"""
    station_id: str
    station_name: str
    status: StationStatus
    has_cso: bool
    cso_station_id: Optional[str]
    posted_station_id: Optional[str]
    sample_date: Optional[datetime]
    latitude: float
    longitude: float
    raw_color: Optional[str]
    cso_color: Optional[str]
    drainage_basin: Optional[str] = None
    cso_outfalls: List[str] = field(default_factory=list)


@dataclass 
class CSOEvent:
    """Combined Sewer Overflow event"""
    station_id: str
    station_name: str
    cso_station_id: str
    latitude: float
    longitude: float
    detected_at: datetime


class SFPUCRealTimeAPI:
    """
    Fetches real-time beach water quality data from SFPUC's LIMS API.
    
    This is the actual data source used by the SFPUC beach map.
    It provides more current information than the SF Gov Open Data API.
    """
    
    # Real-time API endpoint (discovered from SFPUC website JavaScript)
    API_URL = "https://infrastructure.sfwater.org/lims.asmx/getBeaches"
    SFPUC_MAP_URL = "https://webapps.sfpuc.org/sapps/beachesandbay.html"
    BEACH_HOTLINE = "1-877-SFBEACH (1-877-732-3224)"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
    
    def _parse_color_to_status(self, color: Optional[str]) -> StationStatus:
        """Convert a single SFPUC color code to StationStatus"""
        if color is None:
            return StationStatus.UNKNOWN
        color = color.upper()
        if color == "G":
            return StationStatus.SAFE
        if color == "R":
            return StationStatus.POSTED
        if color == "Y":
            return StationStatus.NOT_SAMPLED
        if color == "W":
            return StationStatus.NOT_ROUTINELY_SAMPLED
        return StationStatus.UNKNOWN

    def _parse_station_status(self, item: dict) -> StationStatus:
        """
        Mirror the SFPUC beach map logic for current station status.

        The public map primarily keys off `s_color`, with a fallback that marks
        stations as posted when `posted` is present and `p_color` is R/G.
        Everything else defaults to green/safe.
        """
        sample_color = item.get("s_color")
        posted_station_id = item.get("posted")
        posted_color = item.get("p_color")

        sample_status = self._parse_color_to_status(sample_color)
        if sample_status in {
            StationStatus.NOT_SAMPLED,
            StationStatus.POSTED,
            StationStatus.NOT_ROUTINELY_SAMPLED,
        }:
            return sample_status

        if posted_station_id and posted_color and posted_color.upper() in {"R", "G"}:
            return StationStatus.POSTED

        return StationStatus.SAFE
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse SFPUC date format (MM/DD/YY)"""
        if not date_str:
            return None
        try:
            # Handle 2-digit year
            return datetime.strptime(date_str, "%m/%d/%y")
        except ValueError:
            try:
                return datetime.strptime(date_str, "%m/%d/%Y")
            except ValueError:
                return None
    
    def _detect_cso(self, item: dict) -> bool:
        """
        Detect if a station has an active CSO event.
        
        The SFPUC map draws the CSO icon whenever the `cso` field is populated.
        
        The red triangle on the SFPUC map indicates CSO within last 24-72 hours.
        """
        return bool(item.get("cso"))
    
    def _get_drainage_basin(self, station_name: str) -> Optional[str]:
        """Determine which drainage basin a station belongs to based on name"""
        exact = STATION_NAME_BASINS.get(station_name)
        if exact:
            return exact
        for keyword, basin in BEACH_DRAINAGE_BASINS.items():
            if keyword.lower() in station_name.lower():
                return basin
        return None
    
    def _get_cso_outfalls(self, station_name: str) -> List[str]:
        """CSO outfall ids whose discharge gets this station posted (registry:
        shared/outfalls.py). Exact match on the feed's station name string."""
        for st in STATIONS.values():
            if st.sfpuc_name == station_name or st.name == station_name:
                return sorted(STATION_OUTFALLS.get(st.sfpuc_id, []))
        return []
    
    def fetch_stations(self) -> List[SFPUCStation]:
        """
        Fetch real-time station data from SFPUC API.
        
        Returns:
            List of SFPUCStation objects with current status
        """
        try:
            response = self.session.get(self.API_URL, timeout=30)
            response.raise_for_status()
            
            # Parse XML response - the data is JSON wrapped in XML
            root = ET.fromstring(response.content)
            json_str = root.text
            
            if not json_str:
                print("No data in API response")
                return []
            
            data = json.loads(json_str)
            
        except requests.RequestException as e:
            print(f"Error fetching SFPUC data: {e}")
            return []
        except (ET.ParseError, json.JSONDecodeError) as e:
            print(f"Error parsing SFPUC response: {e}")
            return []
        
        stations = []
        for item in data:
            try:
                # Determine if there's an active CSO
                has_cso = self._detect_cso(item)
                station_name = item.get("stationname", "")
                
                station = SFPUCStation(
                    station_id=item.get("stationid", ""),
                    station_name=station_name,
                    status=self._parse_station_status(item),
                    has_cso=has_cso,
                    cso_station_id=item.get("cso"),
                    posted_station_id=item.get("posted"),
                    sample_date=self._parse_date(item.get("sample_date")),
                    latitude=float(item.get("lat", 0)),
                    longitude=float(item.get("lon", 0)),
                    raw_color=item.get("p_color"),
                    cso_color=item.get("s_color"),
                    drainage_basin=self._get_drainage_basin(station_name),
                    cso_outfalls=self._get_cso_outfalls(station_name)
                )
                stations.append(station)
            except (ValueError, TypeError) as e:
                print(f"Error parsing station data: {e}")
                continue
        
        return stations
    
    def get_cso_events(self) -> List[CSOEvent]:
        """
        Get list of active CSO (Combined Sewer Overflow) events.
        
        Returns:
            List of CSOEvent objects for stations with active CSO
        """
        stations = self.fetch_stations()
        cso_events = []
        
        for station in stations:
            if station.has_cso and station.cso_station_id:
                event = CSOEvent(
                    station_id=station.station_id,
                    station_name=station.station_name,
                    cso_station_id=station.cso_station_id,
                    latitude=station.latitude,
                    longitude=station.longitude,
                    detected_at=datetime.now()  # API doesn't provide exact CSO time
                )
                cso_events.append(event)
        
        return cso_events
    
    def get_posted_stations(self) -> List[SFPUCStation]:
        """
        Get list of stations currently posted (elevated bacteria or CSO).
        
        Returns:
            List of SFPUCStation objects with POSTED status
        """
        stations = self.fetch_stations()
        return [s for s in stations if s.status == StationStatus.POSTED]
    
    def get_safe_stations(self) -> List[SFPUCStation]:
        """
        Get list of stations currently safe for water contact.
        
        Returns:
            List of SFPUCStation objects with SAFE status
        """
        stations = self.fetch_stations()
        return [s for s in stations if s.status == StationStatus.SAFE]
    
    def get_not_sampled_stations(self) -> List[SFPUCStation]:
        """
        Get list of stations that are not sampled or not routinely sampled.
        
        Returns:
            List of SFPUCStation objects with NOT_SAMPLED or NOT_ROUTINELY_SAMPLED status
        """
        stations = self.fetch_stations()
        return [s for s in stations if s.status in [StationStatus.NOT_SAMPLED, StationStatus.NOT_ROUTINELY_SAMPLED]]
    
    def get_status_summary(self) -> dict:
        """
        Get a summary of current beach water quality status.
        
        Returns:
            Dict with counts and lists of stations by status
        """
        stations = self.fetch_stations()
        cso_events = [s for s in stations if s.has_cso]
        
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_stations": len(stations),
            "safe_count": len([s for s in stations if s.status == StationStatus.SAFE]),
            "posted_count": len([s for s in stations if s.status == StationStatus.POSTED]),
            "not_sampled_count": len([s for s in stations if s.status in [StationStatus.NOT_SAMPLED, StationStatus.NOT_ROUTINELY_SAMPLED]]),
            "cso_active_count": len(cso_events),
            "cso_locations": [s.station_name for s in cso_events],
            "posted_locations": [s.station_name for s in stations if s.status == StationStatus.POSTED],
            "safe_locations": [s.station_name for s in stations if s.status == StationStatus.SAFE],
            "not_sampled_locations": [s.station_name for s in stations if s.status in [StationStatus.NOT_SAMPLED, StationStatus.NOT_ROUTINELY_SAMPLED]],
        }
        
        return summary
    
    def format_status_report(self) -> str:
        """Generate a formatted status report"""
        stations = self.fetch_stations()
        cso_events = [s for s in stations if s.has_cso]
        posted = [s for s in stations if s.status == StationStatus.POSTED]
        safe = [s for s in stations if s.status == StationStatus.SAFE]
        not_sampled = [s for s in stations if s.status in [StationStatus.NOT_SAMPLED, StationStatus.NOT_ROUTINELY_SAMPLED]]
        
        lines = [
            "=" * 60,
            "🌊 SFPUC REAL-TIME BEACH WATER QUALITY STATUS",
            f"📅 Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
            ""
        ]
        
        # CSO Alerts (highest priority)
        if cso_events:
            lines.append("🚨 ACTIVE CSO (Combined Sewer Overflow) ALERTS:")
            lines.append("-" * 40)
            for station in cso_events:
                lines.append(f"   ⚠️  {station.station_name}")
                if station.cso_station_id:
                    lines.append(f"      CSO Station: {station.cso_station_id}")
                if station.drainage_basin:
                    lines.append(f"      Drainage Basin: {station.drainage_basin}")
                if station.cso_outfalls:
                    outfall_info = ", ".join(station.cso_outfalls)
                    lines.append(f"      Nearby Outfalls: {outfall_info}")
            lines.append("")
            lines.append("   ℹ️  CSO events indicate sewage discharge in last 24-72 hours")
            lines.append("   ℹ️  Discharges are ~94% treated stormwater, ~6% sanitary flow")
            lines.append(f"   📞 Call {self.BEACH_HOTLINE} or 415-242-2214 for details")
            lines.append("")
        
        # Posted Stations
        if posted:
            lines.append(f"🟠 POSTED STATIONS ({len(posted)}):")
            lines.append("-" * 40)
            lines.append("   (Elevated bacteria or recent CSO - avoid water contact)")
            for station in posted:
                cso_marker = " 🚨CSO" if station.has_cso else ""
                date_str = station.sample_date.strftime('%m/%d') if station.sample_date else "N/A"
                lines.append(f"   • {station.station_name}{cso_marker} (sampled: {date_str})")
            lines.append("")
        
        # Safe Stations
        if safe:
            lines.append(f"🟢 SAFE STATIONS ({len(safe)}):")
            lines.append("-" * 40)
            lines.append("   (Meets CA water quality standards)")
            for station in safe:
                date_str = station.sample_date.strftime('%m/%d') if station.sample_date else "N/A"
                lines.append(f"   • {station.station_name} (sampled: {date_str})")
            lines.append("")
        
        # Not Sampled Stations
        if not_sampled:
            lines.append(f"⚪ NOT SAMPLED ({len(not_sampled)}):")
            lines.append("-" * 40)
            lines.append("   (No data available at this time)")
            for station in not_sampled:
                date_str = station.sample_date.strftime('%m/%d') if station.sample_date else "N/A"
                lines.append(f"   • {station.station_name} (last: {date_str})")
            lines.append("")
        
        # Summary
        lines.extend([
            "-" * 60,
            f"📊 SUMMARY: {len(safe)} safe, {len(posted)} posted, {len(cso_events)} with CSO, {len(not_sampled)} not sampled",
            f"📊 TOTAL: {len(stations)} stations",
            "",
            "📍 For more information:",
            f"   🌐 {self.SFPUC_MAP_URL}",
            f"   📞 {self.BEACH_HOTLINE}",
            "=" * 60
        ])
        
        return "\n".join(lines)


def main():
    """Test the real-time API"""
    print("Testing SFPUC Real-Time API...")
    api = SFPUCRealTimeAPI()
    
    print("\nFetching real-time data...")
    report = api.format_status_report()
    print(report)
    
    print("\n" + "=" * 60)
    print("JSON Summary:")
    print("=" * 60)
    summary = api.get_status_summary()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
