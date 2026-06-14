#!/usr/bin/env python3
"""
30-day geometric mean report for SF beach water quality.

This script is intentionally separate from the real-time alert pipeline.
Use it to review longer-term trends without surfacing them as active alerts.
"""

import argparse
import json
from datetime import datetime

from features.alerts.monitoring import (
    BWTF_PRIORITY_SITES,
    GEOMETRIC_MEAN_WINDOW_DAYS,
    SFWaterQualityMonitor,
    STANDARDS,
    STATION_NAMES,
)


def build_report(priority_only: bool = False) -> list[dict]:
    monitor = SFWaterQualityMonitor()
    stations = BWTF_PRIORITY_SITES if priority_only else None
    samples = monitor.fetch_recent_samples(days=GEOMETRIC_MEAN_WINDOW_DAYS, stations=stations)

    results = []
    checked_pairs = set()
    for sample in samples:
        pair = (sample.station_id, sample.analyte)
        if pair in checked_pairs:
            continue
        checked_pairs.add(pair)

        result = monitor.calculate_geometric_mean(samples, sample.station_id, sample.analyte)
        if not result:
            continue

        results.append({
            "station_id": sample.station_id,
            "station_name": STATION_NAMES.get(sample.station_id, sample.station_id),
            "analyte": sample.analyte,
            "analyte_name": STANDARDS.get(sample.analyte, {}).get("description", sample.analyte),
            "geometric_mean": result["geometric_mean"],
            "standard": result["standard"],
            "sample_count": result["sample_count"],
            "window_days": result["window_days"],
            "exceeds": result["exceeds"],
        })

    return sorted(
        results,
        key=lambda item: (
            not item["exceeds"],
            item["station_name"],
            item["analyte"],
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a separate 30-day geometric mean report."
    )
    parser.add_argument(
        "--priority-only",
        action="store_true",
        help="Only include BWTF priority monitoring sites",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output report as JSON",
    )
    args = parser.parse_args()

    results = build_report(priority_only=args.priority_only)

    if args.json:
        print(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "window_days": GEOMETRIC_MEAN_WINDOW_DAYS,
            "results": results,
        }, indent=2))
        return 0

    print("=" * 60)
    print("SF BEACH WATER QUALITY - 30-DAY GEOMETRIC MEAN REPORT")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

    if not results:
        print("No stations had enough recent samples to calculate a geometric mean.")
        return 0

    exceedances = [item for item in results if item["exceeds"]]
    if exceedances:
        print("EXCEEDANCES")
        print("-" * 40)
        for item in exceedances:
            print(
                f"{item['station_name']}: {item['analyte_name']} "
                f"{item['geometric_mean']:.0f} (limit {item['standard']:.0f}, "
                f"{item['sample_count']} samples)"
            )
        print()

    print("ALL CALCULATED GEOMETRIC MEANS")
    print("-" * 40)
    for item in results:
        status = "EXCEEDS" if item["exceeds"] else "OK"
        print(
            f"[{status}] {item['station_name']}: {item['analyte_name']} "
            f"{item['geometric_mean']:.0f} / {item['standard']:.0f} "
            f"({item['sample_count']} samples)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
