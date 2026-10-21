/* site_picker.js — the one site picker Graphs and Samples share (markup: app/templates/_site_picker.html).
 * The chosen site is the board's title; the name (with its chevron) opens the zone-grouped tiles beneath
 * it, a tap on a tile picks it and closes them, and ‹ › step through the sites in view (zone order). A page hands in the tiles' Source rule with filter()
 * — tiles that source never sampled hide, and a hidden current site falls back: to "All sites" when the
 * page offers it (Samples), else to the page's first site (data-first, Graphs), else to a beach both programs sample.
 *   SitePicker.init({ key, onPick })   // onPick(key) fires on a tap, never on filter()/set()
 *   SitePicker.filter(fits) -> key      // fits(tile) true keeps the tile; returns the (possibly changed) key
 *   SitePicker.set(key), SitePicker.key(), SitePicker.open(bool) */
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var P = { key: "", onPick: function () {} };
  function tiles() { return Array.prototype.slice.call(document.querySelectorAll("#tiles .tile")); }
  function open(on) {
    $("tiles").classList.toggle("open", on);
    $("siteChange").setAttribute("aria-expanded", String(on));
    $("siteChange").classList.toggle("open", on);
  }
  function step(dir) {   // ‹ › : the previous / next site in view, wrapping; the All tile is not a stop
    var shown = tiles().filter(function (t) { return !t.hidden && t.dataset.key !== ""; });
    if (!shown.length) return;
    var i = shown.findIndex(function (t) { return t.dataset.key === P.key; });
    var next = shown[(i < 0 ? (dir > 0 ? 0 : shown.length - 1) : (i + dir + shown.length) % shown.length)];
    open(false); P.key = next.dataset.key; paint(); P.onPick(P.key);
  }
  function paint() {
    var cur = tiles().filter(function (t) { return t.dataset.key === P.key; })[0] || null;
    tiles().forEach(function (t) { t.classList.toggle("active", t === cur); });
    $("siteName").textContent = cur ? cur.dataset.name : "—";
    var group = cur ? cur.closest(".tile-group") : null;
    $("siteZone").textContent = group && group.dataset.group ? group.dataset.group : "";
  }
  function filter(fits) {
    tiles().forEach(function (t) { t.hidden = !(t.dataset.key === "" || fits(t)); });
    document.querySelectorAll("#tiles .tile-group").forEach(function (g) {
      g.hidden = !tiles().some(function (t) { return t.closest(".tile-group") === g && !t.hidden; });
    });
    var shown = tiles().filter(function (t) { return !t.hidden; });
    if (!shown.some(function (t) { return t.dataset.key === P.key; })) {
      var all = shown.filter(function (t) { return t.dataset.key === ""; })[0];
      var first = shown.filter(function (t) { return t.dataset.first === "1"; })[0];
      var dual = shown.filter(function (t) { return t.dataset.city === "1" && t.dataset.bwtf === "1"; })[0];
      var pick = all || first || dual || shown[0];
      P.key = pick ? pick.dataset.key : "";
    }
    paint();
    return P.key;
  }
  function set(key) { P.key = key || ""; paint(); }
  function init(opts) {
    P.key = (opts && opts.key) || "";
    P.onPick = (opts && opts.onPick) || function () {};
    $("tiles").addEventListener("click", function (e) {
      var t = e.target.closest(".tile"); if (!t || t.hidden) return;
      open(false);
      if (t.dataset.key === P.key) return;
      P.key = t.dataset.key; paint(); P.onPick(P.key);
    });
    $("siteChange").addEventListener("click", function () { open(!$("tiles").classList.contains("open")); });
    $("sitePrev").addEventListener("click", function () { step(-1); });
    $("siteNext").addEventListener("click", function () { step(1); });
    paint();
  }
  window.SitePicker = { init: init, set: set, filter: filter, open: open, step: step, key: function () { return P.key; } };
})();
