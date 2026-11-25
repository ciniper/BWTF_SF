-- Local/disposable database only. The Node harness supplies no-network stubs.
-- All recorder rows and runtime edits below roll back.
begin;
do $$
declare
  t timestamptz := clock_timestamp()-interval '9 minutes';
  p jsonb := '[{"stationid":"test-a","stationname":"A","s_color":"W","p_color":null,"posted":null,"cso":null,"sample_date":"11/18/26"}]';
  q jsonb; n integer; error_result jsonb;
begin
  assert public.bwtf_record_feed_colors(p,1,t)=0, 'disabled by default';
  assert not exists(select 1 from public.feed_color_runs), 'disabled must not write history';
  update public.feed_color_runtime set enabled=true where id=1;
  assert public.bwtf_record_feed_colors(p,1,t)=1, 'first observation is baseline';
  assert (select kind='baseline' and previous_state is null from public.feed_color_runs), 'baseline is not a transition';
  assert public.bwtf_record_feed_colors(p,1,t+interval '1 second')=0, 'duplicate response id is not another observation';
  assert public.bwtf_record_feed_colors(p,2,t+interval '1 minute')=0, 'unchanged state extends confirmation';
  assert (select confirmations=2 from public.feed_color_runs), 'only distinct responses confirmed';
  p:=jsonb_set(p,'{0,s_color}','"G"');
  assert public.bwtf_record_feed_colors(p,3,t+interval '2 minutes')=1, 'gray to green retained even though both alert states are ok';
  assert (select previous_state->>'s_color'='W' and state->>'s_color'='G'
       and previous_last_confirmed_at=t+interval '1 minute' and first_observed_at=t+interval '2 minutes'
       from public.feed_color_runs where first_response_id=3), 'uncertainty bounded by actual reads';
  p:=jsonb_set(p,'{0,p_color}','"R"');
  assert public.bwtf_record_feed_colors(p,4,t+interval '3 minutes')=1, 'posting color alone is recorded';
  p:=jsonb_set(p,'{0,posted}','"station-source"');
  assert public.bwtf_record_feed_colors(p,5,t+interval '4 minutes')=1, 'posted flag alone is recorded';
  p:=jsonb_set(p,'{0,cso}','"station-source"');
  assert public.bwtf_record_feed_colors(p,6,t+interval '5 minutes')=1, 'discharge flag alone is recorded';
  p:=jsonb_set(p,'{0,s_color}','null');
  assert public.bwtf_record_feed_colors(p,7,t+interval '6 minutes')=1, 'explicit null color retained';
  p:=jsonb_set(p,'{0,s_color}','"NEW_COLOR"');
  assert public.bwtf_record_feed_colors(p,8,t+interval '7 minutes')=1, 'unknown future color retained without guessing';
  p:=jsonb_set(p,'{0,sample_date}','"11/19/26"');
  assert public.bwtf_record_feed_colors(p,9,t+interval '7 minutes 30 seconds')=0, 'sample-date change is not a color transition';
  assert public.bwtf_record_feed_colors(p,8,t+interval '8 minutes')=0, 'older response ignored';
  assert public.bwtf_record_feed_colors(p,10,t+interval '7 minutes')=0, 'out-of-order observation ignored';
  select count(*) into n from public.feed_color_runs;
  q:=p || jsonb_set(p,'{0,s_color}','"R"');
  error_result:=public.test_capture_feed_colors(q,10,t+interval '8 minutes');
  assert error_result ? 'error', 'duplicate station rejected';
  assert (select count(*)=n from public.feed_color_runs), 'validation is atomic';
  assert (select last_response_id=9 from public.feed_color_runtime), 'invalid read does not advance cursor';
  error_result:=public.test_capture_feed_colors(p #- '{0,s_color}',10,t+interval '8 minutes');
  assert error_result ? 'error', 'missing field is not an explicit null';
  error_result:=public.test_capture_feed_colors('[]',10,t+interval '8 minutes');
  assert error_result ? 'error', 'empty response does not erase roster';
  error_result:=public.test_capture_feed_colors(p,10,clock_timestamp()+interval '1 hour');
  assert error_result ? 'error', 'future response rejected';
  error_result:=public.test_capture_feed_colors(p,10,t-interval '5 minutes');
  assert not error_result ? 'error', 'old observation ignored before freshness validation';
  -- A valid payload missing a previously known station records explicit absence.
  q:=jsonb_set(p,'{0,stationid}','"test-b"');
  assert public.bwtf_record_feed_colors(q,10,t+interval '8 minutes')=2, 'one missing station and one new baseline';
  assert exists(select 1 from public.feed_color_runs where station_id='test-a' and kind='missing' and state='{"present":false}'), 'absence is not green';
  assert public.bwtf_record_feed_colors(p || q,11,t+interval '8 minutes 30 seconds')=1, 'station return starts resume run';
  assert exists(select 1 from public.feed_color_runs where station_id='test-a' and first_response_id=11 and kind='resume'), 'return is marked';
  assert not has_function_privilege('anon','public.bwtf_record_feed_colors(jsonb,bigint,timestamptz)','execute'), 'anon cannot ingest';
  assert not has_function_privilege('authenticated','public.bwtf_feed_color_tick()','execute'), 'authenticated cannot ingest';
  assert not has_function_privilege('service_role','public.bwtf_feed_color_tick()','execute'), 'app service key cannot ingest';
  assert has_table_privilege('service_role','public.feed_color_runs','select'), 'server can read history';
  assert not has_table_privilege('service_role','public.feed_color_runs','insert'), 'server cannot forge history';
  assert (select relrowsecurity from pg_class where oid='public.feed_color_runs'::regclass), 'RLS enabled';
end $$;
rollback;

begin;
do $$
declare t timestamptz:=clock_timestamp()-interval '5 minutes';
        p jsonb:='[{"stationid":"gap","stationname":"Gap","s_color":"W","p_color":null,"posted":null,"cso":null}]';
begin
  update public.feed_color_runtime set enabled=true where id=1;
  assert public.bwtf_record_feed_colors(p,1,t)=1;
  update public.feed_color_runs set first_observed_at=t-interval '46 minutes',last_confirmed_at=t-interval '46 minutes';
  update public.feed_color_runtime set last_observed_at=t-interval '46 minutes' where id=1;
  assert public.bwtf_record_feed_colors(p,2,t+interval '3 minutes')=1, 'missed half-hour polls split unchanged runs';
  assert (select kind='resume' and previous_state=state from public.feed_color_runs where first_response_id=2), 'same color after gap is not a fabricated change';
  update public.feed_color_runtime set enabled=false where id=1;
  assert public.bwtf_record_feed_colors(p,3,t+interval '4 minutes')=0, 'kill switch stops recording';
  assert (select count(*)=2 from public.feed_color_runs), 'kill switch preserves history';
end $$;
rollback;

-- Invalid fresh responses must never consume a baseline or replace good data.
begin;
do $$
declare t timestamptz:=clock_timestamp()-interval '1 minute'; result jsonb;
        p jsonb:='[{"stationid":"a","stationname":"A","s_color":null,"p_color":null,"posted":null,"cso":null}]';
begin
  update public.feed_color_runtime set enabled=true where id=1;
  result:=public.test_capture_feed_colors(p,1,t-interval '1 hour');
  assert result ? 'error', 'stale baseline rejected';
  result:=public.test_capture_feed_colors('{}',1,t);
  assert result ? 'error', 'non-array rejected';
  result:=public.test_capture_feed_colors(jsonb_set(p,'{0,s_color}','42'),1,t);
  assert result ? 'error', 'non-string color rejected';
  result:=public.test_capture_feed_colors((select jsonb_agg(p->0) from generate_series(1,101)),1,t);
  assert result ? 'error', 'oversized roster rejected';
  assert not exists(select 1 from public.feed_color_runs), 'invalid responses leave no runs';
  assert (select last_response_id is null and consecutive_failures=4 from public.feed_color_runtime), 'cursor unchanged, failures visible';
  assert public.bwtf_record_feed_colors(p,1,t)=1, 'valid response recovers after errors';
  assert (select consecutive_failures=0 and last_error is null from public.feed_color_runtime), 'healthy read clears active error';
end $$;
rollback;

-- Simulate two successful half-hour observations without waiting 30 minutes.
begin;
do $$
declare t timestamptz:=clock_timestamp()-interval '1 minute';
        p jsonb:='[{"stationid":"half-hour","stationname":"Half hour","s_color":"G","p_color":null,"posted":null,"cso":null}]';
begin
  update public.feed_color_runtime set enabled=true where id=1;
  assert public.bwtf_record_feed_colors(p,1,t)=1;
  update public.feed_color_runs set first_observed_at=t-interval '30 minutes',last_confirmed_at=t-interval '30 minutes';
  update public.feed_color_reads set observed_at=t-interval '30 minutes';
  update public.feed_color_runtime set last_observed_at=t-interval '30 minutes';
  assert public.bwtf_record_feed_colors(p,2,t)=0, 'normal half-hour read extends unchanged run';
  assert (select confirmations=2 from public.feed_color_runs), 'regular cadence not marked as outage';
  assert (select count(*)=2 from public.feed_color_reads), 'each checkpoint is independently retained';
end $$;
rollback;
