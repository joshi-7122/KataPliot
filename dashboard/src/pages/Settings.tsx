import { Cpu, ScanSearch, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api'
import { Panel } from '../components/ui'
import { useStore } from '../store'
import { MODULES } from '../types'
import type { Module, Prefs } from '../types'

export function SettingsPage() {
  const { prefs, savePrefs, status, run, notify } = useStore()
  const [snapshot, setSnapshot] = useState<string | null>(null)
  const [ovModule, setOvModule] = useState<Module>('codekata')
  if (!prefs) return <div className="text-fg-mute">Loading…</div>
  const ov = prefs.selector_overrides[ovModule] ?? {}
  const setOv = (k: 'problemSelector' | 'titleSelector' | 'editorSelector', v: string) =>
    savePrefs({ selector_overrides: { ...prefs.selector_overrides, [ovModule]: { ...ov, [k]: v || undefined } } })

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-fg-dim">Preferences are stored locally in SQLite. API keys live only in <span className="font-mono text-fg">backend/.env</span>.</p>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Agent preferences" icon={<SlidersHorizontal size={14} />}>
          <div className="grid gap-4 p-4">
            <label className="grid gap-1.5"><span className="label">Preferred language (when GUVI's selection can't be detected)</span>
              <select className="field" value={prefs.preferred_language} onChange={(e) => savePrefs({ preferred_language: e.target.value })}>
                <option value="cpp">C++</option><option value="c">C</option><option value="java">Java</option><option value="python">Python</option>
              </select></label>
            <label className="grid gap-1.5"><span className="label">Default review mode</span>
              <select className="field" value={prefs.review_mode} onChange={(e) => savePrefs({ review_mode: e.target.value as Prefs['review_mode'] })}>
                <option value="insert_and_continue">Insert, then continue</option><option value="review_each">Pause after each question</option><option value="prepare_only">Prepare only, don't insert</option>
              </select></label>
            <label className="grid gap-1.5"><span className="label">Default max questions per session</span>
              <input className="field" type="number" min={1} max={200} value={prefs.max_questions} onChange={(e) => savePrefs({ max_questions: Math.max(1, Math.min(200, Number(e.target.value) || 1)) })} /></label>
            <label className="flex items-center justify-between text-[13px] text-fg-dim">
              Use web search when a provider is configured
              <input type="checkbox" className="h-4 w-4 accent-[#5b8cff]" checked={prefs.use_web_search} onChange={(e) => savePrefs({ use_web_search: e.target.checked })} />
            </label>
          </div>
        </Panel>

        <Panel title="Providers" icon={<Cpu size={14} />}>
          <div className="grid gap-2 p-4 text-[13px]">
            <KV k="LLM provider" v={status?.llm.provider ?? '—'} />
            <KV k="Model" v={status?.llm.model ?? '—'} />
            <KV k="Reasoning effort" v={status?.llm.effort ?? '—'} />
            <KV k="Web search" v={status?.search.configured ? status.search.provider : 'Not configured'} />
            <KV k="Code sandbox" v={status?.sandbox.backend ?? '—'} />
            {status?.llm.error && <div className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-xs text-bad">{status.llm.error}</div>}
            <p className="mt-1 text-xs text-fg-mute">Change these in <span className="font-mono">backend/.env</span> (LLM_PROVIDER, LLM_MODEL, SEARCH_PROVIDER, SANDBOX_BACKEND) and restart the backend.</p>
          </div>
        </Panel>

        <Panel title="GUVI adapter" icon={<ScanSearch size={14} />} className="xl:col-span-2">
          <div className="grid gap-4 p-4 lg:grid-cols-2">
            <div className="grid content-start gap-3">
              <p className="text-[13px] text-fg-dim">The adapter finds the problem text and editor by structure, not fixed class names. If GUVI's layout defeats it, inspect the open page and pin CSS selectors per module.</p>
              <div className="flex items-center gap-2">
                <button className="btn" disabled={!status?.extension.connected}
                  onClick={async () => { const r = await run(() => api.inspect()); if (r) setSnapshot(JSON.stringify(r, null, 2)) }}>
                  <ScanSearch size={13} />Inspect open GUVI page
                </button>
                {snapshot && <button className="btn" onClick={() => { navigator.clipboard.writeText(snapshot); notify('info', 'Snapshot copied') }}>Copy snapshot</button>}
              </div>
              <label className="grid gap-1.5"><span className="label">Overrides for</span>
                <select className="field" value={ovModule} onChange={(e) => setOvModule(e.target.value as Module)}>{MODULES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select></label>
              {(['problemSelector', 'titleSelector', 'editorSelector'] as const).map((k) => (
                <label key={k} className="grid gap-1.5"><span className="label">{k.replace('Selector', ' selector')}</span>
                  <input className="field font-mono" placeholder="auto-detect" defaultValue={ov[k] ?? ''} key={ovModule + k} onBlur={(e) => setOv(k, e.target.value.trim())} /></label>
              ))}
            </div>
            <pre className="h-80 overflow-auto rounded-lg border hairline bg-black/30 p-3 font-mono text-[11.5px] leading-relaxed text-fg-dim">
              {snapshot ?? 'Structural snapshot of the page appears here: editor type, problem container, detected sections, list links. It contains page structure and headings only.'}
            </pre>
          </div>
        </Panel>

        <Panel title="Safety" icon={<ShieldCheck size={14} />} className="xl:col-span-2">
          <ul className="grid gap-2 p-4 text-[13px] text-fg-dim md:grid-cols-2">
            {['Uses your existing GUVI sign-in. Your password is never requested or stored.',
              'Never clicks Run or Submit. Submitting is always your decision.',
              'Editor content is backed up before every insertion and can be restored.',
              'Generated code only runs inside a sandbox with no network access.',
              'The extension only touches *.guvi.in pages and stops at sign-in or CAPTCHA pages.',
              'Check your course rules: if a kata is graded, AI-prepared answers may not be allowed.'].map((t) => (
              <li key={t} className="flex gap-2"><span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-ok" />{t}</li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  )
}

function KV({ k, v }: { k: string; v: string }) {
  return <div className="flex items-center justify-between border-b hairline pb-2 last:border-b-0"><span className="text-fg-mute">{k}</span><span className="font-mono text-[12.5px] text-fg">{v}</span></div>
}
