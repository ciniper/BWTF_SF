/* BWTFTables — long tables start with their first 50 rows and a "Show all N rows" button
 * (Chase, 2026-09-29: every table on every page). Nothing to wire per page: on load, and
 * whenever a <tbody> is rendered or re-rendered, any body with more than 50 rows is trimmed
 * and the button shows the rest. Rows that belong to the row above (the Samples page's
 * field-note rows, class "notes", or any tr[data-child-row]) are not counted and travel with
 * their parent. A table can opt out with data-nolimit. */
(function () {
  var N = 50, ATTR = "data-tbl-hidden", MORE = "tbl-more";
  var st = document.createElement("style");
  st.textContent = "[" + ATTR + "]{display:none!important}" +
    "tr." + MORE + " td{text-align:center;padding:10px 8px;background:#f7fafb;border-top:1px solid #e3ebf2}" +
    "tr." + MORE + " button{padding:6px 14px;border-radius:999px;border:2px solid #d9e4e8;background:#fff;color:#0072BC;cursor:pointer;font:700 12.5px 'Roboto','Segoe UI',Arial,sans-serif}" +
    "tr." + MORE + " button:hover{border-color:#0072BC}tr." + MORE + " span{color:#8a949b;font-size:12px;margin-left:8px}";
  document.head.appendChild(st);
  var isAux = function (tr) { return tr.classList.contains("notes") || tr.hasAttribute("data-child-row"); };
  var isMore = function (node) { return node.nodeType === 1 && node.tagName === "TR" && node.classList.contains(MORE); };

  /* Trim one body to its first n primary rows (idempotent: re-running after a re-render is fine). */
  function limit(tbody, n) {
    n = n || N;
    if (!tbody || tbody.tagName !== "TBODY" || tbody.closest("table[data-nolimit]")) return;
    var old = tbody.querySelector("tr." + MORE); if (old) old.remove();
    var rows = [].filter.call(tbody.children, function (r) { return r.tagName === "TR"; });
    rows.forEach(function (r) { r.removeAttribute(ATTR); });
    var primary = rows.filter(function (r) { return !isAux(r); });
    if (primary.length <= n) return;
    rows.slice(rows.indexOf(primary[n])).forEach(function (r) { r.setAttribute(ATTR, ""); });
    var cols = 0; [].forEach.call(rows[0].children, function (c) { cols += c.colSpan || 1; });
    var tr = document.createElement("tr"); tr.className = MORE;
    tr.innerHTML = '<td colspan="' + (cols || 1) + '"><button type="button">Show all ' + primary.length.toLocaleString() + ' rows</button><span>showing the first ' + n + '</span></td>';
    tr.querySelector("button").addEventListener("click", function () { rows.forEach(function (r) { r.removeAttribute(ATTR); }); tr.remove(); });
    tbody.appendChild(tr);
  }
  function limitAll(root) { [].forEach.call((root || document).querySelectorAll("tbody"), function (b) { limit(b); }); }

  /* Re-apply when a page (re)renders rows — but not for our own more-row coming and going. */
  var queue = new Set(), scheduled = false;
  function flush() { scheduled = false; queue.forEach(function (b) { limit(b); }); queue.clear(); }
  function schedule(tbody) { queue.add(tbody); if (!scheduled) { scheduled = true; setTimeout(flush, 0); } }   // not rAF: it never fires in a hidden tab
  function watch() {
    if (!window.MutationObserver) return;
    new MutationObserver(function (records) {
      records.forEach(function (m) {
        var real = [].some.call(m.addedNodes, function (x) { return !isMore(x) && x.nodeType !== 3; }) || [].some.call(m.removedNodes, function (x) { return !isMore(x) && x.nodeType !== 3; });
        if (!real) return;
        if (m.target.tagName === "TBODY") { schedule(m.target); return; }
        [].forEach.call(m.addedNodes, function (x) {
          if (x.nodeType !== 1) return;
          if (x.tagName === "TBODY") schedule(x);
          else [].forEach.call(x.querySelectorAll ? x.querySelectorAll("tbody") : [], schedule);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }
  function start() { limitAll(document); watch(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  window.BWTFTables = { limit: limit, limitAll: limitAll, N: N };
})();
