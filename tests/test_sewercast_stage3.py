"""Stage 3 dashboard plumbing; no network and no changes to the model."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
import pandas as pd
import test_live_rules as R
from features.forecast import live_dashboard as ld
from shared.zones import ZONES


def test_stage3_paths_and_no_lingering():
    eng=ld.LiveData.__new__(ld.LiveData)
    eng.stage2,eng.split,eng.impact_table=R._stage2_v2()
    eng.specs=eng._load_specs()
    dates=R.DATES
    frames={'avg':pd.DataFrame({'precip_inches':[0.0]*len(dates)})}
    feats=[{'avg':{'precip_avg':0.,'rain_2d_cum':0.,'rain_3d_cum':0.}} for d in dates]
    probs=[{b:0. for b in R.BASINS} for d in dates]
    vols=[{b:1. for b in R.BASINS} for d in dates]
    probs[6]['westside']=1.;vols[6]['westside']=60.
    probs[7]['westside']=.9;vols[7]['westside']=.5
    def payload(i=7,live=None,observed=None):
        return eng._day_payload(frames,i,feats,probs,vols,dates,observed or {},live)
    comp=eng._compose_run(probs,vols,dates,{})
    plain=payload()
    expected={z:float(comp.s3.zone_p.loc[pd.Timestamp(dates[7]),z]) for z in ZONES}
    assert plain['stage3_zones']==plain['plain']['stage3_zones']==expected
    assert 0 < expected['ocean'] < expected['baker_china'] < .9
    assert expected['north']==expected['east']==0.
    assert plain['zones']['ocean'] > expected['ocean']
    assert all(v==0. for v in payload(8)['stage3_zones'].values())
    assert payload(8)['zones']['ocean']>0  # Stage 4 still lingers.
    assert payload(live={'enabled':False})['stage3_zones']==expected
    eng._live_corrections_enabled=lambda:(True,'fixture')
    eng._watcher_health=lambda:{'ok':False,'mode':None,'reason':'test'}
    eng._cso_flag_days=lambda *args:{}
    eng._sample_flags=lambda *args:{('Ocean Beach',dates[6]):False}
    eng._feed_sample_flags=lambda *args:{}
    live=eng._live_context(frames,probs,vols,dates,{},R.TODAY)
    corrected=payload(live=live)
    assert corrected['stage3_zones']==expected
    assert corrected['zones']['ocean'] != plain['zones']['ocean']
    observed={dates[7]:{'westside'}}
    live=eng._live_context(frames,probs,vols,dates,observed,R.TODAY)
    corrected=payload(live=live,observed=observed)
    direct=eng._compose_run(live['probs'],live['vols'],dates,observed)
    assert corrected['stage3_zones']==eng._stage3_zones(direct,dates[7])
    assert corrected['plain']['stage3_zones']==expected
    import test_compose_v2 as CTests
    eng.specs['s3']['cofire'] = CTests._shares('geo_v1')
    stations={dates[7]:{'4601'}}
    link_live={'enabled':True,'rule':ld.LINK_ZONE,'stations':stations,'health':{'ok':True},'source':'fixture'}
    linked=payload(live=link_live)
    direct=eng._compose_run(probs,vols,dates,stations=stations)
    assert linked['stage3_zones']==eng._stage3_zones(direct,dates[7])
    assert linked['plain']['stage3_zones']==expected
    assert linked['stage3_zones']['ocean']==1.

if __name__=='__main__':
    test_stage3_paths_and_no_lingering()
    print('PASS Stage 3 plain, disabled, live_v2, sample-only, observed onset, link_zone; canonical zones, valid zero and Stage 4 independence')
