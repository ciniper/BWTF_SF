/* Live, read-only AQPI monitoring. Historical research context remains dated. */
(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('rain-context').textContent);
  const byId = id => document.getElementById(id);
  const feedInput = byId('rain-feed'), regionInput = byId('rain-region');
  const forecastInput = byId('rain-forecast');
  const shortDate = new Intl.DateTimeFormat('en-US', {timeZone:'America/Los_Angeles',month:'short',day:'numeric'});
  const shortTime = new Intl.DateTimeFormat('en-US', {timeZone:'America/Los_Angeles',hour:'numeric',minute:'2-digit'});
  const fullTime = new Intl.DateTimeFormat('en-US', {timeZone:'America/Los_Angeles',month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
  const format = value => value === null ? 'Unavailable' : value.toFixed(3);
  let map, pointsLayer, outline, requestId = 0, controller;
  const feeds = {}, metadata = {};
  let serverOffset = 0;
  const hasFeed = () => Boolean(feeds[feedInput.value]);
  const colors = ['#d5e9f3','#80bad8','#318bb8','#176c9e','#13466e','#082a48'];
  function selected() {
    const feed = feeds[feedInput.value];
    return {feed, region:feed.regions[regionInput.value], spec:data.regions[regionInput.value]};
  }
  function chartRows() {
    const {feed,region} = selected();
    const rows = feed.times.map((time,i) => ({time,value:region.observed[i],kind:'Radar estimate',step:feed.step_minutes}));
    if (forecastInput.checked) rows.push(...feed.forecast_times.map((time,i) => ({time,value:region.forecast[i],kind:'NBM forecast',step:60})));
    return rows;
  }
  function drawChart() {
    if (!hasFeed()) return;
    const rows = chartRows(), {spec,feed} = selected();
    const holder = byId('rain-chart');
    const width = Math.max(270,holder.clientWidth), height = 260;
    const left=45,right=10,top=27,bottom=51,plotW=width-left-right,plotH=height-top-bottom;
    const start = Date.parse(feed.start), end = Date.parse(rows[rows.length-1].time);
    const max = Math.max(.01,...rows.map(r => r.value === null ? 0 : r.value));
    const ceiling = Math.ceil(max*100)/100;
    const x = t => left+(t-start)/(end-start)*plotW;
    const y = v => top+plotH-v/ceiling*plotH;
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    svg.setAttribute('role','group');
    svg.setAttribute('aria-label',`${spec.label}: rainfall per interval in inches. Blue bars are radar estimates; amber bars are forecasts. Exact values are in the table below.`);
    function node(tag,attrs,text) {
      const el = document.createElementNS(ns,tag);
      Object.entries(attrs).forEach(([k,v]) => el.setAttribute(k,v));
      if (text !== undefined) el.textContent=text;
      svg.appendChild(el); return el;
    }
    node('text',{x:left,y:15},'Rain (inches per interval)');
    for(let i=0;i<=4;i++) {
      const value=ceiling*i/4,yy=y(value);
      node('line',{x1:left,x2:width-right,y1:yy,y2:yy,stroke:'#dce4e9'});
      node('text',{x:left-7,y:yy+4,'text-anchor':'end'},value.toFixed(2));
    }
    const forecastStart=x(Date.parse(feed.end));
    if(forecastInput.checked) {
      node('rect',{x:forecastStart,y:top,width:width-right-forecastStart,height:plotH,fill:'#b56c12','fill-opacity':'.05'});
      node('line',{x1:forecastStart,x2:forecastStart,y1:top,y2:top+plotH,stroke:'#996018','stroke-dasharray':'4 3'});
    }
    rows.forEach(row => {
      const intervalEnd=Date.parse(row.time),xx=x(intervalEnd-row.step*60000);
      const barWidth=Math.max(.7,x(intervalEnd)-xx-1);
      const missing=row.value===null;
      const color=missing?'#747c88':row.kind==='NBM forecast'?'#b56c12':'#176c9e';
      const label=`${fullTime.format(new Date(row.time))} · ${row.kind} · ${row.step} min · ${format(row.value)}${missing?'':' in'}`;
      const bar=node('rect',{x:xx,y:missing?top+plotH-5:y(row.value),width:barWidth,
        height:missing?5:Math.max(row.value===0?1:2,plotH-(y(row.value)-top)),fill:color,
        class:'rain-bar',tabindex:'0',role:'img','aria-label':label});
      const title=document.createElementNS(ns,'title');title.textContent=label;bar.appendChild(title);
      const show=() => {byId('rain-chart-readout').textContent=label;};
      bar.addEventListener('mouseenter',show);bar.addEventListener('focus',show);bar.addEventListener('click',show);
    });
    const tickCount=width<400?3:4;
    for(let i=0;i<tickCount;i++) {
      const t=start+(end-start)*i/(tickCount-1),xx=x(t);
      const anchor=i===0?'start':i===tickCount-1?'end':'middle';
      node('text',{x:xx,y:height-27,'text-anchor':anchor},shortDate.format(t));
      node('text',{x:xx,y:height-10,'text-anchor':anchor},shortTime.format(t));
    }
    holder.replaceChildren(svg);
    byId('rain-chart-title').textContent=forecastInput.checked?'Observed rain, then forecast':'Observed rain per interval';
    byId('rain-forecast-key').hidden=!forecastInput.checked;
    byId('rain-chart-readout').textContent=forecastInput.checked
      ? `Radar: ${feed.step_minutes}-minute amounts. Forecast: 60-minute amounts. Dashed line marks the end of observations.`
      : `Select or focus a bar. Each bar is ${feed.step_minutes} minutes; a grey mark means unavailable, not zero.`;
    const table = document.createDocumentFragment();
    rows.forEach(row => {
      const tr=document.createElement('tr');
      [fullTime.format(new Date(row.time)),`${row.kind} / ${row.step} min`,format(row.value)].forEach(value=>{
        const td=document.createElement('td');td.textContent=value;tr.appendChild(td);
      });table.appendChild(tr);
    });
    byId('rain-values').replaceChildren(table);
  }
  function initMap() {
    if(!window.L) {
      byId('rain-map').textContent='The map library could not load. Rainfall totals and the chart remain available.';
      return;
    }
    map=L.map('rain-map',{preferCanvas:true,scrollWheelZoom:false});
    const base=JSON.parse(byId('rain-basemap').textContent);
    L.tileLayer(base.url,base.options).addTo(map);
    pointsLayer=L.layerGroup().addTo(map);
  }
  function drawMap() {
    const {feed,spec}=selected();
    const [x0,x1,y0,y1]=spec.box;
    const thresholds=feedInput.value==='surfrider'?[.1,.25,.5,1,2]:[1,2,4,6,8];
    const labels=thresholds.map((n,i)=>i===0?`<${n}`:`${thresholds[i-1]}–${n}`).concat(`${thresholds[4]}+`);
    const legend=byId('rain-map-legend');legend.replaceChildren();
    const title=document.createElement('b');title.textContent=`${feedInput.value==='surfrider'?'24-hour':'7-day'} total (in)`;legend.appendChild(title);
    labels.forEach((label,i)=>{
      const el=document.createElement('span'),dot=document.createElement('i');
      dot.style.background=colors[i];el.append(dot,document.createTextNode(label));legend.appendChild(el);
    });
    if(!map)return;
    pointsLayer.clearLayers();
    feed.points.forEach(([lon,lat,total])=>{
      if(!(x0<=lon && lon<x1 && y0<=lat && lat<y1)) return;
      const index=total===null?-1:thresholds.filter(t=>total>=t).length;
      L.circleMarker([lat,lon],{radius:2,stroke:false,fillColor:index<0?'#747c88':colors[index],fillOpacity:.65})
        .bindPopup(`<b>${total===null?'Incomplete observations':total.toFixed(3)+' inches'}</b><br>Radar estimate · ${feedInput.value==='surfrider'?'24 hours':'7 days'}<br>Ending ${fullTime.format(new Date(feed.end))}<br>${lat.toFixed(4)}, ${lon.toFixed(4)}`)
        .addTo(pointsLayer);
    });
    if(outline)outline.remove();
    outline=L.rectangle([[y0,x0],[y1,x1]],{color:'#132f43',weight:1.5,fill:false,dashArray:'5 4',interactive:false}).addTo(map);
    map.fitBounds(outline.getBounds(),{padding:[16,16],animate:false});
    map.invalidateSize();
  }
  function update() {
    byId('rain-source-link').href=data.feeds[feedInput.value].url;
    if (!hasFeed()) {
      ['rain-total','rain-count','rain-complete'].forEach(id => {byId(id).textContent='—';});
      byId('rain-window').textContent='No current observations loaded for this feed.';
      byId('rain-chart').textContent='Current rainfall will appear here once AQPI data is available.';
      byId('rain-forecast-key').hidden=true;
      byId('rain-values').replaceChildren();
      byId('rain-map-legend').replaceChildren();
      byId('rain-chart-readout').textContent='';
      if (pointsLayer) pointsLayer.clearLayers();
      if (outline) {outline.remove(); outline=null;}
      return;
    }
    const {feed,region,spec}=selected();
    byId('rain-total').textContent=region.total===null?'Unavailable':region.total.toFixed(2);
    byId('rain-count').textContent=region.count.toLocaleString();
    byId('rain-complete').textContent=`${region.complete} / ${feed.times.length}`;
    byId('rain-window').textContent=`${spec.label} · ${fullTime.format(new Date(feed.start))} to ${fullTime.format(new Date(feed.end))}`;
    byId('rain-source-link').href=feed.url;
    drawChart();drawMap();
  }
  function showStatus(message) {
    const meta=metadata[feedInput.value], status=byId('rain-live-status');
    if (message) {status.textContent=message;status.dataset.warning='true';return;}
    if (!meta) return;
    const parts=[];
    if (meta.feed) {
      const age=Math.max(0,Math.round((Date.now()+serverOffset-Date.parse(meta.feed.end))/60000));
      const lag=`${Math.floor(age/60)}h ${age%60}m`;
      parts.push(meta.error?'Refresh failed — showing last successful download.':age>180?'Provider data delayed.':'Latest available AQPI data.');
      parts.push(`Observations end ${fullTime.format(new Date(meta.feed.end))} (${lag} behind now).`);
      parts.push(`Downloaded ${fullTime.format(new Date(meta.fetched_at))}.`);
    } else {parts.push('AQPI data is unavailable. No snapshot has been substituted.');}
    if(meta.checked_at) parts.push(`Provider checked ${fullTime.format(new Date(meta.checked_at))}.`);
    if(meta.error) parts.push(meta.error);
    status.textContent=parts.join(' ');
    status.dataset.warning=String(Boolean(meta.error) || (meta.feed && Date.now()+serverOffset-Date.parse(meta.feed.end)>10800000));
  }
  async function refresh() {
    const id=++requestId, key=feedInput.value;
    if(controller) controller.abort();
    controller=new AbortController();
    const currentController=controller;
    const timeout=setTimeout(()=>currentController.abort(),55000);
    byId('rain-refresh').disabled=true;
    byId('rain-refresh').textContent='Checking…';
    showStatus(hasFeed()?'Checking AQPI… Previous download remains visible.':'Loading latest AQPI observations…');
    try {
      const response=await fetch(`/alerts/api/rain-data?feed=${encodeURIComponent(key)}`,{cache:'no-store',signal:currentController.signal});
      if(response.status===401) throw new Error('Admin session expired. Reload this page to unlock it.');
      const result=await response.json();
      if(id!==requestId) return;
      if(!response.ok && response.status!==503) throw new Error('The rainfall service could not respond. Please retry.');
      serverOffset=Date.parse(result.server_time)-Date.now();
      // A different server instance may have no cache after a failed fetch. Keep
      // this browser's last good values, with the error and original fetch time.
      if(!result.feed && feeds[key]) {
        result.feed=feeds[key];result.fetched_at=metadata[key].fetched_at;
      }
      metadata[key]=result;
      if(result.feed) feeds[key]=result.feed;
      update();showStatus();
    } catch(error) {
      if(id!==requestId) return;
      const message=error.name==='AbortError'?'AQPI request timed out. Please retry.':error.message;
      if(metadata[key]) {metadata[key].error=message;showStatus();}
      else showStatus(`${message} No current rainfall data is available.`);
    } finally {
      clearTimeout(timeout);
      if(id===requestId) {byId('rain-refresh').disabled=false;byId('rain-refresh').textContent='Check for updates';}
    }
  }
  initMap();
  if(map) map.setView([37.757,-122.445],12);
  feedInput.addEventListener('change',()=>{update();refresh();});
  regionInput.addEventListener('change',update);
  forecastInput.addEventListener('change',drawChart);
  byId('rain-refresh').addEventListener('click',refresh);
  update();refresh();
  setInterval(()=>{if(!document.hidden && !byId('rain-refresh').disabled) refresh();},300000);
  setInterval(()=>{if(!document.hidden && !byId('rain-refresh').disabled) showStatus();},60000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden && !byId('rain-refresh').disabled) refresh();});
  if(window.ResizeObserver) new ResizeObserver(drawChart).observe(byId('rain-chart'));
})();
