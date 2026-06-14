# Project Plan — SF Sewage (CSO) Real-Time Text-Alert System

**Status:** Draft for review
**Related:** `TODO.md` (Section B), `README.md`

## 1. Objective

Turn the existing detection engine into an **unattended, real-time text-alert service**: when SFPUC reports a combined-sewer-overflow (CSO) at a monitored SF beach, every subscriber for that site automatically receives a timely, accurate SMS/email — once, with delivery confirmed and failures visible to operators.

## 2. Definition of success

System-wide "done":

- **Unattended** — a new CSO triggers messages with no human in the loop.
- **Timely** — subscribers notified within ~15–30 min of the event appearing in the SFPUC feed.
- **Correct** — alerts fire for the right sites (validated mappings); no alert when there's no event.
- **No spam** — one message per event transition; a multi-day event does not re-text; an "all-clear" goes out when it ends.
- **Observable** — every send is logged (sent / preview / failed); operators are paged if the poller or feed dies.
- **Compliant** — subscribers opted in; STOP works.

## 3. Recommended decisions (confirm before building)

The plan assumes these defaults — flag any you'd change and I'll re-sequence:

| Decision | Recommendation | Why |
|---|---|---|
| **Runtime** | One always-on service (dashboard + internal poll loop) on a small PaaS (Fly.io / Render / Railway) | "Real-time + unattended" needs a 24/7 runner; simplest to operate. Cheaper alt: cron / GitHub Action poller + an external state store. |
| **SMS provider** | Twilio for production; keep free email-to-SMS as a dev/test fallback | Reliable and supports the required A2P 10DLC compliance; carrier gateways are being sunset. Cost is small at volunteer scale. |
| **Audience** | Public opt-in, BWTF-operated | Drives the consent flow + light hardening. If internal-only, B10 shrinks. |
| **Schedule** | Volunteer cadence; work sized **S/M/L**, not dates | Sequencing matters more than the calendar. Tell me your weekly cadence and I'll add target dates. |
| **Poll cadence** | Every 15–30 min, gentle on SFPUC's internal API | Meets "real-time" without hammering an undocumented endpoint. |

## 4. Phased plan

Phases are ordered by dependency. Each lists the `TODO.md` items it lands, its deliverables, and acceptance criteria.

### Phase 0 — Foundations & de-risking  *(no user-visible change)*
**Goal:** make it *safe* to automate — durable state, a tested parser, and validated detection — before anything sends on its own.
- **Lands:** B8 (persistence + secrets), B9 (test harness + recorded fixtures), B3 (validate detection rule + outfall/station mappings).
- **Deliverables:**
  - SQLite store for subscriptions, alert state, and delivery log; `data/*.json` migrated; secrets read from env / secret-store only.
  - Recorded-response fixtures + unit tests for SFPUC / SF-Gov parsing and AB-411 logic.
  - A short validation memo: which station IDs can actually carry a CSO, and corrected outfall → beach → basin mappings.
- **Acceptance:** tests pass against fixtures; a simulated CSO produces the expected matched-subscriber set; no secrets in the repo.
- **Effort:** M

### Phase 1 — Close the real-time loop (MVP)  *(the core)*
**Goal:** a new CSO auto-notifies the right subscribers, once, unattended.
- **Lands:** B5 (scheduled poller → auto-dispatch), B6 (edge-trigger + dedup), B1 *(partial — running unattended)*.
- **Deliverables:**
  - Background poller on the chosen cadence that diffs current vs. stored CSO state and dispatches **only on off→on transitions** via the existing `dispatch_subscription_alerts()`.
  - Dedup / throttle + an "all-clear" on on→off.
  - Deployed to the always-on host (even minimally).
- **Acceptance:** simulate an event → exactly one message per subscriber; keep it active across polls → no repeats; clear it → one all-clear. Verified end-to-end with a test subscriber.
- **Effort:** M — **critical path. B6 must ship *with* B5, or the first multi-day event spams everyone.**
- **Depends on:** Phase 0.

### Phase 2 — Reliability & trust
**Goal:** know it's working, prove delivery, and make SMS production-grade + legal.
- **Lands:** B7 (delivery log + heartbeat), B4 (Twilio + A2P 10DLC + opt-in/STOP), remainder of B9.
- **Deliverables:**
  - Delivery history surfaced in the dashboard; a dead-man's switch that alerts an operator if the poll loop stalls or the feed breaks / changes shape.
  - Twilio integration live; **A2P 10DLC brand/campaign registration submitted early** (it has lead time); opt-in capture + STOP handling + consent records.
- **Acceptance:** force a feed failure → operator is alerted; every send appears in the log with status; STOP removes a subscriber; A2P approved.
- **Effort:** M–L *(A2P approval is calendar time, not work time — start it during Phase 1).*
- **Depends on:** Phase 1.

### Phase 3 — Production hardening & launch
**Goal:** safe to open to the public.
- **Lands:** B1 *(remainder — production server, health check, auto-restart)*, B10 (rate-limit, privacy policy).
- **Deliverables:** WSGI/ASGI server replacing the stdlib one; health endpoint; subscribe-form rate-limiting + a privacy policy.
- **Acceptance:** restart/load behaves; abusive form submission is throttled; privacy policy linked from the subscribe flow.
- **Effort:** S–M.

### Phase 4 — UX & portfolio integration
**Goal:** a dashboard people enjoy, and a clean hand-off to the central dashboard (`TODO.md` Section A2).
- **Lands:** B2 (UI refactor, map view, self-service), B3 *(deeper validation)*, expose status to A2.
- **Deliverables:** templated UI + map view; subscriber self-service edit/unsubscribe; conforms to the A1 status-contract so A2 can aggregate it.
- **Acceptance:** a non-developer can read current status at a glance and self-manage their subscription; the central dashboard renders this project from its API.
- **Effort:** M.

## 5. Critical path & sequencing

```
B8 (durable state)
   └─► B5 + B6  (poll loop + dedup — ship together)   ◄── MVP
          └─► B7 / B4  (observability + production SMS)
                 └─► B1 prod + B10  (hardening) ─► LAUNCH
                        └─► B2 polish + A2 integration
```

Start **A2P 10DLC registration during Phase 1** so approval lands by Phase 2.

## 6. Key risks

| Risk | Impact | Mitigation |
|---|---|---|
| SFPUC LIMS feed is undocumented/internal — may change, rate-limit, or block | System goes blind | Schema guard + fixtures (P0); heartbeat (P2); gentle cadence; consider asking SFPUC for a sanctioned feed |
| Carrier email-to-SMS sunset | Silent SMS loss | Twilio as primary (P2) |
| A2P 10DLC approval delay | Blocks SMS launch | Submit early (P1) |
| False positive/negative in a safety alert | Erodes trust / safety | Validate *before* auto-send (P0); conservative copy; all-clear messaging |
| TCPA / consent | Legal exposure | Opt-in + STOP + consent log (P2) |
| Volunteer bandwidth | Slippage | MVP-first; each phase is independently shippable |

## 7. Open decisions (these gate the path above)

1. **Always-on vs. scheduled** runtime? *(default: always-on)*
2. **Twilio vs. free gateways**, and rough subscriber count? *(default: Twilio)*
3. **Public vs. internal** audience? *(default: public opt-in)*
4. **Budget / weekly cadence** — share it and I'll convert effort sizes into target dates.
