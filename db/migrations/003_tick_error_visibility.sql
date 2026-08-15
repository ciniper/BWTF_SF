-- Phase 2 amendment: make tick failures VISIBLE and the fetch URL configurable.
--
-- Motivated by the first shadow-run incident: every SFPUC request from this
-- Supabase project dies in the TCP/SSL handshake (SFPUC's firewall blackholes
-- the project's egress IP range — DNS 0.01ms, handshake 45s, no response), and
-- the original tick recorded nothing for error responses, making the failure
-- invisible from the outside. Now:
--   * timeout/error responses land in watcher_runtime.last_error (+timestamp)
--   * the SFPUC source URL lives in watcher_config ('sfpuc_url'), so pointing
--     the fetch at a relay — or a re-test after re-homing — is a config UPDATE,
--     not a migration.

alter table public.watcher_runtime
  add column if not exists last_error text,
  add column if not exists last_error_at timestamptz;

insert into public.watcher_config (key, value)
values ('sfpuc_url', 'https://infrastructure.sfwater.org/lims.asmx/getBeaches')
on conflict (key) do nothing;

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

  -- 1. harvest the previous request's response (guarded; errors are RECORDED)
  begin
    select r.status_code, r.content, r.timed_out, r.error_msg into resp
      from net._http_response r
      join watcher_runtime rt on r.id = rt.last_request_id
     where rt.id = 1;

    if resp.status_code = 200 and resp.content is not null then
      -- works for both the XML-wrapped SFPUC payload and a JSON wrapper that
      -- carries the station array (e.g. a relay's {"rows": [...]}): keep the
      -- outermost [...] slice.
      raw := substring(resp.content from position('[' in resp.content));
      raw := left(raw, length(raw) - position(']' in reverse(raw)) + 1);
      payload := raw::jsonb;
      if jsonb_array_length(payload) > 0 then
        summary := bwtf_process_payload(payload);
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

  -- 2. dead-man's switch: ping ONLY after real, fresh data was processed
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

  -- 3. issue the next fetch from the CONFIGURED source (bare: no custom UA)
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
