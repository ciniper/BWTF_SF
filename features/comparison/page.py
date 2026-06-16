"""Comparison page — Surfrider BWTF vs. public city water-quality data.

Owns /compare, /api/compare, /api/site-history. Implemented as a mixin on the
request handler in the Flask app (app/wsgi.py), which supplies the HTTP helpers
(self._send_json) and shared clients (self.combined_monitor, self.sfpuc_api).
"""
from urllib.parse import parse_qs, urlparse

from features.comparison.comparison import build_comparison, build_site_history

COMPARISON_MODAL_SCRIPT = """
<div id="hist-modal" class="modal" role="dialog" aria-modal="true" aria-labelledby="hist-title">
  <div class="modal__box">
    <div class="modal__head">
      <h2 id="hist-title">Site history</h2>
      <button class="modal__close" type="button" onclick="closeSite()">Close ✕</button>
    </div>
    <p class="modal__note" id="hist-note">Loading…</p>
    <div class="chart-wrap"><canvas id="hist-canvas"></canvas></div>
    <h3 class="modal__h3">Same-day samples — head to head</h3>
    <p class="modal__note" id="bar-note"></p>
    <div class="chart-wrap"><canvas id="hist-bar"></canvas></div>
    <p class="modal__note">BWTF = Surfrider volunteer lab · SFPUC = monitoring lab results, published via SF Gov Open Data. The real-time posting feed is status-only, so it has no historical numbers to plot. Dashed line = CA single-sample max; the same-day chart uses the worst (max) reading when a source sampled more than once that day.</p>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
let histChart, histBar;
function closeSite(){
  document.getElementById('hist-modal').classList.remove('open');
  if(histChart){histChart.destroy(); histChart=null;}
  if(histBar){histBar.destroy(); histBar=null;}
}
function openSite(site){
  const modal=document.getElementById('hist-modal');
  document.getElementById('hist-title').textContent=site;
  document.getElementById('hist-note').textContent='Loading…';
  modal.classList.add('open');
  var bt=document.getElementById('bacteria-type');
  fetch('/api/site-history?site='+encodeURIComponent(site)+'&analyte='+(bt?bt.value:'ENTERO'))
    .then(r=>r.json()).then(renderHist)
    .catch(e=>{document.getElementById('hist-note').textContent='Could not load history: '+e;});
}
function renderHist(d){
  if(d.ok===false){document.getElementById('hist-note').textContent=d.error||'No data available';return;}
  const std=d.standard;
  const toPts=a=>(a||[]).map(p=>({x:p.ts,y:p.value,raw:p.raw}));
  const bwtf=toPts(d.bwtf), city=toPts(d.city);
  const xs=[...bwtf,...city].map(p=>p.x), ys=[...bwtf,...city].map(p=>p.y);
  const parts=[bwtf.length+' BWTF samples', city.length+' city samples'];
  if(d.city_source) parts.push('city station '+d.city_source);
  document.getElementById('hist-note').textContent=parts.join(' · ');
  const thresh = xs.length ? [{x:Math.min.apply(null,xs),y:std},{x:Math.max.apply(null,xs),y:std}] : [];
  const ymax = Math.max(std*1.15, ys.length?Math.max.apply(null,ys)*1.1:std*1.15);
  if(histChart) histChart.destroy();
  histChart=new Chart(document.getElementById('hist-canvas'),{
    type:'line',
    data:{datasets:[
      {label:'BWTF (Surfrider)',data:bwtf,borderColor:'#317fb2',backgroundColor:'#317fb2',borderWidth:2,tension:0,spanGaps:true,pointRadius:3},
      {label:'SFPUC',data:city,borderColor:'#26272a',backgroundColor:'#26272a',borderWidth:2,tension:0,spanGaps:true,pointRadius:2},
      {label:'CA limit ('+std+')',data:thresh,borderColor:'#ff4100',borderDash:[6,5],borderWidth:1.5,pointRadius:0}
    ]},
    options:{parsing:false,responsive:true,maintainAspectRatio:false,
      interaction:{mode:'nearest',intersect:false},
      scales:{
        x:{type:'linear',ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:7,callback:v=>new Date(v).toLocaleDateString(undefined,{month:'short',year:'2-digit'})}},
        y:{type:'logarithmic',title:{display:true,text:(d.analyte||'Result')+' (MPN/100mL), log scale'}}
      },
      plugins:{legend:{position:'top'},tooltip:{callbacks:{
        title:items=>items.length?new Date(items[0].parsed.x).toLocaleDateString():'',
        label:it=>it.dataset.label+': '+((it.raw&&it.raw.raw!=null)?it.raw.raw:it.parsed.y)
      }}}
    }
  });
  const paired=d.paired||[];
  const barNote=document.getElementById('bar-note');
  if(histBar){histBar.destroy(); histBar=null;}
  if(!paired.length){
    barNote.textContent='No same-day samples in this window — the two programs sampled on different days.';
  }else{
    barNote.textContent=paired.length+' day(s) when both programs sampled this site (worst reading per day).';
    histBar=new Chart(document.getElementById('hist-bar'),{
      data:{labels:paired.map(p=>p.date),datasets:[
        {type:'bar',label:'BWTF (Surfrider)',data:paired.map(p=>p.bwtf),backgroundColor:'#317fb2'},
        {type:'bar',label:'SFPUC',data:paired.map(p=>p.city),backgroundColor:'#26272a'},
        {type:'line',label:'CA limit ('+std+')',data:paired.map(()=>std),borderColor:'#ff4100',borderDash:[6,5],borderWidth:1.5,pointRadius:0}
      ]},
      options:{responsive:true,maintainAspectRatio:false,
        scales:{y:{beginAtZero:true,title:{display:true,text:(d.analyte||'Result')+' (MPN/100mL)'}}},
        plugins:{legend:{position:'top'},tooltip:{callbacks:{
          label:it=>{ if(it.dataset.type==='line') return it.dataset.label;
            const raw=it.dataset.label.indexOf('BWTF')===0?paired[it.dataIndex].bwtf_raw:paired[it.dataIndex].city_raw;
            return it.dataset.label+': '+raw; }
        }}}
      }
    });
  }
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeSite();});
document.getElementById('hist-modal').addEventListener('click',function(e){if(e.target===this)closeSite();});
document.querySelectorAll('.row-click').forEach(function(el){
  el.addEventListener('click',function(){openSite(el.dataset.site);});
  el.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();openSite(el.dataset.site);}});
});
</script>
</body></html>"""


class ComparisonRoutes:
    """Comparison-page routes, mixed into the unified server handler."""

    def _analyte_param(self):
        return (parse_qs(urlparse(self.path).query).get("analyte") or ["ENTERO"])[0]

    def _compare_data(self, analyte="ENTERO"):
        """Build the BWTF-vs-city comparison, reusing this handler's API clients."""
        return build_comparison(
            sf_gov_monitor=self.combined_monitor.sf_gov_monitor,
            sfpuc_api=self.sfpuc_api,
            analyte=analyte,
        )

    def send_api_compare(self):
        """Send the source-comparison data as JSON."""
        try:
            self._send_json(self._compare_data(self._analyte_param()))
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def send_api_site_history(self):
        """Send a single site's BWTF + city Enterococcus time series as JSON."""
        params = parse_qs(urlparse(self.path).query)
        site = (params.get("site") or [""])[0].strip()
        analyte = (params.get("analyte") or ["ENTERO"])[0]
        if not site:
            self._send_json({"ok": False, "error": "missing 'site' parameter"}, status=400)
            return
        try:
            data = build_site_history(site, sf_gov_monitor=self.combined_monitor.sf_gov_monitor, analyte=analyte)
            self._send_json(data)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def send_comparison_page(self):
        """Send the BWTF-vs-city comparison dashboard page."""
        try:
            html = self.generate_comparison_html(self._compare_data(self._analyte_param()))
        except Exception as e:
            html = f"<!doctype html><meta charset='utf-8'><h1>Comparison unavailable</h1><pre>{e}</pre>"
        encoded = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def generate_comparison_html(self, data):
        """Render the source-comparison dashboard (Surfrider BWTF vs. public city data)."""
        std = data["standard"]
        s = data["summary"]
        limit = std["single_sample_max"]
        analyte_label = std["analyte"]
        analyte_code = std["code"]
        bwtf_measures = data.get("bwtf_measures", True)
        analyte_options = "".join(
            f'<option value="{a["code"]}"{" selected" if a["code"] == analyte_code else ""}>{a["label"]}</option>'
            for a in data.get("analytes", [])
        )
        bwtf_note = (
            f'<p class="note-info">ℹ︎ BWTF measures Enterococcus only — showing the SFPUC {analyte_label} '
            f'readings (graded against {limit} MPN/100mL). Switch to Enterococcus for the head-to-head comparison.</p>'
        ) if not bwtf_measures else ""

        def pill(exceeds, raw):
            if raw is None:
                return '<span class="pill pill--na">no data</span>'
            cls, label = ("pill--bad", "exceeds") if exceeds else ("pill--ok", "within")
            return f'<span class="pill {cls}">{raw}<small>{label}</small></span>'

        def sfpuc_pill(status):
            mapping = {
                "cso": ("pill--bad", "CSO"),
                "posted": ("pill--warn", "posted"),
                "safe": ("pill--ok", "safe"),
                "not_sampled": ("pill--na", "not sampled"),
                "not_routinely_sampled": ("pill--na", "not routine"),
            }
            cls, label = mapping.get(status, ("pill--na", "—"))
            return f'<span class="pill {cls}">{label}</span>'

        def agreement_cell(r):
            if not r["both_have"]:
                if r["city_value"] is not None and r["bwtf_value"] is None:
                    return '<span class="agree agree--na">SFPUC only</span>'
                return '<span class="agree agree--na">— incomplete</span>'
            sub = []
            if r["value_delta"] is not None:
                sub.append(f"Δ {r['value_delta']:g}")
            if r["day_gap"] is not None:
                sub.append(f"{r['day_gap']}d apart")
            subline = f"<small>{' · '.join(sub)}</small>" if sub else ""
            if r["agree"]:
                txt = "Both exceed" if r["bwtf_exceeds"] else "Both within standard"
                return f'<span class="agree agree--yes">✓ {txt}</span>{subline}'
            return f'<span class="agree agree--no">⚠ Sources differ</span>{subline}'

        rows_html = ""
        for r in data["rows"]:
            if r['bwtf_raw'] is None:
                bwtf_html = '<span class="pill pill--na">not measured</span>'
            else:
                bwtf_html = (f"{pill(r['bwtf_exceeds'], r['bwtf_raw'])}"
                             f"<small class=\"date\">{r['bwtf_date'] or '—'}{(' · ' + r['bwtf_time']) if r['bwtf_time'] else ''}</small>")
            rows_html += f"""
              <tr class="row-click" data-site="{r['site_name']}" tabindex="0" role="button" aria-label="Show history for {r['site_name']}">
                <td class="site"><strong>{r['site_name']}</strong><span class="go">📈 view history →</span></td>
                <td>{bwtf_html}</td>
                <td>{pill(r['city_exceeds'], r['city_raw'])}<small class="date">{r['city_date'] or '—'}{(' · ' + r['city_source']) if r['city_source'] else ''}</small></td>
                <td>{sfpuc_pill(r['sfpuc_status'])}</td>
                <td>{agreement_cell(r)}</td>
              </tr>"""

        css = """
          *{box-sizing:border-box}
          body{margin:0;background:#e2e8ee;color:#26272a;font-family:'Avenir Next','Trebuchet MS','Segoe UI',sans-serif;}
          a{color:#317fb2}
          .wrap{max-width:1040px;margin:0 auto;padding:24px}
          .hero{background:#1f6fb0;color:#fff;border-radius:24px;padding:28px;margin-bottom:22px}
          .hero h1{margin:6px 0 6px;font-size:28px;text-transform:uppercase;letter-spacing:.03em;color:#fff;-webkit-text-stroke:2px #26272a;paint-order:stroke fill}
          .hero p{margin:0;color:rgba(255,255,255,.85);font-size:15px;line-height:1.5;max-width:70ch}
          .hero ul{margin:8px 0 10px;padding-left:22px;color:rgba(255,255,255,.88);font-size:15px;line-height:1.55}
          .hero li{margin:2px 0}
          .back-to-dash{display:inline-block;margin:0 0 12px;padding:8px 14px;background:#343b44;color:#fff;border-radius:999px;text-decoration:none;font-weight:700;font-size:13px}
          .back-to-dash:hover{background:#26272a}
          .legend{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 18px}
          .legend div{background:#fff;border:1px solid #d9e4e8;border-radius:14px;padding:10px 14px;font-size:13px;color:#5e6a71}
          .legend b{color:#26272a}
          .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
          .card{background:#fff;border:1px solid #d9e4e8;border-radius:16px;padding:16px}
          .card .n{font-size:28px;font-weight:800;color:#26272a}
          .card .l{font-size:12px;color:#5e6a71;text-transform:uppercase;letter-spacing:.08em;margin-top:4px}
          .banner{border-radius:14px;padding:12px 16px;margin-bottom:18px;font-weight:600;font-size:14px}
          .banner--ok{background:rgba(37,214,112,.12);color:#146b37}
          .banner--warn{background:rgba(255,65,0,.10);color:#b5310a}
          .note-info{background:#e3eefb;color:#1f5e8f;border-radius:14px;padding:12px 16px;margin:0 0 16px;font-weight:600;font-size:14px}
          table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border:1px solid #d9e4e8;border-radius:18px;overflow:hidden}
          th,td{padding:13px 14px;text-align:left;border-bottom:1px solid #eef2f4;vertical-align:top}
          th{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#5e6a71;background:#f7fafb}
          tr:last-child td{border-bottom:none}
          td.site{min-width:180px}
          .pill{display:inline-flex;align-items:baseline;gap:6px;padding:5px 10px;border-radius:999px;font-weight:700;font-size:14px}
          .pill small{font-weight:600;font-size:11px;opacity:.8;text-transform:uppercase;letter-spacing:.04em}
          .pill--ok{background:#3c9160;color:#fff}
          .pill--bad{background:#d15c5c;color:#fff}
          .pill--warn{background:#b97e00;color:#fff}
          .pill--na{background:#eef2f4;color:#8a949b}
          .date{display:block;color:#8a949b;font-size:11px;margin-top:5px}
          .agree{font-weight:700;font-size:13px}
          .agree--yes{color:#146b37}.agree--no{color:#b5310a}.agree--na{color:#8a949b}
          .agree small{display:block;color:#8a949b;font-weight:600;margin-top:3px}
          .foot{color:#5e6a71;font-size:12px;line-height:1.6;margin-top:18px}
          .hint{display:flex;align-items:center;gap:6px;color:#5e6a71;font-size:13px;margin:0 0 10px}
          .bacteria-select{display:flex;align-items:center;gap:10px;margin:0 0 12px}
          .bacteria-select label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#5e6a71;font-weight:700}
          .bacteria-select select{font-size:14px;padding:8px 12px;border:1px solid #d9e4e8;border-radius:10px;background:#fff;color:#26272a;font-weight:600;cursor:pointer}
          tr.row-click{cursor:pointer}
          tr.row-click:hover{background:#f3f8fb}
          tr.row-click:focus{outline:2px solid #317fb2;outline-offset:-2px}
          td.site .go{display:block;color:#317fb2;font-size:11px;font-weight:700;margin-top:5px}
          .modal{position:fixed;inset:0;background:rgba(38,39,42,.55);display:none;align-items:center;justify-content:center;padding:20px;z-index:50}
          .modal.open{display:flex}
          .modal__box{background:#fff;border-radius:20px;max-width:780px;width:100%;padding:22px 24px;box-shadow:0 24px 60px rgba(0,0,0,.3);max-height:90vh;overflow:auto}
          .modal__h3{margin:20px 0 2px;font-size:15px;color:#26272a}
          .modal__head{display:flex;justify-content:space-between;align-items:center;gap:12px}
          .modal__head h2{margin:0;font-size:20px;color:#26272a}
          .modal__close{border:none;background:#eef2f4;border-radius:10px;padding:8px 12px;cursor:pointer;font-weight:700;color:#26272a;font-size:14px}
          .modal__note{color:#8a949b;font-size:12px;margin:6px 0 12px;line-height:1.5}
          .chart-wrap{position:relative;height:360px}
        """

        generated = data["generated_at"][:16].replace("T", " ")
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Source Comparison — BWTF vs. SFPUC | SF Beach Water Quality</title>
<style>{css}</style></head>
<body><div class="wrap">
  <a class="back-to-dash" href="/">← Dashboard</a>
  <div class="hero">
    <h1>Source Comparison</h1>
    <p>Independent <b>{analyte_label}</b> results for the same San Francisco beaches, measured by:</p>
    <ul class="sources">
      <li><b>Surfrider SF Blue Water Task Force (BWTF)</b></li>
      <li><b>San Francisco Public Utilities Commission (SFPUC)</b></li>
    </ul>
    <p>Both are graded against the California single-sample maximum of <b>{limit} MPN/100mL</b>.</p>
  </div>

  <div class="cards">
    <div class="card"><div class="n">{s['comparable_count']}/{s['site_count']}</div><div class="l">Sites compared</div></div>
    <div class="card"><div class="n">{s['agree_count']}</div><div class="l">Sources agree</div></div>
    <div class="card"><div class="n">{s['disagree_count']}</div><div class="l">Sources differ</div></div>
    <div class="card"><div class="n">{s['bwtf_exceed_count']} / {s['city_exceed_count']}</div><div class="l">BWTF / SFPUC exceed</div></div>
    <div class="card"><div class="n">{('—' if s['max_day_gap'] is None else str(s['max_day_gap']) + 'd')}</div><div class="l">Max sampling gap</div></div>
  </div>

  <p class="hint">📈 Click any site below for its {analyte_label} history — BWTF vs. SFPUC, over time.</p>

  <div class="bacteria-select">
    <label for="bacteria-type">Bacteria type</label>
    <select id="bacteria-type" aria-label="Bacteria type" onchange="location.href='/compare?analyte='+this.value">
      {analyte_options}
    </select>
  </div>

  {bwtf_note}

  <table>
    <thead><tr>
      <th>Site</th>
      <th>BWTF<br><small>MPN/100mL</small></th>
      <th>SFPUC<br><small>MPN/100mL</small></th>
      <th>Beach posting</th>
      <th>Agreement</th>
    </tr></thead>
    <tbody>{rows_html}
    </tbody>
  </table>

  <p class="foot">
    Generated {generated}. Both sources grade {analyte_label} against the CA single-sample max ({limit} MPN/100mL);
    "&lt;10" means below the lab's detection limit. Agreement compares whether each source's latest result meets
    that standard — not the exact numbers, which differ because sampling days and exact sample points differ.<br>
    Sources: <a href="https://bwtf.surfrider.org/explore/76" target="_blank" rel="noopener">Surfrider BWTF (SF)</a> ·
    <a href="https://data.sfgov.org/Energy-and-Environment/Beach-Water-Quality-Monitoring/v3fv-x3ux" target="_blank" rel="noopener">SF Gov Open Data</a> ·
    <a href="https://webapps.sfpuc.org/sapps/beachesandbay.html" target="_blank" rel="noopener">SFPUC Beach Map</a>
  </p>
</div>
"""
        return page + COMPARISON_MODAL_SCRIPT
