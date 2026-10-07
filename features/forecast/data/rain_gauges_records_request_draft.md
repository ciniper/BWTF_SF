# DRAFT — Public Records Act request for SFPUC's rain gauge data (do not send without review)

Drafted 2026-10-07. Separate from Kyle's discharge-records request, which is already filed.

**Status:** NOT FILED YET

When it is filed, change the line above to `**Status:** Filed <date>, request <number>`. That
stops the every-message reminder (`.claude/hooks/rain-request-reminder.sh`, wired up in
`.claude/settings.local.json`). A copy of this file for Drive lives in
`~/Personal/BWTF_Data_Archive/05_sfpuc_rain_gauges/`; this one is the live copy.

**Why.** SFPUC's monthly reports print each day's rainfall at five named gauges, but only as daily
totals in PDFs that arrive 4–6 weeks late (TODO: "Better rain: SFPUC's own gauges"). The data
behind them sits in SFPUC's Telog system at a finer interval and covers more gauges. Hourly gauge
rain in each basin would replace the ERA5 reanalysis behind the models' rain-intensity features.

**Evidence the records exist** (all in `~/Personal/BWTF_Data_Archive/`):
- Telog tags printed in the monthly reports, 2016 → 2026: RG10_OSP, RG13_SeaCliff (Westside);
  RG20_SEP1, RG21_GFS, RG35_NSS (Bayside).
- RG12_Legion: "TELOG:RG12_Legion.Intensity" stood in for Sea Cliff in Aug 2026, due to
  construction (`01_.../Oceanside_WPCP_CA0037681/2026/OSP_2026-08_..._Westside_Ver18_8_August_2026.pdf`).
- RG34, Palace of Fine Arts: substituted for the damaged NPF gauge in Mar 2023
  (`01_.../Southeast_WPCP_Bayside_CA0037664/2023/SEP_2023-03_..._Final_CL3.pdf`).
- The 2012 Southeast annual report, Real-time Control section: rain gauge sensing devices
  added or upgraded "in 21 location (completed 8/2012)", plus a cellular telemetry system and a
  "Telog Data server located at SEP" (completed 11/2012)
  (`04_.../raw_pdfs/annual_reports/SEP_2012_..._Annual_Report_Final.pdf`).
- SF's open data portal has no SFPUC rain dataset (catalog searches, 2026-10-06).

**The last rainfall request failed:** request 25-4464, June 2025, "All rainfall and wind data for
San Francisco from 2005 to 2025". The requester assigned it to Public Works, which closed it the
same day with no records and a reminder that the City need not "construct a document". Lessons
learned: send ours to SFPUC, name the gauges and the Telog system, and ask for an export of stored
data, not a new compilation.

**Where to send (checked 2026-10-07):** San Francisco's records portal,
https://sanfrancisco.nextrequest.com, department "PUC - Public Utilities Commission". SFPUC's
contact page lists that portal and a phone line, 628-246-1372. Not the CPUC: that is the state
utilities commission, with its own portal.

---

## The request (paste into the portal's description box; it is published there)

**Title:** SFPUC sewer-system rain gauges: gauge list and recorded rainfall, earliest available to present

To the Custodian of Records, San Francisco Public Utilities Commission:

Under the California Public Records Act and the San Francisco Sunshine Ordinance, I request the
following records about the rain gauges SFPUC operates for the combined sewer system.

To help locate them: SFPUC's monthly self-monitoring reports for the Oceanside and Southeast
plants, NPDES permits CA0037681 and CA0037664, print rainfall from Telog tags including
RG10_OSP, RG12_Legion, RG13_SeaCliff, RG20_SEP1, RG21_GFS, RG34 at the Palace of Fine Arts, and
RG35_NSS. The 2012 Southeast annual report describes rain gauge sensing devices added or
upgraded at 21 locations, a cellular telemetry system, and a Telog data server at the Southeast
plant.

1. **Gauge list.** A list of every rain gauge in this network, current or retired. For each
   gauge: its tag or ID, name, location as an address or coordinates, and the dates it began
   operating, moved, was replaced or was retired.

2. **Rainfall data.** The rainfall recorded at each gauge, from the earliest date available to
   the date of this request, at the finest time interval stored. If only summaries are kept,
   hourly totals are best and daily totals are acceptable. An export of the stored data as CSV
   or Excel files is requested.

3. **Data-quality flags.** Any flags or notes stored with that data marking values as missing,
   estimated, substituted or faulty. One example is the substitution of RG34 data for the NPF
   gauge noted in the March 2023 Southeast monthly report.

4. **Field descriptions.** Any existing data dictionary or field list for the data in item 2,
   showing units and time zone.

I am not asking SFPUC to create new records or analysis. An export of data already stored is
enough. If any part of this is already published, a link satisfies that part.

If the full period is burdensome, please release the gauge list first, then the data from
January 1, 2011 to the present, on a rolling basis, and tell me what earlier data exist.

Please deliver the records electronically through this portal. If any fees apply, please tell
me before incurring them. If any part is withheld, please cite the exemption relied on and
release the rest.

[OPTIONAL] These records are for the Surfrider Foundation San Francisco chapter's Blue Water
Task Force, a volunteer program that forecasts beach water quality after storms.

One question outside this request: does SFPUC share these gauges as a live or daily feed with
the public or partners? If so, I would be grateful for the right contact.

Thank you,
[NAME]

---

## How to file it

1. Fill in `[NAME]`, and keep or cut the `[OPTIONAL]` Surfrider line.
2. Go to https://sanfrancisco.nextrequest.com and start a new request.
3. Paste the title and the request text above into the description box. The description is
   published on the portal; published requests there show no requester name or email.
4. Department: SFPUC, listed as "PUC - Public Utilities Commission". Not Public Works, where
   25-4464 died. Not the CPUC, the state agency with its own portal.
5. Give your email so the replies reach you. Submit, and save the request number.
6. Expect a reply in about 10 calendar days: requests filed on 2026-10-07 showed a due date of
   2026-10-19. SFPUC may extend by 14 days for a large data pull, which is common.
7. Send Kyle the number so SFPUC sees two separate requests, not a duplicate.
8. Write the number into the Status line at the top of this file.
9. If the portal gives trouble, SFPUC takes requests by phone at 628-246-1372.

## If SFPUC asks to narrow it

Offer these in order, one at a time:
1. The gauge list, plus hourly data from Jan 1, 2011 to the present. That matches the
   Discharge Ledger's first year.
2. Only the seven gauges named above, hourly, 2011 to the present.
3. Those seven gauges, hourly, from Jan 2016 to the present. That covers the forecast's
   training years.

If they answer "no records": reply in the portal with the evidence above. Name the Telog tags,
the 2012 annual report's 21 locations, and the Telog data server at SEP. Ask them to route the
request to the Wastewater Enterprise staff who run that system.

## When the data arrives

Save everything untouched to `~/Personal/BWTF_Data_Archive/05_sfpuc_rain_gauges/`, with the
request number. Before anything reaches a model, check it against the daily totals printed
in the monthly PDFs and against the NOAA gauges (ACIS 047772 Downtown, 047767 Oceanside),
then work the TODO item "Better rain: SFPUC's own gauges".
