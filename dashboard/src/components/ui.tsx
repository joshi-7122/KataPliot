import type { ReactNode } from 'react'
import type { QStatus, SessionState } from '../types'

const STATUS: Record<QStatus, { label: string; color: string; live?: boolean }> = {
  pending: { label: 'Pending', color: '#6c7080' },
  processing: { label: 'Processing', color: '#5b8cff', live: true },
  researching: { label: 'Researching', color: '#58b6e8', live: true },
  generating: { label: 'Generating', color: '#8a6cf7', live: true },
  validating: { label: 'Validating', color: '#c18cf5', live: true },
  inserting: { label: 'Inserting', color: '#5b8cff', live: true },
  code_ready: { label: 'Code Ready', color: '#4cc38a' },
  needs_review: { label: 'Needs Review', color: '#e5a54b' },
  failed: { label: 'Failed', color: '#ef6b6b' },
  skipped: { label: 'Skipped', color: '#6c7080' },
}

export function StatusPill({ status }: { status: QStatus }) {
  const s = STATUS[status] ?? STATUS.pending
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
      style={{ color: s.color, borderColor: `${s.color}40`, background: `${s.color}14` }}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.live ? 'pulse' : ''}`} style={{ background: s.color }} />
      {s.label}
    </span>
  )
}

const SESSION: Record<SessionState, { label: string; color: string }> = {
  idle: { label: 'Idle', color: '#6c7080' },
  running: { label: 'Running', color: '#5b8cff' },
  paused: { label: 'Paused', color: '#e5a54b' },
  waiting_review: { label: 'Waiting for review', color: '#c18cf5' },
  stopping: { label: 'Stopping', color: '#e5a54b' },
  stopped: { label: 'Stopped', color: '#6c7080' },
  completed: { label: 'Completed', color: '#4cc38a' },
  interrupted: { label: 'Interrupted', color: '#e5a54b' },
  failed: { label: 'Failed', color: '#ef6b6b' },
}
export const sessionMeta = (s: SessionState) => SESSION[s] ?? SESSION.idle

export function Panel({ title, icon, action, children, className = '' }: {
  title?: string; icon?: ReactNode; action?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section className={`glass flex min-h-0 flex-col ${className}`}>
      {title && (
        <header className="flex h-11 shrink-0 items-center justify-between border-b hairline px-4">
          <div className="flex items-center gap-2 text-[12.5px] font-medium text-fg">
            <span className="text-fg-mute">{icon}</span>{title}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-1 px-6 py-10 text-center">
      <div className="text-[13px] text-fg-dim">{title}</div>
      {hint && <div className="max-w-sm text-xs text-fg-mute">{hint}</div>}
    </div>
  )
}

export const timeAgo = (ts: number) => {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return new Date(ts * 1000).toLocaleDateString()
}
