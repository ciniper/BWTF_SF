-- 024: forecast_checkpoints replaces forecast_history (2026-11-12).
--
-- 023 added forecast_checkpoints: the earliest forecast in each Pacific slot
-- starting 00:05, 06:05, 12:05 and 18:05. forecast_history (012) kept only the
-- day's first snapshot (the 00:05 refresh) and its last (~23:35), so its
-- first snapshot is exactly a midnight checkpoint and its last falls at no
-- fixed time. This migration:
--   1. copies every forecast_history first snapshot into forecast_checkpoints,
--      in the slot its generation time falls in (00:05 → slot 0 on every day
--      but 2026-09-27, when 012 was applied at 11:26 → slot 6). A checkpoint
--      already captured is kept unless the copied one is earlier, the same
--      rule the writer uses. The last snapshots are not copied;
--   2. makes bwtf_record_forecast write forecast_checkpoints only;
--   3. drops forecast_history, after checking every first snapshot made it.
--
-- Readers moved in the same change: grade_prospective.py --export reads slot 0,
-- the admin Data sources tile lists forecast_checkpoints.
--
-- Apply by hand in the Supabase SQL editor, away from the :05 and :35 refreshes
-- (a refresh mid-apply only waits for the lock, but a new day's first row
-- arriving between the copy and the check makes the check fail and roll back;
-- re-run it). Safe to re-run: once forecast_history is gone, steps 1 and 3 are
-- skipped.
begin;

-- 1 and 3: copy, check, drop. Guarded so a re-run after the drop is a no-op.
do $$
declare
  missing integer;
begin
  if to_regclass('public.forecast_history') is null then
    raise notice 'forecast_history already dropped; nothing to copy';
    return;
  end if;

  insert into public.forecast_checkpoints as fc (forecast_date, slot_hour, generated_at, snapshot)
  select h.forecast_date,
         case
           when (h.first_generated_at at time zone 'America/Los_Angeles')::time >= time '18:05' then 18
           when (h.first_generated_at at time zone 'America/Los_Angeles')::time >= time '12:05' then 12
           when (h.first_generated_at at time zone 'America/Los_Angeles')::time >= time '06:05' then 6
           else 0
         end,
         h.first_generated_at,
         h.first_snapshot
  from public.forecast_history h
  on conflict (forecast_date, slot_hour) do update
    set generated_at = excluded.generated_at,
        snapshot     = excluded.snapshot
    where excluded.generated_at < fc.generated_at;

  select count(*) into missing
  from public.forecast_history h
  where not exists (select 1 from public.forecast_checkpoints c
                    where c.forecast_date = h.forecast_date
                      and c.generated_at = h.first_generated_at
                      and c.snapshot = h.first_snapshot);
  if missing > 0 then
    raise exception 'forecast_history: % first snapshot(s) not in forecast_checkpoints; nothing dropped', missing;
  end if;

  drop table public.forecast_history;
end $$;

-- 2: the writer, 023's checkpoint insert without the forecast_history half.
create or replace function public.bwtf_record_forecast(p_date date, p_snapshot jsonb, p_generated_at timestamptz)
returns void
language sql security definer set search_path = public as $$
  with capture as (
    select p_generated_at at time zone 'America/Los_Angeles' as local_at
  )
  insert into public.forecast_checkpoints as fc
    (forecast_date, slot_hour, generated_at, snapshot)
  select p_date,
         case
           when local_at::time >= time '18:05' then 18
           when local_at::time >= time '12:05' then 12
           when local_at::time >= time '06:05' then 6
           else 0
         end,
         p_generated_at,
         p_snapshot
  from capture
  where local_at::date = p_date
    and local_at::time >= time '00:05'
  -- Keep the earliest generation even if a retry or concurrent write arrives late.
  on conflict (forecast_date, slot_hour) do update
    set generated_at = excluded.generated_at,
        snapshot     = excluded.snapshot
    where excluded.generated_at < fc.generated_at;
$$;
revoke all on function public.bwtf_record_forecast(date, jsonb, timestamptz) from public, anon, authenticated;
grant execute on function public.bwtf_record_forecast(date, jsonb, timestamptz) to service_role;

commit;
