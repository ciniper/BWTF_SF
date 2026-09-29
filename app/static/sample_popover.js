/* Sample popover — the "latest sample" chips on the alerts and forecast pages
 * open this instead of the raw DataSF rows: one mini bar graph per indicator
 * (Enterococcus, fecal coliform, total coliform), styled like the Source
 * Comparison's head-to-head bars, with the raw rows still one click away.
 * Data: /api/sample-day?station=<DataSF source id>[&date=YYYY-MM-DD] for the
 * city, /api/sample-day?bwtf=<Surfrider site name or site key>[&date=…] for a Surfrider
 * collection (one Enterococcus bar plus the volunteer's field notes)
 * (features/comparison/samples.py). Chart.js loads on first open only, so the
 * pages that include this script pay nothing until a chip is clicked.
 * Any element with data-sample-station or data-sample-bwtf opens it;
 * data-sample-name, data-sample-date and data-sample-feed-date are optional. */
(function () {
  /* Charts are drawn by /static/charts.js (BWTFCharts); it is loaded on demand if the page did not include it. */
  function ensureCharts() {
    var need = window.BWTFCharts ? Promise.resolve() : new Promise(function (res, rej) {
      var t = document.createElement("script"); t.src = "/static/charts.js"; t.onload = res; t.onerror = function () { rej(new Error("charts module did not load")); }; document.head.appendChild(t); });
    return need.then(function () { return window.BWTFCharts.ensureChart(); });
  }
  var CSS = ".sp-modal{position:fixed;inset:0;background:rgba(38,39,42,.55);display:none;align-items:center;justify-content:center;padding:20px;z-index:5000;font-family:'Roboto','Segoe UI',Arial,sans-serif;color:#26272a}" +
    ".sp-modal.open{display:flex}.sp-box{background:#fff;border-radius:20px;max-width:820px;width:100%;padding:20px 22px;box-shadow:0 24px 60px rgba(0,0,0,.3);max-height:92vh;overflow:auto}" +
    ".sp-head{display:flex;justify-content:space-between;align-items:center;gap:12px}.sp-head h2{margin:0;font-size:19px;color:#26272a}" +
    ".sp-close{border:none;background:#eef2f4;border-radius:10px;padding:8px 12px;cursor:pointer;font-weight:700;color:#26272a;font-size:14px;font-family:inherit}" +
    ".sp-note{color:#54576F;font-size:12.5px;margin:6px 0 12px;line-height:1.5}.sp-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}.sp-grid.one{grid-template-columns:minmax(200px,340px)}" +
    ".sp-field{color:#54576F;font-size:12.5px;margin:12px 0 0;line-height:1.5}.sp-field:empty{display:none}" +
    ".sp-cell{border:1px solid #d9e4e8;border-radius:14px;padding:10px 12px}.sp-label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#54576F;font-weight:700;margin:0 0 4px}" +
    ".sp-chart{position:relative;height:170px}.sp-vals{margin-top:6px;font-size:13px;font-weight:700}.sp-val{display:inline-block;padding:1px 7px;border-radius:6px;background:#eef2f4;margin-right:4px}" +
    ".sp-val.over{background:#d15c5c;color:#fff}.sp-val.caution{background:#fbeccd;color:#7a5200}.sp-val.none{background:transparent;color:#98a3ab;font-weight:600}" +
    ".sp-links{margin:14px 0 0;font-size:13px;color:#54576F}.sp-links a{color:#0072BC;font-weight:700}.sp-links a+a{margin-left:14px}";
  var el = null, charts = [];
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); };
  var fmt = function (n) { return n == null ? "—" : Number(n).toLocaleString(); };
  function modal() {
    if (el) return el;
    var st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);
    el = document.createElement("div"); el.className = "sp-modal"; el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true");
    el.innerHTML = '<div class="sp-box"><div class="sp-head"><h2 id="sp-title"></h2><button class="sp-close" type="button">Close &times;</button></div>' +
      '<p class="sp-note" id="sp-note"></p><div class="sp-grid" id="sp-grid"></div><p class="sp-field" id="sp-field"></p><p class="sp-links" id="sp-links"></p></div>';
    document.body.appendChild(el);
    el.querySelector(".sp-close").addEventListener("click", close);
    el.addEventListener("click", function (e) { if (e.target === el) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    return el;
  }
  function close() { if (!el) return; el.classList.remove("open"); charts.forEach(function (c) { c.destroy(); }); charts = []; }
  function open(opts) {
    var m = modal();
    m.querySelector("#sp-title").textContent = opts.name || opts.station || opts.bwtf;
    m.querySelector("#sp-note").textContent = "Loading the lab results…";
    m.querySelector("#sp-grid").innerHTML = ""; m.querySelector("#sp-links").innerHTML = ""; m.querySelector("#sp-field").textContent = "";
    m.classList.add("open");
    var q = new URLSearchParams(opts.bwtf ? { bwtf: opts.bwtf } : { station: opts.station }); if (opts.date) q.set("date", opts.date);
    Promise.all([fetch("/api/sample-day?" + q).then(function (r) { return r.json(); }), ensureCharts()])
      .then(function (res) { render(res[0], opts); })
      .catch(function (e) { m.querySelector("#sp-note").textContent = "Could not load the results: " + e; });
  }
  function render(d, opts) {
    var m = modal(), note = m.querySelector("#sp-note"), grid = m.querySelector("#sp-grid"), links = m.querySelector("#sp-links"), fieldEl = m.querySelector("#sp-field");
    if (d.ok === false) { note.textContent = d.error || "No data"; return; }
    var surf = d.source === "bwtf";
    m.querySelector("#sp-title").textContent = d.name || opts.name || opts.station || opts.bwtf;
    charts.forEach(function (c) { c.destroy(); }); charts = [];
    if (!d.found) { note.textContent = (surf ? "No Surfrider sample at this site" : "No published lab results for this station") + (opts.date ? " on " + opts.date : "") + "."; return; }
    var parts = ["Sampled " + d.date + (surf && d.times && d.times.length ? " at " + d.times.join(" and ") : "")];
    if (surf) parts.push("Surfrider Blue Water Task Force volunteer lab");
    if (d.n_samples > 1) parts.push(surf ? d.n_samples + " collections that day" : d.n_samples + " samples that day (the city's dataset carries the date but not the time)");
    if (opts.feedDate && opts.feedDate > d.date) parts.push("lab numbers for the " + opts.feedDate + " sample not yet published");
    parts.push(d.units + " · dashed line = state single-sample limit" + (d.caution ? ", dotted = caution tier (" + d.caution + ")" : ""));
    note.textContent = parts.join(" · ");
    var f = d.field || {};
    fieldEl.textContent = [f.tested_by ? "Tested by " + f.tested_by : "", f.air ? "air " + f.air : "", f.water ? "water " + f.water : "", f.sky ? "sky " + f.sky : "", f.wind ? "wind " + f.wind : "",
                           f.tide ? "tide " + f.tide : "", f.waves ? "waves " + f.waves : "", f.rain ? "rain " + f.rain : ""].filter(Boolean).join(" · ") + (f.comments ? " — " + f.comments : "");
    grid.className = "sp-grid" + (d.analytes.length === 1 ? " one" : "");
    grid.innerHTML = d.analytes.map(function (a) {
      var vals = d.cells[a.code] || [];
      var chips = vals.length ? vals.map(function (v) { return '<span class="sp-val' + (v.over ? " over" : v.caution ? " caution" : "") + '">' + esc(v.raw) + "</span>"; }).join("") : '<span class="sp-val none">not reported</span>';
      var why = (a.code === "COLI_TOTAL" && d.ratio_applied) ? ' <span title="' + esc(d.ratio_note) + '">(ratio rule)</span>' : "";
      return '<div class="sp-cell"><div class="sp-label">' + esc(a.label) + " · limit " + fmt(d.limits[a.code]) + why + '</div><div class="sp-chart"><canvas data-code="' + esc(a.code) + '"></canvas></div><div class="sp-vals">' + chips + "</div></div>";
    }).join("");
    d.analytes.forEach(function (a) {
      var vals = d.cells[a.code] || [], limit = d.limits[a.code], canvas = grid.querySelector('canvas[data-code="' + a.code + '"]');
      if (!vals.length) { canvas.parentElement.innerHTML = '<div style="height:100%;display:flex;align-items:center;justify-content:center;color:#98a3ab;font-size:12.5px">no result</div>'; return; }
      var labels = vals.length > 1 ? vals.map(function (_, i) { return "sample " + (i + 1); }) : ["result"];
      var ys = vals.map(function (v) { return v.value; }), ymax = Math.max(limit * 1.15, Math.max.apply(null, ys.map(function (y) { return y || 0; })) * 1.1);
      charts.push(window.BWTFCharts.miniBars(canvas, { label: a.label, values: vals, limit: limit, caution: a.code === "ENTERO" ? d.caution : 0 }));
    });
    links.innerHTML = (d.results_url ? '<a href="' + esc(d.results_url) + '" target="_blank" rel="noopener noreferrer">' + (surf ? "Site report on bwtf.surfrider.org" : "Raw results on SF Gov Open Data") + "</a>" : "") +
      '<a href="' + esc(d.viewer_url) + '">All samples for this site</a><a href="' + esc(d.graph_url) + '">Graphs for this site</a>';
  }
  function fromEl(a) { return { station: a.dataset.sampleStation, bwtf: a.dataset.sampleBwtf, name: a.dataset.sampleName, date: a.dataset.sampleDate || "", feedDate: a.dataset.sampleFeedDate || "" }; }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("[data-sample-station],[data-sample-bwtf]");
    if (!a) return;
    if (e.target.closest("a[href]") && (e.metaKey || e.ctrlKey || e.shiftKey)) return;   // modifier-click a real link: let the raw rows open
    e.preventDefault();
    open(fromEl(a));
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var a = e.target.closest ? e.target.closest("[data-sample-station][role=button],[data-sample-bwtf][role=button]") : null;
    if (!a) return;
    e.preventDefault();
    open(fromEl(a));
  });
  window.SamplePopover = { open: open, close: close, charts: function () { return charts.slice(); } };
})();
