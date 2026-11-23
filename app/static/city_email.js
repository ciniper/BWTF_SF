/* City bulletin viewer: newest captured message, then cursor-based history. */
(() => {
  "use strict";
  const dialog = document.getElementById("city-email-dialog");
  if (!dialog) return;
  const status = dialog.querySelector("[data-city-email-status]");
  const retry = dialog.querySelector("[data-city-email-retry]");
  const message = dialog.querySelector("[data-city-email-message]");
  const frame = dialog.querySelector("[data-city-email-layout]");
  const body = dialog.querySelector("[data-city-email-text]");
  const newer = dialog.querySelector("[data-city-email-newer]");
  const older = dialog.querySelector("[data-city-email-older]");
  const position = dialog.querySelector("[data-city-email-position]");
  const content = dialog.querySelector(".city-email-content");
  let controller = null, opener = null, overflow = "", observer = null;
  let cursors = [null], page = 0, nextCursor = null, requestedPage = 0;

  function clearLayout() {
    if (observer) observer.disconnect();
    observer = null;
    frame.hidden = true;
    frame.removeAttribute("srcdoc");
  }
  frame.addEventListener("load", () => {
    if (frame.hidden || !frame.hasAttribute("srcdoc") || !frame.contentDocument) return;
    const doc = frame.contentDocument;
    const resize = () => {
      if (frame.hidden || frame.contentDocument !== doc) return;
      frame.style.height = `${Math.ceil(doc.body.getBoundingClientRect().height) + 4}px`;
    };
    observer = new ResizeObserver(resize);
    observer.observe(doc.body);
    doc.querySelectorAll("img").forEach(img => {
      img.addEventListener("load", resize, {once:true});
      const failed = () => {
        const fallback = doc.createElement("span");
        fallback.textContent = `Image unavailable — ${img.alt || "Email image"}`;
        img.replaceWith(fallback); resize();
      };
      img.addEventListener("error", failed, {once:true});
      if (img.complete && !img.naturalWidth) failed();
    });
    // Sender scripts cannot run. These listeners belong to our parent page.
    doc.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); dialog.close(); }
      if (event.key !== "Tab") return;
      const links = [...doc.querySelectorAll("a[href]")];
      if (event.shiftKey && doc.activeElement === links[0]) {
        event.preventDefault(); content.focus();
      } else if (!event.shiftKey && doc.activeElement === links[links.length - 1]) {
        event.preventDefault(); dialog.querySelector(".city-email-footer a").focus();
      }
    });
    resize();
  });

  async function load(target = 0) {
    if (controller) controller.abort();
    const request = new AbortController();
    controller = request;
    requestedPage = target;
    const timer = setTimeout(() => request.abort(), 15000);
    if ([retry, newer, older].includes(document.activeElement)) content.focus();
    message.hidden = true;
    body.textContent = "";
    clearLayout();
    retry.hidden = true;
    newer.disabled = older.disabled = true;
    status.textContent = "Loading City email…";
    content.scrollTop = 0;
    try {
      const query = cursors[target] ? `?before=${encodeURIComponent(cursors[target])}` : "";
      const response = await fetch(`/api/city-emails${query}`, {signal:request.signal, cache:"no-store"});
      if (controller !== request || !dialog.open) return;
      if (!response.ok) throw new Error("Unavailable");
      const data = await response.json();
      if (controller !== request || !dialog.open) return;
      if (!data.ok) throw new Error("Unavailable");
      page = target;
      nextCursor = data.next_cursor;
      newer.disabled = page === 0;
      older.disabled = !nextCursor;
      position.textContent = page === 0 ? "Latest email" : "Earlier email";
      dialog.querySelector("#city-email-heading").textContent = page === 0 ? "Latest City email" : "Earlier City email";
      const update = data.update;
      if (!update) {
        status.textContent = page === 0 ? "No City bulletins have been captured yet. Check SFPUC’s current information below." : "No bulletin on this page. Use Newer or Older to continue.";
        return;
      }
      message.querySelector("[data-city-email-title]").textContent = update.title;
      message.querySelector("[data-city-email-date]").textContent = `${update.date_label} ${update.date}`;
      body.hidden = !!update.html;
      message.hidden = false;
      if (update.html) {
        frame.hidden = false;
        frame.srcdoc = update.html;
      } else {
        body.textContent = update.text;
      }
      status.textContent = "";
    } catch (_) {
      if (controller !== request || !dialog.open) return;
      status.textContent = "City emails are unavailable right now. Try again or use the SFPUC links below.";
      retry.hidden = false;
      newer.disabled = page === 0;
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
      cursors = [null]; page = 0; nextCursor = null;
      load(0);
    });
  });
  newer.addEventListener("click", () => load(page - 1));
  older.addEventListener("click", () => {
    cursors = cursors.slice(0, page + 1);
    cursors.push(nextCursor);
    load(page + 1);
  });
  retry.addEventListener("click", () => load(requestedPage));
  dialog.querySelector("[data-city-email-close]").addEventListener("click", () => dialog.close());
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
    clearLayout();
    document.body.style.overflow = overflow;
    if (opener && opener.isConnected) opener.focus();
  });
})();
