import { Pause, Play, Radar, SkipForward, Square, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import { MODULES } from '../types'
import type { Module, SessionConfig } from '../types'
import { Panel, sessionMeta } from './ui'

const LANGS: Record<Module, { id: string; label: string }[]> = {
  codekata: [{ id: 'auto', label: 'Auto (GUVI selection)' }, { id: 'cpp', label: 'C++' }, { id: 'c', label: 'C' }, { id: 'java', label: 'Java' }, { id: 'python', label: 'Python' }],
  fixthecode: [{ id: 'auto', label: 'Auto (language of the code)' }, { id: 'cpp', label: 'C++' }, { id: 'c', label: 'C' }, { id: 'java', label: 'Java' }, { id: 'python', label: 'Python' }],
  sqlkata: [{ id: 'sql', label: 'SQL' }],
  webkata: [{ id: 'auto', label: 'Auto' }, { id: 'html', label: 'HTML' }, { id: 'css', label: 'CSS' }, { id: 'javascript', label: 'JavaScript' }],
}

export function ControlPanel({ lockedModule }: { lockedModule?: Module }) {
  const { status, prefs, run, session, refresh, notify, sessionError } = useStore()
  const [module, setModule] = useState<Module>(lockedModule ?? 'codekata')
  const [language, setLanguage] = useState('auto')
  const [discovery, setDiscovery] = useState<SessionConfig['discovery']>('listing')
  const [urls, setUrls] = useState('')
  const [reviewMode, setReviewMode] = useState<SessionConfig['review_mode']>('insert_and_continue')
  const [max, setMax] = useState(10)
  const [busy, setBusy] = useState(false)
  const [probe, setProbe] = useState<string | null>(null)

  useEffect(() => { if (lockedModule) setModule(lockedModule) }, [lockedModule])
  useEffect(() => { setLanguage(module === 'sqlkata' ? 'sql' : 'auto') }, [module])
  useEffect(() => {
    if (prefs) { setReviewMode(prefs.review_mode); setMax(prefs.max_questions) }
  }, [prefs])

  const state = status?.agent.state ?? 'idle'
  const active = ['running', 'paused', 'waiting_review', 'stopping'].includes(state)
  const meta = sessionMeta(state)
  const canStart = !!status?.extension.connected && !status?.llm.error && !active && !busy

  const start = async () => {
    setBusy(true)
    await run(() => api.start({
      module, language, discovery, review_mode: reviewMode, max_questions: max,
      urls: urls.split('\n').map((u) => u.trim()).filter(Boolean),
      skip_already_prepared: true, use_web_search: prefs?.use_web_search ?? true,
    }))
    setBusy(false)
  }
  const discover = async () => {
    setProbe('Looking at the GUVI tab…')
    const d = await run(() => api.detect())
    if (!d) { setProbe(null); return }
    const ed = d.editor as { found?: boolean; type?: string; language?: string } | undefined
    setProbe(d.loginRequired ? 'GUVI is showing a sign-in page — sign in first.'
      : d.pageType === 'question' ? `Question page · ${ed?.type ?? 'editor'}${ed?.language ? ` · ${ed.language}` : ''}`
      : d.pageType === 'listing' ? `Listing page · ${d.listingCount} question link(s) found`
      : 'This page does not look like a GUVI question or list. Open one in the agent tab.')
  }
  const ctl = (a: 'pause' | 'resume' | 'skip' | 'stop') => run(() => api.control(a))

  return (
    <Panel title="Agent control" icon={<Zap size={14} />}
      action={<span className="flex items-center gap-1.5 text-[11.5px]" style={{ color: meta.color }}>
        <span className={`h-1.5 w-1.5 rounded-full ${state === 'running' ? 'pulse' : ''}`} style={{ background: meta.color }} />{meta.label}
      </span>}>
      <div className="grid gap-3 p-4">
        <div className="grid grid-cols-2 gap-3">
          <label className="grid gap-1.5">
            <span className="label">Module</span>
            <select className="field" value={module} disabled={!!lockedModule || active} onChange={(e) => setModule(e.target.value as Module)}>
              {MODULES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          <label className="grid gap-1.5">
            <span className="label">Language</span>
            <select className="field" value={language} disabled={active} onChange={(e) => setLanguage(e.target.value)}>
              {LANGS[module].map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
            </select>
          </label>
          <label className="grid gap-1.5">
            <span className="label">Questions from</span>
            <select className="field" value={discovery} disabled={active} onChange={(e) => setDiscovery(e.target.value as SessionConfig['discovery'])}>
              <option value="listing">List on the open GUVI page</option>
              <option value="current">Only the open question</option>
              <option value="urls">URLs I paste</option>
            </select>
          </label>
          <label className="grid gap-1.5">
            <span className="label">Review mode</span>
            <select className="field" value={reviewMode} disabled={active} onChange={(e) => setReviewMode(e.target.value as SessionConfig['review_mode'])}>
              <option value="auto_submit">Run + auto-submit</option>
              <option value="insert_and_continue">Insert, then continue</option>
              <option value="review_each">Pause after each question</option>
              <option value="prepare_only">Prepare only, don't insert</option>
            </select>
          </label>
        </div>
        {discovery === 'urls' && (
          <textarea className="field" rows={3} placeholder="One GUVI question URL per line" value={urls} disabled={active} onChange={(e) => setUrls(e.target.value)} />
        )}
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-fg-dim">
            Max questions
            <input className="field !w-16 text-center" type="number" min={1} max={200} value={max} disabled={active}
              onChange={(e) => setMax(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} />
          </label>
          <button className="btn ml-auto" onClick={discover} disabled={!status?.extension.connected}><Radar size={13} />Check GUVI tab</button>
        </div>
        {probe && <div className="rounded-lg border hairline bg-black/20 px-3 py-2 text-xs text-fg-dim">{probe}</div>}
        {sessionError && (state === 'paused' || state === 'failed') && (
          <div className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn">{sessionError}</div>
        )}
        {!status?.extension.connected && (
          <div className="rounded-lg border hairline bg-black/20 px-3 py-2 text-xs text-fg-dim">
            Chrome extension not connected. Load <span className="font-mono text-fg">katapilot/extension</span> in chrome://extensions and open a GUVI tab.
          </div>
        )}
        {status?.llm.error && <div className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-xs text-bad">{status.llm.error}</div>}

        <div className="flex flex-wrap items-center gap-2 border-t hairline pt-3">
          {!active ? (
            <>
              <button className="btn btn-primary h-9 px-4 text-[13px]" disabled={!canStart} onClick={start}><Play size={14} fill="currentColor" />Start Session</button>
              {session?.state === 'interrupted' && (
                <button className="btn h-9" onClick={async () => { await run(() => api.resumeSession(session.id)); refresh(); notify('info', 'Resuming interrupted session') }}>
                  Resume session #{session.id}
                </button>
              )}
            </>
          ) : (
            <>
              {state === 'running' && <button className="btn h-9" onClick={() => ctl('pause')}><Pause size={13} />Pause</button>}
              {(state === 'paused' || state === 'waiting_review') && (
                <button className="btn btn-primary h-9" onClick={() => ctl('resume')}><Play size={13} fill="currentColor" />{state === 'waiting_review' ? 'Next question' : 'Resume'}</button>
              )}
              <button className="btn h-9" onClick={() => ctl('skip')} disabled={state === 'stopping'}><SkipForward size={13} />Skip</button>
              <button className="btn h-9 !border-bad/40 !text-bad hover:!bg-bad/10" onClick={() => ctl('stop')} disabled={state === 'stopping'}><Square size={12} fill="currentColor" />Stop</button>
            </>
          )}
          <span className="ml-auto text-[11.5px] text-fg-mute">{reviewMode === 'auto_submit' ? 'Runs on GUVI, fixes failing code, and submits when all tests pass' : 'Never clicks Run or Submit'}</span>
        </div>
      </div>
    </Panel>
  )
}
