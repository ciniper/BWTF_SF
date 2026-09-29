-- 017: one-click unsubscribe.
--
-- Until now every alert ended with 'Reply "unsubscribe" to stop' — a manual
-- step for Chase, and no List-Unsubscribe header for mail clients. This adds:
--   * subscribers.unsubscribe_token (uuid, generated per row) + unsubscribed_at.
--     The token is the whole secret: knowing it proves you hold the address.
--   * watcher_config.site_url — where /unsubscribe lives (swap when a custom
--     domain arrives; no migration needed).
--   * bwtf_render_alert(..., p_unsubscribe_url) — 008's renderer with the footer
--     sentence swapped for two links when a URL is given: "Change your sites"
--     (/manage?t=…, derived from the same token) and "Unsubscribe" (old
--     sentence otherwise, so the Python fallback and phone-only paths still
--     render). The 2- and 3-argument signatures are dropped so PostgREST never
--     has to choose between overloads.
--   * bwtf_dispatch_live — 015's loop plus marked 017 blocks: look up the
--     recipient's token, build the URL, pass it to the renderer, and send the
--     RFC 8058 headers (List-Unsubscribe + List-Unsubscribe-Post) so Gmail and
--     friends show their own unsubscribe button. renderer_ver becomes '017'.
--   The page itself is features/unsubscribe/page.py: GET shows a confirm
--   button, POST (the button, or a mail client's one-click POST) sets
--   active = false. Re-subscribing on /signup reactivates the row (existing
--   behaviour of the store's upsert).
--
-- Apply by hand in the Supabase SQL editor with no simulation active. Then run
-- db/scripts/test_render_parity.py (pg renderer == Python fallback) and one
-- simulated alert: the footer carries an Unsubscribe link, the Deliveries panel
-- stores it, and the link opens the confirm page.

-- ── subscribers: the token ───────────────────────────────────────────────────
alter table public.subscribers
  add column if not exists unsubscribe_token uuid not null default gen_random_uuid(),
  add column if not exists unsubscribed_at   timestamptz;
create unique index if not exists subscribers_unsubscribe_token_key
  on public.subscribers (unsubscribe_token);

insert into public.watcher_config (key, value)
values ('site_url', 'https://bwtf-sf.vercel.app')
on conflict (key) do nothing;

-- ── renderer: 008 + the unsubscribe footer ───────────────────────────────────
drop function if exists public.bwtf_render_alert(jsonb, boolean);
drop function if exists public.bwtf_render_alert(jsonb, boolean, text);
create or replace function public.bwtf_render_alert(
  p_transitions jsonb, p_simulated boolean, p_zone text default null,
  p_unsubscribe_url text default null
) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  prefix     text := case when p_simulated then 'TEST ' else '' end;
  n_matched  integer := jsonb_array_length(p_transitions);
  zone       text := nullif(trim(coalesce(p_zone, '')), '');
  unsub      text := nullif(trim(coalesce(p_unsubscribe_url, '')), '');
  manage     text := replace(nullif(trim(coalesce(p_unsubscribe_url, '')), ''), '/unsubscribe?t=', '/manage?t=');
  ev1        text;
  name1      text;
  subject    text;
  sms_text   text;
  text_body  text;
  rows_html  text;
  html_body  text;
begin
  if n_matched is null or n_matched = 0 then
    return null;
  end if;

  select case (p_transitions->0->>'to') when 'cso' then 'CSO discharge' else 'bacteria posting' end,
         p_transitions->0->>'station_name'
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
    from jsonb_array_elements(p_transitions) t;
  sms_text := left(sms_text, 320);

  select prefix || 'SF Beach Water Quality Alert'
         || case when zone is null then '' else ' — ' || zone end
         || E'\n\nNew events at your selected sites:\n' ||
         string_agg('- ' || (t->>'station_name') || ': ' ||
           case t->>'to' when 'cso' then 'CSO discharge — avoid water contact for 72 hours.'
                         else 'bacteria posting — water contact not recommended.' end, E'\n')
         || E'\n\nLive map: https://webapps.sfpuc.org/sapps/beachesandbay.html\n\n'
         || E'Alerts are a community-science tool, not an official advisory. Posted signs and SFPUC or health-department notices always win.\n\n'
         || 'You subscribed to SF beach alerts (Surfrider SF Blue Water Task Force). '
         || case when unsub is null then 'Reply "unsubscribe" to stop.'
                 else 'Change your sites: ' || manage || E'\nUnsubscribe: ' || unsub end
    into text_body
    from jsonb_array_elements(p_transitions) t;

  -- one row per station: thumbnail | name / severity label / advice
  select string_agg(
      '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
      || 'style="margin:0 0 12px;border:1px solid #dde5ee;border-left:4px solid '
      || case t->>'to' when 'cso' then '#b5310a' else '#d4763a' end
      || ';border-radius:12px;"><tr>'
      || '<td width="132" style="padding:0;line-height:0;"><img src="https://bwtf-sf.vercel.app/static/emailmaps/'
      || (t->>'station_id') || '.jpg" alt="Map: ' || (t->>'station_name')
      || '" width="132" height="96" style="display:block;border:0;"></td>'
      || '<td style="padding:10px 14px;vertical-align:middle;">'
      || '<div style="font-weight:700;font-size:15px;color:#26272a;">' || (t->>'station_name') || '</div>'
      || '<div style="font-size:12px;font-weight:700;letter-spacing:.06em;margin-top:3px;color:'
      || case t->>'to' when 'cso' then '#b5310a;">CSO DISCHARGE' else '#d4763a;">BACTERIA POSTING' end
      || '</div>'
      || '<div style="font-size:13px;color:#54576F;margin-top:3px;line-height:1.45;">'
      || case t->>'to' when 'cso' then 'Sewage discharge — avoid water contact for 72 hours.'
                       else 'Elevated bacteria — water contact not recommended.' end
      || '</div></td></tr></table>', '')
    into rows_html
    from jsonb_array_elements(p_transitions) t;

  html_body :=
      '<html><body style="margin:0;padding:24px;background:#E3EBF2;'
      || 'font-family:Roboto,''Helvetica Neue'',Arial,sans-serif;color:#26272a;">'
      || '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
      || '<table role="presentation" cellpadding="0" cellspacing="0" '
      || 'style="max-width:640px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;">'
      || '<tr><td style="background:#0072BC;padding:22px 24px;">'
      || '<img src="https://bwtf.surfrider.org/images/BWTF-Logo_White.png" alt="Blue Water Task Force" '
      || 'width="150" style="display:block;border:0;">'
      || '<div style="color:rgba(255,255,255,.85);font-size:12px;font-weight:700;'
      || 'letter-spacing:.12em;text-transform:uppercase;margin-top:12px;">Surfrider San Francisco</div>'
      || '<h1 style="margin:6px 0 0;color:#ffffff;font-size:24px;line-height:1.2;">'
      || prefix || 'Beach Water Quality Alert</h1>'
      || case when zone is null then '' else
         '<div style="margin-top:6px;color:#cfe8f9;font-size:14px;font-weight:700;">Your zone: ' || zone || '</div>'
         end
      || '</td></tr>'
      || '<tr><td style="padding:20px 24px 0;">'
      || '<p style="margin:0 0 14px;color:#54576F;line-height:1.6;">New events at your selected sites. '
      || 'Avoid water contact and check conditions before heading out.</p>'
      || rows_html
      || '<p style="margin:18px 0 0;">'
      || '<a href="https://webapps.sfpuc.org/sapps/beachesandbay.html" '
      || 'style="display:inline-block;background:#0072BC;color:#ffffff;text-decoration:none;'
      || 'padding:11px 18px;border-radius:999px;font-weight:700;font-size:14px;">View SFPUC Beach Map</a></p>'
      || '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
      || 'style="margin:18px 0 0;background:#E3EBF2;border-radius:12px;"><tr>'
      || '<td style="padding:12px 14px;font-size:12.5px;color:#54576F;line-height:1.5;">'
      || 'Alerts are a community-science tool, not an official advisory. '
      || 'Posted signs and SFPUC or health-department notices always win.</td></tr></table>'
      || '<p style="margin:16px 0 22px;font-size:12px;color:#8a93a3;line-height:1.5;">'
      || 'You subscribed to SF beach alerts from Surfrider San Francisco&#39;s Blue Water Task Force. '
      || case when unsub is null then 'Reply to this email with &quot;unsubscribe&quot; to stop alerts.'
              else '<a href="' || manage || '" style="color:#8a93a3;text-decoration:underline;">Change your sites</a>'
                   || ' &middot; <a href="' || unsub || '" style="color:#8a93a3;text-decoration:underline;">Unsubscribe</a>' end
      || '</p>'
      || '</td></tr></table></td></tr></table></body></html>';

  return jsonb_build_object('subject', subject, 'sms_text', sms_text,
                            'text_body', text_body, 'html_body', html_body);
end $$;

revoke all on function public.bwtf_render_alert(jsonb, boolean, text, text) from public, anon, authenticated;
grant execute on function public.bwtf_render_alert(jsonb, boolean, text, text) to service_role;

-- ── dispatch: 015's loop + the unsubscribe URL and headers ───────────────────
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
  renderer_ver text := '017';   -- bwtf_render_alert's migration; bump when the renderer changes
  -- /015
  -- 017: this recipient's unsubscribe link
  site_url   text;
  tok        uuid;
  unsub_url  text;
  -- /017
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

    rendered := bwtf_render_alert(matched, p_simulated, zone, unsub_url);

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

-- Check after applying:
--   select email, unsubscribe_token is not null as has_token from subscribers;   -- every row
--   select value from watcher_config where key = 'site_url';
--   venv/bin/python db/scripts/test_render_parity.py                              -- pg == Python, with and without the link
