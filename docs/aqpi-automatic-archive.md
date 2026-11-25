# Automatic AQPI archive

Status: **proposed; not activated**. This PR does not provision Storage, install
secrets, enable a job, deploy the web app, or change any model or alert input.
Merge and activation each require the user's explicit approval. Do not bypass
required reviews/checks or use admin merge privileges.

## Independent archive proposal

This PR contains only the collector, its shared CSV parser, tests, and a disabled
workflow proposal. It does not add or change the Admin viewer. The collector runs
without Flask or any Admin module; both features may use `shared/aqpi.py` without
importing one another. Each PR targets main independently.

The viewer can read public feeds with no archive storage or credentials. Storage
and a scheduler are needed only to preserve history automatically. GitHub Actions
and Supabase Storage below are the current proposal; the scheduler decision
remains open for review. No hosted configuration has been changed.

## What is captured

`.github/workflows/aqpi-archive.yml` proposes runs at :07 and :37 UTC every hour.
It downloads both public CW3E CSVs, independently of visits to the Rain data page:

- Surfrider: rolling 24 hours of 15-minute AQPI observations plus NBM forecasts.
- SFPUC-area: rolling seven days of hourly AQPI observations plus NBM forecasts.

Each complete HTTP 200 body is saved losslessly as gzip, including **all grid
rows, metadata and forecast columns**, not just the SF map or regional means.
The SHA-256 is over the uncompressed body. Changed bytes get a new object, even
when the run timestamp is unchanged; identical bytes reuse the original object.

The private bucket layout is:

```text
raw/<feed>/<first-two-hash-characters>/<sha256>.csv.gz
captures/<feed>/<YYYY>/<MM>/<DD>/<capture-id>.json
```

Every poll gets a receipt recording retrieval time, source URL, HTTP validators,
byte counts, checksum, validation state and (when parseable) run time, observed
intervals, forecast intervals and units. Receipt dates are **retrieval dates**;
observation times in each receipt are the data's UTC accumulation-ending times.
An unchanged feed therefore still has a record of when we checked it.
CW3E defines `run_time` as extraction time; it is not a verified NBM model
initialization time. Use retrieval time to establish when this archive first
had a forecast, and keep that separate from the forecast's valid interval.

The collector never overwrites or deletes stored objects. A receipt is uploaded
after its source object. On a duplicate response it reads and verifies the saved
bytes instead of assuming any HTTP error means success. Retrying a partial upload
repairs the missing receipt; replaying a completed capture is idempotent.

## Failures and monitoring

- Downloads and storage requests have bounded size/time and three attempts.
  Both feeds are attempted even if one fails.
- The local spool is written **before parsing or uploading**. Unexpected CSV
  schemas are preserved with an invalid-validation receipt; they fail the job.
  HTTP error bodies, empty responses and downloads exceeding 40 MiB are rejected.
- Observations older than six hours, timestamps more than 15 minutes in the
  future, or missing research-area observations also fail the job **after saving
  the evidence**. Six hours is a monitoring threshold, not a provider SLA.
- Storage failures leave the spool intact. On workflow failure, a GitHub recovery
  artifact retains that spool for **30 days**. It contains only public source data
  and receipts, never credentials. Restore it before it expires.
- Enable GitHub Actions failure notifications for the designated maintainer and
  inspect the latest successful run daily. A scheduler that stops entirely will
  not emit a failed run; use an independent dead-man monitor if unattended
  collection becomes operationally critical. None is silently provisioned here.

GitHub schedules can be delayed or dropped under load, and public repositories'
schedules can be disabled after 60 days without repository activity. Running away
from the top of the hour reduces one source of delay. The overlapping windows
give recovery headroom, but this is **not a guarantee of zero gaps**: an outage
longer than 24 hours can permanently lose 15-minute data. The seven-day hourly
feed cannot reconstruct those missing 15-minute values. Nor does half-hourly
polling guarantee capture of every intermediate provider revision.

## Reviewable activation steps — only after approval

1. Merge the reviewed PR only when all required reviews/checks pass, without any
   bypass. The `codex/` PR branch is not enabled for Vercel deployment in the
   current repository configuration. Merging `main` auto-deploys the web app, so
   merge approval must also explicitly cover that deployment.
2. In the existing BWTF Supabase project, create a **private** bucket named
   `aqpi-archive`, maximum file size **50 MiB**, allowed MIME types
   `application/gzip` and `application/json`. Do not add public read/write policies
   or lifecycle deletion. The collector verifies privacy and never creates or
   modifies a bucket. No database migration or new table is required.
3. Create GitHub environment `aqpi-archive`, restricted to `main`. Store
   `AQPI_ARCHIVE_SUPABASE_URL` (the HTTPS project origin) and
   `AQPI_ARCHIVE_SUPABASE_KEY` as **environment secrets**. Prefer a separately
   revocable server secret key; legacy service-role JWTs also work. These keys
   have broad project privileges, not bucket-only access. Keep them out of PR
   jobs, logs and files. Credentials are exposed only to the collector step.
4. With explicit activation approval, set repository variable
   `AQPI_ARCHIVE_ENABLED=true`. Without it the job is skipped, including manual
   dispatch. Trigger one manual run on `main`; inspect both feeds' receipts and
   download/decompress one raw object to verify its hash. Observe the next
   scheduled run and confirm that identical source content was not duplicated.
5. Set `AQPI_ARCHIVE_ENABLED=false` to stop future runs without deleting data.
   Cancel an in-progress run separately if needed. Do not remove branch protections
   or required reviews/checks during activation or maintenance.

No secrets or buckets were created by this implementation. A real Storage write
and hosted schedule test are deferred until activation approval; local tests use
a fake Storage service and actual public-feed downloads without uploading them.

## Local verification and recovery

Install `scripts/requirements-aqpi-archive.txt` in an isolated Python environment.
No `.env` is loaded; default operation has no remote writes:

```bash
python -m features.aqpi_archive.collector --output /tmp/aqpi-capture
python -m unittest discover -s tests -p test_aqpi_archive.py
```

After an approved activation, download and extract a failed run's recovery
artifact. Supply the archive environment secrets through your normal secure
environment mechanism, then replay (no provider requests):

```bash
python -m features.aqpi_archive.collector --output /path/to/extracted-spool --replay --upload
```

Successful replay means bytes and receipts are stored; it does not mean that an
originally invalid/stale CSV became valid. Inspect the original receipt warnings.
For analysis, deduplicate observations by product, coordinate and interval end;
keep forecast issue time separate from valid time. Never join by CSV row number.
The collector exposes no web routes and does not supply the viewer's cache.

Storage grows with distinct downloads; no automatic pruning is included. Measure
actual compressed bytes per distinct run and daily new objects after activation
before estimating retention cost. Monitor Storage quota and Actions usage.

## Earlier combined-branch verification (2026-11-25)

- 24 targeted tests pass (11 archive/recovery tests plus the Rain data and
  sample-gap checks); the archive tests were rerun after the final CLI edit.
- Local-only capture of both actual feeds passed validation: 96 and 168 observed
  intervals, each with 100 forecast intervals. Every raw file was decompressed
  and its SHA-256 matched the receipt. No cloud upload was performed.
- Workflow YAML, immutable action pins, read-only repository permissions,
  main-only/opt-in activation guards and disabled `codex/` Vercel previews checked.
- Full `scripts/check.sh`: **84 passed, 3 failed**, 87 test files, 559 seconds.
  `test_city_email_preview.py` expects the retired `/alerts/api/sfpuc-updates`
  route to return 401 rather than 404; the identical failure was reproduced in
  an untouched `origin/main` (`5235090`) source copy. `test_stages_build.py` and
  `test_stages_s1.py` retain the existing forecast source/artifact hash mismatch.
  All forecast files match the base branch; `test_served_golden.py` passes.
  These baseline failures are reported, not bypassed or silently repaired here.

## References

- [CW3E delivery guide](https://cw3e-datashare.ucsd.edu/AQPI/CW3E-AQPI_data-feed_quick-guide.pdf)
- [Supabase standard uploads and overwrite behavior](https://supabase.com/docs/guides/storage/uploads/standard-uploads)
- [Private bucket setup](https://supabase.com/docs/guides/storage/buckets/creating-buckets)
- [Supabase server API keys](https://supabase.com/docs/guides/api/api-keys)
- [GitHub schedule limits and notifications](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
