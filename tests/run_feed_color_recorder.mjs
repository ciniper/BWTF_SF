// Local-only PostgreSQL regression harness. No credentials or network clients.
// Usage: PGLITE_MODULE=/absolute/path/to/@electric-sql/pglite/dist/index.js node tests/run_feed_color_recorder.mjs
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const moduleName=process.env.PGLITE_MODULE;
if(!moduleName) throw new Error('Set PGLITE_MODULE to a local @electric-sql/pglite module. Never run these fixtures on a hosted database.');
const {PGlite}=await import(pathToFileURL(moduleName).href);
const db=new PGlite();
const original=(await fs.readFile('db/migrations/013_forecast_cadence_and_error_log.sql','utf8')).match(/create or replace function public\.bwtf_shadow_tick\(\)[\s\S]*?end \$\$;/)[0];
await db.exec(`
create role anon;create role authenticated;create role service_role;
create schema net;
create table net._http_response(id bigint,status_code integer,content text,timed_out boolean,error_msg text,created timestamptz);
create table public.watcher_config(key text primary key,value text);
insert into watcher_config values('mode','live'),('sfpuc_url','fixture://feed'),('healthchecks_ping_url','fixture://health');
create table public.watcher_runtime(id integer primary key,last_request_id bigint,requested_at timestamptz,last_processed_at timestamptz,last_fetch_status integer,last_summary jsonb,last_error text,last_error_at timestamptz);
insert into watcher_runtime(id,last_request_id) values(1,42);
create table public.test_effects(kind text,payload jsonb);
create table public.watcher_errors(kind text,message text);
create function public.bwtf_process_payload(p jsonb,m text) returns jsonb language plpgsql as $$ begin insert into test_effects values('alerts',p);return '{"transitions":1}'::jsonb;end $$;
create function public.bwtf_record_feed_day(p jsonb) returns integer language plpgsql as $$ begin insert into test_effects values('daily',p);return 1;end $$;
create function public.bwtf_forecast_refresh() returns void language plpgsql as $$ begin insert into test_effects(kind) values('forecast');end $$;
create function public.bwtf_log_error(k text,m text,s integer) returns void language plpgsql as $$ begin insert into watcher_errors values(k,m);end $$;
create function net.http_get(url text,timeout_milliseconds integer) returns bigint language plpgsql as $$ begin insert into test_effects values('http',jsonb_build_object('url',url));return 43;end $$;
insert into net._http_response values(42,200,'[{"stationid":"a","stationname":"A","s_color":"W","p_color":null,"posted":null,"cso":null}]',false,null,clock_timestamp()-interval '1 minute');
`);
await db.exec(original);
const originalHash=(await db.query(`select md5(prosrc) h from pg_proc where oid='public.bwtf_shadow_tick()'::regprocedure`)).rows[0].h;
assert.equal(originalHash,'96497e15a283c0a6ec3c9686cf0feb5e');
await db.exec('begin;select public.bwtf_shadow_tick();');
const baseline=(await db.query('select * from public.test_effects')).rows;
await db.exec('rollback;');
const migration=await fs.readFile('db/migrations/20261119080653_feed_color_transitions.sql','utf8');
await db.exec(migration);
await db.exec(await fs.readFile('tests/sql/feed_color_recorder.sql','utf8'));
// Run the actual patched tick against stubs. A disabled, healthy, or failed
// recorder must leave alert/daily/forecast/HTTP effects identical to baseline.
for(const fault of ['disabled','healthy','storage','logger','missing-function']) {
 await db.exec('begin;');
 if(fault!=='disabled') await db.exec('update public.feed_color_runtime set enabled=true where id=1;');
 if(fault==='storage'||fault==='logger') await db.exec('alter table public.feed_color_runs add constraint injected_failure check(false) not valid;');
 if(fault==='logger') await db.exec(`create or replace function public.bwtf_log_error(k text,m text,s integer) returns void language plpgsql as $$ begin raise exception 'injected logging failure';end $$;`);
 if(fault==='missing-function') await db.exec('drop function public.bwtf_capture_feed_colors(jsonb,bigint,timestamptz);');
 await db.exec('select public.bwtf_shadow_tick();');
 assert.deepEqual((await db.query('select * from public.test_effects')).rows,baseline,`${fault}: existing watcher effects unchanged`);
 assert.equal((await db.query('select last_error from watcher_runtime')).rows[0].last_error,null,`${fault}: watcher health unaffected`);
 if(fault==='healthy') {
  assert.equal((await db.query('select count(*)::int n from feed_color_runs')).rows[0].n,1);
  assert.equal((await db.query('select first_observed_at=(select created from net._http_response where id=42) as correct from feed_color_runs')).rows[0].correct,true,'uses response timestamp, not callback processing time');
 }
 if(fault==='storage'||fault==='logger') {
  assert.equal((await db.query('select count(*)::int n from feed_color_runs')).rows[0].n,0,'history write rollback');
  assert.ok((await db.query('select last_error from feed_color_runtime')).rows[0].last_error,'recorder error retained');
 }
 await db.exec('rollback;');
}
await db.exec(await fs.readFile('db/scripts/disable_feed_color_recorder.sql','utf8'));
assert.equal((await db.query(`select md5(prosrc) h from pg_proc where oid='public.bwtf_shadow_tick()'::regprocedure`)).rows[0].h,originalHash,'rollback restores original tick exactly');
await db.close();
// A changed tick must abort installation instead of replacing newer behavior.
const drift=new PGlite();
await drift.exec(`create role anon;create role authenticated;create role service_role;create function public.bwtf_shadow_tick() returns void language plpgsql as $$begin null;end $$;`);
await drift.exec('begin;');
let refused=false;
try{await drift.exec(migration);}catch(e){refused=String(e.message).includes('changed since');}
await drift.exec('rollback;');assert.equal(refused,true,'drift guard rejects unrelated watcher');await drift.close();
console.log('PASS: raw transitions, nulls, duplicates, missing/returning stations, gaps, malformed inputs, kill switch, privileges, actual tick failure isolation, exact rollback, migration drift guard');
