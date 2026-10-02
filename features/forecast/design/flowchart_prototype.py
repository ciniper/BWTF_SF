"""Scratch prototype of the merged stages flowchart (figure 1). Spec = GRID + NODES + EDGES as data; renderer + geometry check."""
import re, html, itertools, json, sys
R = '/Users/chasecooper/Personal/BWTF-stages'
LOGO = 'file://' + R + '/app/static/logos/'
OUT = '/private/tmp/claude-501/-Users-chasecooper-Personal-BWTF/e8d72723-01c5-4b30-818a-c20681aa15ae/scratchpad/synth/'
esc = html.escape

# ---------------- grid ----------------
VIEW = (1290, 820)
COLX = [44, 296, 548, 800, 1052]; CW = 222
ROWS = {  # row index -> (y, h)
    0: (50, 80),     # TRUTH
    1: (220, 152),   # MODEL
    2: (416, 56),    # LIVE a
    3: (480, 56),    # LIVE b
    4: (544, 56),    # LIVE c
    5: (632, 54),    # NOT SCORED chips
    6: (700, 62),    # CLAIM strip
}
BANDS = [(42, 96, 'TRUTH'), (212, 168, 'MODEL'), (408, 200, 'LIVE')]
HEAD = [('S1 · RAIN', 'per rain gauge · day · lead 0–5'), ('S2 · BASIN OVERFLOW', 'per basin · day'),
        ('S3 · ZONE OVERFLOW', 'per zone · day'), ('S4 · WATER QUALITY', 'per zone · sampled day'), ('OUT · OVERFLOW RISK', 'per zone · 6 days')]

def geom(n):
    x = COLX[n['col']]; w = CW if not n.get('span') else COLX[n['col'] + n['span'] - 1] + CW - x
    y, h = ROWS[n['row']]
    if n.get('rows'):
        h = ROWS[n['row'] + n['rows'] - 1][0] + ROWS[n['row'] + n['rows'] - 1][1] - y
    if n['id'] == 'x.claim':
        x, w = 44, COLX[4] + CW - 44
    return x, y, w, h

D = '—'
NODES = [
    # TRUTH row
    dict(id='t.s1', kind='truth', stage='S1', col=0, row=0, logo='noaa.svg', title='Rain gauges', lines=['Downtown · Oceanside', 'dead-gauge days masked']),
    dict(id='t.s2', kind='truth', stage='S2', col=1, row=0, logo='water-boards.png', title='Overflow ledger', lines=['CIWQS, by basin', '112 overflow days']),
    dict(id='t.s3', kind='truth', stage='S3', col=2, row=0, logo='water-boards.png', title='Ledger, by zone', lines=['outfall → its stations', 'same day, covered days']),
    dict(id='t.s4', kind='truth', stage='S4', col=3, row=0, logo='sf-city-seal.png', title='Lab samples', lines=['any station over', 'the state standard']),
    dict(id='t.out', kind='truth', stage='OUT', col=4, row=0, icon='circle-alert', title='Bad beach days', lines=['overflow day, or over', 'standard within 7 days']),
    # MODEL row
    dict(id='m.s1', kind='stage', stage='S1', col=0, row=1, icon='cloud-rain', title='Rain', q='how much rain, and when?',
         lines=['ICON via Open-Meteo, one point', 'today → 5 days ahead'], metric='wet-day error, inches', chips=('floor ' + D, 'lead 1 ' + D)),
    dict(id='m.s2', kind='stage', stage='S2', col=1, row=1, icon='gauge', title='Basin overflow', q='does the sewer overflow?',
         lines=["chance + size, per basin", "SFPUC's four basins"], metric='skill vs climatology (BSS)', chips=('oracle ' + D, 'chained ' + D)),
    dict(id='m.s3', kind='stage', stage='S3', col=2, row=1, icon='git-branch', title='Zone overflow', q='which zones does it reach?',
         inset=True, metric='skill (BSS) · oracle on the Westside split', chips=('oracle ' + D, 'chained ' + D)),
    dict(id='m.s4', kind='stage', stage='S4', col=3, row=1, icon='flask', title='Water quality', q='is the water over standard?',
         lines=['overflow history + rain', 'per zone, days 0–7 after'], metric='skill vs climatology (BSS)', chips=('oracle ' + D, 'chained ' + D)),
    dict(id='m.out', kind='output', stage='OUT', col=4, row=1, icon='map-pin', title='Overflow risk', q='what people see',
         lines=['% per zone · every 30 min'], metric='skill by lead, chained', lead=[None] * 6),
    # LIVE rows
    dict(id='l.rain', kind='live', stage='S1', col=0, row=2, logo='noaa.svg', title='Rain so far', lines=['gauges, then SFO today']),
    dict(id='l.map', kind='live', stage='S5', col=1, row=2, logo='sfpuc.png', title='SFPUC beach map', lines=['CSO flags · outfall names']),
    dict(id='l.lab', kind='live', stage='S5', col=1, row=3, logo='sf-city-seal.png', title='Lab results, new', lines=['map 1–2 days · DataSF ~5']),
    dict(id='l.perfect', kind='input', stage='S5', col=1, row=4, icon='circle-check', title='Perfect feed', lines=['filed overflows, on time'], dashed=True),
    dict(id='m.s5', kind='stage', stage='S5', col=2, row=2, span=2, rows=2, icon='satellite-dish', title='Live corrections',
         q='does what was seen improve what comes after?',
         lines=['a CSO flag or named outfall → its zone, after the split', 'a lab result → that day’s water quality'],
         metric='change in Brier on the days after', chips=('oracle ' + D, 'chained ' + D), wide=True),
    dict(id='o.line', kind='decision', stage='ALERT', col=4, row=2, rows=2, icon='scales', title='Alert line per zone',
         lines=['the only place cost is used:', 'a miss costs 2 false alarms', 'alert at calibrated risk ≥ 1 in 3']),
    # NOT SCORED chips
    dict(id='x.s1', kind='exclusion', stage='S1', col=0, row=5, title='not scored', lines=['dead-gauge days {n} · missing {n}', 'model gaps {n} · peak hours']),
    dict(id='x.s2', kind='exclusion', stage='S2', col=1, row=5, title='not scored', lines=['no filing {n} · feed archive {n}', 'carry-over {n} · days the fit saw']),
    dict(id='x.s3', kind='exclusion', stage='S3', col=2, row=5, title='not scored', lines=['identity links: checked only', 'no basin overflow (oracle) {n}']),
    dict(id='x.s4', kind='exclusion', stage='S4', col=3, row=5, title='not scored', lines=['days nobody sampled {n}', 'overflow history unknown {n}']),
    dict(id='x.out', kind='exclusion', stage='OUT', col=4, row=5, title='not scored', lines=['unsampled days after one {n}', 'overflow history unknown {n}']),
    dict(id='x.claim', kind='exclusion', stage='CLAIM', col=0, row=6, span=5, title='THE FORECAST DOES NOT CLAIM',
         pills=['dry-weather exceedances {n}', 'rain runoff, no overflow {n}', 'other causes posted {n}', 'shore far from a station {n}', "SFPUC's posting decisions", 'the hour, the size, past day 5']),
]
N = {n['id']: n for n in NODES}

def bx(i): return geom(N[i])

def diag(x0, y0, x1, y1): return [(x0, y0), (x1, y1)]

def col_x(c, off): return COLX[c] + off
T_BOT = ROWS[0][0] + ROWS[0][1]      # 130
M_TOP = ROWS[1][0]                   # 220
M_BOT = ROWS[1][0] + ROWS[1][1]      # 372
CHAIN_Y = M_TOP + 56                 # 276
L_TOP = ROWS[2][0]                   # 416

EDGES = []
def E(id_, a, b, style, pts, label=None, lab=None):
    EDGES.append(dict(id=id_, frm=a, to=b, style=style, pts=pts, label=label, lab=lab))

# chain
for i, (a, b) in enumerate([('m.s1', 'm.s2'), ('m.s2', 'm.s3'), ('m.s3', 'm.s4'), ('m.s4', 'm.out')]):
    E(f'c{i+1}{i+2}', a, b, 'data', [(COLX[i] + CW, CHAIN_Y), (COLX[i + 1], CHAIN_Y)])
# fit (down) S2..S4 at col+64
for k, c in ((2, 1), (3, 2), (4, 3)):
    E(f'f{k}', f't.s{k}', f'm.s{k}', 'fit', [(col_x(c, 64), T_BOT), (col_x(c, 64), M_TOP)], 'fit' if k == 2 else None, (col_x(c, 58), 168, 'end'))
# score (up) all at col+104
for k, (c, a, b) in enumerate([(0, 'm.s1', 't.s1'), (1, 'm.s2', 't.s2'), (2, 'm.s3', 't.s3'), (3, 'm.s4', 't.s4'), (4, 'm.out', 't.out')]):
    E(f's{k+1}', a, b, 'score', [(col_x(c, 104), M_TOP), (col_x(c, 104), T_BOT)], 'scored' if k < 2 else None, (col_x(c, 110), 168, 'start'))
# oracle diagonals: truth k -> model k+1
for k, (c, lab) in enumerate([(0, 'true rain'), (1, 'true basin overflows'), (2, 'true zone overflows')]):
    x0, y0, x1, y1 = COLX[c] + CW - 14, T_BOT, COLX[c + 1] + 30, M_TOP
    E(f'o{k+2}', f't.s{k+1}', f'm.s{k+2}', 'oracle', diag(x0, y0, x1, y1), lab, (x0 + 35, 186, 'end'))
# live
E('l1', 'l.rain', 'm.s1', 'live', [(155, L_TOP), (155, M_BOT)], 'observed rain', (161, 398, 'start'))
E('l2', 'l.map', 'm.s5', 'live', [(COLX[1] + CW, 444), (COLX[2], 444)])
E('l3', 'l.lab', 'm.s5', 'live', [(COLX[1] + CW, 508), (COLX[2], 508)])
E('o5', 'l.perfect', 'm.s5', 'oracle', [(COLX[1] + CW, 572), (584, 572), (584, ROWS[2][0] + 120)])
E('l4', 'm.s5', 'm.s3', 'live', [(659, L_TOP), (659, M_BOT)], 'a flag or a named outfall', (665, 398, 'start'))
E('l5', 'm.s5', 'm.s4', 'live', [(911, L_TOP), (911, M_BOT)], 'a lab result', (917, 398, 'start'))
# decision
E('d1', 'm.out', 'o.line', 'decision', [(1163, M_BOT), (1163, L_TOP)], 'calibrated %', (1169, 398, 'start'))

# ---------------- render ----------------
def tile(x, y, n, size=40):
    if n.get('logo'):
        return (f'<rect class="tile logo" x="{x}" y="{y}" width="{size}" height="{size}" rx="11"/>'
                f'<image href="{LOGO}{n["logo"]}" x="{x+6}" y="{y+6}" width="{size-12}" height="{size-12}" preserveAspectRatio="xMidYMid meet"/>')
    return (f'<rect class="tile" x="{x}" y="{y}" width="{size}" height="{size}" rx="11"/>'
            f'<use class="icn" href="#i-{n["icon"]}" x="{x+8}" y="{y+8}" width="{size-16}" height="{size-16}"/>')

def inset(x, y):
    """basins (left) -> zones (right), 5 links drawn from the geography; Westside links blue (the only split)."""
    B = ['Westside', 'North Shore', 'Central', 'South']; Z = ['Ocean Beach', 'Baker & China', 'North Beaches', 'East Beaches']
    L = [(0, 0, True), (0, 1, True), (1, 2, False), (2, 3, False), (3, 3, False)]
    ys = [y + 6 + 13 * i for i in range(4)]
    xb, xz = x + 84, x + 128
    o = []
    for b, z, split in L:
        o.append(f'<line class="{"lk s" if split else "lk"}" x1="{xb}" y1="{ys[b]}" x2="{xz}" y2="{ys[z]}"/>')
    for i, t in enumerate(B):
        o.append(f'<circle class="nd" cx="{xb}" cy="{ys[i]}" r="3"/><text class="mini" x="{xb-7}" y="{ys[i]+3.5}" text-anchor="end">{esc(t)}</text>')
    for i, t in enumerate(Z):
        o.append(f'<circle class="nd z" cx="{xz}" cy="{ys[i]}" r="3"/><text class="mini" x="{xz+7}" y="{ys[i]+3.5}">{esc(t)}</text>')
    return ''.join(o)

def render_node(n):
    x, y, w, h = geom(n); k = n['kind']; o = []
    cls = {'truth': 'box truth', 'stage': 'box hub', 'output': 'box hub', 'live': 'box live', 'input': 'box oracle',
           'decision': 'box dec', 'exclusion': 'xbox'}[k]
    o.append(f'<a href="#{n["id"]}"><title>{esc(n["id"])}</title><rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{12 if k=="exclusion" else 14}"/>')
    if n['id'] == 'x.claim':
        o.append(f'<text class="col" x="{x+16}" y="{y+19}">{esc(n["title"])}</text>')
        pw = (w - 32 - 5 * 8) / 6
        for i, p in enumerate(n['pills']):
            px = x + 16 + i * (pw + 8)
            o.append(f'<rect class="pill" x="{px:.1f}" y="{y+28}" width="{pw:.1f}" height="24" rx="12"/><text class="pt" x="{px+pw/2:.1f}" y="{y+44}" text-anchor="middle">{esc(p)}</text>')
        o.append('</a>'); return ''.join(o)
    if k == 'exclusion':
        o.append(f'<text class="xh" x="{x+14}" y="{y+17}">{esc(n["title"])}</text>')
        for i, l in enumerate(n['lines']):
            o.append(f'<text class="xs" x="{x+14}" y="{y+32+i*14}">{esc(l)}</text>')
        o.append('</a>'); return ''.join(o)
    if k in ('stage', 'output'):
        o.append(tile(x + 12, y + 12, n))
        st = n['stage'] + ' · ' if n['stage'].startswith('S') else ''
        o.append(f'<text class="t big" x="{x+62}" y="{y+30}">{esc(st + n["title"])}</text>')
        o.append(f'<text class="q" x="{x+62}" y="{y+47}">{esc(n["q"])}</text>')
        if n.get('inset'):
            o.append(inset(x, y + 54))
        for i, l in enumerate(n.get('lines', [])):
            o.append(f'<text class="s" x="{x+14}" y="{y+(66 if n.get("wide") else 72)+i*16}">{esc(l)}</text>')
        if n.get('chips'):
            cy = y + h - (26 if n.get('wide') else 30)
            o.append(f'<text class="m" x="{x+14}" y="{cy+14 if n.get("wide") else cy-6}">{esc(n["metric"])}</text>')
            a, b = n['chips']
            cw = (w - 28 - 8) / 2 if not n.get('wide') else 118
            cx0 = x + 14 if not n.get('wide') else x + w - 14 - 2 * cw - 8
            o.append(f'<rect class="chip or" x="{cx0}" y="{cy}" width="{cw}" height="20" rx="10"/><text class="ct or" x="{cx0+cw/2}" y="{cy+14}" text-anchor="middle">{esc(a)}</text>')
            o.append(f'<rect class="chip ch" x="{cx0+cw+8}" y="{cy}" width="{cw}" height="20" rx="10"/><text class="ct ch" x="{cx0+cw*1.5+8}" y="{cy+14}" text-anchor="middle">{esc(b)}</text>')
        if n.get('lead'):
            cy = y + h - 48
            o.append(f'<text class="m" x="{x+14}" y="{cy-6}">{esc(n["metric"])}</text>')
            for i, v in enumerate(n['lead']):
                bxx = x + 14 + i * 33
                bh = 4 if v is None else 26 * v
                o.append(f'<rect class="lead{" na" if v is None else ""}" x="{bxx}" y="{cy+26-bh}" width="24" height="{bh:.1f}" rx="3"/><text class="ax" x="{bxx+12}" y="{cy+38}" text-anchor="middle">{"today" if i==0 else "+"+str(i)}</text>')
    else:
        o.append(tile(x + 10, y + (min(h, 56) - 40) / 2 + (6 if h > 60 else 0), n))
        lines = n['lines']
        if k == 'decision':
            o.append(f'<text class="t" x="{x+60}" y="{y+38}">{esc(n["title"])}</text>')
            for i, l in enumerate(lines):
                o.append(f'<text class="s" x="{x+14}" y="{y+72+i*17}">{esc(l)}</text>')
        elif h > 60:
            o.append(f'<text class="t" x="{x+60}" y="{y+28}">{esc(n["title"])}</text>')
            for i, l in enumerate(lines):
                o.append(f'<text class="s" x="{x+60}" y="{y+46+i*15}">{esc(l)}</text>')
        else:
            dy = -7 if len(lines) > 1 else 0
            o.append(f'<text class="t" x="{x+60}" y="{y+h/2-3+dy}">{esc(n["title"])}</text>')
            for i, l in enumerate(lines):
                o.append(f'<text class="s" x="{x+60}" y="{y+h/2+14+i*15+dy}">{esc(l)}</text>')
    o.append('</a>'); return ''.join(o)

STYLE = {'data': ('flow', 'pa'), 'fit': ('fit', 'pg'), 'score': ('grade', 'pk'), 'oracle': ('orc', 'po'), 'live': ('live', 'pl'), 'decision': ('dec', 'pd')}

def render_edges():
    o = []
    for e in EDGES:
        cls, mk = STYLE[e['style']]
        d = 'M' + ' L'.join(f'{a},{b}' for a, b in e['pts'])
        o.append(f'<path class="{cls}" d="{d}" marker-end="url(#{mk})"><title>{esc(e["id"])}</title></path>')
        if e['label']:
            lx, ly, an = e['lab']
            o.append(f'<text class="el {cls}l" x="{lx}" y="{ly}" text-anchor="{an}">{esc(e["label"])}</text>')
    return ''.join(o)

def svg():
    s = [f'<svg class="pipe" viewBox="0 0 {VIEW[0]} {VIEW[1]}" role="img"><title>Five stages from rain to a beach percentage, each scored twice</title><defs>']
    for mid, col in (('pa', '#0072BC'), ('pg', '#8a949b'), ('pk', '#8a949b'), ('po', '#237059'), ('pl', '#d4763a'), ('pd', '#26272a')):
        s.append(f'<marker id="{mid}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="{col}"/></marker>')
    s.append('</defs>')
    for y, h, lab in BANDS:
        s.append(f'<rect class="band" x="8" y="{y}" width="1274" height="{h}" rx="18"/><text class="rowl" transform="translate(26,{y+h/2}) rotate(-90)" text-anchor="middle">{lab}</text>')
    for i, (a, b) in enumerate(HEAD):
        s.append(f'<text class="col" x="{COLX[i]}" y="18">{esc(a)}</text><text class="q" x="{COLX[i]}" y="33">{esc(b)}</text>')
    s.append(render_edges())
    for n in NODES:
        s.append(render_node(n))
    # legend
    lg = [('flow', 'pa', 'flows every 30 minutes'), ('orc', 'po', 'oracle: fed the true input'), ('fit', 'pg', 'fits the stage'),
          ('grade', 'pk', 'scored on days the fit never saw'), ('live', 'pl', 'an observation replaces a prediction'), ('dec', 'pd', 'decision')]
    x = 44
    for cls, mk, t in lg:
        s.append(f'<path class="{cls}" d="M{x},806 H{x+34}" marker-end="url(#{mk})"/><text class="lg" x="{x+42}" y="810">{esc(t)}</text>')
        x += 58 + len(t) * 6.2
    s.append('<rect class="chip or" x="44" y="772" width="86" height="20" rx="10"/><text class="ct or" x="87" y="786" text-anchor="middle">oracle</text>'
             '<rect class="chip ch" x="138" y="772" width="86" height="20" rx="10"/><text class="ct ch" x="181" y="786" text-anchor="middle">chained</text>'
             '<text class="lg" x="234" y="786">every stage is scored twice: on its true input (oracle) and on the real output of the stage before it (chained)</text>'
             '<rect class="xbox" x="860" y="772" width="86" height="20" rx="10"/><text class="xh" x="903" y="786" text-anchor="middle">not scored</text>'
             '<text class="lg" x="956" y="786">each rule has a count; ids in tooltips</text>')
    s.append('</svg>')
    return ''.join(s)

EXTRA = """.band{fill:#f7fafc;stroke:none}.rowl{font-size:11px;font-weight:700;letter-spacing:.14em;fill:#8a949b}
.q{font-size:12px;fill:#54576F;font-style:italic}.t.big{font-size:17px}.m{font-size:11px;fill:#8a949b;letter-spacing:.02em}
.box.truth{stroke:#b9c7cf;stroke-width:1.5}.box.live{fill:#fff;stroke:#efc9ad;stroke-width:1.5}.box.oracle{stroke:#237059;stroke-width:1.5;stroke-dasharray:3 3}
.box.dec{stroke:#26272a;stroke-width:2}.xbox{fill:#f3f6f9;stroke:none}.xh{font-size:10.5px;font-weight:700;letter-spacing:.12em;fill:#8a949b;text-transform:uppercase}
.xs{font-size:11.5px;fill:#54576F}.pill{fill:#f3f6f9;stroke:#d9e4e8}.pt{font-size:11.5px;fill:#54576F}
.chip.or{fill:#fff;stroke:#237059;stroke-width:1.5}.chip.ch{fill:#0072BC}.ct{font-size:11px;font-weight:700}.ct.or{fill:#237059}.ct.ch{fill:#fff}
.grade{fill:none;stroke:#8a949b;stroke-width:1.5}.orc{fill:none;stroke:#237059;stroke-width:2;stroke-dasharray:2 4;stroke-linecap:round}.live{fill:none;stroke:#d4763a;stroke-width:2}
.dec{fill:none;stroke:#26272a;stroke-width:2}
.el{font-size:11px;font-weight:700}.flowl{fill:#0072BC}.fitl,.gradel{fill:#8a949b}.orcl{fill:#237059;font-style:italic}.livel{fill:#d4763a}.decl{fill:#26272a}
.lead{fill:#0072BC;opacity:.85}.lead.na{fill:#d9e4e8;opacity:1}.ax{font-size:10.5px;fill:#8a949b}
.lk{stroke:#b9c7cf;stroke-width:1.5}.lk.s{stroke:#0072BC;stroke-width:2}.nd{fill:#54576F}.nd.z{fill:#0072BC}.mini{font-size:10px;fill:#54576F}"""

# ---------------- geometry check ----------------
def boxes():
    return {n['id']: geom(n) for n in NODES}

def seg_inter(p1, p2, p3, p4):
    def orient(a, b, c):
        v = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]); return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    o1, o2, o3, o4 = orient(p1, p2, p3), orient(p1, p2, p4), orient(p3, p4, p1), orient(p3, p4, p2)
    return o1 * o2 < 0 and o3 * o4 < 0

def seg_hits_box(a, b, bxr, shrink=0.5):
    x, y, w, h = bxr; x += shrink; y += shrink; w -= 2*shrink; h -= 2*shrink
    # sample along the segment
    for t in [i / 200 for i in range(1, 200)]:
        px, py = a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t
        if x < px < x + w and y < py < y + h:
            return True
    return False

def check():
    B = boxes(); bad = []
    for a, b in itertools.combinations(B, 2):
        ax, ay, aw, ah = B[a]; bx_, by, bw, bh = B[b]
        if ax < bx_ + bw and bx_ < ax + aw and ay < by + bh and by < ay + ah:
            bad.append(('overlap', a, b))
    for e, f in itertools.combinations(EDGES, 2):
        for s in zip(e['pts'], e['pts'][1:]):
            for t in zip(f['pts'], f['pts'][1:]):
                if seg_inter(*s, *t):
                    bad.append(('cross', e['id'], f['id']))
    for e in EDGES:
        for s in zip(e['pts'], e['pts'][1:]):
            for nid, r in B.items():
                if nid in (e['frm'], e['to']):
                    continue
                if seg_hits_box(*s, r):
                    bad.append(('through', e['id'], nid))
    # labels: rough bbox, must not hit a box or a path other than own
    for e in EDGES:
        if not e['label']:
            continue
        lx, ly, an = e['lab']; wdt = len(e['label']) * 6.0
        x0 = lx - wdt if an == 'end' else (lx - wdt / 2 if an == 'middle' else lx)
        r = (x0, ly - 10, wdt, 12)
        for nid, b in B.items():
            bx_, by, bw, bh = b
            if r[0] < bx_ + bw and bx_ < r[0] + r[2] and r[1] < by + bh and by < r[1] + r[3]:
                bad.append(('label-in-box', e['id'], nid))
        for f in EDGES:
            if f is e:
                continue
            for s in zip(f['pts'], f['pts'][1:]):
                if seg_hits_box(*s, r, shrink=0):
                    bad.append(('label-on-edge', e['id'], f['id']))
    return bad

if __name__ == '__main__':
    print('problems:', check())
    CSS = open('/private/tmp/claude-501/-Users-chasecooper-Personal-BWTF/e8d72723-01c5-4b30-818a-c20681aa15ae/scratchpad/design/fig/base.css').read()
    sprite = re.sub(r"\{#.*?#\}", "", open(R + '/app/templates/_icons.html').read(), flags=re.S)
    open(OUT + 'fig_v2.html', 'w').write(
        f'<!doctype html><html><head><meta charset="utf-8"><style>body{{margin:0;background:#fff;font-family:Roboto,Arial,sans-serif}}{CSS}{EXTRA}</style></head><body>{sprite}<div style="width:1320px;padding:10px">{svg()}</div></body></html>')
    spec = dict(view=VIEW, colx=COLX, cw=CW, rows=ROWS, nodes=[dict(n, box=geom(n)) for n in NODES],
                edges=[dict(id=e['id'], frm=e['frm'], to=e['to'], style=e['style'], d='M' + ' L'.join(f'{a},{b}' for a, b in e['pts']), label=e['label'], lab=e['lab']) for e in EDGES])
    json.dump(spec, open(OUT + 'fig_spec.json', 'w'), indent=1)
    print('ok')
