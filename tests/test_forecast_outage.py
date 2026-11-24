"""Offline regression coverage for weather outages; no shared cache writes."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.forecast import live_dashboard as ld, page


def response(status):
    r = ld.requests.Response()
    r.status_code = status
    r.url = 'https://api.open-meteo.com/v1/forecast?private=diagnostic'
    r._content = json.dumps({'hourly': {
        'time': ['2026-11-23T12:00'], 'precipitation': [2.54],
        'temperature_2m': [15], 'wind_speed_10m': [10], 'wind_direction_10m': [180],
    }}).encode()
    return r


def test_transient_retry_recovers_without_changing_model():
    live = ld.LiveData.__new__(ld.LiveData)
    for failure in [response(503), ld.requests.Timeout('timeout')]:
        with patch.object(ld.requests, 'get', side_effect=[failure, response(200)]) as get, patch.object(ld.time, 'sleep'):
            result = live._fetch_open_meteo()
        assert get.call_count == 2
        assert all(c.kwargs['params']['models'] == 'icon_seamless' for c in get.call_args_list)
        assert abs(result.precip_inches.iloc[0] - .1) < 1e-9


def test_retry_is_bounded_and_skips_permanent_errors():
    live = ld.LiveData.__new__(ld.LiveData)
    for status, count in [(503, 2), (400, 1), (429, 1)]:
        with patch.object(ld.requests, 'get', return_value=response(status)) as get, patch.object(ld.time, 'sleep'):
            try:
                live._fetch_open_meteo()
                raise AssertionError('expected HTTP error')
            except ld.requests.HTTPError:
                pass
        assert get.call_count == count


def test_refresh_preserves_prediction_age_and_updates_beaches_on_outage():
    live = ld.LiveData.__new__(ld.LiveData)
    live.lock = Lock()
    live.last_refresh = datetime(2026, 11, 23, tzinfo=timezone.utc)
    old_time = live.last_refresh
    live.predictions = {'old': {'date': '2026-11-23'}}
    live._fetch_sfpuc = Mock(return_value=[{'name': 'Ocean Beach', 'status': 'posted'}])
    live._fetch_open_meteo = Mock(side_effect=ld.requests.HTTPError('503 with raw URL'))
    with patch.object(ld.traceback, 'print_exc'):
        live.refresh()
    assert live.beach_status[0]['status'] == 'posted'
    assert live.last_refresh == old_time and 'old' in live.predictions
    assert 'Weather data' in live.error and 'URL' not in live.error


def test_failed_refresh_never_stores_or_restamps_old_predictions():
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    old = datetime(2026, 11, 23, tzinfo=timezone.utc)
    snap = {'error': 'Weather unavailable', 'predictions': {'old': {}}, 'beach_status': [{'status': 'posted'}]}
    live = SimpleNamespace(refresh=Mock(), get_snapshot=lambda: snap, last_refresh=old)
    row = {'snapshot': {'predictions': {'stored': {}}}, 'generated_at': page._iso(old)}
    with patch.object(page, '_engine', SimpleNamespace(LIVE=live)), \
         patch.object(page, '_release_claim') as release, \
         patch.object(page, '_store_snapshot') as store, \
         patch.object(page, '_record_history') as history:
        for cached in [row, None]:
            result = json.loads(page._compute_and_store(cached, now)[2])
            assert result['generated_at'] == page._iso(old)
            assert result['beach_status'] == snap['beach_status']
        release.assert_called_once()
        store.assert_not_called()
        history.assert_not_called()
    assert row['snapshot'] == {'predictions': {'stored': {}}}


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('test_'):
            fn()
            print('PASS', name)
