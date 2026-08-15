-- BWTF → Supabase migration, Phase 1 (2026-08: subscribers + alert state + delivery log)
-- Run in the Supabase SQL Editor of the DEDICATED BWTF project (not Surftober's).
--
-- Access model: SERVICE ROLE ONLY. RLS is enabled with zero policies and all
-- grants are revoked from anon/authenticated, so the public API keys can read
-- nothing; the Flask app (and Phase 2's pg_cron jobs) use the service key,
-- which bypasses RLS.

-- ── subscribers ──────────────────────────────────────────────────────────────
-- Mirrors the JSON store's shape (email/phone/carrier/station_ids/timestamps)
-- plus `active` (soft-delete / future unsubscribe) and a nullable `region_zone`
-- to leave room for B3 region-based subscriptions.
create table public.subscribers (
  id          uuid primary key default gen_random_uuid(),
  email       text not null default '',
  phone_number text not null default '',
  carrier     text not null default '',
  station_ids text[] not null default '{}',
  region_zone text,
  active      boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- One row per email (case-insensitive), when an email is present.
create unique index subscribers_email_key
  on public.subscribers (lower(email))
  where email <> '';

-- ── watcher_state ────────────────────────────────────────────────────────────
-- The edge-trigger dedup memory: one row per station, `status` is the last
-- observed state ('ok' | 'posted' | 'cso'). A transition alert fires only when
-- the newly observed status is more severe than this row — so these rows ARE
-- the dedup keys / "current CSO event state".
create table public.watcher_state (
  station_id   text primary key,
  station_name text not null default '',
  status       text not null check (status in ('ok', 'posted', 'cso')),
  updated_at   timestamptz not null default now()
);

-- ── alert_log ────────────────────────────────────────────────────────────────
-- Every dispatch attempt (B7 delivery log). `results` holds the full
-- per-recipient outcome incl. provider responses; `source` distinguishes the
-- in-process watcher, manual button, and (Phase 2) the pg_cron shadow/live path.
create table public.alert_log (
  id              bigint generated always as identity primary key,
  created_at      timestamptz not null default now(),
  event_type      text not null,                       -- 'posted' | 'cso' | 'manual_dispatch'
  station_ids     text[] not null default '{}',
  station_names   text[] not null default '{}',
  recipient_count integer not null default 0,
  channel         text not null default '',            -- email | sms | mixed | none
  simulated       boolean not null default false,
  results         jsonb,
  source          text not null default 'watcher'      -- watcher | manual | pg_shadow | pg_live
);

create index alert_log_created_at_idx on public.alert_log (created_at desc);

-- ── updated_at maintenance ───────────────────────────────────────────────────
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create trigger subscribers_updated_at
  before update on public.subscribers
  for each row execute function public.set_updated_at();

create trigger watcher_state_updated_at
  before update on public.watcher_state
  for each row execute function public.set_updated_at();

-- ── lockdown: service role only ──────────────────────────────────────────────
alter table public.subscribers   enable row level security;
alter table public.watcher_state enable row level security;
alter table public.alert_log     enable row level security;
-- No policies created: anon/authenticated are denied by RLS even before grants.
-- Revoke the default grants too (defense in depth).
revoke all on public.subscribers,  public.watcher_state, public.alert_log
  from anon, authenticated;
revoke usage on all sequences in schema public from anon, authenticated;
