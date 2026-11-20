"""Protect the meaning of the rainfall visuals and their Admin boundary."""
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from scripts.build_rain_snapshot import build_feed, complete_sum, archive_months
from features.alerts.rain_data import snapshot


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


def test_admin_gate_and_embedded_snapshot():
    from app.wsgi import app
    with app.test_client() as client:
        locked=client.get('/alerts/rain-data')
        assert b'name="next" value="/alerts/rain-data"' in locked.data
        assert b'id="rain-snapshot"' not in locked.data
        with client.session_transaction() as session:
            session['alerts_unlocked']=True
        page=client.get('/alerts/rain-data')
        assert page.status_code==200
        assert b'id="rain-snapshot"' in page.data
        assert b'aria-current="page"' in page.data
        assert b'Research snapshot' in page.data
        assert b'Nov 20, 2026' in page.data


if __name__ == '__main__':
    import os
    from tempfile import TemporaryDirectory
    os.environ['ALERT_WATCHER_INTERVAL']='off'
    test_snapshot_windows_and_region_totals()
    with TemporaryDirectory() as directory:
        test_missing_cells_stay_missing_duplicates_and_forecasts_do_not_inflate_totals(Path(directory))
        test_manifest_uses_observation_day_not_next_midnight(Path(directory))
    test_admin_gate_and_embedded_snapshot()
    print('PASS rainfall snapshot, missing data, archive dates, and admin access')
