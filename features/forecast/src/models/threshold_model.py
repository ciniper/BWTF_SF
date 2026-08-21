#!/usr/bin/env python3
"""
Basin-Specific CSO Threshold Model

Two-phase approach:
1. Rule-based model (Phase 1): Uses Rubin's thresholds as starting points,
   refined by correlating historical rainfall with observed CSO events.
2. ML model (Phase 2): Trained on the historical dataset once we have
   enough data points (target: 1+ year of polling data).

Key insight from Rubin (Feb 2025 storm):
  Different basins have different overflow thresholds.
  Westside overflowed at ~1.5", while North Shore did NOT overflow at ~1.0".

Features used:
- Cumulative rainfall (3h, 6h, 12h, 24h windows)
- Antecedent moisture index (prior 48-72h conditions)
- Rainfall intensity (peak hourly rate)
- Wind direction (west = more rain due to orographic lifting)
- Basin-specific capacity factors
"""

import json
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field

try:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import cross_val_score
    from sklearn.metrics import classification_report
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

from src.models.cumulative_rain import compute_all_features

MODEL_DIR = Path(__file__).parent.parent.parent / "data" / "models"


@dataclass
class CSOPrediction:
    """A CSO overflow prediction for a specific basin"""
    basin: str
    probability: float          # 0.0 to 1.0
    confidence: str             # "high", "moderate", "low"
    risk_level: str             # "none", "low", "moderate", "high", "very_high"
    primary_driver: str         # What's driving the prediction
    threshold_status: dict      # Which thresholds are exceeded
    time_horizon_hours: int     # How far out this prediction covers
    details: str                # Human-readable explanation


# ─── Phase 1: Rule-Based Threshold Model ─────────────────────────────────────

# Basin-specific thresholds (initial values from Rubin's observations)
# These should be refined as we collect more data
BASIN_THRESHOLDS = {
    "Westside": {
        "description": "Ocean Beach, Fort Funston, China Beach, Baker Beach",
        "treatment_plant": "Oceanside WPCP",
        # Lower thresholds — overflows earlier
        "24h_low_risk": 0.50,       # Start paying attention
        "24h_moderate_risk": 0.75,  # Likely overflow
        "24h_high_risk": 1.00,      # Almost certain overflow
        "3h_with_antecedent": 0.30, # 3h total with prior rain
        "3h_dry": 0.50,             # 3h total on dry ground
        # Wind adjustment: west wind increases effective rainfall
        "west_wind_multiplier": 1.2,  # Orographic lifting effect
        "south_wind_multiplier": 0.8, # Rain shadow
    },
    "North Shore": {
        "description": "Aquatic Park, Crissy Field, Crane Cove, Mission Creek",
        "treatment_plant": "North Shore facilities",
        # Higher thresholds — more capacity
        "24h_low_risk": 0.75,
        "24h_moderate_risk": 1.00,
        "24h_high_risk": 1.50,
        "3h_with_antecedent": 0.50,
        "3h_dry": 0.75,
        "west_wind_multiplier": 1.0,
        "south_wind_multiplier": 1.0,
    },
    "Southeast": {
        "description": "Islais Creek, Candlestick Point",
        "treatment_plant": "Southeast WPCP (largest)",
        # Moderate thresholds — Islais/Mission Creek overflow at moderate levels
        "24h_low_risk": 0.75,
        "24h_moderate_risk": 1.00,
        "24h_high_risk": 1.30,
        "3h_with_antecedent": 0.40,
        "3h_dry": 0.60,
        "west_wind_multiplier": 1.0,
        "south_wind_multiplier": 1.0,
    },
}


class RuleBasedModel:
    """
    Phase 1: Rule-based CSO prediction using Rubin's thresholds.
    
    This model works immediately with no training data.
    Thresholds are refined as we collect historical correlations.
    """
    
    def __init__(self, thresholds: Optional[dict] = None):
        self.thresholds = thresholds or BASIN_THRESHOLDS
    
    def predict_basin(self, basin: str,
                      cumulative_24h: float,
                      cumulative_3h: float = 0,
                      antecedent_moisture: float = 0,
                      wind_dir_deg: Optional[float] = None,
                      time_horizon_hours: int = 24) -> CSOPrediction:
        """
        Predict CSO probability for a specific drainage basin.
        
        Args:
            basin: Drainage basin name ("Westside", "North Shore", "Southeast")
            cumulative_24h: Running 24-hour rainfall total (inches)
            cumulative_3h: Running 3-hour rainfall total (inches)
            antecedent_moisture: Antecedent moisture index (0-1)
            wind_dir_deg: Wind direction in degrees (270=west, 180=south)
            time_horizon_hours: Forecast time horizon
            
        Returns:
            CSOPrediction with probability and risk assessment
        """
        if basin not in self.thresholds:
            return CSOPrediction(
                basin=basin, probability=0.0, confidence="low",
                risk_level="unknown", primary_driver="Unknown basin",
                threshold_status={}, time_horizon_hours=time_horizon_hours,
                details=f"No thresholds defined for basin: {basin}"
            )
        
        t = self.thresholds[basin]
        
        # Apply wind direction adjustment
        effective_24h = cumulative_24h
        wind_note = ""
        if wind_dir_deg is not None:
            if 240 <= wind_dir_deg <= 300:  # Westerly
                effective_24h *= t["west_wind_multiplier"]
                wind_note = f" (adjusted for west wind: effective {effective_24h:.2f}\")"
            elif 150 <= wind_dir_deg <= 210:  # Southerly
                effective_24h *= t["south_wind_multiplier"]
                wind_note = f" (adjusted for south wind: effective {effective_24h:.2f}\")"
        
        # Determine risk level and probability
        threshold_status = {
            "24h_actual": cumulative_24h,
            "24h_effective": effective_24h,
            "24h_low_risk_threshold": t["24h_low_risk"],
            "24h_moderate_risk_threshold": t["24h_moderate_risk"],
            "24h_high_risk_threshold": t["24h_high_risk"],
            "3h_actual": cumulative_3h,
            "antecedent_moisture": antecedent_moisture,
        }
        
        # Calculate probability using a sigmoid-like function
        # centered on the moderate risk threshold
        midpoint = t["24h_moderate_risk"]
        steepness = 4.0 / (t["24h_high_risk"] - t["24h_low_risk"])
        
        # Base probability from 24h cumulative
        if effective_24h <= 0:
            base_prob = 0.0
        else:
            base_prob = 1.0 / (1.0 + np.exp(-steepness * (effective_24h - midpoint)))
        
        # Boost for antecedent moisture (wet ground → earlier overflow)
        moisture_boost = min(0.15, antecedent_moisture * 0.3)
        
        # Boost for high 3-hour intensity
        intensity_threshold = t["3h_with_antecedent"] if antecedent_moisture > 0.1 else t["3h_dry"]
        intensity_boost = 0.0
        if cumulative_3h >= intensity_threshold:
            intensity_boost = min(0.2, (cumulative_3h / intensity_threshold - 1.0) * 0.2)
        
        probability = min(0.99, base_prob + moisture_boost + intensity_boost)
        
        # Determine risk level
        if probability >= 0.8:
            risk_level = "very_high"
        elif probability >= 0.6:
            risk_level = "high"
        elif probability >= 0.35:
            risk_level = "moderate"
        elif probability >= 0.15:
            risk_level = "low"
        else:
            risk_level = "none"
        
        # Determine primary driver
        if effective_24h >= t["24h_high_risk"]:
            primary_driver = f"24h cumulative ({effective_24h:.2f}\") exceeds high-risk threshold ({t['24h_high_risk']}\")"
        elif cumulative_3h >= intensity_threshold:
            primary_driver = f"High 3h intensity ({cumulative_3h:.2f}\") with {'wet' if antecedent_moisture > 0.1 else 'dry'} conditions"
        elif effective_24h >= t["24h_moderate_risk"]:
            primary_driver = f"24h cumulative ({effective_24h:.2f}\") exceeds moderate-risk threshold"
        elif effective_24h >= t["24h_low_risk"]:
            primary_driver = f"24h cumulative ({effective_24h:.2f}\") approaching risk threshold"
        else:
            primary_driver = "Below all thresholds"
        
        # Confidence based on data quality
        confidence = "moderate"  # Rule-based = moderate by default
        if time_horizon_hours <= 12:
            confidence = "high"
        elif time_horizon_hours > 72:
            confidence = "low"
        
        details = (
            f"{basin} basin: 24h cumulative {cumulative_24h:.2f}\"{wind_note}, "
            f"3h cumulative {cumulative_3h:.2f}\", "
            f"antecedent moisture {antecedent_moisture:.3f}. "
            f"Risk: {risk_level} ({probability:.0%}). "
            f"Driver: {primary_driver}."
        )
        
        return CSOPrediction(
            basin=basin,
            probability=round(probability, 3),
            confidence=confidence,
            risk_level=risk_level,
            primary_driver=primary_driver,
            threshold_status=threshold_status,
            time_horizon_hours=time_horizon_hours,
            details=details,
        )
    
    def predict_all_basins(self, cumulative_24h: float,
                           cumulative_3h: float = 0,
                           antecedent_moisture: float = 0,
                           wind_dir_deg: Optional[float] = None) -> List[CSOPrediction]:
        """Predict CSO probability for all basins"""
        predictions = []
        for basin in self.thresholds:
            pred = self.predict_basin(
                basin, cumulative_24h, cumulative_3h,
                antecedent_moisture, wind_dir_deg
            )
            predictions.append(pred)
        return predictions
    
    def format_prediction_report(self, predictions: List[CSOPrediction]) -> str:
        """Format predictions into a human-readable report"""
        lines = [
            "=" * 60,
            "🌧️ CSO OVERFLOW PREDICTION",
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
            ""
        ]
        
        risk_icons = {
            "none": "🟢",
            "low": "🟡",
            "moderate": "🟠",
            "high": "🔴",
            "very_high": "🚨",
        }
        
        for pred in sorted(predictions, key=lambda p: p.probability, reverse=True):
            icon = risk_icons.get(pred.risk_level, "❓")
            lines.append(f"{icon} {pred.basin} Basin — {pred.probability:.0%} probability")
            lines.append(f"   Risk: {pred.risk_level.upper()} | Confidence: {pred.confidence}")
            lines.append(f"   {pred.primary_driver}")
            lines.append("")
        
        # Overall assessment
        max_prob = max(p.probability for p in predictions)
        if max_prob >= 0.6:
            lines.append("🚨 HIGH CSO RISK — Avoid water contact at affected beaches")
        elif max_prob >= 0.35:
            lines.append("⚠️ MODERATE CSO RISK — Monitor conditions closely")
        elif max_prob >= 0.15:
            lines.append("🟡 LOW CSO RISK — Conditions worth watching")
        else:
            lines.append("✅ LOW/NO CSO RISK — Conditions favorable")
        
        lines.append("=" * 60)
        return "\n".join(lines)


# ─── Phase 2: ML Model (requires training data) ─────────────────────────────

class MLModel:
    """
    Phase 2: Machine learning CSO prediction model.
    
    Trained on historical rainfall → CSO event correlations.
    Superseded by train_v2.py, which trains on the ground-truth CSD
    event dataset (data/csd/); kept for the rule-based fallback path.
    
    Uses Gradient Boosting (good for tabular data with mixed features).
    
    IMPORTANT: This model requires sufficient training data.
    Target: 1+ year of 15-minute polling data to capture seasonal patterns.
    """
    
    def __init__(self):
        if not HAS_SKLEARN:
            raise ImportError("scikit-learn required for ML model. pip install scikit-learn")
        self.model = None
        self.feature_names = None
    
    def prepare_training_data(self, rain_df: pd.DataFrame,
                               cso_df: pd.DataFrame,
                               basin: str) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Align rainfall features with CSO event labels.
        
        Args:
            rain_df: Rainfall data with computed features (from cumulative_rain.py)
            cso_df: CSD event log (data/csd/sf_csd_events.csv or Supabase alert_log)
            basin: Which basin to train for
            
        Returns:
            (X features DataFrame, y labels Series)
        """
        # Compute rainfall features
        features = compute_all_features(rain_df)
        
        # Create binary labels: was there a CSO event at each timestamp?
        # Filter CSO events for this basin
        basin_cso = cso_df[cso_df["basin"] == basin] if "basin" in cso_df.columns else cso_df
        
        labels = pd.Series(0, index=features.index, name="cso_event")
        
        for _, event in basin_cso.iterrows():
            start = event.get("event_start")
            end = event.get("event_end")
            if start and end:
                mask = (features["timestamp"] >= start) & (features["timestamp"] <= end)
                labels[mask] = 1
            elif start:
                # Ongoing event — mark from start to end of data
                mask = features["timestamp"] >= start
                labels[mask] = 1
        
        # Select feature columns (exclude metadata)
        feature_cols = [c for c in features.columns
                        if c not in ("timestamp", "station_id") and features[c].dtype in ("float64", "int64", "bool")]
        
        X = features[feature_cols].fillna(0)
        self.feature_names = feature_cols
        
        return X, labels
    
    def train(self, X: pd.DataFrame, y: pd.Series) -> dict:
        """
        Train the CSO prediction model.
        
        Args:
            X: Feature matrix
            y: Binary labels (0=no CSO, 1=CSO event)
            
        Returns:
            Dict with training metrics
        """
        self.model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            min_samples_leaf=10,
            random_state=42,
        )
        
        # Cross-validation
        cv_scores = cross_val_score(self.model, X, y, cv=5, scoring="roc_auc")
        
        # Fit on full data
        self.model.fit(X, y)
        
        # Feature importance
        importances = dict(zip(self.feature_names, self.model.feature_importances_))
        top_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:10]
        
        return {
            "cv_auc_mean": cv_scores.mean(),
            "cv_auc_std": cv_scores.std(),
            "n_samples": len(X),
            "n_positive": y.sum(),
            "positive_rate": y.mean(),
            "top_features": top_features,
        }
    
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Predict CSO probability"""
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")
        return self.model.predict_proba(X)[:, 1]
    
    def save(self, path: Optional[Path] = None):
        """Save trained model to disk"""
        import pickle
        path = path or MODEL_DIR / "cso_model.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self.model, "features": self.feature_names}, f)
        print(f"Model saved to {path}")
    
    def load(self, path: Optional[Path] = None):
        """Load trained model from disk"""
        import pickle
        path = path or MODEL_DIR / "cso_model.pkl"
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.feature_names = data["features"]
        print(f"Model loaded from {path}")


if __name__ == "__main__":
    # Demo the rule-based model
    print("CSO Threshold Model — Demo")
    print("=" * 60)
    
    model = RuleBasedModel()
    
    # Scenario 1: Light rain
    print("\n📋 Scenario 1: Light rain (0.3\" in 24h)")
    predictions = model.predict_all_basins(cumulative_24h=0.3)
    print(model.format_prediction_report(predictions))
    
    # Scenario 2: Moderate rain (Rubin's "of interest" level)
    print("\n📋 Scenario 2: Moderate rain (0.8\" in 24h, west wind)")
    predictions = model.predict_all_basins(
        cumulative_24h=0.8, cumulative_3h=0.3,
        antecedent_moisture=0.05, wind_dir_deg=270
    )
    print(model.format_prediction_report(predictions))
    
    # Scenario 3: Heavy storm (like Feb 2025)
    print("\n📋 Scenario 3: Heavy storm (1.5\" in 24h, wet ground, west wind)")
    predictions = model.predict_all_basins(
        cumulative_24h=1.5, cumulative_3h=0.5,
        antecedent_moisture=0.3, wind_dir_deg=270
    )
    print(model.format_prediction_report(predictions))
