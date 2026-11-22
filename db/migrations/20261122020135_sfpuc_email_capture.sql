-- Independent inbound email archive. No watcher, alert, sample, or forecast changes.
begin;

create table public.sfpuc_email_messages (
  id uuid primary key default gen_random_uuid(),
  source_key text not null check (length(source_key) = 64),
  content_hash text not null check (length(content_hash) = 64),
  source_message_id text,
  provider text not null check (provider in ('brevo', 'eml_import')),
  sender text not null,
  recipient text not null,
  subject text not null check (length(subject) <= 1000),
  body_text text not null check (length(body_text) <= 100000),
  sent_at timestamptz,
  received_at timestamptz not null,
  sender_matches boolean not null,
  warnings jsonb not null default '[]'::jsonb check (jsonb_typeof(warnings) = 'array'),
  raw_payload jsonb not null check (jsonb_typeof(raw_payload) = 'object'),
  unique (source_key, content_hash)
);
create index sfpuc_email_messages_received_idx on public.sfpuc_email_messages(received_at desc, id desc);

create table public.sfpuc_email_reviews (
  message_id uuid not null references public.sfpuc_email_messages(id),
  revision integer not null check (revision > 0),
  status text not null check (status in ('approved', 'rejected')),
  title text not null check (length(title) <= 300),
  body_text text not null check (length(body_text) <= 30000),
  reviewed_by text not null check (length(btrim(reviewed_by)) between 1 and 100),
  reviewed_at timestamptz not null default now(),
  primary key (message_id, revision),
  check (status <> 'approved' or (length(btrim(title)) > 0 and length(btrim(body_text)) > 0))
);

create table public.sfpuc_email_ingest_runs (
  id uuid primary key default gen_random_uuid(),
  received_at timestamptz not null default now(),
  inserted_count integer not null check (inserted_count >= 0),
  duplicate_count integer not null check (duplicate_count >= 0),
  ignored_count integer not null check (ignored_count >= 0)
);
create index sfpuc_email_ingest_runs_received_idx on public.sfpuc_email_ingest_runs(received_at desc);

alter table public.sfpuc_email_messages enable row level security;
alter table public.sfpuc_email_reviews enable row level security;
alter table public.sfpuc_email_ingest_runs enable row level security;
revoke all on public.sfpuc_email_messages, public.sfpuc_email_reviews, public.sfpuc_email_ingest_runs
  from public, anon, authenticated, service_role;
grant select, insert on public.sfpuc_email_messages, public.sfpuc_email_reviews, public.sfpuc_email_ingest_runs
  to service_role;

create view public.sfpuc_email_inbox with (security_invoker = true) as
select m.id, m.subject, m.sender, m.sent_at, m.received_at, m.sender_matches,
       coalesce(r.revision, 0) as revision,
       coalesce(r.status, case when m.sender_matches then 'pending' else 'quarantined' end) as status,
       exists (select 1 from public.sfpuc_email_messages other
               where other.source_key = m.source_key and other.content_hash <> m.content_hash) as has_conflict
from public.sfpuc_email_messages m
left join lateral (select revision, status from public.sfpuc_email_reviews
                   where message_id = m.id order by revision desc limit 1) r on true;

create view public.sfpuc_email_reviewed with (security_invoker = true) as
select m.id, r.title, r.body_text, m.sent_at, m.received_at, r.reviewed_at,
       coalesce(m.sent_at, m.received_at) as sort_at
from public.sfpuc_email_messages m
join lateral (select title, body_text, status, reviewed_at from public.sfpuc_email_reviews
              where message_id = m.id order by revision desc limit 1) r on r.status = 'approved';

revoke all on public.sfpuc_email_inbox, public.sfpuc_email_reviewed from public, anon, authenticated, service_role;
grant select on public.sfpuc_email_inbox, public.sfpuc_email_reviewed to service_role;

create function public.sfpuc_capture_email_batch(messages jsonb, ignored_count integer default 0)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare inserted integer;
begin
  if messages is null or jsonb_typeof(messages) <> 'array' or jsonb_array_length(messages) > 20
     or ignored_count is null or ignored_count < 0 or ignored_count > 20 then
    raise exception 'Invalid email batch';
  end if;
  insert into public.sfpuc_email_messages
    (source_key, content_hash, source_message_id, provider, sender, recipient, subject,
     body_text, sent_at, received_at, sender_matches, warnings, raw_payload)
  select source_key, content_hash, source_message_id, provider, sender, recipient, subject,
         body_text, sent_at, received_at, sender_matches, warnings, raw_payload
  from jsonb_to_recordset(messages) as x(
    source_key text, content_hash text, source_message_id text, provider text, sender text,
    recipient text, subject text, body_text text, sent_at timestamptz, received_at timestamptz,
    sender_matches boolean, warnings jsonb, raw_payload jsonb)
  on conflict (source_key, content_hash) do nothing;
  get diagnostics inserted = row_count;
  insert into public.sfpuc_email_ingest_runs(inserted_count, duplicate_count, ignored_count)
    values (inserted, jsonb_array_length(messages) - inserted, ignored_count);
  return jsonb_build_object('inserted', inserted, 'duplicates', jsonb_array_length(messages) - inserted,
                           'ignored', ignored_count);
end;
$$;

create function public.sfpuc_review_email(message uuid, expected_revision integer, decision text,
                                         reviewed_title text, reviewed_text text, reviewer text)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare current_revision integer;
begin
  -- Serialize reviews for this message; reject stale browser forms.
  perform pg_advisory_xact_lock(hashtextextended(message::text, 0));
  if not exists (select 1 from public.sfpuc_email_messages where id = message) then
    return jsonb_build_object('ok', false);
  end if;
  select coalesce(max(revision), 0) into current_revision
    from public.sfpuc_email_reviews where message_id = message;
  if expected_revision is null or expected_revision <> current_revision then
    return jsonb_build_object('ok', false);
  end if;
  insert into public.sfpuc_email_reviews(message_id, revision, status, title, body_text, reviewed_by)
    values (message, current_revision + 1, decision, reviewed_title, reviewed_text, reviewer);
  return jsonb_build_object('ok', true, 'revision', current_revision + 1);
end;
$$;

revoke all on function public.sfpuc_capture_email_batch(jsonb, integer),
                       public.sfpuc_review_email(uuid, integer, text, text, text, text)
  from public, anon, authenticated, service_role;
grant execute on function public.sfpuc_capture_email_batch(jsonb, integer),
                          public.sfpuc_review_email(uuid, integer, text, text, text, text)
  to service_role;

comment on table public.sfpuc_email_messages is 'Private immutable inbound emails. Never used for alerts or beach-status calculation.';
comment on table public.sfpuc_email_reviews is 'Append-only coordinator decisions. The latest approved revision appears only in the private Admin preview.';
commit;
