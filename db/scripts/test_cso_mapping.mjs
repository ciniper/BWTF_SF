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
// Isolated clock and fake outbound HTTP; no production connections.
await db.exec(`create function test_now() returns timestamptz language sql stable as $$
select coalesce(nullif(current_setting('bwtf.test_now',true), '')::timestamptz, now()) $$`);
await db.exec(read('20261118051745_alerts_frequency.sql').replaceAll('now()', 'test_now()'));

await db.exec(read('20261121171413_sfpuc_posted_precedence.sql'));
await db.exec(read('20261125075243_aquatic_dogpatch_cso.sql'));

const payload = (aquatic='BAY#211_SL', crane=null, hyde=null) => [
  {stationid:'4613',stationname:'Aquatic Park',cso:aquatic,s_color:'G'},
  {stationid:'4620',stationname:'Crane Cove Park',cso:crane,s_color:'G'},
  {stationid:'4614',stationname:'Hyde Street Pier',cso:hyde,s_color:'G'},
];
const tick = async rows => (await db.query('select bwtf_process_payload($1::jsonb, $2)',[JSON.stringify(rows),'live'])).rows;
const states = async () => (await db.query('select station_id,status from watcher_state_shadow order by station_id')).rows;
await db.exec(`insert into watcher_config(key,value) values('brevo_api_key','fake'),('alert_from_email','test@example.test');
update watcher_config set value='live' where key='mode';
insert into subscribers(email,station_ids) values ('aquatic@example.test',array['4613']),('crane@example.test',array['4620']);`);
await tick(payload(null)); // baseline
await tick(payload());
assert.deepEqual(await states(),[
  {station_id:'4613',status:'ok'}, {station_id:'4614',status:'ok'}, {station_id:'4620',status:'cso'},
]);
assert.equal(await scalar('select count(*) from alert_pending'),1);
assert.equal(await scalar("select transitions->0->>'station_id' from alert_pending"),'4620');
await db.exec("update alert_pending set due_at=now()-interval '1 second'");
await tick(payload());
assert.equal(await scalar('select count(*) from net.requests'),1);
const message = await scalar('select body from net.requests');
assert.match(JSON.stringify(message), /crane@example.test/);
assert.doesNotMatch(JSON.stringify(message), /aquatic@example.test/);
assert.match(message.htmlContent, /Crane Cove Park/);
// Independent native flag keeps Crane active after the carrier clears.
await tick(payload('BAY#211_SL','native'));
await tick(payload(null,'native'));
assert.equal(await scalar("select status from watcher_state_shadow where station_id='4620'"),'cso');
await tick(payload(null));
assert.equal(await scalar("select status from watcher_state_shadow where station_id='4620'"),'ok');
assert.equal(await scalar("select count(*) from alert_log where results->'transitions' @> '[{\"station_id\":\"4620\",\"from\":\"cso\",\"to\":\"ok\"}]'::jsonb"),1);
const posted=payload('BAY#211_SL',null,'BEACH STREET CSD13');
posted[0]={...posted[0],s_color:'W',posted:'BAY#211_SL',p_color:'R'};
await tick(posted);
assert.equal(await scalar("select status from watcher_state_shadow where station_id='4613'"),'posted');
assert.equal(await scalar("select status from watcher_state_shadow where station_id='4614'"),'cso');
// The source's raw JSON is unchanged; run the real daily recorder too.
const history=read('012_history_tables.sql');
await db.exec(history.slice(history.indexOf('create table if not exists public.feed_station_days'),history.indexOf('-- ── the tick:')));
await db.query('select bwtf_record_feed_day($1::jsonb)',[JSON.stringify(posted)]);
assert.equal(await scalar("select raw_last->>'cso' from feed_station_days where station_id='4613'"),'BAY#211_SL');
assert.equal(await scalar("select raw_last->>'cso' from feed_station_days where station_id='4620'"),null);
// Cross-language parity including missing rows, input order, and unknown flags.
const cases=[];
for (const a of [null,'','BAY#211_SL','other']) for (const c of [null,'','native']) {
  cases.push(payload(a,c),payload(a,c).reverse(),payload(a,c).slice(0,1),payload(a,c).slice(1));
}
const python=process.env.PYTHON || 'python3';
const expected=JSON.parse(execFileSync(python,['-c',
  'import json,sys; from shared.cso_mapping import effective_cso; print(json.dumps([[effective_cso(r,p) for r in p] for p in json.load(sys.stdin)]))'],
  {input:JSON.stringify(cases),encoding:'utf8'}));
for (let i=0;i<cases.length;i++) {
  const actual=[];
  for (const row of cases[i]) actual.push((await db.query('select bwtf_effective_cso($1::jsonb,$2::jsonb) as cso', [JSON.stringify(row),JSON.stringify(cases[i])])).rows[0].cso);
  assert.deepEqual(actual,expected[i]);
}
assert.equal(await scalar("select has_function_privilege('anon','bwtf_effective_cso(jsonb,jsonb)','EXECUTE')"),false);
console.log(`PASS: ${cases.length} Python/SQL payload cases; alert routing, clearing, postings, native overlap, and raw-history preservation`);
await db.close();
