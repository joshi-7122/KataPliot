import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { QueueTable } from '../components/QueueTable'
import { Empty, Panel, sessionMeta, timeAgo } from '../components/ui'
import { useStore } from '../store'
import { MODULES } from '../types'
import type { QuestionRow, SessionRow } from '../types'
import { History as HistoryIcon, Search } from 'lucide-react'

export function HistoryPage() {
  const { solutionTick, status, run, refresh } = useStore()
  const [rows, setRows] = useState<QuestionRow[]>([])
  const [sessions, setSessions] = useState<SessionRow[]>([])
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  useEffect(() => {
    api.history().then(setRows).catch(() => setRows([]))
    api.sessions().then(setSessions).catch(() => setSessions([]))
  }, [solutionTick, status?.agent.state])
  const shown = useMemo(() => rows.filter((r) =>
    (filter === 'all' || r.status === filter) && r.title.toLowerCase().includes(query.toLowerCase())), [rows, query, filter])
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight">Question history</h1>
        <p className="mt-1 text-fg-dim">Every question the agent has touched, with its solution, validation results and editor backups.</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-72 max-w-full">
          <Search size={13} className="absolute left-2.5 top-[9px] text-fg-mute" />
          <input className="field !pl-8" placeholder="Search by title" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <select className="field !w-44" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All statuses</option><option value="code_ready">Code Ready</option><option value="needs_review">Needs Review</option>
          <option value="failed">Failed</option><option value="skipped">Skipped</option>
        </select>
      </div>
      <QueueTable rows={shown} title="Questions" empty="No questions match." />
      <Panel title="Sessions" icon={<HistoryIcon size={14} />}>
        {sessions.length === 0 ? <Empty title="No sessions yet" /> : (
          <div className="max-h-72 overflow-y-auto">
            {sessions.map((s) => {
              const m = sessionMeta(s.state)
              return (
                <div key={s.id} className="flex items-center gap-3 border-t hairline px-4 py-2.5 text-[13px] first:border-t-0">
                  <span className="w-12 font-mono text-xs text-fg-mute">#{s.id}</span>
                  <span className="w-28">{MODULES.find((x) => x.id === s.module)?.label}</span>
                  <span className="flex items-center gap-1.5 text-xs" style={{ color: m.color }}><span className="h-1.5 w-1.5 rounded-full" style={{ background: m.color }} />{m.label}</span>
                  <span className="text-xs text-fg-mute">{s.ready ?? 0}/{s.total ?? 0} ready</span>
                  <span className="ml-auto text-xs text-fg-mute">{timeAgo(s.created_at)}</span>
                  {s.state === 'interrupted' && (
                    <button className="btn !h-7" onClick={async () => { await run(() => api.resumeSession(s.id)); refresh() }}>Resume</button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </Panel>
    </div>
  )
}
