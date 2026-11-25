-- Apply only AFTER the new app is live: older admin deployments read reviews.
-- Keep all original messages/payloads and ingest logs; discard only old edits.
begin;
set local lock_timeout = '5s';

-- Serialize the transfer with captures and legacy Hide/Restore writes.
-- A busy database aborts this migration instead of leaving a partial cleanup.
lock table public.sfpuc_email_messages, public.sfpuc_email_reviews in access exclusive mode;

alter table public.sfpuc_email_messages
  add column visibility_hidden boolean not null default false,
  add column visibility_revision integer not null default 0 check (visibility_revision >= 0);

update public.sfpuc_email_messages m
set visibility_hidden = r.status = 'rejected', visibility_revision = r.revision
from (select distinct on (message_id) message_id, status, revision
      from public.sfpuc_email_reviews order by message_id, revision desc) r
where m.id = r.message_id;

-- Keep the existing view contract so the new app can read before/after cutover.
-- 'rejected' is only the compatibility label for Hidden, not an approval flow.
create or replace view public.sfpuc_email_inbox with (security_invoker = true) as
select m.id, m.subject, m.sender, m.sent_at, m.received_at, m.sender_matches,
       m.visibility_revision as revision,
       case when m.visibility_hidden then 'rejected'
            when m.sender_matches then 'captured' else 'quarantined' end as status,
       exists (select 1 from public.sfpuc_email_messages other
               where other.source_key = m.source_key and other.content_hash <> m.content_hash) as has_conflict
from public.sfpuc_email_messages m;

-- Only visibility metadata is mutable; original email columns remain protected.
grant update (visibility_hidden, visibility_revision) on public.sfpuc_email_messages to service_role;

create function public.sfpuc_set_email_visibility(message uuid, expected_revision integer, hidden boolean)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare new_revision integer;
begin
  if expected_revision is null or expected_revision < 0 or hidden is null then
    return jsonb_build_object('ok', false);
  end if;
  -- Atomic compare-and-set rejects stale forms and concurrent decisions.
  update public.sfpuc_email_messages
  set visibility_hidden = hidden, visibility_revision = visibility_revision + 1
  where id = message and visibility_revision = expected_revision
  returning visibility_revision into new_revision;
  if not found then
    return jsonb_build_object('ok', false);
  end if;
  return jsonb_build_object('ok', true, 'revision', new_revision);
end;
$$;
revoke all on function public.sfpuc_set_email_visibility(uuid, integer, boolean)
  from public, anon, authenticated, service_role;
grant execute on function public.sfpuc_set_email_visibility(uuid, integer, boolean) to service_role;

-- No CASCADE: unknown dependencies must stop the transaction for inspection.
drop view public.sfpuc_email_reviewed;
drop function public.sfpuc_review_email(uuid, integer, text, text, text, text);
drop table public.sfpuc_email_reviews;

comment on table public.sfpuc_email_messages is
  'Private inbound originals with current visibility metadata. Original content is immutable; never used for alerts or beach-status calculation.';
comment on column public.sfpuc_email_messages.visibility_revision is
  'Optimistic concurrency counter for Hide/Restore; not edit history.';
commit;
