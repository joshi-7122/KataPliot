import { useEffect, useState } from 'react'
import { api } from '../api'
import { ControlPanel } from '../components/ControlPanel'
import { LiveMonitor } from '../components/LiveMonitor'
import { QueueTable } from '../components/QueueTable'
import { useStore } from '../store'
import { MODULES } from '../types'
import type { Module, QuestionRow } from '../types'

const TIPS: Record<Module, string[]> = {
  codekata: ['Open a CodeKata topic list (or a single problem) in the agent tab.', 'Auto language follows whatever is selected in GUVI; your default is set in Settings.', 'Samples on the page are compiled and run in the sandbox before anything is inserted.'],
  sqlkata: ['Tables shown on the page become a temporary SQLite database for checking the query.', 'Only tables and columns that appear in the problem are used.', 'MySQL-only syntax is checked through an equivalent SQLite query, so treat results as a strong hint.'],
  webkata: ['HTML structure, CSS braces and JavaScript syntax are checked; visual output is not.', 'Required ids and classes named in the task are verified to exist in the code.'],
  fixthecode: ['The code in the GUVI editor is read first and backed up.', 'The fix keeps the original structure and changes as little as possible.'],
}

export function ModulePage({ module }: { module: Module }) {
  const { status, questions, solutionTick } = useStore()
  const [history, setHistory] = useState<QuestionRow[]>([])
  const meta = MODULES.find((m) => m.id === module)!
  const isActive = status?.agent.config?.module === module
  useEffect(() => { api.history(module).then(setHistory).catch(() => setHistory([])) }, [module, solutionTick, status?.agent.state])
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight">{meta.label}</h1>
        <p className="mt-1 text-fg-dim">{meta.blurb}</p>
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="space-y-4">
          <ControlPanel lockedModule={module} />
          <div className="glass p-4">
            <div className="label mb-2">How this module works</div>
            <ul className="space-y-1.5 text-[13px] text-fg-dim">
              {TIPS[module].map((t) => <li key={t} className="flex gap-2"><span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-accent" />{t}</li>)}
            </ul>
          </div>
        </div>
        <LiveMonitor />
      </div>
      {isActive && <QueueTable rows={questions} title="Current session" />}
      <QueueTable rows={history} title={`${meta.label} history`} empty="Nothing processed in this module yet." />
    </div>
  )
}
