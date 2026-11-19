-- Approved operational rollback only. Preserve all reads and transitions.
-- Only this independent collector is disabled; no alert watcher is touched.
begin;
update public.feed_color_runtime set enabled=false,request_id=null where id=1;
do $$
declare job bigint;
begin
  select jobid into job from cron.job where jobname='bwtf-feed-color-collector';
  if job is not null then perform cron.alter_job(job,active:=false); end if;
end $$;
commit;
-- An HTTP request already queued may finish, but its response is not ingested.
