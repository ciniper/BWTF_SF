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
  const plain = dialog.querySelector("[data-city-email-plain]");
  const newer = dialog.querySelector("[data-city-email-newer]");
  const older = dialog.querySelector("[data-city-email-older]");
  const position = dialog.querySelector("[data-city-email-position]");
  const close = dialog.querySelector("[data-city-email-close]");
  let controller = null, opener = null, currentUpdate = null, scrollLock = null, refreshTimer = null;
  let cursors = [null], page = 0, nextCursor = null, requestedPage = 0;

  function schedulePageRefresh() {
    if (!dialog.hasAttribute("data-page-refresh")) return;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => { if (!dialog.open) window.location.reload(); }, 300000);
  }
  schedulePageRefresh();

  function lockPage() {
    const root = document.documentElement, pageBody = document.body;
    const properties = ["position", "top", "left", "width", "overflow", "padding-right", "box-sizing"];
    scrollLock = {x:window.scrollX, y:window.scrollY,
      body:properties.map(key => [key, pageBody.style.getPropertyValue(key), pageBody.style.getPropertyPriority(key)]),
      root:["overflow", "overscroll-behavior", "scroll-behavior"].map(key => [key, root.style.getPropertyValue(key), root.style.getPropertyPriority(key)])};
    const gutter = window.innerWidth - root.clientWidth;
    pageBody.style.paddingRight = `${parseFloat(getComputedStyle(pageBody).paddingRight) + gutter}px`;
    Object.assign(pageBody.style, {position:"fixed", top:`-${scrollLock.y}px`, left:`-${scrollLock.x}px`,
      width:"100%", overflow:"hidden", boxSizing:"border-box"});
    root.style.overflow = "hidden";
    root.style.overscrollBehavior = "none";
  }
  function unlockPage() {
    if (!scrollLock) return;
    const root = document.documentElement;
    const restore = (style, entries) => entries.forEach(([key, value, priority]) => {
      if (value) style.setProperty(key, value, priority); else style.removeProperty(key);
    });
    restore(document.body.style, scrollLock.body);
    restore(root.style, scrollLock.root);
    // Restore the exact reading position even if the site uses smooth scrolling.
    root.style.scrollBehavior = "auto";
    window.scrollTo(scrollLock.x, scrollLock.y);
    restore(root.style, scrollLock.root);
    scrollLock = null;
  }
  function clearLayout() {
    frame.hidden = true;
    frame.removeAttribute("data-ready");
    frame.removeAttribute("srcdoc");
    currentUpdate = null;
  }
  frame.addEventListener("load", () => {
    if (frame.hidden || !currentUpdate || !frame.contentDocument?.querySelector("[data-city-email-document]")) return;
    const doc = frame.contentDocument;
    // Metadata scrolls with the bulletin, leaving room to read on small phones.
    const header = doc.createElement("header");
    header.className = "city-email-meta";
    const date = doc.createElement("p"), title = doc.createElement("h1");
    date.textContent = `${currentUpdate.date_label} ${currentUpdate.date}`;
    title.textContent = currentUpdate.title;
    header.append(date, title);
    doc.body.prepend(header);
    doc.body.tabIndex = 0;
    doc.querySelectorAll("img").forEach(img => {
      const failed = () => {
        const fallback = doc.createElement("span");
        fallback.textContent = `Image unavailable — ${img.alt || "Email image"}`;
        img.replaceWith(fallback);
      };
      img.addEventListener("error", failed, {once:true});
      if (img.complete && !img.naturalWidth) failed();
    });
    // Sender scripts cannot run. These listeners belong to our parent page.
    doc.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); dialog.close(); }
      if (event.key !== "Tab") return;
      const links = [...doc.querySelectorAll("a[href]")];
      if (event.shiftKey && doc.activeElement === doc.body) {
        event.preventDefault(); (older.disabled ? (newer.disabled ? close : newer) : older).focus();
      } else if (event.shiftKey && doc.activeElement === links[0]) {
        event.preventDefault(); doc.body.focus({preventScroll:true});
      } else if (!event.shiftKey && (doc.activeElement === links[links.length - 1] || (!links.length && doc.activeElement === doc.body))) {
        event.preventDefault(); dialog.querySelector(".city-email-footer a").focus();
      }
    });
    frame.setAttribute("data-ready", "");
    status.textContent = "";
  });

  async function load(target = 0) {
    if (controller) controller.abort();
    const request = new AbortController();
    controller = request;
    requestedPage = target;
    const timer = setTimeout(() => request.abort(), 15000);
    if ([retry, newer, older].includes(document.activeElement)) close.focus({preventScroll:true});
    message.hidden = true;
    body.textContent = "";
    plain.hidden = true;
    clearLayout();
    retry.hidden = true;
    newer.disabled = older.disabled = true;
    status.textContent = "Loading City email…";
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
      message.hidden = false;
      if (update.html) {
        currentUpdate = update;
        frame.hidden = false;
        frame.srcdoc = update.html;
        status.textContent = "Loading email layout…";
      } else {
        body.textContent = update.text;
        plain.hidden = false;
        plain.scrollTop = 0;
        status.textContent = "";
      }
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
      // Today's automatic refresh must not dismiss an email being read.
      clearTimeout(refreshTimer);
      lockPage();
      dialog.showModal();
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
  close.addEventListener("click", () => dialog.close());
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
  let backdropDown = null;
  dialog.addEventListener("pointerdown", event => {
    backdropDown = event.target === dialog && outside(event) ? {x:event.clientX,y:event.clientY} : null;
  });
  dialog.addEventListener("pointercancel", () => { backdropDown = null; });
  dialog.addEventListener("click", event => {
    if (backdropDown && event.target === dialog && outside(event) &&
        Math.hypot(event.clientX - backdropDown.x, event.clientY - backdropDown.y) < 8) dialog.close();
    backdropDown = null;
  });
  dialog.addEventListener("close", () => {
    if (controller) controller.abort();
    controller = null;
    message.hidden = true;
    clearLayout();
    unlockPage();
    schedulePageRefresh();
    if (opener && opener.isConnected) opener.focus({preventScroll:true});
  });
})();
