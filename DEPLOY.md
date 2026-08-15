# Deploying the dashboard

The app is a Flask + gunicorn web server (`app/wsgi.py`). It runs anywhere that
runs Python — these notes cover **Railway**, but Render / Fly / a VPS are the
same idea.

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

## Local

```bash
pip install -r requirements.txt
python -m app.wsgi              # dev server; reads $PORT (default 8080)
# or, exactly like prod:
gunicorn -w 1 --threads 8 -b 0.0.0.0:8090 app.wsgi:app
```

## Supabase (subscribers + alert state + delivery log)

| Var | Purpose |
|-----|---------|
| `SUPABASE_URL` | The dedicated BWTF Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Its service/secret key (server-side only; bypasses RLS) |

With these set, subscribers, watcher state, and the alert delivery log live in
Supabase (`db/migrations/`) and **survive redeploys**; without them the app
falls back to the legacy `data/` JSON files (fine for a bare dev checkout).
Local dev: put both in `.env` at the repo root (gitignored). ⚠️ A local
instance with `.env` shares the production database — set
`ALERT_WATCHER_INTERVAL=off` locally while developing so a second watcher
doesn't write state/logs alongside prod's.

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

- **One worker on purpose** (`-w 1`): the forecast engine runs a single background
  refresh thread; multiple workers would each spawn their own.
- **Memory**: the forecast feature (pandas + sklearn + models) is the heavy part.
  Railway's Hobby plan handles it; a 503 on `/forecast` at boot usually means OOM.
- **Build fails on sklearn?** Confirm `.python-version` is being respected — a
  too-new Python may lack a `scikit-learn==1.8.0` wheel.

## Verify after deploy

Hit `/`, `/alerts`, `/compare`, `/forecast`. The forecast page takes a few
seconds after boot to populate (the refresh thread runs once on startup).
