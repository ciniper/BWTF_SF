"""Shared AQPI CSV reduction: observed amounts and forecasts remain separate."""
import csv
import hashlib
import io
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    """Reduce a local CSV using the same parser as the live Admin feed."""
    raw = Path(path).read_bytes()
    return parse_feed(raw, key)


def parse_feed(raw, key, *, expected_intervals=None):
    label, prefix, step, url = SOURCES[key]
    with io.StringIO(raw.decode('utf-8-sig'), newline='') as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {'gridpoint', 'run_time'} <= set(fields):
            raise ValueError('Missing or duplicate CSV headers')
        observed = sorted(c for c in reader.fieldnames if c.startswith(prefix))
        forecast = sorted(c for c in reader.fieldnames if c.startswith('qpf01_'))
        if not observed:
            raise ValueError(f'No {prefix} observations')
        if expected_intervals is not None and len(observed) != expected_intervals:
            raise ValueError('Unexpected observation window length')
        times = [datetime.fromisoformat(iso(c)) for c in observed]
        if any(b-a != timedelta(minutes=step) for a,b in zip(times,times[1:])):
            raise ValueError('Observation timestamps are not contiguous')
        forecast_times = [datetime.fromisoformat(iso(c)) for c in forecast]
        if forecast_times and (forecast_times[0] != times[-1] + timedelta(hours=1) or
                               any(b-a != timedelta(hours=1) for a,b in zip(forecast_times, forecast_times[1:]))):
            raise ValueError('Forecast timestamps do not follow observations hourly')
        points = {}
        run_times = set()
        raw_rows = 0
        for row in reader:
            raw_rows += 1
            if None in row or any(v is None for v in row.values()):
                raise ValueError('Truncated or malformed CSV row')
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
    # The guide defines run_time as extraction time, not the observation end.
    # Validate its format, but use QPE column timestamps for the actual window.
    datetime.strptime(next(iter(run_times)), '%Y-%m-%d %H:%M')
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
            'sha256': hashlib.sha256(raw).hexdigest(),
            'start': (times[0] - timedelta(minutes=step)).isoformat(),
            'end': iso(observed[-1]), 'times': [iso(c) for c in observed],
            'forecast_times': [iso(c) for c in forecast], 'regions': regions,
            'points': [[round(x,6),round(y,6),complete_sum(v[:n])] for (x,y),v in sorted(points.items())]}
