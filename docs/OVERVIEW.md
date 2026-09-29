# BWTF SF — what this repository is, and every data source it uses

*Written 2026-09-26. This is the map; the other documents are the territory:
[README](../README.md) (the original monitor and its standards), [features/forecast/README](../features/forecast/README.md)
(the forecast), [DEPLOY](../DEPLOY.md) (hosting, Supabase, the watcher), [features/forecast/RETRAIN_PLAN](../features/forecast/RETRAIN_PLAN.md)
(model history), [features/forecast/LIVE_COMPOSITION_DESIGN](../features/forecast/LIVE_COMPOSITION_DESIGN.md)
(how live CSO flags and samples should change the forecast), [features/forecast/STAGE2_V3_FINDINGS](../features/forecast/STAGE2_V3_FINDINGS.md)
(why the outfall split's shares could not be refit from the water — a negative result), [TODO](../TODO.md) (what is done and what is open), and the `NOTES.md` next to each dataset.*

## 1. What it is

A small public web app plus a database-side alert watcher, built for the **Surfrider Foundation San Francisco
chapter's Blue Water Task Force (BWTF)**, the volunteer program that samples SF beaches for bacteria. It answers
one question in several ways: **is the water at a San Francisco beach safe right now, and will it be tomorrow?**

- **Live:** https://bwtf-sf.vercel.app (the only host; `main` auto-deploys). Every page footer shows `build <sha>`;
  `/api/build` returns it as JSON.
- **Alerts:** subscribers pick beach zones and get one email when SFPUC posts a beach or reports an active
  combined-sewer discharge (CSO). Detection and sending run inside Supabase (Postgres, pg_cron), not in the web app.
- **Forecast:** a rain-driven machine-learning model predicts, for today and the next five days, the chance that each
  beach zone is fouled by a combined-sewer discharge — a warning *before* the posting.
- **Records:** public pages over the official datasets — every discharge SFPUC reported to regulators, every lab
  sample, every posting and CSO our watcher has seen, and Surfrider's own volunteer results.

Why it exists: San Francisco has a *combined* sewer. Heavy rain overwhelms the storage boxes and the treatment
plants, and the system discharges a mix of stormwater and sewage through 34 permitted outfalls onto Ocean Beach,
Baker/China, the north shore and the bay side. SFPUC posts warning signs after a discharge and when weekly samples
fail the state standard; this project makes that information timely, searchable and predictive.

## 2. How it runs

```
   browser ──► Vercel (Flask, app/wsgi.py) ──► feature packages (features/*)
                    │                              │
                    │ reads/writes                 │ reads
                    ▼                              ▼
             Supabase Postgres  ◄──────── external data sources (§4)
             subscribers · watcher state · alert_log · watcher_config · forecast cache
                    │
                    │ pg_cron every minute (db/migrations/002–004)
                    ▼
             poll SFPUC getBeaches → detect station transitions → email via Brevo (source='pg_live')
             healthchecks.io dead-man ping · forecast refresh every 30 min (+ on a real transition)
```

- **Web app** — one Flask application (`app/wsgi.py`) mounting eight feature packages. Serverless on Vercel
  (`vercel.json`, 60 s function limit); `Procfile` runs gunicorn for any conventional host. Static analyses under
  `reports/` are served at `/reports/<name>`.
- **State** — everything durable lives in Supabase (`db/migrations/001` → `015`): subscribers, the watcher's last-seen
  station states (`watcher_state_shadow`, in shadow and live mode alike), `alert_log` (every detection, dispatch and simulation, with provenance), `watcher_config`
  (key/value: mode, feed URL, keep-alive URL, `live_corrections`), the single-row forecast snapshot cache, and since 012 three
  history tables: `forecast_history` (the first and last snapshot of every day — the first is the start-of-day forecast the
  grading uses; every snapshot carries a `model` stamp: served set from `candidates.SERVED`, artifact version and training time, live-corrections version, input rules, build sha), `feed_station_days` (what the SFPUC feed showed per station per day, written by every tick), and `samples`
  (a mirror of the city's lab results stamped when we first saw them); since 013 also `forecast_changes` (the forecast every time
  it changed, with first_at / last_confirmed_at so the stretch between two rows is confirmed, not assumed) and `watcher_errors`
  (every tick error, 90-day retention); since 015 also `alert_deliveries` (one row per message sent — the rendered subject/body
  and Brevo's reply: `http_status`, `message_id`, `error`, read by its own per-minute pg_cron job; the /alerts Deliveries panel
  shows them; level 2 will add delivered / bounced from Brevo's events API). The production forecast recomputes on a clock — pg_cron every 30 min at :05 and :35, and
  immediately when the tick logs a real transition — and the page always serves the stored snapshot (a visitor computes only if
  the clock has been silent 3 h). Bacteria results otherwise never touch the database:
  pages read DataSF live and training reads the committed CSVs under `features/forecast/data/raw/` (in the repo since 2026-09-27; refreshed by the collectors before a retrain or rescore). Without Supabase credentials the app falls back to the legacy JSON files under
  `data/` and in-memory forecasts — fine for a bare checkout, never for production.
- **Watcher** — the detection→dispatch loop is SQL, run by pg_cron every minute and fetching the SFPUC feed with pg_net.
  It alerts only on transitions to a *worse* state (safe → posted, safe → CSO, posted → CSO), once per event, and logs
  clears since migration 006. Mode is `watcher_config.mode` (`shadow` | `live` | `off`). The former in-process Python
  thread and the Railway host were retired in 2026-08/09.
- **Forecast** — computed on visit: `/forecast/api/data` serves the cached snapshot while it is under 30 minutes old and
  recomputes otherwise (5–20 s), with a claim guard so concurrent visitors never double-compute.
- **Local dev** — `venv/bin/python -m app.wsgi` on port 8080. A local `.env` with Supabase keys shares the production
  database: set `ALERT_WATCHER_INTERVAL=off` and never POST a simulation to production (real subscribers receive it).

## 3. The pages

| Route | Page | What it shows | Data behind it |
|---|---|---|---|
| `/` | Dashboard (landing) | Live status hero (SFPUC summary, rain, tide) above three hubs — Today & alerts (signup button, CSO Forecast, Sewage Alert System), The water record (Site Report Card, Source Comparison, BWTF Sample Log), Postings & discharges (Discharge Ledger, Beach Postings, Online Postings Timeline) — each row carrying a live fact when its source answers within 2.5 s (`app/landing.py` HUBS / `_live_facts`), plus an Under the hood strip (model check, analysis, replays, build) | SFPUC feed, Supabase (forecast snapshot, samples mirror, alert_log), BWTF GraphQL, CIWQS csv |
| `/signup` | Get Beach Alerts | Public, zone-based email signup (four zones, bad-news-only alerts) | Supabase `subscribers` |
| `/alerts` | Sewage Alert System | Operator dashboard: live station status, bacteria, subscriber list, simulations, dispatch log. Behind a passphrase (`ALERTS_PASSPHRASE`) | SFPUC feed, DataSF, Supabase |
| `/forecast` | CSO Forecast | Zone risk today + 5 days, basin view, **What happened** (past days vs reality), **Model check** (scorecard over any window, per model set, any alarm line) | Rain sources, models, CIWQS labels, samples, alert_log |
| `/graphs` | Water Quality Graphs | Any site (tiles grouped by our four zones, Surfrider-only sites in theirs; Source = City / Surfrider / Both = only the shared beaches / All) over any window; the Bacteria select picks one indicator — city and Surfrider lines with same-day pairs when both exist — or all three on one log-scale plot with each state limit drawn in, or "All % threshold" = each sample day's worst indicator as a % of its own limit (dot colour names the indicator; 100% line); click any point, city or Surfrider, for that day's results in the sample popover (`/api/site-series`; drawn by `app/static/charts.js`, the one chart module, also used by the sample popover on `/alerts` and `/forecast`) | DataSF, BWTF GraphQL |
| `/compare` (subpage of Graphs) | Source Comparison | The original head to head for the beaches both programs sample: latest result per program, agreement, posting pill, bacteria selector; a site opens its history (two-source line + same-day bars, drawn by `charts.js` from `/api/site-series`) | BWTF GraphQL, DataSF, SFPUC feed |
| `/samples` (absorbs `/bwtf`, which now redirects to the Surfrider view with the field notes as columns) | Samples | Every published result from both programs, one row per sample (a double-sampled city day shows both values); latest-by-site strip; source / sites / range filters; Surfrider field notes hidden, expandable per row, or as columns (`/api/samples`, `/api/compare`, `/api/sample-day`) | DataSF, BWTF GraphQL, SFPUC feed |
| `/postings` | Beach Postings | Public, read-only: every advisory SF filed with the State since 1999 (BeachWatch) — posted station-days by year and cause, per-station table, recent postings, CSV download | `features/forecast/data/beachwatch/` (static repo file) |
| `/cso-history` | Online Postings Timeline (was Online Postings Timeline, before that CSO Event Timeline) | What the public could see, minute by minute: every sign our watcher saw go up and come down on SFPUC's map, per station; under each station its lab samples — collection day and, since 2026-09-27, how long until the result appeared online (the publish lag) | Supabase `alert_log`, `samples` |
| `/analysis` | Site Report Card | How often each site fails the state standard (Enterococcus by default; a Standard toggle regrades on fecal or total coliform, or on all three the way the city posts); storm-season effect; trends; the four AB 411 limits | DataSF |
| `/discharges` | Discharge Ledger | Every reported discharge since 2016: outfall, duration, million gallons | CIWQS records (`data/csd/`) |
| `/reports/<name>` | Analyses | Model explorers, stage 2 explorer, model analysis, live-corrections replays (archive feed, synthetic feed), leaderboard, training report | Static HTML under `reports/` |

## 4. Data sources — the complete list

### 4.1 Water quality and postings (what actually happened)

| Source | What it gives us | Coverage / cadence | Code | Stored | Used by |
|---|---|---|---|---|---|
| **SFPUC LIMS feed** `infrastructure.sfwater.org/lims.asmx/getBeaches` (the API behind SFPUC's beach map) | Per station right now: posted flag, active CSO flag, latest sample colour/date | Live; polled every minute by the watcher | `shared/sfpuc_api.py`, `db/migrations/002+` | Transitions in Supabase `alert_log` (since Aug 2026) | Watcher/alerts, landing banner, alerts page, forecast's observed-CSO override, CSO Event Timeline |
| **DataSF beach lab dataset** `v3fv-x3ux` (data.sf.gov; legacy `data.sfgov.org` host now 403s `$select` queries) | SFPUC's lab results: Enterococcus, E. coli/fecal and total coliform per station and sample date | 20 stations, roughly weekly, **Jul 2020 → today**, 1–2 day lag | `shared/datasf.py`, `features/forecast/src/collectors/historical.py` | `features/forecast/data/raw/historical_bacteria.csv` (training copy) | Site Report Card, Source Comparison, alerts page, forecast "elevated" labels and impact table |
| **Poo Bot archive** (John Brandon's `Beach_Poo_Bot`, 552 snapshots of SFPUC's `getCSV` feed) | Samples Dec 2015 → Jan 2017, plus each snapshot's POSTED stations and active CSO structures | **Mar 2016 → Jan 2017** | `collectors/poobot_archive.py` | `features/forecast/data/poobot/` (`samples.csv`, `feed_status.csv`, `discharge_onsets.csv`) | Forecast labels for 2016–17 (Westside stage 1 excludes them after ablation); the evidence behind the outfall → station mapping |
| **CIWQS self-monitoring reports** (SFPUC's monthly SMRs to the Regional Water Board, PDF attachments) | The official discharge record: outfall, date, start time, duration, volume (MG) per event | **Bayside Oct 2016 →, Westside Jan 2018 →** (continuous); refreshed quarterly; 1,104 events at 2026-09-24 | `collectors/csd_ciwqs/` (offline pipeline), `collectors/csd_labels.py` | `features/forecast/data/csd/sf_csd_events.csv`, coverage grid, legacy Bayside 2013–16 | Forecast training labels and scorecard "discharge" label, Discharge Ledger |
| **BeachWatch** (California State Water Resources Control Board, data.ca.gov `beach-water-quality-postings-and-closures`) | Every SF beach advisory county health filed with the State: station, posting date, reopening date, type (Posting / Rain / Closure), cause | **1999 → Feb 2026** (SF files months late); 2,142 SF station-advisories | `collectors/beachwatch.py --refresh`, `src/models/posting_label.py` | `features/forecast/data/beachwatch/` (`sf_beach_advisories.csv`, `sf_posted_zone_days.csv`, manifest) | The posting label: Model check "graded against beach postings", analysis report section |
| **Surfrider BWTF database** (AWS AppSync GraphQL behind bwtf.surfrider.org; SF chapter = lab 76) | Volunteer samples with tester, weather, tide, waves, comments | Chapter history | `features/comparison/bwtf_api.py` | Not stored | Source Comparison, BWTF Sample Log |
| **Our own alert_log** (Supabase) | Every station transition the watcher saw (posted / CSO / cleared), every dispatch, every simulation | **Aug 2026 →** (`pg_shadow` from 2026-08-16, `pg_live` after the flip) | `shared/alert_log.py`, `features/cso_history` | Supabase | CSO Event Timeline, forecast live override, "What happened" |
| **Our own history tables** (Supabase, migration 012) | `forecast_history`: first and last forecast snapshot per day; `feed_station_days`: the feed's flags, colours and classified status per station-day ("polled and saw nothing" included); `samples`: the city's lab results with the time we first saw each | **2026-09-27 →** (samples backfilled to 2020-07-27: 20,662 rows) | `features/forecast/page.py` (refresh), the tick (`bwtf_record_feed_day`), `shared/samples_mirror.py` | Supabase | Nothing reads them yet: the watcher-era replay of live_v1, the watcher's miss/lag rate vs CIWQS, the results' arrival lag |

### 4.2 Rain and weather (what drives the forecast)

| Source | What | Coverage | Code | Stored | Used by |
|---|---|---|---|---|---|
| **NOAA ACIS daily gauges** — SF Downtown `047772`, SF Oceanside `047767` | Daily precipitation totals; the series the models were trained on | 2016 → today (longer available) | `collectors/historical.py`, `live_dashboard._daily_frames` | `features/forecast/data/raw/historical_rain.csv` | Stage 1 features; past days re-based onto these gauges. The **gauge outage rule** (`rain_features.GAUGE_OUTAGE_RULE`) masks a gauge reporting exact 0.00 for ≥2 days while the other gauge records ≥0.5" |
| **Open-Meteo** (ECMWF IFS 0.25° via `api.open-meteo.com`) | Hourly forecast for the next 5 days; hourly archive for peak-intensity features | Live + archive | `live_dashboard.py` (`METEO_PARAMS`) | `data/raw/hourly_rain_openmeteo.csv` | Forecast days; `rain_max1h/3h/6h` features |
| **NWS** (`api.weather.gov`: KSFO observations, MTR gridpoint hourly forecast) | Recent hours observed; the rain advisory (avoid water contact 72 h after rain) | Live | `live_dashboard.py`, `shared/weather_tides.py` | — | Today's partial-day rain; alerts page advisory |
| **NOAA CO-OPS** station 9414290 | Tide predictions (and met observations in an exploratory collector) | Live | `shared/weather_tides.py`, `collectors/noaa_met.py` | — | Alerts page context |
| **CoCoRaHS via ACIS** (Potrero / Dogpatch gauge) | A third daily gauge on the east side, since 1998 | Historical | `collectors/cocorahs.py` | `data/raw/historical_rain_cocorahs.csv` | Model leaderboard only — tested and found a worse rain source |
| Exploratory, not in production | Weather Underground PWS scrapes, GFS/ICON model pulls, MesoWest | — | `collectors/wunderground.py`, `collectors/forecast_models.py`, `collectors/nws_rain.py` | — | Early research |

### 4.3 What each label covers, on one timeline

```
             2016   2017   2018   2019   2020   2021   2022   2023   2024   2025   2026
samples      ■■■■   ▪                    ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■   Poo Bot 2016 · DataSF Jul 2020 →
discharges          ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■   Bayside Oct 2016 → · Westside Jan 2018 →
postings     ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■     BeachWatch 1999 → Feb 2026
our flags    ▪▪▪                                                                    ■■■   Poo Bot POSTED/CSO 2016–17 · alert_log Aug 2026 →
rain         ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■   two NOAA gauges (+ ECMWF forward)
```

Three different truths: a **sample** is one bottle at one hour; a **discharge** is sewage reported entering the water on
a day; a **posting** is the official warning sign, up until a clean sample. They disagree in useful ways — postings are
precautionary, and the East is often dirty with no posting and no rain.

### 4.4 Registries — single sources of truth (never hand-type these)

| Module | Holds |
|---|---|
| `shared/stations.py` | The 20 SFPUC shoreline stations: lab `source` id, feed id, names, shoreline group, drainage basin, coordinates |
| `shared/outfalls.py` | The 34 permitted outfalls: CIWQS id, feed name, basin, and **which stations SFPUC posts when it fires** (evidence-tagged) |
| `shared/zones.py` | The four signup/forecast zones: Ocean Beach (6 stations), Baker & China (4), North Beaches (4), East Beaches (6) |
| `features/forecast/src/models/groups.py` | Basins (Westside, North Shore, Central, Southeast) ↔ beach groups ↔ zones, shared by training and serving |
| `shared/standards.py` | California AB 411 single-sample maxima (Enterococcus 104 MPN/100 mL, etc.) and the one "over standard" helper |

`tests/test_single_source.py` scans the tree for stray copies of these numbers and URLs.

## 5. The forecast in one page

- **Stage 1 — rain → discharge.** One model per basin predicts the probability that the basin's outfalls discharge
  today from 19 rain features (`rain_features.py`: daily totals, trailing sums, lags, antecedent moisture, peak
  1/3/6-hour intensity). The served set is **`gb_v1`** (gradient-boosted trees, trained 2026-09-12 on 2016-03 →
  2025-10, holdout from 2023-07). A weights model, **`logit_v1`**, is a candidate. A citywide model gives context.
- **Stage 2 — discharge → beach risk.** For each beach group, today's risk = 1 − Π over the last 8 days of
  (1 − p × x), where x is the impact table's chance the beach is still fouled k days after a discharge of that size
  (fit from samples; volume heads predict size). Zone risk = max over its groups. **v1** composes the basin probability
  directly; **v2** (the outfall split) first scales it by the share of the basin's discharges that reach the group's
  own outfalls. A set is named `<stage1>` or `<stage1>_s2v2`.
- **Live.** Past complete days use the two NOAA gauges (with the outage rule); today uses NWS hours so far plus the
  ECMWF forecast; forecast days use ECMWF. Live corrections (`live_v1`, `src/models/live_rules.py`, since 2026-09-26) then adjust the
  composition from what was observed: a watcher CSO onset sets that basin-day to 1 (and anchors the day before), an
  expected discharge the feed never flagged is downgraded by Bayes with a recall that grows with each quiet day after it
  (0.60 the morning after, 0.87, then 0.95; bayside only, while the watcher is ticking), a flag still up holds the beach at the large-event curve, and published samples cap (clean) the persistence term at
  empirical next-sample rates or floor it (elevated, only where the rate is ≥ 0.5 — today the East's tail floor alone). Every adjustment is recorded and shown as a badge; a page toggle shows the model alone, and the env var `LIVE_CORRECTIONS` or the `watcher_config` key `live_corrections` turns the layer off server-side. Not a third stage: edits before and after stage 2.
- **Evaluation.** `scorecard.json.gz` stores every day's probabilities and labels since 2016; the Model check grades
  any window at any line, per model set. Three rulers (2026-09-26): **discharge days + samples** is primary (a discharge day
  is bad, an elevated sample in the week after confirms persistence, a clean sample is good, unsampled tail days are not graded,
  dry-weather elevated samples are out of scope), discharge days only, and beach postings — a "graded against" selector
  switches the cards. Stage 1 alone is always graded on discharge days. `reports/2026-09_model_analysis.html` ranks the sets by cost (Chase's weighting: a miss costs two false
  alarms). Explorer pages open every set's arithmetic in the browser and self-check against the artifact.
- **Served set (from 2026-09-28): `logit_v1_s2v2`** = stage 1 `logit_v1` (L2 logistic regression with hinge terms on the 19 rain features) + stage 2 `v2` (the outfall split with its refit impact table), operating line 25%. Described once in `features/forecast/data/models/served.json`, written by `src/models/promote.py`; `candidates.SERVED` reads it and every page, explorer and report labels the served set from it. `gb_v1` (served 2026-09-12 → 2026-09-28) is a candidate now, still graded beside the others. Promotion = `promote.py <candidate> --line L`: the candidate's pickles, scorecard and stage2.json replace the served ones, the retired set moves to `candidates/<name>/`, then re-export explorers and the report and run `train_v4.py --rescore --replace-post` as the fidelity gate.
- **Nomenclature (2026-09-26, amended 2026-09-28):** stage 1 sets `gb_v1` (trees; pickles/dir still say "v4" — file names only) and `logit_v1` (weights); stage 2 `v1` (basin composition), `v2` (outfall split, map shares), `v3` (split, shares from the water — a recorded negative result, no candidate). A set is named `<stage1>_s2<variant>` unless it is stage 2 v1.
  release label "v4" survives only in file names (`train_v4.py`, `data/models/v4/`).

## 6. Operating it

| Task | How |
|---|---|
| Deploy | Push `main`; Vercel builds. Confirm with `curl -s https://bwtf-sf.vercel.app/api/build` (sha) |
| Run tests | Each file is standalone, no pytest: `venv/bin/python tests/<file>.py` — `test_scorecard_window` (forecast + explorers), `test_beachwatch`, `test_build_info`, `test_feature_parity`, `test_forecast_geometry`, `test_outfalls`, `test_rain_overlay`, `test_single_source`, `test_stations` |
| Refresh discharge records | Quarterly: the CIWQS pipeline (`collectors/csd_ciwqs/README.md`), then `train_v4.py --rescore --promote` adds the new months to the Model check as post-training days (no retraining) |
| Refresh postings | `collectors/beachwatch.py --refresh`, then `tests/test_beachwatch.py` |
| Grade the live corrections | `venv/bin/python features/forecast/src/models/replay_live.py` → `reports/2026-09_live_replay.html` (the 2016-17 real feed) and `--synthetic` → `reports/2026-09_live_replay_synthetic.html` (every out-of-sample day, feed built from the filed discharges and degraded); re-run after a rescore (the watcher era joins once the artifact extends past Aug 2026) |
| Retrain / new candidates | `RETRAIN_PLAN.md`; candidates via `leaderboard.py`, `candidates.py`, `stage2_variants.py`; regenerate explorers (`export_model_explorer.py [--model NAME]`, `export_stage2_explorer.py`) and the report (`report_models.py`) after any rescore |
| Database changes | `db/migrations/NNN_*.sql`, applied by hand in Supabase with no simulation active |
| Secrets | Env vars only (`DEPLOY.md`): Supabase URL/key, Brevo, `ALERTS_PASSPHRASE`, healthchecks URL. Nothing in the repo |

## 6a. Supabase: every table, function and job (as of migration 013, 2026-09-27)

All in the `public` schema, row-level security on, readable only with the service key. `db/migrations/001` → `013`; applied
by hand in the SQL editor with no simulation active.

| Table | One row per | Written by | Read by |
|---|---|---|---|
| `subscribers` | signup (email or phone + carrier, station list, zone, active) | `/signup`, the `/alerts` operator page | the SQL dispatcher when a transition matches |
| `watcher_state_shadow` | station: last-seen status (ok / posted / cso) and the simulation overlay forced last tick | every tick (`bwtf_process_payload`) | the same, to diff this tick against the last. The live state in BOTH modes; the name is a leftover |
| `alert_log` | transition seen (posted / cso / cleared), dispatch (with Brevo results), or simulation — with source and a simulated flag | the tick; manual dispatches | CSO Event Timeline, the forecast's observed-CSO onsets and flag windows, "What happened" |
| `watcher_config` | key/value: `mode`, `sfpuc_url`, `forecast_refresh_url`, `healthchecks_ping_url`, `alert_from_email`, `brevo_api_key`, `live_corrections`, `keepalive_url` (unused since 013) | hand edits, the operator page | every tick; the forecast's switch and health gate |
| `watcher_runtime` | one row: last request id, last fetch status, last processed time, tick summary, last error | every tick | the forecast's watcher-health gate |
| `simulated_cso` | active simulation: station, kind (cso / posted), optional recipient list | `/alerts` simulation controls | the tick, as an overlay on the real feed |
| `forecast_predictions` | one row: the current forecast snapshot (JSON), generated time, refresh claim | the production refresh | `/forecast/api/data` — every page load |
| `forecast_history` (012) | Pacific day: the day's first snapshot (start-of-day forecast) and its last | the production refresh (rpc `bwtf_record_forecast`) | nothing yet — the watcher-era replay of live_v1 |
| `feed_station_days` (012) | station-day: raw posted / CSO flags, colours, worst and last classified status, raw station object, tick count | every tick (`bwtf_record_feed_day`) | nothing yet — the watcher's miss/lag rate vs CIWQS |
| `samples` (012) | lab result (station, date, analyte, raw value) with `first_seen_at` and `source` (refresh = real arrival time, backfill = load day; 014); view `samples_lag` | the production refresh (`shared/samples_mirror`) and `--backfill` | nothing yet — results' arrival lag; a backstop if DataSF is down |
| `forecast_changes` (013) | distinct forecast: fingerprint, `first_at`, `last_confirmed_at`, refreshes that produced it | the production refresh (rpc `bwtf_record_forecast_change`) | nothing yet — the intra-day trajectory |
| `watcher_errors` (013) | tick error: kind, message, status; 90-day retention | the tick (`bwtf_log_error`) | operators |

Dropped: `watcher_state` (phase 1, 012). **Bacteria results otherwise never touch the database**: pages read DataSF live; training reads
the committed CSVs under `features/forecast/data/raw/`.

Functions: `bwtf_classify` (feed flags → status), `bwtf_process_payload` (diff, overlay, transitions), `bwtf_shadow_tick` (harvest the
last fetch, process, record feed day, trigger a refresh on a real transition, issue the next fetch), `bwtf_dispatch_live` /
`bwtf_render_alert` / `bwtf_log_escalations` / `bwtf_log_downgrades` / `bwtf_sms_gateway` (alerts), `bwtf_record_forecast`,
`bwtf_record_feed_day`, `bwtf_record_forecast_change`, `bwtf_log_error`, `bwtf_forecast_refresh`, `bwtf_keepalive` (defined,
unscheduled), `set_updated_at`.

Cron: `bwtf-shadow-tick` every minute; `bwtf-forecast-refresh` at :05 and :35 (→ `/forecast/api/refresh`, 55 s timeout).

## 7. Known gaps and cautions

- Westside per-event discharge records do not exist before 2018 (SFPUC reported monthly counts only); a records
  request draft is in `data/csd/records_request_draft.md`.
- Bacteria labels have a hole from mid-2017 to mid-2020; BeachWatch postings cover it.
- BeachWatch lags: the county's last filing was Feb 2026 at the Sept 2026 pull. Our watcher covers Aug 2026 onward;
  Mar–Jul 2026 has no posting label.
- The SFPUC feed is undocumented and internal; the watcher's dead-man ping is what tells us if it changes shape.
- The root `README.md`'s notification, scheduling and architecture sections describe the original command-line monitor
  and its Slack/Discord/SMS channels; production alerting is the pg_cron watcher + Brevo email described above.
