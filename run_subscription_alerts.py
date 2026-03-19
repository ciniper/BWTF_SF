#!/usr/bin/env python3
"""Dispatch targeted CSO subscription alerts for current and simulated events."""

import json
from datetime import datetime

from core.cso_alerts import (
    SimulatedCSOStore,
    apply_simulated_cso,
    dispatch_subscription_alerts,
)
from core.sfpuc_api import SFPUCRealTimeAPI
from core.subscriptions import SubscriptionStore


def main() -> int:
    api = SFPUCRealTimeAPI()
    subscription_store = SubscriptionStore()
    simulated_store = SimulatedCSOStore()

    stations = api.fetch_stations()
    simulated_station_ids = simulated_store.get_station_ids()
    stations = apply_simulated_cso(stations, simulated_station_ids)
    subscriptions = subscription_store.list_subscriptions()
    results = dispatch_subscription_alerts(subscriptions, stations, simulated_station_ids)

    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "subscription_count": len(subscriptions),
        "dispatch_count": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
