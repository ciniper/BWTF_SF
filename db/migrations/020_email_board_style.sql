-- 020 — the alert email in the site's voice (Chase, 2026-09-30: "I like the email direction").
--
-- What changes, and only this: bwtf_render_alert's output. The header reads "SF BEACH WATER
-- QUALITY ALERT" over the site's mark; the card is the Today board's — a 2px border in the grade
-- colour, an eyebrow, a headline that states the fact in counts, the status phrase alone in its
-- colour ("Sewage discharge at 1 beach. Bacteria posting at 1 beach."), a facts line
-- naming the beaches, one row per station with its map thumbnail and one factual line, SFPUC's
-- guidance stated once and attributed, one button — "Live status" — to the site's board. SFPUC's
-- own map moves to the small print, which also drops "always win" for "take precedence". No
-- timestamp in the body (the message's own Date header carries it; the Python port must match
-- byte for byte and cannot share a clock with the database).
--
-- bwtf_dispatch_live is restated verbatim from 017 with renderer_ver '020', so every
-- alert_deliveries row names the renderer that wrote it. Signatures unchanged.
--
-- Apply in the Supabase SQL editor with no simulation active (bwtf_shadow_tick and the live
-- dispatcher read these at call time; nothing is queued). Then:
--   venv/bin/python db/scripts/test_render_parity.py     -- pg == Python, with and without the link
--   select renderer, count(*) from alert_deliveries group by 1;   -- new rows say 020

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
  site       text;
  map_url    text := 'https://webapps.sfpuc.org/sapps/beachesandbay.html';
  n_cso      integer;
  n_posted   integer;
  names_cso  text;
  names_post text;
  name1      text;
  cso1       boolean;
  tone       text;
  subject    text;
  headline   text;
  facts      text;
  sms_text   text;
  text_body  text;
  rows_html  text;
  html_body  text;
  footer_sub text;
begin
  if n_matched is null or n_matched = 0 then
    return null;
  end if;
  -- the site's address: the unsubscribe link carries it (017); the public site otherwise
  site := coalesce(nullif(split_part(unsub, '/unsubscribe?t=', 1), ''), 'https://bwtf-sf.vercel.app');

  select count(*) filter (where t->>'to' = 'cso'),
         count(*) filter (where t->>'to' <> 'cso'),
         string_agg(t->>'station_name', ', ' order by ord) filter (where t->>'to' = 'cso'),
         string_agg(t->>'station_name', ', ' order by ord) filter (where t->>'to' <> 'cso')
    into n_cso, n_posted, names_cso, names_post
    from jsonb_array_elements(p_transitions) with ordinality as x(t, ord);
  name1 := p_transitions->0->>'station_name';
  cso1  := (p_transitions->0->>'to') = 'cso';
  tone  := case when n_cso > 0 then '#b5310a' else '#d4763a' end;

  -- subject: one station is named; several are counted
  if n_matched = 1 then
    subject := prefix || 'Beach alert: ' || name1 || case when cso1 then ' — sewage discharge' else ' posted for bacteria' end;
  else
    subject := prefix || 'Beach alert: ' || concat_ws(', ',
      case when n_cso > 0 then 'sewage discharge at ' || n_cso || ' beach' || case when n_cso > 1 then 'es' else '' end end,
      case when n_posted > 0 then 'bacteria posting' || case when n_posted > 1 then 's' else '' end || ' at ' || n_posted || ' beach' || case when n_posted > 1 then 'es' else '' end end);
  end if;

  -- headline in the board's voice: counts, coloured by grade; one station is named outright
  -- names and counts stay black; only the status phrase takes its colour (Chase, 2026-09-30)
  if n_matched = 1 then
    headline := name1 || case when cso1 then ' has a <span style="color:#b5310a">sewage discharge</span>.'
                                        else ' is <span style="color:#d4763a">posted for bacteria</span>.' end;
  else
    headline := concat_ws(' ',
      case when n_cso > 0 then '<span style="color:#b5310a">Sewage discharge</span> at ' || n_cso || ' beach' || case when n_cso > 1 then 'es' else '' end || '.' end,
      case when n_posted > 0 then '<span style="color:#d4763a">Bacteria posting' || case when n_posted > 1 then 's' else '' end || '</span> at ' || n_posted || ' beach' || case when n_posted > 1 then 'es' else '' end || '.' end);
  end if;
  facts := case when n_cso > 0 then 'Discharging: ' || names_cso || '. ' else '' end
        || case when n_posted > 0 then 'Posted: ' || names_post || '. ' else '' end
        || 'From SFPUC''s beach map, which this site checks every minute.';

  select prefix || 'Beach alert: '
         || string_agg(t->>'station_name' || ' — ' || case t->>'to' when 'cso' then 'sewage discharge' else 'posted for bacteria' end, '; ' order by ord)
         || '. SFPUC: avoid water contact. ' || site || '/'
    into sms_text
    from jsonb_array_elements(p_transitions) with ordinality as x(t, ord);
  sms_text := left(sms_text, 320);

  select subject || E'\n\n'
         || string_agg('- ' || (t->>'station_name') || ': ' || case t->>'to' when 'cso' then 'sewage discharge.' else 'posted for bacteria.' end, E'\n' order by ord)
         || E'\n\nSFPUC''s guidance: avoid water contact at a posted beach, and for 72 hours after a discharge or heavy rain.\n\n'
         || 'Live status: ' || site || E'/\nSFPUC''s map: ' || map_url || E'\n\n'
         || E'Community science by Surfrider SF''s Blue Water Task Force, not an official advisory; posted signs and notices from SFPUC or the health department take precedence.\n'
         || case when unsub is null then 'Reply to this email with "unsubscribe" to stop alerts.'
                 else 'Change your sites: ' || manage || E'\nUnsubscribe: ' || unsub end
    into text_body
    from jsonb_array_elements(p_transitions) with ordinality as x(t, ord);

  -- one row per station: thumbnail | name / status / one fact
  select string_agg(
      '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 10px;border:2px solid '
      || case t->>'to' when 'cso' then '#b5310a' else '#d4763a' end || ';border-radius:14px;"><tr>'
      || '<td width="112" style="padding:0;line-height:0;"><img src="' || site || '/static/emailmaps/' || (t->>'station_id')
      || '.jpg" alt="" width="112" height="82" style="display:block;border:0;border-radius:12px 0 0 12px;"></td>'
      || '<td style="padding:10px 14px;vertical-align:middle;">'
      || '<div style="font-weight:700;font-size:15px;color:#26272a;">' || (t->>'station_name') || '</div>'
      || '<div style="font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-top:2px;color:'
      || case t->>'to' when 'cso' then '#b5310a;">Sewage discharge' else '#d4763a;">Posted for bacteria' end || '</div>'
      || '<div style="font-size:13px;color:#54576F;margin-top:3px;line-height:1.45;">'
      || case t->>'to' when 'cso' then 'A combined-sewer discharge reported by SFPUC; the posting stays up about 72 hours after it ends.'
                       else 'The latest sample was over the state single-sample limit; the sign stays up until a clean resample.' end
      || '</div></td></tr></table>', '' order by ord)
    into rows_html
    from jsonb_array_elements(p_transitions) with ordinality as x(t, ord);

  footer_sub := case when unsub is null
    then 'You subscribed to SF beach alerts. Reply to this email with &quot;unsubscribe&quot; to stop them.'
    else 'You get this because you chose ' || case when zone is null then 'these beaches' else 'the ' || zone || ' zone' end
         || ' &middot; <a href="' || manage || '" style="color:#54576F;">Change your sites</a>'
         || ' &middot; <a href="' || unsub || '" style="color:#54576F;">Unsubscribe</a>' end;

  html_body :=
      '<html><body style="margin:0;padding:24px 16px;background:#E3EBF2;font-family:Roboto,''Helvetica Neue'',Arial,sans-serif;color:#26272a;">'
      || '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
      || '<table role="presentation" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">'
      || '<tr><td style="padding:0 6px 12px;"><table role="presentation" cellpadding="0" cellspacing="0"><tr>'
      || '<td style="padding:0 10px 0 0;line-height:0;"><img src="' || site || '/static/brand/bwtf_144x144.png" alt="" width="36" height="36" style="display:block;border:0;border-radius:9px;"></td>'
      || '<td><div style="font-weight:900;font-size:17px;letter-spacing:.04em;text-transform:uppercase;color:#26272a;line-height:1.1;">' || prefix || 'SF Beach Water Quality Alert</div>'
      || '<div style="font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#54576F;margin-top:2px;">Surfrider SF &middot; Blue Water Task Force</div></td>'
      || '</tr></table></td></tr>'
      || '<tr><td style="background:#ffffff;border:2px solid ' || tone || ';border-radius:20px;padding:20px 22px 18px;">'
      || '<div style="font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#54576F;">Beach alert'
      || case when zone is null then '' else ' &middot; ' || zone || ' zone' end || '</div>'
      || '<h1 style="margin:6px 0 0;font-size:26px;line-height:1.15;font-weight:900;letter-spacing:.01em;color:#26272a;">' || headline || '</h1>'
      || '<p style="margin:8px 0 16px;color:#54576F;font-size:14px;line-height:1.5;">' || facts || '</p>'
      || rows_html
      || '<p style="margin:14px 0 0;font-size:13.5px;color:#26272a;line-height:1.5;"><b>SFPUC''s guidance:</b> avoid water contact at a posted beach, and for 72 hours after a discharge or heavy rain.</p>'
      || '<p style="margin:16px 0 4px;"><a href="' || site || '/" style="display:inline-block;background:#0072BC;color:#ffffff;text-decoration:none;padding:11px 18px;border-radius:999px;font-weight:700;font-size:14px;">Live status</a></p>'
      || '</td></tr>'
      || '<tr><td style="padding:14px 8px 0;font-size:12px;color:#54576F;line-height:1.55;">'
      || 'Community science by Surfrider SF''s Blue Water Task Force, not an official advisory; posted signs and notices from SFPUC or the health department take precedence. '
      || 'SFPUC''s own map: <a href="' || map_url || '" style="color:#54576F;">webapps.sfpuc.org</a>.<br>' || footer_sub
      || '</td></tr></table></td></tr></table></body></html>';

  return jsonb_build_object('subject', subject, 'sms_text', sms_text,
                            'text_body', text_body, 'html_body', html_body);
end $$;

revoke all on function public.bwtf_render_alert(jsonb, boolean, text, text) from public, anon, authenticated;
grant execute on function public.bwtf_render_alert(jsonb, boolean, text, text) to service_role;

-- ── dispatch: 017's loop, restated so every delivery row is stamped '020' ────────────────
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
  renderer_ver text := '020';   -- bwtf_render_alert's migration; bump when the renderer changes
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
