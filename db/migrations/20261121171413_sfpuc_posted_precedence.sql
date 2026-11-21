-- Match SFPUC's public map: an active posted flag wins over W (not routinely
-- sampled). Vicente's 2026-11-21 row is W + posted=OCEAN#21_SL + p_color=R.
-- R/Y sample-color precedence and CSO precedence are unchanged.
--
-- Roll out with shared/sfpuc_api.py. This only changes the pure classifier;
-- it does not rewrite history or dispatch messages. Once applied, the next
-- scheduled watcher poll can detect an ok -> posted transition and send
-- subscriber alerts. Coordinate that activation explicitly before applying.
-- Verify after applying: db/scripts/test_classify_parity.py (read-only RPCs).

create or replace function public.bwtf_classify(
  p_cso text, p_s_color text, p_posted text, p_p_color text
) returns text
language plpgsql immutable as $$
begin
  if p_cso is not null and p_cso <> '' then
    return 'cso';
  end if;
  if upper(coalesce(p_s_color, '')) = 'R' then
    return 'posted';
  end if;
  -- Only Y (not sampled) short-circuits the posted flag. W reaches the same
  -- posted fallback as G/null on the official map, before falling back to ok.
  if upper(coalesce(p_s_color, '')) <> 'Y'
     and coalesce(p_posted, '') <> ''
     and upper(coalesce(p_p_color, '')) in ('R', 'G') then
    return 'posted';
  end if;
  return 'ok';
end $$;
