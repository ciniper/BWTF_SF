// Offline PostgreSQL integration test. No credentials or outbound HTTP.
// npm install --prefix /tmp/bwtf-alerts-verify @electric-sql/pglite@0.5.16
// PGLITE_MODULE=/absolute/path/to/node_modules/@electric-sql/pglite/dist/index.js node db/scripts/test_alerts_frequency.mjs
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
const {PGlite} = await import(process.env.PGLITE_MODULE || '@electric-sql/pglite');
const db = new PGlite();
const read = name => fs.readFileSync(`db/migrations/${name}`, 'utf8');
const scalar = async sql => Object.values((await db.query(sql)).rows[0])[0];
await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;`);
await db.exec(read('001_phase1_subscribers_state_log.sql'));
let two=read('002_phase2_shadow_watcher.sql');
await db.exec(two.slice(two.indexOf('create table public.watcher_config'),two.indexOf('-- ── the testable core')));
await db.exec(`alter table subscribers add unsubscribe_token uuid default gen_random_uuid();
alter table watcher_state_shadow add sim_active boolean default false, add sim_kind text;
alter table simulated_cso add kind text default 'cso', add recipients text[];
create schema net;
create table net.requests(id bigserial primary key, body jsonb);
create function net.http_post(url text, body jsonb, headers jsonb, timeout_milliseconds int) returns bigint language sql as $$ insert into net.requests(body) values(body) returning id $$;
create function bwtf_sms_gateway(text,text) returns text language sql as $$ select case when $1<>'' then $1||'@example.test' end $$;`);
let fifteen=read('015_alert_deliveries.sql');
await db.exec(fifteen.slice(fifteen.indexOf('create table if not exists public.alert_deliveries'),fifteen.indexOf('create or replace function public.bwtf_dispatch_live')));
let nine=read('009_simulation_provenance.sql');
await db.exec(nine.slice(nine.indexOf('create or replace function public.bwtf_log_downgrades'),nine.indexOf('create or replace function public.bwtf_process_payload')));
await db.exec(read('021_live_status_to_today.sql'));
// Inject a clock only into the test copy; production SQL uses PostgreSQL now().
await db.exec(`create function test_now() returns timestamptz language sql stable as $$
select coalesce(nullif(current_setting('bwtf.test_now',true), '')::timestamptz, now()) $$`);
await db.exec(read('20261118051745_alerts_frequency.sql').replaceAll('now()', 'test_now()'));
await db.exec(read('20261121171413_sfpuc_posted_precedence.sql'));
await db.exec(read('20261125075243_aquatic_dogpatch_cso.sql').replaceAll('now()', 'test_now()'));
await db.exec(`insert into watcher_config(key,value) values('brevo_api_key','fake'),('alert_from_email','test@example.test'); update watcher_config set value='live' where key='mode';
insert into subscribers(email,station_ids,region_zone) values('a@example.test',array['4608','4609','4610'],'Baker & China Beach');`);
const payload = states => states.map((status,i)=>({stationid:String(4608+i),stationname:['Baker West','Baker East','Baker Lobos'][i],cso:status==='cso'?'yes':null,s_color:status==='posted'?'R':'G'}));
const tick = async states => (await db.query(`select bwtf_process_payload($1,'live')`,[JSON.stringify(payload(states))])).rows;
await tick(['ok','posted','ok']); // baseline, including context station
await tick(['posted','posted','ok']);
assert.equal(await scalar('select count(*) from net.requests'),0);
assert.equal(await scalar('select count(*) from alert_pending'),1);
const due=await scalar('select due_at::text from alert_pending');
await tick(['cso','posted','ok']);
assert.equal(await scalar('select due_at::text from alert_pending'),due);
assert.equal(await scalar("select transitions->0->>'to' from alert_pending"),'cso');
await db.exec("update alert_pending set due_at=now()-interval '1 second'");
await tick(['cso','posted','ok']);
assert.equal(await scalar('select count(*) from net.requests'),1);
assert.equal(await scalar('select count(*) from alert_pending'),0);
const body=await scalar('select body from net.requests');
assert.match(body.htmlContent,/Still in effect — Baker &amp; China Beach/);
assert.match(body.htmlContent,/Baker East/);
assert.match(body.subject,/sewage discharge/);
await tick(['ok','posted','ok']); await tick(['cso','posted','ok']);
assert.equal(await scalar('select count(*) from alert_pending'),0); // repeat still logged
assert.equal(await scalar("select count(*) from alert_log where event_type='cso' and source='pg_live'"),2);
// Mimic a discharge last seen yesterday, then a fresh event today.
await tick(['ok','posted','ok']);
await db.exec("update alert_status_day set day=day-1");
await tick(['cso','posted','ok']);
assert.equal(await scalar('select count(*) from alert_pending'),1);
// Unsubscribe during hold: no send. Resume subscription for subsequent cases.
await db.exec("update subscribers set active=false; update alert_pending set due_at=now()-interval '1 second'");
await tick(['cso','posted','ok']);
assert.equal(await scalar('select count(*) from net.requests'),1);
assert.equal(await scalar('select count(*) from alert_pending'),0);
await db.exec("update subscribers set active=true");
await tick(['cso','posted','posted']);
await db.exec("update alert_pending set due_at=now()-interval '1 second'; delete from watcher_config where key='brevo_api_key'");
await tick(['cso','posted','posted']);
assert.equal(await scalar('select count(*) from alert_pending'),1);
await db.exec("insert into watcher_config values('brevo_api_key','fake',now())");
await tick(['cso','posted','posted']); await tick(['cso','posted','posted']);
assert.equal(await scalar('select count(*) from net.requests'),2); // no duplicate retry
// Posting flicker after delivery is suppressed, with no new batch; escalation still alerts.
await tick(['cso','posted','ok']); await tick(['cso','posted','posted']);
assert.equal(await scalar('select count(*) from alert_pending'),0);
await tick(['cso','posted','cso']);
assert.equal(await scalar("select transitions->0->>'to' from alert_pending"),'cso');
await db.exec('delete from alert_pending');
// A posting that returns on the next Pacific day is eligible again.
await tick(['cso','posted','ok']);
await db.exec("update alert_status_day set day=day-1 where station_id='4610'");
await tick(['cso','posted','posted']);
assert.equal(await scalar('select count(*) from alert_pending'),1);
await db.exec('delete from alert_pending');
// A change exactly at the deadline joins; a later change opens a fresh batch.
await tick(['ok','ok','ok']);
await db.exec('delete from alert_status_day'); // independent window case
await tick(['posted','ok','ok']);
await db.exec('begin; update alert_pending set due_at=now();');
await tick(['posted','posted','ok']);
await db.exec('commit');
assert.equal(await scalar('select count(*) from net.requests'),3);
assert.match(await scalar("select body->>'subject' from net.requests order by id desc limit 1"),/2 beaches/);
await tick(['ok','ok','ok']); await db.exec('delete from alert_status_day'); await tick(['posted','ok','ok']);
await db.exec("update alert_pending set due_at=now()-interval '1 second'");
await tick(['posted','posted','ok']);
assert.equal(await scalar('select count(*) from net.requests'),4);
assert.equal(await scalar('select count(*) from alert_pending'),1);
// Changing sites during the hold removes an obsolete alert.
await db.exec("update subscribers set station_ids=array['4608']; update alert_pending set due_at=now()-interval '1 second'");
await tick(['posted','posted','ok']);
assert.equal(await scalar('select count(*) from net.requests'),4);
assert.equal(await scalar('select count(*) from alert_pending'),0);
// Targeted test events still dispatch immediately and cannot enter real queues.
await db.exec("insert into simulated_cso(station_id,kind,recipients) values('4608','cso',array['nobody@example.test'])");
await tick(['posted','posted','ok']);
assert.equal(await scalar('select count(*) from net.requests'),4);
await db.exec('delete from simulated_cso'); await tick(['posted','posted','ok']);
await db.exec("insert into simulated_cso(station_id,kind,recipients) values('4608','cso',array['a@example.test'])");
await tick(['posted','posted','ok']);
assert.equal(await scalar('select count(*) from net.requests'),5);
assert.match(await scalar("select body->>'subject' from net.requests order by id desc limit 1"),/^TEST /);
assert.equal(await scalar('select count(*) from alert_pending'),0);
await db.exec('delete from simulated_cso');
// Carryover CSO is remembered on its first clear tick today (not last email time).
await db.exec("update watcher_state_shadow set status='cso' where station_id='4608'; update alert_status_day set day=day-1");
await tick(['ok','posted','ok']); await tick(['cso','posted','ok']);
assert.equal(await scalar('select count(*) from alert_pending'),0);
// Off mode does not flush queued alerts, and shadow processing never queues.
await tick(['ok','ok','ok']); await db.exec('delete from alert_status_day'); await tick(['posted','ok','ok']);
await db.exec("update watcher_config set value='off' where key='mode'; update alert_pending set due_at=now()-interval '1 second'");
await db.exec('select bwtf_flush_alerts(now())');
assert.equal(await scalar('select count(*) from net.requests'),5);
await db.exec('delete from alert_pending');
await db.query("select bwtf_process_payload($1,'shadow')",[JSON.stringify(payload(['posted','posted','posted']))]);
assert.equal(await scalar('select count(*) from alert_pending'),0);
// SQL/Python running section parity, with HTML-sensitive names and all-zone heading.
for (const zone of [null,'Baker & China Beach']) {
 const still=[{station_name:'A < B & "C"',to:'cso'},{station_name:'Other',to:'posted'}];
 const msg={html_body:'<p style="margin:14px 0 0;font-size:13.5px;',text_body:'\n\nFrom SFPUC:',sms_text:'. Live status:'};
 const sql=(await db.query('select bwtf_still_in_effect($1,$2,$3) as msg',[JSON.stringify(msg),JSON.stringify(still),zone])).rows[0].msg;
 const py=JSON.parse(execFileSync(process.env.PYTHON || 'python3',['-c',`import json,sys; from features.alerts.render import with_still_in_effect; m,s,z=json.load(sys.stdin); print(json.dumps(with_still_in_effect(m,[(t['station_name'],t['to']) for t in s],z)))`],{input:JSON.stringify([msg,still,zone]),encoding:'utf8'}));
 assert.deepEqual(sql,py);
}
assert.equal(await scalar("select has_function_privilege('anon','bwtf_flush_alerts(timestamptz)','execute')"),false);
assert.equal(await scalar("select has_table_privilege('authenticated','alert_pending','select')"),false);
// Replay the same real storm fixture as the Python simulator through SQL.
const changes=JSON.parse(execFileSync(process.env.PYTHON || 'python3',['-c',`import json; from features.alerts.email_sim import changes_from_windows; from pathlib import Path; c=changes_from_windows(json.loads(Path('tests/fixtures/email_sim_windows_2026-11-17.json').read_text())['stations']); print(json.dumps([{'at':x.at.isoformat(),'sid':x.station_id,'to':x.to} for x in c]))`],{encoding:'utf8'}));
await db.exec("truncate alert_pending, alert_status_day, watcher_state_shadow, net.requests; delete from subscribers; update watcher_config set value='live' where key='mode'");
await db.exec("insert into subscribers(email,station_ids) values('all@example.test',array(select generate_series(4601,4620)::text))");
const state=Object.fromEntries(Array.from({length:20},(_,i)=>[String(4601+i),'ok']));
const feed=()=>Object.entries(state).map(([sid,status])=>({stationid:sid,stationname:sid,cso:status==='cso'?'yes':null,s_color:status==='posted'?'R':'G'}));
const advance=async ms=>{
 await db.query("select set_config('bwtf.test_now',$1,false)",[new Date(ms).toISOString()]);
 await db.query("select bwtf_process_payload($1,'live')",[JSON.stringify(feed())]);
};
const times=[...new Set(changes.flatMap(c=>[Date.parse(c.at),Date.parse(c.at)+15*60000]))].sort((a,b)=>a-b);
await advance(times[0]-60000);
let inAudit=false;
for(const ms of times){
 if(!inAudit && ms>=Date.parse('2026-11-13T00:00:00-08:00')) { await db.exec('truncate net.requests'); inAudit=true; }
 for(const c of changes) if(Date.parse(c.at)===ms) state[c.sid]=c.to; await advance(ms);}
assert.equal(await scalar('select count(*) from net.requests'),5);
console.log('PASS: SQL migration, batching, fixed deadline, suppression, next-day eligibility, running context, unsubscribe, credential retry, no duplicate flush, rendering parity, permissions');
await db.close();
