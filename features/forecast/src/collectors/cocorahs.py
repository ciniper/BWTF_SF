#!/usr/bin/env python3
"""CoCoRaHS volunteer rain gauges inside San Francisco, daily, via ACIS.

The forecast's two NOAA gauges (Downtown 047772, Oceanside 047767) leave the
whole east side unmeasured. ACIS also carries the CoCoRaHS network; three of
its gauges sit inside the city, and one of them — Potrero / Dogpatch, between
Mission Creek and Islais Creek — has reported daily since 1998, so it covers
the entire training window. Pulled here into data/raw/historical_rain_cocorahs.csv
for the model leaderboard (src/models/leaderboard.py) to try as rain sources.

    venv/bin/python features/forecast/src/collectors/cocorahs.py

Caveat baked into the column names: a CoCoRaHS observer reads the gauge each
morning (~07:00), so the value filed for date D is the rain from ~07:00 on D−1
to ~07:00 on D. An evening storm on D lands on D+1. The leaderboard tries the
series both as filed and shifted back a day and lets the holdout choose.
Missing ('M') and suppressed ('S') days are NaN, trace ('T') is 0.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ACIS = "https://data.rcc-acis.org/StnData"
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
OUT = RAW_DIR / "historical_rain_cocorahs.csv"
STATIONS = {   # ACIS sid → short label (ACIS names them by distance/bearing from the city centroid)
    "US1CASF0017": "SF Potrero (1.6 SE)",
    "US1CASF0021": "SF Bayview (3.4 S)",
    "US1CASF0020": "SF Inner Sunset (3.1 W)",
}
START = "2016-01-01"


def fetch(sid: str) -> pd.DataFrame:
    r = requests.post(ACIS, json={"sid": sid, "sdate": START, "edate": str(date.today()),
                                  "elems": [{"name": "pcpn", "interval": "dly"}],
                                  "meta": ["name", "ll", "valid_daterange"]}, timeout=120)
    r.raise_for_status()
    j = r.json()
    meta = j.get("meta", {})
    rows = []
    for d, v in j.get("data", []):
        v = str(v).strip()
        if v in ("M", "S", ""):
            val = None
        elif v == "T":
            val = 0.0
        else:
            try:
                val = float(v.rstrip("A"))   # 'A' = accumulated multi-day total; kept as the day's value
            except ValueError:
                val = None
        rows.append({"date": d, "station_id": sid, "station_name": STATIONS[sid], "acis_name": meta.get("name"),
                     "lat": (meta.get("ll") or [None, None])[1], "lon": (meta.get("ll") or [None, None])[0],
                     "precip_inches": val, "obs_day_ends": "07:00 local (CoCoRaHS)"})
    df = pd.DataFrame(rows)
    print(f"  {sid} {STATIONS[sid]:26} {meta.get('name')!s:28} {len(df)} days, "
          f"{df['precip_inches'].notna().sum()} reported, first {df['date'].min()} last {df['date'].max()}, "
          f"valid {meta.get('valid_daterange')}")
    return df


def main() -> None:
    frames = [fetch(sid) for sid in STATIONS]
    out = pd.concat(frames).sort_values(["station_id", "date"])
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(RAW_DIR.parents[2])}: {len(out)} rows")


if __name__ == "__main__":
    sys.exit(main())
