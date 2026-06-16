#!/usr/bin/env python3
"""
CSO Forecast Engine

Combines weather forecast data with the threshold/ML model to produce
CSO overflow predictions for the next 1-7 days.

Pipeline:
1. Fetch forecast data from multiple weather models (GFS, ECMWF, ICON)
2. Compute cumulative rainfall projections for each model
3. Run CSO threshold model for each basin
4. Assess model agreement (confidence indicator)
5. Produce per-basin, per-timeframe predictions

Output can be consumed by:
- BWTF alert system (as an additional alert source)
- Dashboard (probability timeline)
- Notifications (SMS/Slack/Discord)
"""

import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from dataclasses import dataclass, asdict

from src.collectors.forecast_models import ForecastCollector
from src.collectors.nws_rain import NWSRainfallCollector
from src.models.cumulative_rain import compute_all_features, compute_antecedent_moisture
from src.models.threshold_model import RuleBasedModel, CSOPrediction


@dataclass
class ForecastWindow:
    """A prediction for a specific time window"""
    window_label: str       # "next_12h", "12_24h", "24_48h", etc.
    window_start: datetime
    window_end: datetime
    predictions: List[CSOPrediction]
    model_agreement: float  # 0-1, how well forecast models agree
    confidence: str         # "high", "moderate", "low"
    headline: str           # One-line summary


@dataclass
class CSOForecast:
    """Complete CSO forecast output"""
    generated_at: datetime
    windows: List[ForecastWindow]
    overall_risk: str       # "none", "low", "moderate", "high", "very_high"
    headline: str
    details: str
    raw_data: Optional[dict] = None


class CSOForecastEngine:
    """
    Main forecast engine — produces CSO predictions from weather forecasts.
    
    Usage:
        engine = CSOForecastEngine()
        forecast = engine.generate_forecast()
        print(engine.format_forecast(forecast))
    """
    
    def __init__(self, model: Optional[RuleBasedModel] = None):
        self.forecast_collector = ForecastCollector()
        self.rain_collector = NWSRainfallCollector()
        self.model = model or RuleBasedModel()
    
    def _get_antecedent_conditions(self) -> dict:
        """
        Get current antecedent moisture conditions from recent observations.
        
        Returns:
            Dict with antecedent_moisture, recent_rain_48h, wind_dir
        """
        # Fetch recent observations
        obs_df = self.rain_collector.fetch_observations("KSFO", hours=72)
        
        if obs_df.empty:
            return {
                "antecedent_moisture": 0.0,
                "recent_rain_48h": 0.0,
                "wind_dir_deg": None,
            }
        
        # Compute antecedent moisture
        if "precip_1h_inches" in obs_df.columns:
            features = compute_antecedent_moisture(obs_df, "precip_1h_inches")
            ami = features["antecedent_moisture_index"].iloc[-1] if not features.empty else 0.0
            rain_48h = obs_df["precip_1h_inches"].tail(48).sum()
        else:
            ami = 0.0
            rain_48h = 0.0
        
        # Latest wind direction
        wind_dir = None
        if "wind_dir_deg" in obs_df.columns:
            recent_wind = obs_df["wind_dir_deg"].dropna()
            if not recent_wind.empty:
                wind_dir = recent_wind.iloc[-1]
        
        return {
            "antecedent_moisture": ami,
            "recent_rain_48h": rain_48h,
            "wind_dir_deg": wind_dir,
        }
    
    def _compute_forecast_cumulative(self, forecast_df: pd.DataFrame,
                                      antecedent_rain: float = 0) -> pd.DataFrame:
        """
        Compute cumulative rainfall from forecast data.
        
        Prepends any recent observed rainfall to get accurate
        running totals at the start of the forecast period.
        """
        if forecast_df.empty or "precipitation_inches" not in forecast_df.columns:
            return pd.DataFrame()
        
        df = forecast_df.copy()
        df = df.rename(columns={"precipitation_inches": "precip_1h_inches"})
        
        # Compute features
        features = compute_all_features(df, "precip_1h_inches")
        
        # Adjust early cumulative values for antecedent rain
        # (the rolling sum starts at 0, but there may have been prior rain)
        if antecedent_rain > 0:
            for col in features.columns:
                if col.startswith("cumulative_") and col.endswith("_in"):
                    window = int(col.split("_")[1].replace("h", ""))
                    # Add antecedent rain to early windows (decaying)
                    for i in range(min(window, len(features))):
                        decay = 1.0 - (i / window)
                        features.loc[features.index[i], col] += antecedent_rain * decay
        
        return features
    
    def generate_forecast(self, models: List[str] = None) -> CSOForecast:
        """
        Generate a complete CSO forecast.
        
        Args:
            models: Weather models to use (default: ["ecmwf", "gfs"])
            
        Returns:
            CSOForecast with predictions for multiple time windows
        """
        if models is None:
            models = ["ecmwf", "gfs"]
        
        now = datetime.now()
        
        # Step 1: Get antecedent conditions
        print("  Fetching antecedent conditions...")
        antecedent = self._get_antecedent_conditions()
        
        # Step 2: Fetch forecasts from each model
        print("  Fetching weather forecasts...")
        model_forecasts = {}
        for model_name in models:
            df = self.forecast_collector.fetch_model(model_name, forecast_days=7)
            if not df.empty:
                model_forecasts[model_name] = self._compute_forecast_cumulative(
                    df, antecedent["recent_rain_48h"]
                )
        
        if not model_forecasts:
            return CSOForecast(
                generated_at=now,
                windows=[],
                overall_risk="unknown",
                headline="⚠️ Unable to fetch forecast data",
                details="No weather model data available",
            )
        
        # Step 3: Define time windows
        windows_def = [
            ("next_12h", now, now + timedelta(hours=12)),
            ("12_24h", now + timedelta(hours=12), now + timedelta(hours=24)),
            ("24_48h", now + timedelta(hours=24), now + timedelta(hours=48)),
            ("48_72h", now + timedelta(hours=48), now + timedelta(hours=72)),
            ("3_5_days", now + timedelta(days=3), now + timedelta(days=5)),
            ("5_7_days", now + timedelta(days=5), now + timedelta(days=7)),
        ]
        
        # Step 4: Generate predictions for each window
        forecast_windows = []
        
        for label, win_start, win_end in windows_def:
            # Get max cumulative values from each model for this window
            model_predictions = {}
            
            for model_name, features_df in model_forecasts.items():
                if features_df.empty or "timestamp" not in features_df.columns:
                    continue
                
                window_data = features_df[
                    (features_df["timestamp"] >= win_start) &
                    (features_df["timestamp"] < win_end)
                ]
                
                if window_data.empty:
                    continue
                
                cum_24h = window_data["cumulative_24h_in"].max() if "cumulative_24h_in" in window_data.columns else 0
                cum_3h = window_data["cumulative_3h_in"].max() if "cumulative_3h_in" in window_data.columns else 0
                
                model_predictions[model_name] = {
                    "cumulative_24h": cum_24h,
                    "cumulative_3h": cum_3h,
                }
            
            if not model_predictions:
                continue
            
            # Average across models
            avg_24h = np.mean([m["cumulative_24h"] for m in model_predictions.values()])
            avg_3h = np.mean([m["cumulative_3h"] for m in model_predictions.values()])
            
            # Model agreement (standard deviation of 24h predictions)
            if len(model_predictions) >= 2:
                std_24h = np.std([m["cumulative_24h"] for m in model_predictions.values()])
                agreement = max(0, 1.0 - (std_24h / max(avg_24h, 0.1)))
            else:
                agreement = 0.5  # Single model = moderate confidence
            
            # Determine time-based confidence
            hours_out = (win_start - now).total_seconds() / 3600
            if hours_out <= 24:
                time_confidence = "high"
            elif hours_out <= 72:
                time_confidence = "moderate"
            else:
                time_confidence = "low"
            
            # Adjust confidence based on model agreement
            if agreement < 0.7 and time_confidence == "high":
                time_confidence = "moderate"
            elif agreement < 0.5:
                time_confidence = "low"
            
            # Run threshold model for each basin
            basin_predictions = self.model.predict_all_basins(
                cumulative_24h=avg_24h,
                cumulative_3h=avg_3h,
                antecedent_moisture=antecedent["antecedent_moisture"],
                wind_dir_deg=antecedent["wind_dir_deg"],
            )
            
            # Override confidence with time-based assessment
            for pred in basin_predictions:
                pred.confidence = time_confidence
                pred.time_horizon_hours = int(hours_out + (win_end - win_start).total_seconds() / 3600)
            
            # Generate headline for this window
            max_prob = max(p.probability for p in basin_predictions)
            max_basin = max(basin_predictions, key=lambda p: p.probability)
            
            if max_prob >= 0.6:
                headline = f"🚨 HIGH RISK: {max_basin.basin} basin {max_prob:.0%} CSO probability"
            elif max_prob >= 0.35:
                headline = f"⚠️ MODERATE RISK: {max_basin.basin} basin {max_prob:.0%} CSO probability"
            elif max_prob >= 0.15:
                headline = f"🟡 LOW RISK: Max {max_prob:.0%} CSO probability"
            else:
                headline = f"✅ MINIMAL RISK: Max {max_prob:.0%} CSO probability"
            
            forecast_windows.append(ForecastWindow(
                window_label=label,
                window_start=win_start,
                window_end=win_end,
                predictions=basin_predictions,
                model_agreement=round(agreement, 2),
                confidence=time_confidence,
                headline=headline,
            ))
        
        # Step 5: Overall assessment
        all_probs = [p.probability for w in forecast_windows for p in w.predictions]
        max_overall = max(all_probs) if all_probs else 0
        
        if max_overall >= 0.6:
            overall_risk = "high"
            overall_headline = "🚨 HIGH CSO RISK in forecast period — Avoid water contact at affected beaches"
        elif max_overall >= 0.35:
            overall_risk = "moderate"
            overall_headline = "⚠️ MODERATE CSO RISK in forecast period — Monitor conditions"
        elif max_overall >= 0.15:
            overall_risk = "low"
            overall_headline = "🟡 LOW CSO RISK — Some rain expected, worth monitoring"
        else:
            overall_risk = "none"
            overall_headline = "✅ MINIMAL CSO RISK — Conditions favorable for next 7 days"
        
        details = (
            f"Forecast generated at {now.strftime('%Y-%m-%d %H:%M')} using "
            f"{', '.join(models)} models. "
            f"Antecedent conditions: {antecedent['recent_rain_48h']:.2f}\" rain in last 48h, "
            f"moisture index {antecedent['antecedent_moisture']:.3f}."
        )
        
        return CSOForecast(
            generated_at=now,
            windows=forecast_windows,
            overall_risk=overall_risk,
            headline=overall_headline,
            details=details,
        )
    
    def format_forecast(self, forecast: CSOForecast) -> str:
        """Format forecast into a human-readable report"""
        lines = [
            "=" * 65,
            "🌧️  SF COMBINED SEWER OVERFLOW FORECAST",
            f"📅 Generated: {forecast.generated_at.strftime('%Y-%m-%d %H:%M')}",
            "=" * 65,
            "",
            f"  {forecast.headline}",
            f"  {forecast.details}",
            "",
        ]
        
        risk_icons = {
            "none": "🟢", "low": "🟡", "moderate": "🟠",
            "high": "🔴", "very_high": "🚨", "unknown": "❓",
        }
        
        for window in forecast.windows:
            time_range = (
                f"{window.window_start.strftime('%a %I%p')}"
                f" – {window.window_end.strftime('%a %I%p')}"
            )
            
            lines.append(f"┌─ {window.window_label.upper().replace('_', ' ')} ({time_range})")
            lines.append(f"│  {window.headline}")
            lines.append(f"│  Model agreement: {window.model_agreement:.0%} | Confidence: {window.confidence}")
            
            for pred in sorted(window.predictions, key=lambda p: p.probability, reverse=True):
                icon = risk_icons.get(pred.risk_level, "❓")
                lines.append(f"│    {icon} {pred.basin}: {pred.probability:.0%} ({pred.risk_level})")
            
            lines.append(f"└{'─' * 64}")
            lines.append("")
        
        lines.extend([
            "─" * 65,
            "📞 Beach Hotline: 1-877-SFBEACH or 415-242-2214",
            "🌐 https://webapps.sfpuc.org/sapps/beachesandbay.html",
            "ℹ️  Avoid water contact during and 72 hours after rain",
            "=" * 65,
        ])
        
        return "\n".join(lines)
    
    def to_json(self, forecast: CSOForecast) -> str:
        """Serialize forecast to JSON"""
        data = {
            "generated_at": forecast.generated_at.isoformat(),
            "overall_risk": forecast.overall_risk,
            "headline": forecast.headline,
            "details": forecast.details,
            "windows": [
                {
                    "label": w.window_label,
                    "start": w.window_start.isoformat(),
                    "end": w.window_end.isoformat(),
                    "model_agreement": w.model_agreement,
                    "confidence": w.confidence,
                    "headline": w.headline,
                    "predictions": [
                        {
                            "basin": p.basin,
                            "probability": p.probability,
                            "risk_level": p.risk_level,
                            "confidence": p.confidence,
                            "primary_driver": p.primary_driver,
                        }
                        for p in w.predictions
                    ]
                }
                for w in forecast.windows
            ]
        }
        return json.dumps(data, indent=2)
