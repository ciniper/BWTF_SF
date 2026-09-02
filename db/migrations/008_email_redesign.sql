-- 008: email redesign — brand header + logo, per-station map thumbnails,
-- severity colors, zone name, disclaimer, unsubscribe footer.
-- (A second CTA to our /forecast page was built then pulled 2026-09-02 —
-- Chase wants the alert to stay single-purpose for now.)
--
-- Design notes (2026-09-02 email design review):
--   * Table-based layout, inline styles only — email clients strip <style>
--     and JS; webfonts don't load (Roboto falls back to Helvetica/Arial).
--   * Map thumbnails are PRE-GENERATED static JPEGs, one per station
--     (app/static/emailmaps/<station_id>.jpg, 220x160, served by the Flask
--     app at https://bwtf-sf.vercel.app/static/emailmaps/). No live map API
--     in the send path: a missing image degrades to alt text, the alert
--     still sends. Combinations need no extra images — an N-station alert
--     is N rows, each reusing its station's thumbnail.
--   * Severity: cso = #b5310a (red), posted = #d4763a (amber) — matches the
--     dashboard's semantic colors.
--   * subject and sms_text are byte-identical to 007; text_body gains the
--     zone, forecast link, disclaimer, and unsubscribe line.
--
-- The renderer gains p_zone (subscriber's signup zone, e.g. 'East Beaches');
-- the old 2-arg signature is DROPPED first — PostgREST cannot resolve
-- overloads, and features/alerts/render.py now always passes p_zone.
--
-- Paste order: after 001–007. PASTING THIS CHANGES PRODUCTION EMAILS
-- IMMEDIATELY (the pg dispatcher renders through this function). Deploy the
-- matching branch (thumbnails + render.py port) with it, then run
-- db/scripts/test_render_parity.py.

alter table if exists subscribers add column if not exists region_zone text;

drop function if exists public.bwtf_render_alert(jsonb, boolean);

-- ── the single renderer ──────────────────────────────────────────────────────
-- p_transitions: ONE recipient's matched transitions —
--   [{"station_id","station_name","from","to","simulated"}, ...]
-- p_zone: the recipient's signup zone label, or null.
-- Returns {"subject","sms_text","text_body","html_body"}.
create or replace function public.bwtf_render_alert(
  p_transitions jsonb, p_simulated boolean, p_zone text default null
) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  prefix     text := case when p_simulated then 'TEST ' else '' end;
  n_matched  integer := jsonb_array_length(p_transitions);
  zone       text := nullif(trim(coalesce(p_zone, '')), '');
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
         || 'You subscribed to SF beach alerts (Surfrider SF Blue Water Task Force). Reply "unsubscribe" to stop.'
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
      || 'Reply to this email with &quot;unsubscribe&quot; to stop alerts.</p>'
      || '</td></tr></table></td></tr></table></body></html>';

  return jsonb_build_object('subject', subject, 'sms_text', sms_text,
                            'text_body', text_body, 'html_body', html_body);
end $$;

revoke all on function public.bwtf_render_alert(jsonb, boolean, text) from public, anon, authenticated;
grant execute on function public.bwtf_render_alert(jsonb, boolean, text) to service_role;

-- ── dispatch: identical to 007 except the recipient's zone is passed in ──────
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
      select net.http_post(
        'https://api.brevo.com/v3/smtp/email',
        body := jsonb_build_object(
          'sender', jsonb_build_object('name', from_name, 'email', from_email),
          'to', jsonb_build_array(jsonb_build_object('email', r->>'email')),
          'subject', rendered->>'subject',
          'textContent', rendered->>'text_body',
          'htmlContent', rendered->>'html_body'),
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
          'textContent', rendered->>'sms_text'),
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
