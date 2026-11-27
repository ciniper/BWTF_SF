"""Production evidence guards, including the recorded November 20 failure."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch
import pandas as pd
from features.forecast import live_dashboard as ld

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/nov20_getcsv_batches.json').read_text())

class EvidenceGuardTests(unittest.TestCase):
    def engine(self):
        eng = ld.LiveData.__new__(ld.LiveData)
        eng._live_corrections_enabled = lambda: (True, 'test')
        eng._watcher_health = lambda: {'ok': True, 'mode': 'live'}
        eng._cso_flag_days = lambda *args: {}
        eng._sample_flags = lambda *args: {}
        eng._feed_sample_flags = lambda *args: {}
        return eng

    def context(self, eng):
        day = date(2026, 11, 20)
        dates = [day - timedelta(days=1), day]
        return eng._live_context({'avg': pd.DataFrame({'precip_inches': [0., 0.]})},
                                 [{'westside': .1}] * 2, [{'westside': 1.}] * 2, dates, {}, day)

    def test_recorded_cso_and_unsampled_rows_cannot_be_clean(self):
        class DB:
            def is_configured(self): return True
            def select(self, *args): return FIXTURE['map_days']
        with patch.object(ld, '_supabase', DB()):
            flags = ld.LiveData.__new__(ld.LiveData)._feed_sample_flags(date(2026, 11, 18), date(2026, 11, 20))
        self.assertNotIn(False, flags.values())
        self.assertNotIn(('Ocean Beach', date(2026, 11, 19)), flags)

    def test_partial_datasf_cannot_clear_sibling_posting(self):
        eng = self.engine()
        key = ('Ocean Beach', date(2026, 11, 19))
        eng._sample_flags = lambda *args: {key: False}
        eng._feed_sample_flags = lambda *args: {key: True}
        self.assertIs(self.context(eng)['samples'][key], True)
        eng._feed_sample_flags = lambda *args: {}
        self.assertNotIn(key, self.context(eng)['samples'])

    def test_baker_flag_cannot_hold_ocean_beach(self):
        eng = self.engine()
        eng.beach_status = [{'source': 'OCEAN#16_SL', 'has_cso': True},
                            {'source': 'OCEAN#19_SL', 'has_cso': False}]
        context = self.context(eng)
        day = date(2026, 11, 20)
        self.assertEqual(context['flag_groups'], {day: {'Baker-China'}})
        risks = {'Ocean Beach': .421, 'Baker-China': .202}
        adjusted, notes = ld._lr.adjust_groups(risks, risks, {}, day, {}, {},
            {day-timedelta(days=2): {'westside'}}, {day: {'westside'}},
            {'Ocean Beach': 'ocean', 'Baker-China': 'baker_china'},
            {g: 'westside' for g in risks}, lambda g,k: .692 if g == 'Ocean Beach' else .251,
            flag_groups=context['flag_groups'])
        self.assertEqual(adjusted, {'Ocean Beach': .421, 'Baker-China': .251})
        self.assertNotIn('Ocean Beach', notes)
        self.assertEqual(notes['Baker-China']['rule'], 'flag_hold')

if __name__ == '__main__': unittest.main()
