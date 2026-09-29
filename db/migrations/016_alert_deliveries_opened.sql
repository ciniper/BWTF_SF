-- 016: alert_deliveries.opened_at — the first time Brevo saw the email opened.
--
-- Brevo tracks opens with a pixel and reports them as 'opened' events (Apple
-- Mail's privacy prefetch is reported separately as 'loadedByProxy' and is
-- ignored). The level-2 poller (features/alerts/deliveries.poll_brevo, run by
-- the production forecast refresh) stamps the earliest 'opened' event here and
-- keeps asking about delivered-but-unopened rows for a week. Null means "no
-- open recorded", which is NOT proof the email was not read: readers who block
-- images never register. Chase asked for a single column (2026-09-29).
--
-- Apply by hand in the Supabase SQL editor; nothing else changes.
alter table public.alert_deliveries add column if not exists opened_at timestamptz;
comment on column public.alert_deliveries.opened_at is
  'first Brevo ''opened'' event for the message; null = none recorded (pixel-based, not proof of unread)';
