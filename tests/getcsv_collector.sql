-- Run against a migrated database; all test data and runtime changes roll back.
begin;
do $$
declare p jsonb; n integer;
begin
  update public.sfpuc_getcsv_runtime set previous_values=null,last_success_at=null where id=1;
  p := '[{"source":"__collector_test__","sample_date":"11/13/26","analyte":"COLI_TOTAL","data":">16000"}]';
  assert public.bwtf_record_getcsv(p,'2026-11-16T19:50:00Z') = 1, 'initial baseline';
  assert public.bwtf_record_getcsv(p,'2026-11-16T19:50:00Z') = 0, 'same read is idempotent';
  assert public.bwtf_record_getcsv(p,'2026-11-16T19:51:00Z') = 0, 'unchanged read';
  assert public.bwtf_record_getcsv(p || '[{"source":null,"sample_date":null,"analyte":null,"data":null}]','2026-11-16T19:51:30Z') = 0, 'all-null upstream placeholders ignored';
  p := jsonb_set(p,'{0,data}','"16000"');
  assert public.bwtf_record_getcsv(p,'2026-11-16T19:52:00Z') = 1, 'revision';
  p := jsonb_set(p,'{0,data}','">16000"');
  assert public.bwtf_record_getcsv(p,'2026-11-16T19:54:00Z') = 1, 'reversion';
  p := p || '[{"source":"__collector_test__","sample_date":"11/13/26","analyte":"ENTERO","data":"<10"}]';
  assert public.bwtf_record_getcsv(p,'2026-11-16T19:56:00Z') = 1, 'partial indicator arrives';
  p := p - 0;
  assert public.bwtf_record_getcsv(p,'2026-11-16T19:58:00Z') = 1, 'missing indicator on existing sample';
  assert public.bwtf_record_getcsv(p,'2026-11-16T19:59:00Z') = 0, 'removal not repeated';
  p := jsonb_set(p,'{0,sample_date}','"11/14/26"');
  assert public.bwtf_record_getcsv(p,'2026-11-16T20:00:00Z') = 1, 'aged out whole sample not removed';
  assert public.bwtf_record_getcsv(p,'2026-11-16T19:59:00Z') = 0, 'old response ignored';
  begin
    perform public.bwtf_record_getcsv('[]','2026-11-16T20:01:00Z');
    raise exception 'empty response was accepted';
  exception when others then
    if sqlerrm <> 'Empty getCSV response' then raise; end if;
  end;
  begin
    perform public.bwtf_record_getcsv(p || jsonb_set(p,'{0,data}','"99"'),'2026-11-16T20:01:00Z');
    raise exception 'conflicting duplicate was accepted';
  exception when others then
    if sqlerrm <> 'Conflicting duplicate getCSV result' then raise; end if;
  end;
  select count(*) into n from public.sfpuc_getcsv_observations where source_code='__collector_test__';
  assert n = 6, 'exact event count';
  assert (select last_success_at = '2026-11-16T20:00:00Z'::timestamptz from public.sfpuc_getcsv_runtime where id=1), 'bad responses preserve good state';
end $$;
select 'collector regression checks passed' as result;
rollback;
