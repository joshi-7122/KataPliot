/*
 * KataPilot GUVI adapter. Injected into the GUVI page's MAIN world so it can reach the page's editor.
 *
 * Tuned against the real site (Nov 2026), which is a Qwik app:
 *   - question lists: table.questions-table with clickable <tr> rows (no links), paginated;
 *   - problem text:   .questionsMarked inside .question-container, title in a <p> above it;
 *   - editor:         Ace (div#editor, el.env.editor); language = hidden <select> behind a SlimSelect widget;
 *   - SQLKata:        several sub-questions (.collapse) per problem, schema in a hidden .tables-container;
 *   - URLs:           /code-kata/<slug>/, /sqlkata/<slug>/, /webkata/<track>/<number>/, /debugging/...
 * Every GUVI-specific selector is a first-choice hint with a structural fallback behind it, and the editor
 * family (Ace / Monaco / CodeMirror 5/6 / textarea) is still detected at runtime, so layout changes degrade
 * gracefully. Per-module selector overrides from Settings win over everything.
 */
(() => {
  const VERSION = "2.3.0";
  if (window.__KataPilot && window.__KataPilot.version === VERSION) return;

  const MODULE_PATTERNS = [
    ["fixthecode", /fix[-_ ]?the[-_ ]?code|fixthecode|\/debugging\b|debug/i],
    ["sqlkata", /sql[-_ ]?kata/i],
    ["webkata", /web[-_ ]?kata/i],
    ["codekata", /code[-_ ]?kata/i],
  ];
  const HEADING_WORDS =
    "problem\\s*statement|problem|description|task|input\\s*(?:format|description|specification)?|output\\s*(?:format|description|specification)?|constraints?|sample\\s*inputs?|sample\\s*outputs?|example\\s*inputs?|example\\s*outputs?|examples?|explanation|note|notes|expected\\s*output|table\\s*schema|schema";
  const HEADING_COLON = new RegExp(`^\\s*(${HEADING_WORDS})\\s*(\\d+)?\\s*[:：]\\s*(.*)$`, "i");
  const HEADING_BARE = new RegExp(`^\\s*(${HEADING_WORDS})\\s*(\\d+)?\\s*$`, "i");
  const MARKERS = [/input\s*(format|description|specification)/i, /output\s*(format|description|specification)/i,
    /constraints?/i, /sample\s*input/i, /sample\s*output/i, /example/i, /explanation/i, /expected\s*output/i];
  const DIFF = /\b(easy|medium|hard|beginner|intermediate|advanced)\b/i;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const norm = (s) => (s || "").replace(/\r\n/g, "\n").replace(/[ \t]+$/gm, "").replace(/\n+$/, "");
  const fail = (message, code) => Object.assign(new Error(message), { code });
  const cleanTitle = (t) => (t || "").replace(/\s+/g, " ").replace(/^\s*\d+\s*[.)]\s*/, "").trim();
  const slugify = (t) => cleanTitle(t).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" && cs.opacity !== "0";
  }
  const area = (el) => { const r = el.getBoundingClientRect(); return r.width * r.height; };

  function cssPath(el) {
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 6 && el !== document.body) {
      let s = el.tagName.toLowerCase();
      // Qwik scoped classes look like "⭐️7r6n6o-0"; skip them and state classes.
      const cls = [...el.classList].filter((c) => /^[a-z][\w-]*$/i.test(c) && !/active|selected|hover|focus/i.test(c)).slice(0, 2);
      if (cls.length) s += "." + cls.map((c) => CSS.escape(c)).join(".");
      parts.unshift(s);
      el = el.parentElement;
    }
    return parts.join(" > ");
  }

  // ------------------------------------------------------------------ editors
  function langFromText(t) {
    t = (t || "").toLowerCase().trim();
    if (!t) return null;
    if (/c\+\+|cpp|c_cpp|x-c\+\+src|g\+\+/.test(t)) return "cpp";
    if (/javascript|^js$|node/.test(t)) return "javascript";
    if (/java(?!script)/.test(t)) return "java";
    if (/python|py3|^py$/.test(t)) return "python";
    if (/mysql|sql/.test(t)) return "sql";
    if (/html/.test(t)) return "html";
    if (/css/.test(t)) return "css";
    if (/^c$|^c\s*\(|x-csrc|^gcc|^clang|\bc99\b|\bc11\b/.test(t)) return "c";
    return null;
  }

  function findEditors(overrides) {
    const found = [];
    const scope = overrides && overrides.editorSelector ? document.querySelector(overrides.editorSelector) : null;
    const within = (el) => !scope || scope.contains(el) || el.contains(scope);
    const aceApi = (el) => (el.env && el.env.editor) || (window.ace && window.ace.edit ? (() => { try { return window.ace.edit(el); } catch { return null; } })() : null);

    document.querySelectorAll(".ace_editor").forEach((el) => {
      if (!visible(el) || !within(el)) return;
      const api = aceApi(el);
      found.push({ type: api ? "ace" : "ace-dom", el, api });
    });
    const monaco = window.monaco && window.monaco.editor;
    if (monaco && typeof monaco.getEditors === "function") {
      for (const ed of monaco.getEditors()) {
        const node = ed.getDomNode && ed.getDomNode();
        if (node && visible(node) && within(node) && ed.getModel()) found.push({ type: "monaco", el: node, api: ed });
      }
    }
    if (!found.some((f) => f.type === "monaco")) {
      document.querySelectorAll(".monaco-editor").forEach((el) => {
        if (visible(el) && within(el) && !el.closest(".monaco-diff-editor .original")) found.push({ type: "monaco-dom", el });
      });
    }
    document.querySelectorAll(".CodeMirror").forEach((el) => {
      if (el.CodeMirror && visible(el) && within(el)) found.push({ type: "codemirror5", el, api: el.CodeMirror });
    });
    document.querySelectorAll(".cm-editor").forEach((el) => {
      const content = el.querySelector(".cm-content");
      // CodeMirror 6 keeps its EditorView on the content node: `cmTile.view` (current builds) or `cmView.view` (older).
      const holder = content && (content.cmTile || content.cmView);
      const view = holder && (holder.view || (holder.rootView && holder.rootView.view) || (holder.root && holder.root.view));
      if (visible(el) && within(el)) found.push({ type: view ? "codemirror6" : "codemirror6-dom", el, api: view });
    });
    document.querySelectorAll("textarea").forEach((el) => {
      if (el.closest(".monaco-editor,.ace_editor,.CodeMirror,.cm-editor,dialog,.modal,[class*=modal i]")) return;
      if (visible(el) && within(el) && area(el) > 20000) found.push({ type: "textarea", el });
    });
    if (!found.length) {
      // GUVI's narrow layout hides the editor behind a "Code Editor" tab; its Ace API still works.
      document.querySelectorAll(".ace_editor").forEach((el) => {
        const api = aceApi(el);
        if (api && within(el)) found.push({ type: "ace", el, api, hidden: true });
      });
    }
    if (!found.length) {
      document.querySelectorAll('[contenteditable="true"]').forEach((el) => {
        if (visible(el) && within(el) && area(el) > 40000 && !el.closest(".monaco-editor,.ace_editor,.CodeMirror,.cm-editor"))
          found.push({ type: "contenteditable", el });
      });
    }
    const rank = (f) => (f.type.endsWith("-dom") ? 0 : 1);
    found.sort((a, b) => rank(b) - rank(a) || area(b.el) - area(a.el));
    return found;
  }

  function editorLanguage(ed) {
    try {
      if (ed.type === "monaco") return langFromText(ed.api.getModel().getLanguageId ? ed.api.getModel().getLanguageId() : ed.api.getModel().getModeId());
      if (ed.type === "ace") { const id = ed.api.session.getMode().$id || ""; return id.includes("c_cpp") ? "cpp?" : langFromText(id.split("/").pop()); }
      if (ed.type === "codemirror5") { const m = ed.api.getOption("mode"); return langFromText(typeof m === "string" ? m : m && m.name); }
    } catch { /* fall through */ }
    return null;
  }

  /** The language <select>. GUVI hides the native select behind a SlimSelect widget, so hidden ones count. */
  function languageSelect() {
    let best = null;
    for (const sel of document.querySelectorAll("select")) {
      const n = [...sel.options].filter((o) => langFromText(o.text)).length;
      if (n >= 2 && (!best || n > best.n || (visible(sel) && !visible(best.sel)))) best = { sel, n };
    }
    return best ? best.sel : null;
  }

  function uiLanguage() {
    const sel = languageSelect();
    if (sel) {
      const cur = sel.options[sel.selectedIndex];
      return { lang: langFromText(cur && cur.text), via: "select", text: cur ? cur.text : null,
               options: [...sel.options].map((o) => o.text).filter((t) => langFromText(t)).slice(0, 12) };
    }
    const cands = [...document.querySelectorAll('button,[role="combobox"],[role="button"],[class*="select" i],[class*="dropdown" i],[class*="lang" i]')]
      .filter((el) => visible(el) && el.innerText && el.innerText.trim().length <= 24);
    for (const el of cands) {
      const l = langFromText(el.innerText.trim());
      if (l && !["html", "css"].includes(l)) return { lang: l, via: "dropdown", text: el.innerText.trim() };
    }
    return null;
  }

  function detectEditor(overrides) {
    const eds = findEditors(overrides);
    if (!eds.length) return { found: false };
    const ed = eds[0];
    const ui = uiLanguage();
    let lang = ui && ui.lang ? ui.lang : editorLanguage(ed);
    if (lang === "cpp?") lang = "cpp";
    return { found: true, type: ed.type, count: eds.length, language: lang, languageVia: ui ? ui.via : "editor-mode",
             canSwitchLanguage: !!languageSelect(), hidden: !!ed.hidden };
  }

  function readEditor(overrides) {
    const ed = findEditors(overrides)[0];
    if (!ed) throw fail("No code editor found on this page.", "editor_not_found");
    switch (ed.type) {
      case "monaco": case "ace": case "codemirror5": return { editor: ed.type, content: ed.api.getValue() };
      case "codemirror6": return { editor: ed.type, content: ed.api.state.doc.toString() };
      case "textarea": return { editor: ed.type, content: ed.el.value };
      case "contenteditable": return { editor: ed.type, content: ed.el.innerText };
      default: {
        const lines = [...ed.el.querySelectorAll(".view-line, .ace_line, .cm-line")].map((l) => l.innerText.replace(/ /g, " "));
        return { editor: ed.type, content: lines.join("\n"), partial: true };
      }
    }
  }

  async function writeEditor(code, overrides) {
    const ed = findEditors(overrides)[0];
    if (!ed) throw fail("No code editor found on this page.", "editor_not_found");
    switch (ed.type) {
      case "monaco": {
        const model = ed.api.getModel();
        ed.api.pushUndoStop();   // Ctrl+Z restores the previous code
        ed.api.executeEdits("katapilot", [{ range: model.getFullModelRange(), text: code, forceMoveMarkers: true }]);
        ed.api.pushUndoStop();
        ed.api.setPosition({ lineNumber: 1, column: 1 });
        ed.api.focus();
        break;
      }
      case "ace":
        if (ed.api.getReadOnly && ed.api.getReadOnly()) throw fail("The GUVI editor is read-only right now.", "editor_readonly");
        if (!ed.hidden) ed.api.focus();
        ed.api.setValue(code, -1);   // one undoable change; fires Ace's change event so GUVI stores the code
        break;
      case "codemirror5": ed.api.focus(); ed.api.setValue(code); break;
      case "codemirror6": ed.api.dispatch({ changes: { from: 0, to: ed.api.state.doc.length, insert: code } }); ed.api.focus(); break;
      case "textarea": {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
        ed.el.focus();
        setter.call(ed.el, code);   // native setter so framework-controlled inputs notice the change
        ed.el.dispatchEvent(new Event("input", { bubbles: true }));
        ed.el.dispatchEvent(new Event("change", { bubbles: true }));
        break;
      }
      case "contenteditable": ed.el.focus(); document.execCommand("selectAll"); document.execCommand("insertText", false, code); break;
      default: {
        // No reachable API: select-all then a synthetic paste (avoids auto-indent / auto-bracket mangling).
        const input = ed.el.querySelector("textarea, .cm-content, [contenteditable]") || ed.el;
        input.focus();
        const mod = navigator.platform.includes("Mac") ? { metaKey: true } : { ctrlKey: true };
        input.dispatchEvent(new KeyboardEvent("keydown", { key: "a", code: "KeyA", bubbles: true, ...mod }));
        await sleep(50);
        const dt = new DataTransfer();
        dt.setData("text/plain", code);
        input.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      }
    }
    await sleep(150);
    const after = readEditor(overrides);
    const verified = after.partial ? norm(after.content).split("\n")[0] === norm(code).split("\n")[0] : norm(after.content) === norm(code);
    return { editor: ed.type, verified, partial: !!after.partial, length: (after.content || "").length };
  }

  /** Switch GUVI's language dropdown. Answers GUVI's own "Keep the Code Changes?" prompt with "No"
   *  (the caller has already backed the editor up and is about to replace its content). */
  async function setLanguage(p) {
    const want = p.language;
    const current = detectEditor(p.overrides).language;
    if (current === want) return { changed: false, language: current };
    const sel = languageSelect();
    if (!sel) throw fail("No language selector found on this page.", "language_select_missing");
    const opts = [...sel.options].filter((o) => langFromText(o.text) === want);
    if (!opts.length) throw fail(`GUVI does not offer ${want} for this question.`, "language_unavailable");
    const PREF = { cpp: /^c\+\+$/i, c: /^c$/i, java: /^java$/i, python: /python\s*3/i, javascript: /^javascript$/i };
    const opt = opts.find((o) => PREF[want] && PREF[want].test(o.text.trim())) || opts[0];
    sel.value = opt.value;
    sel.dispatchEvent(new Event("input", { bubbles: true }));
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    for (let i = 0; i < 24; i++) {
      await sleep(250);
      const dlg = [...document.querySelectorAll("dialog[open]")].find((d) => /keep the code changes/i.test(d.textContent));
      if (dlg) {
        const no = [...dlg.querySelectorAll("button")].find((b) => /^no$/i.test(b.textContent.trim()));
        if (no) no.click();
        await sleep(500);
        continue;
      }
      const ed = findEditors(p.overrides)[0];
      const mode = ed && editorLanguage(ed);
      const modeOk = !mode || mode === want || (mode === "cpp?" && (want === "cpp" || want === "c"));
      if (modeOk && langFromText(sel.options[sel.selectedIndex].text) === want && i >= 2) return { changed: true, language: want, option: opt.text };
    }
    throw fail("GUVI did not switch the editor language.", "language_switch_failed");
  }

  // ------------------------------------------------------------------ page detection
  function moduleFromUrl(url) {
    for (const [m, re] of MODULE_PATTERNS) if (re.test(url)) return m;
    return null;
  }

  function loginRequired() {
    if (/sign-?in|log-?in|signup/i.test(location.pathname)) return true;
    const pw = document.querySelector('input[type="password"]');
    return !!(pw && visible(pw));
  }

  function markerCount(text) {
    let n = 0;
    for (const re of MARKERS) if (re.test(text)) n++;
    return n;
  }

  function findProblemContainer(overrides) {
    if (overrides && overrides.problemSelector) {
      const el = document.querySelector(overrides.problemSelector);
      if (el) return { el, strategy: "override" };
    }
    // GUVI: the rendered problem description.
    const hints = [...document.querySelectorAll(".questionsMarked")].filter((el) => el.textContent.trim().length > 10);
    const hint = hints.find(visible) || hints[0];
    if (hint) return { el: hint, strategy: "guvi" };

    const eds = findEditors().map((e) => e.el);
    const bad = (el) => eds.some((e) => el.contains(e) || e.contains(el)) || el.closest("nav,header,footer,[role=navigation],dialog");
    const cands = [...document.querySelectorAll("main,article,section,div,td")].filter((el) => {
      const len = el.textContent.length;
      return len > 60 && len < 30000 && !bad(el) && visible(el);
    });
    let best = null, bestScore = -1;
    for (const el of cands) {
      const m = markerCount(el.textContent);
      if (m > bestScore || (m === bestScore && best && el.textContent.length < best.textContent.length)) { best = el; bestScore = m; }
    }
    if (best && bestScore >= 2) return { el: best, strategy: "markers", markers: bestScore };
    const textual = cands.filter((el) => el.querySelectorAll("p,li,pre").length > 0);
    textual.sort((a, b) => b.innerText.length - a.innerText.length);
    return textual[0] ? { el: textual[0], strategy: "largest-text" } : null;
  }

  const NOT_TITLE = /^(easy|medium|hard|topics?|html|css|javascript|bootstrap|sql|\d+|problem|description|problem description)$/i;

  function findTitle(container, overrides) {
    if (overrides && overrides.titleSelector) {
      const t = document.querySelector(overrides.titleSelector);
      if (t && t.innerText.trim()) return cleanTitle(t.innerText);
    }
    const ok = (t) => t.length >= 3 && t.length <= 140 && /[a-z]/i.test(t) && !NOT_TITLE.test(t) && !HEADING_BARE.test(t);
    // GUVI: first short <p> in the question header, above the description.
    const scope = container && container.closest(".question-container");
    if (scope) {
      for (const p of scope.querySelectorAll("p,h1,h2,h3,h4")) {
        if (container.contains(p)) break;
        const t = (p.textContent || "").trim().split("\n")[0];
        if (ok(t)) return cleanTitle(t);
      }
    }
    // Structural: nearest short text that precedes the problem container.
    let el = container;
    for (let depth = 0; depth < 4 && el && el !== document.body; depth++) {
      for (let sib = el.previousElementSibling; sib; sib = sib.previousElementSibling) {
        const leaves = [sib, ...sib.querySelectorAll("*")].filter((e) => e.children.length === 0);
        for (const leaf of leaves) {
          const t = (leaf.textContent || "").trim();
          if (ok(t)) return cleanTitle(t);
        }
      }
      el = el.parentElement;
    }
    const heads = [...document.querySelectorAll("h1,h2,h3,[class*=title i]")]
      .filter((h) => visible(h) && ok(h.innerText.trim().split("\n")[0]) && !/^(code|sql|web)\s*kata$/i.test(h.innerText.trim()));
    if (heads[0]) return cleanTitle(heads[0].innerText.trim().split("\n")[0]);
    return cleanTitle(document.title.replace(/\s*[|\-–]\s*(HCL\s*)?GUVI.*$/i, ""));
  }

  function dismissOverlays() {
    // GUVI's narrow-window notice ("practice through desktop devices"); harmless to dismiss.
    const b = [...document.querySelectorAll("button")].find((x) => visible(x) && /^continue here anyway$/i.test(x.textContent.trim()));
    if (b) b.click();
    return !!b;
  }

  function detectPage(params = {}) {
    const ov = params.overrides || {};
    if (params.dismissOverlays) dismissOverlays();
    const editor = detectEditor(ov);
    const container = editor.found ? findProblemContainer(ov) : null;
    const listing = editor.found ? null : discoverSync(params.module, 200);
    const pageType = editor.found && container ? "question" : listing && listing.items.length >= 1 ? "listing" : "other";
    return {
      url: location.href, title: container ? findTitle(container.el, ov) : document.title, module: moduleFromUrl(location.href),
      loginRequired: loginRequired(), pageType, editor,
      problemFound: !!container, problemStrategy: container && container.strategy,
      listingCount: listing ? listing.items.length : 0, listingStrategy: listing ? listing.strategy : null,
      pages: listing ? listing.pages : null,
    };
  }

  // ------------------------------------------------------------------ extraction
  function canonicalHeading(h) {
    h = h.toLowerCase().replace(/\s+/g, " ");
    if (/^input|^sample inputs?|^example inputs?/.test(h)) return /sample|example/.test(h) ? "sample_input" : "input_format";
    if (/^output|^sample outputs?|^example outputs?|^expected output/.test(h)) return /sample|example|expected/.test(h) ? "sample_output" : "output_format";
    if (/^constraint/.test(h)) return "constraints";
    if (/^explanation|^note/.test(h)) return "explanation";
    if (/^examples?$/.test(h)) return "example";
    if (/schema/.test(h)) return "schema";
    return "statement";
  }

  function parseSections(text) {
    const lines = text.replace(/ /g, " ").split("\n");
    const sections = [];
    let cur = { key: "statement", n: 0, lines: [], explicit: false };
    let exampleN = 0, inExample = false;
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      let m = line.match(HEADING_COLON);
      if (!m && line.trim().length <= 30) m = line.match(HEADING_BARE);
      if (m) {
        let key = canonicalHeading(m[1]);
        const n = m[2] ? parseInt(m[2], 10) : 0;
        if (key === "example") { inExample = true; exampleN = n || exampleN + 1; }
        if (inExample && key === "input_format") key = "sample_input";
        if (inExample && key === "output_format") key = "sample_output";
        if (key === "input_format" || key === "constraints") inExample = false;
        sections.push(cur);
        cur = { key, n: n || (key.startsWith("sample") && inExample ? exampleN : 0), lines: m[3] ? [m[3]] : [], explicit: true };
        continue;
      }
      cur.lines.push(line);
    }
    sections.push(cur);
    return sections.map((s) => ({ ...s, text: s.lines.join("\n").replace(/\n{3,}/g, "\n\n").replace(/^\n+|\s+$/g, "") }));
  }

  function samplesFrom(sections, container) {
    const ins = sections.filter((s) => s.key === "sample_input");
    const outs = sections.filter((s) => s.key === "sample_output");
    const samples = [];
    for (let i = 0; i < Math.max(ins.length, outs.length); i++)
      samples.push({ input: (ins[i] && ins[i].text) || "", output: (outs[i] && outs[i].text) || "" });
    if (!samples.length && container) {
      const pres = [...container.querySelectorAll("pre")].map((p) => p.innerText.trim()).filter(Boolean);
      for (let i = 0; i + 1 < pres.length; i += 2) samples.push({ input: pres[i], output: pres[i + 1] });
    }
    return samples.filter((s) => s.input || s.output);
  }

  function precedingText(el) {
    let n = el.previousElementSibling, guard = 0;
    while (n && guard++ < 3) {
      const t = (n.innerText || n.textContent || "").trim();
      if (t) return t.split("\n").slice(-2).join(" ");
      n = n.previousElementSibling;
    }
    return el.parentElement && el.parentElement !== document.body ? precedingText(el.parentElement) : "";
  }

  const cellText = (c) => (c.innerText || c.textContent || "").trim();

  function sqlTables(container, text) {
    const tables = [];
    const has = (name) => tables.some((t) => t.name.toLowerCase() === name.toLowerCase());
    // GUVI SQLKata: "Table Name: pets" heading + table inside the (often hidden) Tables panel; first row = columns.
    document.querySelectorAll(".tables-container h4, .tables-container h5").forEach((h) => {
      const name = h.textContent.replace(/table\s*name\s*:?/i, "").trim();
      const t = h.parentElement && h.parentElement.querySelector("table");
      if (!name || !t || has(name)) return;
      const rows = [...t.rows].map((r) => [...r.cells].map(cellText));
      if (rows.length) tables.push({ name, columns: rows[0], rows: rows.slice(1), role: "input" });
    });
    (container || document).querySelectorAll("table").forEach((t, i) => {
      const rows = [...t.rows].map((r) => [...r.cells].map(cellText));
      if (rows.length < 1 || rows[0].length < 1) return;
      const caption = (t.caption && t.caption.innerText) || precedingText(t);
      const nm = caption.match(/table\s*(?:name)?\s*[:\-]?\s*[`"']?([A-Za-z_][\w]*)/i) || caption.match(/([A-Za-z_]\w*)\s+table/i);
      const role = /output|result|expected/i.test(caption) ? "expected_output" : (nm ? "input" : "unknown");
      const name = nm ? nm[1] : role === "expected_output" ? "expected_output" : `table_${i + 1}`;
      if (!has(name)) tables.push({ name, columns: rows[0], rows: rows.slice(1), role });
    });
    const re = /(?:table\s*[:\-]?\s*[`"']?([A-Za-z_]\w*)[`"']?|([A-Za-z_]\w*)\s+table|(output|result|expected output)\s*[:\-]?)\s*\n((?:\s*[+|].*\n?){2,})/gi;
    let m;
    while ((m = re.exec(text))) {
      const body = m[4].split("\n").filter((l) => /^\s*\|/.test(l)).map((l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
      if (body.length < 1) continue;
      const name = m[1] || m[2] || "expected_output";
      if (!has(name)) tables.push({ name, columns: body[0], rows: body.slice(1), role: m[3] ? "expected_output" : "input" });
    }
    const cre = /create\s+table\s+[`"]?(\w+)[`"]?\s*\(([\s\S]*?)\)\s*;/gi;
    while ((m = cre.exec(text))) {
      const cols = m[2].split(",").map((c) => c.trim().split(/\s+/)[0].replace(/[`"]/g, "")).filter((c) => c && !/^(primary|foreign|unique|key|constraint)$/i.test(c));
      if (!has(m[1])) tables.push({ name: m[1], columns: cols, rows: [], role: "input" });
    }
    return tables;
  }

  /** GUVI SQLKata sub-questions: one accordion item each, each with its own editor buffer. */
  function partNodes() {
    return [...document.querySelectorAll(".question-container .collapse, [class*=question-desc] .collapse")]
      .filter((c) => c.querySelector(".collapse-title") && c.querySelector(".collapse-content"));
  }
  function questionParts() {
    return partNodes().map((c, i) => {
      const body = c.querySelector(".collapse-content");
      return { index: i + 1, title: c.querySelector(".collapse-title").textContent.trim() || `Question ${i + 1}`,
               text: (body.innerText || body.textContent || "").trim() };
    }).filter((p) => p.text);
  }
  function editorHeader() {
    const h = [...document.querySelectorAll('[class*="editor" i] *')].find((e) => e.children.length === 0 && /code editor/i.test(e.textContent));
    return h ? (h.parentElement.textContent || "").replace(/\s+/g, " ") : "";
  }
  async function selectPart(p) {
    const node = partNodes()[p.index - 1];
    if (!node) throw fail(`Sub-question ${p.index} was not found on this page.`, "part_not_found");
    const want = new RegExp(`Question\\s*${p.index}\\b`, "i");
    if (want.test(editorHeader())) return { selected: p.index, verified: true };
    const targets = [node.querySelector(".collapse-title"), node.querySelector("input[type=radio],input[type=checkbox]")].filter(Boolean);
    for (const t of targets) {
      t.click();
      for (let i = 0; i < 8; i++) { await sleep(250); if (want.test(editorHeader())) return { selected: p.index, verified: true }; }
    }
    throw fail(`Could not switch the editor to sub-question ${p.index}.`, "part_switch_failed");
  }

  function extract(params = {}) {
    const ov = params.overrides || {};
    const module = params.module || moduleFromUrl(location.href) || "codekata";
    const c = findProblemContainer(ov);
    const warnings = [];
    if (!c) warnings.push("Problem container not found; using page text.");
    const text = ((c ? c.el.innerText || c.el.textContent : document.body.innerText) || "").slice(0, 20000);
    const sections = parseSections(text);
    const pick = (k, explicitOnly) => sections.filter((s) => s.key === k && (!explicitOnly || s.explicit)).map((s) => s.text).filter(Boolean).join("\n\n").trim();
    const title = c ? findTitle(c.el, ov) : cleanTitle(document.title);
    // If the page labels a "Problem Statement", anything before that label is chrome (badges, counters).
    let statement = pick("statement", sections.some((s) => s.key === "statement" && s.explicit && s.text));
    if (title && statement.startsWith(title)) statement = statement.slice(title.length).trim();
    statement = statement.replace(/^(easy|medium|hard|beginner|intermediate|advanced)\s*\n+/i, "");
    const explanation = pick("explanation");
    if (explanation) statement += `\n\nExplanation:\n${explanation}`;
    const schema = pick("schema");
    if (schema) statement += `\n\nSchema:\n${schema}`;
    const samples = samplesFrom(sections, c && c.el);
    const ed = detectEditor(ov);
    let starter = null;
    try { starter = ed.found ? readEditor(ov).content : null; } catch { /* no editor */ }
    if (starter && starter.length > 8000) starter = starter.slice(0, 8000);
    const region = c && (c.el.closest(".question-container") || c.el.parentElement);
    const diffM = region ? (region.innerText || region.textContent || "").match(DIFF) : null;
    let language = ed.found ? ed.language : null;
    if (module === "webkata") {
      const track = (location.pathname.match(/webkata\/([a-z]+)/i) || [])[1];
      language = { html: "html", css: "css", javascript: "javascript", js: "javascript", bootstrap: "html" }[(track || "").toLowerCase()] || language || "html";
    }
    const parts = module === "sqlkata" ? questionParts() : [];
    const out = {
      url: location.href.split("?")[0], module, title, statement: statement.slice(0, 12000),
      input_format: pick("input_format"), output_format: pick("output_format"), constraints: pick("constraints"),
      samples, language, difficulty: diffM ? diffM[1].toLowerCase() : null,
      starter_code: starter && starter.trim() ? starter : null,
      sql_tables: module === "sqlkata" ? sqlTables(c && c.el, text) : [],
      parts, raw_text: text.slice(0, 15000), extraction_warnings: warnings,
    };
    if (!out.samples.length && (module === "codekata" || module === "fixthecode")) warnings.push("No sample input/output detected.");
    if (module === "sqlkata" && !out.sql_tables.length) warnings.push("No table schema detected.");
    if (!ed.found) warnings.push("No code editor detected.");
    return out;
  }

  // ------------------------------------------------------------------ discovery
  function qwikStrings() {
    try {
      const s = document.querySelector('script[type="qwik/json"]');
      return s ? (JSON.parse(s.textContent).objs || []).filter((o) => typeof o === "string" && o.length < 200) : [];
    } catch { return []; }
  }

  function statusFromCell(cell) {
    // GUVI shows a grey dash for untouched questions; solved ones are green, attempted ones another colour.
    const fills = [...cell.querySelectorAll("[fill],[stroke]")].flatMap((e) => [e.getAttribute("fill"), e.getAttribute("stroke")])
      .filter((f) => f && /^#[0-9a-f]{6}$/i.test(f));
    if (!fills.length) return /solved|completed/i.test(cell.textContent) ? "solved" : "unknown";
    let green = false, coloured = false;
    for (const f of fills) {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(f.slice(i, i + 2), 16));
      if (Math.max(r, g, b) - Math.min(r, g, b) < 24) continue;   // grey / white / black
      coloured = true;
      if (g > r + 30 && g > b + 20) green = true;
    }
    return green ? "solved" : coloured ? "attempted" : "todo";
  }

  function questionTable() {
    const tables = [...document.querySelectorAll("table.questions-table, table")].filter((t) => visible(t) && t.tBodies[0] && t.tBodies[0].rows.length);
    return tables.find((t) => t.classList.contains("questions-table"))
        || tables.find((t) => /question|problem|title/i.test(t.tHead ? t.tHead.textContent : "") && getComputedStyle(t.tBodies[0].rows[0]).cursor === "pointer")
        || null;
  }

  function tableItems(table, max) {
    const strings = qwikStrings();
    const head = table.tHead ? [...table.tHead.rows[0].cells].map((c) => c.textContent.trim().toLowerCase()) : [];
    let qi = head.findIndex((h) => /question|problem|title/.test(h));
    const base = location.pathname.replace(/\/+$/, "") + "/";
    const numbered = /\/webkata\/[a-z]+\/$/i.test(base);   // WebKata addresses questions by their list number
    const items = [];
    for (const row of table.tBodies[0].rows) {
      if (!visible(row) || row.cells.length < 2) continue;
      const cells = [...row.cells];
      const qc = cells[qi >= 0 ? qi : 1] || cells[1];
      const raw = qc.textContent.replace(/\s+/g, " ").trim();
      const num = (raw.match(/^(\d+)\s*[.)]/) || [])[1] || null;
      const title = cleanTitle(raw);
      if (!title) continue;
      const diffCell = cells.find((c) => /^(easy|medium|hard)$/i.test(c.textContent.trim()));
      const slug = slugify(title);
      let url = null, guessed = true;
      if (numbered && num) { url = location.origin + base + num + "/"; guessed = false; }
      else if (slug) {
        // GUVI's embedded page data lists each title followed closely by its slug; prefer an exact slug match.
        const i = strings.indexOf(title);
        const exact = strings.find((s) => s.replace(/\//g, "") === slug);
        const near = !exact && i >= 0 ? strings.slice(i + 1, i + 9).find((s) => /^\/?[a-z0-9]+(-[a-z0-9]+)+\/?$/.test(s)) : null;
        const confirmed = exact || near;
        url = location.origin + base + (confirmed ? confirmed.replace(/\//g, "") : slug) + "/";
        guessed = !confirmed;
      }
      items.push({ title, number: num, url, difficulty: diffCell ? diffCell.textContent.trim().toLowerCase() : null,
                   status: statusFromCell(cells[0]), solvedOnGuvi: statusFromCell(cells[0]) === "solved",
                   locator: { listUrl: location.href, text: title, guessed, page: currentPage() } });
      if (items.length >= max) break;
    }
    return items;
  }

  function pagination() {
    const box = [...document.querySelectorAll('[class*="pagination" i]')].find(visible);
    if (!box) return null;
    const buttons = [...box.querySelectorAll("button")];
    const next = buttons.find((b) => /(^|\s)next(\s|$)/i.test(b.className) || /next/i.test(b.getAttribute("aria-label") || ""));
    const active = buttons.find((b) => /active/i.test(b.className));
    const nums = buttons.map((b) => parseInt(b.textContent.trim(), 10)).filter((n) => !isNaN(n));
    return { next: next && !next.disabled ? next : null, page: active ? parseInt(active.textContent.trim(), 10) || 1 : 1, last: nums.length ? Math.max(...nums) : 1 };
  }
  const currentPage = () => { const p = pagination(); return p ? p.page : 1; };

  async function gotoNextPage(table) {
    const p = pagination();
    if (!p || !p.next) return false;
    const before = table.tBodies[0].rows[0] ? table.tBodies[0].rows[0].textContent : "";
    p.next.click();
    for (let i = 0; i < 28; i++) {
      await sleep(250);
      const t = questionTable();
      const now = t && t.tBodies[0].rows[0] ? t.tBodies[0].rows[0].textContent : "";
      if (now && now !== before) { await sleep(300); return true; }
    }
    return false;
  }

  function urlPattern(href) {
    try {
      const u = new URL(href, location.href);
      const segs = u.pathname.replace(/\/+$/, "").split("/");
      segs[segs.length - 1] = "*";
      return u.origin + segs.map((s) => s.replace(/\d+/g, "#")).join("/");
    } catch { return null; }
  }

  function rowOf(a) {
    const semantic = a.closest("li,tr,[role=row],[role=listitem]");
    if (semantic && semantic.querySelectorAll("a[href]").length <= 2) return semantic;
    let el = a;
    for (let i = 0; i < 4 && el.parentElement; i++) {
      const p = el.parentElement;
      if (p.querySelectorAll("a[href]").length > 1 || p.innerText.length > 400) break;
      el = p;
    }
    return el;
  }

  function discoverSync(module, max) {
    const table = questionTable();
    if (table) {
      const p = pagination();
      return { items: tableItems(table, max), strategy: "question-table", pages: p ? { page: p.page, last: p.last } : null };
    }
    const here = location.href.split("#")[0];
    const anchors = [...document.querySelectorAll("a[href]")].filter((a) => {
      if (!visible(a) || a.closest("nav,header,footer,[role=navigation]")) return false;
      const href = a.href.split("#")[0];
      const t = a.innerText.trim();
      return href.startsWith(location.origin) && href !== here && t.length >= 3 && t.length <= 160 && !/sign|login|logout|profile|leaderboard/i.test(href);
    });
    const groups = new Map();
    for (const a of anchors) {
      const p = urlPattern(a.href);
      if (!p) continue;
      if (!groups.has(p)) groups.set(p, []);
      groups.get(p).push(a);
    }
    let best = null, bestScore = 0;
    for (const [p, list] of groups) {
      const uniq = new Set(list.map((a) => a.href)).size;
      if (uniq < 3) continue;
      let score = uniq;
      if (/problem|question|kata|challenge|task/i.test(p)) score *= 2;
      if (module && MODULE_PATTERNS.find(([m]) => m === module)[1].test(p)) score *= 1.5;
      if (score > bestScore) { best = list; bestScore = score; }
    }
    if (best) {
      const seen = new Set();
      const items = [];
      for (const a of best) {
        if (seen.has(a.href)) continue;
        seen.add(a.href);
        const row = rowOf(a);
        const d = row.innerText.match(DIFF);
        const solved = /\b(solved|completed)\b/i.test(row.innerText) || !!row.querySelector('[class*="solved" i],[class*="complete" i]');
        items.push({ title: cleanTitle(a.innerText.trim().split("\n")[0]), url: a.href, difficulty: d ? d[1].toLowerCase() : null, solvedOnGuvi: solved });
        if (items.length >= max) break;
      }
      return { items, strategy: "link-groups", pages: null };
    }
    // Clickable rows without hrefs (other SPA lists).
    const parents = new Map();
    document.querySelectorAll("li,div,tr").forEach((el) => {
      if (!visible(el) || el.closest("nav,header,footer") || !el.parentElement) return;
      const t = el.innerText.trim();
      if (t.length < 3 || t.length > 300 || getComputedStyle(el).cursor !== "pointer") return;
      const key = cssPath(el.parentElement) + " > " + el.tagName.toLowerCase();
      if (!parents.has(key)) parents.set(key, []);
      parents.get(key).push(el);
    });
    let rows = null;
    for (const [key, list] of parents) if (list.length >= 3 && (!rows || list.length > rows.list.length)) rows = { key, list };
    if (!rows) return { items: [], strategy: "none", pages: null };
    return {
      strategy: "clickable-rows", pages: null,
      items: rows.list.slice(0, max).map((el, index) => {
        const t = el.innerText.trim();
        const d = t.match(DIFF);
        return { title: cleanTitle(t.split("\n")[0].slice(0, 120)), url: null, difficulty: d ? d[1].toLowerCase() : null,
                 locator: { listUrl: location.href, selector: rows.key, index, text: t.split("\n")[0].slice(0, 120) } };
      }),
    };
  }

  /** Collect questions from the list exactly as the user has it filtered, following pagination up to `max`. */
  async function discover(params = {}) {
    const max = params.max || 50;
    let res = discoverSync(params.module, max);
    if (res.strategy === "question-table") {
      const seen = new Set(res.items.map((i) => i.title));
      for (let guard = 0; guard < 40 && res.items.length < max; guard++) {
        const table = questionTable();
        if (!table || !(await gotoNextPage(table))) break;
        const more = tableItems(questionTable(), max).filter((i) => !seen.has(i.title));
        if (!more.length) break;
        more.forEach((i) => seen.add(i.title));
        res.items.push(...more);
      }
      res.items = res.items.slice(0, max);
      return { ...res, listUrl: location.href };
    }
    let stable = 0;
    for (let i = 0; i < 8 && res.items.length < max && stable < 2; i++) {
      window.scrollTo(0, document.body.scrollHeight);
      await sleep(900);
      const next = discoverSync(params.module, max);
      stable = next.items.length > res.items.length ? 0 : stable + 1;
      res = next.items.length >= res.items.length ? next : res;
    }
    window.scrollTo(0, 0);
    return { ...res, listUrl: location.href };
  }

  /** Open a list row by clicking it (used when a question has no usable URL). */
  async function openItem(params) {
    const loc = params.locator;
    const want = cleanTitle(loc.text).toLowerCase();
    const table = questionTable();
    if (table) {
      for (let guard = 0; guard < 40; guard++) {
        const t = questionTable();
        const row = t && [...t.tBodies[0].rows].find((r) => [...r.cells].some((c) => cleanTitle(c.textContent).toLowerCase() === want));
        if (row) { row.scrollIntoView({ block: "center" }); row.click(); return { clicked: true }; }
        if (!t || !(await gotoNextPage(t))) break;
      }
      throw fail(`"${loc.text}" is not in the list that is currently shown (re-apply your filter and try again).`, "item_not_found");
    }
    const els = loc.selector ? [...document.querySelectorAll(loc.selector.split(" > ").slice(-2).join(" > "))].filter(visible) : [];
    const el = els.find((e) => cleanTitle(e.innerText.trim().split("\n")[0]).toLowerCase() === want) || els[loc.index];
    if (!el) throw fail(`Could not find "${loc.text}" on the list page.`, "item_not_found");
    el.scrollIntoView({ block: "center" });
    el.click();
    return { clicked: true };
  }

  // ------------------------------------------------------------------ run & submit (opt-in "auto submit" mode only)
  function actionButton(re) {
    return [...document.querySelectorAll("button")].find((b) => visible(b) && !b.disabled && !b.closest("dialog") && re.test(b.textContent.trim()));
  }
  function consoleBox() {
    return document.querySelector("#code-kata-console, #sql-kata-console, #webkata-console, [id$=console i]")
        || [...document.querySelectorAll("[class*=console i]")].find(visible) || null;
  }
  function openDialogs() {
    return [...document.querySelectorAll("dialog[open], .modal-open, [role=dialog]")].filter((d) => visible(d) && d.id !== "mobile_warning_modal");
  }
  /** Resolves once `root` has changed and then stayed quiet for `quiet` ms (or after `max` ms). */
  function settled(root, quiet = 1500, max = 45000) {
    return new Promise((resolve) => {
      let changed = false, timer = null;
      const done = (v) => { obs.disconnect(); clearTimeout(timer); clearTimeout(hard); resolve(v); };
      const obs = new MutationObserver(() => { changed = true; clearTimeout(timer); timer = setTimeout(() => done(true), quiet); });
      obs.observe(root, { childList: true, subtree: true, characterData: true, attributes: true });
      const hard = setTimeout(() => done(changed), max);
    });
  }
  function classify(text) {
    const t = text.toLowerCase();
    if (/\bwrong\b|incorrect|\bfailed\b|runtime error|compil\w* error|syntaxerror|time limit|exception|traceback|not accepted|try again/.test(t)) return "failed";
    if (/\bcorrect\b|accepted|all test ?cases passed|success|congrat|well done|solved/.test(t)) return "passed";
    return "unknown";
  }
  function readRunResult() {
    const box = consoleBox();
    if (!box) return { verdict: "unknown", text: "" };
    const text = (box.innerText || "").replace(/\n{2,}/g, "\n").slice(0, 1500);
    const v = [...box.querySelectorAll('[class*="test-pass"],[class*="test-fail"],[class*="test-wrong"]')].find(visible);
    const pass = box.querySelectorAll('[class*="dot-pass"]').length, dots = box.querySelectorAll('[class*="case-pill-dot"]').length;
    let verdict = v ? (/pass/.test(v.className) && /correct|pass/i.test(v.textContent) ? "passed" : "failed") : classify(text);
    if (dots && pass < dots) verdict = "failed";
    return { verdict, text, cases: dots, casesPassed: pass };
  }
  /** Open each failing test-case pill and collect its input / output so the fixer sees every failure. */
  async function failingCases(fallback) {
    const box = consoleBox();
    const pills = box ? [...box.querySelectorAll("button")].filter((b) => /^case\s*\d+/i.test(b.textContent.trim())) : [];
    const bad = pills.filter((b) => !b.querySelector('[class*="dot-pass"]')).slice(0, 4);
    if (!bad.length) return fallback;
    const out = [];
    for (const pill of bad) {
      pill.click();
      await sleep(350);
      const body = (box.innerText || "").replace(/\n{2,}/g, "\n");
      const at = body.search(/\bInput\b/);
      out.push(`${pill.textContent.trim()} FAILED\n${(at >= 0 ? body.slice(at) : body).replace(/\nConsole\s*$/, "").slice(0, 500)}`);
    }
    return `GUVI verdict: Wrong (${pills.length - bad.length} of ${pills.length} cases passed)\n` + out.join("\n\n");
  }
  /** GUVI marks the open question next to its difficulty: "Attempted!", "Solved!" ... */
  function statusBadge() {
    const c = findProblemContainer();
    const region = c && (c.el.closest(".question-container") || c.el.parentElement);
    const m = region ? (region.innerText || "").match(/\b(attempted|solved|completed|accepted)\b!?/i) : null;
    return m ? m[1].toLowerCase() : "";
  }
  async function runCode() {
    dismissOverlays();
    const btn = actionButton(/^(run code|run|validate)$/i);
    if (!btn) throw fail("No Run button found on this page.", "run_button_missing");
    const box = consoleBox() || document.body;
    const wait = settled(box);
    btn.click();
    const changed = await wait;
    await sleep(400);
    const res = readRunResult();
    if (changed && res.verdict === "failed") res.text = await failingCases(res.text);
    return changed ? res : { ...res, verdict: "unknown", text: "GUVI showed no new result after Run." };
  }
  async function submitCode() {
    dismissOverlays();
    const btn = actionButton(/^submit$/i);
    if (!btn) throw fail("GUVI's Submit button is not available (it may need a successful Run first).", "submit_button_missing");
    const before = document.body.innerText.length;
    const wait = settled(document.body, 2500, 60000);
    btn.click();
    await sleep(1200);
    // GUVI may ask to confirm; the user turned auto-submit on, so confirm it.
    for (const d of openDialogs()) {
      const yes = [...d.querySelectorAll("button")].find((b) => visible(b) && /^(submit|yes|confirm|ok)$/i.test(b.textContent.trim()));
      if (yes) { yes.click(); break; }
    }
    await wait;
    await sleep(800);
    const dlgText = openDialogs().map((d) => d.innerText).join("\n").replace(/\n{2,}/g, "\n").slice(0, 800);
    const toasts = [...document.querySelectorAll('[class*="toast" i],[role=alert],[class*="snackbar" i],[class*="notif" i]')]
      .filter(visible).map((t) => t.innerText).join("\n").slice(0, 300);
    const run = readRunResult();
    const badge = statusBadge();
    // Accepted only on a positive signal: GUVI's result dialog/toast says so, or the question is now marked solved.
    let verdict = classify(dlgText + "\n" + toasts);
    if (/solved|completed|accepted/i.test(badge)) verdict = "passed";
    else if (verdict !== "passed") verdict = "failed";
    return { verdict, badge, text: [dlgText, toasts, run.text].filter(Boolean).join("\n").slice(0, 1500),
             changed: document.body.innerText.length !== before };
  }

  // ------------------------------------------------------------------ diagnostics
  function inspect() {
    const eds = findEditors();
    const c = findProblemContainer();
    const list = discoverSync(null, 500);
    return {
      url: location.href.split("?")[0], title: document.title, module: moduleFromUrl(location.href), loginRequired: loginRequired(),
      editors: eds.map((e) => ({ type: e.type, path: cssPath(e.el), size: Math.round(area(e.el)), hidden: !!e.hidden })),
      editorGlobals: { monaco: !!window.monaco, ace: !!window.ace, CodeMirror: !!window.CodeMirror },
      language: uiLanguage(),
      problemContainer: c ? { path: cssPath(c.el), strategy: c.strategy, markers: c.markers || 0, textLength: c.el.textContent.length } : null,
      detectedTitle: c ? findTitle(c.el) : null,
      sectionHeadings: c ? parseSections(c.el.innerText || c.el.textContent).map((s) => s.key + (s.n ? ` #${s.n}` : "")) : [],
      parts: questionParts().length, sqlTables: document.querySelectorAll(".tables-container table").length,
      headings: [...document.querySelectorAll("h1,h2,h3,h4")].filter(visible).slice(0, 15).map((h) => h.innerText.trim().slice(0, 80)),
      iframes: [...document.querySelectorAll("iframe")].filter(visible).map((f) => ({ src: (f.src || "srcdoc").split("?")[0], size: Math.round(area(f)) })),
      listing: { strategy: list.strategy, count: list.items.length, pages: list.pages,
                 sample: list.items.slice(0, 5).map((i) => ({ title: i.title, url: i.url, status: i.status, guessed: i.locator && i.locator.guessed })) },
    };
  }

  const api = {
    version: VERSION,
    detectPage, extract, discover, openItem, inspect, setLanguage, selectPart, runCode, submitCode,
    detectEditor: (p = {}) => detectEditor(p.overrides),
    readEditor: (p = {}) => readEditor(p.overrides),
    writeEditor: (p = {}) => writeEditor(p.code, p.overrides),
    // Single entry point used by the extension; errors come back as data (executeScript can't throw across worlds).
    async call(method, params) {
      try {
        if (typeof api[method] !== "function" || method === "call") throw fail(`Unknown adapter method ${method}`, "bad_method");
        return { ok: true, result: await api[method](params || {}) };
      } catch (e) {
        return { ok: false, error: { message: String((e && e.message) || e), code: (e && e.code) || "adapter_error" } };
      }
    },
  };
  window.__KataPilot = api;
})();
