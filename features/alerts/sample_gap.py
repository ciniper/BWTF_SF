"""Read-only Admin view of getCSV observations, map dates, and DataSF pickup.

Arrival times are observations, not laboratory publication timestamps. Baselines
are shown as 'by'; DataSF's insert-only mirror cannot prove the current value.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

from flask import render_template
from shared import supabase as sb
from shared.stations import STATIONS

PACIFIC = ZoneInfo('America/Los_Angeles')
INDICATORS = [('ENTERO', 'Enterococcus'), ('COLI_FECAL', 'Fecal coliform'), ('COLI_TOTAL', 'Total coliform')]
PAGE_SIZE, MAX_ROWS = 500, 10000
LOG = logging.getLogger(__name__)


def timestamp(value):
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_time(value):
    dt = timestamp(value) if not isinstance(value, datetime) else value
    return dt.astimezone(PACIFIC).strftime('%b %-d, %-I:%M %p') if dt else 'Not observed'


def settings(query, today):
    def first(key, default=''):
        val = (query or {}).get(key, default)
        return val[0] if isinstance(val, list) and val else val
    def day(key, fallback):
        try:
            return date.fromisoformat(str(first(key)))
        except ValueError:
            return fallback
    end = max(date(1900, 1, 1), min(day('to', today), today))
    start = min(day('from', end - timedelta(days=6)), end)
    start = max(start, end - timedelta(days=30))
    source = first('station')
    return {'from': start, 'to': end, 'station': source if source in STATIONS else ''}


def _read_all(table, params):
    out = []
    for offset in range(0, MAX_ROWS, PAGE_SIZE):
        batch = sb.select(table, {**params, 'offset': str(offset), 'limit': str(PAGE_SIZE)}) or []
        out.extend(batch)
        if len(batch) < PAGE_SIZE:
            return out, False
    return out, True


def load_data(s):
    data = {'observations': [], 'map_dates': [], 'datasf': [], 'runtime': [], 'notes': []}
    if not sb.is_configured():
        data['notes'].append('Supabase is not configured in this environment. No live observations are available.')
        return data
    date_filter = f"(sample_date.gte.{s['from']},sample_date.lte.{s['to']})"
    jobs = {
        'observations': ('sfpuc_getcsv_observations', {'select': '*', 'and': date_filter,
            'order': 'source_code,sample_date,analyte,observed_at'}),
        'map_dates': ('feed_sample_dates', {'select': 'station_id,sample_date,first_seen_at,approx,note',
            'and': date_filter, 'order': 'station_id,sample_date'}),
        'datasf': ('samples', {'select': 'station_id,sample_date,analyte,value_raw,first_seen_at,source',
            'and': date_filter, 'order': 'station_id,sample_date,analyte,value_raw'}),
        'runtime': ('sfpuc_getcsv_runtime', {'select': 'enabled,requested_at,last_success_at,last_error,last_error_at,consecutive_failures,last_row_count,last_changes', 'id': 'eq.1', 'order': 'id'}),
    }
    if s['station']:
        jobs['observations'][1]['source_code'] = 'eq.' + s['station']
        jobs['datasf'][1]['station_id'] = 'eq.' + s['station']
        jobs['map_dates'][1]['station_id'] = 'eq.' + STATIONS[s['station']].sfpuc_id
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {key: pool.submit(_read_all, *job) for key, job in jobs.items()}
        for key, future in futures.items():
            try:
                data[key], capped = future.result()
                if capped:
                    data['notes'].append(f'{key.replace("_", " ").title()} reached the {MAX_ROWS:,}-row limit; narrow the dates or station. Timing comparisons may be incomplete.')
            except Exception:
                LOG.exception('Sample gap dashboard could not load %s', key)
                data['notes'].append(f'{key.replace("_", " ").title()} could not be loaded. Missing data here does not establish that results are pending.')
    return data


def build_model(data, now=None):
    now = now or datetime.now(timezone.utc)
    grouped = defaultdict(list)
    for row in data['observations']:
        grouped[(row['source_code'], row['sample_date'])].append(row)
    map_dates = {(r['station_id'], r['sample_date']): r for r in data['map_dates']}
    datasf = defaultdict(list)
    for row in data['datasf']:
        datasf[(row['station_id'], row['sample_date'], row['analyte'])].append(row)
    cards = []
    for (source, day), rows in sorted(grouped.items(), key=lambda x: (-date.fromisoformat(x[0][1]).toordinal(), x[0][0])):
        station = STATIONS.get(source)
        mp = map_dates.get((station.sfpuc_id, day)) if station else None
        cells, events = [], []
        for analyte, label in INDICATORS:
            history = sorted((r for r in rows if r['analyte'] == analyte), key=lambda r: timestamp(r['observed_at']))
            latest = history[-1] if history else None
            first = next((r for r in history if r['value_raw'] is not None), None)
            df = sorted(datasf.get((source, day, analyte), []), key=lambda r: timestamp(r['first_seen_at']))
            df_first = next((r for r in df if r['source'] == 'refresh'), None)
            cells.append({'label': label, 'value': latest['value_raw'] if latest else None,
                          'removed': latest and latest['observation_kind'] == 'removed',
                          'first': first, 'datasf': df[-1]['value_raw'] if df else None,
                          'datasf_first': df_first, 'history': history,
                          'revisions': sum(r['observation_kind'] in ('changed', 'removed') for r in history)})
            if first:
                events.append({'lane': 'getCSV', 'at': timestamp(first['observed_at']),
                               'label': label, 'approx': first['observation_kind'] == 'baseline'})
            if df_first:
                events.append({'lane': 'DataSF', 'at': timestamp(df_first['first_seen_at']), 'label': label, 'approx': False})
        if mp:
            events.append({'lane': 'City map', 'at': timestamp(mp['first_seen_at']), 'label': 'Sample date updated', 'approx': mp['approx']})
        # One common clock for all lanes; clustered indicators retain individual labels below.
        if events:
            lo, hi = min(e['at'] for e in events), max(e['at'] for e in events)
            span = max((hi - lo).total_seconds(), 1)
            for e in events:
                e['position'] = round(5 + 90 * (e['at'] - lo).total_seconds() / span, 2)
        cards.append({'name': station.name if station else source, 'source': source, 'day': day,
                      'cells': cells, 'events': events, 'map': mp,
                      'revisions': sum(c['revisions'] for c in cells)})
    runtime = data['runtime'][0] if data['runtime'] else {}
    last = timestamp(runtime.get('last_success_at'))
    if not runtime:
        health = 'Not connected'
    elif not runtime.get('enabled'):
        health = 'Paused'
    elif runtime.get('last_error'):
        health = 'Needs attention'
    elif not last:
        health = 'Waiting for first read'
    elif now - last > timedelta(minutes=6):
        health = 'Stale'
    else:
        health = 'Recording'
    return {'cards': cards, 'runtime': runtime, 'health': health,
            'revisions': sum(c['revisions'] for c in cards),
            'early': sum(cell['value'] is not None and cell['datasf'] is None for c in cards for cell in c['cells']),
            'notes': data['notes']}


def render_page(data, s, now=None):
    return render_template('alerts/sample_gap.html', s=s, m=build_model(data, now),
                           stations=STATIONS, fmt_time=fmt_time)


def handle_page(query, body):
    s = settings(query, datetime.now(PACIFIC).date())
    return 200, 'text/html; charset=utf-8', render_page(load_data(s), s).encode()


GET_ROUTES = {'/alerts/sample-gap': handle_page}
