-- 014: samples.source — was the row picked up in real time or backfilled?
-- (Chase, 2026-09-27)
--
-- first_seen_at is only an arrival time for rows the production refresh
-- inserted as the city published them. The 2026-09-27 backfill stamped
-- 20,662 historical rows with that afternoon, so a lag computed from them is
-- meaningless. This column says which is which, set by the writer
-- (shared/samples_mirror.py: mirror(..., source=...)); the view samples_lag
-- is the honest lag series — refresh rows only.
--
-- Apply by hand in the Supabase SQL editor. Safe to re-run.

alter table public.samples
  add column if not exists source text not null default 'refresh'
  check (source in ('backfill', 'refresh'));

-- everything present before the first real-time insert was the backfill
-- (it ran 18:26–18:27 UTC; DataSF had not reloaded since 2026-09-26 23:04, so
-- no refresh row could exist before this cutoff)
update public.samples
   set source = 'backfill'
 where source = 'refresh' and first_seen_at < '2026-09-27T18:30:00+00';

create index if not exists samples_source_idx on public.samples (source);

-- the lag series: collection day → the Pacific day we first saw the row
create or replace view public.samples_lag as
  select sample_date, station_id, analyte, value_raw, value, exceeds, first_seen_at,
         (first_seen_at at time zone 'America/Los_Angeles')::date - sample_date as lag_days
    from public.samples
   where source = 'refresh';
revoke all on public.samples_lag from anon, authenticated;
grant select on public.samples_lag to service_role;
