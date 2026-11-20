"""Build the Admin rain explorer from downloaded AQPI CSVs, without network access.

python scripts/build_rain_snapshot.py --surfrider FILE --wastewater FILE \
    --manifest FILE --checked 2026-11-20 --output features/alerts/data/rain_snapshot.json
The source files remain outside the repository. Rebuild deliberately; this is
a dated research snapshot, not a live collector or a training-data update.
"""
from __future__ import annotations

import argparse
import calendar
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re

REGIONS = {
    'city': {'label': 'SF city area', 'box': [-122.512, -122.380, 37.708, 37.806]},
    'westside': {'label': 'Westside', 'box': [-122.512, -122.455, 37.708, 37.790]},
    'north_shore': {'label': 'North Shore', 'box': [-122.455, -122.385, 37.785, 37.806]},
    'central': {'label': 'Central', 'box': [-122.455, -122.385, 37.745, 37.785]},
    'southeast': {'label': 'Southeast', 'box': [-122.455, -122.380, 37.708, 37.745]},
}
BASE = 'https://cw3e-datashare.ucsd.edu/AQPI/'
SOURCES = {
    'surfrider': ('Surfrider · 24 hours', 'qpe15min_', 15,
                  BASE + 'qpe15min-qpf01/SURFRIDER_FOUNDATION_qpe15min-qpf01.csv'),
    'wastewater': ('SFPUC area · 7 days', 'qpe_', 60,
                   BASE + 'qpe-qpf01/SFPUC_WASTEWATER_ENTERPRISE_qpe-qpf01.csv'),
}


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError):
        return None


def complete_sum(values):
    return round(sum(values), 5) if values and all(v is not None for v in values) else None


def iso(column):
    return datetime.strptime(column.rsplit('_', 1)[1], '%Y%m%d%H%M').replace(tzinfo=timezone.utc).isoformat()


def build_feed(path, key):
    label, prefix, step, url = SOURCES[key]
    with Path(path).open() as f:
        reader = csv.DictReader(f)
        observed = sorted(c for c in reader.fieldnames if c.startswith(prefix))
        forecast = sorted(c for c in reader.fieldnames if c.startswith('qpf01_'))
        if not observed:
            raise ValueError(f'No {prefix} observations in {path}')
        times = [datetime.fromisoformat(iso(c)) for c in observed]
        if any(b-a != timedelta(minutes=step) for a,b in zip(times,times[1:])):
            raise ValueError('Observation timestamps are not contiguous')
        points = {}
        run_times = set()
        raw_rows = 0
        for row in reader:
            raw_rows += 1
            run_times.add(row['run_time'])
            match = re.fullmatch(r'POINT \(([-\d.]+) ([-\d.]+)\)', row['gridpoint'])
            if not match:
                raise ValueError('Unrecognized gridpoint')
            lon, lat = map(float, match.groups())
            if not (-122.53 <= lon <= -122.34 and 37.69 <= lat <= 37.83):
                continue
            values = [number(row[c]) for c in observed + forecast]
            xy = (lon, lat)
            if xy in points and points[xy] != values:
                raise ValueError('Overlapping watershed rows disagree')
            points[xy] = values
    if len(run_times) != 1 or not points:
        raise ValueError('Expected one run time and SF grid points')
    n = len(observed)
    regions = {}
    for region, spec in REGIONS.items():
        x0,x1,y0,y1 = spec['box']
        selected = [v for (x,y),v in points.items() if x0 <= x < x1 and y0 <= y < y1]
        if not selected:
            raise ValueError(f'No grid points in {region}')
        # A missing cell makes that region/interval unavailable. No zero fill or
        # changing geographic denominator as storms pass over missing cells.
        means = [round(sum(v)/len(v), 6) if all(x is not None for x in v) else None
                 for v in zip(*selected)]
        regions[region] = {'count': len(selected), 'observed': means[:n], 'forecast': means[n:],
                           'total': complete_sum(means[:n]),
                           'complete': sum(v is not None for v in means[:n])}
    return {'label': label, 'url': url, 'step_minutes': step,
            'run_time': next(iter(run_times)) + ' UTC', 'raw_rows': raw_rows,
            'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'start': (times[0] - timedelta(minutes=step)).isoformat(),
            'end': iso(observed[-1]), 'times': [iso(c) for c in observed],
            'forecast_times': [iso(c) for c in forecast], 'regions': regions,
            'points': [[round(x,6),round(y,6),complete_sum(v[:n])] for (x,y),v in sorted(points.items())]}


def archive_months(manifest):
    # Filenames are stamped at midnight AFTER the UTC day represented.
    names = re.findall(r'SURFRIDER_FOUNDATION_qpe15min_(\d{12})\.csv', Path(manifest).read_text())
    days = {(datetime.strptime(n, '%Y%m%d%H%M') - timedelta(days=1)).date() for n in names}
    if not days:
        raise ValueError('No archive files in manifest')
    first,last = min(days),max(days)
    months = []
    year,month = first.year,first.month
    while (year,month) <= (last.year,last.month):
        dates = [datetime(year,month,d).date() for d in range(1,calendar.monthrange(year,month)[1]+1)]
        months.append({'label': datetime(year,month,1).strftime('%b %Y'),
                       'days': [{'date': d.isoformat(), 'present': d in days} for d in dates],
                       'count': sum(d in days for d in dates)})
        year,month = (year+1,1) if month == 12 else (year,month+1)
    return {'count': len(days), 'start': first.isoformat(), 'end': last.isoformat(), 'months': months}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('surfrider','wastewater','manifest','output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--checked', required=True, type=date.fromisoformat,
                        help='Date the source files were verified, YYYY-MM-DD')
    args = parser.parse_args()
    result = {'checked': args.checked.isoformat(), 'snapshot_label': args.checked.strftime('%b %d, %Y'),
              'regions': REGIONS, 'feeds': {key: build_feed(getattr(args,key),key) for key in SOURCES},
              'archive': archive_months(args.manifest)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,separators=(',',':'),allow_nan=False)+'\n')
    print(f'Wrote {args.output}: {args.output.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
