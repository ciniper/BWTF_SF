-- 010: simulate a bacteria POSTING as well as a CSO.
--
-- The simulator could only force a station to 'cso'. The more common real
-- alert is a bacteria posting, so simulations now carry a kind:
--   simulated_cso.kind          'cso' (default, today's behaviour) | 'posted'
--   watcher_state_shadow.sim_kind  what the overlay forced last tick (null = none)
-- bwtf_process_payload (009's shape) treats the overlay as before — real
-- transitions diff real against real; simulated ones come from the overlay's
-- edges — with the forced status being the kind. A kind change while a
-- simulation is on logs one simulated transition in the direction of the
-- change. Edges are only logged where the overlay changes what the map
-- shows (the kind is above the real status).
--
-- Apply with NO simulation active (guarded), after 009.
-- Tests: db/scripts/test_010_simulation_kind.py (+ re-run test_009, test_006).

begin;

do $$
begin
  if exists (select 1 from public.simulated_cso) then
    raise exception using message =
      'A CSO simulation is active. Clear it (/alerts → Simulator → Clear) before applying 010.';
  end if;
end $$;

alter table public.simulated_cso
  add column if not exists kind text not null default 'cso'
  check (kind in ('posted', 'cso'));

alter table public.watcher_state_shadow
  add column if not exists sim_kind text check (sim_kind in ('posted', 'cso'));

update public.watcher_state_shadow set sim_kind = 'cso' where sim_active and sim_kind is null;

create or replace function public.bwtf_process_payload(p_payload jsonb, p_mode text default 'shadow')
returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  sev          constant jsonb := '{"ok":0, "posted":1, "cso":2}'::jsonb;
  is_baseline  boolean;
  station      jsonb;
  sid          text;
  sname        text;
  real_status  text;
  prev_status  text;
  prev_kind    text;
  kind_now     text;
  n_stations   integer := 0;
  real_up      jsonb := '[]'::jsonb;
  real_down    jsonb := '[]'::jsonb;
  sim_up       jsonb := '[]'::jsonb;
  sim_down     jsonb := '[]'::jsonb;
  n_recipients integer := 0;
  log_source   text;
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
    select sc.kind into kind_now from simulated_cso sc where sc.station_id = sid;

    select ws.status, coalesce(ws.sim_kind, case when ws.sim_active then 'cso' end)
      into prev_status, prev_kind
      from watcher_state_shadow ws where ws.station_id = sid;

    if not is_baseline and prev_status is not null then
      -- the real world: real status against real status
      if (sev->>real_status)::int > (sev->>prev_status)::int then
        real_up := real_up || jsonb_build_object(
          'station_id', sid, 'station_name', sname,
          'from', prev_status, 'to', real_status, 'simulated', false);
      elsif (sev->>real_status)::int < (sev->>prev_status)::int then
        real_down := real_down || jsonb_build_object(
          'station_id', sid, 'station_name', sname,
          'from', prev_status, 'to', real_status, 'simulated', false);
      end if;

      -- the overlay: its own edges, only where it changes what the map shows
      if kind_now is not null and prev_kind is null then
        if (sev->>kind_now)::int > (sev->>real_status)::int then
          sim_up := sim_up || jsonb_build_object(
            'station_id', sid, 'station_name', sname,
            'from', real_status, 'to', kind_now, 'simulated', true);
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
    n_recipients := n_recipients + bwtf_log_escalations(real_up, false, p_mode, log_source);
  end if;
  if jsonb_array_length(sim_up) > 0 then
    n_recipients := n_recipients + bwtf_log_escalations(sim_up, true, p_mode, log_source);
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
