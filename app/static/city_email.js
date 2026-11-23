/* Latest reviewed City email, fetched only when its private Today preview opens. */
(() => {
  "use strict";
  const dialog = document.getElementById("city-email-dialog");
  if (!dialog) return;
  const status = dialog.querySelector("[data-city-email-status]");
  const retry = dialog.querySelector("[data-city-email-retry]");
  const message = dialog.querySelector("[data-city-email-message]");
  const gallery = dialog.querySelector("[data-city-email-images]");
  let controller = null, opener = null, overflow = "";

  function renderImages(images) {
    for (const item of images || []) {
      if (!/^\/alerts\/sfpuc-emails\/images\/[0-9a-f-]{36}\/\d+$/.test(item.src)) continue;
      const figure = document.createElement("figure");
      figure.className = "city-email-image" + (item.small ? " small" : "");
      const image = document.createElement("img");
      image.alt = item.alt || "Email image";
      image.loading = "eager";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      const caption = document.createElement("figcaption");
      caption.hidden = true;
      caption.textContent = `Image unavailable — ${image.alt}`;
      image.addEventListener("error", () => { image.hidden = true; caption.hidden = false; }, {once:true});
      figure.append(image, caption);
      gallery.append(figure);
      image.src = item.src;
    }
  }

  async function load() {
    if (controller) controller.abort();
    const request = new AbortController();
    controller = request;
    const timer = setTimeout(() => request.abort(), 15000);
    const retryHadFocus = document.activeElement === retry;
    message.hidden = true;
    message.querySelector("[data-city-email-title]").textContent = "";
    message.querySelector("[data-city-email-date]").textContent = "";
    message.querySelector("[data-city-email-text]").textContent = "";
    gallery.replaceChildren();
    retry.hidden = true;
    if (retryHadFocus) dialog.querySelector(".city-email-content").focus();
    status.textContent = "Loading the latest City email…";
    try {
      const response = await fetch("/alerts/api/sfpuc-updates/latest", {signal:request.signal, cache:"no-store"});
      if (controller !== request || !dialog.open) return;
      if (response.status === 401) {
        status.textContent = "Your Admin session has ended. Close this window and reopen the preview from SFPUC emails.";
        return;
      }
      if (!response.ok) throw new Error("Unavailable");
      const data = await response.json();
      if (controller !== request || !dialog.open) return;
      if (!data.ok) throw new Error("Unavailable");
      const update = data.update;
      if (!update) {
        status.textContent = "No City email has been approved for preview yet. Review a bulletin in Admin → SFPUC emails, then reopen this window.";
        return;
      }
      message.querySelector("[data-city-email-title]").textContent = update.title;
      message.querySelector("[data-city-email-date]").textContent = `${update.date_label} ${update.date}`;
      message.querySelector("[data-city-email-text]").textContent = update.text;
      renderImages(update.images);
      message.hidden = false;
      status.textContent = "";
    } catch (_) {
      if (controller !== request || !dialog.open) return;
      status.textContent = "The latest City email is unavailable right now. Try again or use the SFPUC links below.";
      retry.hidden = false;
    } finally {
      clearTimeout(timer);
      if (controller === request) controller = null;
    }
  }

  document.querySelectorAll("[data-city-email]").forEach(trigger => {
    trigger.addEventListener("click", () => {
      if (dialog.open) return;
      opener = trigger;
      overflow = document.body.style.overflow;
      dialog.showModal();
      document.body.style.overflow = "hidden";
      dialog.querySelector(".city-email-content").scrollTop = 0;
      load();
    });
  });
  retry.addEventListener("click", load);
  dialog.querySelector("[data-city-email-close]").addEventListener("click", () => dialog.close());
  // Native dialog supplies Escape and an inert background. Keep Tab cycling
  // within the window, including its keyboard-scrollable message region.
  dialog.addEventListener("keydown", event => {
    if (event.key !== "Tab") return;
    const stops = [...dialog.querySelectorAll("button, a[href], [tabindex='0']")]
      .filter(element => !element.disabled && element.getClientRects().length);
    const first = stops[0], last = stops[stops.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  });
  const outside = event => {
    const box = dialog.getBoundingClientRect();
    return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
  };
  let backdropDown = false;
  dialog.addEventListener("pointerdown", event => { backdropDown = event.target === dialog && outside(event); });
  dialog.addEventListener("click", event => { if (backdropDown && event.target === dialog && outside(event)) dialog.close(); });
  dialog.addEventListener("close", () => {
    if (controller) controller.abort();
    controller = null;
    message.hidden = true;
    gallery.replaceChildren();
    document.body.style.overflow = overflow;
    if (opener && opener.isConnected) opener.focus();
  });
})();
