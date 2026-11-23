import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

"""A posting is not evidence of a sample; known collection dates are."""
from datetime import datetime
from types import SimpleNamespace
from shared.result_gap import pending_results
from app import landing as L


def test_only_an_actual_newer_collection_date_establishes_a_gap():
    assert pending_results({'4608':'2026-11-13'}, {'4608':'2026-11-09'}) == {'4608':{'sample_date':'2026-11-13'}}
    assert pending_results({'4608':'2026-11-13'}, {'4608':'2026-11-13'}) == {}
    assert pending_results({'4608':'2026-11-13'}, {'4608':'2026-11-14'}) == {}
    assert pending_results({'4608':'bad'}, {}) == {}
    assert pending_results({}, {}) == {}


def station(status='posted', cso=False):
    return SimpleNamespace(station_id='4608',station_name='Baker Beach West',status=SimpleNamespace(value=status),
                           has_cso=cso,sample_date=datetime(2026,11,9))


def row(board):
    return next(s for z in board['zones'] for s in z['stations'] if s['source']=='OCEAN#16_SL')


def test_precautionary_posting_and_discharge_never_claim_a_pending_sample():
    # Also ignore cached data from the old posting-based inference.
    old_guess={'4608':{'at':'2026-11-13T14:32:00Z','change':'posted'}}
    for status,cso in [('posted',False),('safe',False),('posted',True)]:
        board=L.today_board([station(status,cso)],now=datetime(2026,11,13),pending=old_guess)
        assert row(board)['pending'] is None
        assert row(board)['sampled_text']=='Sampled 4 Days Ago'
        assert all('unpublished' not in z['meta'] for z in board['zones'])
        assert all('not published' not in f['text'] for f in board['facts'])


def test_early_sample_updates_age_without_changing_posting_status():
    board=L.today_board([station()],now=datetime(2026,11,14),lab_dates={'OCEAN#16_SL':'2026-11-13'})
    r=row(board)
    assert r['sampled']=='2026-11-13' and r['map_sampled']=='2026-11-09'
    assert r['sampled_age_days']==1 and r['status']=='posted' and r['pending'] is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
