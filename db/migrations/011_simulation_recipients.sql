-- 011: a simulation can target specific subscribers.
--
-- Until now every simulation dispatched TEST alerts to EVERY active
-- subscriber of the simulated sites — fine with test accounts, not with real
-- people signed up (Sept 2026). simulated_cso.recipients (text[]) lists the
-- emails (or phone numbers) the TEST alert may go to; null keeps today's
-- behaviour (everyone matching). The station match still applies — the test
-- exercises the same subscriber-matching logic as a real event; targeting
-- only narrows it. Real transitions are never filtered.
--
-- bwtf_log_escalations gains p_only_recipients (the 4-arg signature is
-- dropped so PostgreSQL never has two candidates). bwtf_process_payload
-- (010's shape) passes the union of the involved simulations' recipient
-- lists for simulated escalations — null if any involved simulation is
-- untargeted — and null for real ones.
--
-- Apply with NO simulation active (guarded), after 010.
-- Tests: db/scripts/test_011_simulation_recipients.py (+ re-run 010, 009, 006).

begin;

do $$
begin
  if exists (select 1 from public.simulated_cso) then
    raise exception using message =
      'A simulation is active. Clear it (/alerts → Simulation → Clear) before applying 011.';
  end if;
end $$;

alter table public.simulated_cso
  add column if not exists recipients text[];   -- null = every matching subscriber

drop function if exists public.bwtf_log_escalations(jsonb, boolean, text, text);

create or replace function public.bwtf_log_escalations(
  p_transitions jsonb, p_simulated boolean, p_mode text, p_log_source text,
  p_only_recipients text[] default null
) returns integer
language plpgsql security definer set search_path = public as $$
declare
  recipients   jsonb;
  n_recipients integer := 0;
  ev_type      text;
  dispatched   jsonb;
  log_channel  text;
begin
  select coalesce(jsonb_agg(jsonb_build_object(
           'email',        s.email,
           'phone_number', s.phone_number,
           'carrier',      s.carrier,
           'station_ids',  to_jsonb(s.station_ids),
           'station_names', (select jsonb_agg(x->>'station_name')
                             from jsonb_array_elements(p_transitions) x
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
                           from jsonb_array_elements(p_transitions) x)
     and (p_only_recipients is null
          or lower(s.email) = any (p_only_recipients)
          or (s.phone_number <> '' and s.phone_number = any (p_only_recipients)));

  select case when bool_or(x->>'to' = 'cso') then 'cso' else 'posted' end
    into ev_type
    from jsonb_array_elements(p_transitions) x;

  if p_mode = 'live' then
    dispatched := bwtf_dispatch_live(p_transitions, recipients, p_simulated);
    if dispatched is null then
      log_channel := 'config_missing';
      dispatched := recipients;
    else
      log_channel := 'mixed';
    end if;
  else
    log_channel := 'shadow';
    dispatched := recipients;
  end if;

  insert into alert_log (source, event_type, station_ids, station_names,
                         recipient_count, channel, simulated, results)
  values (p_log_source, ev_type,
          (select array_agg(x->>'station_id')   from jsonb_array_elements(p_transitions) x),
          (select array_agg(x->>'station_name') from jsonb_array_elements(p_transitions) x),
          coalesce(n_recipients, 0), log_channel, p_simulated,
          jsonb_build_object('transitions', p_transitions, 'recipients', dispatched)
            || case when p_only_recipients is null then '{}'::jsonb
                    else jsonb_build_object('only_recipients', to_jsonb(p_only_recipients)) end);
  return coalesce(n_recipients, 0);
end $$;

revoke all on function public.bwtf_log_escalations(jsonb, boolean, text, text, text[]) from public, anon, authenticated;
grant execute on function public.bwtf_log_escalations(jsonb, boolean, text, text, text[]) to service_role;

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
  select not exists (select 1 from watcher_state_shadow) into is_baseline;
  log_source := case when p_mode = 'live' then 'pg_live' else 'pg_shadow' end;

  for station in select * from jsonb_array_elements(p_payload)
  loop
    n_stations  := n_stations + 1;
    sid         := station->>'stationid';
    sname       := coalesce(station->>'stationname', '');
    real_status := bwtf_classify(station->>'cso', station->>'s_color',
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

  return jsonb_build_object(
    'stations', n_stations, 'baselined', is_baseline, 'mode', p_mode,
    'transitions', jsonb_array_length(real_up) + jsonb_array_length(sim_up),
    'downgrades',  jsonb_array_length(real_down) + jsonb_array_length(sim_down),
    'simulated_transitions', jsonb_array_length(sim_up),
    'simulated_downgrades',  jsonb_array_length(sim_down),
    'recipients', n_recipients);
end $$;

revoke all on function public.bwtf_process_payload(jsonb, text) from public, anon, authenticated;
grant execute on function public.bwtf_process_payload(jsonb, text) to service_role;

commit;
