// Disposable PostgreSQL only. HTTP and cron are stubs; never connects to hosted services.
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
if(!process.env.PGLITE_MODULE)throw Error('Set PGLITE_MODULE to a local @electric-sql/pglite/dist/index.js');
const {PGlite}=await import(pathToFileURL(process.env.PGLITE_MODULE).href);
const db=new PGlite();
const query=async sql=>(await db.query(sql)).rows;
await db.exec(`
create role anon;create role authenticated;create role service_role;
create schema net;create schema cron;
create table net._http_response(id bigint,status_code integer,content text,timed_out boolean,error_msg text,created timestamptz);
create table public.test_requests(id bigint generated always as identity,url text,timeout_ms integer);
create table cron.job(jobid bigint generated always as identity,jobname text,schedule text,command text,active boolean default true);
create function cron.schedule(n text,s text,c text) returns bigint language plpgsql as $$declare j bigint;begin insert into cron.job(jobname,schedule,command)values(n,s,c)returning jobid into j;return j;end $$;
create function cron.alter_job(j bigint,active boolean) returns void language sql as $$update cron.job set active=$2 where jobid=$1$$;
create function net.http_get(url text,timeout_milliseconds integer) returns bigint language plpgsql as $$declare r bigint;begin insert into public.test_requests(url,timeout_ms)values(url,timeout_milliseconds)returning id into r;return r;end $$;
-- Sentinel existing watcher and job: migration/collector/rollback must not touch either.
create function public.bwtf_shadow_tick() returns void language plpgsql as $$begin null;end $$;
insert into cron.job(jobname,schedule,command)values('existing-alert-watcher','* * * * *','select public.bwtf_shadow_tick()');
create table public.watcher_runtime(id integer,status text);
insert into public.watcher_runtime values(1,'unchanged');
create function public.test_capture_feed_colors(p jsonb,r bigint,t timestamptz) returns jsonb language plpgsql as $$begin
 perform public.bwtf_record_feed_colors(p,r,t);return '{}';
 exception when others then update public.feed_color_runtime set last_error=sqlerrm,consecutive_failures=consecutive_failures+1 where id=1;return jsonb_build_object('error',sqlerrm);end $$;
`);
const watcherBefore=await query(`select pg_get_functiondef('public.bwtf_shadow_tick()'::regprocedure) as definition`);
const jobBefore=await query(`select * from cron.job where jobname='existing-alert-watcher'`);
const migration=await fs.readFile('db/migrations/20261119080653_feed_color_transitions.sql','utf8');
assert.ok(!/bwtf_shadow_tick|bwtf_process_payload|bwtf_log_error|watcher_runtime|watcher_config/.test(migration),'no watcher coupling');
await db.exec(migration);
assert.equal((await query(`select active from cron.job where jobname='bwtf-feed-color-collector'`))[0].active,false);
await db.exec('select public.bwtf_feed_color_tick();');
assert.equal((await query('select count(*)::int n from test_requests'))[0].n,0,'disabled collector sends nothing');
await db.exec(await fs.readFile('tests/sql/feed_color_recorder.sql','utf8'));
const payload=JSON.stringify([{stationid:'a',stationname:'A',s_color:'W',p_color:null,posted:null,cso:null}]);
await db.exec('update feed_color_runtime set enabled=true where id=1;select public.bwtf_feed_color_tick();');
assert.equal((await query('select count(*)::int n from test_requests'))[0].n,1);
assert.equal((await query('select url from test_requests'))[0].url,'https://infrastructure.sfwater.org/lims.asmx/getBeaches');
await db.exec('select public.bwtf_feed_color_tick();');
assert.equal((await query('select count(*)::int n from test_requests'))[0].n,1,'pending request is not duplicated');
await db.query('insert into net._http_response values(1,200,$1,false,null,clock_timestamp())',[`<string>${payload}</string>`]);
await db.exec('select public.bwtf_feed_color_tick();');
assert.equal((await query('select count(*)::int n from feed_color_reads'))[0].n,1,'exact successful read retained');
assert.equal((await query('select count(*)::int n from feed_color_runs'))[0].n,1);
assert.equal((await query('select request_id from feed_color_runtime'))[0].request_id,null);
await db.exec("update feed_color_runtime set last_attempt_at=clock_timestamp()-interval '29 minutes';select public.bwtf_feed_color_tick();");
assert.equal((await query('select count(*)::int n from test_requests'))[0].n,1,'29 minutes is too soon');
await db.exec("update feed_color_runtime set last_attempt_at=clock_timestamp()-interval '30 minutes';select public.bwtf_feed_color_tick();");
assert.equal((await query('select count(*)::int n from test_requests'))[0].n,2,'30 minutes queues next poll');
await db.exec("insert into net._http_response values(2,503,'offline',false,null,clock_timestamp());select public.bwtf_feed_color_tick();select public.bwtf_feed_color_tick();");
assert.equal((await query('select count(*)::int n from test_requests'))[0].n,2,'HTTP failure does not retry early');
assert.ok((await query('select last_error from feed_color_runtime'))[0].last_error);
// Missing response times out, without a replacement request before the next half-hour.
await db.exec("update feed_color_runtime set request_id=999,requested_at=clock_timestamp()-interval '6 minutes';select public.bwtf_feed_color_tick();");
assert.equal((await query('select request_id from feed_color_runtime'))[0].request_id,null);
assert.equal((await query('select count(*)::int n from test_requests'))[0].n,2);
for(const fault of ['malformed','stale','storage']){
 await db.exec('begin;');
 await db.exec("update feed_color_runtime set request_id=50,requested_at=clock_timestamp(),last_attempt_at=clock_timestamp();");
 if(fault==='storage') await db.exec('alter table feed_color_runs add constraint injected_failure check(false) not valid;');
 await db.query("insert into net._http_response values(50,200,$1,false,null,clock_timestamp()-$2::interval)",[fault==='malformed'?'not-json':payload,fault==='stale'?'20 minutes':'0 seconds']);
 await db.exec('select public.bwtf_feed_color_tick();');
 assert.ok((await query('select last_error from feed_color_runtime'))[0].last_error,`${fault} error reported`);
 assert.equal((await query('select count(*)::int n from feed_color_reads'))[0].n,1,`${fault}: no partial read persisted`);
 assert.equal((await query('select request_id from feed_color_runtime'))[0].request_id,null);
 await db.exec('rollback;');
}
// Queue failure is also throttled, rather than retried every harvest tick.
await db.exec(`begin;create or replace function net.http_get(url text,timeout_milliseconds integer) returns bigint language plpgsql as $$begin raise exception 'queue unavailable';end $$;
update feed_color_runtime set last_attempt_at=null;select public.bwtf_feed_color_tick();`);
assert.ok((await query('select last_attempt_at from feed_color_runtime'))[0].last_attempt_at);
const failures=(await query('select consecutive_failures from feed_color_runtime'))[0].consecutive_failures;
await db.exec('select public.bwtf_feed_color_tick();');
assert.equal((await query('select consecutive_failures from feed_color_runtime'))[0].consecutive_failures,failures);
await db.exec('rollback;');
await db.exec(await fs.readFile('db/scripts/disable_feed_color_recorder.sql','utf8'));
assert.equal((await query('select enabled from feed_color_runtime'))[0].enabled,false);
assert.equal((await query(`select active from cron.job where jobname='bwtf-feed-color-collector'`))[0].active,false);
assert.equal((await query('select count(*)::int n from feed_color_reads'))[0].n,1,'rollback preserves data');
assert.deepEqual(await query(`select pg_get_functiondef('public.bwtf_shadow_tick()'::regprocedure) as definition`),watcherBefore);
assert.deepEqual(await query(`select * from cron.job where jobname='existing-alert-watcher'`),jobBefore);
assert.deepEqual(await query('select * from watcher_runtime'),[{id:1,status:'unchanged'}]);
await db.close();
console.log('PASS: independent collector; 30-minute request throttle; local harvest; raw transitions and checkpoints; failures/timeouts; permissions; disabled installation; preserving rollback; original watcher and schedule untouched');
