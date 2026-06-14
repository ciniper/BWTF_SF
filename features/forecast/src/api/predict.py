#!/usr/bin/env python3
"""
CSO Prediction CLI & API

Main entry point for generating CSO overflow predictions.

Usage:
    # Full forecast (fetches weather data, runs model)
    python -m src.api.predict

    # Quick check with manual rainfall values
    python -m src.api.predict --manual --rain-24h 1.2 --rain-3h 0.4

    # JSON output (for integration with BWTF alert system)
    python -m src.api.predict --json

    # Specific basin
    python -m src.api.predict --basin Westside
"""

import argparse
import json
import sys
from datetime import datetime

from src.models.threshold_model import RuleBasedModel
from src.models.forecast import CSOForecastEngine


def main():
    parser = argparse.ArgumentParser(
        description="SF CSO Overflow Prediction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--manual", action="store_true",
        help="Manual mode: provide rainfall values directly instead of fetching forecasts"
    )
    parser.add_argument(
        "--rain-24h", type=float, default=0,
        help="24-hour cumulative rainfall in inches (manual mode)"
    )
    parser.add_argument(
        "--rain-3h", type=float, default=0,
        help="3-hour cumulative rainfall in inches (manual mode)"
    )
    parser.add_argument(
        "--moisture", type=float, default=0,
        help="Antecedent moisture index 0-1 (manual mode)"
    )
    parser.add_argument(
        "--wind-dir", type=float, default=None,
        help="Wind direction in degrees (270=west, 180=south)"
    )
    parser.add_argument(
        "--basin", type=str, default=None,
        help="Specific basin to predict (Westside, North Shore, Southeast)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON"
    )
    parser.add_argument(
        "--models", type=str, nargs="+", default=["ecmwf", "gfs"],
        help="Weather models to use (default: ecmwf gfs)"
    )
    
    args = parser.parse_args()
    
    print(f"\n{'='*60}")
    print(f"SF CSO Overflow Prediction")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    if args.manual:
        # ── Manual mode: use provided values ──
        print(f"Manual mode: 24h={args.rain_24h}\", 3h={args.rain_3h}\", "
              f"moisture={args.moisture}, wind={args.wind_dir}")
        
        model = RuleBasedModel()
        
        if args.basin:
            predictions = [model.predict_basin(
                args.basin, args.rain_24h, args.rain_3h,
                args.moisture, args.wind_dir
            )]
        else:
            predictions = model.predict_all_basins(
                args.rain_24h, args.rain_3h,
                args.moisture, args.wind_dir
            )
        
        if args.json:
            output = {
                "mode": "manual",
                "input": {
                    "rain_24h_inches": args.rain_24h,
                    "rain_3h_inches": args.rain_3h,
                    "antecedent_moisture": args.moisture,
                    "wind_dir_deg": args.wind_dir,
                },
                "predictions": [
                    {
                        "basin": p.basin,
                        "probability": p.probability,
                        "risk_level": p.risk_level,
                        "confidence": p.confidence,
                        "primary_driver": p.primary_driver,
                        "details": p.details,
                    }
                    for p in predictions
                ]
            }
            print(json.dumps(output, indent=2))
        else:
            report = model.format_prediction_report(predictions)
            print(report)
    
    else:
        # ── Forecast mode: fetch weather data and predict ──
        print(f"Fetching forecasts from: {', '.join(args.models)}")
        
        engine = CSOForecastEngine()
        
        try:
            forecast = engine.generate_forecast(models=args.models)
        except Exception as e:
            print(f"Error generating forecast: {e}")
            sys.exit(1)
        
        if args.json:
            print(engine.to_json(forecast))
        else:
            report = engine.format_forecast(forecast)
            print(report)
        
        # Return exit code based on risk level
        if forecast.overall_risk in ("high", "very_high"):
            sys.exit(2)
        elif forecast.overall_risk == "moderate":
            sys.exit(1)
        else:
            sys.exit(0)


if __name__ == "__main__":
    main()
