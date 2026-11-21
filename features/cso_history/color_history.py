"""Observed map states from retained forecasts, with explicit observation gaps.

This is read-only. Forecast snapshots retain classified beach states, not the
raw SFPUC color fields or the precise instant a source changed between polls.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from requests.exceptions import RequestException
from shared import supabase as sb
from shared.stations import STATIONS

PACIFIC = ZoneInfo('America/Los_Angeles')
STATES = {'safe', 'not_sampled', 'posted', 'cso', 'unknown'}
NAMES = {s.sfpuc_id: s.sfpuc_name for s in STATIONS.values()}
BY_NAME = {name: sid for sid, name in NAMES.items()}
BY_SOURCE = {sid: s.sfpuc_id for sid, s in STATIONS.items()}


def timestamp(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except ValueError:
        return None


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def fetch_snapshots(start, end):
    """Include one snapshot on either side to bound edge transitions honestly."""
    select = 'id,first_at,last_confirmed_at,refreshes,beach_status:snapshot->beach_status'
    before = sb.select('forecast_changes', {'select': select, 'first_at': 'lt.' + iso(start),
                                          'order': 'first_at.desc,id.desc', 'limit': '1'})
    rows = []
    while True:
        page = sb.select('forecast_changes', {'select': select,
                         'and': f'(first_at.gte.{iso(start)},first_at.lt.{iso(end)})',
                         'order': 'first_at.asc,id.asc', 'offset': str(len(rows)), 'limit': '500'})
        rows.extend(page)
        if len(page) < 500:
            break
    after = sb.select('forecast_changes', {'select': select, 'first_at': 'gte.' + iso(end),
                                         'order': 'first_at.asc,id.asc', 'limit': '1'})
    return before + rows + after


def build_tracks(rows, start, end):
    """Do not carry a color across a gap, before first sighting, or past last confirmation."""
    by_station = {sid: [] for sid in NAMES}
    for row in sorted(rows, key=lambda r: (timestamp(r.get('first_at')) or datetime.min.replace(tzinfo=timezone.utc), r.get('id', 0))):
        first = timestamp(row.get('first_at'))
        last = timestamp(row.get('last_confirmed_at'))
        if first is None or last is None or last < first:
            continue
        # Empty snapshots still break continuity; absence is not a green observation.
        states = {}
        for beach in row.get('beach_status') or []:
            sid = BY_SOURCE.get(beach.get('source')) or BY_NAME.get(beach.get('name'))
            if sid:
                value = beach.get('status')
                states[sid] = value if value in STATES else 'unknown'
        for sid in by_station:
            by_station[sid].append({'first': first, 'last': last, 'state': states.get(sid),
                                   'refreshes': row.get('refreshes', 1)})
    tracks = []
    for sid, observations in by_station.items():
        spans = []
        cursor = start
        previous = None

        def gap(a, b, old=None, new=None):
            a, b = max(a, start), min(b, end)
            if a < b:
                spans.append({'kind': 'uncertain', 'start': iso(a), 'end': iso(b),
                              'from_state': old, 'to_state': new})

        for i, observation in enumerate(observations):
            first, last, state = observation['first'], observation['last'], observation['state']
            # An overlapping or malformed confirmation cannot overwrite the next snapshot.
            if i + 1 < len(observations):
                last = min(last, observations[i + 1]['first'])
            if last < start:
                previous = state
                continue
            if first > end:
                gap(cursor, end, previous, state)
                cursor = end
                break
            gap(cursor, first, previous, state)
            a, b = max(first, start), min(last, end)
            if a <= b and state is not None:
                spans.append({'kind': 'observed', 'start': iso(a), 'end': iso(b),
                              'state': state, 'first_observed': iso(first),
                              'last_confirmed': iso(last), 'refreshes': observation['refreshes']})
            elif a < b:
                gap(a, b)
            cursor = max(cursor, b)
            previous = state
        gap(cursor, end, previous, None)
        tracks.append({'station_id': sid, 'name': NAMES[sid], 'spans': spans})
    return tracks


def payload(query, now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(PACIFIC).date()
    try:
        first = date.fromisoformat(query.get('start', [(today-timedelta(days=13)).isoformat()])[0])
        last = date.fromisoformat(query.get('end', [today.isoformat()])[0])
        if first > last or last > today or (last-first).days >= 366:
            raise ValueError
    except (ValueError, IndexError):
        return {'error': 'Choose up to 366 days in order, ending no later than today.'}, 400
    if not sb.is_configured():
        return {'error': 'Timestamped map history is unavailable in this environment.'}, 503
    start = datetime.combine(first, time.min, PACIFIC)
    end = min(datetime.combine(last+timedelta(days=1), time.min, PACIFIC), now)
    try:
        rows = fetch_snapshots(start, end)
    except (sb.SupabaseError, RequestException):
        return {'error': 'Timestamped map history is temporarily unavailable.'}, 503
    return {'start': iso(start), 'end': iso(end), 'source': 'forecast_changes',
            'tracks': build_tracks(rows, start, end)}, 200
