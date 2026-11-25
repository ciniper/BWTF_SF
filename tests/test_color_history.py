from datetime import datetime, timezone
from unittest.mock import patch
from features.cso_history import color_history as C


def at(s):
    return C.timestamp('2026-11-18T'+s+':00Z')


def row(first, last, state, name='Fort Funston', **extra):
    return dict(first_at=at(first).isoformat(), last_confirmed_at=at(last).isoformat(),
                refreshes=2, beach_status=[dict(name=name, status=state)], **extra)


def spans(rows, start='10:00', end='13:00'):
    return next(t['spans'] for t in C.build_tracks(rows, at(start), at(end)) if t['name']=='Fort Funston')


def test_full_state_sequence_keeps_change_windows_and_no_extrapolation():
    result=spans([row('10:10','10:30','not_sampled'),row('10:45','11:00','safe'),
                  row('11:15','11:30','posted'),row('11:45','12:00','cso')])
    observed=[s for s in result if s['kind']=='observed']
    assert [s['state'] for s in observed]==['not_sampled','safe','posted','cso']
    assert result[0]['kind']=='uncertain' and result[-1]['kind']=='uncertain'
    transition=result[2]
    assert transition['from_state']=='not_sampled' and transition['to_state']=='safe'
    assert transition['start']==C.iso(at('10:30')) and transition['end']==C.iso(at('10:45'))


def test_equal_states_do_not_erase_unobserved_intervals():
    result=spans([row('10:00','10:30','safe'),row('11:00','12:00','safe')])
    assert result[1]['kind']=='uncertain'
    assert result[1]['from_state']==result[1]['to_state']=='safe'


def test_missing_snapshot_and_unknown_state_never_become_green():
    empty=row('10:30','11:00','safe');empty['beach_status']=[]
    result=spans([row('10:00','10:15','not_sampled'),empty,row('11:30','12:00','unexpected')])
    assert [s['state'] for s in result if s['kind']=='observed']==['not_sampled','unknown']
    assert any(s['start']==C.iso(at('10:30')) and s['end']==C.iso(at('11:00')) and s['kind']=='uncertain' for s in result)


def test_range_clips_spans_preserving_actual_observation_times():
    result=spans([row('09:00','11:00','safe'),row('12:00','14:00','cso')])
    assert result[0]['start']==C.iso(at('10:00'))
    assert result[0]['first_observed']==C.iso(at('09:00'))
    assert result[-1]['end']==C.iso(at('13:00'))
    assert result[-1]['last_confirmed']==C.iso(at('14:00'))


def test_single_observation_and_empty_history():
    result=spans([row('11:00','11:00','safe')])
    point=next(s for s in result if s['kind']=='observed')
    assert point['start']==point['end']
    assert spans([])==[dict(kind='uncertain',start=C.iso(at('10:00')),end=C.iso(at('13:00')),from_state=None,to_state=None)]


def test_overlapping_confirmation_does_not_cover_next_state():
    result=spans([row('10:00','12:00','safe'),row('11:00','12:30','posted')])
    assert result[0]['end']==C.iso(at('11:00'))


def test_api_is_bounded_read_only_and_uses_pacific_day():
    now=datetime(2026,11,18,20,tzinfo=timezone.utc)
    with patch.object(C.sb,'is_configured',return_value=True),patch.object(C,'fetch_snapshots',return_value=[]) as fetch:
        payload,status=C.payload({'start':['2026-11-01'],'end':['2026-11-01']},now)
        assert status==200
        start,end=fetch.call_args.args
        assert (end.astimezone(timezone.utc)-start.astimezone(timezone.utc)).total_seconds()==25*3600
        assert len(payload['tracks'])==20
    with patch.object(C,'fetch_snapshots') as fetch:
        for query in ({'start':['bad']},{'start':['2026-11-18'],'end':['2026-11-01']},{'start':['2024-01-01'],'end':['2026-11-01']}):
            assert C.payload(query,now)[1]==400
        fetch.assert_not_called()


def test_api_unavailability_is_explicit():
    with patch.object(C.sb,'is_configured',return_value=False):
        assert C.payload({})[1]==503
    with patch.object(C.sb,'is_configured',return_value=True),patch.object(C,'fetch_snapshots',side_effect=C.RequestException('offline')):
        assert C.payload({})[1]==503


def test_fetch_reads_projected_fields_and_boundary_snapshots():
    with patch.object(C.sb,'select',side_effect=[[{'id':1}],[{'id':2}],[{'id':3}]]) as select:
        assert C.fetch_snapshots(at('10:00'),at('13:00'))==[{'id':1},{'id':2},{'id':3}]
        assert select.call_count==3
        for call in select.call_args_list:
            assert call.args[0]=='forecast_changes'
            assert 'beach_status:snapshot->beach_status' in call.args[1]['select']
            assert 'snapshot,' not in call.args[1]['select']
