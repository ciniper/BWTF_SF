"""Parse SFPUC monthly SMR PDFs: extract per-event CSD tables (v2).

v2: visual lines are bound to the nearest date-row center by y-distance,
so event rows vertically offset from their date label (incl. 2nd events on
the same day) are captured.
"""
import pdfplumber, re, sys

DATE_RE = re.compile(r'^(\d{1,2})/(\d{1,2})/(\d{4})$')
CSD_HDR_RE = re.compile(r'^#?(\d+A?)$')
TIME_RE = re.compile(r'^\d{1,2}:\d{2}$')

def group_lines(words, tol=2.5):
    lines = []
    for w in sorted(words, key=lambda w: w['top']):
        if lines and abs(lines[-1][0] - w['top']) <= tol:
            lines[-1][1].append(w)
        else:
            lines.append([w['top'], [w]])
    return [(top, sorted(ws, key=lambda w: w['x0'])) for top, ws in lines]

def merge_times(ln):
    merged, i = [], 0
    while i < len(ln):
        if i+1 < len(ln) and ln[i+1]['text'] in ('AM','PM') and TIME_RE.match(ln[i]['text']):
            merged.append({'text': ln[i]['text']+' '+ln[i+1]['text'],
                           'x0': ln[i]['x0'], 'x1': ln[i+1]['x1'], 'top': ln[i]['top']})
            i += 2
        else:
            merged.append(ln[i]); i += 1
    return merged

def parse_csd_page(page, source):
    text = page.extract_text() or ''
    m = re.search(r'^\s*(.*?CSD Summary)\s*$', text, re.M)
    if not m:
        return None
    basin = m.group(1).strip()
    words = page.extract_words()
    lines = group_lines(words)

    # 1. outfall headers
    outfalls = []
    for top, ln in lines:
        toks = [w['text'] for w in ln]
        if 'CSD' in toks:
            i = 0
            cand = []
            while i < len(ln):
                if ln[i]['text'] == 'CSD' and i+1 < len(ln) and CSD_HDR_RE.match(ln[i+1]['text']):
                    cid = ln[i+1]['text'].lstrip('#')
                    name = []
                    j = i+2
                    while j < len(ln) and ln[j]['text'] != 'CSD':
                        name.append(ln[j]['text']); j += 1
                    cand.append((ln[i]['x0'], cid, ' '.join(name)))
                    i = j
                else:
                    i += 1
            if len(cand) >= 2:
                outfalls = cand
                break
    if not outfalls:
        return {'basin': basin, 'events': [], 'totals': [], 'notes': [], 'warn': 'no outfall headers'}

    # 2. unit anchors
    anchors, units_top = [], None
    for top, ln in lines:
        got = [((w['x0']+w['x1'])/2, w['text']) for w in ln if w['text'] in ('(hh:mm)','(minutes)','(MG)')]
        if len(got) >= 3*len(outfalls):
            anchors, units_top = got, top
            break
    if not anchors:
        return {'basin': basin, 'events': [], 'totals': [], 'notes': [], 'warn': 'no unit anchors'}
    triplets = [anchors[i:i+3] for i in range(0, len(anchors), 3)]
    triplets = [t for t in triplets if len(t) == 3]
    outfalls_sorted = sorted(outfalls)
    k = min(len(triplets), len(outfalls_sorted))
    cols = [(cid, name, tri[0][0], tri[1][0], tri[2][0])
            for (ox, cid, name), tri in zip(outfalls_sorted[:k], triplets[:k])]

    def assign(ws):
        out = {}
        for w in merge_times(ws):
            xc = (w['x0']+w['x1'])/2
            best, bd, bf = None, 1e9, None
            for ci, (cid, name, xt, xd, xv) in enumerate(cols):
                for x, f in ((xt,'time'),(xd,'dur'),(xv,'vol')):
                    d = abs(xc-x)
                    if d < bd:
                        best, bd, bf = ci, d, f
            if bd <= 28:
                # keep first value per field (later dup = column collision)
                out.setdefault(best, {}).setdefault(bf, w['text'])
        return out

    # 3. date row centers + TOTAL row
    date_rows, total_top = [], None
    for top, ln in lines:
        if top <= units_top: continue
        first = ln[0]['text']
        dm = DATE_RE.match(first)
        if dm and int(dm.group(3)) >= 2000:
            date_rows.append((top, first))
        elif first == 'TOTAL':
            total_top = top

    events, totals, notes = [], [], []
    for top, ln in lines:
        if top <= units_top: continue
        first = ln[0]['text']
        if first == 'TOTAL' or (total_top is not None and abs(top-total_top) <= 2.5):
            row = assign([w for w in ln if w['text'] != 'TOTAL'])
            for ci, vals in row.items():
                totals.append({'outfall': cols[ci][0], 'duration_min': vals.get('dur'),
                               'volume_MG': vals.get('vol'), 'basin': basin, 'source': source})
            continue
        if re.match(r'^\d+\.$', first) or first in ('NOTES','Notes'):
            notes.append(' '.join(w['text'] for w in ln))
            continue
        if total_top is not None and top > total_top:
            continue
        # bind line to nearest date row
        if not date_rows: continue
        cand = min(date_rows, key=lambda dr: abs(dr[0]-top))
        if abs(cand[0]-top) > 8: continue
        date = cand[1]
        dm = DATE_RE.match(first)
        ws = ln[1:] if dm else ln
        row = assign(ws)
        for ci, vals in row.items():
            if any(k in vals for k in ('time','dur','vol')):
                events.append({'date': date, 'outfall': cols[ci][0], 'outfall_name': cols[ci][1],
                               'start_time': vals.get('time'), 'duration_min': vals.get('dur'),
                               'volume_MG': vals.get('vol'), 'basin': basin, 'source': source})
    return {'basin': basin, 'events': events, 'totals': totals, 'notes': notes,
            'n_outfalls': len(cols), 'warn': None}

def parse_pdf(path, source):
    results = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            try:
                r = parse_csd_page(page, source)
            except Exception as e:
                r = {'basin': '?', 'events': [], 'totals': [], 'notes': [], 'warn': f'EXC {e}'}
            if r:
                results.append(r)
    return results
