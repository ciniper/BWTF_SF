/* kit.js — one shared behaviour for the kit's toggle buttons: keep aria-pressed in step with the
 * .active (or .focus) class every page already toggles, so screen readers hear the chosen option.
 * Loaded by the shared frame (_frame.html tabbar macro) on every page. */
(function () {
  var SEL = ".seg-btn, .preset, .v-preset, .group-btn, .range-btn, .mode-btn, .view-toggle button, .tile, .zone";
  function sync() {
    document.querySelectorAll(SEL).forEach(function (b) {
      if (b.tagName !== "BUTTON") return;
      var on = b.classList.contains("active") || b.classList.contains("focus");
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  document.addEventListener("click", function () { requestAnimationFrame(sync); }, true);
  // pages also flip .active after data arrives (e.g. a date preset lit once the range is known): follow class changes too
  var pending = false;
  function queue() { if (pending) return; pending = true; requestAnimationFrame(function () { pending = false; sync(); }); }
  function start() { sync(); new MutationObserver(queue).observe(document.body, {subtree: true, attributes: true, attributeFilter: ["class"]}); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
