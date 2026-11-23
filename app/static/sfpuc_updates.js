/* Admin-only disclosure: never reads or changes the map, watcher, or forecast. */
(() => {
  "use strict";
  function watchImages(root) {
    root.querySelectorAll(".email-image img").forEach(image => {
      const failed = () => {
        image.hidden = true;
        image.parentElement.querySelector("figcaption").hidden = false;
      };
      image.addEventListener("error", failed, {once: true});
      if (image.complete && !image.naturalWidth) failed();
    });
  }
  watchImages(document);
  const form = document.querySelector(".email-review-form");
  if (form) {
    const clean = form.querySelector("[data-clean-forward]");
    const undo = form.querySelector("[data-undo-cleanup]");
    const notice = form.querySelector("[data-cleanup-status]");
    const title = form.elements.title, body = form.elements.body;
    let previous = null;
    const edited = () => { previous = null; undo.hidden = true; };
    title.addEventListener("input", edited);
    body.addEventListener("input", edited);
    clean.addEventListener("click", async () => {
      const original = {title: title.value, body: body.value};
      clean.disabled = true;
      notice.textContent = "Removing forwarding text…";
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch("/alerts/sfpuc-emails/clean-forward", {
          method: "POST", cache: "no-store", signal: controller.signal,
          headers: {"Content-Type": "application/json", "X-CSRF-Token": form.elements.csrf.value},
          body: JSON.stringify(original)
        });
        if (!response.ok) throw new Error("Unavailable");
        const result = await response.json();
        if (!result.ok) throw new Error("Invalid response");
        if (body.value !== original.body || title.value !== original.title) {
          notice.textContent = "Your draft changed during cleanup. Try again when you're ready.";
        } else if (!result.removed) {
          notice.textContent = "No forwarding header found. You can edit the message directly.";
        } else {
          previous = original;
          title.value = result.title;
          body.value = result.body;
          undo.hidden = false;
          notice.textContent = "Forwarding text removed from this draft. Nothing is saved until you submit the review.";
        }
      } catch (_) {
        notice.textContent = "Cleanup is unavailable. Your draft hasn't changed; you can edit it directly.";
      } finally {
        clearTimeout(timer);
        clean.disabled = false;
      }
    });
    undo.addEventListener("click", () => {
      if (!previous) return;
      title.value = previous.title;
      body.value = previous.body;
      edited();
      notice.textContent = "Your previous draft has been restored.";
    });
  }
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
        if (Array.isArray(item.images) && item.images.length) {
          const gallery = document.createElement("div");
          gallery.className = "email-images";
          gallery.setAttribute("aria-label", "Email images");
          for (const itemImage of item.images) {
            const figure = document.createElement("figure");
            figure.className = "email-image" + (itemImage.small ? " email-image-small" : "");
            const image = document.createElement("img");
            image.alt = itemImage.alt;
            image.loading = "eager";
            image.decoding = "async";
            image.referrerPolicy = "no-referrer";
            // Only the authenticated image route is a valid preview image source.
            if (!/^\/alerts\/sfpuc-emails\/images\/[0-9a-f-]{36}\/\d+$/.test(itemImage.src)) continue;
            image.src = itemImage.src;
            const fallback = document.createElement("figcaption");
            fallback.hidden = true;
            fallback.textContent = `Image unavailable — ${itemImage.alt}`;
            figure.append(image, fallback);
            gallery.append(figure);
          }
          article.append(gallery);
          watchImages(gallery);
        }
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
