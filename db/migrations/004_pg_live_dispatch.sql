-- Phase 2, role reversal: give the pg watcher a LIVE dispatch path.
--
-- mode='shadow' (default): unchanged — would-send decisions logged, no sends.
-- mode='live': on transitions, Postgres itself sends via Brevo's HTTP API
-- (pg_net POST per recipient; SMS rides the carrier email gateways, which are
-- just email) and logs source='pg_live' with the async request ids.
--
-- Config read from watcher_config: brevo_api_key, alert_from_email,
-- alert_from_name (optional). If mode='live' but the Brevo config is missing,
-- the event is logged with channel='config_missing' — an event must never
-- vanish silently.
--
-- Also adds the hourly free-tier keep-alive job: pings watcher_config
-- 'keepalive_url' if set (seed later if/when the observer thread retires —
-- while the thread runs, its 2-min reads are the keep-alive).
--
-- Paste order: after 001–003. Applying this changes NOTHING by itself
-- (mode stays 'shadow'); the flip is a config update done deliberately later.

-- ── carrier gateway map (mirror of EmailToSMSNotifier.CARRIER_GATEWAYS) ─────
create or replace function public.bwtf_sms_gateway(p_phone text, p_carrier text)
returns text
language plpgsql immutable as $$
declare
  digits text;
  domain text;
begin
  digits := regexp_replace(coalesce(p_phone, ''), '[^0-9]', '', 'g');
  if length(digits) = 11 and left(digits, 1) = '1' then
    digits := substr(digits, 2);
  end if;
  if length(digits) <> 10 then
    return null;
  end if;
  domain := case lower(coalesce(p_carrier, ''))
    when 'verizon'    then 'vtext.com'
    when 'att'        then 'txt.att.net'
    when 'tmobile'    then 'tmomail.net'
    when 'sprint'     then 'messaging.sprintpcs.com'
    when 'cricket'    then 'sms.cricketwireless.net'
    when 'metropcs'   then 'mymetropcs.com'
    when 'uscellular' then 'email.uscc.net'
    else null end;
  if domain is null then
    return null;
  end if;
  return digits || '@' || domain;
end $$;

-- ── live dispatch: one Brevo POST per recipient/channel ─────────────────────
-- Returns the per-recipient results (with pg_net request ids — sends are
-- async; delivery confirmation lives in Brevo's dashboard/logs).
create or replace function public.bwtf_dispatch_live(
  p_transitions jsonb, p_recipients jsonb, p_simulated boolean
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  api_key    text;
  from_email text;
  from_name  text;
  prefix     text;
  r          jsonb;
  matched    jsonb;
  n_matched  integer;
  ev1        text;
  name1      text;
  subject    text;
  sms_text   text;
  text_body  text;
  html_body  text;
  gateway    text;
  req_id     bigint;
  sends      jsonb;
  out_all    jsonb := '[]'::jsonb;
begin
  select value into api_key    from watcher_config where key = 'brevo_api_key';
  select value into from_email from watcher_config where key = 'alert_from_email';
  select value into from_name  from watcher_config where key = 'alert_from_name';
  from_name := coalesce(from_name, 'SF BWTF Alerts');
  if coalesce(api_key, '') = '' or coalesce(from_email, '') = '' then
    return null;  -- caller logs channel='config_missing'
  end if;
  prefix := case when p_simulated then 'TEST ' else '' end;

  for r in select * from jsonb_array_elements(p_recipients)
  loop
    -- this recipient's transitions (their stations only)
    select coalesce(jsonb_agg(t), '[]'::jsonb) into matched
      from jsonb_array_elements(p_transitions) t
     where t->>'station_id' in (select jsonb_array_elements_text(r->'station_ids'));
    n_matched := jsonb_array_length(matched);
    if n_matched = 0 then
      continue;
    end if;

    select case (matched->0->>'to') when 'cso' then 'CSO discharge' else 'bacteria posting' end,
           matched->0->>'station_name'
      into ev1, name1;
    if n_matched = 1 then
      subject := prefix || 'SF Beach Alert: ' || ev1 || ' at ' || name1;
    else
      subject := prefix || 'SF Beach Alert: ' || n_matched || ' sites affected';
    end if;

    select '🚨 ' || prefix || 'SF Beach alert: ' ||
           string_agg(t->>'station_name' || ' (' ||
             case t->>'to' when 'cso' then 'CSO discharge' else 'bacteria posting' end || ')', '; ')
           || '. Avoid water contact. Map: https://webapps.sfpuc.org/sapps/beachesandbay.html'
      into sms_text
      from jsonb_array_elements(matched) t;
    sms_text := left(sms_text, 320);

    select prefix || E'SF Beach Water Quality Alert\n\nNew events at your selected sites:\n' ||
           string_agg('- ' || (t->>'station_name') || ': ' ||
             case t->>'to' when 'cso' then 'CSO discharge — avoid water contact for 72 hours.'
                           else 'bacteria posting — water contact not recommended.' end, E'\n')
           || E'\n\nLive map: https://webapps.sfpuc.org/sapps/beachesandbay.html\n\n'
           || 'You are receiving this because you subscribed on the Surfrider SF BWTF dashboard.'
      into text_body
      from jsonb_array_elements(matched) t;

    select '<html><body style="margin:0;padding:24px;background:#e2e8ee;'
        || 'font-family:''Avenir Next'',''Trebuchet MS'',''Segoe UI'',sans-serif;color:#26272a;">'
        || '<div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:24px;overflow:hidden;">'
        || '<div style="background:#1f6fb0;padding:22px 24px;">'
        || '<div style="color:rgba(255,255,255,.85);font-size:12px;font-weight:700;'
        || 'letter-spacing:.12em;text-transform:uppercase;">Blue Water Task Force • Surfrider SF</div>'
        || '<h1 style="margin:10px 0 0;color:#ffffff;font-size:26px;">' || prefix || 'Beach Water Quality Alert</h1>'
        || '</div><div style="padding:22px 24px;">'
        || '<div style="background:rgba(209,92,92,.10);border-radius:16px;padding:14px 16px;margin-bottom:16px;">'
        || '<p style="margin:0 0 8px;font-weight:700;">New events at your sites</p>'
        || '<ul style="margin:0;padding-left:18px;line-height:1.6;">'
        || string_agg('<li><b>' || (t->>'station_name') || '</b> — '
             || case t->>'to' when 'cso' then 'CSO discharge' else 'bacteria posting' end || '</li>', '')
        || '</ul></div>'
        || '<p style="margin:0;color:#5e6a71;line-height:1.6;">Avoid water contact and check the live map before heading out.</p>'
        || '<p style="margin:16px 0 0;"><a href="https://webapps.sfpuc.org/sapps/beachesandbay.html" '
        || 'style="display:inline-block;background:#317fb2;color:#ffffff;text-decoration:none;'
        || 'padding:11px 16px;border-radius:999px;font-weight:700;">View SFPUC Beach Map</a></p>'
        || '</div></div></body></html>'
      into html_body
      from jsonb_array_elements(matched) t;

    sends := '[]'::jsonb;

    if coalesce(r->>'email', '') <> '' then
      select net.http_post(
        'https://api.brevo.com/v3/smtp/email',
        body := jsonb_build_object(
          'sender', jsonb_build_object('name', from_name, 'email', from_email),
          'to', jsonb_build_array(jsonb_build_object('email', r->>'email')),
          'subject', subject,
          'textContent', text_body,
          'htmlContent', html_body),
        headers := jsonb_build_object('api-key', api_key, 'accept', 'application/json',
                                      'content-type', 'application/json'),
        timeout_milliseconds := 15000
      ) into req_id;
      sends := sends || jsonb_build_object('channel', 'email', 'request_id', req_id);
    end if;

    gateway := bwtf_sms_gateway(r->>'phone_number', r->>'carrier');
    if gateway is not null then
      select net.http_post(
        'https://api.brevo.com/v3/smtp/email',
        body := jsonb_build_object(
          'sender', jsonb_build_object('name', from_name, 'email', from_email),
          'to', jsonb_build_array(jsonb_build_object('email', gateway)),
          'subject', ' ',
          'textContent', sms_text),
        headers := jsonb_build_object('api-key', api_key, 'accept', 'application/json',
                                      'content-type', 'application/json'),
        timeout_milliseconds := 15000
      ) into req_id;
      sends := sends || jsonb_build_object('channel', 'sms', 'request_id', req_id);
    end if;

    out_all := out_all || jsonb_build_object(
      'email', r->>'email', 'phone_number', r->>'phone_number',
      'station_names', r->'station_names', 'sends', sends);
  end loop;

  return out_all;
end $$;

revoke all on function public.bwtf_dispatch_live(jsonb, jsonb, boolean) from public, anon, authenticated;
grant execute on function public.bwtf_dispatch_live(jsonb, jsonb, boolean) to service_role;

-- ── process_payload: mode-aware (RPC default stays 'shadow' — tests can never
--    send). Also now includes station_ids per recipient. ─────────────────────
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
  recipients     jsonb;
  n_recipients   integer := 0;
  ev_type        text;
  any_simulated  boolean := false;
  dispatched     jsonb;
  log_source     text;
  log_channel    text;
begin
  select not exists (select 1 from watcher_state_shadow) into is_baseline;

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
        log_source := 'pg_live'; log_channel := 'config_missing';
        dispatched := recipients;  -- keep the would-send list so nothing is lost
      else
        log_source := 'pg_live'; log_channel := 'mixed';
      end if;
    else
      log_source := 'pg_shadow'; log_channel := 'shadow';
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

  return jsonb_build_object(
    'stations', n_stations, 'baselined', is_baseline, 'mode', p_mode,
    'transitions', coalesce(jsonb_array_length(transitions), 0),
    'recipients', coalesce(n_recipients, 0));
end $$;

revoke all on function public.bwtf_process_payload(jsonb, text) from public, anon, authenticated;
grant execute on function public.bwtf_process_payload(jsonb, text) to service_role;

-- ── tick: pass the configured mode through ──────────────────────────────────
create or replace function public.bwtf_shadow_tick()
returns void
language plpgsql security definer set search_path = public as $$
declare
  cfg_mode   text;
  ping_url   text;
  fetch_url  text;
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

  begin
    select r.status_code, r.content, r.timed_out, r.error_msg into resp
      from net._http_response r
      join watcher_runtime rt on r.id = rt.last_request_id
     where rt.id = 1;

    if resp.status_code = 200 and resp.content is not null then
      raw := substring(resp.content from position('[' in resp.content));
      raw := left(raw, length(raw) - position(']' in reverse(raw)) + 1);
      payload := raw::jsonb;
      if jsonb_array_length(payload) > 0 then
        summary := bwtf_process_payload(payload, coalesce(cfg_mode, 'shadow'));
        processed := true;
        update watcher_runtime
           set last_processed_at = now(), last_fetch_status = resp.status_code,
               last_summary = summary, last_error = null
         where id = 1;
      end if;
    elsif resp.status_code is not null then
      update watcher_runtime
         set last_fetch_status = resp.status_code,
             last_error = 'HTTP ' || resp.status_code, last_error_at = now()
       where id = 1;
    elsif resp.error_msg is not null or resp.timed_out then
      update watcher_runtime
         set last_error = coalesce(resp.error_msg, 'timed out'),
             last_error_at = now()
       where id = 1;
    end if;
  exception when others then
    update watcher_runtime
       set last_error = 'harvest: ' || sqlerrm, last_error_at = now()
     where id = 1;
  end;

  if processed then
    begin
      select value into ping_url from watcher_config where key = 'healthchecks_ping_url';
      if ping_url is not null and ping_url <> '' then
        perform net.http_get(ping_url, timeout_milliseconds := 10000);
      end if;
    exception when others then
      null;
    end;
  end if;

  begin
    select value into fetch_url from watcher_config where key = 'sfpuc_url';
    if fetch_url is not null and fetch_url <> '' then
      select net.http_get(fetch_url, timeout_milliseconds := 15000) into req_id;
      update watcher_runtime set last_request_id = req_id, requested_at = now() where id = 1;
    end if;
  exception when others then
    update watcher_runtime
       set last_error = 'issue: ' || sqlerrm, last_error_at = now()
     where id = 1;
  end;
end $$;

revoke all on function public.bwtf_shadow_tick() from public, anon, authenticated;
grant execute on function public.bwtf_shadow_tick() to service_role;

-- ── hourly keep-alive (no-op until 'keepalive_url' is seeded — only needed
--    if/when the observer thread retires) ────────────────────────────────────
create or replace function public.bwtf_keepalive()
returns void
language plpgsql security definer set search_path = public as $$
declare
  url text;
begin
  select value into url from watcher_config where key = 'keepalive_url';
  if url is not null and url <> '' then
    perform net.http_get(url, timeout_milliseconds := 10000);
  end if;
exception when others then
  null;
end $$;

revoke all on function public.bwtf_keepalive() from public, anon, authenticated;

do $$
begin
  perform cron.unschedule('bwtf-keepalive');
exception when others then
  null;
end $$;
select cron.schedule('bwtf-keepalive', '17 * * * *', 'select public.bwtf_keepalive()');

-- drop the old single-arg signature so there's exactly one process_payload
drop function if exists public.bwtf_process_payload(jsonb);
