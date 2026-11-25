"""Protect the meaning of the rainfall visuals and their Admin boundary."""
import csv
import copy
from datetime import datetime, timedelta, timezone
import io
import math
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from scripts.build_rain_snapshot import build_feed, complete_sum, archive_months
from features.alerts.rain_data import snapshot
from features.alerts import rain_data
from shared.aqpi import parse_feed


def test_snapshot_windows_and_region_totals():
    s=snapshot()
    for key,count,step in [('surfrider',96,15),('wastewater',168,60)]:
        f=s['feeds'][key]
        assert len(f['times'])==count and f['step_minutes']==step
        assert f['times'][-1]==f['end']
        assert f['forecast_times'][0]>f['end']
        assert len({tuple(p[:2]) for p in f['points']})==len(f['points'])
        for region in f['regions'].values():
            # The JSON is rounded; Python 3.12 also changed float summation.
            assert math.isclose(complete_sum(region['observed']),region['total'],abs_tol=0.00002)
            assert region['count']>0
    assert s['archive']['count']==306
    assert s['archive']['start']=='2025-10-01'
    assert s['archive']['end']=='2026-08-29'


def test_missing_cells_stay_missing_duplicates_and_forecasts_do_not_inflate_totals(tmp_path):
    path=tmp_path/'feed.csv'
    fields=['name','institution','gridpoint','run_time','qpe15min_202611201545','qpe15min_202611201600','qpf01_202611201700']
    # One point in each research rectangle; overlapping watershed duplicate.
    rows=[['area','test',f'POINT ({x} {y})','2026-11-20 16:00','.1','.2','99']
          for x,y in [(-122.48,37.75),(-122.42,37.79),(-122.42,37.76),(-122.42,37.72)]]
    rows.append(list(rows[0]));rows[2][5]=''
    with path.open('w') as f:
        writer=csv.writer(f);writer.writerow(fields);writer.writerows(rows)
    feed=build_feed(path,'surfrider')
    assert len(feed['points'])==4
    assert feed['regions']['westside']['count']==1
    assert feed['regions']['westside']['total']==.3
    assert feed['regions']['central']['total'] is None
    assert feed['regions']['city']['observed']==[.1,None]
    assert feed['regions']['city']['total'] is None
    assert feed['regions']['city']['forecast']==[99]


def test_manifest_uses_observation_day_not_next_midnight(tmp_path):
    path=tmp_path/'manifest.csv'
    path.write_text('SURFRIDER_FOUNDATION_qpe15min_202602010000.csv\n')
    archive=archive_months(path)
    assert archive['start']==archive['end']=='2026-01-31'
    assert archive['months'][0]['count']==1


def test_admin_gate_and_live_page():
    from app.wsgi import app
    with app.test_client() as client:
        locked=client.get('/alerts/rain-data')
        assert b'name="next" value="/alerts/rain-data"' in locked.data
        assert b'id="rain-context"' not in locked.data
        with patch.object(rain_data, '_download') as download:
            assert client.get('/alerts/api/rain-data').status_code == 401
            download.assert_not_called()
        with client.session_transaction() as session:
            session['alerts_unlocked']=True
        page=client.get('/alerts/rain-data')
        assert page.status_code==200
        assert b'id="rain-context"' in page.data
        assert b'aria-current="page"' in page.data
        assert b'Live AQPI explorer' in page.data
        assert b'Nov 20, 2026' in page.data
        assert b'"observed":' not in page.data
        assert b'"points":' not in page.data
        assert client.get('/alerts/api/rain-data?feed=other').status_code == 400
        with patch.object(rain_data, 'live_feed', return_value={'error':'unavailable'}):
            failed = client.get('/alerts/api/rain-data')
            assert failed.status_code == 503
            assert failed.headers['Cache-Control'] == 'private, no-store'
        with patch.object(rain_data, 'live_feed', return_value={'feed':{'end':'test'}}):
            loaded = client.get('/alerts/api/rain-data')
            assert loaded.status_code == 200
            assert loaded.json['feed']['end'] == 'test'


def live_csv(count=96):
    end = datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0) - timedelta(hours=1)
    observed = ['qpe15min_' + (end-timedelta(minutes=15*i)).strftime('%Y%m%d%H%M')
                for i in reversed(range(count))]
    forecast = 'qpf01_' + (end+timedelta(hours=1)).strftime('%Y%m%d%H%M')
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['gridpoint','run_time',*observed,forecast])
    for x,y in [(-122.48,37.75),(-122.42,37.79),(-122.42,37.76),(-122.42,37.72)]:
        writer.writerow([f'POINT ({x} {y})',end.strftime('%Y-%m-%d %H:%M'),*(['0.01']*count),'99'])
    return output.getvalue().encode()


def test_live_parser_rejects_short_and_truncated_windows():
    good = live_csv()
    feed = parse_feed(good,'surfrider',expected_intervals=96)
    assert feed['regions']['city']['total'] == .96
    assert feed['regions']['city']['forecast'] == [99]
    extraction = datetime.fromisoformat(feed['end']) + timedelta(minutes=15)
    later_extraction = good.replace(feed['run_time'].removesuffix(' UTC').encode(),
                                   extraction.strftime('%Y-%m-%d %H:%M').encode())
    assert parse_feed(later_extraction,'surfrider',expected_intervals=96)['end'] == feed['end']
    for raw in (live_csv(95), good.rsplit(b',',1)[0], good.replace(b'qpf01_',b'qpe15min_',1)):
        try:
            parse_feed(raw,'surfrider',expected_intervals=96)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed window was accepted')


def test_cache_refresh_failure_retry_and_no_snapshot_fallback():
    with patch.object(rain_data,'_cache',{}), patch.object(rain_data,'_download',return_value=(live_csv(),'modified')) as download:
        first = rain_data.live_feed('surfrider')
        assert len(first['feed']['times']) == 96
        assert not first['stale']
        assert rain_data.live_feed('surfrider')['fetched_at'] == first['fetched_at']
        assert download.call_count == 1
        rain_data._cache['surfrider']['retry_at'] = 0
        download.side_effect = TimeoutError('test timeout')
        failed = rain_data.live_feed('surfrider')
        assert failed['stale'] and failed['error']
        assert failed['feed'] == first['feed']
        assert failed['fetched_at'] == first['fetched_at']
        rain_data.live_feed('surfrider')
        assert download.call_count == 2  # Error cooldown prevents a download storm.
        empty = rain_data.live_feed('wastewater')
        assert empty['stale'] and 'feed' not in empty
        rain_data._cache['surfrider']['retry_at'] = 0
        download.side_effect = None
        assert rain_data.live_feed('surfrider')['error'] is None


def test_old_provider_data_is_flagged_and_backwards_update_rejected():
    old = copy.deepcopy(snapshot()['feeds']['surfrider'])
    old['end'] = (datetime.now(timezone.utc)-timedelta(hours=4)).isoformat()
    with patch.object(rain_data,'_cache',{}), patch.object(rain_data,'_download',return_value=(b'csv',None)), patch.object(rain_data,'parse_feed',return_value=old) as parser:
        result = rain_data.live_feed('surfrider')
        assert result['delayed'] and not result['stale']
        rain_data._cache['surfrider']['retry_at'] = 0
        backwards = {**old,'end':(datetime.now(timezone.utc)-timedelta(hours=5)).isoformat()}
        parser.return_value = backwards
        retained = rain_data.live_feed('surfrider')
        assert retained['error'] and retained['feed']['end'] == old['end']


def test_download_enforces_size_limit():
    from unittest.mock import MagicMock
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_content.return_value = [b'12345']
    with patch.object(rain_data.requests,'get',return_value=response), patch.object(rain_data,'MAX_BYTES',4):
        try:
            rain_data._download('surfrider')
        except ValueError as exc:
            assert 'size limit' in str(exc)
        else:
            raise AssertionError('Oversize download was accepted')


if __name__ == '__main__':
    import os
    from tempfile import TemporaryDirectory
    os.environ['ALERT_WATCHER_INTERVAL']='off'
    test_snapshot_windows_and_region_totals()
    with TemporaryDirectory() as directory:
        test_missing_cells_stay_missing_duplicates_and_forecasts_do_not_inflate_totals(Path(directory))
        test_manifest_uses_observation_day_not_next_midnight(Path(directory))
    test_admin_gate_and_live_page()
    test_live_parser_rejects_short_and_truncated_windows()
    test_cache_refresh_failure_retry_and_no_snapshot_fallback()
    test_old_provider_data_is_flagged_and_backwards_update_rejected()
    test_download_enforces_size_limit()
    print('PASS live rainfall, caching, failures, missing data, archive dates, and admin access')
