/* Admin-only disclosure: never reads or changes the map, watcher, or forecast. */
(() => {
  "use strict";
  const panel = document.getElementById("sfpuc-updates");
  if (!panel) return;
  const status = panel.querySelector("[data-updates-status]");
  const list = panel.querySelector("[data-updates-list]");
  const more = panel.querySelector("[data-updates-more]");
  const retry = panel.querySelector("[data-updates-retry]");
  let loaded = false, busy = false, next = 0;
  async function load() {
    if (busy || next === null) return;
    busy = true;
    more.hidden = true;
    retry.hidden = true;
    status.textContent = "Loading SFPUC updates…";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(`/alerts/api/sfpuc-updates?offset=${next}`, {signal: controller.signal, cache: "no-store"});
      if (!response.ok) throw new Error("Unavailable");
      const data = await response.json();
      if (!data.ok || !Array.isArray(data.updates)) throw new Error("Invalid response");
      for (const item of data.updates) {
        const article = document.createElement("article");
        article.className = "sfpuc-update";
        const heading = document.createElement("h3");
        heading.textContent = item.title;
        const date = document.createElement("p");
        date.className = "email-muted";
        date.textContent = `${item.source} · ${item.date_label} ${item.date}`;
        const body = document.createElement("div");
        body.className = "sfpuc-update-text";
        body.textContent = item.text;
        article.append(heading, date, body);
        list.append(article);
      }
      next = data.next_offset;
      loaded = true;
      status.textContent = list.children.length ? "" : "No approved updates yet. Check SFPUC’s current information below.";
      more.hidden = next === null;
    } catch (_) {
      status.textContent = "Updates are temporarily unavailable. Please try again or check SFPUC below.";
      retry.hidden = false;
    } finally {
      clearTimeout(timer);
      busy = false;
    }
  }
  panel.addEventListener("toggle", () => { if (panel.open && !loaded) load(); });
  more.addEventListener("click", load);
  retry.addEventListener("click", load);
})();
