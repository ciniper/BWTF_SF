"""Comparison page — Surfrider BWTF vs. public city water-quality data.

Owns /compare, /api/compare, /api/site-history. Implemented as a mixin on the
request handler in the Flask app (app/wsgi.py), which supplies the HTTP helpers
(self._send_json) and shared clients (self.combined_monitor, self.sfpuc_api).
"""
from urllib.parse import parse_qs, urlparse

from flask import render_template

from features.comparison.comparison import build_comparison, build_site_history

COMPARISON_MODAL_SCRIPT = """
<div id="hist-modal" class="modal" role="dialog" aria-modal="true" aria-labelledby="hist-title">
  <div class="modal__box">
    <div class="modal__head">
      <h2 id="hist-title">Site history</h2>
      <button class="modal__close" type="button" onclick="closeSite()">Close &times;</button>
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
                return f'<span class="agree agree--yes"><svg class="ic"><use href="#i-circle-check"/></svg> {txt}</span>{subline}'
            return f'<span class="agree agree--no"><svg class="ic"><use href="#i-triangle-alert"/></svg> Sources differ</span>{subline}'

        rows_html = ""
        for r in data["rows"]:
            if r['bwtf_raw'] is None:
                bwtf_html = '<span class="pill pill--na">not measured</span>'
            else:
                bwtf_html = (f"{pill(r['bwtf_exceeds'], r['bwtf_raw'])}"
                             f"<small class=\"date\">{r['bwtf_date'] or '—'}{(' · ' + r['bwtf_time']) if r['bwtf_time'] else ''}</small>")
            rows_html += f"""
              <tr class="row-click" data-site="{r['site_name']}" tabindex="0" role="button" aria-label="Show history for {r['site_name']}">
                <td class="site"><strong>{r['site_name']}</strong><span class="go"><svg class="ic"><use href="#i-chart-line"/></svg> view history →</span></td>
                <td>{bwtf_html}</td>
                <td>{pill(r['city_exceeds'], r['city_raw'])}<small class="date">{r['city_date'] or '—'}{(' · ' + r['city_source']) if r['city_source'] else ''}</small></td>
                <td>{sfpuc_pill(r['sfpuc_status'])}</td>
                <td>{agreement_cell(r)}</td>
              </tr>"""

        generated = data["generated_at"][:16].replace("T", " ")
        max_gap_display = '—' if s['max_day_gap'] is None else str(s['max_day_gap']) + 'd'
        return render_template(
            "comparison/page.html",
            analyte_label=analyte_label,
            limit=limit,
            s=s,
            max_gap_display=max_gap_display,
            analyte_options=analyte_options,
            bwtf_note=bwtf_note,
            rows_html=rows_html,
            generated=generated,
            modal_script=COMPARISON_MODAL_SCRIPT,
        )
