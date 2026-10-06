import json, os, re, time, requests

idx = json.load(open('attachment_index.json'))
os.makedirs('pdfs', exist_ok=True)
sess = requests.Session()
sess.headers['User-Agent'] = 'Mozilla/5.0 (research; CSD records compilation)'

MONTHS = {m: i+1 for i, m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'])}

def want(a):
    n = a['name'].lower()
    if a['attType'] == '2': return True
    if 'wet weather' in n: return True
    if re.search(r'\bww\b', n): return True        # "February 2014 Bayside WW Report.pdf": missed until 2026-10-06
    if re.search(r'smr[- ]?dmr', n): return True
    return False

manifest = []
n_dl = 0
for d in idx:
    m = re.search(r'for (\w+) (\d{4})', d['report_name'])
    mon = MONTHS.get(m.group(1)) if m else None
    for a in d['attachments']:
        if not want(a): continue
        safe = re.sub(r'[^A-Za-z0-9._-]', '_', a['name'])
        fn = f"pdfs/{d['facility']}_{d['year']}-{mon:02d}_{d['document_id']}_{a['attachmentID']}_{safe}" if mon else f"pdfs/{d['facility']}_{d['year']}_{d['document_id']}_{a['attachmentID']}_{safe}"
        manifest.append({'facility': d['facility'], 'year': d['year'], 'month': mon,
                         'document_id': d['document_id'], 'report_name': d['report_name'],
                         'att_name': a['name'], 'attType': a['attType'], 'file': fn})
        if os.path.exists(fn) and os.path.getsize(fn) > 1000: continue
        url = f"https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/PublicAttachmentRetriever?parentID={a['parentID']}&attachmentID={a['attachmentID']}&attType={a['attType']}"
        try:
            r = sess.get(url, timeout=120)
            open(fn, 'wb').write(r.content)
            n_dl += 1
            if n_dl % 20 == 0: print(f"{n_dl} downloaded...")
            time.sleep(0.2)
        except Exception as e:
            print('ERR', fn, e)
json.dump(manifest, open('download_manifest.json','w'), indent=1)
print('DONE', n_dl, 'downloaded,', len(manifest), 'in manifest')
