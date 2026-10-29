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

  // Presence sensor live view (control devices page): camera stream + current values, polled every second.
  let liveTimer = null;
  function closeLive(panel) {
    clearInterval(liveTimer);
    liveTimer = null;
    const img = panel.querySelector("[data-live-img]");
    img.removeAttribute("src");                                  // closes the MJPEG connection
    panel.hidden = true;
    document.querySelectorAll("[data-live-sensor][aria-expanded='true']").forEach((b) => b.setAttribute("aria-expanded", "false"));
  }
  function showValues(panel, data) {
    const L = JSON.parse(panel.querySelector("[data-live-i18n]").textContent);
    const set = (key, text, cls) => { const el = panel.querySelector(`[data-v="${key}"]`); el.textContent = text; el.className = cls || (key === "plate" ? "mono" : ""); };
    const time = (iso) => iso ? new Date(iso).toLocaleTimeString(document.documentElement.lang || undefined) : "–";
    const cameraLost = data.online && data.camera_ok === false;
    panel.querySelector("[data-live-lamp]").className = "lamp " + (!data.online || cameraLost ? "alarm" : data.thermal_state === "normal" ? "ok" : "warning");
    const offline = panel.querySelector("[data-live-offline]");
    offline.hidden = data.online && !cameraLost;
    offline.textContent = cameraLost ? `${L.camera_lost} (${L.last_frame} ${time(data.last_frame_at)})` : L.offline;
    const img = panel.querySelector("[data-live-img]");
    img.hidden = !data.online || cameraLost;
    const seeing = data.online && !cameraLost;
    if (seeing && panel.dataset.seeing === "false") img.src = `/devices/${panel.dataset.sensorId}/stream.mjpg?t=${Date.now()}`;  // the stream ended while the camera was lost
    panel.dataset.seeing = seeing;
    if (!data.online) { set("occupied", L.offline); set("camera", "–"); return; }
    set("camera", cameraLost ? L.camera_lost_short : data.camera_ok === null ? L.camera_starting : L.camera_ok, cameraLost ? "red" : "");
    set("occupied", data.occupied ? L.occupied : L.free, data.occupied ? "occupied" : "free");
    set("plate", data.plate || L.none);
    set("confidence", data.confidence == null ? "–" : Math.round(data.confidence * 100) + " %");
    set("plate_since", time(data.plate_since));
    set("analysed_fps", data.analysed_fps == null ? "–" : data.analysed_fps.toFixed(1) + " /s");
    set("stream_fps", data.stream_fps == null ? "–" : data.stream_fps.toFixed(0) + " /s");
    set("cpu_temp_c", data.cpu_temp_c == null ? "–" : data.cpu_temp_c.toFixed(1) + " °C");
    set("thermal_state", L[data.thermal_state] || data.thermal_state || "–");
    set("updated_at", time(data.updated_at));
  }
  document.addEventListener("click", (ev) => {
    const close = ev.target.closest("[data-live-close]");
    if (close) { closeLive(close.closest(".sensor-live")); return; }
    const btn = ev.target.closest("[data-live-sensor]");
    if (!btn) return;
    const panel = document.getElementById(btn.getAttribute("aria-controls"));
    const open = btn.getAttribute("aria-expanded") === "true";
    document.querySelectorAll(".sensor-live:not([hidden])").forEach(closeLive);
    if (open) return;
    const id = btn.dataset.liveSensor;
    btn.setAttribute("aria-expanded", "true");
    panel.querySelector("[data-live-code]").textContent = btn.dataset.code;
    panel.querySelector("[data-live-where]").textContent = btn.dataset.where;
    panel.querySelector("[data-live-img]").src = `/devices/${id}/stream.mjpg?t=${Date.now()}`;
    panel.dataset.sensorId = id;
    panel.dataset.seeing = "true";
    panel.hidden = false;
    const poll = () => fetch(`/devices/${id}/live.json`, { credentials: "same-origin", cache: "no-store" })
      .then((r) => r.json()).then((data) => showValues(panel, data)).catch(() => showValues(panel, { online: false }));
    poll();
    liveTimer = setInterval(poll, 1000);
  });

  const pending = sessionStorage.getItem("pk_toast");
  if (pending) { sessionStorage.removeItem("pk_toast"); toast(pending, false); }

  // Live refresh at 0.5 Hz: every 2 s the page asks the server for the fingerprint of its data and
  // reloads when a cabinet published new values; it also shows the communication watchdog.
  const WD_PERIOD_MS = 2000;
  const scope = document.body.dataset.liveScope;
  if (document.body.dataset.live) {
    const wdText = document.querySelector("[data-wd-i18n]");
    const W = wdText ? JSON.parse(wdText.textContent) : null;
    const lang = document.documentElement.lang || undefined;
    let baseline = null;
    const busy = () => (document.activeElement && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName))
      || document.querySelector(".sensor-live:not([hidden])");
    const showWatchdog = (cabinets) => {
      document.querySelectorAll("[data-watchdog]").forEach((box) => {
        const list = box.querySelector("[data-wd-list]");
        const items = cabinets[box.dataset.watchdog] || [];
        list.replaceChildren(...(items.length ? items.map((w) => {
          const el = document.createElement("span");
          el.className = "wd " + (w.ok ? "ok" : "lost");
          const lamp = document.createElement("span");
          lamp.className = "lamp " + (w.ok ? "ok" : "alarm");
          lamp.setAttribute("aria-hidden", "true");
          const detail = w.ok ? W.ok.replace("{age}", w.age_s.toLocaleString(lang)).replace("{seq}", w.seq)
            : W.lost.replace("{time}", new Date(w.lost_since || w.last).toLocaleTimeString(lang));
          el.append(lamp, `${W[w.source] || w.source} · ${detail}`);
          return el;
        }) : [Object.assign(document.createElement("span"), { className: "muted", textContent: W.none })]));
      });
    };
    const tick = () => fetch(`/live/state.json?scope=${scope}`, { credentials: "same-origin", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        if (W) showWatchdog(data.cabinets);
        if (baseline === null) baseline = data.version;
        else if (data.version !== baseline && !busy()) location.reload();
      })
      .catch(() => {});
    tick();
    setInterval(tick, WD_PERIOD_MS);
  }

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
        if (document.querySelector(".sensor-live:not([hidden])")) return;   // keep a sensor's live view open
        clearTimeout(timer);
        timer = setTimeout(() => location.reload(), 1500);
      };
      ws.onclose = () => setTimeout(connect, 5000);
    };
    connect();
  }
})();
