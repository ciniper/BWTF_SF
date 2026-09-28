# Deploying the dashboard

**Current hosting (since 2026-09-02): Vercel only** — `bwtf-sf.vercel.app`,
auto-deploying `main`. The Railway deployment was retired 2026-09-02 after a
two-week parallel run (its last jobs — the observer thread's keep-alive reads
and the subscriber JSON mirror — were handed to the hourly pg keep-alive ping
and accepted as a pre-launch gap respectively). The Railway/gunicorn sections
below are kept for reference: the app still runs anywhere Python runs.

The app is a Flask + gunicorn web server (`app/wsgi.py`). It runs anywhere that
runs Python — these notes cover **Railway** (retired), but Render / Fly / a VPS
are the same idea.

## What's already in the repo for hosting

- `requirements.txt` — all deps (Flask, gunicorn, scikit-learn, pandas, …).
- `Procfile` — the start command: `gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:$PORT app.wsgi:app`.
- `.python-version` — pins Python `3.12` so the build is reproducible (the
  forecast models need `scikit-learn==1.8.0`, which has wheels there).
- The trained forecast models (`features/forecast/data/models/*.pkl` +
  `thresholds.json`) are committed, so `/forecast` works on a fresh clone.
- The server binds `0.0.0.0:$PORT` — hosts inject `$PORT` automatically.

## Railway — from GitHub (auto-deploys on push)

1. Push the repo to GitHub.
2. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → pick the repo.
3. Railway auto-detects Python (Nixpacks), runs `pip install -r requirements.txt`,
   and uses the `Procfile`. First build takes a few minutes (scipy/sklearn/pandas).
4. Open the **service** → **Settings → Networking** → **Generate Domain**
   (it's per-service, not in project settings) → you get a `*.up.railway.app` URL.
   Watch the spelling of the generated subdomain.

## Railway — from the CLI (no GitHub needed)

```bash
npm i -g @railway/cli
railway login
railway init
railway up          # uploads + builds + deploys the current dir
railway domain      # generate the public URL
```

## Vercel (Phase 3 — serverless)

The same repo deploys to Vercel with **no shim**: Vercel auto-detects the
Flask `app` instance at `app/wsgi.py` (a supported entrypoint location),
respects `.python-version` (3.12), installs `requirements.txt`, and runs the
whole app as one Fluid function. `vercel.json` sets `maxDuration: 60` so a
compute-on-visit forecast refresh (~5–20 s) never gets cut off. The ~310 MB
dependency bundle fits the 500 MB Python limit (raised from 250 MB in
Feb 2026).

Setup: vercel.com → **Add New Project** → import the GitHub repo → set the
env vars below → Deploy. Pushes to `main` then auto-deploy (same as Railway).

| Env var (Vercel dashboard) | Why |
|-----|-----|
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Everything stateful lives in Supabase |
| `FLASK_SECRET_KEY` | **Required here** (any long random string): serverless instances each generate a random key otherwise, so the alerts passphrase cookie would bounce between instances |
| `BREVO_API_KEY`, `ALERT_FROM_EMAIL` | Only for manual sends from the /alerts simulator — automatic alerts are dispatched by Postgres, not the web app |
| `ALERTS_PASSPHRASE`, `ALERT_FROM_NAME` | Optional overrides, same as Railway |

What deliberately does **not** run on Vercel:

- **The watcher thread** — `start_watcher()` sees Vercel's built-in `VERCEL`
  env var and refuses to start (no persistent process; pg_cron is the sender).
  Since 2026-09-12 it is opt-in everywhere: it only runs when
  `ALERT_WATCHER_INTERVAL=<seconds>` is set, so a local checkout no longer
  double-logs detections beside pg_live.
- **Forecast cache writes from anywhere else** — the compute-on-visit
  snapshot (`forecast_predictions`, one row) is WRITTEN only where
  `VERCEL_ENV=production` (set by Vercel) or `FORECAST_CACHE_WRITE=1`. Any
  other host with the Supabase key reads the row but computes in memory when
  it is stale, so a laptop on old code can't publish its forecast to visitors
  (it did, 2026-09-09). `db/scripts/test_forecast_cache.py` sets the flag itself.
- **The subscriber JSON mirror** — the filesystem is read-only, so the
  best-effort mirror silently skips. Railway keeps mirroring while it runs;
  once Railway retires, the warm backup is gone until the Pro-org transfer
  (acceptable pre-launch with test accounts only).

Transition: DONE. Vercel ran alongside Railway 2026-08-20 → 09-02; the final
comparator run was clean (including a real event on 2026-09-01 handled
end-to-end), `watcher_config.keepalive_url` now points the hourly pg cron at
`/forecast/api/data` (Supabase gateway activity + keeps the forecast warm —
verified firing), and Railway was deleted. `thread_shadow` rows ended with it.

## Local

```bash
pip install -r requirements.txt
python -m app.wsgi              # dev server; reads $PORT (default 8080)

# Which build is live? Footers show `build <sha>`; the JSON is one curl:
curl -s https://bwtf-sf.vercel.app/api/build
# (sha comes from Vercel's VERCEL_GIT_COMMIT_SHA — needs "Automatically expose System
#  Environment Variables" on in the project settings; locally it comes from git, '+' = uncommitted changes)
# or, exactly like prod:
gunicorn -w 1 --threads 8 -b 0.0.0.0:8090 app.wsgi:app
```

## Supabase (subscribers + alert state + delivery log)

| Var | Purpose |
|-----|---------|
| `SUPABASE_URL` | The dedicated BWTF Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Its service/secret key (server-side only; bypasses RLS) |

With these set, subscribers, watcher state, the alert delivery log, and the
forecast snapshot cache live in Supabase (`db/migrations/`) and **survive
redeploys**; without them the app falls back to the legacy `data/` JSON files
and in-memory forecasts (fine for a bare dev checkout).
Local dev: put both in `.env` at the repo root (gitignored). ⚠️ A local
instance with `.env` shares the production database — set
`ALERT_WATCHER_INTERVAL=off` locally while developing so a second watcher
doesn't write state/logs alongside prod's.

## Phase 2: pg shadow watcher (pg_cron + pg_net)

`db/migrations/002_phase2_shadow_watcher.sql` runs the poll→detect loop inside
Postgres every minute in **shadow mode** — it logs would-send decisions to
`alert_log` (`source='pg_shadow'`) and sends nothing; the in-process watcher
keeps sending during the parallel run. Controlled by `watcher_config.mode`
(`shadow` | `live` | `off`). The dead-man's switch pings
`watcher_config.healthchecks_ping_url` only after a fresh SFPUC payload is
parsed and processed, so a dead cron, broken feed, or schema change all stop
the pings and healthchecks.io emails.

Scripts: `db/scripts/setup_phase2.py` (seed ping URL from `.env`
`HEALTHCHECKS_PING_URL` + verify ticking) · `test_phase2_shadow.py`
(behavioral suite + live thread-vs-pg parity) · `compare_shadow.py`
(parallel-run log diff — handles both eras) · `setup_live_flip.py`
(seed Brevo config for pg dispatch + flip-readiness checklist).

**Role reversal (pg sends, thread shadows)** — after `004`:
`watcher_config` gains `brevo_api_key`/`alert_from_email` (seeded from
`.env` by `setup_live_flip.py`). Flip order: set Railway env
`ALERT_WATCHER_MODE=observe` (thread detects + logs `thread_shadow`,
sends nothing, keeps its 2-min reads as the free-tier keep-alive) →
redeploy → set `watcher_config.mode='live'` → pg dispatches via Brevo
(`source='pg_live'`; missing config logs `channel='config_missing'`
rather than dropping the event). Rollback = `mode='shadow'` + remove
the env var. An hourly `bwtf-keepalive` cron pings
`watcher_config.keepalive_url` when set (only needed if the observer
thread ever retires).

## Forecast: compute-on-visit (no thread, serverless-ready)

After `db/migrations/005_forecast_predictions.sql`, predictions live in a
single-row Supabase cache. `/forecast/api/data` serves the stored snapshot
while it's **younger than 30 min**; the visit that finds it stale recomputes
(~5–20 s) and stores. `refresh_started_at` is a claim guard so concurrent
visitors never double-compute — losers serve the stale snapshot with a
`refreshing` flag. A failed refresh never overwrites a good snapshot. There is
no background refresh thread anymore, which is what makes the app fit
serverless hosts (Vercel). If scheduled freshness is ever wanted, point a
pg_cron `net.http_get` at `/forecast/api/refresh` — no new entities.
Verify: `venv/bin/python db/scripts/test_forecast_cache.py` (needs 005 applied).

## Optional env vars (only to actually send alerts)

| Var | Purpose |
|-----|---------|
| `BREVO_API_KEY`, `ALERT_FROM_EMAIL` | **Preferred email path** — Brevo's HTTPS API (port 443, works on every host). `ALERT_FROM_EMAIL` must be a sender verified in the Brevo account. Also powers the free email-to-SMS gateway. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Email via SMTP — fallback only. **Railway blocks outbound SMTP on all plans below Pro**, so use Brevo there. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | SMS via Twilio (paid, more reliable than carrier gateways) |
| `ALERT_WATCHER_INTERVAL` | Auto-alert poll interval in seconds (default `120`; `off` disables the watcher) |
| `ALERT_FROM_NAME` | Display name on alert emails (default "SF BWTF Alerts") |
| `ALERTS_PASSPHRASE` | Passphrase gating the /alerts page + its write APIs (default `snowy plover` — override it if the repo is public, since the default is in source) |
| `FLASK_SECRET_KEY` | Signs the "alerts unlocked" session cookie. Optional: without it a random key is generated per boot, so everyone re-enters the passphrase after each deploy. |

Without the SMTP/Twilio creds the app runs fine — dispatches (manual *and* the
automatic watcher's) are recorded as previews instead of sending. The watcher
auto-emails/texts subscribers when a site is newly posted or has a CSO; check
its last run at `/api/watcher`. Its state file lives in `data/` (ephemeral on
PaaS — each deploy silently re-baselines, never re-alerts standing events).

## Gotchas

- **One worker on purpose** (`-w 1`): the alert watcher runs as a single
  background thread; multiple workers would each spawn their own. (The forecast
  refresh thread is gone — see compute-on-visit above.)
- **Memory**: the forecast feature (pandas + sklearn + models) is the heavy part.
  Railway's Hobby plan handles it; a 503 on `/forecast` at boot usually means OOM.
- **Build fails on sklearn?** Confirm `.python-version` is being respected — a
  too-new Python may lack a `scikit-learn==1.8.0` wheel.

## Verify after deploy

Hit `/`, `/alerts`, `/compare`, `/forecast`. The first `/forecast` visit after
a deploy (or after 30 quiet minutes) takes ~5–20 s while that visit recomputes;
subsequent visits serve the stored snapshot instantly.

## 012: history tables (2026-09-27, applied the same day; samples backfilled)

`db/migrations/012_history_tables.sql` — apply by hand in the Supabase SQL
editor with no simulation active. It drops `watcher_state` (the phase-1 Python
watcher's table; nothing reads it) and adds three tables nothing depends on
until they exist — the Python writers catch a missing table and log it:

| Table | Written by | One row per |
|---|---|---|
| `forecast_history` | the production forecast refresh (`features/forecast/page.py` → rpc `bwtf_record_forecast`) | Pacific day: the day's first snapshot (the start-of-day forecast) and its last |
| `feed_station_days` | every watcher tick (`bwtf_shadow_tick` → `bwtf_record_feed_day`) | station-day: raw posted / CSO flags, colours, worst and last classified status, the raw station object |
| `samples` | the production refresh (`shared/samples_mirror.mirror`) and the backfill below | lab result (station, date, analyte, raw value), stamped `first_seen_at` |

After applying, backfill the samples mirror once from this laptop (DataSF from 2020-07-27, DO NOTHING on rows already there):

```bash
venv/bin/python -m shared.samples_mirror --backfill
```

`watcher_config` also carries the row `live_corrections` (`on` / `off`): the
forecast's live-corrections layer, flippable without a deploy; the env var
`LIVE_CORRECTIONS` on Vercel wins over it.

## 013: constant forecast cadence, capture on change, tick error log (2026-09-27)

`db/migrations/013_forecast_cadence_and_error_log.sql` — apply by hand with no
simulation active. What changes:

| | Before | After |
|---|---|---|
| Forecast recompute | a visitor who found the snapshot > 30 min old computed it (5–20 s spinner); hourly `bwtf-keepalive` ping | pg_cron `bwtf-forecast-refresh` at :05 and :35 hits `watcher_config.forecast_refresh_url` (`/forecast/api/refresh`, 55 s pg_net timeout); the tick also fires it when a REAL transition is logged. `bwtf-keepalive` unscheduled; `keepalive_url` row unused (delete at will) |
| Page load | served stored if < 30 min, else computed | always served stored (stale note past 45 min; compute-on-visit only if the clock has been silent 3 h) |
| Forecast capture | `forecast_history` first + last per day (012) | plus `forecast_changes`: a row every time the fingerprint (zone/basin %, rules fired, rain to 0.05", feed statuses) changes; unchanged refreshes bump `last_confirmed_at` / `refreshes` |
| Tick errors | only `watcher_runtime.last_error` (the last one) | every error in `watcher_errors` (kind, message, status), pruned at 90 days |

Check after applying: `watcher_errors` empty or explaining itself; `forecast_changes` gains a row at the first refresh after apply; `select * from cron.job` shows `bwtf-forecast-refresh` and no `bwtf-keepalive`.

## 014: samples.source (2026-09-27)

`db/migrations/014_samples_source.sql` — `samples.source` = `refresh` (the
production refresh picked the row up as the city published it; `first_seen_at`
is a real arrival time) or `backfill` (historical load; the stamp is the load
day). The 2026-09-27 backfill rows are relabelled by timestamp. `samples_lag`
is the view to read lags from: refresh rows only, `lag_days` = Pacific day
first seen minus the collection day. Compare its median with the one-day lag
the live-corrections rules assume (`live_rules.RULES["samples"]["known_lag_days"]`).
