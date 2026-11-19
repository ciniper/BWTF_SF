begin;

-- Prepared only; applying requires a separate production rollout decision.
-- Independent getBeaches collector: at most one upstream request per 30 minutes.
-- Does not read or modify the alert watcher, its runtime, or its schedules.
-- Recorder is disabled by default. Its first accepted read is a BASELINE.

create table public.feed_color_runtime (
  id integer primary key default 1 check (id = 1),
  enabled boolean not null default false,
  request_id bigint,
  requested_at timestamptz,
  last_attempt_at timestamptz,
  last_http_status integer,
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
-- Poll gaps >45 minutes start a new run even when the state did not change.
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
-- Keep each successful read as well as compressed runs: the future timeline
-- can show actual observation checkpoints and uncertainty between them.
create table public.feed_color_reads (
  response_id bigint primary key,
  observed_at timestamptz not null,
  states jsonb not null check (jsonb_typeof(states)='object')
);
create index feed_color_reads_time_idx on public.feed_color_reads(observed_at);
create table public.feed_color_state (
  station_id text primary key,
  run_id bigint not null references public.feed_color_runs(id)
);

alter table public.feed_color_runtime enable row level security;
alter table public.feed_color_runs enable row level security;
alter table public.feed_color_state enable row level security;
alter table public.feed_color_reads enable row level security;
revoke all on public.feed_color_runtime,public.feed_color_runs,public.feed_color_state,public.feed_color_reads from public,anon,authenticated,service_role;
grant select on public.feed_color_runtime,public.feed_color_runs,public.feed_color_reads to service_role;
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
  -- Do not wait behind a concurrent collector invocation.
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
  insert into public.feed_color_reads(response_id,observed_at,states) values(p_response_id,p_observed_at,states);
  for item in select key,value from jsonb_each(states) order by key loop
    sid := item.key; new_state := item.value->'state'; name := item.value->>'name';
    sample_date := item.value->>'sample_date';
    select r.* into prior from public.feed_color_state s join public.feed_color_runs r on r.id=s.run_id
     where s.station_id=sid;
    if prior.id is not null and prior.state=new_state
       and p_observed_at-prior.last_confirmed_at <= interval '45 minutes' then
      update public.feed_color_runs set last_confirmed_at=p_observed_at,last_response_id=p_response_id,
          confirmations=confirmations+1,last_sample_date=sample_date,station_name=name where id=prior.id;
    else
      run_kind := case when prior.id is null then 'baseline'
        when new_state->>'present'='false' and prior.state->>'present'='true' then 'missing'
        when prior.state->>'present'='false' or p_observed_at-prior.last_confirmed_at>interval '45 minutes' then 'resume'
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

-- Local-only harvest tick. pg_net completes requests after commit, so a light
-- minute tick harvests its own pending response. The HTTP cadence is 30 minutes,
-- including failed attempts; there is no retry burst and no watcher coupling.
create function public.bwtf_feed_color_tick()
returns void language plpgsql security invoker
set search_path = public, pg_temp set lock_timeout = '100ms' as $$
declare
  rt public.feed_color_runtime%rowtype;
  resp record; req bigint; raw text; payload jsonb;
begin
  if not pg_try_advisory_xact_lock(61720417) then return; end if;
  select * into strict rt from public.feed_color_runtime where id=1 for update nowait;
  if not rt.enabled then return; end if;
  if rt.request_id is not null then
    select * into resp from net._http_response where id=rt.request_id;
    if found then
      begin
        if resp.status_code is distinct from 200 or coalesce(resp.timed_out,false) or resp.error_msg is not null then
          raise exception 'Color feed HTTP %, %',resp.status_code,coalesce(resp.error_msg,'request failed');
        end if;
        if resp.content is null or octet_length(resp.content)>262144 then raise exception 'Empty or oversized response'; end if;
        -- getBeaches is an ASMX XML wrapper around a JSON array.
        raw:=substring(resp.content from position('[' in resp.content));
        raw:=left(raw,length(raw)-position(']' in reverse(raw))+1);
        payload:=raw::jsonb;
        perform public.bwtf_record_feed_colors(payload,rt.request_id,resp.created);
      exception when others then
        update public.feed_color_runtime set last_error=left(sqlerrm,1000),last_error_at=clock_timestamp(),
          consecutive_failures=consecutive_failures+1 where id=1;
      end;
      update public.feed_color_runtime set request_id=null,last_http_status=resp.status_code where id=1;
    elsif rt.requested_at > clock_timestamp()-interval '5 minutes' then
      return;
    else
      update public.feed_color_runtime set request_id=null,last_error='Color response missing after five minutes',
        last_error_at=clock_timestamp(),consecutive_failures=consecutive_failures+1 where id=1;
    end if;
  end if;
  if rt.last_attempt_at is null or rt.last_attempt_at<=clock_timestamp()-interval '30 minutes' then
    -- Set outside the inner exception block so even queueing failures are throttled.
    update public.feed_color_runtime set last_attempt_at=clock_timestamp() where id=1;
    begin
      select net.http_get(url:='https://infrastructure.sfwater.org/lims.asmx/getBeaches',timeout_milliseconds:=15000) into req;
      update public.feed_color_runtime set request_id=req,requested_at=clock_timestamp() where id=1;
    exception when others then
      update public.feed_color_runtime set last_error=left(sqlerrm,1000),last_error_at=clock_timestamp(),
        consecutive_failures=consecutive_failures+1 where id=1;
    end;
  end if;
end $$;
revoke all on function public.bwtf_feed_color_tick() from public,anon,authenticated,service_role;

-- Registered inactive as well as disabled in runtime. No request is made by
-- installation; a separately approved activation must enable BOTH switches.
do $install$
declare job bigint;
begin
  if exists(select 1 from cron.job where jobname='bwtf-feed-color-collector') then
    raise exception 'Color collector job already exists; inspect instead of replacing it';
  end if;
  job:=cron.schedule('bwtf-feed-color-collector','* * * * *','select public.bwtf_feed_color_tick()');
  perform cron.alter_job(job,active:=false);
end $install$;
commit;
