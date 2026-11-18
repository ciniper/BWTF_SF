-- Alert frequency: durable per-subscriber windows, Pacific-day status memory,
-- and running-list email context. Apply after 024, with no simulation active.
-- Real delivery is held; targeted simulations retain immediate dispatch.
begin;
select pg_advisory_xact_lock(61720415);
do $$ begin
  if exists(select 1 from public.simulated_cso) then
    raise exception 'Clear active simulations before applying alert frequency';
  end if;
end $$;
create table public.alert_pending (
  subscriber_id uuid primary key references public.subscribers(id) on delete cascade,
  due_at timestamptz not null,
  transitions jsonb not null
);
create table public.alert_status_day (
  station_id text not null,
  status text not null check (status in ('posted','cso')),
  day date not null,
  primary key (station_id, status)
);
alter table public.alert_pending enable row level security;
alter table public.alert_status_day enable row level security;
revoke all on public.alert_pending, public.alert_status_day from public, anon, authenticated;
grant all on public.alert_pending, public.alert_status_day to service_role;
-- Seed today's memory from real PG history, including clears from an old posting or discharge.
insert into public.alert_status_day
select t->>'station_id', st.status, max((l.created_at at time zone 'America/Los_Angeles')::date)
from public.alert_log l cross join lateral jsonb_array_elements(l.results->'transitions') t
cross join lateral (select distinct unnest(array[t->>'from',t->>'to']) as status) st
where l.source in ('pg_live','pg_shadow') and not l.simulated
  and st.status in ('posted','cso')
  and l.created_at >= ((now() at time zone 'America/Los_Angeles')::date::timestamp at time zone 'America/Los_Angeles')
group by t->>'station_id', st.status;

create or replace function public.bwtf_queue_alerts(p_transitions jsonb, p_recipients jsonb)
returns jsonb language plpgsql security definer set search_path = public as $$
declare s record; eligible jsonb; merged jsonb; result jsonb := '[]';
begin
  for s in select * from subscribers where active loop
    -- Match exactly the recipient snapshot selected by the detector.
    if not exists(select 1 from jsonb_array_elements(p_recipients) r
      where r->>'email'=s.email and r->>'phone_number'=s.phone_number) then continue; end if;
    select coalesce(jsonb_agg(t), '[]') into eligible
    from jsonb_array_elements(p_transitions) t
    where t->>'station_id'=any(s.station_ids) and not (
      exists(select 1 from alert_status_day d
        where d.station_id=t->>'station_id' and d.status=t->>'to' and d.day=(now() at time zone 'America/Los_Angeles')::date));
    if jsonb_array_length(eligible)=0 then continue; end if;
    select coalesce(transitions,'[]') into merged from alert_pending where subscriber_id=s.id;
    select jsonb_agg(t order by t->>'station_id') into merged from (
      select distinct on (t->>'station_id') t
      from jsonb_array_elements(coalesce(merged,'[]') || eligible) t
      order by t->>'station_id', (t->>'to'='cso') desc
    ) worst;
    insert into alert_pending values(s.id, now()+interval '15 minutes', merged)
    on conflict(subscriber_id) do update set transitions=excluded.transitions;
    result := result || jsonb_build_object('email',s.email,'phone_number',s.phone_number,'queued',true);
  end loop;
  return result;
end $$;

create or replace function public.bwtf_flush_alerts(p_before timestamptz)
returns integer language plpgsql security definer set search_path = public as $$
declare q record; s subscribers%rowtype; matched jsonb; recipients jsonb; sent jsonb; n int := 0;
begin
  -- Same lock as the detector: serializes overlapping ticks and manual retries.
  perform pg_advisory_xact_lock(61720415);
  if (select value from watcher_config where key='mode') is distinct from 'live' then return 0; end if;
  for q in select * from alert_pending where due_at <= p_before for update loop
    select * into s from subscribers where id=q.subscriber_id and active;
    if not found then delete from alert_pending where subscriber_id=q.subscriber_id; continue; end if;
    select coalesce(jsonb_agg(t),'[]') into matched from jsonb_array_elements(q.transitions) t
      where t->>'station_id'=any(s.station_ids);
    if jsonb_array_length(matched)=0 then delete from alert_pending where subscriber_id=q.subscriber_id; continue; end if;
    recipients := jsonb_build_array(jsonb_build_object('email',s.email,'phone_number',s.phone_number,
      'carrier',s.carrier,'station_ids',to_jsonb(s.station_ids),
      'station_names',(select jsonb_agg(t->>'station_name') from jsonb_array_elements(matched) t)));
    sent := bwtf_dispatch_live(matched, recipients, false);
    if sent is null then continue; end if; -- missing credentials: keep the batch for retry
    insert into alert_log(source,event_type,channel,recipient_count,results)
      values('pg_live_batch','batch_delivery','mixed',1,jsonb_build_object('recipients',sent,'due_at',q.due_at));
    delete from alert_pending where subscriber_id=q.subscriber_id;
    n := n+1;
  end loop;
  return n;
end $$;
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
    if p_simulated then
      dispatched := bwtf_dispatch_live(p_transitions, recipients, true);
    else
      dispatched := bwtf_queue_alerts(p_transitions, recipients);
    end if;
    if dispatched is null then
      log_channel := 'config_missing';
      dispatched := recipients;
    else
      log_channel := case when p_simulated then 'mixed' else 'queued' end;
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

create or replace function public.bwtf_still_in_effect(msg jsonb, p_still jsonb, p_zone text)
returns jsonb language plpgsql immutable set search_path = public as $$
declare t jsonb; heading text := 'Still in effect'; rows text := ''; lines text := ''; brief text := '';
  block text; label text; color text; nm text; escaped text;
  anchor text := '<p style="margin:14px 0 0;font-size:13.5px;';
begin
  if jsonb_array_length(p_still)=0 then return msg; end if;
  if nullif(p_zone,'') is not null then heading := heading || ' — ' || p_zone; end if;
  for t in select * from jsonb_array_elements(p_still) loop
    nm := t->>'station_name';
    escaped := replace(replace(replace(replace(replace(nm,'&','&amp;'),'<','&lt;'),'>','&gt;'),'"','&quot;'),chr(39),'&#x27;');
    label := case when t->>'to'='cso' then 'Sewage discharge' else 'Posted' end;
    color := case when t->>'to'='cso' then '#b5310a' else '#d4763a' end;
    rows := rows || '<tr><td style="padding:6px 0;border-top:1px solid #E3EBF2;font-size:14px;color:#26272a;">' || escaped || '</td><td align="right" style="padding:6px 0;border-top:1px solid #E3EBF2;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:' || color || ';">' || label || '</td></tr>';
    lines := lines || case when lines='' then '' else E'\n' end || '- ' || nm || ': ' || lower(label) || '.';
    brief := brief || case when brief='' then '' else '; ' end || nm || ' (' || case when t->>'to'='cso' then 'discharge' else 'posted' end || ')';
  end loop;
  escaped := replace(replace(replace(replace(replace(heading,'&','&amp;'),'<','&lt;'),'>','&gt;'),'"','&quot;'),chr(39),'&#x27;');
  block := '<div style="margin:16px 0 0;font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#54576F;">' || escaped || ' &middot; ' || jsonb_array_length(p_still) || '</div><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:4px 0 0;">' || rows || '</table>';
  return msg || jsonb_build_object(
    'html_body',replace(msg->>'html_body',anchor,block||anchor),
    'text_body',replace(msg->>'text_body',E'\n\nFrom SFPUC:',E'\n\n'||heading||E':\n'||lines||E'\n\nFrom SFPUC:'),
    'sms_text',left(replace(msg->>'sms_text','. Live status:','. '||heading||': '||brief||'. Live status:'),320));
end $$;
create or replace function public.bwtf_dispatch_live(
  p_transitions jsonb, p_recipients jsonb, p_simulated boolean
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  api_key    text;
  from_email text;
  from_name  text;
  r          jsonb;
  matched    jsonb;
  zone       text;
  rendered   jsonb;
  still      jsonb;
  gateway    text;
  req_id     bigint;
  sends      jsonb;
  out_all    jsonb := '[]'::jsonb;
  -- 015: the delivery row for the message about to be sent
  del_id       bigint;
  renderer_ver text := 'alerts-frequency';   -- bwtf_render_alert's migration; bump when the renderer changes
  -- /015
  -- 017: this recipient's unsubscribe link
  site_url   text;
  tok        uuid;
  unsub_url  text;
  -- /017
  -- 020: the moment of this alert, Pacific, read once for every recipient
  stamp      text;
  tz_saved   text;
  -- /020
begin
  select value into api_key    from watcher_config where key = 'brevo_api_key';
  select value into from_email from watcher_config where key = 'alert_from_email';
  select value into from_name  from watcher_config where key = 'alert_from_name';
  from_name := coalesce(from_name, 'SF BWTF Alerts');
  if coalesce(api_key, '') = '' or coalesce(from_email, '') = '' then
    return null;  -- caller logs channel='config_missing'
  end if;
  -- 017
  select value into site_url from watcher_config where key = 'site_url';
  site_url := rtrim(coalesce(nullif(site_url, ''), 'https://bwtf-sf.vercel.app'), '/');
  -- /017
  -- 020: "Tue Sep 30, 7:12 AM PDT" — TZ needs the session zone, so switch it for one line and put it back
  tz_saved := current_setting('timezone');
  perform set_config('timezone', 'America/Los_Angeles', true);
  stamp := to_char(now(), 'Dy Mon FMDD, FMHH12:MI AM TZ');
  perform set_config('timezone', tz_saved, true);
  -- /020

  for r in select * from jsonb_array_elements(p_recipients)
  loop
    -- this recipient's transitions (their stations only)
    select coalesce(jsonb_agg(t), '[]'::jsonb) into matched
      from jsonb_array_elements(p_transitions) t
     where t->>'station_id' in (select jsonb_array_elements_text(r->'station_ids'));
    if jsonb_array_length(matched) = 0 then
      continue;
    end if;

    zone := null;
    if coalesce(r->>'email', '') <> '' then
      select region_zone into zone from subscribers
       where email = r->>'email' limit 1;
    end if;
    -- 017: the token lives on the subscriber row; no token (phone-only) → no link
    tok := null;
    unsub_url := null;
    if coalesce(r->>'email', '') <> '' then
      select unsubscribe_token into tok from subscribers
       where email = r->>'email' limit 1;
      if tok is not null then
        unsub_url := site_url || '/unsubscribe?t=' || tok;
      end if;
    end if;
    -- /017

    rendered := bwtf_render_alert(matched, p_simulated, zone, unsub_url, stamp);   -- 020: with the stamp

    if not p_simulated then
      select coalesce(jsonb_agg(jsonb_build_object('station_name',ws.station_name,'to',ws.status)
        order by ws.status <> 'cso',ws.station_name),'[]') into still
      from watcher_state_shadow ws
      where ws.status in ('posted','cso')
        and ws.station_id in (select jsonb_array_elements_text(r->'station_ids'))
        and not exists(select 1 from jsonb_array_elements(matched) t where t->>'station_id'=ws.station_id);
      rendered := bwtf_still_in_effect(rendered, still,
        case when (select count(distinct x) from jsonb_array_elements_text(r->'station_ids') x)=20
             then null else zone end);
    end if;

    sends := '[]'::jsonb;

    if coalesce(r->>'email', '') <> '' then
      -- 015: the delivery row first, so the request can carry its id as a tag
      del_id := null;
      begin
        insert into alert_deliveries (recipient, channel, simulated, subject, text_body, html_body, renderer)
        values (r->>'email', 'email', p_simulated, rendered->>'subject',
                rendered->>'text_body', rendered->>'html_body', renderer_ver)
        returning id into del_id;
      exception when others then
        del_id := null;   -- the email still goes out; the row is just missing
      end;
      -- /015
      select net.http_post(
        'https://api.brevo.com/v3/smtp/email',
        body := jsonb_build_object(
          'sender', jsonb_build_object('name', from_name, 'email', from_email),
          'to', jsonb_build_array(jsonb_build_object('email', r->>'email')),
          'subject', rendered->>'subject',
          'textContent', rendered->>'text_body',
          'htmlContent', rendered->>'html_body',
          -- 015: Brevo echoes tags on every event, a second key back to this row
          'tags', case when del_id is null then jsonb_build_array('bwtf-alert')
                       else jsonb_build_array('bwtf-alert', 'bwtf-delivery-' || del_id) end
          -- /015
          -- 017: one-click unsubscribe (RFC 8058) — mail clients show their own button
          , 'headers', case when unsub_url is null then '{}'::jsonb
                            else jsonb_build_object('List-Unsubscribe', '<' || unsub_url || '>',
                                                    'List-Unsubscribe-Post', 'List-Unsubscribe=One-Click') end
          -- /017
        ),
        headers := jsonb_build_object('api-key', api_key, 'accept', 'application/json',
                                      'content-type', 'application/json'),
        timeout_milliseconds := 15000
      ) into req_id;
      -- 015: remember which pg_net request carries this message
      begin
        update alert_deliveries set request_id = req_id where id = del_id;
      exception when others then
        null;
      end;
      -- /015
      sends := sends || jsonb_build_object('channel', 'email', 'request_id', req_id, 'delivery_id', del_id);
    end if;

    gateway := bwtf_sms_gateway(r->>'phone_number', r->>'carrier');
    if gateway is not null then
      -- 015: same for the SMS-by-email-gateway message
      del_id := null;
      begin
        insert into alert_deliveries (recipient, channel, simulated, subject, sms_text, renderer)
        values (gateway, 'sms', p_simulated, ' ', rendered->>'sms_text', renderer_ver)
        returning id into del_id;
      exception when others then
        del_id := null;
      end;
      -- /015
      select net.http_post(
        'https://api.brevo.com/v3/smtp/email',
        body := jsonb_build_object(
          'sender', jsonb_build_object('name', from_name, 'email', from_email),
          'to', jsonb_build_array(jsonb_build_object('email', gateway)),
          'subject', ' ',
          'textContent', rendered->>'sms_text',
          -- 015
          'tags', case when del_id is null then jsonb_build_array('bwtf-alert')
                       else jsonb_build_array('bwtf-alert', 'bwtf-delivery-' || del_id) end
          -- /015
        ),
        headers := jsonb_build_object('api-key', api_key, 'accept', 'application/json',
                                      'content-type', 'application/json'),
        timeout_milliseconds := 15000
      ) into req_id;
      -- 015
      begin
        update alert_deliveries set request_id = req_id where id = del_id;
      exception when others then
        null;
      end;
      -- /015
      sends := sends || jsonb_build_object('channel', 'sms', 'request_id', req_id, 'delivery_id', del_id);
    end if;

    out_all := out_all || jsonb_build_object(
      'email', r->>'email', 'phone_number', r->>'phone_number',
      'station_names', r->'station_names', 'sends', sends);
  end loop;

  return out_all;
end $$;

revoke all on function public.bwtf_dispatch_live(jsonb, jsonb, boolean) from public, anon, authenticated;
grant execute on function public.bwtf_dispatch_live(jsonb, jsonb, boolean) to service_role;

revoke all on function public.bwtf_queue_alerts(jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.bwtf_queue_alerts(jsonb,jsonb) to service_role;

revoke all on function public.bwtf_flush_alerts(timestamptz) from public, anon, authenticated;
grant execute on function public.bwtf_flush_alerts(timestamptz) to service_role;

revoke all on function public.bwtf_still_in_effect(jsonb,jsonb,text) from public, anon, authenticated;
grant execute on function public.bwtf_still_in_effect(jsonb,jsonb,text) to service_role;

commit;
