-- 007: one renderer for alert messages — pg AND Python use it.
--
-- Motivation (2026-09-02): Chase's simulation produced two emails with
-- different designs — the pg sender and the legacy Python path each carried
-- their own template. This extracts the PRODUCTION rendering (004's inline
-- message building, byte-for-byte) into a pure function; bwtf_dispatch_live
-- now calls it, and the Python legacy path calls it via RPC
-- (features/alerts/render.py) instead of keeping its own copy. Any future
-- format change is made HERE once and both senders follow automatically.
--
-- Paste order: after 001–006. Rendering output is unchanged; dispatch
-- behavior is unchanged.

-- ── the single renderer ──────────────────────────────────────────────────────
-- p_transitions: ONE recipient's matched transitions —
--   [{"station_id","station_name","from","to","simulated"}, ...]
-- Returns {"subject","sms_text","text_body","html_body"}.
create or replace function public.bwtf_render_alert(p_transitions jsonb, p_simulated boolean)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  prefix     text := case when p_simulated then 'TEST ' else '' end;
  n_matched  integer := jsonb_array_length(p_transitions);
  ev1        text;
  name1      text;
  subject    text;
  sms_text   text;
  text_body  text;
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

  select prefix || E'SF Beach Water Quality Alert\n\nNew events at your selected sites:\n' ||
         string_agg('- ' || (t->>'station_name') || ': ' ||
           case t->>'to' when 'cso' then 'CSO discharge — avoid water contact for 72 hours.'
                         else 'bacteria posting — water contact not recommended.' end, E'\n')
         || E'\n\nLive map: https://webapps.sfpuc.org/sapps/beachesandbay.html\n\n'
         || 'You are receiving this because you subscribed on the Surfrider SF BWTF dashboard.'
    into text_body
    from jsonb_array_elements(p_transitions) t;

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
    from jsonb_array_elements(p_transitions) t;

  return jsonb_build_object('subject', subject, 'sms_text', sms_text,
                            'text_body', text_body, 'html_body', html_body);
end $$;

revoke all on function public.bwtf_render_alert(jsonb, boolean) from public, anon, authenticated;
grant execute on function public.bwtf_render_alert(jsonb, boolean) to service_role;

-- ── dispatch: identical to 004 except rendering goes through the renderer ───
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

    rendered := bwtf_render_alert(matched, p_simulated);

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
