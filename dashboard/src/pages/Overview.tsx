import { motion } from 'framer-motion'
import { CircleCheck, Clock, Gauge, Layers, PlugZap } from 'lucide-react'
import type { ReactNode } from 'react'
import { ControlPanel } from '../components/ControlPanel'
import { LiveMonitor } from '../components/LiveMonitor'
import { QueueTable } from '../components/QueueTable'
import { useStore } from '../store'
import { MODULES } from '../types'

function Stat({ icon, label, value, sub, i }: { icon: ReactNode; label: string; value: string; sub?: string; i: number }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04, duration: 0.3 }}
      className="glass group p-4 transition-colors hover:border-line-strong">
      <div className="flex items-center justify-between text-fg-mute"><span className="label">{label}</span>{icon}</div>
      <div className="mt-2 text-[24px] font-semibold leading-none tracking-tight tabular-nums">{value}</div>
      <div className="mt-1.5 truncate text-[11.5px] text-fg-mute">{sub ?? ' '}</div>
    </motion.div>
  )
}

export function Overview() {
  const { status, questions } = useStore()
  const st = status?.stats
  const done = (st?.ready ?? 0) + (st?.review ?? 0) + (st?.failed ?? 0)
  const rate = done ? Math.round(((st?.ready ?? 0) / done) * 100) : null
  const activeModule = status?.agent.config?.module
  const pendingNow = questions.filter((q) => q.status === 'pending').length
  const hour = new Date().getHours()
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight">{hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'}</h1>
          <p className="mt-1 text-fg-dim">KataPilot prepares and validates solutions in your GUVI tab. You review and submit.</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-fg-mute">
          <span className="rounded-md border hairline bg-white/[0.03] px-2 py-1">Sandbox · <span className="text-fg-dim">{status?.sandbox.backend ?? '—'}</span></span>
          <span className="rounded-md border hairline bg-white/[0.03] px-2 py-1">Search · <span className="text-fg-dim">{status?.search.configured ? status.search.provider : 'off'}</span></span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat i={0} icon={<PlugZap size={14} />} label="GUVI session" value={status?.extension.connected ? 'Connected' : 'Offline'}
          sub={status?.extension.connected ? 'Using your signed-in Chrome tab' : 'Extension not connected'} />
        <Stat i={1} icon={<Layers size={14} />} label="Active module" value={activeModule ? MODULES.find((m) => m.id === activeModule)!.label : '—'} sub={status?.agent.state ?? 'idle'} />
        <Stat i={2} icon={<Gauge size={14} />} label="Processed" value={String(done)} sub={`${st?.inserted ?? 0} inserted into GUVI`} />
        <Stat i={3} icon={<CircleCheck size={14} />} label="Solutions prepared" value={String(st?.ready ?? 0)} sub={rate == null ? 'No sessions yet' : `${rate}% validated on samples`} />
        <Stat i={4} icon={<Clock size={14} />} label="Pending" value={String(pendingNow)} sub={`${st?.review ?? 0} need review · ${st?.failed ?? 0} failed`} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <ControlPanel />
        <LiveMonitor />
      </div>
      <QueueTable />
    </div>
  )
}
