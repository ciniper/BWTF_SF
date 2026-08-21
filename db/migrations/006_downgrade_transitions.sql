-- 006: log DOWNGRADE transitions so events have an end, not just a start.
--
-- Motivation: the /cso-history event timeline plots our own real-time
-- detections from alert_log, but bwtf_process_payload only collected severity
-- INCREASES into `transitions` — a clear (posted→ok, cso→ok, cso→posted)
-- silently overwrote watcher_state_shadow and left every event window
-- open-ended. This replaces the function (same signature, 004's version) to
-- also collect severity decreases and log them:
--
--   event_type='cleared', recipient_count=0, channel='log',
--   source = 'pg_live' / 'pg_shadow' (whichever mode produced it),
--   results = {"transitions": [{station_id, station_name, from, to, simulated}]}
--
-- Clears are LOG-ONLY: no recipients are looked up and nothing is dispatched
-- in either mode. The escalation path is byte-for-byte 004's.
--
-- Known quirk, tolerated by design: deleting a simulated_cso row makes the
-- station drop back on the next tick, and that downgrade is logged with
-- simulated=false (the simulation row is already gone, so there's nothing to
-- flag it by). The timeline page ignores a 'cleared' with no matching open
-- window, so these dangling ends are harmless.
--
-- Paste order: after 001–005. Applying this changes logging only.

create or replace function public.bwtf_process_payload(p_payload jsonb, p_mode text default 'shadow')
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
  downgrades     jsonb := '[]'::jsonb;
  recipients     jsonb;
  n_recipients   integer := 0;
  ev_type        text;
  any_simulated  boolean := false;
  dispatched     jsonb;
  log_source     text;
  log_channel    text;
begin
  select not exists (select 1 from watcher_state_shadow) into is_baseline;
  log_source := case when p_mode = 'live' then 'pg_live' else 'pg_shadow' end;

  for station in select * from jsonb_array_elements(p_payload)
  loop
    n_stations := n_stations + 1;
    new_status := bwtf_classify(station->>'cso', station->>'s_color',
                                station->>'posted', station->>'p_color');
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
    elsif not is_baseline and prev_status is not null
       and (sev->>new_status)::int < (sev->>prev_status)::int then
      downgrades := downgrades || jsonb_build_object(
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
    select coalesce(jsonb_agg(jsonb_build_object(
             'email',        s.email,
             'phone_number', s.phone_number,
             'carrier',      s.carrier,
             'station_ids',  to_jsonb(s.station_ids),
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

    if p_mode = 'live' then
      dispatched := bwtf_dispatch_live(transitions, recipients, coalesce(any_simulated, false));
      if dispatched is null then
        log_channel := 'config_missing';
        dispatched := recipients;  -- keep the would-send list so nothing is lost
      else
        log_channel := 'mixed';
      end if;
    else
      log_channel := 'shadow';
      dispatched := recipients;
    end if;

    insert into alert_log (source, event_type, station_ids, station_names,
                           recipient_count, channel, simulated, results)
    values (log_source, ev_type,
            (select array_agg(x->>'station_id')   from jsonb_array_elements(transitions) x),
            (select array_agg(x->>'station_name') from jsonb_array_elements(transitions) x),
            coalesce(n_recipients, 0), log_channel, coalesce(any_simulated, false),
            jsonb_build_object('transitions', transitions, 'recipients', dispatched));
  end if;

  -- Downgrades: log-only, so event windows get their end minute. No recipient
  -- lookup, no dispatch, recipient_count=0, channel='log'.
  if jsonb_array_length(downgrades) > 0 then
    insert into alert_log (source, event_type, station_ids, station_names,
                           recipient_count, channel, simulated, results)
    values (log_source, 'cleared',
            (select array_agg(x->>'station_id')   from jsonb_array_elements(downgrades) x),
            (select array_agg(x->>'station_name') from jsonb_array_elements(downgrades) x),
            0, 'log',
            (select coalesce(bool_or((x->>'simulated')::boolean), false)
             from jsonb_array_elements(downgrades) x),
            jsonb_build_object('transitions', downgrades));
  end if;

  return jsonb_build_object(
    'stations', n_stations, 'baselined', is_baseline, 'mode', p_mode,
    'transitions', coalesce(jsonb_array_length(transitions), 0),
    'downgrades', coalesce(jsonb_array_length(downgrades), 0),
    'recipients', coalesce(n_recipients, 0));
end $$;

revoke all on function public.bwtf_process_payload(jsonb, text) from public, anon, authenticated;
grant execute on function public.bwtf_process_payload(jsonb, text) to service_role;
