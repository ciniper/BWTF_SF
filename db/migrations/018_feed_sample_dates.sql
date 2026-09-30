-- 018: when SFPUC's map first showed each sample date.
--
-- The beach map feed carries, per station, the date of the newest sample the
-- lab has graded; the station's colour is the read of that sample. It advances
-- the day after collection (every station moved to 09/28/26 on 2026-09-29),
-- while the numbers reach DataSF days later (the 09/21 round appeared there on
-- the 26th; the 09/28 round had not by the 29th). That gap — the city has
-- graded and, if need be, posted a beach whose result the public cannot yet
-- read — is what the SFPUC Alerts Timeline shows next to each sample. The
-- station-day table (012) keeps only the last tick's object per day, so it
-- can place the moment to a day at best; this table keeps the tick.
--
--   * feed_sample_dates: one row per (station, sample date) — the tick that
--     first showed it, the station's classified status and colours at that
--     tick. `approx` marks rows backfilled from feed_station_days (day precision).
--   * bwtf_feed_sample_date(text): the feed's MM/DD/YY (or MM/DD/YYYY) → date,
--     null for anything else — never raises, so a malformed feed cannot trip
--     the tick's guarded feed_day block.
--   * bwtf_record_feed_day: 012's body plus one marked insert.
--
-- Apply by hand in the Supabase SQL editor with no simulation active. Safe to re-run.

create or replace function public.bwtf_feed_sample_date(p text)
returns date
language plpgsql stable as $$
begin
  if p is null or p !~ '^\d{1,2}/\d{1,2}/(\d{2}|\d{4})$' then
    return null;
  end if;
  return case when length(split_part(p, '/', 3)) = 2 then to_date(p, 'MM/DD/YY') else to_date(p, 'MM/DD/YYYY') end;
exception when others then
  return null;
end $$;
revoke all on function public.bwtf_feed_sample_date(text) from public, anon, authenticated;
grant execute on function public.bwtf_feed_sample_date(text) to service_role;

create table if not exists public.feed_sample_dates (
  station_id           text not null,                        -- the feed's stationid (the timeline's key)
  sample_date          date not null,                        -- the feed's sample_date, parsed
  first_seen_at        timestamptz not null default now(),   -- the tick that first showed it
  approx               boolean not null default false,       -- backfilled from feed_station_days: the day, not the tick
  status_first         text not null default 'ok' check (status_first in ('ok', 'posted', 'cso')),  -- bwtf_classify at that tick
  posting_color_first  text,                                 -- p_color at that tick
  sample_color_first   text,                                 -- s_color at that tick
  primary key (station_id, sample_date)
);
create index if not exists feed_sample_dates_first_seen_idx on public.feed_sample_dates (first_seen_at);
revoke all on public.feed_sample_dates from anon, authenticated;
grant all on public.feed_sample_dates to service_role;

create or replace function public.bwtf_record_feed_day(p_payload jsonb)
returns integer
language plpgsql security definer set search_path = public as $$
declare
  station jsonb;
  sid text;
  st text;
  n integer := 0;
  -- 018: parsed feed sample date
  sd date;
  -- /018
  today date := (now() at time zone 'America/Los_Angeles')::date;
begin
  for station in select * from jsonb_array_elements(p_payload) loop
    sid := station->>'stationid';
    if sid is null or sid = '' then
      continue;
    end if;
    st := bwtf_classify(station->>'cso', station->>'s_color', station->>'posted', station->>'p_color');
    insert into public.feed_station_days as f
      (station_id, day, station_name, posted, cso, status_max, status_last, sample_color, posting_color, raw_last)
    values (sid, today, coalesce(station->>'stationname', ''),
            coalesce(station->>'posted', '') <> '', coalesce(station->>'cso', '') <> '',
            st, st, station->>'s_color', station->>'p_color', station)
    on conflict (station_id, day) do update
      set station_name  = excluded.station_name,
          posted        = f.posted or excluded.posted,
          cso           = f.cso or excluded.cso,
          status_max    = case when f.status_max = 'cso' or excluded.status_max = 'cso' then 'cso'
                               when f.status_max = 'posted' or excluded.status_max = 'posted' then 'posted'
                               else 'ok' end,
          status_last   = excluded.status_last,
          sample_color  = excluded.sample_color,
          posting_color = excluded.posting_color,
          raw_last      = excluded.raw_last,
          last_seen_at  = now(),
          ticks         = f.ticks + 1;
    -- 018: the tick that first showed this station's sample date on the map
    sd := bwtf_feed_sample_date(station->>'sample_date');
    if sd is not null then
      insert into public.feed_sample_dates (station_id, sample_date, status_first, posting_color_first, sample_color_first)
      values (sid, sd, st, station->>'p_color', station->>'s_color')
      on conflict (station_id, sample_date) do nothing;
    end if;
    -- /018
    n := n + 1;
  end loop;
  return n;
end $$;
revoke all on function public.bwtf_record_feed_day(jsonb) from public, anon, authenticated;
grant execute on function public.bwtf_record_feed_day(jsonb) to service_role;

-- What the station-day table already knows, to the day: the first day whose
-- last tick showed each sample date. Anything the map showed on the table's
-- first day (2026-09-27) was there before we watched — the page says "by".
insert into public.feed_sample_dates (station_id, sample_date, first_seen_at, approx, status_first, posting_color_first, sample_color_first)
select distinct on (f.station_id, bwtf_feed_sample_date(f.raw_last->>'sample_date'))
       f.station_id, bwtf_feed_sample_date(f.raw_last->>'sample_date'),
       f.first_seen_at, true, f.status_last, f.posting_color, f.sample_color
  from public.feed_station_days f
 where bwtf_feed_sample_date(f.raw_last->>'sample_date') is not null
 order by f.station_id, bwtf_feed_sample_date(f.raw_last->>'sample_date'), f.day
on conflict (station_id, sample_date) do nothing;
