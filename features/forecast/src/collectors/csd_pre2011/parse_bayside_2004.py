"""Read the Bayside "Discharge Summary" pages in SFPUC's 2004-2006 monthly wet weather reports.

The pages are text PDFs: one table per basin, a row per day of the month, and for each outfall
or outfall group a pair of columns, discharge hours and discharge count. No volumes.

Each number is placed on the day row nearest it vertically and the column pair nearest it
horizontally (some cells sit a few points off their row, which a line-based reader loses).
Every column is then checked against the page's printed Total row.

Usage: parse_bayside_2004.py [--checks checks.csv] <pdf>...
Writes the rows to stdout as CSV, and with --checks one line per column with its row sum and
printed total. The inputs used for data/csd/pre2011 are the 22 monthly reports listed with
status "new" in data/csd/pre2011/inventory.csv (Kyle's records, archived in the chapter's data
archive under 07_kyle_csd_records_2004-2010/raw_pdfs/).
"""
import csv
import re
import sys

import pdfplumber

MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December']
MISSPELT = {'Febuary': 'February', 'Ocotber': 'October'}   # as printed on some page titles
NUM = re.compile(r'^\d*\.?\d+$')


def centre(w):
    return (w['x0'] + w['x1']) / 2


def mid_y(w):
    return (w['top'] + w['bottom']) / 2


def parse_page(page, source, page_no):
    text = page.extract_text() or ''
    if 'Discharge Summary' not in text:
        return [], []
    m = re.search(r'(' + '|'.join(MONTHS + list(MISSPELT)) + r'),?\s+(\d{4})', text)
    if not m:
        return [], []
    month = f"{int(m.group(2))}-{MONTHS.index(MISSPELT.get(m.group(1), m.group(1))) + 1:02d}"
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)

    hours = sorted((w for w in words if w['text'] == 'Hours'), key=centre)
    counts = sorted((w for w in words if w['text'] == 'Count'), key=centre)
    if len(hours) < 2:
        return [], []
    head_y = max(mid_y(w) for w in hours + counts)
    # column pairs: each Hours anchor with the nearest Count anchor to its right
    pairs = []
    for h in hours:
        right = [c for c in counts if centre(c) > centre(h)]
        c = min(right, key=lambda c: centre(c) - centre(h)) if right else None
        pairs.append((centre(h), centre(c) if c else None))
    # basin name from the words "North/Central/South Basin" above the header
    basins = [(centre(w), w['text']) for w in words
              if w['text'] in ('North', 'Central', 'South') and mid_y(w) < head_y]

    def basin_for(x):
        return min(basins, key=lambda b: abs(b[0] - x))[1] if basins else ''

    # column label = header words above the Hours/Count line that fall inside the pair's span
    bounds = []
    for i, (hx, cx) in enumerate(pairs):
        left = (pairs[i - 1][1] + hx) / 2 if i and pairs[i - 1][1] else hx - 60
        right = (cx + pairs[i + 1][0]) / 2 if cx and i + 1 < len(pairs) else (cx or hx) + 60
        bounds.append((left, right))
    title_y = max((mid_y(w) for w in words if w['text'] in ('Summary',)), default=0)
    labels = []
    for left, right in bounds:
        ws = [w for w in words if title_y + 5 < mid_y(w) < head_y - 3 and left <= centre(w) <= right
              and w['text'] not in ('Discharge', 'Date', 'North', 'Central', 'South', 'Basin',
                                    m.group(1), m.group(2))]
        ws.sort(key=lambda w: (round(mid_y(w)), w['x0']))
        labels.append(' '.join(w['text'] for w in ws))

    # day rows: integers 1-31 in the left margin (left of the first Hours column), below the header
    margin = min(w['x0'] for w in hours) - 10
    days = [(int(w['text']), mid_y(w)) for w in words
            if re.fullmatch(r'\d{1,2}', w['text']) and w['x1'] < margin and mid_y(w) > head_y
            and 1 <= int(w['text']) <= 31]
    total_y = next((mid_y(w) for w in words if w['text'].lower().startswith('total') and w['x1'] < margin), None)
    if not days:
        return [], []
    step = (days[-1][1] - days[0][1]) / max(1, days[-1][0] - days[0][0])
    rows_y = days + ([('Total', total_y)] if total_y else [])

    cells = {}
    for w in words:
        v = w['text'].replace(',', '')
        if not NUM.match(v) or mid_y(w) <= head_y or w['x1'] < margin:
            continue
        row, ry = min(rows_y, key=lambda r: abs(r[1] - mid_y(w)))
        if abs(ry - mid_y(w)) > step * 0.6:
            continue
        x = centre(w)
        best = None
        for i, (hx, cx) in enumerate(pairs):
            for kind, ax in (('hours', hx), ('count', cx)):
                if ax is not None and (best is None or abs(ax - x) < best[0]):
                    best = (abs(ax - x), i, kind)
        if best is None or best[0] > 30:
            continue    # e.g. the Overall Count column
        cells.setdefault((row, best[1]), {})[best[2]] = float(v)

    out, checks = [], []
    for i, (hx, cx) in enumerate(pairs):
        h_sum = round(sum(c.get('hours', 0) for (r, j), c in cells.items() if j == i and r != 'Total'), 2)
        n_sum = sum(c.get('count', 0) for (r, j), c in cells.items() if j == i and r != 'Total')
        tot = cells.get(('Total', i), {})
        checks.append({'month': month, 'source': source, 'page': page_no, 'basin': basin_for(hx),
                       'column': labels[i], 'hours_rows': h_sum, 'hours_total': tot.get('hours'),
                       'count_rows': n_sum, 'count_total': tot.get('count'),
                       'agrees': (tot.get('hours') is None or abs(h_sum - tot['hours']) < 0.06)
                                 and (tot.get('count') is None or n_sum == tot['count'])})
        for (r, j), c in sorted(cells.items(), key=lambda kv: str(kv[0][0]).zfill(5)):
            if j != i or r == 'Total':
                continue
            if c.get('hours', 0) > 0 or c.get('count', 0) > 0:
                out.append({'date': f"{month}-{r:02d}", 'basin': basin_for(hx), 'column': labels[i],
                            'discharge_hours': c.get('hours'), 'discharge_count': c.get('count'),
                            'source': source, 'page': page_no})
    return out, checks


def parse(paths):
    rows, checks = [], []
    for p in paths:
        with pdfplumber.open(p) as pdf:
            for n, page in enumerate(pdf.pages, 1):
                r, c = parse_page(page, p.split('/')[-1], n)
                rows += r
                checks += c
    return rows, checks


if __name__ == '__main__':
    args = sys.argv[1:]
    checks_path = None
    if args[:1] == ['--checks']:
        checks_path, args = args[1], args[2:]
    rows, checks = parse(args)
    if checks_path:
        with open(checks_path, 'w', newline='') as fh:
            cw = csv.DictWriter(fh, fieldnames=list(checks[0]))
            cw.writeheader()
            cw.writerows(checks)
    w = csv.DictWriter(sys.stdout, fieldnames=['date', 'basin', 'column', 'discharge_hours',
                                               'discharge_count', 'source', 'page'])
    w.writeheader()
    w.writerows(rows)
    bad = [c for c in checks if not c['agrees']]
    print(f'{len(rows)} rows; {len(checks)} columns checked against Total rows, {len(bad)} disagree',
          file=sys.stderr)
    for c in bad:
        print('  DISAGREES', c, file=sys.stderr)
