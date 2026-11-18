import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from datetime import date, datetime, timezone
from pathlib import Path

from features.alerts import sample_gap as G
from scripts.import_getcsv_recordings import observations


def data():
    return {'observations': observations(Path('docs/discharge-observations/feed').glob('*-getCSV.json')),
            'map_dates': [], 'datasf': [], 'runtime': [], 'notes': []}


def test_corrections_partial_results_and_baseline_times():
    d = data()
    m = G.build_model(d)
    card = next(c for c in m['cards'] if c['source'] == 'BAY#220_SL' and c['day'] == '2026-11-13')
    cell = next(c for c in card['cells'] if c['label'] == 'Total coliform')
    assert cell['value'] == '>16000'
    assert cell['revisions'] == 2
    assert cell['first']['observation_kind'] == 'baseline'
    assert m['health'] == 'Not connected'
    partial = data()
    partial['observations'] = [{'source_code': 'BAY#220_SL', 'sample_date': '2026-11-16', 'analyte': 'COLI_TOTAL',
        'observed_at': '2026-11-17T23:00:00Z', 'value_raw': '0', 'observation_kind': 'appeared', 'recording_source': 'collector'}]
    c = G.build_model(partial)['cards'][0]
    assert c['cells'][0]['value'] is None
    assert c['cells'][2]['value'] == '0'


def test_clock_map_id_and_datasf_backfill_not_arrival():
    d = data()
    d['map_dates'] = [{'station_id': '4618', 'sample_date': '2026-11-13', 'first_seen_at': '2026-11-14T22:33:00Z', 'approx': False}]
    d['datasf'] = [{'station_id': 'BAY#220_SL', 'sample_date': '2026-11-13', 'analyte': 'ENTERO', 'value_raw': '>24196',
                    'first_seen_at': '2026-11-17T23:00:00Z', 'source': 'backfill'}]
    c = next(c for c in G.build_model(d)['cards'] if c['source'] == 'BAY#220_SL' and c['day'] == '2026-11-13')
    assert any(e['lane'] == 'City map' for e in c['events'])
    assert not any(e['lane'] == 'DataSF' for e in c['events'])
    assert all(5 <= e['position'] <= 95 for e in c['events'])


def test_filter_bounds_and_stale_health():
    s = G.settings({'from': ['2020-01-01'], 'to': ['2026-11-17'], 'station': ['not-a-station']}, date(2026,11,17))
    assert s == {'from': date(2026,10,18), 'to': date(2026,11,17), 'station': ''}
    assert G.settings({'to': '0001-01-01'}, date(2026,11,17))['to'] == date(1900,1,1)
    d = data()
    d['runtime'] = [{'enabled': True, 'last_success_at': '2026-11-17T20:00:00Z', 'last_error': None}]
    assert G.build_model(d, datetime(2026,11,17,20,10,tzinfo=timezone.utc))['health'] == 'Stale'


def test_pagination_and_failure_note(monkeypatch):
    monkeypatch.setattr(G, 'PAGE_SIZE', 2)
    monkeypatch.setattr(G, 'MAX_ROWS', 6)
    monkeypatch.setattr(G.sb, 'select', lambda table, params: [{'n':n} for n in range(int(params['offset']), min(3,int(params['offset'])+2))])
    assert G._read_all('x', {}) == ([{'n':0},{'n':1},{'n':2}], False)
    monkeypatch.setattr(G.sb, 'is_configured', lambda: True)
    def fail(*args):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(G, '_read_all', fail)
    d = G.load_data(G.settings({},date(2026,11,17)))
    assert len(d['notes']) == 4
    assert not d['observations']


def test_admin_gate_render_filters_and_escaping(monkeypatch):
    from app.wsgi import app
    d = data()
    d['notes'] = ['<script>alert(1)</script>']
    seen = []
    def load(s):
        seen.append(s)
        return d
    monkeypatch.setattr(G, 'load_data', load)
    with app.test_client() as client:
        locked = client.get('/alerts/sample-gap')
        assert b'name="next" value="/alerts/sample-gap"' in locked.data
        assert not seen
        with client.session_transaction() as session:
            session['alerts_unlocked'] = True
        response = client.get('/alerts/sample-gap?station=BAY%23220_SL&from=2026-11-13&to=2026-11-17')
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert '<h1>The sample gap</h1>' in html
        assert 'href="/alerts/sample-gap" class="on" aria-current="page"' in html
        assert '&gt;16000' in html and '&lt;script&gt;alert(1)&lt;/script&gt;' in html
        assert seen[0]['station'] == 'BAY#220_SL'
        assert 'Historical recorder' in html


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
