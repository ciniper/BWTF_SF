-- Independent getCSV collector. Does not call or modify alert dispatch.
-- Requires pg_net and pg_cron, already used by the beach watcher.
-- A one-minute tick harvests responses; requests are at least two minutes apart.
create table public.sfpuc_getcsv_runtime (
  id integer primary key default 1 check (id = 1),
  enabled boolean not null default false,
  request_id bigint,
  requested_at timestamptz,
  last_success_at timestamptz,
  last_processed_at timestamptz,
  last_error text,
  last_error_at timestamptz,
  consecutive_failures integer not null default 0,
  last_row_count integer,
  last_changes integer,
  previous_values jsonb
);
insert into public.sfpuc_getcsv_runtime (id) values (1);
alter table public.sfpuc_getcsv_runtime enable row level security;
revoke all on public.sfpuc_getcsv_runtime from public, anon, authenticated, service_role;
grant select on public.sfpuc_getcsv_runtime to service_role;

-- Invoker-only functions: the scheduled job runs as postgres. No public RPC
-- can queue requests or alter ingestion state, even with authenticated access.
create function public.bwtf_record_getcsv(p_payload jsonb, p_observed_at timestamptz)
returns integer language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  rt public.sfpuc_getcsv_runtime%rowtype;
  r jsonb; item record; k text; src text; an text; sd date; raw text;
  vals jsonb := '{}'::jsonb;
  sample_days jsonb := '{}'::jsonb;
  parts jsonb; prev text; kind text; n integer := 0;
begin
  select * into strict rt from public.sfpuc_getcsv_runtime where id = 1 for update;
  if p_observed_at is null then raise exception 'Missing observation timestamp'; end if;
  if rt.last_success_at is not null and p_observed_at <= rt.last_success_at then return 0; end if;
  if jsonb_typeof(p_payload) is distinct from 'array' then raise exception 'getCSV is not an array'; end if;
  if jsonb_array_length(p_payload) = 0 then raise exception 'Empty getCSV response'; end if;
  for r in select value from jsonb_array_elements(p_payload) loop
    -- SFPUC includes all-null placeholder rows between real result groups.
    if jsonb_typeof(r) = 'object' and r->>'source' is null and r->>'sample_date' is null
       and r->>'analyte' is null and r->>'data' is null then continue; end if;
    src := btrim(r->>'source'); an := btrim(r->>'analyte');
    if coalesce(src,'') = '' or coalesce(an,'') not in ('ENTERO','COLI_FECAL','COLI_TOTAL','COLI_E') then
      raise exception 'Invalid getCSV source or analyte';
    end if;
    if coalesce(r->>'sample_date','') !~ '^\d{2}/\d{2}/\d{2}$' then raise exception 'Invalid getCSV date'; end if;
    sd := to_date(r->>'sample_date', 'MM/DD/YY');
    if to_char(sd, 'MM/DD/YY') <> r->>'sample_date' then raise exception 'Invalid getCSV calendar date'; end if;
    raw := nullif(btrim(r->>'data'), '');
    if raw is not null and raw !~ '^[<>]?\s*[0-9]+(\.[0-9]+)?$' then raise exception 'Invalid getCSV value'; end if;
    k := jsonb_build_array(src, sd::text, an)::text;
    if vals ? k and vals->>k is distinct from raw then raise exception 'Conflicting duplicate getCSV result'; end if;
    vals := vals || jsonb_build_object(k, raw);
    sample_days := sample_days || jsonb_build_object(jsonb_build_array(src,sd::text)::text, true);
  end loop;
  if vals = '{}'::jsonb then raise exception 'No valid getCSV results'; end if;
  -- Treat a disappearing indicator on a still-present sample as unavailable.
  -- Whole sample dates aging out of the source window are not withdrawals.
  for item in select key,value from jsonb_each(coalesce(rt.previous_values,'{}'::jsonb)) loop
    parts := item.key::jsonb;
    if not vals ? item.key and sample_days ? jsonb_build_array(parts->>0,parts->>1)::text then
      vals := vals || jsonb_build_object(item.key, null);
    end if;
  end loop;
  for item in select key,value from jsonb_each(vals) loop
    parts := item.key::jsonb;
    raw := vals->>item.key;
    prev := rt.previous_values->>item.key;
    if raw is not distinct from prev then continue; end if;
    kind := case when raw is null then 'removed'
                 when rt.previous_values is null then 'baseline'
                 when prev is null then 'appeared' else 'changed' end;
    insert into public.sfpuc_getcsv_observations
      (source_code,sample_date,analyte,observed_at,value_raw,observation_kind,recording_source)
    values (parts->>0,(parts->>1)::date,parts->>2,p_observed_at,raw,kind,'collector')
    on conflict do nothing;
    if found then n := n + 1; end if;
  end loop;
  update public.sfpuc_getcsv_runtime set previous_values = vals,
    last_success_at = p_observed_at, last_processed_at = now(),
    last_row_count = jsonb_array_length(p_payload), last_changes = n,
    last_error = null, consecutive_failures = 0 where id = 1;
  return n;
end $$;
revoke all on function public.bwtf_record_getcsv(jsonb,timestamptz) from public,anon,authenticated,service_role;

create function public.bwtf_getcsv_tick()
returns void language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  rt public.sfpuc_getcsv_runtime%rowtype;
  resp record;
  req bigint;
begin
  -- Protect manual ticks from racing the scheduled job.
  if not pg_try_advisory_xact_lock(hashtextextended('bwtf_getcsv_tick',0)) then return; end if;
  select * into strict rt from public.sfpuc_getcsv_runtime where id = 1 for update;
  if not rt.enabled then return; end if;
  if rt.request_id is not null then
    select * into resp from net._http_response where id = rt.request_id;
    if found then
      begin
        if resp.status_code is distinct from 200 or coalesce(resp.timed_out,false) or resp.error_msg is not null then
          raise exception 'getCSV HTTP %, %', resp.status_code, coalesce(resp.error_msg,'request failed');
        end if;
        perform public.bwtf_record_getcsv(resp.content::jsonb,resp.created);
      exception when others then
        update public.sfpuc_getcsv_runtime set last_error = left(sqlerrm,500), last_error_at = now(),
          consecutive_failures = consecutive_failures + 1 where id = 1;
      end;
      update public.sfpuc_getcsv_runtime set request_id = null where id = 1;
    elsif rt.requested_at > now() - interval '5 minutes' then
      return;
    else
      update public.sfpuc_getcsv_runtime set request_id = null,
        last_error = 'getCSV response missing after five minutes', last_error_at = now(),
        consecutive_failures = consecutive_failures + 1 where id = 1;
    end if;
  end if;
  if rt.requested_at is null or rt.requested_at <= now() - interval '2 minutes' then
    begin
      select net.http_get(
        url := 'https://infrastructure.sfwater.org/lims.asmx/getCSV',
        timeout_milliseconds := 15000) into req;
      update public.sfpuc_getcsv_runtime set request_id = req, requested_at = now() where id = 1;
    exception when others then
      update public.sfpuc_getcsv_runtime set last_error = left(sqlerrm,500), last_error_at = now(),
        consecutive_failures = consecutive_failures + 1 where id = 1;
    end;
  end if;
end $$;
revoke all on function public.bwtf_getcsv_tick() from public,anon,authenticated,service_role;
select cron.schedule('bwtf-getcsv-tick', '* * * * *', 'select public.bwtf_getcsv_tick()');
