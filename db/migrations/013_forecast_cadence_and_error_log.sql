-- 013: tick error log, a constant forecast cadence, and forecast capture on change
-- (Chase, 2026-09-27).
--
-- * watcher_errors — every error the tick meets (HTTP status, transport
--   timeout, harvest or issue exception, feed-day recording, refresh trigger)
--   as a row, where until now only the LAST error survived in watcher_runtime
--   and the 2026-09-22 one is already unexplainable. bwtf_log_error swallows
--   its own failures and prunes rows older than 90 days.
-- * bwtf_forecast_refresh + cron 'bwtf-forecast-refresh' every 30 minutes
--   (:05 and :35) — the production forecast recomputes on a clock, not when
--   a visitor happens to find it stale; the page now always serves the stored
--   snapshot. The URL lives in watcher_config 'forecast_refresh_url'. The pg_net
--   timeout is 55 s: the compute takes 5–20 s, and the old keep-alive's 10 s
--   gave up before it finished. The hourly 'bwtf-keepalive' is unscheduled —
--   this job does its work (Supabase gateway activity through the app).
-- * the tick also calls bwtf_forecast_refresh when a REAL transition was
--   logged, so a CSO is on the forecast within a minute of the feed.
-- * forecast_changes — the forecast captured every time it CHANGES: one row
--   per distinct fingerprint (zone and basin percentages, live rules fired,
--   rain, feed statuses), with first_at, last_confirmed_at and how many
--   refreshes confirmed it unchanged. Written by the production refresh
--   through bwtf_record_forecast_change. Between two rows the forecast is
--   KNOWN to have been the earlier one (confirmed at every refresh), not
--   assumed. forecast_history (first/last per day) stays.
--
-- Apply by hand in the Supabase SQL editor with no simulation active. Safe to
-- re-run. The tick is 012's body plus the blocks marked "-- 013:" … "-- /013".

-- ── watcher_errors ────────────────────────────────────────────────────────────
create table if not exists public.watcher_errors (
  id          bigint generated always as identity primary key,
  at          timestamptz not null default now(),
  kind        text not null,        -- http | fetch | harvest | issue | feed_day | refresh | refresh_trigger
  message     text not null default '',
  status_code integer
);
create index if not exists watcher_errors_at_idx on public.watcher_errors (at);
alter table public.watcher_errors enable row level security;
revoke all on public.watcher_errors from anon, authenticated;
grant all on public.watcher_errors to service_role;

create or replace function public.bwtf_log_error(p_kind text, p_message text, p_status integer)
returns void
language plpgsql security definer set search_path = public as $$
begin
  insert into public.watcher_errors (kind, message, status_code)
  values (p_kind, left(coalesce(p_message, ''), 2000), p_status);
  delete from public.watcher_errors where at < now() - interval '90 days';
exception when others then
  null;   -- logging must never take the tick down
end $$;
revoke all on function public.bwtf_log_error(text, text, integer) from public, anon, authenticated;
grant execute on function public.bwtf_log_error(text, text, integer) to service_role;

-- ── forecast refresh on a clock ──────────────────────────────────────────────
insert into public.watcher_config (key, value)
values ('forecast_refresh_url', 'https://bwtf-sf.vercel.app/forecast/api/refresh')
on conflict (key) do nothing;

create or replace function public.bwtf_forecast_refresh()
returns void
language plpgsql security definer set search_path = public as $$
declare
  url text;
begin
  select value into url from watcher_config where key = 'forecast_refresh_url';
  if url is not null and url <> '' then
    perform net.http_get(url, timeout_milliseconds := 55000);
  end if;
exception when others then
  perform bwtf_log_error('refresh', sqlerrm, null);
end $$;
revoke all on function public.bwtf_forecast_refresh() from public, anon, authenticated;
grant execute on function public.bwtf_forecast_refresh() to service_role;

do $$
begin
  perform cron.unschedule('bwtf-forecast-refresh');
exception when others then
  null;
end $$;
select cron.schedule('bwtf-forecast-refresh', '5,35 * * * *', 'select public.bwtf_forecast_refresh()');

do $$
begin
  perform cron.unschedule('bwtf-keepalive');   -- superseded by the refresh job
exception when others then
  null;
end $$;

-- ── forecast_changes ─────────────────────────────────────────────────────────
create table if not exists public.forecast_changes (
  id                bigint generated always as identity primary key,
  first_at          timestamptz not null,        -- the refresh that first produced this forecast
  last_confirmed_at timestamptz not null,        -- the latest refresh that produced it unchanged
  refreshes         integer not null default 1,  -- how many refreshes produced it
  fingerprint       text not null,               -- features/forecast/page.py _fingerprint
  snapshot          jsonb not null
);
create index if not exists forecast_changes_first_at_idx on public.forecast_changes (first_at);
alter table public.forecast_changes enable row level security;
revoke all on public.forecast_changes from anon, authenticated;
grant all on public.forecast_changes to service_role;

create or replace function public.bwtf_record_forecast_change(p_fingerprint text, p_snapshot jsonb, p_at timestamptz)
returns boolean
language plpgsql security definer set search_path = public as $$
declare
  last_id bigint;
  last_fp text;
begin
  select id, fingerprint into last_id, last_fp
    from public.forecast_changes order by first_at desc, id desc limit 1;
  if last_fp is not null and last_fp = p_fingerprint then
    update public.forecast_changes
       set last_confirmed_at = greatest(last_confirmed_at, p_at), refreshes = refreshes + 1
     where id = last_id;
    return false;
  end if;
  insert into public.forecast_changes (first_at, last_confirmed_at, fingerprint, snapshot)
  values (p_at, p_at, p_fingerprint, p_snapshot);
  return true;
end $$;
revoke all on function public.bwtf_record_forecast_change(text, jsonb, timestamptz) from public, anon, authenticated;
grant execute on function public.bwtf_record_forecast_change(text, jsonb, timestamptz) to service_role;

-- ── the tick: 012's body plus the "-- 013" blocks ────────────────────────────
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
          -- 013: keep the error
          perform bwtf_log_error('feed_day', sqlerrm, null);
          -- /013
        end;
        -- 013: a real transition (posted / cso / cleared) re-runs the forecast now
        -- instead of at the next half-hour, so a discharge shows on the page
        -- within a minute. Its own block; a failure is logged, never fatal.
        begin
          if coalesce((summary->>'transitions')::int, 0) > 0 then
            perform bwtf_forecast_refresh();
          end if;
        exception when others then
          perform bwtf_log_error('refresh_trigger', sqlerrm, null);
        end;
        -- /013
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
        -- 013: keep the error
        perform bwtf_log_error('http', 'HTTP ' || resp.status_code, resp.status_code);
        -- /013
    elsif resp.error_msg is not null or resp.timed_out then
      update watcher_runtime
         set last_error = coalesce(resp.error_msg, 'timed out'),
             last_error_at = now()
       where id = 1;
        -- 013: keep the error
        perform bwtf_log_error('fetch', coalesce(resp.error_msg, 'timed out'), null);
        -- /013
    end if;
  exception when others then
    update watcher_runtime
       set last_error = 'harvest: ' || sqlerrm, last_error_at = now()
     where id = 1;
    -- 013: keep the error
    perform bwtf_log_error('harvest', sqlerrm, null);
    -- /013
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
    -- 013: keep the error
    perform bwtf_log_error('issue', sqlerrm, null);
    -- /013
  end;
end $$;

revoke all on function public.bwtf_shadow_tick() from public, anon, authenticated;
grant execute on function public.bwtf_shadow_tick() to service_role;
