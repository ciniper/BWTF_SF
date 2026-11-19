begin;

-- Prepared only; applying requires a separate production rollout decision.
-- Reuse the existing real getBeaches response. No HTTP request, new cron job,
-- alert classification change, recipient change, or retroactive backfill.
-- Recorder is disabled by default. Its first accepted read is a BASELINE.

create table public.feed_color_runtime (
  id integer primary key default 1 check (id = 1),
  enabled boolean not null default false,
  last_response_id bigint,
  last_observed_at timestamptz,
  last_processed_at timestamptz,
  last_new_runs integer,
  last_error text,
  last_error_at timestamptz,
  consecutive_failures integer not null default 0
);
insert into public.feed_color_runtime(id) values (1);

-- Each run is an unchanged raw state, confirmed by successful distinct reads.
-- first/last observations bound changes; they are NOT upstream publication times.
-- Poll gaps >90s start a new run even when the state did not change.
create table public.feed_color_runs (
  id bigint generated always as identity primary key,
  station_id text not null,
  station_name text not null,
  state jsonb not null check (jsonb_typeof(state) = 'object'),
  kind text not null check (kind in ('baseline','change','missing','resume')),
  first_observed_at timestamptz not null,
  last_confirmed_at timestamptz not null,
  first_response_id bigint not null,
  last_response_id bigint not null,
  confirmations integer not null default 1 check (confirmations > 0),
  previous_state jsonb,
  previous_last_confirmed_at timestamptz,
  first_sample_date text,
  last_sample_date text,
  check (last_confirmed_at >= first_observed_at),
  check (previous_last_confirmed_at is null or previous_last_confirmed_at < first_observed_at),
  unique (station_id,first_response_id)
);
create index feed_color_runs_station_time_idx on public.feed_color_runs(station_id,first_observed_at desc);
create index feed_color_runs_time_idx on public.feed_color_runs(first_observed_at);
create table public.feed_color_state (
  station_id text primary key,
  run_id bigint not null references public.feed_color_runs(id)
);

alter table public.feed_color_runtime enable row level security;
alter table public.feed_color_runs enable row level security;
alter table public.feed_color_state enable row level security;
revoke all on public.feed_color_runtime,public.feed_color_runs,public.feed_color_state from public,anon,authenticated,service_role;
grant select on public.feed_color_runtime,public.feed_color_runs to service_role;
revoke all on sequence public.feed_color_runs_id_seq from public,anon,authenticated,service_role;

create function public.bwtf_record_feed_colors(p_payload jsonb,p_response_id bigint,p_observed_at timestamptz)
returns integer language plpgsql security invoker
set search_path = public, pg_temp set lock_timeout = '100ms' as $$
declare
  rt public.feed_color_runtime%rowtype;
  prior public.feed_color_runs%rowtype;
  station jsonb; item record; field text; sid text; name text; sample_date text;
  states jsonb := '{}'::jsonb;
  new_state jsonb; run_kind text; run_id bigint; changes integer := 0;
begin
  -- No waiting behind another recorder. The alert watcher must keep moving.
  if not pg_try_advisory_xact_lock(61720416) then return 0; end if;
  select * into strict rt from public.feed_color_runtime where id=1 for update nowait;
  if not rt.enabled then return 0; end if;
  if p_response_id is null or p_observed_at is null then raise exception 'Missing feed response identity/time'; end if;
  if p_response_id <= coalesce(rt.last_response_id,-1)
     or p_observed_at <= coalesce(rt.last_observed_at,'-infinity'::timestamptz) then return 0; end if;
  if p_observed_at > clock_timestamp()+interval '5 seconds'
     or p_observed_at < clock_timestamp()-interval '10 minutes' then
    raise exception 'Feed response is stale or future-dated';
  end if;
  if jsonb_typeof(p_payload) is distinct from 'array' then raise exception 'Feed must be an array'; end if;
  if jsonb_array_length(p_payload) not between 1 and 100 or octet_length(p_payload::text)>262144 then
    raise exception 'Empty or oversized feed';
  end if;
  -- Validate the ENTIRE response before writing any station state. Missing
  -- required color fields are malformed; explicit JSON null is a valid value.
  for station in select value from jsonb_array_elements(p_payload) loop
    if jsonb_typeof(station) is distinct from 'object'
       or not station ?& array['stationid','stationname','s_color','p_color','posted','cso'] then
      raise exception 'Malformed feed station';
    end if;
    sid := station->>'stationid';
    if jsonb_typeof(station->'stationid') is distinct from 'string'
       or coalesce(length(btrim(sid)),0) not between 1 and 64 then raise exception 'Invalid station id'; end if;
    if states ? sid then raise exception 'Duplicate feed station'; end if;
    foreach field in array array['stationname','s_color','p_color','posted','cso','sample_date'] loop
      if station ? field and (jsonb_typeof(station->field) not in ('string','null')
           or length(station->>field)>256) then raise exception 'Invalid station field type/size'; end if;
    end loop;
    -- Raw values retained exactly, including W/Y, case, empty strings, nulls,
    -- and any new future color code. Do not guess unknown values to be green.
    new_state := jsonb_build_object('present',true,'s_color',station->'s_color',
                   'p_color',station->'p_color','posted',station->'posted','cso',station->'cso');
    states := states || jsonb_build_object(sid,jsonb_build_object('state',new_state,
                 'name',coalesce(station->>'stationname',sid),'sample_date',station->'sample_date'));
  end loop;
  -- A station absent from an otherwise valid response is explicitly missing,
  -- never cleared/green. Empty responses above do not clear the entire roster.
  for item in select s.station_id,r.station_name from public.feed_color_state s
              join public.feed_color_runs r on r.id=s.run_id limit 101 loop
    if not states ? item.station_id then
      states := states || jsonb_build_object(item.station_id,jsonb_build_object(
          'name',item.station_name,'state',jsonb_build_object('present',false)));
    end if;
  end loop;
  if (select count(*) from jsonb_object_keys(states))>100 then raise exception 'Tracked station roster exceeds limit'; end if;
  for item in select key,value from jsonb_each(states) order by key loop
    sid := item.key; new_state := item.value->'state'; name := item.value->>'name';
    sample_date := item.value->>'sample_date';
    select r.* into prior from public.feed_color_state s join public.feed_color_runs r on r.id=s.run_id
     where s.station_id=sid;
    if prior.id is not null and prior.state=new_state
       and p_observed_at-prior.last_confirmed_at <= interval '90 seconds' then
      update public.feed_color_runs set last_confirmed_at=p_observed_at,last_response_id=p_response_id,
          confirmations=confirmations+1,last_sample_date=sample_date,station_name=name where id=prior.id;
    else
      run_kind := case when prior.id is null then 'baseline'
        when new_state->>'present'='false' and prior.state->>'present'='true' then 'missing'
        when prior.state->>'present'='false' or p_observed_at-prior.last_confirmed_at>interval '90 seconds' then 'resume'
        else 'change' end;
      insert into public.feed_color_runs(station_id,station_name,state,kind,first_observed_at,last_confirmed_at,
          first_response_id,last_response_id,previous_state,previous_last_confirmed_at,first_sample_date,last_sample_date)
      values(sid,name,new_state,run_kind,p_observed_at,p_observed_at,p_response_id,p_response_id,
          prior.state,prior.last_confirmed_at,sample_date,sample_date) returning id into run_id;
      insert into public.feed_color_state(station_id,run_id) values(sid,run_id)
        on conflict(station_id) do update set run_id=excluded.run_id;
      changes := changes+1;
    end if;
  end loop;
  update public.feed_color_runtime set last_response_id=p_response_id,last_observed_at=p_observed_at,
    last_processed_at=clock_timestamp(),last_new_runs=changes,last_error=null,consecutive_failures=0 where id=1;
  return changes;
end $$;
revoke all on function public.bwtf_record_feed_colors(jsonb,bigint,timestamptz) from public,anon,authenticated,service_role;

-- Only this fail-open wrapper is called by the watcher. A recorder error rolls
-- back this subtransaction's history writes, not the already-processed alerts.
-- Error reporting is itself guarded; it must not turn a recording error fatal.
create function public.bwtf_capture_feed_colors(p_payload jsonb,p_response_id bigint,p_observed_at timestamptz)
returns jsonb language plpgsql security invoker
set search_path = public, pg_temp set lock_timeout = '100ms' as $$
declare n integer; message text;
begin
  n := public.bwtf_record_feed_colors(p_payload,p_response_id,p_observed_at);
  return jsonb_build_object('new_runs',n);
exception when others then
  message := left(sqlerrm,1000);
  begin
    update public.feed_color_runtime set last_error=message,last_error_at=clock_timestamp(),
      consecutive_failures=consecutive_failures+1 where id=1;
  exception when others then null;
  end;
  begin
    perform public.bwtf_log_error('feed_color',message,null);
  exception when others then null;
  end;
  return jsonb_build_object('error',message);
end $$;
revoke all on function public.bwtf_capture_feed_colors(jsonb,bigint,timestamptz) from public,anon,authenticated,service_role;

-- Narrow, guarded hook into the inspected live tick. Keep its owner, grants,
-- security attributes and EVERY existing alert/forecast/network action intact.
-- Abort if another migration changed the tick since review (do not overwrite it).
do $install$
declare definition text; body_hash text;
begin
  select pg_get_functiondef(p.oid),md5(p.prosrc) into definition,body_hash
    from pg_proc p where p.oid='public.bwtf_shadow_tick()'::regprocedure;
  if body_hash <> '96497e15a283c0a6ec3c9686cf0feb5e' then
    raise exception 'Watcher tick changed since color-recorder review; rebase this migration';
  end if;
  definition := replace(definition,$old$select r.status_code, r.content, r.timed_out, r.error_msg into resp$old$,$new$select r.status_code, r.content, r.timed_out, r.error_msg, r.id as response_id, r.created as observed_at into resp$new$);
  definition := replace(definition,$old$        processed := true;$old$,$new$        processed := true;
        -- feed-color recorder: real payload only; never affects alert decisions.
        begin
          summary := coalesce(summary,'{}'::jsonb) || jsonb_build_object('feed_colors',
            public.bwtf_capture_feed_colors(payload,resp.response_id,resp.observed_at));
        exception when others then
          summary := coalesce(summary,'{}'::jsonb) || jsonb_build_object('feed_colors',jsonb_build_object('error',left(sqlerrm,1000)));
        end;
        -- /feed-color recorder$new$);
  execute definition;
end $install$;

-- Intentionally NO enable, HTTP call, cron scheduling, or historical backfill.

commit;
