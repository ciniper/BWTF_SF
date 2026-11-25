-- #44: the confirmed 4613 / BAY#211_SL flag belongs to Dogpatch.
-- Coordinate this migration with the Python deployment after explicit approval:
-- the next scheduled poll can clear Aquatic Park and alert Crane Cove.
-- Raw feed_station_days and historical alert records are intentionally untouched.
-- All watcher behavior below is copied from 20261118051745 except the classifier
-- input. Keep the pure helper in parity with shared/cso_mapping.py.
create or replace function public.bwtf_effective_cso(p_station jsonb, p_payload jsonb)
returns text language sql immutable set search_path = public as $$
  select case
    when p_station->>'stationid' = '4613' and p_station->>'cso' = 'BAY#211_SL'
      then null
    when p_station->>'stationid' = '4620' and coalesce(p_station->>'cso', '') = ''
      and exists (select 1 from jsonb_array_elements(p_payload) s
                  where s->>'stationid' = '4613' and s->>'cso' = 'BAY#211_SL')
      then 'BAY#211_SL'
    else p_station->>'cso'
  end
$$;
revoke all on function public.bwtf_effective_cso(jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.bwtf_effective_cso(jsonb, jsonb) to service_role;

create or replace function public.bwtf_process_payload(p_payload jsonb, p_mode text default 'shadow')
returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  sev            constant jsonb := '{"ok":0, "posted":1, "cso":2}'::jsonb;
  is_baseline    boolean;
  station        jsonb;
  sid            text;
  sname          text;
  real_status    text;
  prev_status    text;
  prev_kind      text;
  kind_now       text;
  rec_now        text[];
  n_stations     integer := 0;
  real_up        jsonb := '[]'::jsonb;
  real_down      jsonb := '[]'::jsonb;
  sim_up         jsonb := '[]'::jsonb;
  sim_down       jsonb := '[]'::jsonb;
  sim_targets    text[] := '{}';
  sim_untargeted boolean := false;
  n_recipients   integer := 0;
  log_source     text;
begin
  perform pg_advisory_xact_lock(61720415);
  -- A status carried overnight counts as already up today, even on its clear tick.
  insert into alert_status_day
    select station_id,status,(now() at time zone 'America/Los_Angeles')::date
    from watcher_state_shadow where status in ('posted','cso')
    on conflict(station_id,status) do update set day=excluded.day;
  select not exists (select 1 from watcher_state_shadow) into is_baseline;
  log_source := case when p_mode = 'live' then 'pg_live' else 'pg_shadow' end;

  for station in select * from jsonb_array_elements(p_payload)
  loop
    n_stations  := n_stations + 1;
    sid         := station->>'stationid';
    sname       := coalesce(station->>'stationname', '');
    real_status := bwtf_classify(bwtf_effective_cso(station, p_payload), station->>'s_color',
                                 station->>'posted', station->>'p_color');
    kind_now := null; rec_now := null;
    select sc.kind, sc.recipients into kind_now, rec_now
      from simulated_cso sc where sc.station_id = sid;

    select ws.status, coalesce(ws.sim_kind, case when ws.sim_active then 'cso' end)
      into prev_status, prev_kind
      from watcher_state_shadow ws where ws.station_id = sid;

    if not is_baseline and prev_status is not null then
      if (sev->>real_status)::int > (sev->>prev_status)::int then
        real_up := real_up || jsonb_build_object(
          'station_id', sid, 'station_name', sname,
          'from', prev_status, 'to', real_status, 'simulated', false);
      elsif (sev->>real_status)::int < (sev->>prev_status)::int then
        real_down := real_down || jsonb_build_object(
          'station_id', sid, 'station_name', sname,
          'from', prev_status, 'to', real_status, 'simulated', false);
      end if;

      if kind_now is not null and prev_kind is null then
        if (sev->>kind_now)::int > (sev->>real_status)::int then
          sim_up := sim_up || jsonb_build_object(
            'station_id', sid, 'station_name', sname,
            'from', real_status, 'to', kind_now, 'simulated', true);
          if rec_now is null then sim_untargeted := true;
          else sim_targets := array(select distinct unnest(sim_targets || rec_now)); end if;
        end if;
      elsif kind_now is null and prev_kind is not null then
        if (sev->>prev_kind)::int > (sev->>real_status)::int then
          sim_down := sim_down || jsonb_build_object(
            'station_id', sid, 'station_name', sname,
            'from', prev_kind, 'to', real_status, 'simulated', true);
        end if;
      elsif kind_now is not null and prev_kind is not null and kind_now <> prev_kind then
        if (sev->>kind_now)::int > (sev->>prev_kind)::int and (sev->>kind_now)::int > (sev->>real_status)::int then
          sim_up := sim_up || jsonb_build_object(
            'station_id', sid, 'station_name', sname,
            'from', prev_kind, 'to', kind_now, 'simulated', true);
          if rec_now is null then sim_untargeted := true;
          else sim_targets := array(select distinct unnest(sim_targets || rec_now)); end if;
        elsif (sev->>kind_now)::int < (sev->>prev_kind)::int and (sev->>prev_kind)::int > (sev->>real_status)::int then
          sim_down := sim_down || jsonb_build_object(
            'station_id', sid, 'station_name', sname,
            'from', prev_kind,
            'to', case when (sev->>kind_now)::int > (sev->>real_status)::int then kind_now else real_status end,
            'simulated', true);
        end if;
      end if;
    end if;

    insert into watcher_state_shadow as ws (station_id, station_name, status, sim_active, sim_kind)
    values (sid, sname, real_status, kind_now is not null, kind_now)
    on conflict (station_id) do update
      set status = excluded.status, station_name = excluded.station_name,
          sim_active = excluded.sim_active, sim_kind = excluded.sim_kind;
  end loop;

  -- Flush expired windows before accepting new events; an event exactly at
  -- the deadline joins the original batch. State above is fresh for context.
  if p_mode='live' then perform bwtf_flush_alerts(now()-interval '1 microsecond'); end if;
  if jsonb_array_length(real_up) > 0 then
    n_recipients := n_recipients + bwtf_log_escalations(real_up, false, p_mode, log_source, null);
  end if;
  if jsonb_array_length(sim_up) > 0 then
    n_recipients := n_recipients + bwtf_log_escalations(
      sim_up, true, p_mode, log_source,
      case when sim_untargeted then null else sim_targets end);
  end if;
  if jsonb_array_length(real_down) > 0 then
    perform bwtf_log_downgrades(real_down, false, log_source);
  end if;
  if jsonb_array_length(sim_down) > 0 then
    perform bwtf_log_downgrades(sim_down, true, log_source);
  end if;

  insert into alert_status_day
    select station_id,status,(now() at time zone 'America/Los_Angeles')::date
    from watcher_state_shadow where status in ('posted','cso')
    on conflict(station_id,status) do update set day=excluded.day;
  if p_mode='live' then perform bwtf_flush_alerts(now()); end if;
  return jsonb_build_object(
    'stations' , n_stations, 'baselined', is_baseline, 'mode', p_mode,
    'transitions', jsonb_array_length(real_up) + jsonb_array_length(sim_up),
    'downgrades',  jsonb_array_length(real_down) + jsonb_array_length(sim_down),
    'simulated_transitions', jsonb_array_length(sim_up),
    'simulated_downgrades',  jsonb_array_length(sim_down),
    'recipients', n_recipients);
end $$;

revoke all on function public.bwtf_process_payload(jsonb, text) from public, anon, authenticated;
grant execute on function public.bwtf_process_payload(jsonb, text) to service_role;
