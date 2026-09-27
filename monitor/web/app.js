/* =====================================================================
 * CKR Traffic Monitor — front-end (vanilla, no external deps, offline-ok)
 *
 * Backend contract:
 *   GET  /api/flows        -> { flows: [flow, ...] }
 *   GET  /api/flow/{id}    -> flow (full, untruncated body_hex)
 *   POST /api/clear        -> { ok: true }
 *   WS   /ws               -> {type:"snapshot", flows:[...]}  on connect
 *                             {type:"flow", flow:{...}}       on new/updated
 *
 * If no backend is reachable within 1.5s, mock data is loaded so the UI
 * can be previewed by opening index.html directly (file://).
 * ===================================================================== */
(function () {
  "use strict";

  // ---------------- state ----------------
  const flowsMap = new Map();   // id -> flow
  const order = [];             // ordered list of ids (display order)
  let selectedId = null;
  let selectedFlow = null;      // full flow object (may be fetched detail)

  let paused = false;
  const pauseBuffer = [];       // flows arriving while paused
  let autoscroll = true;

  let wsConnected = false;
  let backendReached = false;   // true if /api/flows or /ws ever succeeded
  let ws = null;

  const filterState = { text: "", protos: { grpc: true, http: true, raw: true } };

  // active body view mode per side: "json" | "decoded" | "hex" | null (=auto default)
  // null means "no explicit user choice yet" -> effectiveMode() picks the best
  // available view (json > decoded > hex) for whichever message is shown.
  const bodyMode = { request: null, response: null };
  let activeTab = "overview";

  // ---------------- dom refs ----------------
  const $ = (id) => document.getElementById(id);
  const wsDot = $("wsDot"), wsLabel = $("wsLabel");
  const flowBody = $("flowBody"), emptyList = $("emptyList"), tablewrap = $("tablewrap");
  const counter = $("counter"), filterCount = $("filterCount");
  const detailPlaceholder = $("detailPlaceholder"), detail = $("detail"), detailBody = $("detailBody");

  // ---------------- helpers ----------------
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Lightweight JSON syntax highlighter (no deps). Escapes only &,<,> so the
  // string quotes stay literal for the tokenizer; inside <pre> text content that
  // is sufficient for HTML safety. Any non-JSON leftover is emitted verbatim.
  function jsonHighlight(str) {
    const safe = String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return safe.replace(
      /("(?:\\u[0-9a-fA-F]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
      function (m) {
        let cls = "j-num";
        if (m.charAt(0) === '"') {
          cls = m.charAt(m.length - 1) === ":" ? "j-key" : "j-str";
        } else if (m === "true" || m === "false" || m === "null") {
          cls = "j-lit";
        }
        return '<span class="' + cls + '">' + m + '</span>';
      }
    );
  }

  function fmtBytes(n) {
    if (n === null || n === undefined) return "–";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(2) + " MB";
  }

  function fmtDur(ms) {
    if (ms === null || ms === undefined) return "";
    if (ms < 1) return ms.toFixed(2) + " ms";
    if (ms < 1000) return ms.toFixed(1) + " ms";
    return (ms / 1000).toFixed(2) + " s";
  }

  function statusClass(status) {
    if (status === null || status === undefined || status === "") return "status-none";
    const s = String(status).trim();
    // grpc-status "0" == OK
    if (s === "0") return "status-ok";
    const n = parseInt(s, 10);
    if (!isNaN(n)) {
      if (n >= 200 && n < 300) return "status-ok";
      if (n >= 400) return "status-err";
      if (n >= 1 && n < 100) return "status-err"; // non-zero grpc codes
    }
    return "status-none";
  }

  function nameCell(f) {
    if (f.service && f.rpc) {
      return '<span class="name-svc">' + esc(f.service) + '</span>' +
             '<span class="name-sep">.</span>' +
             '<span class="name-rpc">' + esc(f.rpc) + '</span>';
    }
    return '<span class="name-path">' + esc(f.path || "") + '</span>';
  }

  function parseJsonMaybe(raw) {
    if (!raw) return null;
    if (typeof raw === "object") return raw;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function afterplayOf(f) {
    if (f && f.afterplay && typeof f.afterplay === "object") return f.afterplay;
    const obj0 = parseJsonMaybe(f && f.request && f.request.body_json);
    const obj = Array.isArray(obj0) ? (obj0[0] || {}) : obj0;
    if (!obj || typeof obj !== "object") return null;
    const rpc = (f && f.rpc) || "";
    const combo = String(obj["19"] || "");
    const looks = rpc === "EpisodeAfterPlay"
      || combo.toLowerCase().indexOf("crg") >= 0
      || (obj["15"] != null && obj["9"] != null && obj["8"] != null);
    if (!looks) return null;
    let st = {};
    if (typeof obj["43"] === "string") st = parseJsonMaybe(obj["43"]) || {};
    else if (obj["43"] && typeof obj["43"] === "object") st = obj["43"];
    const cj = st.CoinJellyTotal;
    return {
      play_time: obj["15"],
      earned_coin: obj["9"],
      earned_exp: obj["10"],
      score: obj["8"],
      pureCoin: st.pureCoin,
      CoinJellyTotal: cj,
      coin_cap: (cj != null && cj !== "") ? Number(cj) * 280 : null,
      life_key: obj["11"],
    };
  }

  function playTimeLabel(ap) {
    if (!ap || ap.play_time === null || ap.play_time === undefined || ap.play_time === "") return "";
    return String(ap.play_time) + "s";
  }

  function shortHost(h) { return h || ""; }

  // ---------------- ingest / ordering ----------------
  function ingestFlow(flow) {
    if (!flow || flow.id === undefined || flow.id === null) return false;
    const existed = flowsMap.has(flow.id);
    if (existed) {
      // merge: keep any already-fetched full bodies unless new data supplies them
      const prev = flowsMap.get(flow.id);
      flowsMap.set(flow.id, Object.assign({}, prev, flow));
    } else {
      flowsMap.set(flow.id, flow);
      order.push(flow.id);
    }
    return !existed; // whether it's a brand new row
  }

  function loadSnapshot(list) {
    flowsMap.clear();
    order.length = 0;
    (list || []).forEach((f) => ingestFlow(f));
    renderList();
    // if the selected flow disappeared, clear
    if (selectedId !== null && !flowsMap.has(selectedId)) clearDetail();
  }

  // ---------------- filtering ----------------
  function passesFilter(f) {
    if (!filterState.protos[f.proto]) return false;
    const t = filterState.text.trim().toLowerCase();
    if (!t) return true;
    const hay = [f.host, f.path, f.service, f.rpc, f.method, f.status]
      .filter(Boolean).join(" ").toLowerCase();
    return hay.indexOf(t) !== -1;
  }

  // ---------------- rendering: list ----------------
  function renderList() {
    const frag = document.createDocumentFragment();
    let shown = 0;

    for (let i = 0; i < order.length; i++) {
      const f = flowsMap.get(order[i]);
      if (!f) continue;
      if (!passesFilter(f)) continue;
      shown++;

      const tr = document.createElement("tr");
      tr.dataset.id = f.id;
      if (f.id === selectedId) tr.classList.add("is-selected");
      if (f.dur_ms === null || f.dur_ms === undefined) tr.classList.add("is-pending");

      const proto = (f.proto || "raw").toLowerCase();
      const sizeStr = fmtBytes(f.req_size) + " / " + fmtBytes(f.res_size);
      const ap = afterplayOf(f);
      const play = playTimeLabel(ap);
      const nameTitle = (f.service && f.rpc) ? f.service + "." + f.rpc : (f.path || "");

      tr.innerHTML =
        '<td class="col-id">' + esc(f.id) + '</td>' +
        '<td class="col-time">' + esc(f.time || "") + '</td>' +
        '<td class="col-proto"><span class="badge ' + esc(proto) + '">' + esc(proto) + '</span></td>' +
        '<td class="col-host" title="' + esc(f.host || "") + '">' + esc(shortHost(f.host)) + '</td>' +
        '<td class="col-name" title="' + esc(nameTitle) + '">' + nameCell(f) + '</td>' +
        '<td class="col-method">' + esc(f.method || "") + '</td>' +
        '<td class="col-status ' + statusClass(f.status) + '">' + esc(f.status || "–") + '</td>' +
        '<td class="col-size">' + esc(sizeStr) + '</td>' +
        '<td class="col-dur">' + esc(fmtDur(f.dur_ms)) + '</td>';

      frag.appendChild(tr);
    }

    flowBody.innerHTML = "";
    flowBody.appendChild(frag);

    emptyList.style.display = order.length === 0 ? "block" : "none";
    counter.textContent = order.length + (order.length === 1 ? " flow" : " flows");
    filterCount.textContent = (shown === order.length)
      ? ""
      : (shown + " / " + order.length + " shown");

    if (autoscroll && !paused) {
      tablewrap.scrollTop = tablewrap.scrollHeight;
    }
  }

  function flashRow(id) {
    const tr = flowBody.querySelector('tr[data-id="' + id + '"]');
    if (tr) {
      tr.classList.remove("flash");
      void tr.offsetWidth; // reflow to restart animation
      tr.classList.add("flash");
    }
  }

  // ---------------- rendering: detail ----------------
  function clearDetail() {
    selectedId = null;
    selectedFlow = null;
    detail.hidden = true;
    detailPlaceholder.style.display = "flex";
  }

  function selectFlow(id) {
    selectedId = id;
    // highlight
    Array.prototype.forEach.call(flowBody.querySelectorAll("tr.is-selected"),
      (tr) => tr.classList.remove("is-selected"));
    const tr = flowBody.querySelector('tr[data-id="' + id + '"]');
    if (tr) tr.classList.add("is-selected");

    detailPlaceholder.style.display = "none";
    detail.hidden = false;

    // show what we have immediately, then fetch full body
    selectedFlow = flowsMap.get(id) || null;
    renderDetail();

    fetchFullFlow(id);
  }

  function fetchFullFlow(id) {
    fetch("/api/flow/" + encodeURIComponent(id))
      .then((r) => (r.ok ? r.json() : null))
      .then((full) => {
        if (!full || selectedId !== id) return;
        ingestFlow(full);              // merge full bodies into store
        selectedFlow = flowsMap.get(id);
        renderDetail();
      })
      .catch(() => { /* offline / mock: keep list version */ });
  }

  function setTab(tab) {
    activeTab = tab;
    Array.prototype.forEach.call(document.querySelectorAll(".detail-tabs .tab"), (b) => {
      b.classList.toggle("is-active", b.dataset.tab === tab);
    });
    renderDetail();
  }

  function renderDetail() {
    if (!selectedFlow) return;
    const f = selectedFlow;
    if (activeTab === "overview") detailBody.innerHTML = overviewHtml(f);
    else if (activeTab === "request") detailBody.innerHTML = messageHtml(f.request, "request");
    else detailBody.innerHTML = messageHtml(f.response, "response");
    wireDetailControls();
  }

  function overviewHtml(f) {
    const ap = afterplayOf(f);
    let html = "";
    if (ap && (ap.play_time != null || ap.earned_coin != null)) {
      html += '<div class="ap-box">';
      html += '<h3>AfterPlay</h3>';
      html += '<div class="ap-big">' + esc(playTimeLabel(ap) || "—") +
              ' <span style="font-size:13px;color:var(--fg-dim);font-weight:500">play_time</span></div>';
      html += '<table class="kv" style="margin:0">';
      const apRows = [
        ["play_time", ap.play_time != null ? ap.play_time + " วินาที" : ""],
        ["earned_coin", ap.earned_coin],
        ["earned_exp", ap.earned_exp],
        ["score", ap.score],
        ["pureCoin", ap.pureCoin],
        ["CoinJellyTotal", ap.CoinJellyTotal],
        ["coin cap (CJ×280)", ap.coin_cap],
        ["life_key", ap.life_key],
      ];
      apRows.forEach((r) => {
        if (r[1] === undefined || r[1] === null || r[1] === "") return;
        html += '<tr><td class="k">' + esc(r[0]) + '</td><td class="v">' + esc(r[1]) + '</td></tr>';
      });
      html += '</table></div>';
    }
    const rows = [
      ["proto", f.proto],
      ["host", f.host],
      ["path", f.path],
      ["service.rpc", (f.service || f.rpc) ? ((f.service || "") + (f.rpc ? "." + f.rpc : "")) : ""],
      ["method", f.method],
      ["status", f.status],
      ["ts", f.ts],
      ["time", f.time],
      ["dur_ms", (f.dur_ms === null || f.dur_ms === undefined) ? "(pending)" : f.dur_ms],
      ["req_size", fmtBytes(f.req_size)],
      ["res_size", fmtBytes(f.res_size)],
      ["id", f.id]
    ];
    html += '<table class="kv">';
    rows.forEach((r) => {
      if (r[1] === undefined || r[1] === null || r[1] === "") return;
      html += '<tr><td class="k">' + esc(r[0]) + '</td><td class="v">' + esc(r[1]) + '</td></tr>';
    });
    html += '</table>';
    return html;
  }

  // Resolve which body view to actually render for a message.
  // Honors an explicit user choice when that view has content; otherwise falls
  // back to the best available default: json > decoded(text) > hex.
  function effectiveMode(msg, side) {
    msg = msg || {};
    const hasJson = !!(msg.body_json && msg.body_json.length);
    const hasHex  = !!(msg.body_hex  && msg.body_hex.length);
    const hasText = !!(msg.body_text && msg.body_text.length);
    const want = bodyMode[side];
    if (want === "json" && hasJson) return "json";
    if (want === "hex"  && hasHex)  return "hex";
    if (want === "decoded") return "decoded";
    // auto (null) or a chosen view that isn't available for this message
    if (hasJson) return "json";
    if (hasText) return "decoded";
    if (hasHex)  return "hex";
    return "decoded";
  }

  function messageHtml(msg, side) {
    msg = msg || {};
    const headers = msg.headers || [];
    const kind = msg.body_kind || "empty";
    const mode = effectiveMode(msg, side);

    let html = "";

    // ---- headers ----
    html += '<div class="section-title">Headers <span style="color:var(--fg-faint)">(' + headers.length + ')</span></div>';
    if (headers.length) {
      html += '<table class="headers-table">';
      headers.forEach((h) => {
        const k = Array.isArray(h) ? h[0] : "";
        const v = Array.isArray(h) ? h[1] : "";
        html += '<tr><td class="hk">' + esc(k) + '</td><td class="hv">' + esc(v) + '</td></tr>';
      });
      html += '</table>';
    } else {
      html += '<div class="body-pre empty-body" style="margin-bottom:16px">(no headers)</div>';
    }

    // ---- body ----
    const hasJson = !!(msg.body_json && msg.body_json.length);
    const hasHex = !!(msg.body_hex && msg.body_hex.length);
    const dis = 'disabled style="opacity:.4;cursor:default"';
    html += '<div class="section-title">Body</div>';
    html += '<div class="body-block" data-side="' + side + '">';
    html += '  <div class="body-toolbar">';
    html += '    <span class="kind-pill ' + esc(kind) + '">' + esc(kind) + '</span>';
    html += '    <span class="seg" data-side="' + side + '">' +
            '      <button data-mode="json" class="' + (mode === "json" ? "is-active" : "") + '" ' + (hasJson ? "" : dis) + '>JSON</button>' +
            '      <button data-mode="decoded" class="' + (mode === "decoded" ? "is-active" : "") + '">Decoded</button>' +
            '      <button data-mode="hex" class="' + (mode === "hex" ? "is-active" : "") + '" ' + (hasHex ? "" : dis) + '>Hex</button>' +
            '    </span>';
    html += '    <button class="body-copy" data-side="' + side + '">Copy</button>';
    html += '  </div>';

    const text = msg.body_text || "";
    if (mode === "hex") {
      const hex = msg.body_hex || "";
      html += '<pre class="body-pre hex' + (hex ? "" : " empty-body") + '">' + esc(hex || "(no bytes)") + '</pre>';
    } else if (mode === "json") {
      const jsonStr = msg.body_json || "";
      html += '<pre class="body-pre json' + (jsonStr ? "" : " empty-body") + '">' +
              (jsonStr ? jsonHighlight(jsonStr) : esc("(no json)")) + '</pre>';
    } else {
      html += '<pre class="body-pre' + (text ? "" : " empty-body") + '">' + esc(text || "(empty)") + '</pre>';
    }
    html += '</div>';
    return html;
  }

  function wireDetailControls() {
    // decoded / hex segmented control
    Array.prototype.forEach.call(detailBody.querySelectorAll(".seg button"), (btn) => {
      btn.addEventListener("click", () => {
        if (btn.disabled) return;
        const side = btn.parentElement.dataset.side;
        bodyMode[side] = btn.dataset.mode;
        renderDetail();
      });
    });
    // copy buttons
    Array.prototype.forEach.call(detailBody.querySelectorAll(".body-copy"), (btn) => {
      btn.addEventListener("click", () => {
        const side = btn.dataset.side;
        const msg = (side === "request" ? selectedFlow.request : selectedFlow.response) || {};
        const mode = effectiveMode(msg, side);
        const payload = mode === "hex" ? (msg.body_hex || "")
                      : mode === "json" ? (msg.body_json || "")
                      : (msg.body_text || "");
        copyText(payload, btn);
      });
    });
  }

  function copyText(text, btn) {
    const done = () => {
      if (!btn) return;
      const old = btn.textContent;
      btn.textContent = "Copied!";
      btn.classList.add("copied");
      setTimeout(() => { btn.textContent = old; btn.classList.remove("copied"); }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
    }
  }
  function fallbackCopy(text, done) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      done();
    } catch (e) { /* ignore */ }
  }

  // ---------------- incoming flow (live) ----------------
  function onIncomingFlow(flow) {
    if (paused) { pauseBuffer.push(flow); return; }
    const isNew = ingestFlow(flow);
    // update selected detail if it's this flow
    if (flow.id === selectedId) {
      selectedFlow = flowsMap.get(flow.id);
      renderDetail();
    }
    renderList();
    if (isNew) flashRow(flow.id);
  }

  function drainPauseBuffer() {
    if (!pauseBuffer.length) return;
    pauseBuffer.forEach((f) => ingestFlow(f));
    pauseBuffer.length = 0;
    if (selectedId !== null) { selectedFlow = flowsMap.get(selectedId); renderDetail(); }
    renderList();
  }

  // ---------------- WebSocket ----------------
  async function pollAttachStatus() {
    try {
      const r = await fetch("/api/status", { cache: "no-store" });
      if (!r.ok) return;
      const s = await r.json();
      if (!wsConnected) return;
      const pid = s.pid ? ("pid " + s.pid) : "รอเกม";
      const kb = ((s.bytes_w || 0) + (s.bytes_r || 0));
      wsLabel.textContent = "connected · " + pid + " · " + kb + " B";
    } catch (e) {}
  }
  setInterval(function () { if (wsConnected) pollAttachStatus(); }, 2000);

  function setWsUi(mode) {
    // mode: "up" | "down" | "wait" | "mock"
    wsDot.className = "ws-dot";
    if (mode === "up")   { wsDot.classList.add("is-up");   wsLabel.textContent = "connected"; pollAttachStatus(); }
    else if (mode === "wait") { wsDot.classList.add("is-wait"); wsLabel.textContent = "connecting…"; }
    else if (mode === "mock") { wsDot.classList.add("is-mock"); wsLabel.textContent = "mock (offline)"; }
    else { wsLabel.textContent = "disconnected"; }
  }

  function connectWS() {
    if (location.protocol === "file:") { return; } // no WS possible from file://
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    let url;
    try {
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      url = scheme + "//" + location.host + "/ws";
    } catch (e) { return; }

    setWsUi("wait");
    try { ws = new WebSocket(url); }
    catch (e) { setWsUi("down"); return; }

    ws.onopen = function () {
      wsConnected = true; backendReached = true;
      setWsUi("up");
    };
    ws.onmessage = function (ev) {
      let data;
      try { data = JSON.parse(ev.data); } catch (e) { return; }
      if (data.type === "snapshot") {
        loadSnapshot(data.flows || []);
      } else if (data.type === "flow" && data.flow) {
        onIncomingFlow(data.flow);
      }
    };
    ws.onclose = function () {
      wsConnected = false;
      setWsUi("down");
      setTimeout(connectWS, 2000);
    };
    ws.onerror = function () { /* onclose will follow */ };
  }

  // ---------------- initial REST load ----------------
  function initData() {
    if (location.protocol === "file:") { return Promise.resolve(false); }
    return fetch("/api/flows")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && Array.isArray(data.flows)) {
          backendReached = true;
          // only load if WS snapshot hasn't already populated
          if (order.length === 0) loadSnapshot(data.flows);
          return true;
        }
        return false;
      })
      .catch(() => false);
  }

  // ---------------- Clear ----------------
  function clearAll() {
    // optimistic local clear
    loadSnapshot([]);
    clearDetail();
    pauseBuffer.length = 0;
    if (location.protocol !== "file:") {
      fetch("/api/clear", { method: "POST" }).catch(() => {});
    }
  }

  // ---------------- controls wiring ----------------
  function wireControls() {
    // Clear
    $("btnClear").addEventListener("click", clearAll);

    // Pause / resume
    const btnPause = $("btnPause");
    btnPause.addEventListener("click", () => {
      paused = !paused;
      btnPause.classList.toggle("is-paused", paused);
      $("pauseLabel").textContent = paused ? "Resume" : "Pause";
      $("pauseIco").innerHTML = paused ? "&#9654;" : "&#10073;&#10073;";
      if (!paused) drainPauseBuffer();
    });

    // Auto-scroll
    const btnAuto = $("btnAutoscroll");
    btnAuto.addEventListener("click", () => {
      autoscroll = !autoscroll;
      btnAuto.classList.toggle("is-on", autoscroll);
      if (autoscroll) tablewrap.scrollTop = tablewrap.scrollHeight;
    });

    // Filter text
    const filterText = $("filterText");
    filterText.addEventListener("input", () => {
      filterState.text = filterText.value;
      renderList();
    });
    $("filterClear").addEventListener("click", () => {
      filterText.value = "";
      filterState.text = "";
      renderList();
      filterText.focus();
    });

    // Proto toggles
    Array.prototype.forEach.call(document.querySelectorAll(".proto-toggle"), (btn) => {
      btn.addEventListener("click", () => {
        const p = btn.dataset.proto;
        filterState.protos[p] = !filterState.protos[p];
        btn.classList.toggle("is-on", filterState.protos[p]);
        renderList();
      });
    });

    // Row selection (event delegation)
    flowBody.addEventListener("click", (e) => {
      const tr = e.target.closest("tr");
      if (!tr || !tr.dataset.id) return;
      const id = parseInt(tr.dataset.id, 10);
      selectFlow(isNaN(id) ? tr.dataset.id : id);
    });

    // Tabs
    Array.prototype.forEach.call(document.querySelectorAll(".detail-tabs .tab"), (b) => {
      b.addEventListener("click", () => setTab(b.dataset.tab));
    });

    // Export dropdown
    wireExport();

    // If user scrolls up manually, keep auto-scroll but don't fight — simple: leave as-is.
    wireDivider();
  }

  // ---------------- export ----------------
  // Build query params from the active filter so the export matches what's on screen.
  function exportQuery(fmt) {
    const params = new URLSearchParams();
    params.set("format", fmt);
    params.set("download", "0");            // fetch as blob so we can read X-Export-Path
    const protos = Object.keys(filterState.protos).filter((p) => filterState.protos[p]);
    if (protos.length && protos.length < 4) params.set("proto", protos.join(","));
    const q = filterState.text.trim();
    if (q) params.set("q", q);
    return params.toString();
  }

  function showToast(msg, isErr) {
    const el = $("toast");
    if (!el) return;
    el.innerHTML = msg;
    el.classList.toggle("is-err", !!isErr);
    el.hidden = false;
    el.classList.add("show");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => { el.hidden = true; }, 250);
    }, 6000);
  }

  function doExport(fmt) {
    // offline / file:// preview -> client-side JSON only
    if (location.protocol === "file:" || !backendReached) {
      const flows = order.map((id) => flowsMap.get(id)).filter(Boolean);
      const blob = new Blob([JSON.stringify({ flows: flows }, null, 2)],
        { type: "application/json" });
      triggerDownload(blob, "ckr_traffic_offline.json");
      showToast("offline — บันทึกเป็น JSON ฝั่ง browser (ไม่ได้เขียนลง exports/)", true);
      return;
    }
    fetch("/api/export?" + exportQuery(fmt))
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        const path = r.headers.get("X-Export-Path") || "";
        const count = r.headers.get("X-Export-Count") || "?";
        return r.blob().then((b) => ({ b: b, path: path, count: count }));
      })
      .then((res) => {
        const fname = (res.path.split(/[\\/]/).pop()) || ("ckr_traffic." + fmt);
        triggerDownload(res.b, fname);
        const disk = res.path
          ? ('<br><span class="toast-path">เขียนลง disk: <code>' + esc(res.path) + '</code></span>')
          : "";
        showToast("export " + esc(res.count) + " flow (" + esc(fmt) + ") เรียบร้อย" + disk);
      })
      .catch((e) => showToast("export ล้มเหลว: " + esc(e.message || e), true));
  }

  function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function wireExport() {
    const wrap = $("exportWrap");
    const btn = $("btnExport");
    const menu = $("exportMenu");
    if (!wrap || !btn || !menu) return;

    function closeMenu() {
      menu.hidden = true;
      btn.classList.remove("is-open");
      document.removeEventListener("click", onDocClick, true);
    }
    function onDocClick(e) {
      if (!wrap.contains(e.target)) closeMenu();
    }
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = menu.hidden;
      menu.hidden = !open;
      btn.classList.toggle("is-open", open);
      if (open) document.addEventListener("click", onDocClick, true);
    });
    Array.prototype.forEach.call(menu.querySelectorAll(".menu-item"), (mi) => {
      mi.addEventListener("click", () => {
        closeMenu();
        if (mi.dataset.fmt === "all") doExportAll();
        else doExport(mi.dataset.fmt);
      });
    });
  }

  // Write all three formats to exports/ in one shot (for agents to pick up).
  // No browser download here — the point is the files on disk.
  function doExportAll() {
    if (location.protocol === "file:" || !backendReached) {
      doExport("json");   // offline: fall back to the client-side JSON blob
      return;
    }
    const fmts = ["md", "json", "jsonl"];
    Promise.all(fmts.map((fmt) =>
      fetch("/api/export?" + exportQuery(fmt)).then((r) => {
        if (!r.ok) throw new Error(fmt + " HTTP " + r.status);
        return { fmt: fmt, path: r.headers.get("X-Export-Path") || "",
                 count: r.headers.get("X-Export-Count") || "?" };
      })
    )).then((results) => {
      const count = results[0].count;
      const dir = (results[0].path || "").replace(/[\\/][^\\/]+$/, "");
      const files = results
        .map((r) => "<code>" + esc((r.path || "").split(/[\\/]/).pop()) + "</code>")
        .join(" · ");
      const loc = dir
        ? ('<br><span class="toast-path">ที่: <code>' + esc(dir) + '</code></span>')
        : "";
      showToast("export ทั้งหมด " + esc(count) + " flow → 3 ไฟล์" + loc + "<br>" + files);
    }).catch((e) => showToast("export all ล้มเหลว: " + esc(e.message || e), true));
  }

  // ---------------- resizable divider ----------------
  function wireDivider() {
    const divider = $("divider");
    const main = $("main");
    const paneLeft = $("paneLeft");
    let dragging = false;

    function onMove(e) {
      if (!dragging) return;
      const rect = main.getBoundingClientRect();
      const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
      let pct = (x / rect.width) * 100;
      pct = Math.max(25, Math.min(80, pct));
      paneLeft.style.flex = "0 0 " + pct + "%";
      e.preventDefault();
    }
    function onUp() {
      dragging = false;
      divider.classList.remove("dragging");
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onUp);
    }
    function onDown(e) {
      dragging = true;
      divider.classList.add("dragging");
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      window.addEventListener("touchmove", onMove, { passive: false });
      window.addEventListener("touchend", onUp);
    }
    divider.addEventListener("mousedown", onDown);
    divider.addEventListener("touchstart", onDown, { passive: false });
  }

  // ---------------- mock data (offline preview) ----------------
  function mockFlows() {
    return [
      {
        id: 1, ts: 1723300000.121, time: "14:20:00.121",
        proto: "ds", host: "api.game.crg.devsisters.com", path: "/game/episodeBeforePlay.ds",
        service: "", rpc: "", method: "POST", status: "200", dur_ms: 211.4,
        req_size: 344, res_size: 512,
        request: {
          headers: [
            ["content-type", "application/x-www-form-urlencoded"],
            ["user-agent", "BestHTTP"],
            ["host", "api.game.crg.devsisters.com"]
          ],
          body_text: "isEncryptedData=4&data=<ChaCha20+FastLZ blob>\n\n[ds-decrypted]\n{\n  \"memberSeq\": 108452231,\n  \"episodeSeq\": 1,\n  \"groupSeq\": 1002,\n  \"ms\": 48213\n}",
          body_hex: "69 73 45 6e 63 72 79 70 74 65 64 44 61 74 61 3d 34 26 64 61 74 61 3d 2e 2e 2e",
          body_json: "{\n  \"memberSeq\": 108452231,\n  \"episodeSeq\": 1,\n  \"groupSeq\": 1002,\n  \"ms\": 48213\n}",
          body_kind: "ds-decrypted"
        },
        response: {
          headers: [["content-type", "text/plain"], ["content-length", "512"]],
          body_text: "[ds-decrypted]\n{\n  \"resultCode\": 0,\n  \"lifeKey\": \"a1b2c3d4-5566-7788-99aa-bbccddeeff00\",\n  \"life\": 4\n}",
          body_hex: "34 26 64 61 74 61 3d 5a 58 6c 4b 61 47 4a 48 59 32 6c 50 61 55 70 76 ...",
          body_json: "{\n  \"resultCode\": 0,\n  \"lifeKey\": \"a1b2c3d4-5566-7788-99aa-bbccddeeff00\",\n  \"life\": 4\n}",
          body_kind: "ds-decrypted"
        }
      },
      {
        id: 2, ts: 1723300001.402, time: "14:20:01.402",
        proto: "grpc", host: "service.api.crg.devsisters.com", path: "/EpisodeAPI/GetLeaguePlace",
        service: "EpisodeAPI", rpc: "GetLeaguePlace", method: "POST", status: "0", dur_ms: 34.2,
        req_size: 128, res_size: 96,
        request: {
          headers: [
            ["content-type", "application/grpc"],
            ["authorization", "Bearer eyJhbGciOi…(truncated)"],
            ["player-id", "108452231"],
            ["combo-name", "26.7.0_crg_rhrnakakt"],
            ["grpc-encoding", "identity"]
          ],
          body_text: "message GetLeaguePlaceReq {\n  1 season_id: 42\n  2 member_seq: 108452231\n}",
          body_hex: "00 00 00 00 08 08 2a 10 87 d5 8b 33",
          body_json: "{\n  \"1\": 42,\n  \"2\": 108452231\n}",
          body_kind: "protobuf"
        },
        response: {
          headers: [["content-type", "application/grpc"], ["grpc-status", "0"]],
          body_text: "message GetLeaguePlaceRes {\n  1 place: 3\n  2 league: \"gold\"\n  3 score: 18240\n}",
          body_hex: "00 00 00 00 0d 08 03 12 04 67 6f 6c 64 18 c0 8e 01",
          body_json: "{\n  \"1\": 3,\n  \"2\": \"gold\",\n  \"3\": 18240\n}",
          body_kind: "protobuf"
        }
      },
      {
        id: 3, ts: 1723300002.988, time: "14:20:02.988",
        proto: "grpc", host: "service.api.crg.devsisters.com", path: "/FriendAPI/ListFriends",
        service: "FriendAPI", rpc: "ListFriends", method: "POST", status: "0", dur_ms: 58.9,
        req_size: 64, res_size: 2048,
        request: {
          headers: [["content-type", "application/grpc"], ["player-id", "108452231"]],
          body_text: "message ListFriendsReq {\n  1 page: 0\n  2 page_size: 50\n}",
          body_hex: "00 00 00 00 04 08 00 10 32",
          body_json: "{\n  \"1\": 0,\n  \"2\": 50\n}",
          body_kind: "protobuf"
        },
        response: {
          headers: [["content-type", "application/grpc"], ["grpc-status", "0"]],
          body_text: "message ListFriendsRes {\n  1 friends: [ 47 entries … ]\n  2 total: 47\n}",
          body_hex: "00 00 00 08 00 0a 20 08 aa bb cc dd 12 06 43 6f 6f 6b 69 65 ...",
          body_json: "{\n  \"1\": [\n    { \"1\": 3735928559, \"2\": \"Cookie\" }\n  ],\n  \"2\": 47\n}",
          body_kind: "protobuf"
        }
      },
      {
        id: 4, ts: 1723300004.010, time: "14:20:04.010",
        proto: "http", host: "account.devplay.com", path: "/v3/login/refresh",
        service: "", rpc: "", method: "POST", status: "200", dur_ms: 142.7,
        req_size: 210, res_size: 780,
        request: {
          headers: [
            ["content-type", "application/json"],
            ["x-device-id", "b8f1c0a2e9d34f57"],
            ["accept", "application/json"]
          ],
          body_text: "{\n  \"guest_token\": \"gt_9f83…\",\n  \"device_secret\": \"\",\n  \"market_type\": \"google\"\n}",
          body_hex: "7b 22 67 75 65 73 74 5f 74 6f 6b 65 6e 22 3a 22 67 74 5f 39 66 38 33 ...",
          body_json: "{\n  \"guest_token\": \"gt_9f83…\",\n  \"device_secret\": \"\",\n  \"market_type\": \"google\"\n}",
          body_kind: "json"
        },
        response: {
          headers: [["content-type", "application/json"], ["set-cookie", "sess=…; HttpOnly"]],
          body_text: "{\n  \"game_access_token\": \"eyJhbGciOiJI…\",\n  \"expires_in\": 3600,\n  \"player_id\": 108452231\n}",
          body_hex: "7b 22 67 61 6d 65 5f 61 63 63 65 73 73 5f 74 6f 6b 65 6e 22 3a 22 ...",
          body_json: "{\n  \"game_access_token\": \"eyJhbGciOiJI…\",\n  \"expires_in\": 3600,\n  \"player_id\": 108452231\n}",
          body_kind: "json"
        }
      },
      {
        id: 5, ts: 1723300005.560, time: "14:20:05.560",
        proto: "ds", host: "api.shop.crg.devsisters.com", path: "/shop/buyStuff.ds",
        service: "", rpc: "", method: "POST", status: "504", dur_ms: 998.2,
        req_size: 288, res_size: 64,
        request: {
          headers: [["content-type", "application/x-www-form-urlencoded"], ["user-agent", "BestHTTP"]],
          body_text: "[ds-decrypted]\n{\n  \"memberSeq\": 108452231,\n  \"stuffSeq\": 804,\n  \"buyType\": 1,\n  \"ms\": 0\n}   // ms=0 -> ILLEGAL PARAMETER",
          body_hex: "69 73 45 6e 63 72 79 70 74 65 64 44 61 74 61 3d 34 26 64 61 74 61 3d ...",
          body_json: "{\n  \"memberSeq\": 108452231,\n  \"stuffSeq\": 804,\n  \"buyType\": 1,\n  \"ms\": 0\n}",
          body_kind: "ds-decrypted"
        },
        response: {
          headers: [["content-type", "text/plain"]],
          body_text: "[ds-decrypted]\n{\n  \"resultCode\": 504,\n  \"message\": \"ILLEGAL PARAMETER\"\n}",
          body_hex: "35 30 34 20 49 4c 4c 45 47 41 4c 20 50 41 52 41 4d 45 54 45 52",
          body_json: "{\n  \"resultCode\": 504,\n  \"message\": \"ILLEGAL PARAMETER\"\n}",
          body_kind: "ds-decrypted"
        }
      },
      {
        id: 6, ts: 1723300006.900, time: "14:20:06.900",
        proto: "raw", host: "gameplay.crg.devsisters.com:17012", path: "(tcp match session)",
        service: "", rpc: "", method: "", status: "", dur_ms: null,
        req_size: 40, res_size: null,
        request: {
          headers: [],
          body_text: "",
          body_hex: "de ad be ef 00 12 04 00 aa bb cc dd 01 00 00 10",
          body_json: "",
          body_kind: "raw"
        },
        response: { headers: [], body_text: "", body_hex: "", body_json: "", body_kind: "empty" }
      }
    ];
  }

  function loadMockIfNeeded() {
    if (backendReached || wsConnected) return;
    setWsUi("mock");
    loadSnapshot(mockFlows());
  }

  // ---------------- boot ----------------
  function boot() {
    wireControls();
    setWsUi("wait");

    connectWS();
    initData();

    // fallback to mock if nothing reachable within 1.5s
    setTimeout(loadMockIfNeeded, 1500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
