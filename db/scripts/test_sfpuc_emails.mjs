// Offline PostgreSQL integration: PGLITE_MODULE points at an installed PGlite dist/index.js.
// Updated for the visibility migration; do not run without test authorization.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {PGlite} = await import(process.env.PGLITE_MODULE ? pathToFileURL(process.env.PGLITE_MODULE).href : '@electric-sql/pglite');
const db = new PGlite();
const scalar = async (sql, args = []) => Object.values((await db.query(sql, args)).rows[0])[0];
await db.exec('create role anon; create role authenticated; create role service_role bypassrls;');
// Local sentinel only; no production connection or existing subscriber data.
await db.exec("create table public.watcher_state_shadow (status text); insert into public.watcher_state_shadow values ('unchanged');");
await db.exec(readFileSync(new URL('../migrations/20261122020135_sfpuc_email_capture.sql', import.meta.url), 'utf8'));
const message = {source_key:'a'.repeat(64), content_hash:'b'.repeat(64), source_message_id:'original@example.test',
  provider:'brevo', sender:'beachwaterquality@sfwater.org', recipient:'capture@example.test', subject:'SF BEACH POSTED',
  body_text:'Original bulletin', sent_at:'2026-11-20T23:00:00Z', received_at:'2026-11-21T16:00:00Z',
  sender_matches:true, warnings:[], raw_payload:{private:'headers',RawHtmlBody:'<p>Original bulletin</p>'}};
await db.exec('set role service_role');
const capture = async items => scalar('select public.sfpuc_capture_email_batch($1::jsonb, 0)', [JSON.stringify(items)]);
assert.deepEqual(await capture([message]), {inserted:1,duplicates:0,ignored:0});
assert.deepEqual(await capture([message]), {inserted:0,duplicates:1,ignored:0});
const id = await scalar('select id from public.sfpuc_email_messages limit 1');
// Seed legacy edits and a hidden decision to verify the migration, not the old UI.
const review = async (mid, revision, status) => scalar(
  'select public.sfpuc_review_email($1::uuid,$2,$3,$4,$5,$6)',[mid,revision,status,'Edited title','Edited text','Test reviewer']);
await review(id, 0, 'approved');
await review(id, 1, 'rejected');
await capture([{...message,source_key:'c'.repeat(64),subject:'SF BEACH DE-POSTED'},
               {...message,source_key:'d'.repeat(64),subject:'SF BEACH UPDATE'}]);
const approvedId = await scalar("select id from public.sfpuc_email_messages where source_key=$1", ['c'.repeat(64)]);
await review(approvedId, 0, 'approved');
const originals = (await db.query('select * from public.sfpuc_email_messages order by id')).rows;
assert.equal(originals.length, 3);
const runsBeforeMigration = await scalar('select count(*) from public.sfpuc_email_ingest_runs');
await db.exec('reset role');
await db.exec(readFileSync(new URL('../migrations/20261125074617_sfpuc_email_visibility.sql', import.meta.url), 'utf8'));
await db.exec('set role service_role');
const after = (await db.query('select * from public.sfpuc_email_messages order by id')).rows;
assert.deepEqual(after.map(({visibility_hidden,visibility_revision,...original}) => original), originals);
assert.equal(await scalar('select count(*) from public.sfpuc_email_ingest_runs'), runsBeforeMigration);
assert.equal(await scalar('select status from public.sfpuc_email_inbox where id=$1', [id]), 'rejected');
assert.equal(await scalar('select revision from public.sfpuc_email_inbox where id=$1', [id]), 2);
assert.equal(await scalar("select count(*) from public.sfpuc_email_inbox where status='captured'"), 2);
assert.equal(await scalar("select to_regclass('public.sfpuc_email_reviews')"), null);
assert.equal(await scalar("select to_regclass('public.sfpuc_email_reviewed')"), null);
assert.equal(await scalar("select to_regprocedure('public.sfpuc_review_email(uuid,integer,text,text,text,text)')"), null);
const visibility = async (revision, hidden) => scalar(
  'select public.sfpuc_set_email_visibility($1::uuid,$2,$3)', [id,revision,hidden]);
assert.deepEqual(await visibility(1, false), {ok:false});
assert.deepEqual(await visibility(2, false), {ok:true,revision:3});
assert.deepEqual(await visibility(3, true), {ok:true,revision:4});
assert.deepEqual(await visibility(3, false), {ok:false});
assert.deepEqual(await visibility(4, null), {ok:false});
assert.deepEqual(await visibility(-1, false), {ok:false});
assert.deepEqual(await scalar('select public.sfpuc_set_email_visibility($1::uuid,0,false)', ['00000000-0000-0000-0000-000000000000']), {ok:false});
// Retried delivery retains original receipt/content and does not undo Hide.
await capture([{...message,received_at:'2026-12-01T16:00:00Z'}]);
assert.equal(await scalar('select received_at::text from public.sfpuc_email_messages where id=$1',[id]), '2026-11-21 16:00:00+00');
assert.equal(await scalar('select visibility_hidden from public.sfpuc_email_messages where id=$1',[id]), true);
assert.equal(await scalar('select body_text from public.sfpuc_email_messages where id=$1',[id]), message.body_text);
assert.deepEqual(await visibility(4, false), {ok:true,revision:5});
await capture([{...message,content_hash:'e'.repeat(64),body_text:'Conflicting content'}]);
assert.equal(await scalar('select count(*) from public.sfpuc_email_inbox where has_conflict'), 2);
// A bad item rolls back the entire batch, including the run record.
const before = await scalar('select count(*) from public.sfpuc_email_messages');
const runsBefore = await scalar('select count(*) from public.sfpuc_email_ingest_runs');
await assert.rejects(capture([{...message,source_key:'f'.repeat(64)}, {...message,subject:null}]));
assert.equal(await scalar('select count(*) from public.sfpuc_email_messages'), before);
assert.equal(await scalar('select count(*) from public.sfpuc_email_ingest_runs'), runsBefore);
await assert.rejects(scalar('select public.sfpuc_capture_email_batch(null, 0)'));
await assert.rejects(db.exec("update public.sfpuc_email_messages set subject='tampered'"));
await assert.rejects(db.exec("update public.sfpuc_email_messages set raw_payload='{}'::jsonb"));
await assert.rejects(db.exec('delete from public.sfpuc_email_messages'));
for (const role of ['anon','authenticated']) {
  await db.exec(`reset role; set role ${role}`);
  for (const table of ['sfpuc_email_messages','sfpuc_email_ingest_runs','sfpuc_email_inbox']) {
    await assert.rejects(db.exec(`select * from public.${table}`));
  }
  await assert.rejects(capture([message]));
  await assert.rejects(visibility(5, true));
  await assert.rejects(db.exec('update public.sfpuc_email_messages set visibility_hidden=true'));
}
await db.exec('reset role');
assert.equal(await scalar('select status from watcher_state_shadow'), 'unchanged');
assert.equal(await scalar("select count(*) from pg_class where relname in ('sfpuc_email_messages','sfpuc_email_ingest_runs') and relrowsecurity"),2);
await db.close();
console.log('PASS: original retention, review cleanup, visibility concurrency, ingestion, grants, RLS, and isolation');
