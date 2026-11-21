"""Read-only Admin AQPI explorer, refreshed on demand with a per-worker cache."""
import csv
import json
import logging
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import requests
from flask import after_this_request, render_template

from features.alerts.aqpi import REGIONS, SOURCES, parse_feed

CACHE_SECONDS = 15 * 60
RETRY_SECONDS = 60
MAX_BYTES = 40 * 1024 * 1024
DOWNLOAD_SECONDS = 30
LAG_WARNING_SECONDS = 3 * 60 * 60  # UI warning, not a provider service guarantee.
_cache = {}
_locks = {key: threading.Lock() for key in SOURCES}
log = logging.getLogger(__name__)


def _download(key):
    """Fixed public URLs only; bound time and decompressed size before parsing."""
    started = time.monotonic()
    raw = bytearray()
    with requests.get(SOURCES[key][3], stream=True, timeout=(5, 10),
                      headers={'User-Agent': 'BWTF-SF/Admin-Rain-Explorer'}) as response:
        response.raise_for_status()
        for chunk in response.iter_content(64 * 1024):
            if time.monotonic() - started > DOWNLOAD_SECONDS:
                raise TimeoutError('AQPI download exceeded deadline')
            raw.extend(chunk)
            if len(raw) > MAX_BYTES:
                raise ValueError('AQPI download exceeded size limit')
        return bytes(raw), response.headers.get('Last-Modified')


def live_feed(key):
    """One refresh per worker/feed; failure retains last good values, never a fixture."""
    with _locks[key]:
        cached = _cache.get(key, {})
        if time.monotonic() >= cached.get('retry_at', 0):
            checked = datetime.now(timezone.utc)
            try:
                raw, modified = _download(key)
                feed = parse_feed(raw, key, expected_intervals=96 if key == 'surfrider' else 168)
                end = datetime.fromisoformat(feed['end'])
                if (end - checked).total_seconds() > 15 * 60:
                    raise ValueError('Observations are dated in the future')
                if cached.get('feed') and end < datetime.fromisoformat(cached['feed']['end']):
                    raise ValueError('Provider observation window moved backwards')
                cached = {'feed': feed, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                          'last_modified': modified, 'error': None}
                delay = CACHE_SECONDS
            except (requests.RequestException, ValueError, KeyError, TypeError, TimeoutError, csv.Error) as exc:
                log.warning('AQPI %s refresh failed: %s', key, exc)
                cached = {**cached, 'error': 'The AQPI feed could not be refreshed. Retrying shortly.'}
                delay = RETRY_SECONDS
            cached.update(checked_at=datetime.now(timezone.utc).isoformat(),
                          retry_at=time.monotonic() + delay)
            _cache[key] = cached
        result = {k: v for k, v in cached.items() if k != 'retry_at'}
        now = datetime.now(timezone.utc)
        lag = ((now - datetime.fromisoformat(result['feed']['end'])).total_seconds()
               if result.get('feed') else None)
        result.update(key=key, server_time=now.isoformat(), observation_lag_seconds=lag,
                      delayed=lag is not None and lag > LAG_WARNING_SECONDS,
                      stale=bool(result.get('error')), cache_seconds=CACHE_SECONDS,
                      retry_after_seconds=max(1, int(cached['retry_at'] - time.monotonic())))
        return result


@lru_cache(maxsize=1)
def snapshot():
    return json.loads((Path(__file__).parent / 'data' / 'rain_snapshot.json').read_text())


def handle_page(query, body):
    # Only dated research context is embedded. No old rainfall values can masquerade as live.
    research = snapshot()
    rain = {'snapshot_label': research['snapshot_label'], 'archive': research['archive'],
            'regions': REGIONS, 'feeds': {key: {'url': spec[3]} for key, spec in SOURCES.items()}}
    html = render_template('alerts/rain_data.html', rain=rain)
    return 200, 'text/html; charset=utf-8', html.encode()


def handle_data(query, body):
    @after_this_request
    def private(response):
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    key = query.get('feed', ['surfrider'])[0]
    if key not in SOURCES:
        return 400, 'application/json', b'{"error":"Unknown AQPI feed"}'
    result = live_feed(key)
    return (200 if result.get('feed') else 503), 'application/json', json.dumps(result, allow_nan=False).encode()


GET_ROUTES = {'/alerts/rain-data': handle_page}
API_ROUTES = {'/alerts/api/rain-data': handle_data}
