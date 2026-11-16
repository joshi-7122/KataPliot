// KataPilot side panel: the one-click UI. Talks to the local engine (REST + WebSocket); the engine drives the GUVI tab.
const $ = (id) => document.getElementById(id);
const LANGS = {
  codekata: [["cpp", "C++"], ["c", "C"], ["java", "Java"], ["python", "Python"], ["auto", "Whatever GUVI has selected"]],
  fixthecode: [["auto", "Language of the given code"]],
  sqlkata: [["sql", "SQL"]],
  webkata: [["auto", "Auto (track)"]],
};
const STATUS = {
  pending: ["Pending", "#6c7080"], processing: ["Opening", "#5b8cff"], researching: ["Researching", "#58b6e8"],
  generating: ["Generating", "#8a6cf7"], validating: ["Validating", "#c18cf5"], inserting: ["Inserting", "#5b8cff"],
  code_ready: ["Code Ready", "#4cc38a"], needs_review: ["Needs Review", "#e5a54b"], failed: ["Failed", "#ef6b6b"], skipped: ["Skipped", "#6c7080"],
};
const ACTIVE = ["running", "paused", "waiting_review", "stopping"];
let backend = "http://127.0.0.1:8765", token = null, ws = null, up = false;
let agent = { state: "idle" }, questions = [], sessionId = null, prefs = null, sessionError = null, moduleTouched = false;
const open = new Set(), details = new Map();

function toast(text, bad) {
  const t = $("toast"); t.textContent = text; t.className = bad ? "bad" : ""; clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.add("hide"), 6000);
}
async function api(path, method = "GET", body) {
  if (!token) token = (await (await fetch(`${backend}/api/bootstrap`)).json()).token;
  const r = await fetch(backend + path, { method, headers: { "Content-Type": "application/json", "X-KataPilot-Token": token },
                                           body: body === undefined ? undefined : JSON.stringify(body) });
  if (r.status === 401) token = null;
  if (!r.ok) { let d = `HTTP ${r.status}`; try { d = (await r.json()).detail || d; } catch {} throw new Error(typeof d === "string" ? d : JSON.stringify(d)); }
  return r.json();
}
const act = async (fn) => { try { return await fn(); } catch (e) { toast(e.message, true); } };

function fillLanguages() {
  const m = $("module").value, sel = $("language"), prev = sel.value;
  sel.innerHTML = LANGS[m].map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  const want = m === "codekata" ? (prev && LANGS[m].some(([v]) => v === prev) ? prev : (prefs && prefs.preferred_language) || "cpp") : LANGS[m][0][0];
  sel.value = want;
}

async function refreshPage() {
  $("page").textContent = "Looking at the GUVI tab…";
  try {
    const d = await api("/api/browser/detect", "POST");
    $("d-tab").className = "on";
    if (d.module && !moduleTouched) { $("module").value = d.module; fillLanguages(); }
    const name = { codekata: "CodeKata", sqlkata: "SQLKata", webkata: "WebKata", fixthecode: "Debugging" }[d.module] || "GUVI";
    if (d.loginRequired) $("page").innerHTML = "GUVI is showing a sign-in page. <b>Sign in first.</b>";
    else if (d.pageType === "listing") {
      const pg = d.pages && d.pages.last > 1 ? ` · page ${d.pages.page} of ${d.pages.last}` : "";
      $("page").innerHTML = `<b>${name} list</b> — ${d.listingCount} question(s) shown${pg}. More pages are followed automatically.`;
    } else if (d.pageType === "question") $("page").innerHTML = `<b>${name} question</b> — ${escapeHtml(d.title || "")}. Only this question will be done.`;
    else $("page").innerHTML = "This page is not a GUVI question list. Open CodeKata, SQLKata or a WebKata track, apply your filter, then Refresh.";
    refreshPage.type = d.pageType;
  } catch (e) {
    $("d-tab").className = "";
    $("page").textContent = e.message;
    refreshPage.type = null;
  }
  render();
}

async function load() {
  try {
    const [st, cur, pf] = await Promise.all([api("/api/status"), api("/api/session/current"), api("/api/prefs")]);
    up = true; prefs = pf; agent = st.agent;
    if (cur.session) { sessionId = cur.session.id; questions = cur.questions; sessionError = cur.session.error; }
    $("llmerr").textContent = st.llm.error || ""; $("llmerr").classList.toggle("hide", !st.llm.error);
    $("sub").textContent = `${st.llm.model || st.llm.provider} · sandbox ${st.sandbox.backend}`;
    if (!load.done) { $("max").value = pf.max_questions; $("mode").value = pf.review_mode; fillLanguages(); load.done = true; }
    $("dash").href = backend;
    if (!st.extension.connected) $("page").textContent = "Connecting the extension to the engine…";
  } catch { up = false; }
  render();
}

function connectWs() {
  if (ws && ws.readyState <= 1) return;
  if (!token) return;
  ws = new WebSocket(backend.replace(/^http/, "ws") + "/ws/dashboard?token=" + encodeURIComponent(token));
  ws.onopen = () => { up = true; load().then(refreshPage); };
  ws.onclose = () => { ws = null; up = false; render(); };
  ws.onmessage = (ev) => {
    const { type, data: d } = JSON.parse(ev.data);
    if (type === "session") {
      agent = { ...agent, state: d.state, session_id: d.session_id, current_question_id: d.current_question_id };
      sessionError = d.error || null;
      if (["completed", "stopped", "failed"].includes(d.state)) setTimeout(refreshPage, 800);
      if (d.session_id !== sessionId) { sessionId = d.session_id; questions = []; open.clear(); details.clear(); }
    } else if (type === "question" && (d.session_id === sessionId || sessionId == null)) {
      const i = questions.findIndex((q) => q.id === d.id);
      if (i >= 0) questions[i] = d; else questions.push(d);
      questions.sort((a, b) => a.position - b.position);
      if (open.has(d.id)) loadDetail(d.id);
    } else if (type === "activity") {
      $("activity").textContent = d.message;
      const q = questions.find((x) => x.id === d.question_id); if (q) q.activity = d.message;
    } else if (type === "extension" && d.connected) refreshPage();
    else if (type === "solution" && open.has(d.question_id)) loadDetail(d.question_id);
    render();
  };
}

const escapeHtml = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function loadDetail(id) {
  try { details.set(id, await api(`/api/questions/${id}`)); renderQueue(); } catch {}
}

function renderQueue() {
  const box = $("queue");
  box.innerHTML = questions.map((q) => {
    const [label, color] = STATUS[q.status] || STATUS.pending;
    const d = details.get(q.id), sol = d && d.solution;
    let body = "";
    if (open.has(q.id)) {
      if (sol) {
        const checks = sol.validation.checks.map((c) => `<div><span>${{ pass: "✓", fail: "✗", warn: "!", skipped: "–" }[c.status]}</span><span>${escapeHtml(c.name)}</span></div>`).join("");
        body = `<div class="body"><div class="meta">${escapeHtml(sol.language)} · time ${escapeHtml(sol.analysis.time_complexity)} · space ${escapeHtml(sol.analysis.space_complexity)}</div>
          <pre>${escapeHtml(sol.code)}</pre><div class="checks">${checks}</div><div class="meta">${escapeHtml(sol.analysis.approach)}</div>
          <div class="btns"><button data-copy="${q.id}">Copy code</button><button class="primary" data-insert="${q.id}">Insert into GUVI</button></div></div>`;
      } else body = `<div class="body"><div class="meta">${escapeHtml(q.error || "No solution yet.")}</div></div>`;
    }
    return `<details class="q" data-id="${q.id}" ${open.has(q.id) ? "open" : ""}><summary><div class="t"><b>${escapeHtml(q.title)}</b><small>${escapeHtml(q.error || q.activity || "")}</small></div>
      <span class="pill" style="color:${color};border-color:${color}55;background:${color}18">${label}</span></summary>${body}</details>`;
  }).join("");
}

function render() {
  $("d-back").className = up ? "on" : "";
  $("offline").classList.toggle("hide", up);
  const active = ACTIVE.includes(agent.state);
  $("setup").classList.toggle("hide", active || !up);
  $("pagecard").classList.toggle("hide", !up);
  $("running").classList.toggle("hide", !active && !["completed", "stopped", "failed", "interrupted"].includes(agent.state) || !questions.length && !active);
  $("start").disabled = !up || !!$("llmerr").textContent || !["listing", "question"].includes(refreshPage.type);
  const done = questions.filter((q) => ["code_ready", "needs_review", "failed", "skipped"].includes(q.status)).length;
  $("count").textContent = questions.length ? `${done} / ${questions.length}` : "";
  $("fill").style.width = questions.length ? `${(done / questions.length) * 100}%` : "0";
  $("state").textContent = { running: "Working…", paused: "Paused", waiting_review: "Waiting for your review", stopping: "Stopping…", completed: "Done",
                             stopped: "Stopped", failed: "Session failed", interrupted: "Interrupted" }[agent.state] || "";
  $("serr").textContent = ["paused", "failed"].includes(agent.state) ? sessionError || "" : "";
  $("pause").classList.toggle("hide", agent.state !== "running");
  $("resume").classList.toggle("hide", !["paused", "waiting_review"].includes(agent.state));
  $("resume").textContent = agent.state === "waiting_review" ? "Next question" : "Resume";
  $("skip").classList.toggle("hide", !active); $("stop").classList.toggle("hide", !active);
  if (!active) { $("setup").classList.toggle("hide", !up); if (!["completed", "stopped", "failed"].includes(agent.state)) $("activity").textContent = ""; }
  renderQueue();
}

$("module").onchange = () => { moduleTouched = true; fillLanguages(); };
const hint = () => { $("hint").textContent = $("mode").value === "auto_submit"
  ? "Runs each solution on GUVI and submits it only if every test case passes, then moves to the next question."
  : "Uses the list exactly as you filtered it. Does not click Run or Submit in this mode."; };
$("mode").onchange = () => { hint(); chrome.storage.local.set({ mode: $("mode").value }); };
$("refresh").onclick = refreshPage;
$("start").onclick = () => act(async () => {
  await refreshPage();   // the tab may have moved since the panel last looked
  if (!["listing", "question"].includes(refreshPage.type)) throw new Error("Open a GUVI question list (or a question) in the active tab first.");
  questions = []; open.clear(); details.clear();
  await api("/api/session/start", "POST", {
    module: $("module").value, language: $("language").value, review_mode: $("mode").value,
    discovery: refreshPage.type === "question" ? "current" : "listing",
    max_questions: Math.max(1, Math.min(200, +$("max").value || 10)), urls: [],
    skip_already_prepared: true, skip_solved_on_guvi: $("skipsolved").checked, use_web_search: prefs ? prefs.use_web_search : true,
  });
});
for (const a of ["pause", "resume", "skip", "stop"]) $(a).onclick = () => act(() => api(`/api/session/${a}`, "POST"));
$("queue").addEventListener("click", (e) => {
  const copy = e.target.closest("[data-copy]"), ins = e.target.closest("[data-insert]"), sum = e.target.closest("summary");
  if (copy) { const d = details.get(+copy.dataset.copy); navigator.clipboard.writeText(d.solution.code); toast("Code copied"); }
  else if (ins) act(async () => { const r = await api(`/api/questions/${ins.dataset.insert}/insert`, "POST"); toast(r.inserted ? "Inserted into the GUVI editor." : `Not inserted: ${r.note || "check the GUVI tab"}`, !r.inserted); });
  else if (sum) { e.preventDefault(); const id = +sum.parentElement.dataset.id; if (open.has(id)) open.delete(id); else { open.add(id); loadDetail(id); } renderQueue(); }
});

(async function boot() {
  const c = await chrome.storage.local.get(["backend", "mode"]); if (c.backend) backend = c.backend;
  await load(); if (c.mode) $("mode").value = c.mode; hint(); connectWs(); if (up) refreshPage();
  setInterval(async () => { if (!up || !ws) { await load(); connectWs(); } }, 3000);
  chrome.tabs.onActivated.addListener(() => { if (up && !ACTIVE.includes(agent.state)) refreshPage(); });
  chrome.tabs.onUpdated.addListener((_id, info, tab) => { if (info.status === "complete" && tab.active && up && !ACTIVE.includes(agent.state)) refreshPage(); });
})();
