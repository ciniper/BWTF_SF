# Swapping a part of the forecast

How to change one input or one piece of the forecast, score the change the same
way the live forecast runs, and put it live (STAGES_DESIGN.md A6). Every command
runs from the repo root with the repo's venv: `venv/bin/python …`, or the main
checkout's venv from a worktree.

The rule for every swap:
1. Build the new piece next to the served one. Never edit the served files in
   `features/forecast/data/models/` by hand.
2. Score it with the stages build. It compares the new piece with the served
   one on the same days, stage by stage (`STAGES_PROTOCOL.md`).
3. Put it live only through a promotion, which is Chase's call. The served
   pins in `tests/test_served_golden.py` move only in that commit, by
   re-running `tests/fixtures/make_stages_goldens.py`.
4. Run `scripts/check.sh` before every push (DEPLOY.md).

The live page and the stage scores compose with the same code
(`src/models/compose_v2.py`), so what is scored is what serves.

## The model catalog

`features/forecast/data/models/catalog.json` lists every set on disk, the
live one first. For each set it gives its status (live, candidate or
retired), its five parts in words and ids, its description and its record.
It also gives every basin's overflow-model weights as plain numbers: the
intercept, and per term its weight per standard deviation, mean and sd (trees:
their size and gain shares). It is generated, never edited. Re-run it after
any save, promotion or rebuild:

```bash
venv/bin/python features/forecast/src/models/export_catalog.py
```

`tests/test_catalog.py` fails while it is stale.

## A set's name

A set is named by its five stages as they were when it was made: one short
code per part, joined by hyphens (`shared/lineup.py`, STAGES_DESIGN.md Part
B 36). Basins come first, and only when they are not BWTF's. Then the weather
model, the overflow model, the beach split, the lingering table and the live
correction rule. `icon-w38-osplit-lt2-bflags` is the live forecast;
`icon-w38-osplit-lt2more-bflags` differs from it in the lingering table alone.

- The savers name a set themselves; a name that is not its lineup's id is refused.
- A new part needs its words and its code in `shared/lineup.py` before
  anything can save it.
- A stage candidate built under a working name moves to its id once its five
  parts exist (`stages_candidates.py --finalize NAME`; `assemble` does it).
- The names before 2026-10-07 (`logit_v1_s2v2`, `gb_v1`, …) are in
  `lineup.RENAMED`.

## The weather model (the forecast days' rain)

The live forecast reads one weather model through Open-Meteo:
`METEO_PARAMS["models"]` in `live_dashboard.py` (ICON, `icon_seamless`,
since 2026-09-30). A weather model is an input, so it is chosen on stage 1
alone: how close its rain comes to the two NOAA gauges, by lead.

```bash
venv/bin/python features/forecast/src/collectors/openmeteo_previous_runs.py --fetch --models icon_seamless,ecmwf_ifs025,gfs_seamless
venv/bin/python features/forecast/src/models/stages_s1.py --write
```

The first command archives what each model forecast 1–5 days ahead (it needs
the network; add a new model to `MODELS` in the collector first). The second
scores every model against the gauges. The paired test against the served model
at lead 1 is in `features/forecast/data/models/stages/_s1/s1_scores.json`.

To switch: change the one line in `METEO_PARAMS`, and rename the served set in
the same commit, since its S1 is part of its name
(`src/models/promote.py --rename-served`). Then rebuild the stage scores (the
last section), because every forecast-day entry reads it.

To score the live set with another weather model before switching (nothing
served moves; STAGES_DESIGN.md Part B 39):

```bash
venv/bin/python features/forecast/src/models/promote.py --weather ecmwf_ifs025
venv/bin/python features/forecast/src/models/stages_build.py --set ecmwf-t9wind3h-osplit-lt2more-bflags --root candidates --write
```

The copy's lineup names the weather model, and the stages build reads every lead
entry from that model's archived forecasts (`stages_build.weather_model`). The
Model check shows its public number when that weather model is picked.

## The overflow model's weights or terms (stage 2)

A set of weights is a model set: one model per basin, with its terms, weights
and rain source. Build a candidate set next to the served one, then score it.

- **On BWTF basins:** `src/models/shared_logit.py shared8` (a same-terms
  design from `leaderboard.SHARED_DESIGNS`, every weight ≥ 0), or the
  leaderboard's own families. These write `data/models/candidates/<name>/`.
- **On the city's basins:** `src/models/stages_s2_sfpuc4.py --write` runs the
  term-set bake-off (A5). Its winner is saved under
  `data/models/stages_candidates/<name>/`.

```bash
venv/bin/python features/forecast/src/models/stages_build.py --set <name> --root candidates --write
```

Use `--root stages_candidates` for a city-basin set. The comparison with the
served set is in `features/forecast/data/models/stages/<name>/scores.json`:
`primaries` holds the pre-registered tests and `promotion` the checklist.

To put a set live on BWTF basins:
`src/models/promote.py <name> --line 0.25`, then the follow-ups its docstring
lists. A city-basin set needs the promotion tooling (P11), which is built when such
a set earns promotion (STAGES_DESIGN.md Part B 29).

## The lingering stage (S4), per zone

`src/models/stages_s4_zones.py` builds an SFPUC-basin stage candidate that
differs from its base in S4 only. In every fold, each zone takes the rain curve
or the lingering table, whichever scores better inside the fold on the score you
choose: S4's own (`--criterion s4`) or the public number's (`--criterion out`).

```bash
venv/bin/python features/forecast/src/models/stages_s4_zones.py --assemble NAME --base sfpuc-icon-t8s-osplits-lt2zone-lzflags --criterion out
venv/bin/python features/forecast/src/models/stages_build.py --set NAME --root stages_candidates --write
venv/bin/python features/forecast/src/models/stages_s4_zones.py --compare NAME --base sfpuc-icon-t8s-osplits-lt2zone-lzflags
```

The last command prints the paired change against the base, per stage, zone
and window. The results so far are in TODO.md and STAGES_DESIGN.md Part B 32.

## Trying term sets: the term lab

A local page for trying overflow-model term sets in seconds, before building
a candidate.

```bash
venv/bin/python features/forecast/src/collectors/historical.py --wind
venv/bin/python features/forecast/src/models/term_lab.py
```

The first command fetches ERA5's hourly wind once (`data/raw/openmeteo_wind_hourly.csv`).
The second serves the lab at http://localhost:8095.

- **What you set.** Pick terms (the 19 inputs, their hinges, and five new
  ones: the largest running 24 hours, the rain that fell on a west wind, the
  rain-weighted west and south winds, and the 3-hour peak after wet days),
  a C, whether every weight must be ≥ 0, and a training record.
- **How it grades.** The lab fits the same terms in every basin, season by
  season, as the stages build refits a set. It grades on the live set's own
  scored S2 rows, with the same truth, exclusions, reference, storm blocks and
  bootstrap. A lab number is the number the stages build would give
  (`term_lab.py --check`; `tests/test_term_lab.py`).
- **What it never shows.** Post-training days and the live season stay out,
  so they can still confirm a pick.
- **Choose for me.** It adds terms one at a time inside each season's fold,
  so its grade is fair to the procedure. It also gives the terms it would
  pick on all nine seasons.

One new input is servable: the south wind on the rainy hours (`wind_v_rain`).
The training frames, the stage scores and the live page compute it, and
`train_terms.py` saves candidates on it (below). The other four new inputs are
lab-only: a term set that uses one can be graded but not saved or served.

## A short term list with the south wind

Candidates on a named term list, picked nested, with the wind (Chase,
2026-10-07: "an 8/9/10 term model and including the wind").

```bash
venv/bin/python features/forecast/src/collectors/openmeteo_previous_runs.py --wind
venv/bin/python features/forecast/src/models/train_terms.py
venv/bin/python features/forecast/src/models/train_terms.py --save
venv/bin/python features/forecast/src/models/train_terms.py --peak
venv/bin/python features/forecast/src/models/stages_build.py --set icon-t8wind-osplit-lt2-bflags --root candidates --write
```

1. The first command fetches the archived wind *forecasts* at leads 0–5 for
   all three weather models (`data/raw/openmeteo_wind_<model>.csv`), over each
   rain archive's span. A lead entry's wind is then the forecast's own, as the
   page will see it, never the measured wind. ERA5's measured wind
   (`historical.py --wind`) feeds training and "rain known".
2. The second runs the term lab's nested choice and prints each size's grade.
   It writes nothing. Four terms are forced: the day's rain, the 2- and 3-day
   sums and the south wind. The rest are added one at a time inside each
   season's fold, on terms the page can compute, with the sensible rules.
3. The third saves six candidates: 8, 9 and 10 terms, each with Linger
   table 2 (`icon-t8wind-osplit-lt2-bflags` at 8 terms) and with the linger
   table on more samples (`…-lt2more-…`, next section). All use the served rain sources and C, and the older reports from
   2011 (every discharge day). The manifest keeps each fold's own terms
   (`fold_terms`), so the stages build refits every fold on its own pick.
4. The fourth saves the 8-term set plus the day's 3-hour peak, forced: 9
   terms, with the linger table on more samples
   (`icon-t9wind3h-osplit-lt2more-bflags`, live since 2026-10-07; Chase: "it may help
   later on when we get better rain info"). It reads the committed selection
   instead of choosing again. Each graded fold takes its own 8 picks plus the
   peak; a fold that picked the peak itself takes its next pick instead. A
   forced term is a line in `train_terms.ADDED` and `PEAK_SETS`.

The wind is one shared formula (`rain_features.wind_rain_features`): the
rain-weighted south → north wind over the day's hours, in m/s, 0 on a day
under 0.005" of rain. A rainy hour without a wind reading makes the day
unknown, never calm. Served models don't read it; their `features` lists
stay the 19 inputs.

T2 is still labelled selection-contaminated: the forced terms and the sizes
were chosen with the lab's nine-season grades in view. Each set is also tagged
`post_seen` (`candidates.tag_candidate`): its record was chosen in
`OLDER_REPORTS.md` with post-training scores in view, so its post-training
scores carry the tag and only the live season can confirm it. Promoting such a
set copies `record`, `terms`, `fold_terms` and the tag into `served.json`
(`promote.CARRIED`).

## The linger table on more samples

`v2_d10` is stage 2 v2 with its linger table refit on the stages' S4 truth
samples (`samples.D10_SOURCES`). Those add STARDB's 2016-10 → 2020-07 results,
45–65% more sample days per beach group than the served table's DataSF and
Poo Bot. The split's shares are v2's.

```bash
venv/bin/python features/forecast/src/models/stage2_variants.py fit --variant v2_d10
venv/bin/python features/forecast/src/models/stage2_variants.py save --stage1 served --variant v2_d10   # saved as icon-w38-osplit-lt2more-bflags
venv/bin/python -c "import sys; sys.path.insert(0, 'features/forecast/src/models'); import candidates; candidates.rescore_post('icon-w38-osplit-lt2more-bflags')"
```

The last line rescores the candidate's post-training days on the served input rules (the gauge-outage
rule), as every set's are, so the Model check compares like with like. `train_terms.py` and
`train_older_reports.py` do it themselves.

The spec keeps `"variant": "v2"`, plus `"impact_samples": "d10"`, so it
composes and serves exactly as v2 does: no served code reads the samples
field. Its sets record the id `v2_d10` (`candidates.stage2_variant_id`). The
lineup calls it "Linger table 2, more samples". The stages build refits it per
fold on the D10 samples of the fold's training days. Its sets are tagged
`post_seen`: the table was built after `sfpuc-icon-t8s-osplits-pickout-lzflags`'s post-training
scores showed the extra samples helping.

## Training on the older discharge reports

SFPUC's older monthly reports (Mar 2011 until the CIWQS ledger opens: Bayside
Oct 2016, Westside Dec 2017; `data/csd/pre2018/`) can be extra **training**
labels for a candidate set. They are never truth: every score still reads the
CIWQS ledger only, on the same days, under the same protocol, so a candidate
trained on them is compared with the served set exactly like any other.

```bash
venv/bin/python features/forecast/src/collectors/historical.py --older
venv/bin/python features/forecast/src/models/train_older_reports.py
venv/bin/python features/forecast/src/models/train_older_reports.py --save all
venv/bin/python features/forecast/src/models/stages_build.py --set icon-w38r11a-osplit-lt2-bflags --root candidates --write
venv/bin/python features/forecast/src/models/train_older_reports.py --report
```

Since 2026-10-08 the four candidates are trimmed (STAGES_DESIGN.md Part B 39) and
OLDER_REPORTS.md is frozen: `--report` refuses until `--save all` and a stages
build each bring them back. Their 2011 record trains the live overflow model.

1. The first command fetches the 2011–2015 rain (both gauges, and ERA5's
   hourly rain) into two files of their own,
   `data/raw/historical_rain_2011-2015.csv` and
   `hourly_rain_openmeteo_2011-2015.csv`. The record's own rain files stay as
   they are, because every stage score pins them. The gauge-outage rule is
   applied to the older span: ACIS reads 0.00 while a gauge is dead.
2. The second compares the candidates with the served design on the
   trainer's own holdout numbers. It writes nothing.
3. The third saves the four candidates (`train_older_reports.RECORDS`):
   - records from 2016-03 (no new rain) and from 2011-03;
   - each with the first day of each run of discharge days as the label, or
     every discharge day.

   Each is the served overflow model's design refit on the longer record, with
   the served beach split and linger table. The candidate's manifest names
   its `record`.
4. Score each set with the stages build, then write `OLDER_REPORTS.md` from the
   scores. The build fits S2 on the set's record. Its T2 folds also fit on the
   record's seasons before 2016-17, which no fold scores. S3 and S4 are fit
   per fold on the served record, so the candidate differs from the served
   set in its S2 weights only.

The reader is `src/collectors/csd_pre2018.py`. Nothing served or live reads
it (`tests/test_discharges_pre2018.py`). Promoting such a set copies its
`record` into `served.json`, so the stage build keeps refitting the served set
on it.

## A rain gauge

Each basin's model reads one rain series: the two-gauge mean `avg`, `SF
Downtown` or `SF Oceanside` (`served.json` `rain_sources`;
`train_v4.RAIN_SOURCES`). Moving a basin to another of these is a new model
set: fit it with the other source, then score and promote it as above.

Adding a new gauge is a bigger change. It touches the gauge collector, the rain
series names in `train_v4`, the truth tables (`truth.gauges`), the outage rule
and retraining. Plan it as its own piece of work.

## A dead gauge's days (the gauge-outage rule)

A gauge that dies reads 0.00 for days or weeks. `gauge_outage_v1` (the live
rule) finds those runs and reads the other gauge in their place.
`gauge_outage_v2` finds the same runs but reads MRMS, NOAA's radar + gauge
rain, at the dead gauge's own cell where MRMS has the day (October 2020 on),
and the other gauge before that (`rain_features.GAUGE_OUTAGE_V2`,
`train_v4.fill_from_mrms`; RAIN_SOURCES.md; STAGES_DESIGN.md Part B 40).

v2 is opt-in, set by set, and it is the set's stage 1: S1 is the rain a set
reads, and v2 changes a dead gauge's days. Its S1 part is "ICON, dead gauges
from MRMS" (code `iconmrms`, `shared/lineup.py` `S1_RAIN`); the overflow
model keeps its own name, refit on that rain (`iconmrms-t9wind3h-…`; Part B
41). A candidate also names the rule in its manifest (`input_rules` for
scoring, `train_input_rules` for training), and the two must agree. The stages
build reads it from there in every entry, as the issue day knew it, and pins
the MRMS file. The term lab takes it too (`term_lab.Lab(input_rules=[...])`).
Nothing served reads it.

```bash
<rain env>/bin/python features/forecast/src/collectors/rain_grids.py mrms-daily --end <the gauges' last day>
venv/bin/python features/forecast/src/models/train_terms.py --fill mrmsfill
venv/bin/python features/forecast/src/models/stages_build.py --set iconmrms-t9wind3h-osplit-lt2more-bflags --root candidates --write
```

The first command refreshes MRMS's daily totals (the rain environment:
`features/forecast/requirements-rain.txt`); run it whenever the gauges are
refreshed. The second saves the live design under v2 on every split and
table pair. The third scores one of them.

`promote.py` refuses a set whose rule the live page cannot apply
(`rain_features.INPUT_RULES_SERVABLE`). Serving v2 first needs MRMS on the
live page (TODO "MRMS on the live board"), then that rule added to the
servable list in the same commit.

## The basins and zones (the geography)

Geographies are versioned in `shared/geography.py`: `geo_v1` is the served one
and `sfpuc4_v1` is the city's four basins. A model set carries the geography it
was built for, and every stage reads that stamp. Never edit the registries
(`shared/stations.py`, `outfalls.py`, `zones.py`) to move an outfall. Add a new
geography version instead. `tests/test_geography.py` shows what one must
satisfy.

## The live correction rule (stage 5)

The rules that move the forecast when the watcher sees an overflow flag or a
lab result: `live_v2` ("Basin flags") is the live rule (`src/models/live_rules.py`). The candidates
are replay variants in `src/models/stages_s5.py`: no correction, `basin_swap`
(= `live_v2`), `link_swap`, `zone_swap`, `link_zone_swap`, `sample_swap` and
`downgrade`. The stages build scores them all on the days after each
observation, with a perfect feed and with realistic late or missed flags. Its
S5 table is in `scores.json`.

To switch the live rule (A7) to `link_zone_v1` (the rule the replay scores as `link_zone_swap`; both read "link/zone"): a station's CSO flag sets the
beaches posted by that station's outfalls to certain on the flag day, raises the
basin's other beaches to how often they overflow together, and lets the
lingering table carry the days after. No lab result moves the percentage.

```bash
venv/bin/python features/forecast/src/models/promote.py --corrections link_zone_v1 --dry-run
venv/bin/python features/forecast/src/models/promote.py --corrections link_zone_v1
venv/bin/python tests/fixtures/make_stages_goldens.py
```

The second command writes `data/models/s5.json`, which holds the rule and its
co-firing shares, fit on the overflow record through the served set's
training end. It also records the rule in `served.json` and renames the served
set, whose S5 is now `lzflags` instead of `bflags`. The live page reads
that file and composes the flags through `compose_v2` instead of `live_rules`.
The third command re-pins the served forecast, and belongs in the same commit.
`promote.py --corrections live_v2` switches back.

## After any swap: rebuild the stage scores

The stage scores pin the code and data they were built from, and
`tests/test_stages_build.py` fails when they are stale. Rebuild in this order:

```bash
venv/bin/python features/forecast/src/models/stages_s1.py --write
venv/bin/python features/forecast/src/models/stages_build.py --set served --write
venv/bin/python features/forecast/src/models/stages_build.py --set icon-w38-nosplit-lt1-bflags --root candidates --write
venv/bin/python features/forecast/src/models/stages_build.py --set icon-trees-nosplit-lt1-bflags --root candidates --write
```

Then rebuild every other candidate (`ls features/forecast/data/models/candidates/`)
with `--set <name> --root candidates --write`, and each stage candidate you are
keeping with `--set <name> --root stages_candidates --write`. Every candidate on
disk is scored, so the Model check has a public number for every pick (Part B 39;
`tests/test_stage_builder.py`). That takes about 5 minutes per set, four at a time
in parallel; the S1 step takes about 2.

After rebuilding the stage scores, run `venv/bin/python features/forecast/src/models/export_stage_builder.py` and commit its `data/models/stage_builder.json`: the Model check's stage builder reads that file, since the app bundle leaves the stage artifacts out (`tests/test_stage_builder.py` fails while it is stale).

## Grading the live season (T0)

The live season is every day after the rules froze (`STAGES_PROTOCOL.md` §2,
T0: from 2026-10-03). Once the data reach it, the stages build scores it two
ways:
- **Shadow-run.** Every lineup, the live one included, runs on the same archived
  inputs as on post-training days, its fitted parts not refit. Each
  challenger's test of the public number then reads post-training and the
  live season as one window.
- **As served.** For the live forecast only: what the page actually showed,
  from each day's first forecast stored in Supabase's `forecast_history`,
  graded on the same truth (`scores.json` → `t0_as_served`, and "The public
  number" in the stages report). It is never one side of a comparison.

A day is graded only once its truth is in. The overflow ledger comes from
CIWQS quarterly, so October 2026 is graded after the December 2026 refresh at
the earliest.

1. Refresh the data (each step needs the network):
   - the overflow ledger: the run order in
     `features/forecast/src/collectors/csd_ciwqs/README.md`, appending the new
     months to `features/forecast/data/csd/`;
   - the gauges and DataSF's samples:
     `venv/bin/python features/forecast/src/collectors/historical.py`, and
     ERA5's hourly rain, which has no command of its own:
     `venv/bin/python -c "import sys; sys.path.insert(0, 'features/forecast/src/collectors'); import historical; historical.fetch_hourly_rain()"`;
   - the weather model's archived forecasts:
     `venv/bin/python features/forecast/src/collectors/openmeteo_previous_runs.py --fetch`.
     Lead 0's short-lead cache (`data/raw/openmeteo_hist_forecast_<model>.csv`)
     has no forward fetch yet. Until it reaches the season, the shadow-run's
     lead entries have no public-number row there (`scores.json` → `dropped`),
     so the one-day-ahead test reads post-training days only.

   Then, as after any ledger refresh:
   `venv/bin/python features/forecast/src/models/train_v4.py --rescore --promote`.
2. Export the snapshot from the main checkout. It needs the Supabase service
   key in `.env` and only reads:

   ```bash
   venv/bin/python features/forecast/src/models/grade_prospective.py --export
   ```

   It writes `features/forecast/data/forecast_history/t0_first_snapshots.csv`,
   one row per issue day, lead and zone.
3. Commit the snapshot with the data refresh.
4. Rebuild the stage scores in the order above (S1, the served set, then every
   other set), then regenerate the stages report:
   `venv/bin/python features/forecast/src/models/export_stages_report.py`.
5. Run `scripts/check.sh`.
