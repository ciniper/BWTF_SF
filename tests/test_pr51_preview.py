"""The review deployment exercises PR 51 without touching production cache."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import os
import unittest
from unittest.mock import patch
from features.forecast import page

class PreviewTests(unittest.TestCase):
    def test_preview_uses_own_model_without_reading_or_writing_shared_cache(self):
        with patch.dict(os.environ, {'VERCEL_ENV': 'preview', 'FORECAST_CACHE_WRITE': '1'}), \
             patch.object(page, '_require_engine', return_value=None), \
             patch.object(page, '_serve_from_memory', return_value='preview') as serve, \
             patch.object(page, '_read_row') as read:
            self.assertFalse(page._cache_writable())
            self.assertEqual(page.handle_data({}, {}), 'preview')
            serve.assert_called_once()
            read.assert_not_called()

if __name__ == '__main__': unittest.main()
