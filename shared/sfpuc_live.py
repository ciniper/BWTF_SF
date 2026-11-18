"""Serve the last validated getCSV snapshot; never call SFPUC per visitor.

DataSF wins per station/collection day/analyte. Early rows are usable only while
collection is enabled and its last good read is within six minutes. Historical
observations remain available for timing even when the collector is unavailable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import threading
import time

from shared import supabase as sb
from shared.standards import parse_result
from shared.stations import STATIONS

MAX_AGE = timedelta(minutes=6)
_CACHE = None
_CACHE_UNTIL = 0.0
_LOCK = threading.Lock()
CODES = ('ENTERO', 'COLI_FECAL', 'COLI_TOTAL', 'COLI_E')


def _ts(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def snapshot_records(runtime, now=None):
    """(DataSF-shaped rows, state) from one runtime row; pure for tests."""
    now = now or datetime.now(timezone.utc)
    if not runtime:
        return [], 'unavailable'
    if not runtime.get('enabled'):
        return [], 'paused'
    try:
        seen = _ts(runtime['last_success_at'])
        if not timedelta(0) <= now - seen <= MAX_AGE:
            return [], 'stale'
        values = runtime.get('previous_values')
        if not isinstance(values, dict):
            return [], 'unavailable'
        out = []
        for key, raw in values.items():
            source, day, code = json.loads(key)
            if source not in STATIONS or code not in CODES or raw is None or parse_result(str(raw)) is None:
                continue
            datetime.strptime(day, '%Y-%m-%d')
            out.append({'source': source, 'sample_date': day, 'analyte': code,
                        'data': str(raw), 'early': True, 'checked_at': seen.isoformat()})
        return out, 'current'
    except (ValueError, TypeError, KeyError):
        return [], 'unavailable'


def read_snapshot(now=None):
    """One cached server-side database read shared by Today and sample popovers."""
    global _CACHE, _CACHE_UNTIL
    if not sb.is_configured():
        return [], 'unavailable'
    with _LOCK:
        if time.monotonic() >= _CACHE_UNTIL:
            try:
                rows = sb.select('sfpuc_getcsv_runtime', {'select': 'enabled,last_success_at,previous_values', 'id': 'eq.1'})
                _CACHE = rows[0] if rows else None
            except Exception:
                _CACHE = None
            _CACHE_UNTIL = time.monotonic() + 30
        runtime = _CACHE
    return snapshot_records(runtime, now)


def merge_records(datasf, early):
    """Retain all DataSF values (including double samples); fill missing keys only."""
    def key(r):
        return r.get('source'), str(r.get('sample_date') or '')[:10], r.get('analyte')
    good = [r for r in datasf if parse_result(str(r.get('data') if r.get('data') is not None else '')) is not None]
    present = {key(r) for r in good}
    return good + [r for r in early if key(r) not in present]


def latest_dates(records):
    out = {}
    for row in records:
        source, day = row['source'], row['sample_date'][:10]
        out[source] = max(day, out.get(source, day))
    return out


def fetch_observations(since_day, station=None, day=None):
    """Paginated, deterministic observation history for the timeline/popover."""
    if not sb.is_configured():
        return []
    params = {'select': 'source_code,sample_date,analyte,observed_at,value_raw,observation_kind',
              'sample_date': 'eq.' + day if day else 'gte.' + since_day,
              'order': 'source_code,sample_date,analyte,observed_at', 'limit': '500'}
    if station:
        params['source_code'] = 'eq.' + station
    out = []
    for offset in range(0, 20000, 500):
        rows = sb.select('sfpuc_getcsv_observations', {**params, 'offset': str(offset)}) or []
        out.extend(rows)
        if len(rows) < 500:
            return out
    raise RuntimeError('getCSV observation history exceeds the display limit')


def first_observations(rows):
    """First nonblank observation per indicator; baselines are upper bounds."""
    out = {}
    for r in rows:
        if r.get('value_raw') is None:
            continue
        key = r['source_code'], r['sample_date'], r['analyte']
        if key not in out or _ts(r['observed_at']) < _ts(out[key]['at']):
            out[key] = {'at': r['observed_at'], 'censored': r['observation_kind'] == 'baseline'}
    return out
