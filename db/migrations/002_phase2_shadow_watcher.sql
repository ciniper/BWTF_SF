-- BWTF → Supabase migration, Phase 2: the poll/detect/dispatch loop in Postgres
-- (pg_cron + pg_net), deployed in SHADOW MODE — it records would-send decisions
-- to alert_log (source='pg_shadow') and SENDS NOTHING. The in-process watcher
-- keeps sending during the parallel run. Run in the BWTF project's SQL Editor.
--
-- Lessons reused from Surftober's proven fetchers:
--   * pg_net is async: each tick HARVESTS the previous request's response from
--     net._http_response, then ISSUES a fresh request (request id kept in a
--     single-row runtime table).
--   * Do NOT set a custom User-Agent on the SFPUC request — pg_net adds its
--     own, and a duplicate UA makes SFPUC's IIS reject with 400.
--   * Guard each step so one bad response never blocks the rest.
--
-- Dead-man's switch: the tick pings healthchecks.io ONLY after successfully
-- parsing + processing a fresh 200 payload — so a dead cron, a broken feed, or
-- a parse-breaking upstream change all stop the pings (staleness and liveness
-- collapse into one signal, which is the point).

-- ── extensions ───────────────────────────────────────────────────────────────
-- If either errors, enable it in Dashboard → Database → Extensions first.
create extension if not exists pg_net;
create extension if not exists pg_cron;

-- ── config / runtime / state tables ─────────────────────────────────────────
create table public.watcher_config (
  key        text primary key,
  value      text not null,
  updated_at timestamptz not null default now()
);

-- mode: 'shadow' (log only — current), 'live' (pg sends, reserved for the
-- flip), 'off' (tick exits immediately).
insert into public.watcher_config (key, value) values ('mode', 'shadow')
  on conflict (key) do nothing;

-- Shared simulator store — BOTH the Python thread and this pg path read it,
-- so simulated events produce matching decisions in the parallel-run diff.
create table public.simulated_cso (
  station_id text primary key,
  created_at timestamptz not null default now()
);

-- The pg path's own state twin; the thread keeps owning watcher_state until
-- the flip. Same shape + semantics (last observed status per station).
create table public.watcher_state_shadow (
  station_id   text primary key,
  station_name text not null default '',
  status       text not null check (status in ('ok', 'posted', 'cso')),
  updated_at   timestamptz not null default now()
);

create trigger watcher_state_shadow_updated_at
  before update on public.watcher_state_shadow
  for each row execute function public.set_updated_at();

-- Single-row pipeline state for the async fetch/harvest cycle.
create table public.watcher_runtime (
  id                 integer primary key default 1 check (id = 1),
  last_request_id    bigint,
  requested_at       timestamptz,
  last_fetch_status  integer,
  last_processed_at  timestamptz,
  last_summary       jsonb
);
insert into public.watcher_runtime (id) values (1) on conflict (id) do nothing;

-- ── classification: EXACT mirror of the Python side ─────────────────────────
-- shared/sfpuc_api.py: has_cso = bool(item.cso); status from s_color
-- (R→posted, G→safe, Y/W→not-sampled variants) with the posted+p_color∈{R,G}
-- fallback. watcher.classify(): cso if has_cso else posted if status==posted
-- else ok (not-sampled variants collapse to ok).
create or replace function public.bwtf_classify(
  p_cso text, p_s_color text, p_posted text, p_p_color text
) returns text
language plpgsql immutable as $$
begin
  if p_cso is not null and p_cso <> '' then
    return 'cso';
  end if;
  if upper(coalesce(p_s_color, '')) = 'R' then
    return 'posted';
  end if;
  -- Y (not sampled) and W (not routinely sampled) map to StationStatus values
  -- that watcher.classify() collapses to 'ok'; G and null likewise end 'ok'
  -- unless the posted fallback below fires. The fallback only applies when
  -- s_color didn't already decide (mirrors _parse_station_status's early
  -- returns for R/Y/W).
  if upper(coalesce(p_s_color, '')) not in ('Y', 'W')
     and coalesce(p_posted, '') <> ''
     and upper(coalesce(p_p_color, '')) in ('R', 'G') then
    return 'posted';
  end if;
  return 'ok';
end $$;

-- ── the testable core: process one SFPUC payload ─────────────────────────────
-- Takes the parsed station array, applies the simulated-CSO overlay, diffs
-- against watcher_state_shadow with the thread's exact edge-trigger semantics
-- (empty state → silent baseline; unseen station → silent; alert only on
-- severity increase), records would-send decisions, updates shadow state.
-- Exposed via RPC to service_role so it can be behaviorally tested with
-- synthetic payloads.
create or replace function public.bwtf_process_payload(p_payload jsonb)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  sev            constant jsonb := '{"ok":0, "posted":1, "cso":2}'::jsonb;
  is_baseline    boolean;
  station        jsonb;
  new_status     text;
  prev_status    text;
  n_stations     integer := 0;
  transitions    jsonb := '[]'::jsonb;
  t              jsonb;
  recipients     jsonb;
  n_recipients   integer := 0;
  ev_type        text;
  any_simulated  boolean := false;
begin
  select not exists (select 1 from watcher_state_shadow) into is_baseline;

  for station in select * from jsonb_array_elements(p_payload)
  loop
    n_stations := n_stations + 1;
    new_status := bwtf_classify(station->>'cso', station->>'s_color',
                                station->>'posted', station->>'p_color');
    -- simulated overlay (mirrors apply_simulated_cso: forces has_cso)
    if exists (select 1 from simulated_cso sc where sc.station_id = station->>'stationid') then
      new_status := 'cso';
    end if;

    select ws.status into prev_status
      from watcher_state_shadow ws where ws.station_id = station->>'stationid';

    if not is_baseline and prev_status is not null
       and (sev->>new_status)::int > (sev->>prev_status)::int then
      transitions := transitions || jsonb_build_object(
        'station_id',   station->>'stationid',
        'station_name', station->>'stationname',
        'from',         prev_status,
        'to',           new_status,
        'simulated',    exists (select 1 from simulated_cso sc
                                where sc.station_id = station->>'stationid'));
    end if;

    insert into watcher_state_shadow as ws (station_id, station_name, status)
    values (station->>'stationid', coalesce(station->>'stationname', ''), new_status)
    on conflict (station_id) do update
      set status = excluded.status, station_name = excluded.station_name;
  end loop;

  if jsonb_array_length(transitions) > 0 then
    -- would-send decisions: every active subscriber whose stations intersect
    select coalesce(jsonb_agg(jsonb_build_object(
             'email',        s.email,
             'phone_number', s.phone_number,
             'station_names', (select jsonb_agg(x->>'station_name')
                               from jsonb_array_elements(transitions) x
                               where (x->>'station_id') = any (s.station_ids)),
             'would', case
               when s.email <> '' and s.phone_number <> '' then jsonb_build_array('email','sms')
               when s.email <> '' then jsonb_build_array('email')
               when s.phone_number <> '' then jsonb_build_array('sms')
               else '[]'::jsonb end)), '[]'::jsonb),
           count(*)
      into recipients, n_recipients
      from subscribers s
     where s.active
       and s.station_ids && (select coalesce(array_agg(x->>'station_id'), '{}')
                             from jsonb_array_elements(transitions) x);

    select bool_or((x->>'simulated')::boolean),
           case when bool_or(x->>'to' = 'cso') then 'cso' else 'posted' end
      into any_simulated, ev_type
      from jsonb_array_elements(transitions) x;

    insert into alert_log (source, event_type, station_ids, station_names,
                           recipient_count, channel, simulated, results)
    values ('pg_shadow', ev_type,
            (select array_agg(x->>'station_id')   from jsonb_array_elements(transitions) x),
            (select array_agg(x->>'station_name') from jsonb_array_elements(transitions) x),
            coalesce(n_recipients, 0), 'shadow', coalesce(any_simulated, false),
            jsonb_build_object('transitions', transitions, 'would_send', recipients));
  end if;

  return jsonb_build_object(
    'stations', n_stations, 'baselined', is_baseline,
    'transitions', coalesce(jsonb_array_length(transitions), 0),
    'recipients', coalesce(n_recipients, 0));
end $$;

revoke all on function public.bwtf_process_payload(jsonb) from public, anon, authenticated;
grant execute on function public.bwtf_process_payload(jsonb) to service_role;

-- ── the cron tick: harvest → process → ping → issue ─────────────────────────
create or replace function public.bwtf_shadow_tick()
returns void
language plpgsql security definer set search_path = public as $$
declare
  cfg_mode   text;
  ping_url   text;
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

  -- 1. harvest the previous request's response (guarded)
  begin
    select r.status_code, r.content into resp
      from net._http_response r
      join watcher_runtime rt on r.id = rt.last_request_id
     where rt.id = 1;

    if resp.status_code = 200 and resp.content is not null then
      -- XML-wrapped JSON: keep the [...] slice
      raw := substring(resp.content from position('[' in resp.content));
      raw := left(raw, length(raw) - position(']' in reverse(raw)) + 1);
      payload := raw::jsonb;
      if jsonb_array_length(payload) > 0 then
        summary := bwtf_process_payload(payload);
        processed := true;
        update watcher_runtime
           set last_processed_at = now(), last_fetch_status = resp.status_code,
               last_summary = summary
         where id = 1;
      end if;
    elsif resp.status_code is not null then
      update watcher_runtime set last_fetch_status = resp.status_code where id = 1;
    end if;
  exception when others then
    null;  -- malformed/expired response: skip; no ping this tick
  end;

  -- 2. dead-man's switch: ping ONLY after real, fresh data was processed
  if processed then
    begin
      select value into ping_url from watcher_config where key = 'healthchecks_ping_url';
      if ping_url is not null and ping_url <> '' then
        perform net.http_get(ping_url, timeout_milliseconds := 10000);
      end if;
    exception when others then
      null;  -- monitoring must never break the pipeline
    end;
  end if;

  -- 3. issue the next fetch (bare: no custom UA — see header comment)
  begin
    select net.http_get(
      'https://infrastructure.sfwater.org/lims.asmx/getBeaches',
      timeout_milliseconds := 15000
    ) into req_id;
    update watcher_runtime set last_request_id = req_id, requested_at = now() where id = 1;
  exception when others then
    null;
  end;
end $$;

revoke all on function public.bwtf_shadow_tick() from public, anon, authenticated;
grant execute on function public.bwtf_shadow_tick() to service_role;

-- ── lockdown for the new tables ──────────────────────────────────────────────
alter table public.watcher_config       enable row level security;
alter table public.simulated_cso        enable row level security;
alter table public.watcher_state_shadow enable row level security;
alter table public.watcher_runtime      enable row level security;
revoke all on public.watcher_config, public.simulated_cso,
              public.watcher_state_shadow, public.watcher_runtime
  from anon, authenticated;
grant all on public.watcher_config, public.simulated_cso,
             public.watcher_state_shadow, public.watcher_runtime
  to service_role;

-- ── schedule: every minute (harvest lag ≈1 min → effective cadence beats the
--    thread's 2 min); re-schedulable idempotently ─────────────────────────────
do $$
begin
  perform cron.unschedule('bwtf-shadow-tick');
exception when others then
  null;  -- first install
end $$;
select cron.schedule('bwtf-shadow-tick', '* * * * *', 'select public.bwtf_shadow_tick()');

-- Prime the pipeline now (first tick issues the initial request; the next
-- cron tick will harvest + baseline silently).
select public.bwtf_shadow_tick();
