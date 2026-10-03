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

To switch: change the one line in `METEO_PARAMS`. Then rebuild the stage scores
(the last section), because every forecast-day entry reads it.

## The overflow model's weights or terms (stage 2)

A set of weights is a model set: one model per basin, with its terms, weights
and rain source. Build a candidate set next to the served one, then score it.

- **On today's basins:** `src/models/shared_logit.py shared8` (a same-terms
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

To put a set live on today's basins:
`src/models/promote.py <name> --line 0.25`, then the follow-ups its docstring
lists. A city-basin set needs the promotion tooling (P11), which is built when such
a set earns promotion (STAGES_DESIGN.md Part B 29).

## A rain gauge

Each basin's model reads one rain series: the two-gauge mean `avg`, `SF
Downtown` or `SF Oceanside` (`served.json` `rain_sources`;
`train_v4.RAIN_SOURCES`). Moving a basin to another of these is a new model
set: fit it with the other source, then score and promote it as above.

Adding a new gauge is a bigger change. It touches the gauge collector, the rain
series names in `train_v4`, the truth tables (`truth.gauges`), the outage rule
and retraining. Plan it as its own piece of work.

## The basins and zones (the geography)

Geographies are versioned in `shared/geography.py`: `geo_v1` is the served one
and `sfpuc4_v1` is the city's four basins. A model set carries the geography it
was built for, and every stage reads that stamp. Never edit the registries
(`shared/stations.py`, `outfalls.py`, `zones.py`) to move an outfall. Add a new
geography version instead. `tests/test_geography.py` shows what one must
satisfy.

## The live correction rule (stage 5)

The rules that move the forecast when the watcher sees an overflow flag or a
lab result: `live_v2` serves today (`src/models/live_rules.py`). The candidates
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
training end. It also records the rule in `served.json`. The live page reads
that file and composes the flags through `compose_v2` instead of `live_rules`.
The third command re-pins the served forecast, and belongs in the same commit.
`promote.py --corrections live_v2` switches back.

## After any swap: rebuild the stage scores

The stage scores pin the code and data they were built from, and
`tests/test_stages_build.py` fails when they are stale. Rebuild in this order:

```bash
venv/bin/python features/forecast/src/models/stages_s1.py --write
venv/bin/python features/forecast/src/models/stages_build.py --set served --write
venv/bin/python features/forecast/src/models/stages_build.py --set logit_v1 --root candidates --write
venv/bin/python features/forecast/src/models/stages_build.py --set gb_v1 --root candidates --write
```

Then rebuild each stage candidate you are keeping with
`--set <name> --root stages_candidates --write`. That takes about 4 minutes per
set; the S1 step takes about 2.
