# KataPilot AI

A local agent that works through GUVI practice questions in **your own signed-in Chrome tab**: it reads each
question, researches it, writes a solution, checks it in a sandbox, and puts it in the GUVI editor.
**You review and press Run/Submit yourself — KataPilot never does.**

> If a kata counts toward a grade or ranking at your university, check the rules first. AI-prepared
> answers may not be allowed there; use KataPilot for practice and for studying the explanations.

## Quick start

1. **Start the engine** — double-click `KataPilot.command` (or run `./run.sh`). Keep that window open.
   Settings live in `backend/.env` (LLM provider, model and key; optional web-search key).
2. **Extension** — `chrome://extensions` → Developer mode → *Load unpacked* → the `extension/` folder.
   After any update to this folder, press the reload arrow on the KataPilot card.
3. **Use it** — sign in to GUVI, open CodeKata / SQLKata / a WebKata track, apply your filters, click the
   KataPilot toolbar icon. A side panel opens: check the detected page, pick language and count, press **Start**.
   Each finished question can be expanded there (code, checks, Copy, Insert). The fuller dashboard is at
   <http://127.0.0.1:8765>.

What it does on the real site: collects the rows of your filtered list (following pagination), opens each
question, switches GUVI's language dropdown to yours (backing up the editor first), writes the code into the
Ace editor, and for SQLKata fills every sub-question separately using the schema from GUVI's Tables panel.

## How it fits together

```
 Chrome (your profile, your GUVI login)                     Your machine
┌───────────────────────────────────────┐   WebSocket   ┌───────────────────────────────────────┐
│ GUVI tab                              │   JSON-RPC    │ FastAPI backend  (backend/app)        │
│  └ adapter.js (page world)            │◄─────────────►│  agent.py      session state machine  │
│     detect page · extract question    │               │  engine.py     research + generation  │
│     discover list · read/write editor │               │  validator.py  checks                 │
│ background.js (service worker)        │               │  sandbox.py    isolated execution     │
│  tab control · runs adapter · keepalive│              │  llm.py / search.py  pluggable        │
└───────────────────────────────────────┘               │  db.py         SQLite history         │
                                                        └───────────────▲───────────────────────┘
 Dashboard (React, served by the backend) ── REST + WebSocket events ───┘
```

* **Why an extension, not Playwright:** Playwright attaches through Chrome's remote-debugging port, which
  Chrome 136+ refuses for your normal profile. A Manifest V3 extension runs inside the profile where you are
  already signed in, so no credentials or cookies are ever copied anywhere.
* **Extension ⇄ backend:** the service worker opens `ws://127.0.0.1:8765/ws/extension`. The backend sends
  requests (`tab.navigate`, `page.extract`, `editor.write`, …) and the extension replies. A ping every 20 s keeps
  the MV3 worker alive; an alarm reconnects it if Chrome suspends it.
* **Dashboard ⇄ backend:** REST for controls, `/ws/dashboard` for live events.
* **Pairing / local security:** a random token is created in `backend/data/api_token`. The dashboard (same
  origin) and the extension fetch it from `/api/bootstrap`; other websites are refused by Origin and Host checks,
  every API call needs the token, and the extension socket only accepts `chrome-extension://` origins.

## Folder structure

```
katapilot/
├── run.sh                 one-command start
├── backend/
│   ├── .env.example       all configuration (keys stay here, never in the dashboard)
│   ├── app/
│   │   ├── main.py        FastAPI routes + WebSockets + security
│   │   ├── agent.py       queue, pause/resume/skip/stop, review modes, recovery
│   │   ├── engine.py      web research + equivalence judging, generation, repair prompts
│   │   ├── validator.py   static checks, sample tests, SQL on SQLite, HTML/CSS/JS checks
│   │   ├── sandbox.py     docker | seatbelt (macOS) | judge0 | none
│   │   ├── llm.py         anthropic | openai_compatible
│   │   ├── search.py      tavily | brave | serper + SSRF-safe page fetch
│   │   ├── bridge.py      extension RPC + dashboard event hub
│   │   ├── db.py          SQLite: sessions, questions, solutions, editor backups, logs, prefs
│   │   └── models.py      Question / SolutionDraft / ValidationReport / SessionConfig
│   └── tests/             pytest suite + e2e_harness.py (UI test harness)
├── extension/
│   ├── manifest.json      MV3; host access limited to *.guvi.in and the local backend
│   ├── background.js      connection, tab control, adapter execution
│   ├── adapter.js         GUVI adapter (structure-based, editor-agnostic)
│   ├── panel.html/js/css  side panel: Start button, live queue, code preview
│   └── test/              fixture pages used to test the adapter
└── dashboard/             React + TypeScript + Tailwind + Framer Motion + Lucide
```

## What a session does

For every queued question: open it → wait for the editor → extract a structured question (title, statement,
input/output format, constraints, samples, language, tables) → optional web research with an LLM check that
each hit is *actually the same problem* → generate → validate → repair up to `MAX_REPAIR_ATTEMPTS` times →
back up the editor → insert → verify the editor shows the code.

| Status | Meaning |
|---|---|
| Code Ready | compiled and every sample on the page matched, locally |
| Needs Review | could not be fully validated (no samples / language mismatch) or still failing after repairs |
| Failed | the question could not be processed (page, extraction or AI error) |

Review modes: *Insert, then continue* · *Pause after each question* · *Prepare only*.
Code that fails validation is never inserted. "Code Ready" is local evidence — only GUVI decides acceptance.

Nothing is silently lost: the editor is copied before every insertion (Solution panel → **Backups** → Restore),
edits you make over inserted code are saved before the agent navigates away, and inserts go through the
editor's undo stack where the editor supports it.

## Configuration (`backend/.env`)

| Variable | Values |
|---|---|
| `LLM_PROVIDER` | `anthropic` (default) or `openai_compatible` (OpenAI, Groq, OpenRouter, Ollama… via `OPENAI_BASE_URL`) |
| `LLM_MODEL`, `LLM_EFFORT` | default `claude-opus-5-5`, `high`. `claude-sonnet-5-5` is cheaper. |
| `SEARCH_PROVIDER` | `tavily`, `brave`, `serper`, `none` |
| `SANDBOX_BACKEND` | `auto` (docker → seatbelt → judge0 → none), or force one |
| `MAX_REPAIR_ATTEMPTS` | default 2 |

Adding a provider means one class in `llm.py` or `search.py` plus a line in its factory.
Adding a GUVI module means a regex in `MODULE_PATTERNS` (adapter) and a rule in `MODULE_RULES` (engine).

### Sandbox

Generated code never runs unsandboxed. On macOS without Docker the **Seatbelt** backend is used
(`sandbox-exec`): no network, no reads under `/Users`, writes only in a throwaway folder, CPU and file-size
limits, wall-clock kill. Docker, when installed, gives stronger isolation (separate container, memory and
process limits) and matches GUVI's GCC more closely than macOS clang. With no backend, validation reports
"not validated" instead of running anything.

## Known limits

* **GUVI's logged-in pages were not available while building this.** The adapter therefore finds things by
  structure (section headings, repeated links, live editor APIs) and was tested against fixture pages with
  Monaco, Ace, CodeMirror 5/6 and textarea editors. On the real site, run **Settings → Inspect open GUVI page**
  once per module; if something is missed, pin a CSS selector there.
* FixTheCode is detected from the URL/title; if GUVI files it under CodeKata, start from the FixTheCode page in
  the dashboard so the right prompt is used.
* Changing the language dropdown in GUVI is left to you: a mismatch is reported as *Needs Review* rather than
  clicked automatically, because switching can reset the editor.
* SQL is checked on SQLite built from the tables on the page; MySQL-only syntax is checked through an
  equivalent SQLite query. WebKata checks syntax and required ids/classes, not rendering.
* If the editor lives in a cross-origin iframe, add that origin to `host_permissions` in the manifest
  (Inspect shows iframe sources).
* Sign-in pages, CAPTCHAs and "Leave site?" dialogs pause the session with a message; they are never bypassed.

## Tests

```bash
cd backend && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
```

Covers the sandbox (including blocked network/home access), validators, SQL safety, API security, and whole
sessions against a fake browser bridge: repair, review mode, pause on blocked navigation, crash recovery.
`tests/e2e_harness.py` runs the real app and dashboard with a scripted browser and LLM for UI testing.

Dashboard development: `cd dashboard && npm run dev` (proxies to the backend on 8765).
