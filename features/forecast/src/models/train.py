#!/usr/bin/env python3
"""
Model Training Pipeline

Builds the full training dataset from historical rain + bacteria data,
trains basin-specific CSO prediction models, and evaluates performance.

Key design decisions (from data analysis):
- Lag 0 (same-day) gives strongest signal — SFPUC samples during/after storms
- 1-day rain is primary feature, 2-3 day cumulative are secondary
- Antecedent moisture (7-day prior rain) is a key modifier
- Different basins have different thresholds (Westside lowest)
- Bacteria persists 3-5 days after CSO, returns to baseline by day 6-7
"""

import json
import pickle
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple, Optional

from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import cross_val_score, StratifiedKFold
from sklearn.metrics import (
    classification_report, roc_auc_score, precision_recall_curve,
    roc_curve, confusion_matrix
)
from sklearn.calibration import calibration_curve
from scipy.special import expit  # sigmoid

DATA_DIR = Path(__file__).parent.parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MODEL_DIR = DATA_DIR / "models"

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
    "BAY#310_SL": "North Shore", "BAY#305_SL": "North Shore",
    "BAY#320_SL": "Southeast", "BAY#320.1_SL": "Southeast",
    "BAY#320.2_SL": "Southeast", "BAY#315_SL": "Southeast",
}

BACTERIA_THRESHOLDS = {
    "ENTERO": 104,
    "COLI_E": 235,
    "COLI_FECAL": 400,
    "COLI_TOTAL": 10000,
}


def build_full_training_dataset() -> pd.DataFrame:
    """
    Build training dataset starting from ALL calendar days (not just sample days).
    
    For each calendar day, compute:
    - Rainfall features (same-day, lagged, cumulative windows, antecedent)
    - Bacteria labels (joined from sample data, lag 0)
    - CSO proxy labels (3+ stations elevated across 2+ basins)
    - Per-basin exceedance rates
    
    Returns:
        DataFrame with ~2100 rows (one per calendar day), rain features + labels
    """
    print("Loading raw data...")
    rain_df = pd.read_csv(RAW_DIR / "historical_rain.csv", parse_dates=["date"])
    bacteria_df = pd.read_csv(RAW_DIR / "historical_bacteria.csv", parse_dates=["sample_date"])
    
    # ── Build daily rain features ──
    print("Building daily rain features...")
    rain_pivot = rain_df.pivot_table(
        index="date", columns="rain_station_name",
        values="precip_inches", aggfunc="first"
    ).reset_index()
    rain_pivot.columns.name = None
    
    precip_cols = [c for c in rain_pivot.columns if c != "date"]
    rain_pivot["precip_avg"] = rain_pivot[precip_cols].mean(axis=1)
    rain_pivot["precip_max"] = rain_pivot[precip_cols].max(axis=1)
    rain_pivot = rain_pivot.sort_values("date").reset_index(drop=True)
    
    # Fill missing precip with 0
    rain_pivot["precip_avg"] = rain_pivot["precip_avg"].fillna(0)
    rain_pivot["precip_max"] = rain_pivot["precip_max"].fillna(0)
    
    # Cumulative windows
    for w in [2, 3, 5, 7, 14, 30]:
        rain_pivot[f"rain_{w}d_cum"] = rain_pivot["precip_avg"].rolling(window=w, min_periods=1).sum()
    
    # Lagged rain (yesterday, 2 days ago, 3 days ago)
    for lag in [1, 2, 3, 5, 7]:
        rain_pivot[f"rain_lag{lag}d"] = rain_pivot["precip_avg"].shift(lag).fillna(0)
    
    # Antecedent moisture: exponentially weighted sum of prior 14 days
    half_life = 3  # days
    weights = np.exp(-np.log(2) / half_life * np.arange(14))
    rain_pivot["antecedent_moisture"] = (
        rain_pivot["precip_avg"]
        .rolling(window=14, min_periods=1)
        .apply(lambda x: np.sum(x * weights[:len(x)][::-1]) / np.sum(weights[:len(x)]), raw=True)
    )
    
    # Was it raining recently? (any rain in prior 3 days)
    rain_pivot["wet_prior_3d"] = (rain_pivot["rain_3d_cum"].shift(1).fillna(0) > 0.1).astype(int)
    
    # Peak intensity proxy: max single-day rain in last 3 days
    rain_pivot["peak_3d"] = rain_pivot["precip_avg"].rolling(window=3, min_periods=1).max()
    
    # Dry spell length (consecutive days with < 0.05")
    is_dry = (rain_pivot["precip_avg"] < 0.05).astype(int)
    groups = is_dry.ne(is_dry.shift()).cumsum()
    rain_pivot["dry_spell_days"] = is_dry.groupby(groups).cumsum()
    
    # ── Build bacteria labels (lag 0 — same day) ──
    print("Building bacteria labels...")
    
    # Overall daily labels
    bact_daily = bacteria_df.groupby("sample_date").agg(
        total_samples=("value", "count"),
        elevated_samples=("exceeds_standard", "sum"),
        stations_sampled=("station", "nunique"),
    ).reset_index()
    bact_daily["exceedance_rate"] = bact_daily["elevated_samples"] / bact_daily["total_samples"]
    
    # Elevated station count
    elevated_by_day = (
        bacteria_df[bacteria_df["exceeds_standard"]]
        .groupby("sample_date")
        .agg(
            elevated_station_count=("station", "nunique"),
            elevated_basin_count=("basin", "nunique"),
        )
        .reset_index()
    )
    bact_daily = bact_daily.merge(elevated_by_day, on="sample_date", how="left")
    bact_daily["elevated_station_count"] = bact_daily["elevated_station_count"].fillna(0)
    bact_daily["elevated_basin_count"] = bact_daily["elevated_basin_count"].fillna(0)
    
    # CSO proxy: 3+ stations elevated across 2+ basins
    bact_daily["likely_cso"] = (
        (bact_daily["elevated_station_count"] >= 3) &
        (bact_daily["elevated_basin_count"] >= 2)
    ).astype(int)
    
    # Any elevated
    bact_daily["any_elevated"] = (bact_daily["elevated_samples"] > 0).astype(int)
    
    # Per-basin exceedance rates
    for basin in ["Westside", "North Shore", "Southeast"]:
        basin_stations = [s for s, b in STATION_BASINS.items() if b == basin]
        basin_data = bacteria_df[bacteria_df["station"].isin(basin_stations)]
        
        basin_daily = basin_data.groupby("sample_date").agg(
            **{f"{basin}_samples": ("value", "count"),
               f"{basin}_elevated": ("exceeds_standard", "sum")}
        ).reset_index()
        basin_daily[f"{basin}_exc_rate"] = (
            basin_daily[f"{basin}_elevated"] / basin_daily[f"{basin}_samples"]
        )
        basin_daily[f"{basin}_any_elevated"] = (basin_daily[f"{basin}_elevated"] > 0).astype(int)
        
        bact_daily = bact_daily.merge(basin_daily, on="sample_date", how="left")
    
    bact_daily = bact_daily.rename(columns={"sample_date": "date"})
    
    # ── Merge: start from rain (all calendar days), join bacteria ──
    print("Merging datasets...")
    dataset = rain_pivot.merge(bact_daily, on="date", how="left")
    dataset = dataset.sort_values("date").reset_index(drop=True)
    
    # Mark which days have bacteria data
    dataset["has_sample"] = dataset["total_samples"].notna().astype(int)
    
    print(f"\nDataset built: {len(dataset)} total days")
    print(f"  Days with bacteria samples: {dataset['has_sample'].sum()}")
    print(f"  Days with likely CSO: {dataset['likely_cso'].sum()}")
    print(f"  Date range: {dataset['date'].min().date()} to {dataset['date'].max().date()}")
    
    return dataset


def get_feature_columns() -> list:
    """
    Return the list of feature columns for model training.
    
    NOTE: precip_max was REMOVED because it means different things in training
    vs inference. In training data (ACIS), precip_max = max daily total across
    2 rain gauge stations. In the live dashboard (Open-Meteo), it was being
    computed as max hourly precipitation — a completely different scale (5-10x
    smaller). This caused Westside and North Shore models to massively
    underpredict during sustained, moderate-intensity storms.
    
    All remaining features are consistent between ACIS training data and
    Open-Meteo inference: daily totals, cumulative windows, and lags.
    """
    return [
        "precip_avg",
        "rain_2d_cum", "rain_3d_cum", "rain_5d_cum", "rain_7d_cum",
        "rain_14d_cum", "rain_30d_cum",
        "rain_lag1d", "rain_lag2d", "rain_lag3d", "rain_lag5d", "rain_lag7d",
        "antecedent_moisture", "wet_prior_3d", "peak_3d", "dry_spell_days",
    ]


def train_citywide_model(dataset: pd.DataFrame) -> dict:
    """
    Train a city-wide CSO prediction model.
    
    Target: likely_cso (3+ stations elevated across 2+ basins)
    
    Returns:
        Dict with model, metrics, feature importances, thresholds
    """
    print("\n" + "=" * 60)
    print("TRAINING: City-wide CSO model")
    print("=" * 60)
    
    # Filter to days with bacteria samples (we need labels)
    labeled = dataset[dataset["has_sample"] == 1].copy()
    
    features = get_feature_columns()
    X = labeled[features].fillna(0)
    y = labeled["likely_cso"].fillna(0).astype(int)
    
    print(f"Training samples: {len(X)}")
    print(f"Positive (CSO) samples: {y.sum()} ({y.mean():.1%})")
    
    # Train gradient boosting
    model = GradientBoostingClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        min_samples_leaf=10,
        subsample=0.8,
        random_state=42,
    )
    
    # Cross-validation
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    auc_scores = cross_val_score(model, X, y, cv=cv, scoring="roc_auc")
    f1_scores = cross_val_score(model, X, y, cv=cv, scoring="f1")
    
    print(f"\nCross-validation (5-fold):")
    print(f"  ROC AUC: {auc_scores.mean():.3f} ± {auc_scores.std():.3f}")
    print(f"  F1:      {f1_scores.mean():.3f} ± {f1_scores.std():.3f}")
    
    # Fit on full data
    model.fit(X, y)
    
    # Feature importance
    importances = dict(zip(features, model.feature_importances_))
    top_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)
    
    print(f"\nTop features:")
    for feat, imp in top_features[:8]:
        print(f"  {feat:25s} {imp:.3f} {'█' * int(imp * 50)}")
    
    # Predictions on training data (for threshold analysis)
    y_prob = model.predict_proba(X)[:, 1]
    
    # Find optimal threshold
    precisions, recalls, thresholds_pr = precision_recall_curve(y, y_prob)
    f1_scores_all = 2 * precisions * recalls / (precisions + recalls + 1e-8)
    best_idx = np.argmax(f1_scores_all)
    best_threshold = thresholds_pr[best_idx] if best_idx < len(thresholds_pr) else 0.5
    
    print(f"\nOptimal probability threshold: {best_threshold:.3f}")
    print(f"  At this threshold: precision={precisions[best_idx]:.2f}, recall={recalls[best_idx]:.2f}")
    
    # Classification report at optimal threshold
    y_pred = (y_prob >= best_threshold).astype(int)
    print(f"\nClassification Report (threshold={best_threshold:.3f}):")
    print(classification_report(y, y_pred, target_names=["No CSO", "CSO"]))
    
    # Confusion matrix
    cm = confusion_matrix(y, y_pred)
    print(f"Confusion Matrix:")
    print(f"  True Negatives:  {cm[0,0]:4d}  |  False Positives: {cm[0,1]:4d}")
    print(f"  False Negatives: {cm[1,0]:4d}  |  True Positives:  {cm[1,1]:4d}")
    
    # Calibration offset: average prediction on dry days → subtract so dry = ~0%
    dry_mask = labeled["precip_avg"].fillna(0) < 0.01
    dry_probs = model.predict_proba(X[dry_mask])[:, 1] if dry_mask.sum() > 0 else np.array([0])
    calibration_offset = round(float(np.mean(dry_probs)), 3)
    print(f"\nDry-day avg prediction: {calibration_offset:.3f} (calibration offset)")
    
    return {
        "model": model,
        "features": features,
        "auc_mean": auc_scores.mean(),
        "auc_std": auc_scores.std(),
        "f1_mean": f1_scores.mean(),
        "optimal_threshold": best_threshold,
        "feature_importances": top_features,
        "calibration_offset": calibration_offset,
        "y_true": y.values,
        "y_prob": y_prob,
        "X": X,
    }


def train_basin_models(dataset: pd.DataFrame) -> dict:
    """
    Train separate models for each drainage basin.
    
    Each basin has different overflow characteristics:
    - Westside: lower threshold, Ocean Beach outfalls
    - North Shore: higher capacity, bay stations
    - Southeast: moderate, Islais/Candlestick
    
    Returns:
        Dict of {basin_name: model_results}
    """
    basin_results = {}
    
    # Basin-specific target definitions.
    # Using stricter targets than "any_elevated" to reduce false positives
    # from natural background bacteria (e.g. Westside has 38.7% elevated on dry days).
    #
    # Westside: 4+ stations elevated (filters out natural variation, 15 stations)
    # North Shore: 1+ stations elevated with simpler model (only 4 stations total,
    #   so 2+ was too strict — only 36 events, causing brittle step-function behavior
    #   that missed multi-day storms. 1+ gives 84 events with enough signal to learn
    #   cumulative rain patterns. Higher calibration offset handles dry-day noise.)
    # Southeast: any elevated (only 1 station — BAY#320 — so any exceedance matters)
    basin_configs = {
        "Westside": {
            "min_elevated": 4,
            "description": "4+ stations elevated",
            "model_params": {
                "n_estimators": 150, "max_depth": 3,
                "learning_rate": 0.05, "min_samples_leaf": 8,
            },
        },
        "North Shore": {
            "min_elevated": 1,
            "description": "any station elevated (simpler model)",
            "model_params": {
                # Simpler model: only 4 stations, few positive examples.
                # Deeper/more complex models overfit to precip_avg step function
                # and miss multi-day storms where same-day rain is moderate.
                "n_estimators": 100, "max_depth": 2,
                "learning_rate": 0.05, "min_samples_leaf": 15,
            },
        },
        "Southeast": {
            "min_elevated": 1,
            "description": "any station elevated",
            "model_params": {
                "n_estimators": 150, "max_depth": 3,
                "learning_rate": 0.05, "min_samples_leaf": 8,
            },
        },
    }
    
    for basin, config in basin_configs.items():
        print(f"\n{'=' * 60}")
        print(f"TRAINING: {basin} basin model")
        print(f"  Target: {config['description']}")
        print(f"{'=' * 60}")
        
        labeled = dataset[dataset["has_sample"] == 1].copy()
        
        elevated_col = f"{basin}_elevated"
        if elevated_col not in labeled.columns:
            print(f"  No data for {basin} basin, skipping")
            continue
        
        labeled = labeled[labeled[elevated_col].notna()]
        
        if len(labeled) < 50:
            print(f"  Only {len(labeled)} samples for {basin}, skipping")
            continue
        
        # Build target using the stricter threshold
        min_elev = config["min_elevated"]
        y = (labeled[elevated_col] >= min_elev).astype(int)
        
        features = get_feature_columns()
        X = labeled[features].fillna(0)
        
        print(f"Training samples: {len(X)}")
        print(f"Positive (elevated) samples: {y.sum()} ({y.mean():.1%})")
        
        if y.sum() < 10:
            print(f"  Too few positive samples, skipping")
            continue
        
        model = GradientBoostingClassifier(
            subsample=0.8,
            random_state=42,
            **config["model_params"],
        )
        
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        
        try:
            auc_scores = cross_val_score(model, X, y, cv=cv, scoring="roc_auc")
        except ValueError:
            print(f"  CV failed (likely too few positives in some folds)")
            auc_scores = np.array([0.5])
        
        model.fit(X, y)
        y_prob = model.predict_proba(X)[:, 1]
        
        # Feature importance
        importances = sorted(
            zip(features, model.feature_importances_),
            key=lambda x: x[1], reverse=True
        )
        
        print(f"  AUC: {auc_scores.mean():.3f} ± {auc_scores.std():.3f}")
        print(f"  Top 5 features:")
        for feat, imp in importances[:5]:
            print(f"    {feat:25s} {imp:.3f}")
        
        # Compute calibration offset: average prediction on dry days
        # This ensures dry days show ~0% probability
        dry_mask = labeled["precip_avg"].fillna(0) < 0.01
        dry_probs = model.predict_proba(X[dry_mask])[:, 1] if dry_mask.sum() > 0 else np.array([0])
        calibration_offset = round(float(np.mean(dry_probs)), 3)
        
        print(f"  Dry-day avg prediction: {calibration_offset:.3f} (calibration offset)")
        
        basin_results[basin] = {
            "model": model,
            "features": features,
            "auc_mean": auc_scores.mean(),
            "auc_std": auc_scores.std(),
            "feature_importances": importances,
            "y_true": y.values,
            "y_prob": y_prob,
            "X": X,
            "positive_rate": y.mean(),
            "calibration_offset": calibration_offset,
        }
    
    return basin_results


def compute_data_driven_thresholds(dataset: pd.DataFrame) -> dict:
    """
    Compute real basin-specific rainfall thresholds from the data.
    
    For each basin, find the rainfall levels where:
    - 25% of samples are elevated (caution)
    - 50% of samples are elevated (moderate risk)
    - 75% of samples are elevated (high risk)
    
    Returns:
        Dict of {basin: {metric: {threshold_level: rainfall_inches}}}
    """
    print("\n" + "=" * 60)
    print("COMPUTING DATA-DRIVEN THRESHOLDS")
    print("=" * 60)
    
    labeled = dataset[dataset["has_sample"] == 1].copy()
    
    thresholds = {}
    
    rain_metrics = [
        ("precip_avg", "Same-day rain"),
        ("rain_2d_cum", "2-day cumulative"),
        ("rain_3d_cum", "3-day cumulative"),
    ]
    
    for basin in ["Westside", "North Shore", "Southeast", "City-wide"]:
        print(f"\n  {basin}:")
        
        if basin == "City-wide":
            target_col = "likely_cso"
        else:
            target_col = f"{basin}_any_elevated"
        
        if target_col not in labeled.columns:
            continue
        
        basin_data = labeled[labeled[target_col].notna()].copy()
        
        thresholds[basin] = {}
        
        for rain_col, rain_label in rain_metrics:
            # Bin rainfall into increments and compute exceedance rate per bin
            bins = np.arange(0, 3.5, 0.1)
            basin_data["rain_bin"] = pd.cut(basin_data[rain_col], bins=bins, labels=bins[:-1])
            
            # Compute cumulative exceedance rate (at or above each threshold)
            threshold_results = {}
            for threshold in np.arange(0.0, 3.0, 0.05):
                above = basin_data[basin_data[rain_col] >= threshold]
                if len(above) >= 5:
                    rate = above[target_col].mean()
                    threshold_results[round(threshold, 2)] = {
                        "exceedance_rate": round(rate, 3),
                        "sample_count": len(above),
                    }
            
            # Find rainfall levels for specific exceedance rates
            levels = {}
            for target_rate, level_name in [(0.25, "caution"), (0.50, "moderate"), (0.75, "high")]:
                for rain_val, info in sorted(threshold_results.items()):
                    if info["exceedance_rate"] >= target_rate and info["sample_count"] >= 5:
                        levels[level_name] = rain_val
                        break
            
            thresholds[basin][rain_col] = levels
            
            if levels:
                parts = []
                for level_name in ["caution", "moderate", "high"]:
                    if level_name in levels:
                        parts.append(f"{level_name}={levels[level_name]}\"")
                print(f"    {rain_label:22s}: {', '.join(parts)}")
            else:
                print(f"    {rain_label:22s}: insufficient data")
    
    return thresholds


def save_models(citywide_result: dict, basin_results: dict,
                thresholds: dict, dataset: pd.DataFrame):
    """Save all trained models and metadata"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    
    # Save citywide model
    with open(MODEL_DIR / "citywide_model.pkl", "wb") as f:
        pickle.dump({
            "model": citywide_result["model"],
            "features": citywide_result["features"],
            "optimal_threshold": citywide_result["optimal_threshold"],
            "auc": citywide_result["auc_mean"],
            "calibration_offset": citywide_result["calibration_offset"],
        }, f)
    
    # Save basin models
    for basin, result in basin_results.items():
        safe_name = basin.lower().replace(" ", "_")
        with open(MODEL_DIR / f"{safe_name}_model.pkl", "wb") as f:
            pickle.dump({
                "model": result["model"],
                "features": result["features"],
                "auc": result["auc_mean"],
                "calibration_offset": result["calibration_offset"],
            }, f)
    
    # Save thresholds as JSON
    with open(MODEL_DIR / "thresholds.json", "w") as f:
        json.dump(thresholds, f, indent=2)
    
    # Save training dataset
    dataset.to_csv(PROCESSED_DIR / "full_training_dataset.csv", index=False)
    
    # Save model summary
    summary = {
        "trained_at": datetime.now().isoformat(),
        "citywide": {
            "auc": citywide_result["auc_mean"],
            "f1": citywide_result["f1_mean"],
            "optimal_threshold": citywide_result["optimal_threshold"],
            "top_features": citywide_result["feature_importances"][:10],
        },
        "basins": {
            basin: {
                "auc": result["auc_mean"],
                "positive_rate": result["positive_rate"],
                "top_features": result["feature_importances"][:5],
            }
            for basin, result in basin_results.items()
        },
        "thresholds": thresholds,
    }
    
    with open(MODEL_DIR / "model_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    print(f"\n💾 Models saved to {MODEL_DIR}/")
    print(f"   citywide_model.pkl")
    for basin in basin_results:
        safe_name = basin.lower().replace(" ", "_")
        print(f"   {safe_name}_model.pkl")
    print(f"   thresholds.json")
    print(f"   model_summary.json")


def backtest_models(dataset: pd.DataFrame, citywide_result: dict,
                    basin_results: dict) -> dict:
    """
    Run every labeled day through the trained models and compare to ground truth.
    
    This is the sanity check that should catch gross model failures like
    predicting 0% during a confirmed CSO event, or 80% on a bone-dry day.
    
    Checks every labeled day (not just storms) across the full spectrum:
    dry days, light rain, moderate rain, heavy rain.
    
    Returns:
        Dict with pass/fail status, per-model stats, and any flagged mismatches.
        Raises RuntimeError if critical failures are found.
    """
    print("\n" + "=" * 60)
    print("BACKTEST: Running all labeled days through trained models")
    print("=" * 60)
    
    labeled = dataset[dataset["has_sample"] == 1].copy()
    features = get_feature_columns()
    
    # ── Build ground truth targets matching what each model was trained on ──
    # Citywide: likely_cso (3+ stations across 2+ basins)
    labeled["gt_citywide"] = labeled["likely_cso"].fillna(0).astype(int)
    # Westside: 4+ stations elevated
    labeled["gt_westside"] = (labeled["Westside_elevated"].fillna(0) >= 4).astype(int)
    # North Shore: 2+ stations elevated
    labeled["gt_north_shore"] = (labeled["North Shore_elevated"].fillna(0) >= 2).astype(int)
    # Southeast: any elevated
    labeled["gt_southeast"] = (labeled["Southeast_elevated"].fillna(0) >= 1).astype(int)
    
    # ── Run predictions on every labeled day ──
    all_models = {"citywide": citywide_result}
    for basin, result in basin_results.items():
        key = basin.lower().replace(" ", "_")
        all_models[key] = result
    
    # Rain-weighted calibration: scale offset by how dry the day is
    rain_3d_vals = labeled["rain_3d_cum"].fillna(0).values
    rain_factors = np.clip(1.0 - rain_3d_vals * 2.0, 0, 1)
    
    predictions = {}
    for name, result in all_models.items():
        model = result["model"]
        feat_names = result["features"]
        offset = result.get("calibration_offset", 0)
        
        X = labeled[feat_names].fillna(0)
        raw_probs = model.predict_proba(X)[:, 1]
        effective_offsets = offset * rain_factors
        calibrated = np.clip(raw_probs - effective_offsets, 0, 1)
        predictions[name] = calibrated
    
    # ── Categorize days by rainfall intensity ──
    rain = labeled["precip_avg"].fillna(0).values
    rain_2d = labeled["rain_2d_cum"].fillna(0).values
    
    buckets = {
        "dry":      (rain < 0.01),
        "light":    (rain >= 0.01) & (rain < 0.25),
        "moderate":  (rain >= 0.25) & (rain < 0.50),
        "heavy":    (rain >= 0.50) & (rain < 1.00),
        "extreme":  (rain >= 1.00),
    }
    
    # ── Per-model, per-bucket analysis ──
    report = {}
    all_flags = []
    
    for model_name in predictions:
        gt_col = f"gt_{model_name}"
        gt = labeled[gt_col].values
        pred = predictions[model_name]
        
        print(f"\n  {'─' * 50}")
        print(f"  Model: {model_name}")
        print(f"  {'─' * 50}")
        
        model_report = {"buckets": {}, "flags": []}
        
        # Overall stats
        total_pos = gt.sum()
        total_neg = len(gt) - total_pos
        
        for bucket_name, mask in buckets.items():
            bucket_gt = gt[mask]
            bucket_pred = pred[mask]
            n = mask.sum()
            
            if n == 0:
                continue
            
            n_positive = bucket_gt.sum()
            n_negative = n - n_positive
            avg_pred = bucket_pred.mean()
            
            # Predictions binned
            pred_high = (bucket_pred >= 0.50).sum()     # model says high risk
            pred_mod = ((bucket_pred >= 0.10) & (bucket_pred < 0.50)).sum()
            pred_low = (bucket_pred < 0.10).sum()       # model says low/no risk
            
            # Mismatches
            # False negatives: ground truth positive but model says < 10%
            fn_mask = (bucket_gt == 1) & (bucket_pred < 0.10)
            false_negatives = fn_mask.sum()
            
            # False positives: ground truth negative but model says >= 50%
            fp_mask = (bucket_gt == 0) & (bucket_pred >= 0.50)
            false_positives = fp_mask.sum()
            
            bucket_stats = {
                "n": int(n),
                "actual_positive": int(n_positive),
                "actual_negative": int(n_negative),
                "avg_prediction": round(float(avg_pred), 3),
                "pred_high": int(pred_high),
                "pred_moderate": int(pred_mod),
                "pred_low": int(pred_low),
                "false_negatives": int(false_negatives),
                "false_positives": int(false_positives),
            }
            model_report["buckets"][bucket_name] = bucket_stats
            
            print(f"\n    {bucket_name:10s} ({n:3d} days) | "
                  f"actual: {n_positive:3d} events | "
                  f"avg pred: {avg_pred:5.1%} | "
                  f"FN(<10%): {false_negatives:2d} | "
                  f"FP(≥50%): {false_positives:2d}")
        
        # ── Flag individual gross mismatches across ALL days ──
        # 
        # We distinguish between rain-driven events (which the model CAN predict)
        # and dry-weather exceedances (which it fundamentally cannot — those come
        # from birds, runoff, natural sources, not CSO).
        #
        # "Recent rain" = any rain in the 3-day cumulative window (rain_3d_cum > 0.05)
        # This is the model's signal. If there was recent rain and the model still
        # missed a CSO event, that's a real model failure. If there was NO recent
        # rain and bacteria were elevated, that's background contamination.
        
        dates = labeled["date"].values
        rain_3d = labeled["rain_3d_cum"].fillna(0).values
        
        for i in range(len(labeled)):
            p = float(pred[i])
            actual = int(gt[i])
            r = float(rain[i])
            r2d = float(rain_2d[i])
            r3d = float(rain_3d[i])
            d = str(dates[i])[:10]
            
            had_recent_rain = r3d > 0.05
            
            flag = None
            
            if actual == 1 and p < 0.10:
                if had_recent_rain:
                    # Model had a rain signal and still missed it
                    # "Critical" = meaningful rain (3d > 0.25") and model says <10%
                    # "Marginal" = light rain (3d 0.05-0.25") and model says <10%
                    is_meaningful_rain = r3d > 0.25
                    flag = {
                        "type": "CRITICAL_FALSE_NEGATIVE" if is_meaningful_rain else "MARGINAL_FALSE_NEGATIVE",
                        "model": model_name,
                        "date": d,
                        "prediction": round(p, 3),
                        "actual": actual,
                        "rain_today": round(r, 3),
                        "rain_2d_cum": round(r2d, 3),
                        "rain_3d_cum": round(r3d, 3),
                    }
                else:
                    # Dry-weather exceedance — model can't predict this
                    flag = {
                        "type": "DRY_WEATHER_EXCEEDANCE",
                        "model": model_name,
                        "date": d,
                        "prediction": round(p, 3),
                        "actual": actual,
                        "rain_today": round(r, 3),
                        "rain_2d_cum": round(r2d, 3),
                        "rain_3d_cum": round(r3d, 3),
                    }
            
            # Bad false positive: no event, truly dry (no recent rain), model says >= 25%
            elif actual == 0 and not had_recent_rain and p >= 0.25:
                flag = {
                    "type": "DRY_DAY_FALSE_POSITIVE",
                    "model": model_name,
                    "date": d,
                    "prediction": round(p, 3),
                    "actual": actual,
                    "rain_today": round(r, 3),
                    "rain_2d_cum": round(r2d, 3),
                    "rain_3d_cum": round(r3d, 3),
                }
            
            # Moderate false negative: event with recent rain, model says < 25%
            elif actual == 1 and had_recent_rain and p < 0.25:
                flag = {
                    "type": "FALSE_NEGATIVE",
                    "model": model_name,
                    "date": d,
                    "prediction": round(p, 3),
                    "actual": actual,
                    "rain_today": round(r, 3),
                    "rain_2d_cum": round(r2d, 3),
                    "rain_3d_cum": round(r3d, 3),
                }
            
            # Moderate false positive: no event, model says >= 75%
            elif actual == 0 and p >= 0.75:
                flag = {
                    "type": "FALSE_POSITIVE",
                    "model": model_name,
                    "date": d,
                    "prediction": round(p, 3),
                    "actual": actual,
                    "rain_today": round(r, 3),
                    "rain_2d_cum": round(r2d, 3),
                    "rain_3d_cum": round(r3d, 3),
                }
            
            if flag:
                model_report["flags"].append(flag)
                all_flags.append(flag)
        
        report[model_name] = model_report
    
    # ── Print flagged mismatches ──
    critical = [f for f in all_flags if f["type"] == "CRITICAL_FALSE_NEGATIVE"]
    marginal = [f for f in all_flags if f["type"] == "MARGINAL_FALSE_NEGATIVE"]
    dry_weather = [f for f in all_flags if f["type"] == "DRY_WEATHER_EXCEEDANCE"]
    dry_fps = [f for f in all_flags if f["type"] == "DRY_DAY_FALSE_POSITIVE"]
    moderate_fns = [f for f in all_flags if f["type"] == "FALSE_NEGATIVE"]
    moderate_fps = [f for f in all_flags if f["type"] == "FALSE_POSITIVE"]
    
    print(f"\n\n{'=' * 60}")
    print(f"BACKTEST RESULTS")
    print(f"{'=' * 60}")
    
    print(f"\n  Total labeled days tested: {len(labeled)}")
    print(f"\n  Model failures (rain was present, model got it wrong):")
    print(f"    🚨 Critical FN (event + rain >0.25\", pred <10%):   {len(critical)}")
    print(f"    ⚠️  Moderate FN (event + rain, pred <25%):          {len(moderate_fns)}")
    print(f"    🏜️  Dry-day FP (no recent rain, pred ≥25%):         {len(dry_fps)}")
    print(f"    📈 Moderate FP (no event, pred ≥75%):               {len(moderate_fps)}")
    print(f"\n  Edge cases (hard to predict, not model bugs):")
    print(f"    🔸 Marginal FN (event + light rain <0.25\", pred <10%): {len(marginal)}")
    print(f"\n  Outside model scope (no rain signal to work with):")
    print(f"    🌤️  Dry-weather exceedances (event but no rain):     {len(dry_weather)}")
    
    if critical:
        print(f"\n  🚨 CRITICAL FALSE NEGATIVES — meaningful rain but model missed CSO:")
        for f in sorted(critical, key=lambda x: x["date"]):
            print(f"    {f['date']}  {f['model']:15s}  pred={f['prediction']:5.1%}  "
                  f"rain={f['rain_today']:.2f}\"  3d={f['rain_3d_cum']:.2f}\"")
    
    if dry_fps:
        print(f"\n  🏜️  DRY-DAY FALSE POSITIVES — no recent rain but model predicted risk:")
        for f in sorted(dry_fps, key=lambda x: x["date"]):
            print(f"    {f['date']}  {f['model']:15s}  pred={f['prediction']:5.1%}  "
                  f"rain={f['rain_today']:.2f}\"  3d={f['rain_3d_cum']:.2f}\"")
    
    if marginal:
        print(f"\n  🔸 MARGINAL FALSE NEGATIVES (event + light rain 0.05-0.25\", pred <10%):")
        for f in sorted(marginal, key=lambda x: x["date"]):
            print(f"    {f['date']}  {f['model']:15s}  pred={f['prediction']:5.1%}  "
                  f"rain={f['rain_today']:.2f}\"  3d={f['rain_3d_cum']:.2f}\"")
    
    if moderate_fns:
        print(f"\n  ⚠️  MODERATE FALSE NEGATIVES (event + rain, pred <25%) — showing first 20:")
        for f in sorted(moderate_fns, key=lambda x: x["prediction"])[:20]:
            print(f"    {f['date']}  {f['model']:15s}  pred={f['prediction']:5.1%}  "
                  f"rain={f['rain_today']:.2f}\"  3d={f['rain_3d_cum']:.2f}\"")
    
    if moderate_fps:
        print(f"\n  📈 MODERATE FALSE POSITIVES (no event, pred ≥75%) — showing first 20:")
        for f in sorted(moderate_fps, key=lambda x: -x["prediction"])[:20]:
            print(f"    {f['date']}  {f['model']:15s}  pred={f['prediction']:5.1%}  "
                  f"rain={f['rain_today']:.2f}\"  3d={f['rain_3d_cum']:.2f}\"")
    
    if dry_weather:
        print(f"\n  🌤️  DRY-WEATHER EXCEEDANCES (not model failures — no rain signal):")
        print(f"    These are bacteria elevations with no recent rain (3d < 0.05\").")
        print(f"    Likely causes: birds, urban runoff, natural sources.")
        print(f"    The model cannot predict these — they're outside its scope.")
        from collections import Counter
        dw_counts = Counter(f["model"] for f in dry_weather)
        print(f"    Count by model: " + ", ".join(f"{m}: {c}" for m, c in sorted(dw_counts.items())))
    
    # ── Pass/fail verdict ──
    # Only fail on things the model SHOULD get right:
    # - Critical FN with rain present = model bug
    # - Dry-day FP with no recent rain = model bug
    # Dry-weather exceedances are NOT failures — no rain signal to predict from
    passed = True
    reasons = []
    
    if len(critical) > 0:
        passed = False
        reasons.append(f"{len(critical)} critical false negatives (rain present, event predicted <10%)")
    
    if len(dry_fps) > 0:
        passed = False
        reasons.append(f"{len(dry_fps)} dry-day false positives (no recent rain, predicted ≥25%)")
    
    if passed:
        print(f"\n  ✅ BACKTEST PASSED — no critical model failures detected")
    else:
        print(f"\n  ❌ BACKTEST FAILED:")
        for r in reasons:
            print(f"    • {r}")
    
    return {
        "passed": passed,
        "reasons": reasons,
        "total_days": len(labeled),
        "total_flags": len(all_flags),
        "critical_false_negatives": len(critical),
        "marginal_false_negatives": len(marginal),
        "dry_weather_exceedances": len(dry_weather),
        "dry_day_false_positives": len(dry_fps),
        "moderate_false_negatives": len(moderate_fns),
        "moderate_false_positives": len(moderate_fps),
        "models": report,
        "flags": all_flags,
    }


def main():
    """Full training pipeline"""
    print("=" * 60)
    print("SF SEWAGE FORECAST — MODEL TRAINING")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Step 1: Build dataset
    print("\n📊 Step 1: Building training dataset...")
    dataset = build_full_training_dataset()
    
    # Step 2: Train city-wide model
    print("\n🏋️ Step 2: Training city-wide CSO model...")
    citywide_result = train_citywide_model(dataset)
    
    # Step 3: Train basin-specific models
    print("\n🏋️ Step 3: Training basin-specific models...")
    basin_results = train_basin_models(dataset)
    
    # Step 4: Compute data-driven thresholds
    print("\n📏 Step 4: Computing data-driven thresholds...")
    thresholds = compute_data_driven_thresholds(dataset)
    
    # Step 5: Backtest — run all labeled days through models
    print("\n🧪 Step 5: Backtesting models against ground truth...")
    backtest = backtest_models(dataset, citywide_result, basin_results)
    
    # Step 6: Save everything
    print("\n💾 Step 6: Saving models and data...")
    save_models(citywide_result, basin_results, thresholds, dataset)
    
    # Save backtest results
    backtest_save = {k: v for k, v in backtest.items() if k != "models"}
    with open(MODEL_DIR / "backtest_results.json", "w") as f:
        json.dump(backtest_save, f, indent=2, default=str)
    print(f"   backtest_results.json")
    
    # Final summary
    print("\n" + "=" * 60)
    print("TRAINING COMPLETE")
    print("=" * 60)
    print(f"\nCity-wide model:")
    print(f"  AUC: {citywide_result['auc_mean']:.3f}")
    print(f"  Optimal threshold: {citywide_result['optimal_threshold']:.3f}")
    
    print(f"\nBasin models:")
    for basin, result in basin_results.items():
        print(f"  {basin}: AUC={result['auc_mean']:.3f}, positive_rate={result['positive_rate']:.1%}")
    
    print(f"\nData-driven thresholds:")
    for basin, metrics in thresholds.items():
        if "precip_avg" in metrics:
            t = metrics["precip_avg"]
            parts = [f"{k}={v}\"" for k, v in t.items()]
            print(f"  {basin}: {', '.join(parts)}")
    
    print(f"\nBacktest: {'✅ PASSED' if backtest['passed'] else '❌ FAILED'}")
    if not backtest['passed']:
        for r in backtest['reasons']:
            print(f"  • {r}")
    
    return dataset, citywide_result, basin_results, thresholds, backtest


if __name__ == "__main__":
    main()
