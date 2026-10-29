// Parking back office – small client script.
// Buttons and forms call the same JSON API as the Android app:
//   <button data-api="POST /api/v1/payment-alarms/{id}/acknowledge">
//   <form data-api-form="PATCH /api/v1/customers/{id}">  (fields -> JSON body)
// The access cookie is sent automatically; the X-Requested-With header is the CSRF guard.
(function () {
  "use strict";
  const T = window.PK_I18N || {};

  function toast(message, isError) {
    const el = document.createElement("div");
    el.className = "toast" + (isError ? " error" : "");
    el.setAttribute("role", "status");
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  async function call(spec, body) {
    const [method, url] = spec.trim().split(/\s+/, 2);
    const res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 401) { window.location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); return null; }
    if (!res.ok) {
      let detail = res.statusText;
      try { const data = await res.json(); detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail); } catch (e) { /* not JSON */ }
      throw new Error((T.errors && T.errors[detail]) || detail);
    }
    if (res.status === 204) return {};
    const type = res.headers.get("Content-Type") || "";
    return type.includes("application/json") ? res.json() : res.blob().then((blob) => ({ blob, res }));
  }

  function parseValue(input) {
    if (input.type === "checkbox") return input.checked;
    if (input.dataset.type === "cents") return Math.round(parseFloat(String(input.value).replace(",", ".")) * 100);
    if (input.dataset.type === "int" || input.type === "number") return input.value === "" ? null : Number(input.value);
    if (input.dataset.type === "json") return JSON.parse(input.value);
    if (input.dataset.type === "bool") return input.value === "true";
    if (input.dataset.type === "datetime") return input.value ? new Date(input.value).toISOString() : null;
    return input.value === "" ? null : input.value;
  }

  function setPath(obj, path, value) {
    const parts = path.split(".");
    let cur = obj;
    parts.slice(0, -1).forEach((p) => { cur = cur[p] = cur[p] || {}; });
    cur[parts[parts.length - 1]] = value;
  }

  async function run(el, spec, body) {
    if (el.dataset.confirm && !window.confirm(el.dataset.confirm)) return;
    el.setAttribute("aria-busy", "true");
    if ("disabled" in el) el.disabled = true;
    try {
      const out = await call(spec, body);
      if (out && out.blob) {
        const name = (out.res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
        const link = document.createElement("a");
        link.href = URL.createObjectURL(out.blob);
        link.download = name ? name[1] : "report";
        link.click();
        setTimeout(() => location.reload(), 600);
        return;
      }
      if (el.dataset.success) sessionStorage.setItem("pk_toast", el.dataset.success);
      location.reload();
    } catch (err) {
      toast(err.message, true);
      if ("disabled" in el) el.disabled = false;
    } finally {
      el.removeAttribute("aria-busy");
    }
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-api]");
    if (btn) {
      ev.preventDefault();
      let body;
      if (btn.dataset.body) body = JSON.parse(btn.dataset.body);
      if (btn.dataset.bodyFrom) {
        body = {};
        document.querySelectorAll(btn.dataset.bodyFrom).forEach((input) => setPath(body, input.name, parseValue(input)));
      }
      run(btn, btn.dataset.api, body);
      return;
    }
    const sw = ev.target.closest("button.switch[data-name]");
    if (sw) {
      sw.setAttribute("aria-checked", sw.getAttribute("aria-checked") === "true" ? "false" : "true");
      const hidden = document.getElementById(sw.dataset.name);
      if (hidden) { hidden.value = sw.getAttribute("aria-checked"); hidden.dispatchEvent(new Event("change", { bubbles: true })); }
    }
  });

  document.addEventListener("submit", (ev) => {
    const form = ev.target.closest("form[data-api-form]");
    if (!form) return;
    ev.preventDefault();
    const body = {};
    form.querySelectorAll("[name]").forEach((input) => {
      if (input.dataset.skipEmpty !== undefined && input.value === "") return;
      if (form.dataset.flat !== undefined) body[input.name] = parseValue(input);
      else setPath(body, input.name, parseValue(input));
    });
    const wrap = form.dataset.wrap;
    run(form.querySelector("[type=submit]") || form, form.dataset.apiForm, wrap ? { [wrap]: body } : body);
  });

  // Mark unsaved forms (settings, pricing)
  document.addEventListener("input", (ev) => {
    const form = ev.target.closest("form[data-dirty-label]");
    if (form) document.querySelectorAll("[data-dirty-target]").forEach((el) => { el.textContent = form.dataset.dirtyLabel; });
  });

  const pending = sessionStorage.getItem("pk_toast");
  if (pending) { sessionStorage.removeItem("pk_toast"); toast(pending, false); }

  // Live refresh: reload the page when something relevant changes (throttled).
  const live = document.body.dataset.live;
  if (live && "WebSocket" in window) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let timer = null;
    const connect = () => {
      const ws = new WebSocket(`${proto}://${location.host}/api/v1/ws`);
      ws.onmessage = (msg) => {
        const { event } = JSON.parse(msg.data);
        if (event === "ping" || !live.split(",").some((prefix) => event.startsWith(prefix))) return;
        if (document.activeElement && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
        clearTimeout(timer);
        timer = setTimeout(() => location.reload(), 1500);
      };
      ws.onclose = () => setTimeout(connect, 5000);
    };
    connect();
  }
})();
