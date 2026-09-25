import json, re, csv, subprocess, os
from collections import defaultdict

res = json.load(open('parse_results.json'))
MONTHS = {m: i+1 for i, m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'])}
BASIN_MAP = {
    'Oceanside Basin CSD Summary': ('Oceanside', 'Pacific Ocean'),
    'Westside CSD Summary': ('Oceanside', 'Pacific Ocean'),   # header SFPUC's template uses from ~2026
    'North Shore Basin CSD Summary': ('North Shore', 'SF Bay - North Shore waterfront'),
    'Central Basin #1 CSD Summary': ('Central (Mission Creek)', 'Mission Creek / China Basin'),
    'Central Basin #2 CSD Summary': ('Central (Islais Creek)', 'Islais Creek'),
    'Southeast Basin CSD Summary': ('Southeast', 'SF Bay - Southeast (Yosemite Slough / Candlestick)'),
}
OUTFALL_NAMES = {
    'OSP': {'1':'Lake Merced','2':'Vicente','3':'Lincoln','4':'Mile Rock','5':'Sea Cliff #1','6':'Sea Cliff Brick Sewer','7':'Sea Cliff #2'},
    'SEP': {'9':'Baker Street','10':'Pierce Street','11':'Laguna Street','13':'Beach Street','15':'Sansome Street','17':'Jackson Street',
            '18':'Howard Street','19':'Brannan Street','22':'Third Street','23':'Fourth Street North','24':'Fifth Street North',
            '25':'Sixth Street North','26':'Division Street','27':'Sixth Street South','28':'Fourth Street South',
            '29':'Mariposa Street','30':'20th Street','30A':'22nd Street','31':'Third Street North','31A':'Islais Creek North',
            '32':'Marin Street','33':'Selby Street','35':'Third Street South',
            '37':'Evans Avenue','38':'Hudson Avenue','40':'Griffith Street','41':'Yosemite Avenue','42':'Fitch Street','43':'Sunnydale Avenue'}
}

def num(s):
    if s is None: return None, None
    s = s.strip().replace(',', '')
    if s in ('', '#N/A', 'NA', '-'): return None, None
    q = ''
    if s.startswith('<'): q = '<'; s = s[1:]
    m = re.match(r'^(\d+):(\d{2})$', s)  # h:mm duration
    if m: return int(m.group(1))*60 + int(m.group(2)), 'hmm'
    try: return float(s), q
    except ValueError: return None, 'unparsed:' + s

seen_files, records = set(), []
for r in res:
    if r['file'] in seen_files: continue
    seen_files.add(r['file'])
    m = re.search(r'for (\w+) (\d{4})', r['report_name'])
    rp_mon, rp_yr = (MONTHS.get(m.group(1)), int(m.group(2))) if m else (None, None)
    att_id = int(re.search(r'_(\d+)_[^/]*$', r['file']).group(1))
    for p in r['parse']:
        if p['basin'] not in BASIN_MAP: continue
        records.append({'facility': r['facility'], 'rp_yr': rp_yr, 'rp_mon': rp_mon,
                        'document_id': r['document_id'], 'att_id': att_id,
                        'att_name': r['att_name'], 'file': r['file'], 'page': p})

best = {}
for rec in records:
    key = (rec['facility'], rec['rp_yr'], rec['rp_mon'], rec['page']['basin'])
    if key not in best or rec['att_id'] > best[key]['att_id']:
        best[key] = rec

events, qa = [], []
month_status = defaultdict(dict)  # (fac, yr, mon) -> basin -> n_events
for key, rec in sorted(best.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2] or 0)):
    fac, rp_yr, rp_mon, _ = key
    p = rec['page']
    basin, rw = BASIN_MAP[p['basin']]
    sums = defaultdict(lambda: [0.0, 0.0])
    month_events = []
    for e in p['events']:
        dur, dq = num(e['duration_min'])
        vol, vq = num(e['volume_MG'])
        st = e['start_time'] if e['start_time'] not in ('#N/A', None) else ''
        if (dur or 0) <= 0 and (vol or 0) <= 0 and not st:
            continue
        mm, dd, yy = e['date'].split('/')
        cid = e['outfall']
        name = OUTFALL_NAMES.get(fac, {}).get(cid, e['outfall_name'])
        base = cid.rstrip('AB')
        row = {
            'event_date': f"{yy}-{int(mm):02d}-{int(dd):02d}",
            'facility': 'Oceanside (CA0037681)' if fac == 'OSP' else 'Southeast/Bayside (CA0037664)',
            'outfall_id': f"CSD-{int(base):03d}{cid[len(base):]}" if base.isdigit() else f"CSD-{cid}",
            'outfall_name': name, 'basin': basin, 'receiving_water': rw,
            'start_time': st,
            'duration_min': dur, 'duration_flag': 'reported_hmm' if dq == 'hmm' else ('unparsed' if (dq or '').startswith('unparsed') else ''),
            'volume_MG': vol, 'volume_qualifier': vq if vq in ('<',) else '',
            'source_document': rec['att_name'], 'ciwqs_document_id': rec['document_id'],
            'report_period': f"{rp_yr}-{rp_mon:02d}" if rp_mon else '',
            '_cid': cid,
        }
        month_events.append(row)
        if dur: sums[cid][0] += dur
        if vol: sums[cid][1] += vol
    # backfill duration from TOTAL when exactly one event that month lacks a usable duration
    tot_by = {}
    for t in p['totals']:
        tdur, _ = num(t['duration_min']); tvol, _ = num(t['volume_MG'])
        tot_by[t['outfall']] = (tdur, tvol)
    for cid, (tdur, tvol) in tot_by.items():
        evs = [e for e in month_events if e['_cid'] == cid]
        if len(evs) == 1 and tdur and not evs[0]['duration_min']:
            evs[0]['duration_min'] = tdur; evs[0]['duration_flag'] = 'from_monthly_total'
            sums[cid][0] += tdur
        if len(evs) == 1 and tdur and evs[0]['duration_min'] == 0 and tdur > 0:
            evs[0]['duration_min'] = tdur; evs[0]['duration_flag'] = 'from_monthly_total'
            sums[cid][0] += tdur
    for cid, (tdur, tvol) in tot_by.items():
        sdur, svol = sums.get(cid, [0.0, 0.0])
        if tdur is not None and abs(tdur - sdur) > max(1.5, 0.02*tdur):
            qa.append(f"DUR mismatch {key} CSD-{cid}: total={tdur} sum={round(sdur,2)} [{rec['att_name']}]")
        if tvol is not None and abs(tvol - svol) > max(0.03, 0.02*tvol):
            qa.append(f"VOL mismatch {key} CSD-{cid}: total={tvol} sum={round(svol,2)} [{rec['att_name']}]")
    events.extend(month_events)
    month_status[(fac, rp_yr, rp_mon)][basin] = len(month_events)

for e in events: e.pop('_cid')
events.sort(key=lambda e: (e['event_date'], e['facility'], e['outfall_id']))
EVENT_FIELDS = ['event_date', 'facility', 'outfall_id', 'outfall_name', 'basin', 'receiving_water', 'start_time',
                'duration_min', 'duration_flag', 'volume_MG', 'volume_qualifier', 'source_document', 'ciwqs_document_id', 'report_period']
if events:
    assert list(events[0].keys()) == EVENT_FIELDS, list(events[0].keys())
with open('sf_csd_events.csv', 'w', newline='') as f:   # header even for a zero-event run (dry months)
    w = csv.DictWriter(f, fieldnames=EVENT_FIELDS)
    w.writeheader(); w.writerows(events)

# ---- coverage grid ----
manifest = json.load(open('download_manifest.json'))
docs_by_month = defaultdict(list)
for m in manifest:
    mm = re.search(r'for (\w+) (\d{4})', m['report_name'])
    if not mm or 'Monthly' not in m['report_name']: continue
    docs_by_month[(m['facility'], int(mm.group(2)), MONTHS.get(mm.group(1)))].append(m)

def stated_no_discharge(files):
    for m in files:
        if m['attType'] != '2' or not os.path.exists(m['file']): continue
        try:
            txt = subprocess.run(['pdftotext', m['file'], '-'], capture_output=True, timeout=120).stdout.decode('utf8', 'replace')
        except Exception:
            continue
        if re.search(r'(there were|there was) no (combined sewer|CSD)', txt, re.I):
            return True
    return False

grid = []
for (fac, yr, mon), files in sorted(docs_by_month.items()):
    st = month_status.get((fac, yr, mon))
    n = sum(st.values()) if st else 0
    if st and n > 0: status = 'events_parsed'
    elif st: status = 'table_present_zero_events'
    elif stated_no_discharge(files): status = 'no_table_stated_no_discharge'
    else: status = 'no_event_table_found'
    grid.append({'facility': 'Oceanside (CA0037681)' if fac=='OSP' else 'Southeast/Bayside (CA0037664)',
                 'year': yr, 'month': mon, 'status': status, 'n_event_rows': n})
with open('sf_csd_monthly_coverage.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['facility','year','month','status','n_event_rows'])
    w.writeheader(); w.writerows(grid)

json.dump(qa, open('qa_report.json','w'), indent=1)
from collections import Counter
print(len(events), 'events;', len(qa), 'QA flags')
print(Counter(e['event_date'][:4] for e in events))
print(Counter(g['status'] for g in grid))
