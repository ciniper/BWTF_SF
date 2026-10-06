"""Parse legacy Bayside Wet Weather Report 'Discharge Summary' tables (2013 - Sep 2016).
Emits per-day per-outfall(-group) discharge hours + event count. No volumes (legacy
reports give only basin-level monthly volume estimates)."""
import pdfplumber, re, glob, json, csv
from collections import defaultdict

MONTH_NUM = {m: i+1 for i, m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'])}

def group_lines(words, tol=2.5):
    lines = []
    for w in sorted(words, key=lambda w: w['top']):
        if lines and abs(lines[-1][0] - w['top']) <= tol:
            lines[-1][1].append(w)
        else:
            lines.append([w['top'], [w]])
    return [(top, sorted(ws, key=lambda w: w['x0'])) for top, ws in lines]

def parse_page(page, source):
    text = page.extract_text() or ''
    if 'Discharge Summary' not in text: return []
    mt = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', text)
    if not mt: return []
    mon, yr = MONTH_NUM[mt.group(1)], int(mt.group(2))
    words = page.extract_words()
    lines = group_lines(words)
    # anchor pairs: 'Hours' tokens in the subheader (each has matching Count to its right)
    hours_x, count_x, hdr_top, overall_x = [], [], None, None
    for top, ln in lines:
        hx = [(w['x0']+w['x1'])/2 for w in ln if w['text'] == 'Hours']
        cx = [(w['x0']+w['x1'])/2 for w in ln if w['text'] == 'Count']
        if len(hx) >= 3:
            hours_x, count_x, hdr_top = hx, cx, top
            break
    for top, ln in lines:
        for w in ln:
            if w['text'] == 'Overall':
                overall_x = (w['x0']+w['x1'])/2
    if not hours_x: return []
    # group labels: line(s) above with outfall numbers like '009' '010' or '18,' groups
    # collect label line: numeric-ish tokens with x spans
    groups = []  # (x_center, label)
    for top, ln in lines:
        if top >= hdr_top: break
        toks = [w for w in ln if re.match(r'^#?\d+[0-9aA,]*$', w['text'])]
        if len(toks) >= 3:
            # label line: cluster consecutive tokens into groups by gap
            cur = [toks[0]]
            for w in toks[1:]:
                if w['x0'] - cur[-1]['x1'] < 30: cur.append(w)
                else: groups.append(cur); cur = [w]
            groups.append(cur)
    labels = [(sum((w['x0']+w['x1'])/2 for w in g)/len(g), ' '.join(w['text'] for w in g)) for g in groups] if groups else []
    def label_for(x):
        if not labels: return ''
        return min(labels, key=lambda l: abs(l[0]-x))[1]
    rows = []
    for top, ln in lines:
        if top <= hdr_top: continue
        first = ln[0]['text']
        if not re.match(r'^\d{1,2}$', first): 
            if first.lower() in ('total','totals'): continue
            continue
        day = int(first)
        if not (1 <= day <= 31): continue
        # day-number column is near left; ensure x0 small
        if ln[0]['x0'] > 150: continue
        for w in ln[1:]:
            xc = (w['x0']+w['x1'])/2
            v = w['text'].replace(',', '')
            if not re.match(r'^\d*\.?\d+$', v): continue
            # nearest anchor: hours or count
            dh = min(((abs(xc-x), i) for i, x in enumerate(hours_x)), default=(1e9, -1))
            dc = min(((abs(xc-x), i) for i, x in enumerate(count_x)), default=(1e9, -1))
            if overall_x is not None and abs(xc-overall_x) < min(dh[0], dc[0]):
                continue
            if dh[0] <= dc[0] and dh[0] <= 25:
                rows.append({'date': f"{yr}-{mon:02d}-{day:02d}", 'kind': 'hours', 'col': dh[1],
                             'x': hours_x[dh[1]], 'value': float(v)})
            elif dc[0] < dh[0] and dc[0] <= 25:
                rows.append({'date': f"{yr}-{mon:02d}-{day:02d}", 'kind': 'count', 'col': dc[1],
                             'x': count_x[dc[1]], 'value': float(v)})
    # merge hours+count per (date, col)
    out = defaultdict(dict)
    for r in rows:
        out[(r['date'], r['col'], label_for(r['x']))][r['kind']] = r['value']
    recs = []
    for (date, col, label), kv in sorted(out.items()):
        if kv.get('hours', 0) > 0 or kv.get('count', 0) > 0:
            recs.append({'date': date, 'outfall_group': label, 'discharge_hours': kv.get('hours'),
                         'discharge_count': kv.get('count'), 'source': source})
    return recs

if __name__ == '__main__':
    all_recs = []
    files = sorted(glob.glob('pdfs/SEP_201[3-6]-*Wet_Weather_Report*'))
    for f in files:
        try:
            with pdfplumber.open(f) as pdf:
                for page in pdf.pages:
                    all_recs.extend(parse_page(page, f.split('/')[-1]))
        except Exception as e:
            print('ERR', f, e)
    # keep only pre-Oct-2016 (modern covers 2016-10+)
    all_recs = [r for r in all_recs if r['date'] < '2016-10']
    # its output now lives at data/csd/pre2018/transcription/bayside_parsed_rows_2013_2016.csv (build_pre2018.py reads it)
    with open('sf_csd_events_bayside_legacy_2013_2016.csv', 'w', newline='') as fo:
        w = csv.DictWriter(fo, fieldnames=['date','outfall_group','discharge_hours','discharge_count','source'])
        w.writeheader(); w.writerows(all_recs)
    print(len(all_recs), 'legacy rows from', len(files), 'files')
    from collections import Counter
    print(Counter(r['date'][:4] for r in all_recs))
