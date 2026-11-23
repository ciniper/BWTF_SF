import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone
import json
from types import SimpleNamespace

from shared import sfpuc_live as L
from features.comparison import samples as S
from features.cso_history import page as T

NOW=datetime(2026,11,17,22,tzinfo=timezone.utc)
SOURCE='OCEAN#16_SL'


def rec(code='COLI_TOTAL',value='100',day='2026-11-16',early=True):
    return dict(source=SOURCE,sample_date=day,analyte=code,data=value,early=early)


def runtime(value='>100'):
    return dict(enabled=True,last_success_at='2026-11-17T21:59:00Z',
                previous_values={json.dumps([SOURCE,'2026-11-16','COLI_TOTAL']):value})


def test_snapshot_preserves_qualifiers_and_refuses_stale_paused_or_bad_state():
    rows,state=L.snapshot_records(runtime(),NOW)
    assert state=='current' and rows[0]['data']=='>100'
    assert L.snapshot_records({**runtime(),'last_success_at':'2026-11-17T21:50:00Z'},NOW)==([], 'stale')
    assert L.snapshot_records({**runtime(),'enabled':False},NOW)==([], 'paused')
    assert L.snapshot_records({**runtime(),'previous_values':{'bad':'5'}},NOW)==([], 'unavailable')
    assert L.snapshot_records(runtime(None),NOW)==([], 'current')


def test_datasf_wins_per_indicator_and_preserves_double_samples():
    early=[rec(value='100'),rec('ENTERO','<10')]
    df=[rec(value='110',early=False),rec(value='120',early=False)]
    merged=L.merge_records(df,early)
    assert [r['data'] for r in merged]==['110','120','<10']
    assert L.merge_records([rec(value=None,early=False)],early)==early


def test_partial_payload_does_not_borrow_older_indicators():
    payload=S.sample_day_payload([rec(),rec('ENTERO','5','2026-11-09',False)],SOURCE,live=True)
    assert payload['date']=='2026-11-16'
    assert payload['cells']['COLI_TOTAL'][0]['value']==100
    assert payload['cells']['ENTERO']==[] and set(payload['pending_codes'])=={'ENTERO','COLI_FECAL'}


def wire(monkeypatch,early,df):
    monkeypatch.setattr(L,'read_snapshot',lambda: (early,'current'))
    monkeypatch.setattr(L,'fetch_observations',lambda *args: [])
    monkeypatch.setattr(S,'fetch_city_records',lambda *args: df)


def test_full_handoff_and_mixed_indicators(monkeypatch):
    early=[rec(),rec('ENTERO','<10'),rec('COLI_FECAL','15')]
    wire(monkeypatch,early,[rec(value='110',early=False)])
    p=S.build_sample_day(SOURCE,sf_gov_monitor=object())
    assert p['early'] and p['indicator_sources']=={'COLI_TOTAL':'datasf','ENTERO':'getcsv','COLI_FECAL':'getcsv'}
    assert p['cells']['COLI_TOTAL'][0]['raw']=='110' and not p['pending_codes']
    wire(monkeypatch,early,[{**r,'early':False} for r in early])
    p=S.build_sample_day(SOURCE,sf_gov_monitor=object())
    assert not p['early'] and set(p['indicator_sources'].values())=={'datasf'}


def test_datasf_failure_can_still_show_available_lab_values(monkeypatch):
    wire(monkeypatch,[rec()],[])
    def unavailable(*args):raise RuntimeError('offline')
    monkeypatch.setattr(S,'fetch_city_records',unavailable)
    p=S.build_sample_day(SOURCE,sf_gov_monitor=object())
    assert p['found'] and p['early'] and 'not available now' in p['note']


def test_stale_feed_falls_back_without_inventing_new_sample(monkeypatch):
    wire(monkeypatch,[],[rec(day='2026-11-09',early=False)])
    monkeypatch.setattr(L,'read_snapshot',lambda:([],'stale'))
    p=S.build_sample_day(SOURCE,sf_gov_monitor=object())
    assert p['date']=='2026-11-09' and not p['early'] and p['pending_sample'] is None
    p=S.build_sample_day(SOURCE,sf_gov_monitor=object(),feed_date='2026-11-16')
    assert p['pending_sample']=={'sample_date':'2026-11-16'}


def test_dates_overlay_fresh_rows_even_when_history_is_cached(monkeypatch):
    monkeypatch.setattr(L,'read_snapshot',lambda:([rec()],'current'))
    old={'dates':['2026-11-09'],'latest':'2026-11-09'}
    new=S._with_live_dates(old,SOURCE)
    assert new['dates']==['2026-11-09','2026-11-16'] and new['latest']=='2026-11-16'
    assert old['dates']==['2026-11-09']


def observation(code,at,kind='appeared'):
    return dict(source_code=SOURCE,sample_date='2026-11-16',analyte=code,
                observed_at=at,observation_kind=kind,value_raw='100')


def test_timeline_adds_csv_only_sample_and_one_first_indicator_marker():
    rows=[observation('COLI_TOTAL','2026-11-17T20:00:00Z'),observation('ENTERO','2026-11-17T21:00:00Z')]
    days=T.build_sample_days([],{},NOW,rows)['4608']
    assert len(days)==1 and days[0]['source']=='getcsv' and days[0]['pending']
    assert days[0]['getcsv_seen']=='2026-11-17T20:00:00Z' and not days[0]['getcsv_censored']
    assert len(days[0]['getcsv_indicators'])==2 and days[0]['lane']==0


def test_timeline_marker_baseline_is_seen_by_not_false_arrival():
    rows=[observation('COLI_TOTAL','2026-11-17T20:00:00Z','baseline'),observation('COLI_TOTAL','2026-11-17T21:00:00Z','changed')]
    entry=T.build_sample_days([],{},NOW,rows)['4608'][0]
    assert entry['getcsv_censored'] and entry['getcsv_seen']=='2026-11-17T20:00:00Z'
    assert len(entry['getcsv_indicators'])==1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
