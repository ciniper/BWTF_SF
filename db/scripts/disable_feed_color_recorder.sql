-- Operational rollback. Run only when explicitly authorized for that database.
-- Preserves all color history and leaves alert behavior/schedules unchanged.
begin;
update public.feed_color_runtime set enabled=false where id=1;
-- Remove ONLY this migration's hook; refuse to overwrite later watcher edits.
do $rollback$
declare definition text; body_hash text;
begin
  select pg_get_functiondef(p.oid),md5(p.prosrc) into definition,body_hash
    from pg_proc p where p.oid='public.bwtf_shadow_tick()'::regprocedure;
  if body_hash = '96497e15a283c0a6ec3c9686cf0feb5e' then return; end if;
  if body_hash <> 'd5f1a15d6958bc199204e876b92dc201' then
    raise warning 'Watcher changed since recorder installation; recorder disabled, hook left for manual review';
    return;
  end if;
  definition := replace(definition,$new$select r.status_code, r.content, r.timed_out, r.error_msg, r.id as response_id, r.created as observed_at into resp$new$,$old$select r.status_code, r.content, r.timed_out, r.error_msg into resp$old$);
  definition := replace(definition,$hook$
        -- feed-color recorder: real payload only; never affects alert decisions.
        begin
          summary := coalesce(summary,'{}'::jsonb) || jsonb_build_object('feed_colors',
            public.bwtf_capture_feed_colors(payload,resp.response_id,resp.observed_at));
        exception when others then
          summary := coalesce(summary,'{}'::jsonb) || jsonb_build_object('feed_colors',jsonb_build_object('error',left(sqlerrm,1000)));
        end;
        -- /feed-color recorder$hook$,'');
  execute definition;
end $rollback$;
commit;
