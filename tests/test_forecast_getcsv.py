"""As-of replay of recorded batches and complete-panel correction safeguards."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from features.forecast import sample_evidence as E
from shared import sfpuc_live as L

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/nov20_getcsv_batches.json').read_text())
SOURCES = E.SITE_GROUPS['Ocean Beach'][1]
NOW = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)  # Nov 20, 4 PM Pacific


def early_at(now=NOW):
    values = {}
    for row in FIXTURE['observations']:
        at = datetime.fromisoformat(row['observed_at'])
        if at <= now:
            values[json.dumps([row['source_code'], row['sample_date'], row['analyte']])] = row['value_raw']
    runtime = {'enabled': True, 'last_success_at': now.isoformat(), 'previous_values': values}
    with patch.object(L, '_read_runtime', return_value=runtime):
        return L.read_evidence_snapshot(now)


def beaches():
    return [{'source': s, 'sample_date_iso': '2026-11-19', 'status': 'safe', 'has_cso': False} for s in SOURCES]


def snapshot():
    groups = {g: .1 for g in E.SITE_GROUPS}
    groups['Ocean Beach'] = .784
    model = {'groups': groups, 'persist': dict(groups), 'today_terms': {'Ocean Beach': .054712878},
             'probs': {'2026-11-18': {'westside': 1.}}, 'onsets': {}, 'flags': {}, 'flag_groups': {},
             'large_curves': {g: {str(k): .7 for k in range(1, 8)} for g in groups}}
    return {'sample_inputs': [], 'beach_status': beaches(), 'predictions': {'2026-11-20': {
        'date': '2026-11-20', 'is_today': True, 'sample_model': model,
        'live_corrections': {'enabled': True, 'groups': {}}, 'zones': {'ocean': .784}}}}


def passing():
    early = early_at()
    for row in early['records']:
        row['data'] = '10'
    return early


def ocean(result): return result['predictions']['2026-11-20']


class RecordedBatchTests(unittest.TestCase):
    def test_original_recorder_snapshots_keep_new_partial_panels_pending(self):
        snapshots = sorted((Path(__file__).resolve().parents[1] / 'docs/discharge-observations/feed').glob('*-getCSV.json'))
        partials = 0
        for path in snapshots:
            recording = json.loads(path.read_text())
            now = datetime.fromisoformat(recording['read_at'])
            values = {}
            for source, days in recording['stations'].items():
                for day in days:
                    collected = datetime.strptime(day['sample_date'], '%m/%d/%y').date().isoformat()
                    for code in L.CODES:
                        values[json.dumps([source, collected, code])] = day.get(code)
            runtime = {'enabled': True, 'last_success_at': now.isoformat(), 'previous_values': values}
            with patch.object(L, '_read_runtime', return_value=runtime):
                early = L.read_evidence_snapshot(now)
            # Counterfactual clear map isolates the lab completeness rule.
            clear_map = [{'source': s, 'sample_date_iso': d, 'status': 'safe', 'has_cso': False}
                         for s, d in early['known_dates'].items()]
            groups, _ = E.build_evidence([], early, clear_map, now)
            for group in groups.values():
                if any(p['pending'] and p['date'] for p in group['stations']):
                    partials += 1
                    self.assertFalse(group['clean_eligible'], path.name)
        self.assertEqual(len(snapshots), 16)
        self.assertGreater(partials, 0)

    def test_recorded_batches_never_clear_mixed_ocean_beach_results(self):
        snap = snapshot()
        for hour, minute, complete, failed in [(23, 32, 0, 0), (23, 33, 0, 2), (23, 43, 0, 2), (23, 59, 6, 2)]:
            now = datetime(2026, 11, 20, hour, minute, 1, tzinfo=timezone.utc)
            with self.subTest(time=now):
                out = ocean(E.overlay_snapshot(snap, early_at(now), now))
                group = out['sample_evidence']['Ocean Beach']
                self.assertEqual(sum(p['complete'] for p in group['stations']), complete)
                self.assertEqual(sum(p['state'] == 'elevated' for p in group['stations']), failed)
                self.assertFalse(group['clean_eligible'])
                self.assertEqual(out['zones']['ocean'], .784)
                self.assertNotIn('Ocean Beach', out['live_corrections']['groups'])

    def test_complete_pass_can_lower_but_partial_station_and_advisory_cannot(self):
        snap, early = snapshot(), passing()
        result = E.overlay_snapshot(snap, early, NOW)
        self.assertEqual(ocean(result)['zones']['ocean'], .452)
        self.assertEqual(ocean(result)['live_corrections']['groups']['Ocean Beach']['rule'], 'sample_clean_cap')
        for code in E.REQUIRED:
            partial = copy.deepcopy(early)
            partial['records'] = [r for r in partial['records'] if not (r['source'] == SOURCES[0] and r['analyte'] == code)]
            self.assertEqual(ocean(E.overlay_snapshot(snap, partial, NOW))['zones']['ocean'], .784)
        snap['beach_status'][0]['has_cso'] = True
        self.assertEqual(ocean(E.overlay_snapshot(snap, early, NOW))['zones']['ocean'], .784)
        snap['beach_status'][0].update(has_cso=False, status='unknown')
        self.assertEqual(ocean(E.overlay_snapshot(snap, early, NOW))['zones']['ocean'], .784)

    def test_absent_or_older_station_and_new_pending_sample_block_cap(self):
        for mode in ('absent', 'older', 'pending'):
            snap, early = snapshot(), passing()
            if mode == 'absent': early['records'] = [r for r in early['records'] if r['source'] != SOURCES[0]]
            if mode == 'older':
                for r in early['records']:
                    if r['source'] == SOURCES[0]: r['sample_date'] = '2026-11-18'
            if mode == 'pending': early['known_dates'][SOURCES[0]] = '2026-11-20'
            self.assertEqual(ocean(E.overlay_snapshot(snap, early, NOW))['zones']['ocean'], .784, mode)

    def test_datasf_precedence_is_per_indicator_not_per_group(self):
        early, snap = early_at(), snapshot()
        snap['sample_inputs'] = [{'source': 'OCEAN#19_SL', 'sample_date': '2026-11-19', 'analyte': 'ENTERO', 'data': '10'}]
        out = ocean(E.overlay_snapshot(snap, early, NOW))
        stations = {p['source']: p for p in out['sample_evidence']['Ocean Beach']['stations']}
        self.assertEqual(stations['OCEAN#19_SL']['indicators']['ENTERO'], [{'raw': '10', 'source': 'datasf'}])
        self.assertEqual(stations['OCEAN#21_SL']['state'], 'elevated')
        snap['sample_inputs'][0]['data'] = 'NaN'
        checked = ocean(E.overlay_snapshot(snap, early, NOW))['sample_evidence']['Ocean Beach']['stations']
        self.assertEqual(next(p for p in checked if p['source']=='OCEAN#19_SL')['indicators']['ENTERO'][0]['source'], 'getcsv')
        snap['sample_inputs'] = [{**r, 'early': False} for r in passing()['records']]
        out = ocean(E.overlay_snapshot(snap, early, NOW))
        self.assertEqual(out['zones']['ocean'], .452)

    def test_corrections_withdrawals_expiry_and_repeated_reads_recompute_from_base(self):
        snap, early = snapshot(), passing()
        initial = copy.deepcopy(snap)
        capped = E.overlay_snapshot(snap, early, NOW)
        self.assertEqual(snap, initial)
        self.assertEqual(E.overlay_snapshot(capped, early, NOW), capped)
        revised = copy.deepcopy(early)
        next(r for r in revised['records'] if r['analyte'] == 'ENTERO')['data'] = '500'
        self.assertEqual(ocean(E.overlay_snapshot(capped, revised, NOW))['zones']['ocean'], .784)
        removed = copy.deepcopy(early); removed['records'].pop()
        self.assertEqual(ocean(E.overlay_snapshot(capped, removed, NOW))['zones']['ocean'], .784)
        self.assertEqual(ocean(E.overlay_snapshot(capped, early, NOW+timedelta(minutes=7)))['zones']['ocean'], .784)
        for state in ('paused', 'unavailable', 'stale'):
            self.assertEqual(ocean(E.overlay_snapshot(capped, {**early, 'state': state}, NOW))['zones']['ocean'], .784)

    def test_blank_only_sample_remains_known(self):
        runtime = {'enabled': True, 'last_success_at': NOW.isoformat(), 'previous_values': {
            json.dumps([SOURCES[0], '2026-11-20', 'ENTERO']): None}}
        with patch.object(L, '_read_runtime', return_value=runtime):
            evidence = L.read_evidence_snapshot(NOW)
        self.assertEqual(evidence['records'], [])
        self.assertEqual(evidence['known_dates'][SOURCES[0]], '2026-11-20')

    def test_pending_resample_does_not_resolve_failure(self):
        early = early_at(); early['known_dates']['OCEAN#19_SL'] = '2026-11-20'
        groups, _ = E.build_evidence([], early, beaches(), NOW)
        station = next(p for p in groups['Ocean Beach']['stations'] if p['source'] == 'OCEAN#19_SL')
        self.assertEqual(station['state'], 'partial')
        self.assertEqual(station['failure_date'], '2026-11-19')

    def test_ratio_qualifiers_and_duplicate_results(self):
        def rows(total='1200', fecal='200', entero='10'):
            return [{'analyte': c, 'data': v} for c, v in [('COLI_TOTAL', total), ('COLI_FECAL', fecal), ('ENTERO', entero)]]
        self.assertEqual(E.station_panel(rows())['state'], 'elevated')
        self.assertEqual(E.station_panel(rows(fecal='50'))['state'], 'below_limits')
        self.assertEqual(E.station_panel(rows(fecal='>50'))['state'], 'partial')
        self.assertEqual(E.station_panel(rows(fecal='50', entero='>104'))['state'], 'elevated')
        self.assertEqual(E.station_panel(rows(fecal='<50', entero='<10'))['state'], 'below_limits')
        self.assertEqual(E.station_panel(rows(fecal='50')+[{'analyte': 'ENTERO', 'data': '600'}])['state'], 'elevated')

    def test_api_cached_response_and_today_share_fresh_evidence_without_mutating_cache(self):
        from features.forecast import page
        snap, early = snapshot(), passing()
        row = {'snapshot': snap, 'generated_at': NOW.isoformat()}
        with patch.object(page, '_utcnow', return_value=NOW), patch.object(L, 'read_evidence_snapshot', return_value=early):
            api = page._with_meta(snap, NOW)
            today = page.display_snapshot(row)
        self.assertEqual(ocean(api)['zones']['ocean'], .452)
        self.assertEqual(ocean(api), ocean(today))
        self.assertEqual(ocean(snap)['zones']['ocean'], .784)
        with patch.object(page, '_utcnow', return_value=NOW+timedelta(minutes=7)), patch.object(L, 'read_evidence_snapshot', return_value=early):
            self.assertEqual(ocean(page.display_snapshot(row))['zones']['ocean'], .784)

    def test_legacy_snapshot_does_not_invent_model_inputs(self):
        old = {'predictions': {'2026-11-20': {'zones': {'ocean': .5}}}}
        self.assertIs(E.overlay_snapshot(old, early_at(), NOW), old)

    def test_map_positive_floor_survives_unavailable_lab_feed(self):
        snap = snapshot()
        group = next(g for g, z in E.ZONE_OF_GROUP.items() if z == 'east')
        model = ocean(snap)['sample_model']
        model['feed_samples'] = [{'group': group, 'date': '2026-11-19'}]
        model['probs']['2026-11-18'][E.BASIN_OF_GROUP[group]] = 1.
        out = ocean(E.overlay_snapshot(snap, {'state': 'unavailable'}, NOW))
        self.assertEqual(out['impact_groups'][group], .8)
        self.assertEqual(out['live_corrections']['groups'][group]['rule'], 'sample_elevated_floor')

    def test_forecast_datasf_reader_preserves_double_samples(self):
        from features.forecast import live_dashboard as ld
        from unittest.mock import Mock
        rows = [{'source': SOURCES[0], 'sample_date': '2026-11-19T00:00:00', 'analyte': 'ENTERO', 'data': value}
                for value in ('10', '600')]
        response = Mock(); response.json.return_value = rows
        with patch.object(ld.requests, 'get', return_value=response):
            read = ld.LiveData.__new__(ld.LiveData)._samples_window(NOW.date(), NOW.date())
        self.assertEqual(len(read), 2)
        self.assertTrue(any(r['exceeds'] for r in read))

if __name__ == '__main__': unittest.main()
