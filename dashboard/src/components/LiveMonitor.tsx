import { AnimatePresence, motion } from 'framer-motion'
import { Activity as ActivityIcon, Check } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useStore } from '../store'
import { Empty, Panel } from './ui'

const STEPS: { id: string; label: string; match: string[] }[] = [
  { id: 'detect', label: 'Detect page', match: ['detect', 'discover', 'queue', 'open'] },
  { id: 'extract', label: 'Extract', match: ['extract', 'language'] },
  { id: 'research', label: 'Web search', match: ['research'] },
  { id: 'generate', label: 'Generate', match: ['generate', 'repair'] },
  { id: 'validate', label: 'Validate', match: ['validate'] },
  { id: 'insert', label: 'Insert', match: ['insert'] },
  { id: 'review', label: 'Review', match: ['review', 'done'] },
]

export function LiveMonitor() {
  const { activity, logs, status, questions } = useStore()
  const state = status?.agent.state ?? 'idle'
  const live = state === 'running' || state === 'waiting_review' || state === 'paused'
  const idx = live && activity?.step ? STEPS.findIndex((s) => s.match.includes(activity.step!)) : -1
  const current = questions.find((q) => q.id === status?.agent.current_question_id)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }) }, [logs.length])

  return (
    <Panel title="Live execution" icon={<ActivityIcon size={14} />}
      action={current && <span className="max-w-[55%] truncate text-[11.5px] text-fg-mute">{current.title}</span>}>
      <div className="grid grid-cols-7 gap-1 px-4 pt-4">
        {STEPS.map((s, i) => {
          const done = idx > i, on = idx === i
          return (
            <div key={s.id} className="min-w-0">
              <div className="relative h-[3px] overflow-hidden rounded-full bg-white/[0.06]">
                {(done || on) && (
                  <motion.div layout className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-accent to-accent-2"
                    initial={{ width: 0 }} animate={{ width: done ? '100%' : '55%' }} transition={{ duration: 0.5, ease: 'easeOut' }} />
                )}
              </div>
              <div className={`mt-2 flex items-center gap-1 text-[10.5px] leading-tight ${on ? 'text-fg' : done ? 'text-fg-dim' : 'text-fg-mute'}`}>
                {done && <Check size={10} className="shrink-0 text-ok" />}
                <span className="truncate max-lg:hidden">{s.label}</span>
              </div>
            </div>
          )
        })}
      </div>
      <div className="px-4 pb-2 pt-3">
        <AnimatePresence mode="wait">
          <motion.div key={activity?.message ?? 'idle'} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18 }} className="flex items-center gap-2 text-[13px]">
            {live && state === 'running' && <span className="pulse h-2 w-2 shrink-0 rounded-full bg-accent" />}
            <span className={live ? 'text-fg' : 'text-fg-mute'}>
              {live ? activity?.message ?? 'Working…' : state === 'completed' ? 'Session complete — review and submit in GUVI yourself.' : 'Idle. Start a session to see the agent work.'}
            </span>
          </motion.div>
        </AnimatePresence>
      </div>
      <div ref={box} className="mx-4 mb-4 h-44 overflow-y-auto rounded-lg border hairline bg-black/25 p-2.5 font-mono text-[11.5px] leading-relaxed">
        {logs.length === 0 ? <Empty title="No activity yet" /> : logs.map((l) => (
          <div key={l.id} className="flex gap-2">
            <span className="shrink-0 text-fg-mute">{new Date(l.created_at * 1000).toLocaleTimeString([], { hour12: false })}</span>
            <span className={l.level === 'error' ? 'text-bad' : l.level === 'warn' ? 'text-warn' : 'text-fg-dim'}>{l.message}</span>
          </div>
        ))}
      </div>
    </Panel>
  )
}
