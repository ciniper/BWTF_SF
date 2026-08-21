import json, os, sys, traceback
from parse_csd import parse_pdf

manifest = json.load(open('download_manifest.json'))
out = []
for i, m in enumerate(manifest):
    fn = m['file']
    rec = dict(m)
    rec['parse'] = []
    try:
        with open(fn,'rb') as f:
            head = f.read(5)
        if head != b'%PDF-':
            rec['error'] = f'not a PDF ({head[:20]})'
        else:
            rec['parse'] = parse_pdf(fn, os.path.basename(fn))
    except Exception as e:
        rec['error'] = f'{type(e).__name__}: {e}'
    out.append(rec)
    if (i+1) % 25 == 0:
        print(f"{i+1}/{len(manifest)} parsed", flush=True)
json.dump(out, open('parse_results.json','w'), indent=1)
n_ev = sum(len(p['events']) for r in out for p in r['parse'])
n_pg = sum(len(r['parse']) for r in out)
print('DONE:', len(out), 'files,', n_pg, 'CSD summary pages,', n_ev, 'event rows')
