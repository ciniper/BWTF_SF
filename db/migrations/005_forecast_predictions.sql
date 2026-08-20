-- Forecast predictions move from process memory to Supabase (compute-on-visit).
--
-- Single-row cache: the /forecast/api/data handler serves `snapshot` when
-- `generated_at` is fresh (<30 min), otherwise re-runs the sklearn engine and
-- upserts. `refresh_started_at` is the concurrency guard: a visitor claims it
-- with a conditional update before computing, so simultaneous visitors can't
-- double-compute — losers serve the stale snapshot with a `refreshing` flag.
--
-- No cron anywhere: freshness is pulled by visits (the forecast is a
-- dashboard, not an alert — its output only matters when observed). The
-- designed upgrade path, if scheduled freshness is ever wanted: pg_cron
-- pings the refresh endpoint via pg_net — no new entities.

create table public.forecast_predictions (
  id                 integer primary key default 1 check (id = 1),
  snapshot           jsonb,
  generated_at       timestamptz,
  refresh_started_at timestamptz
);
insert into public.forecast_predictions (id) values (1) on conflict (id) do nothing;

alter table public.forecast_predictions enable row level security;
revoke all on public.forecast_predictions from anon, authenticated;
grant all on public.forecast_predictions to service_role;
