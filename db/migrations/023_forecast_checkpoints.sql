-- Retain four forecast snapshots per Pacific day from the existing refreshes.
begin;

create table if not exists public.forecast_checkpoints (
  forecast_date date not null,
  slot_hour     smallint not null check (slot_hour in (0, 6, 12, 18)),
  generated_at  timestamptz not null,
  snapshot      jsonb not null,
  primary key (forecast_date, slot_hour)
);
alter table public.forecast_checkpoints enable row level security;
revoke all on public.forecast_checkpoints from anon, authenticated;
grant all on public.forecast_checkpoints to service_role;

create or replace function public.bwtf_record_forecast(p_date date, p_snapshot jsonb, p_generated_at timestamptz)
returns void
language sql security definer set search_path = public as $$
  insert into public.forecast_history as fh
    (forecast_date, first_snapshot, first_generated_at, last_snapshot, last_generated_at)
  values (p_date, p_snapshot, p_generated_at, p_snapshot, p_generated_at)
  on conflict (forecast_date) do update
    set last_snapshot     = excluded.last_snapshot,
        last_generated_at = excluded.last_generated_at,
        refreshes         = fh.refreshes + 1
    where excluded.last_generated_at >= fh.last_generated_at;

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
        snapshot = excluded.snapshot
    where excluded.generated_at < fc.generated_at;
$$;
revoke all on function public.bwtf_record_forecast(date, jsonb, timestamptz) from public, anon, authenticated;
grant execute on function public.bwtf_record_forecast(date, jsonb, timestamptz) to service_role;

commit;
