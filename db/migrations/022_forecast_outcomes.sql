-- 022: forecast_issued + forecast_outcomes — what each morning's forecast said, next to what then happened
-- (Brian, 2026-11-11).
--
-- The record the Admin "Forecast check" tab reads (/alerts/forecast-check, features/alerts/forecast_check.py) and
-- the shape grade_prospective.py exports for the protocol's T0 grade (STAGES_PROTOCOL.md §2). Two tables, so the
-- forecast and the truth are written by different hands at different times and neither overwrites the other:
--
-- * forecast_issued — one row per target day × lead (0–5) × zone: the day's FIRST forecast of that target (what a
--   morning visitor saw; the later refreshes are the live rules at work, kept in forecast_changes), with the rain it
--   was fed (that day's inches, the 2- and 3-day totals, the weather model), the uncorrected percentage beside the
--   served one, the live rules that fired and the per-basin overflow probabilities. Written by the production refresh
--   (features/forecast/outcomes.record) with DO NOTHING on the key, so the first refresh of the day wins.
-- * forecast_outcomes — one row per target day × zone: what happened, filled in as each truth arrives and refilled
--   for the last ten days on every refresh. The fast rulers come from tables the site already keeps: the SFPUC map's
--   posted / CSO flags (feed_station_days), the city's lab results over standard (samples) and the gauges' measured
--   rain (ACIS). ledger_overflow is the gold truth — a CIWQS-filed overflow at an outfall posting the zone — and
--   stays null until the quarterly ledger refresh fills it (never from the refresh: the merge upsert omits it).
--
-- Apply by hand in the Supabase SQL editor. Safe to re-run. Then, from the main checkout (service key in .env):
--   venv/bin/python -m features.forecast.outcomes --backfill      -- every first snapshot since forecast_history began
--   select count(*), min(issue_date), max(issue_date) from forecast_issued;
--   select * from forecast_outcomes order by target_date desc, zone limit 8;

create table if not exists public.forecast_issued (
  target_date  date not null,                                    -- the day the forecast is about
  lead         smallint not null check (lead between 0 and 5),   -- days ahead: 0 = issued that morning
  zone         text not null,                                    -- shared/zones.py key
  issue_date   date not null,                                    -- Pacific day it was issued (= target_date - lead)
  generated_at timestamptz not null,                             -- the refresh that produced it
  model        text,                                             -- the model stamp's name
  corrections  text,                                             -- its live-correction rule (live_v2 …)
  stamp        jsonb,                                            -- the stamp's lineup fields, for the T0 grade
  p            numeric not null check (p between 0 and 1),       -- the served zone percentage, as a probability
  p_plain      numeric,                                          -- the same with no live corrections
  rules        text[] not null default '{}',                     -- live rules that fired that day
  rain_in      numeric,                                          -- the rain it was fed: that day's inches …
  rain_2d      numeric,                                          -- … the trailing 2-day total …
  rain_3d      numeric,                                          -- … and 3-day total
  rain_source  text,                                             -- gauge / weather model that supplied them
  basins       jsonb,                                            -- per-basin overflow probabilities
  primary key (target_date, lead, zone)
);
create index if not exists forecast_issued_issue_idx on public.forecast_issued (issue_date);
alter table public.forecast_issued enable row level security;
revoke all on public.forecast_issued from anon, authenticated;
grant all on public.forecast_issued to service_role;

create table if not exists public.forecast_outcomes (
  target_date       date not null,
  zone              text not null,
  rain_obs_in       numeric,          -- the two gauges' mean, inches (the current day's total is partial)
  rain_obs_by_gauge jsonb,
  feed_stations     integer,          -- stations of the zone the feed showed that day (null / 0: the watcher saw nothing)
  feed_posted       boolean,          -- a station of the zone carried the posted flag
  feed_cso          boolean,          -- … or the CSO flag
  samples_n         integer,          -- station-days sampled in the zone
  samples_exceeded  integer,          -- … of them over the AB411 single-sample standard
  exceeded_stations text[],
  ledger_overflow   boolean,          -- CIWQS: an overflow at an outfall posting the zone (null until the ledger covers the day)
  ledger_as_of      date,
  updated_at        timestamptz not null default now(),
  primary key (target_date, zone)
);
alter table public.forecast_outcomes enable row level security;
revoke all on public.forecast_outcomes from anon, authenticated;
grant all on public.forecast_outcomes to service_role;
