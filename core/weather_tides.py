#!/usr/bin/env python3
"""
Weather and Tide Data Integration for SF Beach Water Quality

Provides rainfall and tide data to enhance water quality monitoring:
- NWS API: Precipitation forecasts and recent rain detection
- NOAA CO-OPS API: Tide predictions for San Francisco

Key Safety Rule:
  SFPUC advises avoiding water contact during and 72 hours after rain events.
  Rain is the #1 driver of CSO events and elevated bacteria at SF beaches.

Data Sources:
- NWS Forecast: https://api.weather.gov/gridpoints/MTR/88,126/forecast
- NOAA Tides: https://api.tidesandcurrents.noaa.gov/api/prod/datagetter
- NOAA Station 9414290 (San Francisco, CA)
"""

import json
import sys
import requests
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
from enum import Enum

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ─── NWS API Configuration ───────────────────────────────────────────────────

# San Francisco gridpoint (from https://api.weather.gov/points/37.7749,-122.4194)
NWS_GRID_OFFICE = "MTR"
NWS_GRID_X = 88
NWS_GRID_Y = 126

NWS_FORECAST_URL = f"https://api.weather.gov/gridpoints/{NWS_GRID_OFFICE}/{NWS_GRID_X},{NWS_GRID_Y}/forecast"
NWS_FORECAST_HOURLY_URL = f"https://api.weather.gov/gridpoints/{NWS_GRID_OFFICE}/{NWS_GRID_X},{NWS_GRID_Y}/forecast/hourly"
NWS_OBSERVATIONS_URL = "https://api.weather.gov/stations/KOAK/observations/latest"  # Oakland (closest with reliable data)
NWS_SF_OBSERVATIONS_URL = "https://api.weather.gov/stations/KSFO/observations/latest"  # SFO Airport

# ─── NOAA CO-OPS Tides Configuration ─────────────────────────────────────────

NOAA_TIDES_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NOAA_SF_STATION = "9414290"  # San Francisco, CA

# ─── Rain Advisory Thresholds ────────────────────────────────────────────────

# SFPUC guidance: avoid water contact during and immediately after rain
RAIN_ADVISORY_HOURS = 72  # Hours after rain to maintain advisory
RAIN_THRESHOLD_INCHES = 0.1  # Minimum rainfall to trigger advisory
HEAVY_RAIN_THRESHOLD_INCHES = 0.5  # Heavy rain — likely CSO trigger
PRECIP_PROBABILITY_THRESHOLD = 60  # % probability to flag upcoming rain


class RainSeverity(Enum):
    """Rain severity levels for beach advisories"""
    NONE = "none"
    LIGHT = "light"          # < 0.25 inches
    MODERATE = "moderate"    # 0.25 - 0.5 inches
    HEAVY = "heavy"          # > 0.5 inches — likely CSO trigger
    UNKNOWN = "unknown"


@dataclass
class RainEvent:
    """Represents a detected or forecasted rain event"""
    start_time: datetime
    end_time: Optional[datetime]
    severity: RainSeverity
    total_inches: float
    description: str
    is_forecast: bool  # True if forecasted, False if observed


@dataclass
class RainAdvisory:
    """Rain-based beach advisory"""
    is_active: bool
    severity: RainSeverity
    message: str
    advisory_until: Optional[datetime]  # When the 72-hour window expires
    recent_rain: List[RainEvent] = field(default_factory=list)
    upcoming_rain: List[RainEvent] = field(default_factory=list)
    total_recent_inches: float = 0.0
    cso_risk: str = "low"  # "low", "moderate", "high"


@dataclass
class TidePrediction:
    """A single tide prediction"""
    time: datetime
    height_ft: float
    tide_type: str  # "H" (high) or "L" (low)


@dataclass
class TideInfo:
    """Current tide information"""
    predictions: List[TidePrediction]
    next_high: Optional[TidePrediction]
    next_low: Optional[TidePrediction]
    current_trend: str  # "rising", "falling", "high", "low"


class WeatherAPI:
    """
    Fetches weather data from the National Weather Service API.
    
    The NWS API is free, requires no API key, and provides:
    - 7-day forecasts with precipitation probability
    - Hourly forecasts with detailed precipitation data
    - Recent observations from nearby weather stations
    
    Requires User-Agent header per NWS API policy.
    """
    
    def __init__(self, contact_email: str = "bwtf@surfrider.org"):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f"(SFBeachWaterQuality, {contact_email})",
            "Accept": "application/geo+json"
        })
    
    def get_forecast(self) -> Optional[dict]:
        """
        Get 7-day forecast for San Francisco.
        
        Returns:
            Raw NWS forecast data or None on error
        """
        try:
            response = self.session.get(NWS_FORECAST_URL, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching NWS forecast: {e}")
            return None
    
    def get_hourly_forecast(self) -> Optional[dict]:
        """
        Get hourly forecast for San Francisco (next 156 hours).
        
        Returns:
            Raw NWS hourly forecast data or None on error
        """
        try:
            response = self.session.get(NWS_FORECAST_HOURLY_URL, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching NWS hourly forecast: {e}")
            return None
    
    def get_recent_observations(self) -> Optional[dict]:
        """
        Get most recent weather observations from SFO airport station.
        
        Returns:
            Raw NWS observation data or None on error
        """
        try:
            response = self.session.get(NWS_SF_OBSERVATIONS_URL, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching NWS observations: {e}")
            return None
    
    def get_rain_advisory(self) -> RainAdvisory:
        """
        Determine if a rain advisory should be active based on:
        1. Recent observations (has it rained recently?)
        2. Forecast data (is rain expected?)
        
        SFPUC guidance: Avoid water contact during and 72 hours after rain.
        
        Returns:
            RainAdvisory with current status
        """
        recent_rain = []
        upcoming_rain = []
        total_recent_inches = 0.0
        now = datetime.now()
        
        # ── Check recent observations ──
        obs = self.get_recent_observations()
        if obs:
            try:
                props = obs.get("properties", {})
                precip_last_hour = props.get("precipitationLastHour", {})
                precip_value = precip_last_hour.get("value")
                
                if precip_value is not None and precip_value > 0:
                    # Convert mm to inches
                    inches = precip_value / 25.4
                    total_recent_inches += inches
                    severity = self._classify_rain(inches)
                    recent_rain.append(RainEvent(
                        start_time=now - timedelta(hours=1),
                        end_time=now,
                        severity=severity,
                        total_inches=inches,
                        description=f"Recent rainfall: {inches:.2f} inches in last hour",
                        is_forecast=False
                    ))
                
                # Check 6-hour precipitation
                precip_6h = props.get("precipitationLast6Hours", {})
                precip_6h_value = precip_6h.get("value")
                if precip_6h_value is not None and precip_6h_value > 0:
                    inches_6h = precip_6h_value / 25.4
                    if inches_6h > total_recent_inches:
                        total_recent_inches = inches_6h
                        severity = self._classify_rain(inches_6h)
                        recent_rain.append(RainEvent(
                            start_time=now - timedelta(hours=6),
                            end_time=now,
                            severity=severity,
                            total_inches=inches_6h,
                            description=f"Recent rainfall: {inches_6h:.2f} inches in last 6 hours",
                            is_forecast=False
                        ))
            except (KeyError, TypeError, ValueError):
                pass
        
        # ── Check forecast for upcoming rain ──
        forecast = self.get_forecast()
        if forecast:
            try:
                periods = forecast.get("properties", {}).get("periods", [])
                for period in periods[:6]:  # Check next 3 days (6 periods)
                    precip_prob = period.get("probabilityOfPrecipitation", {}).get("value", 0)
                    if precip_prob and precip_prob >= PRECIP_PROBABILITY_THRESHOLD:
                        # Parse the forecast period time
                        start_str = period.get("startTime", "")
                        try:
                            start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        except ValueError:
                            start_time = now
                        
                        detailed = period.get("detailedForecast", "")
                        short = period.get("shortForecast", "")
                        
                        # Estimate severity from forecast text
                        severity = RainSeverity.LIGHT
                        lower_detail = detailed.lower()
                        if any(w in lower_detail for w in ["heavy", "downpour", "flooding"]):
                            severity = RainSeverity.HEAVY
                        elif any(w in lower_detail for w in ["moderate", "showers likely", "rain likely"]):
                            severity = RainSeverity.MODERATE
                        
                        upcoming_rain.append(RainEvent(
                            start_time=start_time,
                            end_time=None,
                            severity=severity,
                            total_inches=0.0,  # Forecast doesn't always give exact amounts
                            description=f"{period.get('name', 'Upcoming')}: {short} ({precip_prob}% chance)",
                            is_forecast=True
                        ))
            except (KeyError, TypeError, ValueError):
                pass
        
        # ── Determine advisory status ──
        is_active = False
        advisory_until = None
        severity = RainSeverity.NONE
        cso_risk = "low"
        
        if recent_rain:
            is_active = True
            # Advisory lasts 72 hours after last rain
            last_rain_end = max(r.end_time or r.start_time for r in recent_rain)
            advisory_until = last_rain_end + timedelta(hours=RAIN_ADVISORY_HOURS)
            severity = max((r.severity for r in recent_rain), key=lambda s: list(RainSeverity).index(s))
            
            if total_recent_inches >= HEAVY_RAIN_THRESHOLD_INCHES:
                cso_risk = "high"
            elif total_recent_inches >= RAIN_THRESHOLD_INCHES:
                cso_risk = "moderate"
        
        if upcoming_rain:
            heaviest = max((r.severity for r in upcoming_rain), key=lambda s: list(RainSeverity).index(s))
            if heaviest in (RainSeverity.HEAVY, RainSeverity.MODERATE):
                if cso_risk == "low":
                    cso_risk = "moderate"
        
        # Build message
        if is_active:
            message = (
                f"🌧️ RAIN ADVISORY: Recent rainfall detected ({total_recent_inches:.2f} inches). "
                f"SFPUC advises avoiding water contact during and 72 hours after rain. "
                f"Advisory active until {advisory_until.strftime('%m/%d %I:%M %p') if advisory_until else 'unknown'}."
            )
        elif upcoming_rain:
            next_rain = upcoming_rain[0]
            message = (
                f"🌦️ RAIN FORECAST: {next_rain.description}. "
                f"Plan accordingly — SFPUC advises avoiding water contact during and 72 hours after rain."
            )
        else:
            message = "☀️ No rain detected or forecasted. Conditions favorable for beach recreation."
        
        return RainAdvisory(
            is_active=is_active,
            severity=severity,
            message=message,
            advisory_until=advisory_until,
            recent_rain=recent_rain,
            upcoming_rain=upcoming_rain,
            total_recent_inches=total_recent_inches,
            cso_risk=cso_risk
        )
    
    def _classify_rain(self, inches: float) -> RainSeverity:
        """Classify rainfall amount into severity level"""
        if inches >= HEAVY_RAIN_THRESHOLD_INCHES:
            return RainSeverity.HEAVY
        elif inches >= 0.25:
            return RainSeverity.MODERATE
        elif inches >= RAIN_THRESHOLD_INCHES:
            return RainSeverity.LIGHT
        return RainSeverity.NONE


class TidesAPI:
    """
    Fetches tide predictions from NOAA CO-OPS API.
    
    Station 9414290 (San Francisco, CA) provides tide data
    relevant to all SF beaches. Tides affect:
    - Bacteria concentration (dilution at high tide)
    - CSO discharge behavior
    - Beach accessibility
    
    No API key required.
    """
    
    def __init__(self):
        self.session = requests.Session()
    
    def get_tide_predictions(self, hours: int = 48) -> List[TidePrediction]:
        """
        Get high/low tide predictions for San Francisco.
        
        Args:
            hours: Number of hours of predictions to fetch
            
        Returns:
            List of TidePrediction objects
        """
        now = datetime.now()
        end = now + timedelta(hours=hours)
        
        params = {
            "station": NOAA_SF_STATION,
            "product": "predictions",
            "begin_date": now.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "datum": "MLLW",
            "time_zone": "lst_ldt",
            "units": "english",
            "interval": "hilo",
            "format": "json"
        }
        
        try:
            response = self.session.get(NOAA_TIDES_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching NOAA tide data: {e}")
            return []
        except json.JSONDecodeError as e:
            print(f"Error parsing NOAA tide response: {e}")
            return []
        
        predictions = []
        for pred in data.get("predictions", []):
            try:
                time = datetime.strptime(pred["t"], "%Y-%m-%d %H:%M")
                height = float(pred["v"])
                tide_type = pred.get("type", "")
                
                predictions.append(TidePrediction(
                    time=time,
                    height_ft=height,
                    tide_type=tide_type
                ))
            except (ValueError, KeyError) as e:
                continue
        
        return predictions
    
    def get_tide_info(self) -> Optional[TideInfo]:
        """
        Get current tide information including next high/low.
        
        Returns:
            TideInfo with predictions and current trend, or None on error
        """
        predictions = self.get_tide_predictions(hours=48)
        if not predictions:
            return None
        
        now = datetime.now()
        
        # Find next high and low tides
        next_high = None
        next_low = None
        
        for pred in predictions:
            if pred.time > now:
                if pred.tide_type == "H" and next_high is None:
                    next_high = pred
                elif pred.tide_type == "L" and next_low is None:
                    next_low = pred
                if next_high and next_low:
                    break
        
        # Determine current trend
        if next_high and next_low:
            if next_high.time < next_low.time:
                current_trend = "rising"
            else:
                current_trend = "falling"
        elif next_high:
            current_trend = "rising"
        elif next_low:
            current_trend = "falling"
        else:
            current_trend = "unknown"
        
        return TideInfo(
            predictions=predictions,
            next_high=next_high,
            next_low=next_low,
            current_trend=current_trend
        )
    
    def format_tide_summary(self) -> str:
        """Generate a human-readable tide summary"""
        info = self.get_tide_info()
        if not info:
            return "⚠️ Tide data unavailable"
        
        lines = ["🌊 TIDE INFORMATION (San Francisco):"]
        lines.append(f"   Current trend: {'📈 Rising' if info.current_trend == 'rising' else '📉 Falling'}")
        
        if info.next_high:
            lines.append(
                f"   Next high tide: {info.next_high.time.strftime('%I:%M %p')} "
                f"({info.next_high.height_ft:.1f} ft)"
            )
        if info.next_low:
            lines.append(
                f"   Next low tide:  {info.next_low.time.strftime('%I:%M %p')} "
                f"({info.next_low.height_ft:.1f} ft)"
            )
        
        # Show next 24 hours of predictions
        now = datetime.now()
        upcoming = [p for p in info.predictions if now < p.time < now + timedelta(hours=24)]
        if upcoming:
            lines.append("   Next 24 hours:")
            for pred in upcoming:
                tide_label = "High" if pred.tide_type == "H" else "Low"
                lines.append(
                    f"     {pred.time.strftime('%a %I:%M %p'):>16s}: "
                    f"{tide_label:4s} {pred.height_ft:+.1f} ft"
                )
        
        return "\n".join(lines)


class EnvironmentalContext:
    """
    Combines weather and tide data to provide environmental context
    for water quality assessments.
    
    This is the main interface for other modules to use.
    """
    
    def __init__(self, contact_email: str = "bwtf@surfrider.org"):
        self.weather = WeatherAPI(contact_email)
        self.tides = TidesAPI()
    
    def get_full_context(self) -> dict:
        """
        Get complete environmental context for water quality assessment.
        
        Returns:
            Dict with rain advisory, tide info, and combined risk assessment
        """
        rain_advisory = self.weather.get_rain_advisory()
        tide_info = self.tides.get_tide_info()
        
        # Combined risk assessment
        overall_risk = "low"
        risk_factors = []
        
        if rain_advisory.is_active:
            risk_factors.append("Recent rainfall detected")
            if rain_advisory.cso_risk == "high":
                overall_risk = "high"
            elif rain_advisory.cso_risk == "moderate":
                overall_risk = "moderate"
        
        if rain_advisory.upcoming_rain:
            risk_factors.append("Rain in forecast")
            if overall_risk == "low":
                overall_risk = "moderate"
        
        return {
            "timestamp": datetime.now().isoformat(),
            "rain_advisory": {
                "is_active": rain_advisory.is_active,
                "severity": rain_advisory.severity.value,
                "message": rain_advisory.message,
                "advisory_until": rain_advisory.advisory_until.isoformat() if rain_advisory.advisory_until else None,
                "total_recent_inches": rain_advisory.total_recent_inches,
                "cso_risk": rain_advisory.cso_risk,
                "upcoming_rain_count": len(rain_advisory.upcoming_rain)
            },
            "tides": {
                "current_trend": tide_info.current_trend if tide_info else "unknown",
                "next_high": {
                    "time": tide_info.next_high.time.isoformat(),
                    "height_ft": tide_info.next_high.height_ft
                } if tide_info and tide_info.next_high else None,
                "next_low": {
                    "time": tide_info.next_low.time.isoformat(),
                    "height_ft": tide_info.next_low.height_ft
                } if tide_info and tide_info.next_low else None
            },
            "overall_risk": overall_risk,
            "risk_factors": risk_factors
        }
    
    def format_environmental_report(self) -> str:
        """Generate a formatted environmental context report"""
        rain = self.weather.get_rain_advisory()
        tide_summary = self.tides.format_tide_summary()
        
        lines = [
            "=" * 60,
            "🌤️  ENVIRONMENTAL CONDITIONS",
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
            ""
        ]
        
        # Rain advisory
        if rain.is_active:
            lines.append("🌧️ RAIN ADVISORY ACTIVE")
            lines.append("-" * 40)
            lines.append(f"   {rain.message}")
            lines.append(f"   Total recent rainfall: {rain.total_recent_inches:.2f} inches")
            lines.append(f"   CSO risk level: {rain.cso_risk.upper()}")
            if rain.advisory_until:
                lines.append(f"   Advisory until: {rain.advisory_until.strftime('%m/%d/%Y %I:%M %p')}")
            lines.append("")
        elif rain.upcoming_rain:
            lines.append("🌦️ RAIN IN FORECAST")
            lines.append("-" * 40)
            for event in rain.upcoming_rain[:3]:
                lines.append(f"   • {event.description}")
            lines.append("")
        else:
            lines.append("☀️ No rain detected or forecasted")
            lines.append("")
        
        # Tide info
        lines.append(tide_summary)
        lines.append("")
        
        # Safety guidance
        lines.append("📋 SAFETY GUIDANCE:")
        lines.append("-" * 40)
        if rain.is_active or rain.cso_risk in ("moderate", "high"):
            lines.append("   ⚠️  Avoid water contact during and 72 hours after rain")
            lines.append("   ⚠️  CSO events are more likely during/after heavy rain")
            lines.append("   📞 Call 1-877-SFBEACH or 415-242-2214 for current conditions")
        else:
            lines.append("   ✅ Weather conditions favorable for beach recreation")
            lines.append("   ℹ️  Always check current water quality before entering water")
        
        lines.append("=" * 60)
        return "\n".join(lines)


def main():
    """Test weather and tide integration"""
    print("Testing Weather & Tide Integration...")
    print()
    
    ctx = EnvironmentalContext()
    
    # Print formatted report
    report = ctx.format_environmental_report()
    print(report)
    
    # Print JSON context
    print("\nJSON Context:")
    print("=" * 60)
    context = ctx.get_full_context()
    print(json.dumps(context, indent=2))


if __name__ == "__main__":
    main()
