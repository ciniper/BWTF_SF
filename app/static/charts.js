/* BWTFCharts — the one place the site's bacteria charts are drawn from.
 * Colours, labels, the horizontal limit lines and three chart shapes:
 *   seriesChart  — results over time, log axis, one line per indicator/source
 *   barSeries    — the same series as bars, one slot per sample day (the Graphs page's Bars choice)
 *   pairedBars   — same-day head-to-head bars (city vs Surfrider)
 *   miniBars     — one sample's results, one small chart per indicator (the popover)
 * Chart.js loads on first use (ensureChart). Limits come from the API payloads,
 * which read shared/standards.py — never typed here. */
(function () {
  var CHART_SRC = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js";
  var COLORS = { ENTERO: "#0072BC", COLI_FECAL: "#d4763a", COLI_E: "#8f5cc7", COLI_TOTAL: "#26272a", BWTF: "#317fb2", BWTF_LIGHT: "#7ab8e0",
                 CITY: "#26272a", OVER: "#d15c5c", CAUTION: "#d4a017", LIMIT: "#ff4100", CAUTION_LINE: "#b97e00" };
  var LABELS = { ENTERO: "Enterococcus", COLI_FECAL: "Fecal coliform", COLI_E: "E. coli", COLI_TOTAL: "Total coliform" };
  var fmt = function (n) { return n == null ? "—" : Number(n).toLocaleString(); };
  var monthTick = function (v) { return new Date(v).toLocaleDateString(undefined, { month: "short", year: "2-digit" }); };
  var LOG_TICKS = [5, 10, 36, 100, 400, 1000, 10000];
  var pending = null;
  function ensureChart() {
    if (window.Chart) return Promise.resolve();
    if (!pending) pending = new Promise(function (res, rej) {
      var s = document.createElement("script"); s.src = CHART_SRC; s.onload = res;
      s.onerror = function () { pending = null; rej(new Error("chart library did not load")); }; document.head.appendChild(s); });
    return pending;
  }
  // Horizontal lines across the plot area. A line *dataset* needs two points and
  // vanished on one-bar charts; this draws regardless of how many points there are.
  var limitLines = { id: "limitLines", afterDatasetsDraw: function (chart, _args, opts) {
    var area = chart.chartArea, y = chart.scales.y, ctx = chart.ctx;
    if (!area || !y) return;
    (opts.lines || []).forEach(function (l) {
      var yy = y.getPixelForValue(l.value);
      if (yy < area.top || yy > area.bottom) return;
      ctx.save(); ctx.strokeStyle = l.color; ctx.lineWidth = l.width || 1.5; ctx.setLineDash(l.dash || [6, 5]);
      ctx.beginPath(); ctx.moveTo(area.left, yy); ctx.lineTo(area.right, yy); ctx.stroke();
      if (l.label) { ctx.fillStyle = l.color; ctx.font = "11px Roboto, 'Segoe UI', Arial, sans-serif"; ctx.fillText(l.label, area.left + 4, yy - 4); }
      ctx.restore();
    });
  } };
  function limitLine(value, color, label, dashed) { return { value: value, color: color || COLORS.LIMIT, label: label || "", dash: dashed === "dotted" ? [2, 3] : [6, 5], width: dashed === "dotted" ? 1.2 : 1.5 }; }

  /* series: [{label, color, dash, pointColors?, points:[{x, y, raw, over, date, station, name}]}]; lines: [limitLine…];
     onPoint(point) fires on click. Log axis by default; yMin / yTicks / yFormat reshape it (the % view);
     tooltip(point, seriesLabel) replaces the default line. */
  function yScale(o) {   // the shared y axis: log by default (yMin / yTicks / yFormat reshape it), linear on request
    var yTicks = o.yTicks || LOG_TICKS, yFmt = o.yFormat || fmt;
    return o.log === false ? { beginAtZero: true, title: { display: true, text: o.yTitle || "MPN/100 mL" } }
      : { type: "logarithmic", min: o.yMin || 4, title: { display: true, text: o.yTitle || "MPN/100 mL, log scale" },
          afterBuildTicks: o.yTicks ? function (ax) { ax.ticks = o.yTicks.filter(function (v) { return v >= ax.min && v <= ax.max; }).map(function (v) { return { value: v }; }); } : undefined,
          ticks: { callback: function (v) { return yTicks.indexOf(v) >= 0 ? yFmt(v) : ""; } } };
  }
  function pointText(o, p, label, y) {   // one tooltip line for a point: custom, or "label: raw · over its limit (ratio rule)"
    if (o.tooltip) return o.tooltip(p, label);
    return label + ": " + (p.raw != null ? p.raw : y) + (p.over ? " · over its limit" : "") + (p.ratio ? " (ratio rule)" : "");
  }
  var dayOf = function (p) { return p.date || new Date(p.x).toISOString().slice(0, 10); };
  var dayText = function (d) { return new Date(d + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "2-digit" }); };

  function seriesChart(canvas, o) {
    var datasets = (o.series || []).map(function (s) {
      var pts = s.points || [];
      return { label: s.label, data: pts, parsing: false, borderColor: s.color, backgroundColor: s.color, borderDash: s.dash || [],
        borderWidth: s.width || 1.6, tension: 0, spanGaps: true,
        pointBackgroundColor: s.pointColors || s.color, pointBorderColor: s.pointColors || s.color,
        pointRadius: s.pointRadius || 2.6, pointHoverRadius: 6 };
    });
    var xs = []; datasets.forEach(function (d) { d.data.forEach(function (p) { xs.push(p.x); }); });
    var xmin = xs.length ? Math.min.apply(null, xs) : Date.now() - 365 * 864e5, xmax = xs.length ? Math.max.apply(null, xs) : Date.now();
    if (xmin === xmax) { xmin -= 15 * 864e5; xmax += 15 * 864e5; }
    return new Chart(canvas, { type: "line", plugins: [limitLines], data: { datasets: datasets }, options: {
      responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "nearest", intersect: false },
      onClick: function (_e, els) { if (o.onPoint && els.length) { var el = els[0]; o.onPoint(datasets[el.datasetIndex].data[el.index]); } },
      scales: { x: { type: "linear", min: xmin, max: xmax, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8, callback: monthTick } }, y: yScale(o) },
      plugins: { legend: { display: o.legend !== false, position: "top" }, limitLines: { lines: o.lines || [] },
                 tooltip: { callbacks: { title: function (it) { return it.length ? new Date(it[0].parsed.x).toLocaleDateString() : ""; },
                                         label: function (it) { return pointText(o, it.raw || {}, it.dataset.label, it.parsed.y); } } } }
    } });
  }

  /* The same series drawn as bars: one slot per sample day (the union of the series' days), the bars of a day
     side by side; a series with two collections on one day shows the day's worst. Options as seriesChart. */
  function barSeries(canvas, o) {
    var series = o.series || [], seen = {};
    series.forEach(function (s) { (s.points || []).forEach(function (p) { seen[dayOf(p)] = 1; }); });
    var labels = Object.keys(seen).sort();
    var datasets = series.map(function (s) {
      var best = {};
      (s.points || []).forEach(function (p, i) { var d = dayOf(p); if (!best[d] || p.y > best[d].p.y) best[d] = { p: p, i: i }; });
      var meta = labels.map(function (d) { return best[d] ? best[d].p : null; });
      var colors = labels.map(function (d) { return best[d] && s.pointColors ? s.pointColors[best[d].i] : s.color; });
      return { label: s.label, data: meta.map(function (p) { return p ? p.y : null; }), meta: meta, backgroundColor: colors, borderColor: colors,
               borderWidth: 0, maxBarThickness: 28, minBarLength: 2, skipNull: true };
    });
    return new Chart(canvas, { type: "bar", plugins: [limitLines], data: { labels: labels, datasets: datasets }, options: {
      responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
      onClick: function (_e, els) { if (!o.onPoint || !els.length) return; var el = els[0], p = datasets[el.datasetIndex].meta[el.index]; if (p) o.onPoint(p); },
      scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8, callback: function (_v, i) { return labels[i] ? dayText(labels[i]) : ""; } } }, y: yScale(o) },
      plugins: { legend: { display: o.legend !== false, position: "top" }, limitLines: { lines: o.lines || [] },
                 tooltip: { filter: function (it) { return it.raw != null; },
                            callbacks: { title: function (it) { return it.length ? dayText(labels[it[0].dataIndex]) : ""; },
                                         label: function (it) { var p = it.dataset.meta[it.dataIndex] || {}; return pointText(o, p, it.dataset.label, it.parsed.y); } } } }
    } });
  }

  /* Two bars per day. a/b: {label, color, data:[…]}; labels: day strings; lines as above. */
  function pairedBars(canvas, o) {
    var ys = (o.a.data || []).concat(o.b.data || []).filter(function (v) { return v != null; });
    var top = Math.max.apply(null, ys.concat([0]));
    var lineMax = Math.max.apply(null, (o.lines || []).map(function (l) { return l.value; }).concat([0]));
    return new Chart(canvas, { type: "bar", plugins: [limitLines], data: { labels: o.labels, datasets: [
      { label: o.a.label, data: o.a.data, backgroundColor: o.a.color }, { label: o.b.label, data: o.b.data, backgroundColor: o.b.color } ] },
      options: { responsive: true, maintainAspectRatio: false, animation: false,
        scales: { y: { beginAtZero: true, suggestedMax: Math.max(lineMax * 1.2, top * 1.1), title: { display: true, text: o.yTitle || "MPN/100 mL" } } },
        plugins: { legend: { position: "top" }, limitLines: { lines: o.lines || [] },
                   tooltip: { callbacks: { label: function (it) { var raws = it.datasetIndex === 0 ? o.a.raws : o.b.raws; return it.dataset.label + ": " + ((raws && raws[it.dataIndex]) || it.parsed.y); } } } } } });
  }

  /* One sample's results for one indicator: a bar per value, the limit and (Enterococcus) caution drawn across. */
  function miniBars(canvas, o) {
    var vals = o.values || [], labels = vals.length > 1 ? vals.map(function (_, i) { return "sample " + (i + 1); }) : ["result"];
    var ys = vals.map(function (v) { return v.value; }), ymax = Math.max(o.limit * 1.15, Math.max.apply(null, ys.map(function (y) { return y || 0; })) * 1.1);
    var lines = [limitLine(o.limit, COLORS.LIMIT)]; if (o.caution) lines.push(limitLine(o.caution, COLORS.CAUTION_LINE, "", "dotted"));
    return new Chart(canvas, { type: "bar", plugins: [limitLines], data: { labels: labels, datasets: [
      { label: o.label, data: ys, backgroundColor: vals.map(function (v) { return v.over ? COLORS.OVER : v.caution ? COLORS.CAUTION : COLORS.CITY; }), maxBarThickness: 46 } ] },
      options: { responsive: true, maintainAspectRatio: false, animation: false,
        scales: { y: { beginAtZero: true, suggestedMax: ymax, ticks: { maxTicksLimit: 5 } }, x: { grid: { display: false } } },
        plugins: { legend: { display: false }, limitLines: { lines: lines },
                   tooltip: { callbacks: { label: function (it) { return o.label + ": " + (vals[it.dataIndex] || {}).raw + " (limit " + fmt(o.limit) + ")"; } } } } } });
  }
  window.BWTFCharts = { COLORS: COLORS, LABELS: LABELS, limitLines: limitLines, limitLine: limitLine, ensureChart: ensureChart,
                        seriesChart: seriesChart, barSeries: barSeries, pairedBars: pairedBars, miniBars: miniBars, fmt: fmt, monthTick: monthTick };
})();
