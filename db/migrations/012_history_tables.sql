-- 012: history tables, and drop the phase-1 watcher table (Chase, 2026-09-27).
--
-- Three questions the database could not answer until now:
--   * what the forecast said on a past day — forecast_predictions is one row,
--     overwritten every refresh → forecast_history: one row per Pacific
--     calendar day holding the FIRST snapshot of that day (the start-of-day
--     forecast the grading uses) and the LAST, written by the production
--     refresh through bwtf_record_forecast;
--   * what the SFPUC feed showed per station per day — alert_log keeps only
--     transitions → feed_station_days: one row per station-day, upserted by
--     every tick with the raw flags, the colours, the worst and last
--     classified status and the raw station object. "Polled and saw nothing"
--     becomes a row, so the no-flag downgrade's assumption and the watcher's
--     miss/lag rate against CIWQS can be measured from here on;
--   * when we first saw each of the city's lab results — DataSF carries no
--     arrival time and the live rules assume a one-day lag → samples: a mirror
--     keyed by station, date, analyte and raw value, first_seen_at stamped,
--     written by the production refresh (shared/samples_mirror.py) and by the
--     backfill: venv/bin/python -m shared.samples_mirror --backfill
-- And: drop watcher_state, the phase-1 Python watcher's table (last written
-- 2026-09-02, the day Railway retired; nothing reads it). The SQL watcher's
-- state is watcher_state_shadow, in shadow AND live mode — the name is a
-- leftover, kept because three functions reference it.
--
-- Apply by hand in the Supabase SQL editor with no simulation active
-- (simulated_cso empty), like every watcher migration. Safe to re-run.

drop table if exists public.watcher_state;

-- ── forecast_history ─────────────────────────────────────────────────────────
create table if not exists public.forecast_history (
  forecast_date      date primary key,        -- Pacific calendar day the snapshots were generated on
  first_snapshot     jsonb not null,          -- the day's first snapshot: the start-of-day forecast
  first_generated_at timestamptz not null,
  last_snapshot      jsonb not null,          -- the day's latest snapshot
  last_generated_at  timestamptz not null,
  refreshes          integer not null default 1
);
alter table public.forecast_history enable row level security;
revoke all on public.forecast_history from anon, authenticated;
grant all on public.forecast_history to service_role;

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
    where excluded.last_generated_at >= fh.last_generated_at;   -- an out-of-order write never moves "last" backwards
$$;
revoke all on function public.bwtf_record_forecast(date, jsonb, timestamptz) from public, anon, authenticated;
grant execute on function public.bwtf_record_forecast(date, jsonb, timestamptz) to service_role;

-- ── feed_station_days ────────────────────────────────────────────────────────
create table if not exists public.feed_station_days (
  station_id    text not null,
  day           date not null,                              -- Pacific calendar day of the tick
  station_name  text not null default '',
  posted        boolean not null default false,             -- the feed's posted flag was set at some tick that day
  cso           boolean not null default false,             -- the feed's CSO flag was set at some tick that day
  status_max    text not null default 'ok' check (status_max in ('ok', 'posted', 'cso')),   -- worst bwtf_classify result that day
  status_last   text not null default 'ok' check (status_last in ('ok', 'posted', 'cso')),  -- the last tick's
  sample_color  text,                                       -- s_color at the last tick (R / G / Y / W)
  posting_color text,                                       -- p_color at the last tick
  raw_last      jsonb,                                      -- the station object as the feed sent it, last tick
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  ticks         integer not null default 1,
  primary key (station_id, day)
);
create index if not exists feed_station_days_day_idx on public.feed_station_days (day);
alter table public.feed_station_days enable row level security;
revoke all on public.feed_station_days from anon, authenticated;
grant all on public.feed_station_days to service_role;

create or replace function public.bwtf_record_feed_day(p_payload jsonb)
returns integer
language plpgsql security definer set search_path = public as $$
declare
  station jsonb;
  sid text;
  st text;
  n integer := 0;
  today date := (now() at time zone 'America/Los_Angeles')::date;
begin
  for station in select * from jsonb_array_elements(p_payload) loop
    sid := station->>'stationid';
    if sid is null or sid = '' then
      continue;
    end if;
    st := bwtf_classify(station->>'cso', station->>'s_color', station->>'posted', station->>'p_color');
    insert into public.feed_station_days as f
      (station_id, day, station_name, posted, cso, status_max, status_last, sample_color, posting_color, raw_last)
    values (sid, today, coalesce(station->>'stationname', ''),
            coalesce(station->>'posted', '') <> '', coalesce(station->>'cso', '') <> '',
            st, st, station->>'s_color', station->>'p_color', station)
    on conflict (station_id, day) do update
      set station_name  = excluded.station_name,
          posted        = f.posted or excluded.posted,
          cso           = f.cso or excluded.cso,
          status_max    = case when f.status_max = 'cso' or excluded.status_max = 'cso' then 'cso'
                               when f.status_max = 'posted' or excluded.status_max = 'posted' then 'posted'
                               else 'ok' end,
          status_last   = excluded.status_last,
          sample_color  = excluded.sample_color,
          posting_color = excluded.posting_color,
          raw_last      = excluded.raw_last,
          last_seen_at  = now(),
          ticks         = f.ticks + 1;
    n := n + 1;
  end loop;
  return n;
end $$;
revoke all on function public.bwtf_record_feed_day(jsonb) from public, anon, authenticated;
grant execute on function public.bwtf_record_feed_day(jsonb) to service_role;

-- ── the tick: 004's body plus one guarded call to bwtf_record_feed_day ───────
create or replace function public.bwtf_shadow_tick()
returns void
language plpgsql security definer set search_path = public as $$
declare
  cfg_mode   text;
  ping_url   text;
  fetch_url  text;
  resp       record;
  raw        text;
  payload    jsonb;
  summary    jsonb;
  processed  boolean := false;
  req_id     bigint;
begin
  select value into cfg_mode from watcher_config where key = 'mode';
  if coalesce(cfg_mode, 'off') = 'off' then
    return;
  end if;

  begin
    select r.status_code, r.content, r.timed_out, r.error_msg into resp
      from net._http_response r
      join watcher_runtime rt on r.id = rt.last_request_id
     where rt.id = 1;

    if resp.status_code = 200 and resp.content is not null then
      raw := substring(resp.content from position('[' in resp.content));
      raw := left(raw, length(raw) - position(']' in reverse(raw)) + 1);
      payload := raw::jsonb;
      if jsonb_array_length(payload) > 0 then
        summary := bwtf_process_payload(payload, coalesce(cfg_mode, 'shadow'));
        processed := true;
        -- 012: one row per station-day of what the feed showed ("polled and
        -- saw nothing" included). Its own block: a failure here must never
        -- stall the tick or look like a dead watcher (the forecast's health
        -- gate reads the error column), so it is noted in the summary only.
        begin
          perform bwtf_record_feed_day(payload);
        exception when others then
          summary := coalesce(summary, '{}'::jsonb) || jsonb_build_object('feed_day_error', sqlerrm);
        end;
        update watcher_runtime
           set last_processed_at = now(), last_fetch_status = resp.status_code,
               last_summary = summary, last_error = null
         where id = 1;
      end if;
    elsif resp.status_code is not null then
      update watcher_runtime
         set last_fetch_status = resp.status_code,
             last_error = 'HTTP ' || resp.status_code, last_error_at = now()
       where id = 1;
    elsif resp.error_msg is not null or resp.timed_out then
      update watcher_runtime
         set last_error = coalesce(resp.error_msg, 'timed out'),
             last_error_at = now()
       where id = 1;
    end if;
  exception when others then
    update watcher_runtime
       set last_error = 'harvest: ' || sqlerrm, last_error_at = now()
     where id = 1;
  end;

  if processed then
    begin
      select value into ping_url from watcher_config where key = 'healthchecks_ping_url';
      if ping_url is not null and ping_url <> '' then
        perform net.http_get(ping_url, timeout_milliseconds := 10000);
      end if;
    exception when others then
      null;
    end;
  end if;

  begin
    select value into fetch_url from watcher_config where key = 'sfpuc_url';
    if fetch_url is not null and fetch_url <> '' then
      select net.http_get(fetch_url, timeout_milliseconds := 15000) into req_id;
      update watcher_runtime set last_request_id = req_id, requested_at = now() where id = 1;
    end if;
  exception when others then
    update watcher_runtime
       set last_error = 'issue: ' || sqlerrm, last_error_at = now()
     where id = 1;
  end;
end $$;

revoke all on function public.bwtf_shadow_tick() from public, anon, authenticated;
grant execute on function public.bwtf_shadow_tick() to service_role;

-- ── samples ──────────────────────────────────────────────────────────────────
create table if not exists public.samples (
  station_id    text not null,      -- DataSF "source" = the station id in shared/stations.py
  sample_date   date not null,
  analyte       text not null,      -- ENTERO | COLI_E | COLI_FECAL | COLI_TOTAL
  value_raw     text not null,      -- as reported: '41', '<10', '>24196'
  value         numeric,            -- shared/standards.parse_result
  exceeds       boolean,            -- shared/standards.flag_exceedances (ratio rule included) when first seen
  first_seen_at timestamptz not null default now(),
  primary key (station_id, sample_date, analyte, value_raw)
);
create index if not exists samples_date_idx on public.samples (sample_date);
alter table public.samples enable row level security;
revoke all on public.samples from anon, authenticated;
grant all on public.samples to service_role;
