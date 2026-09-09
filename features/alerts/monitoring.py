#!/usr/bin/env python3
"""
SF Beach Water Quality Alert System
For Surfrider SF Blue Water Task Force (BWTF)

Monitors SF beach water quality data and sends alerts when:
1. E. coli or Enterococcus levels exceed California state standards
2. Combined Sewer Overflow (CSO) events are detected

Data Sources:
- SF Gov Open Data: https://data.sf.gov/resource/v3fv-x3ux.json
- SFPUC Beach Water Quality: https://webapps.sfpuc.org/sapps/beachesandbay.html

Note on Data Freshness:
- The SF Gov API data may have a 1-2 day delay from sample collection to publication
- For real-time CSO alerts, the SFPUC website is more current
- Always check the SFPUC website or call 1-877-SFBEACH for current conditions
"""

import os
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from shared.stations import (  # noqa: E402 — needs the sys.path bootstrap above
    SFPUC_TO_SFGOV_SOURCES,
    STATION_BASINS as STATION_DRAINAGE_BASINS,
    STATION_NAMES,
)

# California State Standards for Beach Water Quality (per 100mL)
# Based on AB 411 / California Code of Regulations, Title 17, Section 7958
#
# IMPORTANT: Total Coliform has a conditional standard:
#   - Default single sample max: 10,000 MPN/100mL
#   - BUT if fecal-to-total coliform ratio > 0.1, limit drops to 1,000 MPN/100mL
#   This ratio test is a critical posting criterion often missed.
#
# Geometric mean standards require at least 5 weekly samples over 30 days.
STANDARDS = {
    "ENTERO": {
        "single_sample_max": 104,  # Enterococcus single sample max (MPN/100mL)
        "geometric_mean": 35,       # 30-day geometric mean
        "description": "Enterococcus"
    },
    "COLI_E": {
        "single_sample_max": 235,   # E. coli single sample max (MPN/100mL)
        "geometric_mean": 126,      # 30-day geometric mean  
        "description": "E. coli"
    },
    "COLI_FECAL": {
        "single_sample_max": 400,   # Fecal coliform single sample max
        "geometric_mean": 200,
        "description": "Fecal Coliform"
    },
    "COLI_TOTAL": {
        "single_sample_max": 10000, # Total coliform single sample max (default)
        "single_sample_max_ratio": 1000,  # Limit when fecal/total ratio > 0.1
        "ratio_threshold": 0.1,     # Fecal-to-total coliform ratio threshold
        "geometric_mean": 1000,
        "description": "Total Coliform"
    }
}

# Minimum number of weekly samples required for geometric mean calculation (AB 411)
GEOMETRIC_MEAN_MIN_SAMPLES = 5
GEOMETRIC_MEAN_WINDOW_DAYS = 30

# Station names, drainage basins (for CSO risk assessment), and the SFPUC
# live-map name → SF Gov lab source mapping all come from the canonical
# registry in shared/stations.py — imported above.

# City lab stations nearest the six sites BWTF's volunteer lab monitors
# (the BWTF site names live in BWTF_TO_SFPUC_NAME, features/comparison).
BWTF_PRIORITY_SITES = [
    "OCEAN#15_SL",      # Baker Beach at Lobos Creek
    "OCEAN#17_SL",      # China Beach
    "OCEAN#19_SL",      # Ocean Beach at Lincoln
    "OCEAN#21_SL",      # Ocean Beach at Vicente
    "BAY#202.4_SL",     # Crissy Field East
    "BAY#211_SL",       # Aquatic Park
]


@dataclass
class WaterQualitySample:
    """Represents a single water quality sample"""
    station_id: str
    station_name: str
    sample_date: datetime
    analyte: str
    value: float
    exceeds_standard: bool
    standard_value: float
    data_as_of: datetime


@dataclass
class Alert:
    """Represents an alert condition"""
    alert_type: str  # "elevated_bacteria" or "cso_discharge"
    station_id: str
    station_name: str
    message: str
    severity: str  # "warning" or "advisory"
    sample_date: datetime
    details: dict


class SFWaterQualityMonitor:
    """Monitors SF beach water quality and generates alerts"""
    
    API_URL = "https://data.sf.gov/resource/v3fv-x3ux.json"
    
    def __init__(self, app_token: Optional[str] = None):
        """
        Initialize the monitor.
        
        Args:
            app_token: Optional Socrata app token for higher rate limits
        """
        self.app_token = app_token or os.environ.get("SOCRATA_APP_TOKEN")
        self.session = requests.Session()
        if self.app_token:
            self.session.headers["X-App-Token"] = self.app_token
    
    def _parse_value(self, value_str: str) -> float:
        """Parse sample value, handling '<' notation for below detection limit"""
        if not value_str:
            return 0.0
        value_str = str(value_str).strip()
        if value_str.startswith("<"):
            # Below detection limit - use half the detection limit
            return float(value_str[1:]) / 2
        if value_str.startswith(">"):
            # Above measurement range
            return float(value_str[1:])
        try:
            return float(value_str)
        except ValueError:
            return 0.0
    
    def fetch_recent_samples(self, days: int = 7, stations: Optional[list] = None) -> list[WaterQualitySample]:
        """
        Fetch recent water quality samples from SF Gov API.
        
        Args:
            days: Number of days of data to fetch
            stations: Optional list of station IDs to filter
            
        Returns:
            List of WaterQualitySample objects
        """
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Build query
        params = {
            "$limit": 1000,
            "$order": "sample_date DESC",
            "$where": f"sample_date >= '{start_date.strftime('%Y-%m-%dT00:00:00')}'"
        }
        
        if stations:
            station_filter = " OR ".join([f"source='{s}'" for s in stations])
            params["$where"] += f" AND ({station_filter})"
        
        try:
            response = self.session.get(self.API_URL, params=params)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching data: {e}")
            return []
        
        samples = []
        for record in data:
            station_id = record.get("source", "")
            analyte = record.get("analyte", "")
            value = self._parse_value(record.get("data", "0"))
            
            # Check if exceeds standard
            standard = STANDARDS.get(analyte, {})
            standard_value = standard.get("single_sample_max", float("inf"))
            exceeds = value > standard_value
            
            try:
                sample_date = datetime.fromisoformat(record.get("sample_date", "").replace("Z", "+00:00").split(".")[0])
                data_as_of = datetime.fromisoformat(record.get("data_as_of", "").replace("Z", "+00:00").split(".")[0])
            except (ValueError, AttributeError):
                continue
            
            samples.append(WaterQualitySample(
                station_id=station_id,
                station_name=STATION_NAMES.get(station_id, station_id),
                sample_date=sample_date,
                analyte=analyte,
                value=value,
                exceeds_standard=exceeds,
                standard_value=standard_value,
                data_as_of=data_as_of
            ))
        
        return samples
    
    def get_latest_by_station(self, samples: list[WaterQualitySample]) -> dict:
        """Group samples by station and get the latest for each analyte"""
        latest = {}
        for sample in sorted(samples, key=lambda x: x.sample_date, reverse=True):
            key = (sample.station_id, sample.analyte)
            if key not in latest:
                latest[key] = sample
        return latest

    def _build_results_link(self, source_ids: list[str], sample_date: datetime) -> str:
        where_parts = [f"source='{source_id}'" for source_id in source_ids]
        where_clause = f"({' OR '.join(where_parts)}) AND sample_date = '{sample_date.strftime('%Y-%m-%dT00:00:00')}'"
        params = {
            "$select": "source,sample_date,analyte,data,data_as_of,data_loaded_at",
            "$where": where_clause,
            "$order": "source, analyte",
        }
        return f"{self.API_URL}?{urlencode(params)}"

    def get_latest_lab_results_for_sfpuc_stations(self, days: int = 45) -> dict:
        """
        Return the freshest SF Gov lab sample metadata for each SFPUC station name.
        """
        samples = self.fetch_recent_samples(days=days)
        latest_sample_by_source = {}
        for sample in sorted(samples, key=lambda item: item.sample_date, reverse=True):
            if sample.station_id not in latest_sample_by_source:
                latest_sample_by_source[sample.station_id] = sample

        lookup = {}
        for sfpuc_station_name, source_ids in SFPUC_TO_SFGOV_SOURCES.items():
            candidates = [
                latest_sample_by_source[source_id]
                for source_id in source_ids
                if source_id in latest_sample_by_source
            ]
            if not candidates:
                continue

            latest_sample = max(candidates, key=lambda item: item.sample_date)
            lookup[sfpuc_station_name] = {
                "sample_date": latest_sample.sample_date,
                "data_as_of": latest_sample.data_as_of,
                "source_id": latest_sample.station_id,
                "source_ids": source_ids,
                "results_url": self._build_results_link(source_ids, latest_sample.sample_date),
            }

        return lookup
    
    def _check_coliform_ratio(self, samples: list[WaterQualitySample]) -> list:
        """
        Check the fecal-to-total coliform ratio (AB 411 conditional standard).
        
        If fecal coliform / total coliform > 0.1, the total coliform single
        sample limit drops from 10,000 to 1,000 MPN/100mL.
        
        Returns:
            List of (station_id, total_coliform_value, fecal_value, ratio) tuples
            for samples that exceed the conditional standard.
        """
        # Group latest samples by station
        latest = self.get_latest_by_station(samples)
        
        exceedances = []
        # Find stations with both COLI_TOTAL and COLI_FECAL
        stations_with_total = {
            sid: sample for (sid, analyte), sample in latest.items()
            if analyte == "COLI_TOTAL"
        }
        stations_with_fecal = {
            sid: sample for (sid, analyte), sample in latest.items()
            if analyte == "COLI_FECAL"
        }
        
        for station_id in stations_with_total:
            if station_id in stations_with_fecal:
                total_val = stations_with_total[station_id].value
                fecal_val = stations_with_fecal[station_id].value
                
                if total_val > 0:
                    ratio = fecal_val / total_val
                    ratio_threshold = STANDARDS["COLI_TOTAL"]["ratio_threshold"]
                    ratio_limit = STANDARDS["COLI_TOTAL"]["single_sample_max_ratio"]
                    
                    # If ratio > 0.1 AND total coliform > 1,000, it's an exceedance
                    if ratio > ratio_threshold and total_val > ratio_limit:
                        exceedances.append((station_id, total_val, fecal_val, ratio))
        
        return exceedances
    
    def calculate_geometric_mean(self, samples: list[WaterQualitySample],
                                  station_id: str, analyte: str) -> Optional[dict]:
        """
        Calculate the 30-day geometric mean for a station/analyte pair.
        
        AB 411 requires at least 5 weekly samples in a 30-day window.
        
        Args:
            samples: All available samples
            station_id: Station to calculate for
            analyte: Analyte to calculate for
            
        Returns:
            Dict with geometric_mean, sample_count, exceeds, standard, or None if insufficient data
        """
        now = datetime.now()
        window_start = now - timedelta(days=GEOMETRIC_MEAN_WINDOW_DAYS)
        
        # Filter to matching samples within the 30-day window
        matching = [
            s for s in samples
            if s.station_id == station_id
            and s.analyte == analyte
            and s.sample_date >= window_start
            and s.value > 0  # Can't take log of 0
        ]
        
        if len(matching) < GEOMETRIC_MEAN_MIN_SAMPLES:
            return None  # Insufficient data
        
        # Calculate geometric mean: exp(mean(ln(values)))
        log_sum = sum(math.log(s.value) for s in matching)
        geo_mean = math.exp(log_sum / len(matching))
        
        # Check against standard
        standard = STANDARDS.get(analyte, {})
        geo_mean_standard = standard.get("geometric_mean", float("inf"))
        exceeds = geo_mean > geo_mean_standard
        
        return {
            "geometric_mean": geo_mean,
            "sample_count": len(matching),
            "window_days": GEOMETRIC_MEAN_WINDOW_DAYS,
            "exceeds": exceeds,
            "standard": geo_mean_standard,
            "analyte": analyte,
            "station_id": station_id
        }
    
    def check_for_alerts(self, days: int = 3, priority_only: bool = False) -> list[Alert]:
        """
        Check for alert conditions in recent data.
        
        Checks:
        1. Single sample exceedances (standard AB 411 check)
        2. Fecal/Total coliform ratio (conditional standard)
        3. Multiple bay station elevations (potential CSO indicator)
        
        Args:
            days: Number of days to check
            priority_only: If True, only check BWTF priority sites
            
        Returns:
            List of Alert objects
        """
        stations = BWTF_PRIORITY_SITES if priority_only else None
        samples = self.fetch_recent_samples(days=days, stations=stations)
        latest = self.get_latest_by_station(samples)
        
        alerts = []
        
        # ── 1. Check for single sample exceedances ──
        for (station_id, analyte), sample in latest.items():
            if sample.exceeds_standard:
                standard_info = STANDARDS.get(analyte, {})
                severity = "advisory"
                
                # Higher severity for key indicators
                if analyte in ["ENTERO", "COLI_E"] and sample.value > sample.standard_value * 2:
                    severity = "warning"
                
                alert = Alert(
                    alert_type="elevated_bacteria",
                    station_id=station_id,
                    station_name=sample.station_name,
                    message=f"ELEVATED {standard_info.get('description', analyte)} at {sample.station_name}: "
                           f"{sample.value:.0f} MPN/100mL (Standard: {sample.standard_value:.0f})",
                    severity=severity,
                    sample_date=sample.sample_date,
                    details={
                        "analyte": analyte,
                        "value": sample.value,
                        "standard": sample.standard_value,
                        "exceedance_ratio": sample.value / sample.standard_value,
                        "check_type": "single_sample"
                    }
                )
                alerts.append(alert)
        
        # ── 2. Check fecal/total coliform ratio (AB 411 conditional standard) ──
        ratio_exceedances = self._check_coliform_ratio(samples)
        for station_id, total_val, fecal_val, ratio in ratio_exceedances:
            station_name = STATION_NAMES.get(station_id, station_id)
            # Only add if not already flagged by single sample check
            already_flagged = any(
                a.station_id == station_id and a.details.get("analyte") == "COLI_TOTAL"
                for a in alerts
            )
            if not already_flagged:
                alerts.append(Alert(
                    alert_type="elevated_bacteria",
                    station_id=station_id,
                    station_name=station_name,
                    message=f"COLIFORM RATIO EXCEEDANCE at {station_name}: "
                           f"Total Coliform {total_val:.0f} MPN/100mL exceeds conditional limit of 1,000 "
                           f"(fecal/total ratio: {ratio:.2f} > 0.1)",
                    severity="advisory",
                    sample_date=datetime.now(),
                    details={
                        "analyte": "COLI_TOTAL",
                        "value": total_val,
                        "fecal_value": fecal_val,
                        "ratio": ratio,
                        "standard": 1000,
                        "check_type": "coliform_ratio"
                    }
                ))
        
        # ── 3. Check for potential CSO conditions ──
        # Multiple elevated readings at bay stations suggest CSO event
        bay_station_ids = {
            a.station_id for a in alerts
            if a.station_id.startswith("BAY#")
        }
        if len(bay_station_ids) >= 2:
            # Identify affected drainage basins
            affected_basins = set()
            for station_id in bay_station_ids:
                basin = STATION_DRAINAGE_BASINS.get(station_id)
                if basin:
                    affected_basins.add(basin)
            
            basin_info = f" Affected basins: {', '.join(affected_basins)}." if affected_basins else ""
            alerts.append(Alert(
                alert_type="potential_cso",
                station_id="MULTIPLE",
                station_name="Bay Area Stations",
                message=f"POTENTIAL CSO EVENT: Multiple bay stations showing elevated bacteria.{basin_info} "
                       f"Call 1-877-SFBEACH (1-877-732-3224) or 415-242-2214 for current conditions.",
                severity="warning",
                sample_date=datetime.now(),
                details={
                    "affected_stations": sorted(bay_station_ids),
                    "station_count": len(bay_station_ids),
                    "affected_basins": list(affected_basins)
                }
            ))
        
        return alerts
    
    def get_current_status(self, days: int = 7) -> dict:
        """Get current status of all monitored beaches"""
        samples = self.fetch_recent_samples(days=days)
        latest = self.get_latest_by_station(samples)
        
        status = {}
        for (station_id, analyte), sample in latest.items():
            if not station_id:  # Skip empty station IDs
                continue
            if station_id not in status:
                status[station_id] = {
                    "station_name": sample.station_name,
                    "last_sampled": sample.sample_date,
                    "analytes": {},
                    "status": "safe"  # Will be updated if any exceed
                }
            
            status[station_id]["analytes"][analyte] = {
                "value": sample.value,
                "standard": sample.standard_value,
                "exceeds": sample.exceeds_standard
            }
            
            if sample.exceeds_standard:
                status[station_id]["status"] = "advisory"
        
        return status
    
    def format_status_report(self) -> str:
        """Generate a formatted status report"""
        status = self.get_current_status()
        alerts = self.check_for_alerts()
        
        lines = [
            "=" * 60,
            "🏖️  SF BEACH WATER QUALITY STATUS REPORT",
            f"📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
            ""
        ]
        
        # Active alerts
        if alerts:
            lines.append("⚠️  ACTIVE ALERTS:")
            lines.append("-" * 40)
            for alert in alerts:
                lines.append(f"  {alert.message}")
                lines.append(f"     Sample Date: {alert.sample_date.strftime('%Y-%m-%d')}")
                lines.append("")
        else:
            lines.append("✅ No active alerts - All stations within standards")
            lines.append("")
        
        # Station status
        lines.append("📍 STATION STATUS:")
        lines.append("-" * 40)
        
        for station_id in sorted(status.keys()):
            info = status[station_id]
            icon = "🟢" if info["status"] == "safe" else "🟠"
            lines.append(f"{icon} {info['station_name']}")
            lines.append(f"   Last sampled: {info['last_sampled'].strftime('%Y-%m-%d')}")
            
            for analyte, data in info["analytes"].items():
                if data["exceeds"]:
                    lines.append(f"   ⚠️ {analyte}: {data['value']:.0f} (limit: {data['standard']:.0f})")
            lines.append("")
        
        lines.extend([
            "-" * 60,
            "📞 For current conditions: 1-877-SFBEACH (1-877-732-3224)",
            "🌐 https://webapps.sfpuc.org/sapps/beachesandbay.html",
            "=" * 60
        ])
        
        return "\n".join(lines)


class CombinedWaterQualityMonitor:
    """
    Combined monitor that uses both SF Gov API and SFPUC real-time data,
    plus environmental context (weather and tides).
    
    Data sources:
    - SFPUC real-time API: CSO events, posting status (most current)
    - SF Gov Open Data API: Detailed bacteria measurements (1-2 day delay)
    - NWS API: Rainfall data and forecasts
    - NOAA CO-OPS API: Tide predictions
    """
    
    def __init__(self, app_token: Optional[str] = None):
        self.sf_gov_monitor = SFWaterQualityMonitor(app_token)
        
        # Import SFPUC scraper
        try:
            from shared.sfpuc_api import SFPUCRealTimeAPI
            self.sfpuc_api = SFPUCRealTimeAPI()
            self.has_sfpuc = True
        except ImportError:
            self.sfpuc_api = None
            self.has_sfpuc = False
        
        # Import weather/tides integration
        try:
            from shared.weather_tides import EnvironmentalContext
            self.env_context = EnvironmentalContext()
            self.has_weather = True
        except ImportError:
            self.env_context = None
            self.has_weather = False
    
    def get_combined_alerts(self) -> list[Alert]:
        """
        Get alerts from all data sources, prioritizing SFPUC real-time data.
        
        Sources checked (in priority order):
        1. SFPUC real-time API — CSO events and posting status
        2. NWS weather API — Rain advisories (72-hour rule)
        3. SF Gov API — Detailed bacteria measurements
        """
        alerts = []
        
        # ── 0. Check weather conditions (rain advisory) ──
        if self.has_weather:
            try:
                rain_advisory = self.env_context.weather.get_rain_advisory()
                if rain_advisory.is_active:
                    severity = "warning" if rain_advisory.cso_risk == "high" else "advisory"
                    alerts.append(Alert(
                        alert_type="rain_advisory",
                        station_id="ALL",
                        station_name="All SF Beaches",
                        message=rain_advisory.message,
                        severity=severity,
                        sample_date=datetime.now(),
                        details={
                            "total_inches": rain_advisory.total_recent_inches,
                            "cso_risk": rain_advisory.cso_risk,
                            "advisory_until": rain_advisory.advisory_until.isoformat() if rain_advisory.advisory_until else None,
                            "source": "nws_weather"
                        }
                    ))
            except Exception as e:
                print(f"Error fetching weather data: {e}")
        
        # ── 1. Get SFPUC real-time alerts (CSO and posting status) ──
        if self.has_sfpuc:
            try:
                stations = self.sfpuc_api.fetch_stations()
                
                # CSO alerts (highest priority)
                for station in stations:
                    if station.has_cso:
                        alerts.append(Alert(
                            alert_type="cso_discharge",
                            station_id=station.cso_station_id or station.station_id,
                            station_name=station.station_name,
                            message=f"CSO ALERT: Combined Sewer Discharge at {station.station_name}. "
                                   f"Avoid water contact. Call 1-877-SFBEACH for details.",
                            severity="warning",
                            sample_date=station.sample_date or datetime.now(),
                            details={
                                "cso_station_id": station.cso_station_id,
                                "latitude": station.latitude,
                                "longitude": station.longitude,
                                "source": "sfpuc_realtime"
                            }
                        ))
                
                # Posted station alerts (elevated bacteria)
                from shared.sfpuc_api import StationStatus
                for station in stations:
                    if station.status == StationStatus.POSTED and not station.has_cso:
                        alerts.append(Alert(
                            alert_type="elevated_bacteria",
                            station_id=station.posted_station_id or station.station_id,
                            station_name=station.station_name,
                            message=f"POSTED: {station.station_name} - Elevated bacteria levels. "
                                   f"Avoid water contact.",
                            severity="advisory",
                            sample_date=station.sample_date or datetime.now(),
                            details={
                                "posted_station_id": station.posted_station_id,
                                "latitude": station.latitude,
                                "longitude": station.longitude,
                                "source": "sfpuc_realtime"
                            }
                        ))
            except Exception as e:
                print(f"Error fetching SFPUC data: {e}")
        
        # Also get SF Gov API alerts for detailed bacteria data
        sf_gov_alerts = self.sf_gov_monitor.check_for_alerts()
        
        # Add SF Gov alerts that aren't duplicates
        existing_stations = {a.station_name for a in alerts}
        for alert in sf_gov_alerts:
            if alert.station_name not in existing_stations:
                alert.details["source"] = "sf_gov_api"
                alerts.append(alert)
        
        return alerts
    
    def format_combined_report(self) -> str:
        """Generate a combined status report from all data sources"""
        lines = [
            "=" * 60,
            "🏖️  SF BEACH WATER QUALITY - COMBINED STATUS REPORT",
            f"📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
            ""
        ]
        
        # ── Environmental conditions (weather + tides) ──
        if self.has_weather:
            try:
                rain = self.env_context.weather.get_rain_advisory()
                
                if rain.is_active:
                    lines.append("🌧️ RAIN ADVISORY ACTIVE")
                    lines.append("-" * 40)
                    lines.append(f"   {rain.message}")
                    lines.append(f"   CSO risk: {rain.cso_risk.upper()}")
                    lines.append("")
                elif rain.upcoming_rain:
                    lines.append("🌦️ RAIN IN FORECAST")
                    lines.append("-" * 40)
                    for event in rain.upcoming_rain[:2]:
                        lines.append(f"   • {event.description}")
                    lines.append("")
                else:
                    lines.append("☀️ No rain detected or forecasted")
                    lines.append("")
                
                # Tide info
                tide_summary = self.env_context.tides.format_tide_summary()
                lines.append(tide_summary)
                lines.append("")
            except Exception as e:
                lines.append(f"⚠️ Could not fetch environmental data: {e}")
                lines.append("")
        
        # ── SFPUC Real-time data (more current) ──
        if self.has_sfpuc:
            try:
                from shared.sfpuc_api import StationStatus
                stations = self.sfpuc_api.fetch_stations()
                cso_stations = [s for s in stations if s.has_cso]
                posted_stations = [s for s in stations if s.status == StationStatus.POSTED]
                safe_stations = [s for s in stations if s.status == StationStatus.SAFE]
                not_sampled = [s for s in stations if s.status in [StationStatus.NOT_SAMPLED, StationStatus.NOT_ROUTINELY_SAMPLED]]
                
                lines.append("📡 REAL-TIME STATUS (from SFPUC):")
                lines.append("-" * 40)
                
                # CSO Alerts
                if cso_stations:
                    lines.append("🚨 ACTIVE CSO (Combined Sewer Overflow) ALERTS:")
                    for station in cso_stations:
                        lines.append(f"   ⚠️  {station.station_name}")
                        if station.cso_station_id:
                            lines.append(f"      CSO Station: {station.cso_station_id}")
                    lines.append("   ℹ️  CSO events indicate sewage discharge in last 24-72 hours")
                
                # Posted Stations
                if posted_stations:
                    lines.append(f"🟠 POSTED STATIONS ({len(posted_stations)}):")
                    lines.append("   (Elevated bacteria or recent CSO - avoid water contact)")
                    for station in posted_stations:
                        cso_marker = " 🚨CSO" if station.has_cso else ""
                        date_str = station.sample_date.strftime('%m/%d') if station.sample_date else "N/A"
                        lines.append(f"   • {station.station_name}{cso_marker} (sampled: {date_str})")
                
                # Safe Stations
                if safe_stations:
                    lines.append(f"🟢 SAFE STATIONS ({len(safe_stations)}):")
                    for station in safe_stations:
                        date_str = station.sample_date.strftime('%m/%d') if station.sample_date else "N/A"
                        lines.append(f"   • {station.station_name} (sampled: {date_str})")
                
                # Not Sampled Stations
                if not_sampled:
                    lines.append(f"⚪ NOT SAMPLED ({len(not_sampled)}):")
                    lines.append("   (No data available at this time)")
                    for station in not_sampled:
                        date_str = station.sample_date.strftime('%m/%d') if station.sample_date else "N/A"
                        lines.append(f"   • {station.station_name} (last: {date_str})")
                
                lines.append(f"📊 SUMMARY: {len(safe_stations)} safe, {len(posted_stations)} posted, {len(cso_stations)} with CSO, {len(not_sampled)} not sampled")
                lines.append("")
            except Exception as e:
                lines.append(f"⚠️ Could not fetch SFPUC real-time data: {e}")
                lines.append("")
        
        # SF Gov API data (detailed bacteria levels)
        lines.append("📊 BACTERIA LEVELS (from SF Gov API):")
        lines.append("-" * 40)
        status = self.sf_gov_monitor.get_current_status()
        
        elevated_count = 0
        for station_id, info in sorted(status.items()):
            has_elevated = any(d["exceeds"] for d in info["analytes"].values())
            if has_elevated:
                elevated_count += 1
                lines.append(f"   ⚠️ {info['station_name']}")
                for analyte, data in info["analytes"].items():
                    if data["exceeds"]:
                        lines.append(f"      {analyte}: {data['value']:.0f} (limit: {data['standard']:.0f})")
        
        if elevated_count == 0:
            lines.append("   ✅ No elevated bacteria levels in recent samples")
        
        lines.append(f"   (Data as of: {list(status.values())[0]['last_sampled'].strftime('%Y-%m-%d') if status else 'N/A'})")
        
        lines.extend([
            "",
            "-" * 60,
            "📞 Beach Hotline: 1-877-SFBEACH (1-877-732-3224) or 415-242-2214",
            "🌐 https://webapps.sfpuc.org/sapps/beachesandbay.html",
            "ℹ️  Avoid water contact during and 72 hours after rain events",
            "=" * 60
        ])
        
        return "\n".join(lines)


def main():
    """Main entry point for testing"""
    print("Initializing Combined Water Quality Monitor...")
    
    # Test the combined monitor
    combined = CombinedWaterQualityMonitor()
    
    print("\n" + "=" * 60)
    print("COMBINED STATUS REPORT")
    print("=" * 60)
    report = combined.format_combined_report()
    print(report)
    
    print("\n" + "=" * 60)
    print("ALL ACTIVE ALERTS")
    print("=" * 60)
    alerts = combined.get_combined_alerts()
    
    if alerts:
        print(f"\n🚨 Found {len(alerts)} alert(s):\n")
        
        # Group by type
        cso_alerts = [a for a in alerts if a.alert_type == "cso_discharge"]
        bacteria_alerts = [a for a in alerts if a.alert_type != "cso_discharge"]
        
        if cso_alerts:
            print("CSO (Combined Sewer Overflow) Alerts:")
            for alert in cso_alerts:
                print(f"  • {alert.message}")
            print()
        
        if bacteria_alerts:
            print("Elevated Bacteria Alerts:")
            for alert in bacteria_alerts[:10]:  # Limit to 10
                print(f"  • {alert.station_name}: {alert.message[:60]}...")
            if len(bacteria_alerts) > 10:
                print(f"  ... and {len(bacteria_alerts) - 10} more")
    else:
        print("\n✅ No active alerts")


if __name__ == "__main__":
    main()
