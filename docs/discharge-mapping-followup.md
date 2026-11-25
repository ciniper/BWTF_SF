# Discharge mapping follow-up — draft, awaiting evidence

Tracking: [issue #44](https://github.com/ciniper/BWTF_SF/issues/44).
This holding PR changes documentation only. It is not ready for merge or
deployment and does not delay the separate confirmed Aquatic Park/Dogpatch fix.

## Confirmed evidence

- SFPUC's map code uses station 4613 / `BAY#211_SL` for its Dogpatch triangle,
  explicitly naming the 20th Street and 22nd Street outfalls (CSD-030/030A).
- Production daily final raw readings for Aquatic Park were `BAY#211_SL` on
  November 18–20, 2026 (23:59 PST), then null on November 21. The November 21
  daily flag confirms it had been present earlier that day.
- Our existing outfall registry assigns CSD-030/030A to Crane Cove Park.
- The immediate fix addresses this exact carrier/value combination in Python
  and the Postgres watcher. It preserves raw evidence and existing history.

## Questions to resolve before adding mapping rules

1. **Hyde Street / Aquatic Park:** confirm the meaning of 4614's raw CSO
   values and the physical outfalls represented by its Pier 31/39 triangle.
   The existing registry associates CSD-013/015 with both beaches, but the
   decision to propagate a live Hyde Street flag to Aquatic Park needs separate
   support. Compare raw readings, official map behavior, event timing and,
   where possible, an explanation from SFPUC. Do not infer propagation merely
   because two station flags overlap in time.
2. **Other station/outfall associations:** audit carrier ID, raw CSO value,
   official triangle coordinates, receiving water and affected monitoring
   station as separate fields. Identify unknown values and ambiguous shared
   signals. Distinguish a flag covering a group of structures from evidence
   that every structure in that group discharged.
3. **Historical interpretation:** retain what the source said and what our app
   showed at the time. Design a separately labeled corrected interpretation
   for `/cso-history`, daily summaries and forecast labels; never silently
   rewrite original feed snapshots or sent-alert records. Record mapping
   versions and effective dates if introducing historical reinterpretation.
4. **Subscriber impact:** read the November 18–21 transition and delivery
   records to establish which Aquatic Park alerts were actually sent and
   whether Crane Cove already had an independent flag. Summarize without
   exposing subscriber details. Any outreach is a separate approved action.
5. **Evidence retention:** daily `raw_last` keeps only the last reading of
   each day. Assess whether an append-only change log of raw CSO values,
   carrier IDs and source-map revisions is warranted for future investigations.

## Readiness criteria

- [ ] Evidence attached for each proposed additional mapping, including its
  uncertainty and counterexamples.
- [ ] Decision recorded on Hyde Street → Aquatic Park propagation.
- [ ] Agreed treatment of historical observations versus corrected attribution.
- [ ] Python/Postgres parity and tests for activation, clearing, overlapping
  flags, missing stations, native postings and unknown raw values.
- [ ] UI wording explains derived attribution without overstating which
  individual structure fired.
- [ ] Required checks/reviews pass, and explicit approval is obtained before
  merging or deploying any runtime or database changes.

Keep the PR in draft until the evidence supports concrete implementation.
