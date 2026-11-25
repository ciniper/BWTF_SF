"""Preserve complete AQPI downloads; optional immutable Supabase Storage upload.

Default: local collection only. No .env loading, bucket creation, deletion,
database writes, or interaction with the forecast or alert system.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import zlib
from urllib.parse import urlparse
from uuid import uuid4

import requests

from shared.aqpi import SOURCES, parse_feed

MAX_BYTES = 40 * 1024 * 1024
MAX_STORED_BYTES = MAX_BYTES + 1024 * 1024
STALE_HOURS = 6


class ArchiveError(RuntimeError):
    """Safe diagnostic without credentials or HTTP response bodies."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_bounded(response, limit=MAX_BYTES, seconds=90):
    started = time.monotonic()
    raw = bytearray()
    for chunk in response.iter_content(64 * 1024):
        if time.monotonic() - started > seconds:
            raise ArchiveError('Download deadline exceeded')
        raw.extend(chunk)
        if len(raw) > limit:
            raise ArchiveError('Download size limit exceeded')
    return bytes(raw)


def fetch_feed(key):
    # Source and destination requests never share an authenticated session.
    for attempt in range(3):
        try:
            with requests.get(SOURCES[key][3], stream=True, timeout=(10, 20),
                              headers={'User-Agent': 'BWTF-SF/AQPI-Archive'}) as response:
                if response.status_code != 200:
                    raise ArchiveError(f'Provider returned HTTP {response.status_code}')
                raw = read_bounded(response)
                if not raw:
                    raise ArchiveError('Provider returned an empty file')
                return raw, {name: response.headers.get(name) for name in ('Last-Modified', 'ETag')}
        except (requests.RequestException, ArchiveError):
            if attempt == 2:
                raise ArchiveError('Provider download failed after three attempts') from None
            time.sleep(2 ** attempt)


def raw_key(feed, sha):
    return f'raw/{feed}/{sha[:2]}/{sha}.csv.gz'


def receipt_key(record):
    captured = datetime.fromisoformat(record['retrieved_at'])
    return f"captures/{record['feed']}/{captured:%Y/%m/%d}/{record['capture_id']}.json"


def write_local(root, key, data):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    # Content-addressed raw files may already be present in a reused spool.
    if path.exists():
        if path.read_bytes() != data:
            raise ArchiveError('Local archive object differs; refusing overwrite')
        return
    temporary = path.with_name(path.name + '.tmp-' + uuid4().hex)
    temporary.write_bytes(data)
    temporary.replace(path)


def capture(root, key, raw, headers, now=None):
    now = now or datetime.now(timezone.utc)
    sha = digest(raw)
    existing = root / raw_key(key, sha)
    if existing.exists():
        compressed = existing.read_bytes()
        if digest(unpack(compressed)) != sha:
            raise ArchiveError('Local raw object differs; refusing overwrite')
    else:
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    # Save bytes before parsing: schema changes must not discard original evidence.
    write_local(root, raw_key(key, sha), compressed)
    record = {'schema_version': 1, 'capture_id': uuid4().hex, 'feed': key,
              'source_url': SOURCES[key][3], 'retrieved_at': now.isoformat(),
              'http_last_modified': headers.get('Last-Modified'), 'http_etag': headers.get('ETag'),
              'sha256': sha, 'raw_key': raw_key(key, sha), 'raw_bytes': len(raw),
              'gzip_bytes': len(compressed), 'validation': 'invalid', 'warnings': []}
    try:
        parsed = parse_feed(raw, key, expected_intervals=96 if key == 'surfrider' else 168)
        lag = (now - datetime.fromisoformat(parsed['end'])).total_seconds() / 3600
        record.update(validation='valid', units='inches', run_time=parsed['run_time'],
                      observation_start=parsed['start'], observation_end=parsed['end'],
                      observation_times=parsed['times'], forecast_times=parsed['forecast_times'],
                      observation_step_minutes=parsed['step_minutes'], raw_rows=parsed['raw_rows'])
        if lag > STALE_HOURS:
            record['warnings'].append('Provider observations more than six hours old')
        if lag < -.25:
            record['warnings'].append('Provider observations more than 15 minutes in the future')
        if any(r['complete'] != len(parsed['times']) for r in parsed['regions'].values()):
            record['warnings'].append('Missing observations in one or more research areas')
    except (ValueError, KeyError, TypeError, csv.Error):
        record['warnings'].append('CSV failed validation; raw bytes preserved for investigation')
    encoded = (json.dumps(record, sort_keys=True, allow_nan=False) + '\n').encode()
    write_local(root, receipt_key(record), encoded)
    return record


def unpack(compressed):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
            raw = source.read(MAX_BYTES + 1)
    except (OSError, EOFError, zlib.error):
        raise ArchiveError('Invalid gzip archive') from None
    if len(raw) > MAX_BYTES:
        raise ArchiveError('Uncompressed archive exceeds size limit')
    return raw


class Storage:
    def __init__(self, url, key, bucket='aqpi-archive'):
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ArchiveError('Set AQPI_ARCHIVE_SUPABASE_URL to the HTTPS project origin')
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', bucket):
            raise ArchiveError('Missing archive key or invalid bucket name')
        self.base = url.rstrip('/') + '/storage/v1'
        self.bucket = bucket
        self.headers = {'apikey': key}
        # New secret keys belong in apikey, not in a JWT bearer header.
        if not key.startswith('sb_secret_'):
            self.headers['Authorization'] = 'Bearer ' + key

    @classmethod
    def from_env(cls):
        return cls(os.environ.get('AQPI_ARCHIVE_SUPABASE_URL', ''),
                   os.environ.get('AQPI_ARCHIVE_SUPABASE_KEY', ''),
                   os.environ.get('AQPI_ARCHIVE_BUCKET', 'aqpi-archive'))

    def request(self, method, path, **kwargs):
        headers = {**self.headers, **kwargs.pop('headers', {})}
        for attempt in range(3):
            try:
                response = requests.request(method, self.base + path, headers=headers,
                                            timeout=(10, 30), allow_redirects=False, **kwargs)
                if response.status_code != 429 and response.status_code < 500:
                    return response
                response.close()
            except requests.RequestException:
                pass
            if attempt < 2:
                time.sleep(2 ** attempt)
        raise ArchiveError('Storage request failed after three attempts')

    def check_private_bucket(self):
        with self.request('GET', '/bucket/' + self.bucket) as response:
            if response.status_code != 200:
                raise ArchiveError(f'Cannot read archive bucket (HTTP {response.status_code})')
            bucket = response.json()
            if not isinstance(bucket, dict) or bucket.get('public') is not False:
                raise ArchiveError('Archive bucket must be private')

    def put_once(self, key, content, mime, raw_sha=None):
        path = '/object/' + self.bucket + '/' + key
        with self.request('POST', path, data=content,
                          headers={'Content-Type': mime, 'x-upsert': 'false'}) as response:
            if response.status_code in (200, 201):
                return
            # An existing object is success only after checking its bytes. This
            # also makes a retry safe if the original POST succeeded but timed out.
            if response.status_code not in (400, 409):
                raise ArchiveError(f'Storage upload failed (HTTP {response.status_code})')
        with self.request('GET', path, stream=True) as existing:
            if existing.status_code != 200:
                raise ArchiveError('Could not verify existing archive object')
            saved = read_bounded(existing, MAX_STORED_BYTES if raw_sha else 256 * 1024)
        matches = digest(unpack(saved)) == raw_sha if raw_sha else saved == content
        if not matches:
            raise ArchiveError('Existing archive object differs; refusing overwrite')


def upload_receipt(root, path, storage):
    encoded = path.read_bytes()
    record = json.loads(encoded)
    # Replays can come from downloaded failure artifacts. Derive paths only
    # from validated identifiers, never trust arbitrary paths in a receipt.
    if (record.get('schema_version') != 1 or record.get('feed') not in SOURCES
            or not re.fullmatch(r'[a-f0-9]{64}', record.get('sha256', ''))
            or not re.fullmatch(r'[a-f0-9]{32}', record.get('capture_id', ''))):
        raise ArchiveError('Invalid capture receipt')
    key = raw_key(record['feed'], record['sha256'])
    if record.get('raw_key') != key or path.relative_to(root).as_posix() != receipt_key(record):
        raise ArchiveError('Capture paths do not match receipt')
    compressed = (root / key).read_bytes()
    if len(compressed) > MAX_STORED_BYTES or digest(unpack(compressed)) != record['sha256']:
        raise ArchiveError('Raw data does not match receipt checksum')
    storage.put_once(key, compressed, 'application/gzip', raw_sha=record['sha256'])
    # Receipt only becomes durable once its source object is durable.
    storage.put_once(receipt_key(record), encoded, 'application/json')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Local spool; never contains credentials')
    parser.add_argument('--upload', action='store_true', help='Write to the explicitly configured private bucket')
    parser.add_argument('--replay', action='store_true', help='Upload an existing spool without fetching feeds; requires --upload')
    args = parser.parse_args(argv)
    if args.replay and not args.upload:
        parser.error('--replay requires --upload; normal collection is local-only by default')
    root = args.output.resolve()
    failures = 0
    paths = []
    if args.replay:
        paths = sorted(root.glob('captures/*/*/*/*/*.json'))
        if not paths:
            print('ERROR: no capture receipts to replay')
            return 1
    else:
        for key in SOURCES:
            try:
                raw, headers = fetch_feed(key)
                record = capture(root, key, raw, headers)
                paths.append(root / receipt_key(record))
                print(f"{key}: saved {record['raw_bytes']} bytes; {record['validation']}; sha256={record['sha256']}")
                for warning in record['warnings']:
                    print(f'{key}: WARNING: {warning}')
                failures += bool(record['warnings'])
            except (ArchiveError, OSError) as exc:
                reason = str(exc) if isinstance(exc, ArchiveError) else 'Local spool write failed'
                print(f'{key}: ERROR: {reason}; other feeds will still be attempted')
                failures += 1
    if args.upload:
        try:
            storage = Storage.from_env()
            storage.check_private_bucket()
        except (ArchiveError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, ArchiveError) else 'Invalid bucket metadata'
            print(f'ERROR: {reason}; local spool retained')
            return 1
        for path in paths:
            try:
                upload_receipt(root, path, storage)
                print(f'Archived {path.relative_to(root)}')
            except (ArchiveError, OSError, ValueError, KeyError, TypeError, requests.RequestException) as exc:
                reason = str(exc) if isinstance(exc, ArchiveError) else 'Archive upload/verification failed'
                print(f'ERROR: {reason} for {path.name}; local spool retained')
                failures += 1
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
