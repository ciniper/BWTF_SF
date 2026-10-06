# SFPUC beach bacteria results, 1 Jan 2000 – 27 Jul 2020 (STARDB export)

**Source.** SFPUC's laboratory database (STARDB), exported 2026-08-04 as a 374-page PDF,
`SFPUC Beach Water Quality_1Jan2000_27Jul2020.pdf` (18.6 MB; kept in Chase's data archive
`BWTF_Data_Archive/02_sfpuc_beach_bacteria_2000-2020/`, not in the repo). One row per result:
station number and name, sample date and time, analytical method, analyte, qualifier, value,
units. Obtained by Chase; parsed 2026-09-29 with `src/collectors/sfpuc_stardb_pdf.py`
(pdfplumber text extraction, one regex per row).

**Why it matters.** DataSF's public bacteria record (`v3fv-x3ux`, mirrored in
`data/raw/historical_bacteria.csv` and Supabase `samples`) begins 2020-07-27. This export ends
that day, and the 45 results the two hold for 2020-07-27 are identical, so it is the same
database and extends the bacteria history back twenty years without overlap: **60,574
results, 3,312 sample days, 27,088 station-days, 4,602 exceedances.**

## Files

- `sfpuc_beach_bacteria_2000-01_2020-07_normalized.csv` — the schema of
  `data/raw/historical_bacteria.csv` plus a few columns: `sample_date, station, analyte`
  (`COLI_TOTAL` / `ENTERO` / `COLI_E` / `COLI_FECAL`), `value` (`<10` → 5, `>x` → x, per
  `shared.standards.parse_result`), `value_raw`, `exceeds_standard`, `threshold`
  (`shared.standards.flag_exceedances`, total-coliform ratio rule included), `basin`, `zone`,
  `units`, `method`, `sample_time`, `in_registry`.
- `westside_csd_followup_sampling_episodes.csv` — sample dates at OCEAN#20 / #21 / #22
  grouped into episodes (gap ≤ 2 days): `first, last, n_days, year, stations, era`.
- `parse_summary.json` — counts per year, analyte, method, station; registry overlap.
- The raw as-printed rows (`..._rows.csv`, 8 MB) and `unparsed_lines.txt` are in the archive
  folder, reproducible from the PDF with the collector.

## What is in the data

- **Analytes.** Total coliform throughout (27,112 results). **Enterococcus and E. coli start
  2002-07-01 at the bay stations and 2003-10-02 on the ocean beaches** (Pacheco, Vicente,
  Fort Funston: Dec 2003; 16,731 and 16,730 results) — before that only total coliform was
  run, which is why 2000–2002 show almost no exceedances (0.4–4.8% of results vs 8–12% later).
  Stations added later start complete: Crissy Field West Apr 2008, Islais Creek and Mission
  Creek Nov 2013. One fecal
  coliform result. DataSF from Apr 2021 reports fecal coliform (`COLI_FECAL`) where this
  export has E. coli; the standards module carries both limits.
- **Methods.** Membrane filtration (CFU/100 mL, 10,383 results, to ~2003), then Quantitray /
  Colilert-18 and Enterolert (MPN/100 mL, 50,191). Qualifiers: 19,408 `<` (below detection),
  674 `>` (above range).
- **Stations.** 28. 19 are in `shared/stations.py` (56,522 results). The other 9 are retired
  or legacy points: BAY#201 Fort Point, BAY#202.1 Coast Guard Station, BAY#202.2 mid Crissy
  Field, BAY#202.3 west of the tidal channel, BAY#202_LAGOON, BAY#203, BAY#300 Sunnydale,
  BAY#301 Candlestick Fishing Pier, BAY#301.1_W (12 rows). They carry `in_registry = 0` and
  `basin = Unknown`; map them by hand if a use needs them.
- **Coverage notes printed on page 1 of the export:** Aquatic Park (BAY#210.1) not sampled
  early Oct 2013 and 24 Dec 2018 – 29 Jan 2019 (federal shutdowns) and 23 Mar – 27 Jul 2020
  (Covid); Mission Creek (BAY#220) not sampled 4 May 2020; Islais Creek (BAY#320) routine
  sampling started 26 Oct 2015; Mission Creek routine sampling started 23 Oct 2017.
- **Parse quality.** 152–162 rows per page, no row lost to layout. 3 results were printed
  without a value (E. coli at Sunnydale, Sunnydale Cove and Jackrabbit, 6 and 11 Apr 2002)
  and are not in the CSV. 9 rows on page 1 carry a `trailing_text` fragment of the metadata
  block (raw file only; values unaffected).

## The Westside discharge proxy (2004–2017)

Page 1 states that **OCEAN#20 (Ocean Beach at Pacheco), OCEAN#21 (Vicente) and OCEAN#22
(Fort Funston) are "only sampled following Combined Sewer Discharges"**. The data agree:
those three were sampled weekly through 2003 (162–165 dates a year) and on 4–17 dates a year
from 2004. Checked against the filed Westside discharge record where one exists (CIWQS
per-event tables, Jan 2018 – Jul 2020: 22 discharge days): 23 of the three stations' 26 sample
dates fall within four days after a filed discharge, and 15 of the 22 discharge days were
followed by such sampling (the others were small events or fell in the routine Monday round).
The episodes file therefore gives approximate Westside discharge dates for 2004–2017 — 77
follow-up episodes, 99% in Oct–Apr — the period CIWQS has no Oceanside per-event record for
(`data/csd/NOTES.md`, gap 1). Caveats: a sampling date lags the discharge by one to three
days; a discharge with no follow-up sampling leaves no trace; the count of episodes per season
can be checked against the monthly CSD counts in the scanned Oceanside cover letters.

## How the site uses it (2026-09-29)

`shared/city_history.py` serves these rows in DataSF's record shape for any window
that starts before 2020-07-27; `features/comparison/comparison.fetch_city_records`
(Samples page, Graphs, sample popover) and `features/site_analysis/page._fetch_rows`
(Site Report Card) splice them ahead of the API rows. E. coli became a fourth indicator
on those pages because it is what the city measured in place of fecal coliform until
March 2021; the Report Card's "routine" flag became a per-year rate so the three
after-discharge stations do not read as routine over 26 years. The forecast does not
read it yet.

## Still open for the forecast

Candidates, in rough order of value: (1) the samples ruler and the stage 2 persistence
evidence get 2000–2020 sample outcomes after the Bayside discharge days of the legacy record
(2013–2016) and the Poo Bot onsets (2016–17); (2) `live_rules.fit_sample_rates` and the
stage 2 v3 "shares from the water" fit get far more sampled tails; (3) the Westside proxy
becomes an extra label source for stage 1 back to 2004 (rain record exists), to be graded
like the Poo Bot onsets (holdout ablation per basin); (4) the site's bacteria graphs could
show the full 26-year history per station. Each is a separate decision.
