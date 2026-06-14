#!/usr/bin/env python3
"""Dispatch targeted CSO subscription alerts for current and simulated events."""

import argparse
import json
from datetime import datetime

from features.alerts.cso_alerts import (
    SimulatedCSOStore,
    apply_simulated_cso,
    dispatch_subscription_alerts,
)
from shared.sfpuc_api import SFPUCRealTimeAPI
from features.alerts.subscriptions import SubscriptionStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch targeted CSO subscription alerts.")
    parser.add_argument(
        "--channel",
        choices=("email", "sms"),
        default="email",
        help="Delivery channel to use for matching subscriptions.",
    )
    args = parser.parse_args()

    api = SFPUCRealTimeAPI()
    subscription_store = SubscriptionStore()
    simulated_store = SimulatedCSOStore()

    stations = api.fetch_stations()
    simulated_station_ids = simulated_store.get_station_ids()
    stations = apply_simulated_cso(stations, simulated_station_ids)
    subscriptions = subscription_store.list_subscriptions()
    results = dispatch_subscription_alerts(
        subscriptions,
        stations,
        simulated_station_ids,
        channel=args.channel,
    )

    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "channel": args.channel,
        "subscription_count": len(subscriptions),
        "dispatch_count": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
