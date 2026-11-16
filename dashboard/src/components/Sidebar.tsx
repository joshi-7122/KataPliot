import { Braces, Bug, Code2, Database, History, LayoutDashboard, Settings } from 'lucide-react'
import type { ReactNode } from 'react'
import { useStore } from '../store'

export type View = 'overview' | 'codekata' | 'sqlkata' | 'webkata' | 'fixthecode' | 'history' | 'settings'

const NAV: { id: View; label: string; icon: ReactNode; group: string }[] = [
  { id: 'overview', label: 'Overview', icon: <LayoutDashboard size={15} />, group: 'Workspace' },
  { id: 'codekata', label: 'CodeKata', icon: <Code2 size={15} />, group: 'Modules' },
  { id: 'sqlkata', label: 'SQLKata', icon: <Database size={15} />, group: 'Modules' },
  { id: 'webkata', label: 'WebKata', icon: <Braces size={15} />, group: 'Modules' },
  { id: 'fixthecode', label: 'FixTheCode', icon: <Bug size={15} />, group: 'Modules' },
  { id: 'history', label: 'Question History', icon: <History size={15} />, group: 'Library' },
  { id: 'settings', label: 'Settings', icon: <Settings size={15} />, group: 'Library' },
]

export function Sidebar({ view, onView }: { view: View; onView: (v: View) => void }) {
  const { status, backendUp } = useStore()
  const groups = [...new Set(NAV.map((n) => n.group))]
  const ext = status?.extension.connected
  return (
    <aside className="flex w-[216px] shrink-0 flex-col border-r hairline bg-ink-900/60 backdrop-blur-xl max-md:w-14">
      <div className="flex h-14 items-center gap-2.5 px-4 max-md:justify-center max-md:px-0">
        <div className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-accent to-accent-2 shadow-[0_4px_16px_-4px_rgba(91,140,255,.7)]">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 4l4 4-4 4M9 12h4" /></svg>
        </div>
        <div className="leading-tight max-md:hidden">
          <div className="text-[13.5px] font-semibold tracking-tight">KataPilot</div>
          <div className="text-[10.5px] text-fg-mute">GUVI practice agent</div>
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto px-2 pb-3">
        {groups.map((g) => (
          <div key={g} className="mt-3">
            <div className="label px-2 pb-1.5 max-md:hidden">{g}</div>
            {NAV.filter((n) => n.group === g).map((n) => {
              const active = view === n.id
              return (
                <button key={n.id} onClick={() => onView(n.id)} title={n.label}
                  className={`group relative mb-0.5 flex h-8 w-full items-center gap-2.5 rounded-lg px-2 text-[13px] transition-colors max-md:justify-center
                    ${active ? 'bg-white/[0.07] text-fg' : 'text-fg-dim hover:bg-white/[0.04] hover:text-fg'}`}>
                  {active && <span className="absolute left-0 top-1.5 h-5 w-[2px] rounded-full bg-gradient-to-b from-accent to-accent-2" />}
                  <span className={active ? 'text-accent' : 'text-fg-mute group-hover:text-fg-dim'}>{n.icon}</span>
                  <span className="max-md:hidden">{n.label}</span>
                </button>
              )
            })}
          </div>
        ))}
      </nav>
      <div className="border-t hairline p-3 text-[11.5px] max-md:hidden">
        <Row ok={backendUp} label="Backend" value={backendUp ? 'Online' : 'Offline'} />
        <Row ok={!!ext} label="Extension" value={ext ? 'Connected' : 'Not connected'} />
        <Row ok={!status?.llm.error && !!status} label="Model" value={status?.llm.model ?? status?.llm.provider ?? '—'} />
      </div>
    </aside>
  )
}

function Row({ ok, label, value }: { ok: boolean; label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-fg-mute">{label}</span>
      <span className="flex items-center gap-1.5 truncate text-fg-dim">
        <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-ok' : 'bg-fg-mute'}`} />
        <span className="max-w-[110px] truncate">{value}</span>
      </span>
    </div>
  )
}
