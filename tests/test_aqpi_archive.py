"""Exercise preservation, retry safety, recovery and the archive's write boundary."""
import csv
from datetime import datetime, timedelta, timezone
import gzip
import io
import json
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.aqpi_archive import collector as archive


def fixture(feed='surfrider', end=None):
    end = end or datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    prefix, step, count = ('qpe15min_', 15, 96) if feed == 'surfrider' else ('qpe_', 60, 168)
    fields = [prefix + (end-timedelta(minutes=step*i)).strftime('%Y%m%d%H%M')
              for i in reversed(range(count))]
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['gridpoint', 'run_time', *fields, 'qpf01_' + (end+timedelta(hours=1)).strftime('%Y%m%d%H%M')])
    # Includes an outside-SF row: archive must preserve more than the map parser.
    for x,y in [(-122.48,37.75),(-122.42,37.79),(-122.42,37.76),(-122.42,37.72),(-122.8,38.2)]:
        writer.writerow([f'POINT ({x} {y})',end.strftime('%Y-%m-%d %H:%M'),*(['0.01']*count),'99'])
    return stream.getvalue().encode()


class Response:
    def __init__(self, status=200, body=b'', payload=None):
        self.status_code, self.body, self.payload = status, body, payload
        self.headers = {'Last-Modified': 'source-time', 'ETag': 'source-etag'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def close(self):
        pass

    def json(self):
        return self.payload

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i+size]


class FakeStorage:
    """REST behavior exercised through the real Storage adapter, never production."""
    def __init__(self):
        self.objects, self.calls = {}, []
        self.public = False
        self.fail_receipts = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert kwargs['allow_redirects'] is False
        if '/bucket/' in url:
            return Response(payload={'public': self.public})
        key = url.split('/object/aqpi-archive/',1)[1]
        if method == 'GET':
            return Response(200, self.objects[key]) if key in self.objects else Response(404)
        assert method == 'POST' and kwargs['headers']['x-upsert'] == 'false'
        if self.fail_receipts and key.startswith('captures/'):
            return Response(503)
        if key in self.objects:
            return Response(400)  # Legacy duplicate response; must verify by reading.
        self.objects[key] = kwargs['data']
        return Response(201)


class ArchiveTests(unittest.TestCase):
    def test_collector_import_does_not_load_web_app_or_credentials(self):
        # A fresh process catches package initialization as well as direct imports.
        result = subprocess.run(
            [sys.executable, '-c',
             "import sys; from features.aqpi_archive import collector; "
             "assert not any(name == 'flask' or name.startswith('flask.') "
             "or name == 'app' or name.startswith('app.') "
             "or name == 'features.alerts' or name.startswith('features.alerts.') "
             "or name == 'shared.supabase' for name in sys.modules)"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = archive.Storage('https://archive-test.supabase.co', 'sb_secret_test')
        self.remote = FakeStorage()
        self.request = patch.object(archive.requests, 'request', side_effect=self.remote.request).start()
        self.addCleanup(patch.stopall)
        patch.object(archive.time, 'sleep').start()

    def capture(self, raw=None):
        return archive.capture(self.root, 'surfrider', raw or fixture(), {'ETag':'v1'})

    def upload(self, record):
        archive.upload_receipt(self.root, self.root / archive.receipt_key(record), self.storage)

    def test_raw_round_trip_dedup_and_capture_provenance(self):
        raw = fixture()
        first, second = self.capture(raw), self.capture(raw)
        self.assertEqual(first['validation'], 'valid')
        self.assertEqual(first['warnings'], [])
        self.assertEqual(first['raw_rows'], 5)
        self.assertEqual(first['http_etag'], 'v1')
        self.assertEqual(len(first['observation_times']), 96)
        self.assertEqual(len(first['forecast_times']), 1)
        self.assertEqual(gzip.decompress((self.root / first['raw_key']).read_bytes()), raw)
        self.assertNotEqual(first['capture_id'], second['capture_id'])
        self.upload(first)
        self.upload(second)
        self.upload(first)  # Exact replay creates nothing and overwrites nothing.
        self.assertEqual(len(self.remote.objects), 3)
        self.assertTrue(any(method == 'GET' for method, _, _ in self.remote.calls))
        self.assertTrue(all('Authorization' not in kw['headers'] for _,_,kw in self.remote.calls))

    def test_revision_with_same_run_time_is_preserved(self):
        raw = fixture()
        first = self.capture(raw)
        revised = self.capture(raw.replace(b'0.01', b'0.02', 1))
        self.assertEqual(first['run_time'], revised['run_time'])
        self.assertNotEqual(first['sha256'], revised['sha256'])
        self.upload(first)
        self.upload(revised)
        self.assertEqual(len(self.remote.objects), 4)

    def test_existing_raw_can_use_another_gzip_encoding(self):
        raw = fixture()
        record = self.capture(raw)
        alternate = gzip.compress(raw, compresslevel=1, mtime=12345)
        (self.root / record['raw_key']).write_bytes(alternate)
        next_capture = self.capture(raw)
        self.assertEqual(next_capture['gzip_bytes'], len(alternate))
        self.assertEqual((self.root / record['raw_key']).read_bytes(), alternate)

    def test_invalid_stale_and_missing_data_are_saved_and_flagged(self):
        invalid = self.capture(b'<html>provider changed format</html>')
        self.assertEqual(invalid['validation'], 'invalid')
        self.upload(invalid)
        self.assertTrue(invalid['warnings'])
        stale = self.capture(fixture(end=datetime(2020,1,1,tzinfo=timezone.utc)))
        self.assertIn('Provider observations more than six hours old', stale['warnings'])
        missing = self.capture(fixture().replace(b'0.01', b'', 1))
        self.assertIn('Missing observations', missing['warnings'][0])

    def test_failed_receipt_upload_can_be_replayed_without_fetch(self):
        record = self.capture()
        self.remote.fail_receipts = True
        with self.assertRaises(archive.ArchiveError):
            self.upload(record)
        self.assertEqual(set(self.remote.objects), {record['raw_key']})
        self.remote.fail_receipts = False
        with patch.object(archive.Storage, 'from_env', return_value=self.storage), patch.object(archive,'fetch_feed') as fetch:
            self.assertEqual(archive.main(['--output', str(self.root), '--replay', '--upload']), 0)
            fetch.assert_not_called()
        self.assertEqual(len(self.remote.objects), 2)

    def test_conflicting_stored_object_is_not_overwritten(self):
        record = self.capture()
        self.remote.objects[record['raw_key']] = gzip.compress(b'wrong-data', mtime=0)
        with self.assertRaisesRegex(archive.ArchiveError, 'refusing overwrite'):
            self.upload(record)
        self.assertEqual(len(self.remote.objects), 1)

    def test_replay_rejects_tampered_paths_and_bytes(self):
        record = self.capture()
        receipt = self.root / archive.receipt_key(record)
        tampered = {**record, 'raw_key':'../../private-file'}
        receipt.write_text(json.dumps(tampered))
        with self.assertRaises(archive.ArchiveError):
            self.upload(record)
        receipt.write_text(json.dumps(record))
        (self.root / record['raw_key']).write_bytes(gzip.compress(b'wrong-data'))
        with self.assertRaises(archive.ArchiveError):
            self.upload(record)
        self.assertEqual(self.remote.calls, [])

    def test_one_provider_failure_does_not_skip_other_feed(self):
        def fetch(key):
            if key == 'surfrider':
                raise archive.ArchiveError('offline')
            return fixture(key), {}
        with patch.object(archive, 'fetch_feed', side_effect=fetch), patch.object(archive.Storage, 'from_env', return_value=self.storage):
            self.assertEqual(archive.main(['--output', str(self.root), '--upload']), 1)
        self.assertEqual(len(self.remote.objects), 2)
        self.assertTrue(all('/wastewater/' in k for k in self.remote.objects))

    def test_default_run_never_uploads_and_bad_config_retains_spool(self):
        with patch.object(archive, 'fetch_feed', side_effect=lambda key:(fixture(key), {})), patch.object(archive.Storage,'from_env') as config:
            self.assertEqual(archive.main(['--output', str(self.root)]), 0)
            config.assert_not_called()
            config.side_effect = archive.ArchiveError('not configured')
            self.assertEqual(archive.main(['--output', str(self.root), '--upload']), 1)
        self.assertEqual(len(list(self.root.rglob('*.csv.gz'))), 2)
        self.assertEqual(self.remote.calls, [])

    def test_public_bucket_is_rejected_and_keys_are_server_only(self):
        self.remote.public = True
        with self.assertRaisesRegex(archive.ArchiveError, 'private'):
            self.storage.check_private_bucket()
        legacy = archive.Storage('https://archive-test.supabase.co', 'eyJlegacy')
        self.assertEqual(legacy.headers['Authorization'], 'Bearer eyJlegacy')
        with self.assertRaises(archive.ArchiveError):
            archive.Storage('https://user:password@host', 'secret')

    def test_download_retries_bounds_and_never_uses_storage_credentials(self):
        with patch.object(archive.requests, 'get', side_effect=[Response(503), Response(body=fixture())]) as get:
            raw, headers = archive.fetch_feed('surfrider')
            self.assertEqual(get.call_count, 2)
            self.assertTrue(raw)
            self.assertEqual(headers['ETag'], 'source-etag')
            self.assertNotIn('apikey', get.call_args.kwargs['headers'])
        with self.assertRaises(archive.ArchiveError):
            archive.read_bounded(Response(body=b'oversized'), limit=2)


if __name__ == '__main__':
    unittest.main()
