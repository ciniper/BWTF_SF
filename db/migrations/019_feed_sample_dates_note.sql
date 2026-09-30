-- 019: a note on a feed_sample_dates row, and the one observation we have.
--
-- 018 backfilled the sample dates the map already showed from the station-day
-- table, which knows the day the feed changed but not the minute. For the
-- 09/28/26 round Chase watched the map change live at about 4:15 PM Pacific
-- on 2026-09-29 (the forecast snapshot at 00:05 still showed 09/21; the
-- other session's fetch at 4:17 PM showed 09/28). That is an observation, not
-- a tick: the row keeps `approx = true`, takes the observed time, and says so
-- in `note`, which the timeline shows on hover ("seen live, not confirmed by
-- the watcher"). Rows the tick records from now on have no note.
--
-- Apply by hand in the Supabase SQL editor. Safe to re-run.

alter table public.feed_sample_dates
  add column if not exists note text;   -- a hand-entered caveat shown on hover; null for rows the tick recorded

update public.feed_sample_dates
   set first_seen_at = '2026-09-29 23:15:00+00',
       note = 'seen live by Chase, not confirmed by the watcher'
 where sample_date = '2026-09-28'
   and approx
   and note is null;
