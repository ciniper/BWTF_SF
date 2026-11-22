// Offline PostgreSQL integration: PGLITE_MODULE points at an installed PGlite dist/index.js.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {PGlite} = await import(process.env.PGLITE_MODULE ? pathToFileURL(process.env.PGLITE_MODULE).href : '@electric-sql/pglite');
const db = new PGlite();
const scalar = async (sql, args = []) => Object.values((await db.query(sql, args)).rows[0])[0];
await db.exec('create role anon; create role authenticated; create role service_role bypassrls;');
// Sentinel: this feature must not touch an existing watcher table.
await db.exec("create table public.watcher_state_shadow (status text); insert into public.watcher_state_shadow values ('unchanged');");
await db.exec(readFileSync(new URL('../migrations/20261122020135_sfpuc_email_capture.sql', import.meta.url), 'utf8'));
const message = {source_key:'a'.repeat(64), content_hash:'b'.repeat(64), source_message_id:'original@example.test',
  provider:'brevo', sender:'beachwaterquality@sfwater.org', recipient:'capture@example.test', subject:'Private original',
  body_text:'Private reader@example.test', sent_at:'2026-11-20T23:00:00Z', received_at:'2026-11-21T16:00:00Z',
  sender_matches:true, warnings:[], raw_payload:{private:'headers'}};
await db.exec('set role service_role');
const capture = async items => scalar('select public.sfpuc_capture_email_batch($1::jsonb, 0)', [JSON.stringify(items)]);
assert.deepEqual(await capture([message]), {inserted:1,duplicates:0,ignored:0});
assert.deepEqual(await capture([message]), {inserted:0,duplicates:1,ignored:0});
assert.equal(await scalar('select count(*) from public.sfpuc_email_reviewed'), 0);
const id = await scalar('select id from public.sfpuc_email_messages limit 1');
const review = async (revision, status, body='Approved text') => scalar(
  'select public.sfpuc_review_email($1::uuid,$2,$3,$4,$5,$6)',[id,revision,status,'Approved title',body,'Test reviewer']);
assert.deepEqual(await review(0,'approved'), {ok:true,revision:1});
assert.equal(await scalar('select body_text from public.sfpuc_email_reviewed'), 'Approved text');
assert.deepEqual(await review(0,'approved','Stale edit'), {ok:false});
assert.deepEqual(await review(1,'rejected'), {ok:true,revision:2});
assert.equal(await scalar('select count(*) from public.sfpuc_email_reviewed'), 0);
assert.deepEqual(await review(2,'approved','Corrected text'), {ok:true,revision:3});
assert.equal(await scalar('select body_text from public.sfpuc_email_reviewed'), 'Corrected text');
assert.equal(await scalar('select count(*) from public.sfpuc_email_reviews'), 3);
// Old retries retain original receipt and never undo a review.
await capture([{...message, received_at:'2026-12-01T16:00:00Z'}]);
assert.equal(await scalar("select received_at::text from public.sfpuc_email_messages where id=$1",[id]), '2026-11-21 16:00:00+00');
assert.equal(await scalar('select body_text from public.sfpuc_email_reviewed'), 'Corrected text');
await capture([{...message,content_hash:'c'.repeat(64),body_text:'Conflicting content'}]);
assert.equal(await scalar('select count(*) from public.sfpuc_email_inbox where has_conflict'), 2);
assert.equal(await scalar('select count(*) from public.sfpuc_email_reviewed'), 1);
// A bad item rolls back the entire batch, including the run record.
const before = await scalar('select count(*) from public.sfpuc_email_messages');
const runsBefore = await scalar('select count(*) from public.sfpuc_email_ingest_runs');
await assert.rejects(capture([{...message,source_key:'d'.repeat(64)}, {...message,subject:null}]));
assert.equal(await scalar('select count(*) from public.sfpuc_email_messages'), before);
assert.equal(await scalar('select count(*) from public.sfpuc_email_ingest_runs'), runsBefore);
await assert.rejects(scalar('select public.sfpuc_capture_email_batch(null, 0)'));
await assert.rejects(db.exec("update public.sfpuc_email_messages set subject='tampered'"));
await assert.rejects(db.exec('delete from public.sfpuc_email_reviews'));
for (const role of ['anon','authenticated']) {
  await db.exec(`reset role; set role ${role}`);
  for (const table of ['sfpuc_email_messages','sfpuc_email_reviews','sfpuc_email_ingest_runs','sfpuc_email_reviewed','sfpuc_email_inbox']) {
    await assert.rejects(db.exec(`select * from public.${table}`));
  }
  await assert.rejects(capture([message]));
  await assert.rejects(review(3,'approved'));
}
await db.exec('reset role');
assert.equal(await scalar('select status from watcher_state_shadow'), 'unchanged');
assert.equal(await scalar("select count(*) from pg_class where relname in ('sfpuc_email_messages','sfpuc_email_reviews','sfpuc_email_ingest_runs') and relrowsecurity"),3);
await db.close();
console.log('PASS: email SQL ingestion, duplicate/conflict handling, atomic batches, review revisions, withdrawal, grants, RLS, and watcher isolation');
