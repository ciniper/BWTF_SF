-- getCSV observations are separate from the DataSF samples mirror.
-- Append a row when a station/day/analyte first appears or changes.
-- A later reversion gets its own timestamp; never deduplicate by value.
-- observed_at is the actual feed read time, including on recorder imports.
-- Baseline rows existed at the first read and do not establish arrival time.
-- Missing indicators are not zero. 'removed' requires a validated, complete
-- feed read; outages and rows aging out of the feed are NOT removals.

create table public.sfpuc_getcsv_observations (
  source_code text not null check (btrim(source_code) <> ''),
  sample_date date not null,
  analyte text not null check (btrim(analyte) <> ''),
  observed_at timestamptz not null,
  value_raw text,
  observation_kind text not null
    check (observation_kind in ('baseline', 'appeared', 'changed', 'removed')),
  recording_source text not null
    check (recording_source in ('collector', 'recorder_import')),
  recorded_at timestamptz not null default now(),
  primary key (source_code, sample_date, analyte, observed_at),
  constraint sfpuc_getcsv_value_state check (
    (observation_kind = 'removed' and value_raw is null)
    or (observation_kind <> 'removed' and value_raw is not null and btrim(value_raw) <> '')
  )
);

comment on table public.sfpuc_getcsv_observations is
  'Append-only observed getCSV result changes; separate from DataSF arrival measurements. No alert dispatch.';
comment on column public.sfpuc_getcsv_observations.source_code is
  'Original getCSV source code (including _SL when supplied); normalize when joining the station registry.';
comment on column public.sfpuc_getcsv_observations.observed_at is
  'Time the collector read this value, not laboratory publication time. Preserve recorder read_at on import.';
comment on column public.sfpuc_getcsv_observations.observation_kind is
  'baseline = present on initial read (arrival unknown); appeared/changed/removed = observed feed transition.';

create index sfpuc_getcsv_observations_observed_idx
  on public.sfpuc_getcsv_observations (observed_at desc);

alter table public.sfpuc_getcsv_observations enable row level security;
revoke all on public.sfpuc_getcsv_observations from public, anon, authenticated, service_role;
grant select, insert on public.sfpuc_getcsv_observations to service_role;
