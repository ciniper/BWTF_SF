"""Source Comparison uses the same early-result precedence as sample popovers."""
import sys
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.comparison import comparison as C
from features.comparison.bwtf_api import BWTFLab, BWTFSite, BWTFSample

NOW = datetime(2026, 11, 20, 12)
SOURCE = 'BAY#211_SL'
NAME = 'Aquatic Park'


def row(day, value, code='ENTERO', early=False):
    return dict(source=SOURCE, sample_date=day, data=value, analyte=code, early=early)


def history(day, value):
    return dict(site_name=NAME, collection_time=datetime.fromisoformat(day+'T09:00:00'),
                substance='Enterococcus', result_raw=value, result_value=C.std_parse(value))


class Monitor:
    API_URL = 'fixture'
    def __init__(self, rows=(), fail=False):
        self.rows, self.fail = rows, fail
        self.session = self
    def get(self, url, params, timeout):
        if self.fail:
            raise requests.Timeout('fixture timeout')
        code = 'ENTERO' if "analyte='ENTERO'" in params['$where'] else 'COLI_TOTAL'
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [r for r in self.rows if r['analyte'] == code])


def build(city=(), early=(), hist=None, view='latest', state='current', fail=False, analyte='ENTERO', as_of=''):
    hist = hist if hist is not None else [history('2026-11-20', '20')]
    latest = max(hist, key=lambda r: r['collection_time'])
    sample = BWTFSample(latest['collection_time'], 'Enterococcus', latest['result_raw'], latest['result_value'])
    site = BWTFSite('s', NAME, 37.8, -122.4, latest['collection_time'], latest['result_raw'], latest['result_value'], [sample])
    lab = BWTFLab(76, 'SF', 'San Francisco', 36, 104, [site])
    client = SimpleNamespace(fetch_lab=lambda: lab, fetch_history=lambda **kw: hist)
    with patch.object(C.sfpuc_live, 'read_snapshot', return_value=(list(early), state)), patch.object(C, 'now_pacific_naive', return_value=NOW):
        return C.build_comparison(client, Monitor(city, fail), SimpleNamespace(fetch_stations=lambda: []), view=view, analyte=analyte, as_of=as_of)


def test_early_result_then_datasf_handoff_keeps_qualifier_and_duplicates():
    early = [row('2026-11-20', '>200', early=True)]
    first = build(early=early)
    r = first['rows'][0]
    assert r['city_early'] and r['city_raw'] == '>200' and r['same_day'] and r['agree'] is False
    assert first['summary']['same_day']['pairs'] == 1
    final = build(city=[row('2026-11-20', '30'), row('2026-11-20', '40')], early=early)
    r = final['rows'][0]
    assert not r['city_early'] and r['city_raws'] == ['30', '40'] and r['city_n'] == 2
    assert r['agree'] is True and final['summary']['same_day']['pairs'] == 1


def test_partial_coliform_does_not_masquerade_as_new_enterococcus():
    d = build(city=[row('2026-11-16', '10')], early=[row('2026-11-20', '500', 'COLI_TOTAL', True)])
    r = d['rows'][0]
    assert r['city_date'] == '2026-11-16' and r['city_pending_date'] == '2026-11-20'
    assert not r['same_day'] and r['agree'] is None


def test_shared_view_finds_older_match_instead_of_comparing_latest_dates():
    city = [row('2026-11-19', '10'), row('2026-11-13', '100')]
    hist = [history('2026-11-20', '10'), history('2026-11-13', '20'), history('2026-11-13', '>200')]
    latest = build(city=city, hist=hist)['rows'][0]
    assert latest['day_gap'] == 1 and latest['agree'] is None
    shared = build(city=city, hist=hist, view='shared')['rows'][0]
    assert shared['city_date'] == shared['bwtf_date'] == '2026-11-13'
    assert shared['bwtf_raw'] == '>200' and shared['agree'] is False


def test_shared_early_pair_disappears_on_withdrawal_or_stale_feed():
    early = [row('2026-11-20', '<10', early=True)]
    first = build(early=early, view='shared')['rows'][0]
    assert first['city_early'] and first['same_day']
    for state in ('current', 'stale'):
        d = build(view='shared', state=state)
        r = d['rows'][0]
        assert r['shared_missing'] and r['city_raw'] is None and r['bwtf_raw'] is None
        assert d['summary']['same_day']['pairs'] == 0
        if state == 'stale': assert d['notes']


def test_shared_no_match_window_boundary_and_coliform_mode():
    d = build(city=[row('2025-10-01', '10')], hist=[history('2026-11-20', '20'), history('2025-10-01', '20')], view='shared')
    assert d['rows'][0]['shared_missing']
    d = build(early=[row('2026-11-20', '100', 'COLI_TOTAL', True)], view='shared', analyte='COLI_TOTAL')
    assert d['view'] == 'latest' and not d['bwtf_measures'] and d['summary']['same_day'] is None
    assert d['rows'][0]['city_early']


def test_datasf_failure_preserves_early_values_with_explicit_warning():
    d = build(early=[row('2026-11-20', '50', early=True)], fail=True, view='shared')
    assert d['rows'][0]['city_raw'] == '50' and d['rows'][0]['city_early']
    assert any('DataSF' in note and 'incomplete' in note for note in d['notes'])


def test_shared_window_includes_morning_sample_on_first_collection_day():
    d = build(city=[row('2025-11-20', '10')],
              hist=[history('2026-11-20', '20'), history('2025-11-20', '20')], view='shared')
    assert d['rows'][0]['city_date'] == d['rows'][0]['bwtf_date'] == '2025-11-20'
    assert d['summary']['same_day']['pairs'] == 1


def test_series_early_scope_provenance_and_handoff():
    early = [row('2026-11-20', '>200', early=True), row('2026-11-21', '999', early=True)]
    client = SimpleNamespace(fetch_history=lambda **kw: [history('2026-11-20', '20')])
    with patch.object(C.sfpuc_live, 'read_snapshot', return_value=(early, 'current')), patch.object(C, 'fetch_city_records', return_value=[]):
        d = C.build_site_series(SOURCE, client, Monitor(), start='2026-11-01', end='2026-11-20', include_early=True)
        assert len(d['city']['ENTERO']) == 1 and d['city']['ENTERO'][0]['early']
        assert d['paired'][0]['city_early'] and d['paired'][0]['city_raw'] == '>200'
        regular = C.build_site_series(SOURCE, client, Monitor(), start='2026-11-01', end='2026-11-20')
        assert not regular['city']['ENTERO']  # standalone Graphs stays outside this experiment
    merged = C._merge_early([row('2026-11-20', '25')], early, [SOURCE], datetime(2026,11,1), NOW)
    d = C.site_series_payload(C.resolve_site(SOURCE), merged, [history('2026-11-20', '20')], datetime(2026,11,1), NOW)
    assert not d['paired'][0]['city_early'] and d['paired'][0]['city'] == 25


def test_shared_route_and_html_explain_experiment_and_provenance():
    from app.wsgi import app
    from features.comparison import page
    calls = []
    def fixture(**kw):
        calls.append(kw)
        return build(early=[row('2026-11-20', '>200', early=True)], view=kw.get('view', 'latest'), analyte=kw.get('analyte','ENTERO'), as_of=kw.get('as_of', ''))
    with patch.object(page, 'build_comparison', side_effect=fixture), app.test_client() as client:
        response = client.get('/compare?view=shared')
        html = response.get_data(as_text=True)
        assert response.status_code == 200 and calls[-1]['view'] == 'shared'
        assert 'Latest shared collection date · Experiment' in html and 'Early SFPUC · getCSV' in html
        assert 'older results, not current conditions' in html and 'shared-date exceed' in html
        assert client.get('/api/compare?view=shared').json['view'] == 'shared'
        response = client.get('/compare?view=shared&as_of=2026-11-13')
        html = response.get_data(as_text=True)
        assert calls[-1]['as_of'] == '2026-11-13' and 'value="2026-11-13"' in html
        assert 'view=latest&amp;as_of=2026-11-13' in html
        assert 'It does not show the data that the site published on that day' in html
        assert client.get('/api/compare?as_of=2026-11-13').json['historical']


def test_historical_overview_excludes_future_samples_and_rolls_summary_back():
    hist = [history('2026-11-20', '200'), history('2026-11-13', '20')]
    city = [row('2026-11-20', '200'), row('2026-11-13', '200')]
    d = build(city=city, hist=hist, early=[row('2026-11-20', '500', 'COLI_TOTAL', True)], as_of='2026-11-13')
    r = d['rows'][0]
    assert d['historical'] and d['as_of_date'] == '2026-11-13' and d['today'] == '2026-11-20'
    assert r['city_date'] == r['bwtf_date'] == '2026-11-13'
    assert r['agree'] is False and r['city_pending_date'] is None and r['sfpuc_status'] is None
    assert d['summary']['agree_count'] == 0 and d['summary']['disagree_count'] == 1
    assert d['summary']['same_day']['pairs'] == 1 and d['summary']['same_day']['until'] == '2026-11-13'
    shared = build(city=city, hist=hist, view='shared', as_of='2026-11-19')
    assert shared['rows'][0]['city_date'] == shared['rows'][0]['bwtf_date'] == '2026-11-13'


def test_historical_empty_date_never_falls_back_to_current_lab_values():
    d = build(city=[row('2026-11-20', '200')], as_of='2026-11-01')
    r = d['rows'][0]
    assert r['bwtf_date'] is None and r['city_date'] is None and r['agree'] is None
    assert d['summary']['same_day']['pairs'] == 0


def test_historical_date_bounds_and_coliform_selection():
    for date in ('', 'bad-date', '2099-01-01'):
        d = build(as_of=date)
        assert d['as_of_date'] == '2026-11-20' and not d['historical']
    d = build(city=[row('2026-11-13', '500', 'COLI_TOTAL'), row('2026-11-20', '10000', 'COLI_TOTAL')],
              as_of='2026-11-13', analyte='COLI_TOTAL')
    assert d['rows'][0]['city_raw'] == '500' and d['rows'][0]['bwtf_raw'] is None
    assert d['summary']['same_day'] is None


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-q']))
