# Project TODO & Roadmap

This roadmap has four parts:

- **A. Overall project management** — the umbrella for organizing this and future projects, including a shared dashboard and a common structure every project plugs into.
- **B. Sewage alert system** — the BWTF real-time CSO / sewage-dumping text-alert system.
- **C. Data comparison dashboard** — Surfrider BWTF vs. public city data.
- **D. SF sewage forecaster** — machine-learning forecast of CSO risk from rainfall.

---

# A. Overall project management

## Shipped — infrastructure overhaul (one repo · feature packages · landing page)
- [x] Brought the forecaster (was a separate repo) into this repo under `features/forecast/`.
- [x] Restructured into `app/` (Flask server + landing) · `features/{alerts,forecast,comparison}/` · `shared/` (sfpuc_api, weather_tides, paths) so the three pages are cleanly separated for collaboration. Run with `python -m app.wsgi` (gunicorn in prod).
- [x] Landing page (`/`) shows current conditions + overall CSO status with buttons to the alert, forecast, and comparison pages — a within-repo version of the A2 central dashboard.
- [x] Forecast is lazy-loaded with graceful degradation: the alert & comparison pages run without the ML deps installed.
- [x] **Web layer → Flask + gunicorn (deployable).** Retired the stdlib `http.server`; `app/wsgi.py` is the single entrypoint (`Procfile`: `gunicorn -w 1 --threads 8 … app.wsgi:app`). A thin adapter subclasses the existing alert/comparison route mixins so all page + API logic runs unchanged; one worker keeps the forecast refresh thread singular. Reads `$PORT` for PaaS hosts.
- [x] **HTML → Jinja2 templates** (`app/templates/`). All four pages render from templates instead of inline strings: `landing.html`, `alerts/dashboard.html`, `comparison/page.html`, and `forecast/page.html` — removes the `{{ }}` brace-escaping footguns. The forecast shell was extracted from the engine's `HTML_TEMPLATE` (with the `/forecast/api` namespacing + back button baked in); `live_dashboard.py` itself is left untouched, so the forecaster's standalone mode still works. Each page verified byte-identical to its prior output.
- [ ] A1/A2 below remain for the *cross-repo* portfolio (a registry + aggregator spanning multiple projects).

### A1. Establish a multi-project structure
- [ ] Define a standard template every project follows: repo layout, README, a shared status/health JSON contract, branding kit, and hosting conventions — so each new project is consistent and pluggable into the central dashboard.
- [ ] Keep a registry / index of projects (name, repo, owner, status endpoint, health).
- [ ] Decide umbrella hosting + identity (shared domain, auth, branding).
- [ ] Document how a new project gets added to the portfolio.

### A2. Central / multi-project dashboard
- [ ] Decide what "central" means: a Surfrider-wide public aggregator vs. an internal portfolio view. (Drives auth, branding, hosting.)
- [ ] Integration contract: the dashboard consumes each project's status API (the sewage app already exposes `/api/status`, `/api/alerts`, `/api/realtime`, `/api/weather`), or projects publish events to a shared bus/webhook.
- [ ] Provide a stable, versioned, documented API if external consumers exist.
- [ ] Apply the shared branding/identity kit from A1.

---

# B. Sewage alert system

**Goal:** A reliable, real-time **text-alert** system for combined-sewer-overflow (CSO / sewage dumping) events in San Francisco, built for the Surfrider SF Blue Water Task Force.

## Snapshot — where it stands today

- **Detection works.** `core/sfpuc_api.py` pulls live station status + CSO flags from SFPUC's internal LIMS API; `core/monitoring.py` adds SF Gov bacteria data + AB-411 logic; `core/weather_tides.py` adds rain/tide context.
- **Channels exist.** `core/notifiers.py` supports Twilio SMS, free email-to-SMS gateways, SMTP email, Slack, Discord, and console.
- **Subscriptions work per-site.** `core/subscriptions.py` + `data/subscriptions.json`, with targeted dispatch in `core/cso_alerts.py`.
- **Dashboard runs locally.** `dashboard.py` serves `localhost:8080` via Python's single-threaded stdlib server; HTML is generated inline (~1,695 lines).
- **Test harness exists.** Simulated CSO events (`SimulatedCSOStore`) let you fire fake events without waiting for a real one.

**Central gap:** detection and dispatch are **manual** — a real CSO only goes out when someone clicks the dashboard button or runs `run_subscription_alerts.py` by hand. Nothing polls SFPUC on a schedule, remembers what it already sent, or runs unattended. State is local JSON files, and there are no tests. Most items below build toward closing that loop.

## From your list

### B1. Hosting — local vs. online
- [ ] Pick a target model. "Real-time" implies something running unattended 24/7 — local-only can't do that. Options: always-on cloud (Fly.io / Render / Railway / small VPS) **or** a scheduled job + lightweight hosted dashboard.
- [ ] Replace the stdlib `TCPServer` (`dashboard.py:1687`) with a production server (gunicorn/uvicorn behind a WSGI/ASGI app, or at minimum `ThreadingHTTPServer`). It currently handles one request at a time.
- [ ] Move secrets off-box: `token.txt` and SMTP/Twilio creds are file/env-based today — use the host's secret store, keep them out of the repo.
- [ ] Move state off the local filesystem (see B8) — most hosts have ephemeral disks, so `data/*.json` won't survive deploys/restarts.
- [ ] Add a health-check endpoint + auto-restart.

### B2. Better dashboard UI
- [x] ~~Break up the ~1,695-line `dashboard.py`~~ — done via the feature-package restructure + Jinja2 templates (`app/templates/`).
- [ ] In-dashboard lab-results view instead of opening the raw SF Gov query in a new tab. *(carried from prior TODO)*
- [ ] Add a real map view — station lat/lon is already in the data — so CSO/posted sites are visual.
- [ ] Subscriber self-service (edit/unsubscribe) in the UI *(see B4)*.
- [ ] Mobile-friendly layout with a clear "current status" hero state.

### B3. Validation of locations & alert logic ("the product")
- [ ] **Region-based alert areas** — let subscribers pick regions (e.g. Ocean Beach, Baker/China, Aquatic Park–Bayside) instead of individual stations: group the ~20 stations into named zones, alert when *any* station in a subscribed zone goes problematic, and show the zone in the alert copy. Keeps the subscribe form simple as the station roster grows, and matches how people actually think about beaches. (Longer-term; per-site stays as an "advanced" option.)
- [ ] Resolve the open mystery: the SFPUC map only draws CSO triangles for ~13 station IDs — confirm whether that's a map/coordinate limit or a deeper data/model constraint. *(carried from prior TODO)* This directly bounds which sites can ever fire an alert.
- [ ] Validate the hand-built mappings — `CSO_OUTFALLS` / `BEACH_CSO_OUTFALLS` (`sfpuc_api.py`) and `SFPUC_TO_SFGOV_SOURCES` (`monitoring.py`) are explicitly "best-effort." Check each beach → outfall → drainage-basin link against an authoritative SFPUC / EPA NPDES source.
- [ ] Verify the station roster & names against the current SFPUC list (code targets a 2025 snapshot).
- [ ] Ground-truth the CSO detection rule (`_detect_cso` keys off a populated `cso` field) against a few known historical events.
- [ ] Add a schema/format guard on the SFPUC LIMS feed (undocumented internal API, JSON-wrapped-in-XML) — see B9.

### B4. Text-messaging hosts & connectors
- [ ] Choose the primary SMS path: **Twilio** (reliable, paid, but now requires **A2P 10DLC** brand/campaign registration for US app-to-person SMS) vs. the **free email-to-SMS gateways** already coded (`EmailToSMSNotifier`), which are increasingly **deprecated/unreliable** (e.g., Verizon's `vtext` inbound has been curtailed) — fine for testing, risky for production.
- [ ] Add consent & compliance: explicit opt-in capture, **STOP/UNSUBSCRIBE** handling, and a record of consent (TCPA). The subscribe form currently takes a number with no opt-in flow.
- [ ] Unsubscribe/edit controls for saved phone subscriptions. *(carried from prior TODO)*
- [ ] Workshop alert copy across SMS / email / dashboard so it reads naturally and explains preview/failure states in plain English. *(carried from prior TODO)*
- [ ] Keep a provider abstraction so Twilio / gateway / future providers swap without touching dispatch logic.
- [ ] **Outgrow Gmail SMTP when the list grows** — the sending account is a plain Gmail, capped at ~500 recipients/day (and email-to-SMS texts count against it). If the subscriber list gets big, switch to a transactional email provider (Resend / Brevo / SES etc.): same `SMTP_*` env vars, different values, plus real deliverability (SPF/DKIM) and send logs.

## Additional suggestions

### B5. Detection → dispatch automation — *the real-time core*
- [x] **Shipped: the alert watcher** (`features/alerts/watcher.py`). A daemon thread in the web app polls SFPUC every 2 min (`ALERT_WATCHER_INTERVAL` overrides; `off` disables) and auto-dispatches email + SMS to subscribers of affected stations. Alerts fire for **both bacteria postings and CSO discharges**, labeled by type; subscriptions now cover all monitored sites (not just CSO-eligible). Run status at `/api/watcher`. Simulated CSO events flow through the same path (TEST-prefixed) — that's the end-to-end test. Needs `SMTP_USERNAME`/`SMTP_PASSWORD` (and optionally Twilio) set on the host to actually send; otherwise deliveries are recorded as previews.

### B6. Alert state, edge-triggering & dedup
- [x] **Shipped with the watcher**: last-known state persists in `data/alert_watcher_state.json`; alerts fire only on transitions to a *more severe* state (safe→posted, safe→CSO, posted→CSO), once per event. Never-seen stations (first boot / fresh deploy — the state file is ephemeral on PaaS) baseline silently, so a deploy can't re-alert standing conditions.
- [ ] Add throttling / quiet hours, and an explicit "all-clear" message when an event ends (recoveries currently just update state silently).

### B7. Delivery log & uptime/observability
- [ ] Persist delivery history — which alerts were sent / previewed / failed, per recipient and channel. *(carried from prior TODO)*
- [ ] Add a heartbeat / dead-man's switch: a silent failure in a *safety* alert system is the worst case — page an admin if the poller stops or the SFPUC feed breaks.

### B8. Data persistence & secrets/config
- [ ] Move subscriptions + delivery log + alert state from JSON files to a real store (SQLite locally → hosted Postgres in cloud). JSON files aren't concurrency-safe and don't survive ephemeral hosts.
- [ ] Centralize config/secrets (env + secret manager); get `token.txt` and creds out of the working tree.

### B9. Tests & upstream resilience
- [ ] Add a test suite (there are none today): unit tests for parsing / AB-411 standards / dedup logic, plus recorded-fixture tests for SFPUC / SF-Gov / NWS / NOAA responses.
- [ ] Fail loudly when an upstream feed changes shape (ties to B3 validation and B7 observability).

### B10. Public-site hardening (only if it goes public)
- [ ] Spam / rate-limit protection and basic abuse controls on the subscribe form.
- [ ] Privacy policy for storing emails and phone numbers.

## Open decisions (these shape the sewage-alert work above)

1. **Always-on vs. scheduled?** Real-time texting really wants an unattended 24/7 runner.
2. **SMS provider:** pay for Twilio (compliant, reliable) or stay on free gateways (cheap, fragile)? Roughly how many subscribers do you expect?
3. **Audience:** public-facing site or internal BWTF/volunteer tool? (Also drives A2, B4 compliance, B10.)
4. **Budget / effort ceiling** for hosting + SMS.

---

# C. Data comparison dashboard

**Goal:** Compare water-quality results for the same SF beaches between the two independent monitoring programs — the **Surfrider Blue Water Task Force** volunteer lab and **public city data** (SF Gov Open Data + SFPUC) — to build trust, surface divergence, and tell a public story about beach safety.

## Shipped (v1)
- `features/comparison/bwtf_api.py` — client for the public BWTF database (AWS AppSync GraphQL; SF chapter = **lab 76**). Pulls each site's latest Enterococcus result + CA thresholds, and exposes a historical series (`fetch_history`).
- `features/comparison/comparison.py` — pairs each BWTF site with its city counterpart (reusing `SFPUC_TO_SFGOV_SOURCES`), grades both against the CA single-sample max (104 MPN/100mL), and computes agreement, value delta, and sampling-day gap.
- `features/comparison/page.py` — `/compare` page + `/api/compare` + `/api/site-history`, served by the Flask app (`app/wsgi.py`) and linked from the landing page.
- Compares the **6** sites BWTF currently publishes for SF: Aquatic Park, Baker Beach at Lobos Creek, China Beach, Crissy Field East, Ocean Beach at Lincoln Way, Ocean Beach at Vicente St.

## Next steps
- [x] **Site set decided:** show **all 6** sites BWTF publishes for SF (not a fixed 5). New BWTF sites surface automatically; any without a city counterpart render as "incomplete" until paired.
- [x] **Historical trend view (shipped)** — click any site on `/compare` to open a modal with a two-line chart (BWTF vs city Enterococcus over time, log scale, CA-limit line). Backed by `/api/site-history` + `build_site_history`; chart via Chart.js.
- [x] **Same-day comparison (shipped)** — second chart in the modal: a grouped bar chart of only the dates both programs sampled the same site (worst reading per day), with the CA-limit line. Surfaces same-day divergence (e.g. China Beach 2026-02-17: BWTF 10 vs city 933).
- [ ] Loosen the same-day match to a configurable tolerance (±1–3 days) — exact same-day pairs are sparse (~2–3 per site); a small window yields 6–13 paired points while staying contemporaneous.
- [x] **Collection date + time (shipped)** — BWTF `collectionTime` is now converted UTC→SF local (fixing evening samples that showed a day late), and the compare table shows the BWTF collection time. Both sides show the **collection** date, not the publish date. Confirmed the city/SF Gov feed is date-only (its `data_as_of` / `data_loaded_at` are pipeline timestamps, not collection time), so time is BWTF-only.
- [x] **Surface collector + conditions (BWTF-only) — shipped as the Sample Log page (`/bwtf`).** A dedicated tab lists every BWTF sampling event with `testedBy` (collector), the full `weather` block (air & water temp, current weather, precipitation, tide, wave height, wind), `method`, and the volunteer `comments` — via `fetch_event_history()` in `bwtf_api.py`, filterable by site/text. Remaining ideas: also fold a per-sample detail row into the comparison history modal, and flag rain / incoming-tide samples (ties to the rain→CSO thesis in B5). The city/SFPUC feed has none of this (date + value only).
- [ ] **Improve the comparison UI** — mobile-responsive charts, a site map/picker (lat/long already available per site), clearer units & legend, a selectable time window, tighter table + modal styling, and loading states; longer term, fold into the dashboard-wide UI refactor (B2).
- [ ] Quantify agreement over time (correlation, % of weeks both agree, typical value gap) instead of only the latest sample.
- [ ] Validate the BWTF↔city site pairings (same name-based caveat as B3).
- [ ] Cache BWTF responses and add the same schema/heartbeat guards as the rest of the system (another undocumented endpoint).
- [x] **Bacteria-type selector (compare) — shipped** — the hero/table/history modal now switch analyte via a real dropdown (`/compare?analyte=…`). `comparison.py` is analyte-aware (`ANALYTES` map + per-analyte CA limits from `STANDARDS`: Enterococcus 104, Fecal coliform 400, Total coliform 10000). Marine sites carry no E. coli, so the three live analytes are ENTERO / COLI_FECAL / COLI_TOTAL. Data is lopsided as expected: BWTF reports **only Enterococcus** (full head-to-head, 6/6), while Fecal/Total are SFPUC-only — BWTF cells show "not measured", agreement shows "SFPUC only", and a note explains it. The history modal labels its axis + CA-limit line per analyte.
- [ ] Feed this comparison into the A2 central dashboard.

---

# D. SF sewage forecaster

**Goal:** Predict CSO (combined sewer overflow) risk in SF *before* discharges happen, from rainfall — advance warning rather than after-the-fact beach postings.

## Shipped (brought in from the prototype)
- Lives at `features/forecast/` (moved intact from the standalone `sf_sewage_forecast` repo): scikit-learn models (per-basin + citywide `.pkl`), rainfall/CSO collectors (`src/`), basin config, and the live engine (`live_dashboard.py`).
- Served at `/forecast` via `features/forecast/page.py`, which wraps the engine (`LiveData`) and reuses its HTML; APIs namespaced under `/forecast/api/*`. A background thread refreshes predictions every 30 min.
- Lazy-loaded: needs the ML extras in `requirements.txt`; without them the page shows an install notice and the rest of the app is unaffected.

## Next steps
- [ ] Validate the moved forecaster end-to-end with deps installed (predictions render, refresh loop runs, historical + bacteria views work).
- [ ] Surface forecast risk on the landing page's conditions banner (currently SFPUC status + weather only).
- [ ] Feed forecast output into the alert system (B5) as an *upcoming-CSO* alert, not just current-event.
- [ ] De-dupe the forecaster's own SFPUC/weather fetchers against `shared/` (it kept its own collectors in the move).
- [ ] Document retraining (`features/forecast/src/models/train.py`); confirm committed models load on a fresh clone.
- [ ] Tests + schema guards for the weather/forecast feeds (same fragility as the other undocumented sources).
