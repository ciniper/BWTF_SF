-- 015: alert deliveries — what each subscriber was sent, and whether Brevo took it.
--
-- Until now the pg dispatcher (bwtf_dispatch_live, 004 → 008) kept only the
-- pg_net request id per message, inside alert_log.results. Brevo's reply landed
-- in net._http_response and was purged by pg_net after ~6 hours (pg_net.ttl)
-- unread, so a rejected API key, exhausted credits or a bad address looked
-- exactly like a good send. The rendered message was never stored either.
--
-- What this adds (level 1 of the "persist delivery history" TODO):
--   * alert_deliveries — one row per message (recipient × channel) with the
--     rendered subject/body, pg_net's request id, and Brevo's reply once read.
--   * bwtf_dispatch_live — 008's send loop plus, per message: insert the
--     delivery row BEFORE the POST (own exception block), tag the Brevo request
--     with the row id, record the request id AFTER (own block). The POST itself
--     is untouched and never inside a swallowing block: a fault in the new code
--     costs a log row, never an email.
--   * bwtf_link_deliveries — after-insert trigger on alert_log that points the
--     rows at their alert_log row (the callers in 006/009/011 store the
--     dispatcher's return, which now carries delivery_id, in results — they
--     are not redefined here).
--   * bwtf_check_deliveries — its own pg_cron job every minute (NOT a change to
--     the tick): reads Brevo's reply for unchecked rows from net._http_response
--     while it is still there, stamps http_status / message_id / error, and
--     logs failures to watcher_errors (kind 'delivery'). Rows never answered
--     before the purge are marked so rather than left blank.
--   Level 2 (delivered / bounced, from Brevo's events API) fills
--   delivery_state / delivery_at later, polled from the Python refresh.
--
-- Nothing here touches watcher_runtime.last_error (the health gate's column)
-- or bwtf_shadow_tick.
--
-- Apply by hand in the Supabase SQL editor with no simulation active, in a dry
-- spell. Check afterwards with a simulated alert to the test recipients: rows
-- appear at dispatch and show http_status 201 + a message_id a minute later.

-- ── the table ────────────────────────────────────────────────────────────────
create table if not exists public.alert_deliveries (
  id             bigint generated always as identity primary key,
  alert_log_id   bigint references public.alert_log(id),   -- set by the trigger right after dispatch
  recipient      text not null,          -- email address, or the carrier's SMS gateway address
  channel        text not null,          -- 'email' | 'sms'
  simulated      boolean not null default false,
  request_id     bigint,                 -- pg_net's id for the POST (null if recording it failed)
  subject        text,
  sms_text       text,
  text_body      text,
  html_body      text,
  renderer       text,                   -- which bwtf_render_alert wrote it (that function's migration number)
  sent_at        timestamptz not null default now(),
  checked_at     timestamptz,            -- when bwtf_check_deliveries read (or gave up on) Brevo's reply
  http_status    integer,                -- 201 = Brevo accepted the message
  message_id     text,                   -- Brevo's messageId from the reply; level 2 joins on it
  error          text,                   -- pg_net's error, Brevo's message, or why nothing could be read
  delivery_state text,                   -- level 2: delivered | soft_bounce | hard_bounce | blocked | spam | deferred | error
  delivery_at    timestamptz,
  source         text not null default 'pg'   -- 'pg' = bwtf_dispatch_live; 'manual' = the /alerts dispatch button (Python)
);
create index if not exists alert_deliveries_alert_log_idx on public.alert_deliveries (alert_log_id);
create index if not exists alert_deliveries_sent_at_idx   on public.alert_deliveries (sent_at desc);
create index if not exists alert_deliveries_unchecked_idx on public.alert_deliveries (id) where checked_at is null;
alter table public.alert_deliveries enable row level security;
revoke all on public.alert_deliveries from anon, authenticated;
grant all on public.alert_deliveries to service_role;

-- ── dispatch: 008's loop with the delivery row around each POST ──────────────
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
  gateway    text;
  req_id     bigint;
  sends      jsonb;
  out_all    jsonb := '[]'::jsonb;
  -- 015: the delivery row for the message about to be sent
  del_id       bigint;
  renderer_ver text := '008';   -- bwtf_render_alert's migration; bump when the renderer changes
  -- /015
begin
  select value into api_key    from watcher_config where key = 'brevo_api_key';
  select value into from_email from watcher_config where key = 'alert_from_email';
  select value into from_name  from watcher_config where key = 'alert_from_name';
  from_name := coalesce(from_name, 'SF BWTF Alerts');
  if coalesce(api_key, '') = '' or coalesce(from_email, '') = '' then
    return null;  -- caller logs channel='config_missing'
  end if;

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

    rendered := bwtf_render_alert(matched, p_simulated, zone);

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

-- ── link the rows to their alert_log row once the caller has written it ──────
-- The callers (bwtf_log_escalations in 006, the simulation dispatchers in
-- 009/011) insert alert_log AFTER dispatch, with results.recipients = the
-- dispatcher's return; each send now carries delivery_id, so the link can be
-- made here without redefining any caller. results may also be a plain list
-- (the Python manual path) — then there is nothing to link and nothing happens.
create or replace function public.bwtf_link_deliveries()
returns trigger
language plpgsql security definer set search_path = public as $$
begin
  begin
    update alert_deliveries d
       set alert_log_id = new.id
     where d.alert_log_id is null
       and d.id in (
         select (s->>'delivery_id')::bigint
           from jsonb_array_elements(case when jsonb_typeof(new.results->'recipients') = 'array'
                                          then new.results->'recipients' else '[]'::jsonb end) rec,
                jsonb_array_elements(case when jsonb_typeof(rec->'sends') = 'array'
                                          then rec->'sends' else '[]'::jsonb end) s
          where s->>'delivery_id' is not null);
  exception when others then
    perform bwtf_log_error('delivery_link', sqlerrm, null);
  end;
  return new;
end $$;
revoke all on function public.bwtf_link_deliveries() from public, anon, authenticated;
grant execute on function public.bwtf_link_deliveries() to service_role;
drop trigger if exists alert_log_link_deliveries on public.alert_log;
create trigger alert_log_link_deliveries
  after insert on public.alert_log
  for each row execute function public.bwtf_link_deliveries();

-- ── read Brevo's reply while pg_net still has it ─────────────────────────────
-- pg_net answers within seconds and purges responses after pg_net.ttl (6 h by
-- default); this runs every minute so nearly every row is checked on the next
-- tick. A 2xx carries {"messageId": "<…>"}; anything else carries Brevo's
-- {"code", "message"} or pg_net's own error / timeout.
create or replace function public.bwtf_check_deliveries()
returns void
language plpgsql security definer set search_path = public as $$
declare
  d    record;
  body jsonb;
  msg  text;
begin
  for d in
    select ad.id, ad.recipient, ad.channel, ad.request_id, ad.sent_at,
           r.status_code, r.content, r.error_msg, r.timed_out
      from alert_deliveries ad
      left join net._http_response r on r.id = ad.request_id
     where ad.checked_at is null
     order by ad.id
     limit 200
  loop
    if d.status_code is not null or d.error_msg is not null or coalesce(d.timed_out, false) then
      body := null;
      begin
        body := d.content::jsonb;
      exception when others then
        body := null;
      end;
      if d.status_code between 200 and 299 then
        update alert_deliveries
           set checked_at = now(), http_status = d.status_code,
               message_id = body->>'messageId', error = null
         where id = d.id;
      else
        msg := case when coalesce(d.timed_out, false) then 'timed out'
                    else coalesce(d.error_msg, body->>'message', body->>'code', left(d.content, 300), 'no detail') end;
        update alert_deliveries
           set checked_at = now(), http_status = d.status_code, error = msg
         where id = d.id;
        perform bwtf_log_error('delivery',
                               format('%s to %s: %s', d.channel, d.recipient, msg), d.status_code);
      end if;
    elsif d.request_id is null and d.sent_at < now() - interval '10 minutes' then
      update alert_deliveries
         set checked_at = now(), error = 'request id not recorded at dispatch'
       where id = d.id;
    elsif d.sent_at < now() - interval '6 hours' then   -- pg_net.ttl: the reply is gone
      update alert_deliveries
         set checked_at = now(), error = 'no reply recorded before pg_net purged it'
       where id = d.id;
    end if;
  end loop;
exception when others then
  perform bwtf_log_error('deliveries', sqlerrm, null);
end $$;
revoke all on function public.bwtf_check_deliveries() from public, anon, authenticated;
grant execute on function public.bwtf_check_deliveries() to service_role;

do $$
begin
  perform cron.unschedule('bwtf-check-deliveries');
exception when others then
  null;
end $$;
select cron.schedule('bwtf-check-deliveries', '* * * * *', 'select public.bwtf_check_deliveries()');

-- Check after applying:
--   select jobname, schedule from cron.job;            -- bwtf-check-deliveries every minute
--   select * from alert_deliveries order by id desc;   -- rows from the first (simulated) alert,
--                                                      -- http_status 201 + message_id a minute later
--   select * from watcher_errors where kind like 'deliver%' order by at desc;
