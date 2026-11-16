// KataPilot service worker: keeps a WebSocket to the local backend and executes browser actions
// in the user's GUVI tab. Only *.guvi.in pages are ever touched. Nothing is ever submitted.

const DEFAULT_BACKEND = "http://127.0.0.1:8765";
const GUVI_HOST = /(^|\.)guvi\.in$/i;
let ws = null;
let pingTimer = null;
let reconnectDelay = 1000;
let reconnectTimer = null;
let lastError = null;

// ------------------------------------------------------------------ config & pairing
async function cfg() {
  const c = await chrome.storage.local.get(["backend", "token"]);
  return { backend: c.backend || DEFAULT_BACKEND, token: c.token || null };
}

async function pair(backend) {
  const r = await fetch(`${backend}/api/bootstrap`);
  if (!r.ok) throw new Error(`Pairing failed (HTTP ${r.status})`);
  const { token } = await r.json();
  await chrome.storage.local.set({ token });
  return token;
}

function setBadge(on) {
  chrome.action.setBadgeText({ text: on ? "" : "off" });
  chrome.action.setBadgeBackgroundColor({ color: on ? "#4f7cff" : "#6b7280" });
}

// ------------------------------------------------------------------ connection
let connecting = false;
async function connect() {
  if (connecting || (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING))) return;
  connecting = true;
  try { await openSocket(); } finally { connecting = false; }
}

async function openSocket() {
  const { backend } = await cfg();
  let { token } = await cfg();
  try {
    if (!token) token = await pair(backend);
  } catch (e) {
    lastError = `Backend not reachable at ${backend}. Start it with ./run.sh`;
    setBadge(false);
    return scheduleReconnect();
  }
  const url = backend.replace(/^http/, "ws") + "/ws/extension?token=" + encodeURIComponent(token);
  const sock = new WebSocket(url);
  ws = sock;
  ws.onopen = () => {
    reconnectDelay = 1000;
    lastError = null;
    setBadge(true);
    send({ type: "hello", data: { version: chrome.runtime.getManifest().version, userAgent: navigator.userAgent } });
    clearInterval(pingTimer);
    // WebSocket traffic every 20s keeps the MV3 service worker alive (Chrome 116+).
    pingTimer = setInterval(() => send({ type: "ping" }), 20000);
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "request") handleRequest(msg);
  };
  ws.onclose = async (ev) => {
    if (ws !== sock) return;          // an older socket closing must not disturb the live one
    clearInterval(pingTimer);
    setBadge(false);
    ws = null;
    if (ev.code === 4000) return;     // the engine replaced this connection with a newer one
    if (ev.code === 4401) {
      await chrome.storage.local.remove("token");   // token rotated: re-pair on next attempt
      lastError = "Pairing token rejected; re-pairing.";
    }
    scheduleReconnect();
  };
  ws.onerror = () => { lastError = `Engine not reachable at ${backend}`; };   // expected while the engine is off; retried automatically
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 30000);
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

chrome.alarms.create("katapilot-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "katapilot-keepalive") connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
// Clicking the toolbar icon opens the KataPilot side panel next to the GUVI page.
chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

// ------------------------------------------------------------------ tabs
class ActionError extends Error {
  constructor(message, code) { super(message); this.code = code; }
}

const isGuvi = (url) => { try { const u = new URL(url); return u.protocol === "https:" && GUVI_HOST.test(u.hostname); } catch { return false; } };

async function getWorkingTab() {
  const { workingTabId } = await chrome.storage.session.get("workingTabId");
  if (workingTabId != null) {
    try {
      const t = await chrome.tabs.get(workingTabId);
      if (t && isGuvi(t.url || t.pendingUrl || "")) return t;
    } catch { /* closed */ }
  }
  return null;
}

async function claimTab(preferActive = true) {
  let tab = null;
  if (preferActive) {
    const [active] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (active && isGuvi(active.url || "")) tab = active;
  }
  tab = tab || (await getWorkingTab());
  if (!tab) {
    const all = await chrome.tabs.query({ url: "https://*.guvi.in/*" });
    tab = all[0] || null;
  }
  if (!tab) throw new ActionError("No GUVI tab is open. Open www.guvi.in (signed in) in Chrome.", "no_guvi_tab");
  await chrome.storage.session.set({ workingTabId: tab.id });
  return tab;
}

async function requireTab() {
  const t = await getWorkingTab();
  if (t) return t;
  try { return await claimTab(false); } catch { throw new ActionError("The agent's GUVI tab was closed.", "tab_closed"); }
}

function waitForLoad(tabId, { urlMatch, timeoutMs = 30000 } = {}) {
  return new Promise((resolve, reject) => {
    const done = (fn, arg) => { clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); fn(arg); };
    const listener = (id, info, tab) => {
      if (id !== tabId) return;
      if (info.status === "complete" && (!urlMatch || urlMatch(tab.url || ""))) done(resolve, tab);
    };
    const timer = setTimeout(() => done(reject, new ActionError(
      "Navigation did not finish. GUVI may be showing a 'Leave site?' dialog — choose in the tab, then press Resume.",
      "navigation_blocked")), timeoutMs);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function navigate(url) {
  if (!isGuvi(url)) throw new ActionError("Refusing to navigate outside guvi.in", "bad_url");
  const tab = await requireTab();
  const same = (u) => u.split("#")[0].replace(/\/$/, "") === url.split("#")[0].replace(/\/$/, "");
  if (same(tab.url || "")) {
    await chrome.tabs.reload(tab.id);
  } else {
    await chrome.tabs.update(tab.id, { url });
  }
  const loaded = await waitForLoad(tab.id, { timeoutMs: 35000 });
  if (/sign-?in|login/i.test(new URL(loaded.url).pathname)) throw new ActionError("GUVI redirected to sign-in.", "login_required");
  return { url: loaded.url, title: loaded.title };
}

// ------------------------------------------------------------------ adapter execution
async function runAdapter(method, params = {}, { allFrames = false } = {}) {
  const tab = await requireTab();
  if (!isGuvi(tab.url || "")) throw new ActionError("The agent tab is no longer on guvi.in", "bad_url");
  const target = { tabId: tab.id, allFrames };
  await chrome.scripting.executeScript({ target, files: ["adapter.js"], world: "MAIN" });
  const results = await chrome.scripting.executeScript({
    target, world: "MAIN",
    func: (m, p) => (window.__KataPilot ? window.__KataPilot.call(m, p) : { ok: false, error: { message: "adapter not loaded", code: "adapter_missing" } }),
    args: [method, params],
  });
  const ok = results.filter((r) => r.result && r.result.ok);
  let chosen;
  if (allFrames) {
    // Editor operations: the frame that actually has an editor wins (GUVI could host it in an iframe).
    chosen = ok.find((r) => r.result.result && (r.result.result.editor || (r.result.result.found !== undefined && r.result.result.found)))
          || results.find((r) => r.frameId === 0);
  } else {
    chosen = results[0];
  }
  const res = chosen && chosen.result;
  if (!res) throw new ActionError("Adapter returned nothing (page still loading?)", "adapter_empty");
  if (!res.ok) throw new ActionError(res.error.message, res.error.code);
  return res.result;
}

async function waitReady(params) {
  const deadline = Date.now() + (params.timeoutMs || 25000);
  let last = null;
  while (Date.now() < deadline) {
    try {
      last = await runAdapter("detectPage", params);
      if (last.loginRequired || last.pageType === "question") return last;
    } catch (e) {
      if (e.code === "login_required") throw e;
    }
    await new Promise((r) => setTimeout(r, 800));   // editor / SPA content still loading
  }
  return last || { pageType: "other" };
}

async function openItem(locator) {
  const tab = await requireTab();
  if ((tab.url || "").split("#")[0] !== locator.listUrl.split("#")[0]) {
    await navigate(locator.listUrl);
    await new Promise((r) => setTimeout(r, 1500));
  }
  const before = (await chrome.tabs.get(tab.id)).url;
  await runAdapter("openItem", { locator });
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const t = await chrome.tabs.get(tab.id);
    if (t.url !== before && t.status === "complete") return { url: t.url };
    await new Promise((r) => setTimeout(r, 400));
  }
  return { url: (await chrome.tabs.get(tab.id)).url };   // SPA panes may change content without a URL change
}

const HANDLERS = {
  "tab.claim": async () => { const t = await claimTab(); return { tabId: t.id, url: t.url, title: t.title }; },
  "tab.navigate": (p) => navigate(p.url),
  "page.detect": (p) => runAdapter("detectPage", p),
  "page.waitReady": (p) => waitReady(p),
  "page.extract": (p) => runAdapter("extract", p),
  "page.discover": (p) => runAdapter("discover", p),
  "page.openItem": (p) => openItem(p.locator),
  "page.inspect": async (p) => {
    const frames = await (async () => {
      const tab = await requireTab();
      return (await chrome.webNavigation?.getAllFrames?.({ tabId: tab.id })) || null;
    })().catch(() => null);
    return { ...(await runAdapter("inspect", p)), frames };
  },
  "editor.setLanguage": (p) => runAdapter("setLanguage", p),
  "page.run": (p) => runAdapter("runCode", p),
  "page.submit": (p) => runAdapter("submitCode", p),
  "editor.selectPart": (p) => runAdapter("selectPart", p),
  "editor.read": (p) => runAdapter("readEditor", p, { allFrames: true }),
  "editor.write": (p) => runAdapter("writeEditor", p, { allFrames: true }),
};

async function handleRequest(msg) {
  const h = HANDLERS[msg.method];
  try {
    if (!h) throw new ActionError(`Unknown method ${msg.method}`, "bad_method");
    const result = await h(msg.params || {});
    send({ type: "response", id: msg.id, ok: true, result });
  } catch (e) {
    send({ type: "response", id: msg.id, ok: false, error: { message: e.message || String(e), code: e.code || "extension_error" } });
  }
}

// Tell dashboards when the agent tab moves (e.g. the user navigates manually).
chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== "complete") return;
  const { workingTabId } = await chrome.storage.session.get("workingTabId");
  if (tabId === workingTabId) send({ type: "event", data: { kind: "tab", url: tab.url, title: tab.title } });
});

// ------------------------------------------------------------------ popup
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    if (msg.cmd === "status") {
      const c = await cfg();
      const t = await getWorkingTab();
      reply({ connected: !!(ws && ws.readyState === WebSocket.OPEN), backend: c.backend, paired: !!c.token, lastError,
              tab: t ? { title: t.title, url: t.url } : null });
    } else if (msg.cmd === "setBackend") {
      await chrome.storage.local.set({ backend: msg.backend.replace(/\/$/, "") });
      await chrome.storage.local.remove("token");
      if (ws) ws.close();
      reconnectDelay = 500;
      connect();
      reply({ ok: true });
    } else if (msg.cmd === "reconnect") {
      await chrome.storage.local.remove("token");
      if (ws) ws.close(); else connect();
      reply({ ok: true });
    } else if (msg.cmd === "claim") {
      try { const t = await claimTab(true); reply({ ok: true, tab: { title: t.title, url: t.url } }); }
      catch (e) { reply({ ok: false, error: e.message }); }
    }
  })();
  return true;
});
