-- 009: keep simulations out of the real watcher state.
--
-- Problem (hit 2026-09-05; surfaced in the forecast's "What happened" view):
-- bwtf_process_payload wrote the simulation overlay INTO
-- watcher_state_shadow.status and flagged a transition "simulated" only if a
-- simulated_cso row existed at that tick. So:
--   * ending a simulation logged its cso→ok clear-down as simulated=false
--     (006 called this a "known quirk"), and every alert_log reader had to
--     know to filter it;
--   * a REAL CSO starting at a station under simulation produced no
--     transition at all — the overlay already had the station at cso — so no
--     alert went out.
--
-- Fix: provenance becomes part of the state.
--   watcher_state_shadow.status     = the REAL feed status, always
--   watcher_state_shadow.sim_active = a simulated_cso row was present for the
--                                     station last tick
-- Real transitions diff real against real → simulated=false, dispatched and
-- logged exactly as 006. Simulated transitions come from the overlay's own
-- edges, only where the overlay changes what the map shows (real status not
-- already cso): row appears → {from: real, to: cso, simulated: true} with the
-- TEST dispatch as before; row disappears → 'cleared' {from: cso, to: real,
-- simulated: true}, log only. A tick carrying both real and simulated
-- escalations now logs TWO rows (006 marked the whole dispatch TEST if any
-- transition was simulated).
--
-- Apply with NO simulation active (guarded below): existing state rows then
-- already hold real statuses and sim_active defaults to false.
--
-- Also backfills alert_log: a 'cleared' transition whose station's most
-- recent prior escalation was simulated is a simulation teardown → simulated.
--
-- Paste order: after 001–008.  Tests: db/scripts/test_009_simulation_provenance.py
-- (shadow mode, synthetic TEST-009 stations; start / end / real-during-
-- simulation / both-in-one-tick / plain-real regression).

begin;

do $$
begin
  if exists (select 1 from public.simulated_cso) then
    raise exception using message =
      'A CSO simulation is active. Clear it (/alerts → Simulations → Clear) before applying 009, '
      'so watcher_state_shadow holds only real statuses.';
  end if;
end $$;

alter table public.watcher_state_shadow
  add column if not exists sim_active boolean not null default false;

-- ── escalations: recipients → (live) dispatch → alert_log row ──────────────
-- 006's inline block, lifted out so real and simulated escalations in the
-- same tick are handled as two separate dispatches / rows.
create or replace function public.bwtf_log_escalations(
  p_transitions jsonb, p_simulated boolean, p_mode text, p_log_source text
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
                           from jsonb_array_elements(p_transitions) x);

  select case when bool_or(x->>'to' = 'cso') then 'cso' else 'posted' end
    into ev_type
    from jsonb_array_elements(p_transitions) x;

  if p_mode = 'live' then
    dispatched := bwtf_dispatch_live(p_transitions, recipients, p_simulated);
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
  values (p_log_source, ev_type,
          (select array_agg(x->>'station_id')   from jsonb_array_elements(p_transitions) x),
          (select array_agg(x->>'station_name') from jsonb_array_elements(p_transitions) x),
          coalesce(n_recipients, 0), log_channel, p_simulated,
          jsonb_build_object('transitions', p_transitions, 'recipients', dispatched));
  return coalesce(n_recipients, 0);
end $$;

revoke all on function public.bwtf_log_escalations(jsonb, boolean, text, text) from public, anon, authenticated;
grant execute on function public.bwtf_log_escalations(jsonb, boolean, text, text) to service_role;

-- ── downgrades: log-only 'cleared' row (recipient_count=0, channel='log') ──
create or replace function public.bwtf_log_downgrades(
  p_downgrades jsonb, p_simulated boolean, p_log_source text
) returns void
language plpgsql security definer set search_path = public as $$
begin
  insert into alert_log (source, event_type, station_ids, station_names,
                         recipient_count, channel, simulated, results)
  values (p_log_source, 'cleared',
          (select array_agg(x->>'station_id')   from jsonb_array_elements(p_downgrades) x),
          (select array_agg(x->>'station_name') from jsonb_array_elements(p_downgrades) x),
          0, 'log', p_simulated,
          jsonb_build_object('transitions', p_downgrades));
end $$;

revoke all on function public.bwtf_log_downgrades(jsonb, boolean, text) from public, anon, authenticated;
grant execute on function public.bwtf_log_downgrades(jsonb, boolean, text) to service_role;

-- ── the watcher ────────────────────────────────────────────────────────────
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
  prev_sim     boolean;
  sim_now      boolean;
  n_stations   integer := 0;
  real_up      jsonb := '[]'::jsonb;   -- real escalations   (simulated=false)
  real_down    jsonb := '[]'::jsonb;   -- real clear-downs   (simulated=false)
  sim_up       jsonb := '[]'::jsonb;   -- overlay switched on  (simulated=true)
  sim_down     jsonb := '[]'::jsonb;   -- overlay switched off (simulated=true)
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
    sim_now     := exists (select 1 from simulated_cso sc where sc.station_id = sid);

    select ws.status, ws.sim_active into prev_status, prev_sim
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
      if sim_now and not coalesce(prev_sim, false) and real_status <> 'cso' then
        sim_up := sim_up || jsonb_build_object(
          'station_id', sid, 'station_name', sname,
          'from', real_status, 'to', 'cso', 'simulated', true);
      elsif coalesce(prev_sim, false) and not sim_now and real_status <> 'cso' then
        sim_down := sim_down || jsonb_build_object(
          'station_id', sid, 'station_name', sname,
          'from', 'cso', 'to', real_status, 'simulated', true);
      end if;
    end if;

    insert into watcher_state_shadow as ws (station_id, station_name, status, sim_active)
    values (sid, sname, real_status, sim_now)
    on conflict (station_id) do update
      set status = excluded.status, station_name = excluded.station_name,
          sim_active = excluded.sim_active;
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

-- ── backfill: relabel simulation teardowns already in alert_log ────────────
-- A 'cleared' transition is a teardown when the station's most recent prior
-- escalation (any source) was simulated. Per transition; the row flag is the
-- writer's convention, bool_or over its transitions.
create temporary table _relabel on commit drop as
with cleared as (
  select l.id, l.created_at, x.ord, x.t
    from alert_log l
    cross join lateral jsonb_array_elements(coalesce(l.results->'transitions', '[]'::jsonb))
         with ordinality as x(t, ord)
   where l.event_type = 'cleared' and not l.simulated
),
judged as (
  select c.id, c.ord, c.t,
         coalesce((
           select coalesce(
                    (select (pt->>'simulated')::boolean
                       from jsonb_array_elements(coalesce(p.results->'transitions', '[]'::jsonb)) pt
                      where pt->>'station_id' = c.t->>'station_id' limit 1),
                    p.simulated)
             from alert_log p
            where p.event_type in ('posted', 'cso')
              and p.created_at < c.created_at
              and p.station_ids @> array[c.t->>'station_id']
            order by p.created_at desc
            limit 1), false) as prior_was_simulated
    from cleared c
)
select id,
       jsonb_agg(case when prior_was_simulated then t || '{"simulated": true}'::jsonb else t end
                 order by ord) as transitions,
       bool_or(prior_was_simulated) as any_simulated
  from judged
 group by id
having bool_or(prior_was_simulated);

update alert_log l
   set results   = jsonb_set(coalesce(l.results, '{}'::jsonb), '{transitions}', r.transitions),
       simulated = true
  from _relabel r
 where l.id = r.id;

select count(*) as cleared_rows_relabelled_as_simulation_teardowns from _relabel;

commit;
