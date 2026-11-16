import type { Prefs, QuestionDetail, QuestionRow, SessionConfig, SessionRow, Status, AgentSnapshot, LogRow } from './types'

let token: string | null = null

export class ApiError extends Error {}

async function ensureToken(): Promise<string> {
  if (token) return token
  const r = await fetch('/api/bootstrap')
  if (!r.ok) throw new ApiError('Backend refused pairing')
  token = (await r.json()).token as string
  return token
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const t = await ensureToken()
  let r: Response
  try {
    r = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', 'X-KataPilot-Token': t, ...(init.headers || {}) } })
  } catch {
    throw new ApiError('Backend is not reachable. Is it running?')
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try { detail = (await r.json()).detail ?? detail } catch { /* not json */ }
    throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return r.json() as Promise<T>
}

const post = <T,>(path: string, body?: unknown) => req<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  status: () => req<Status>('/api/status'),
  current: () => req<{ session: SessionRow | null; questions: QuestionRow[]; logs: LogRow[]; agent: AgentSnapshot }>('/api/session/current'),
  prefs: () => req<Prefs>('/api/prefs'),
  savePrefs: (p: Partial<Prefs>) => req<Prefs>('/api/prefs', { method: 'PUT', body: JSON.stringify(p) }),
  start: (cfg: SessionConfig) => post<{ session_id: number }>('/api/session/start', cfg),
  control: (action: 'pause' | 'resume' | 'skip' | 'stop') => post<AgentSnapshot>(`/api/session/${action}`),
  resumeSession: (sid: number) => post<AgentSnapshot>(`/api/sessions/${sid}/resume`),
  sessions: () => req<SessionRow[]>('/api/sessions'),
  history: (module?: string) => req<QuestionRow[]>(`/api/history${module ? `?module=${module}` : ''}`),
  question: (id: number) => req<QuestionDetail>(`/api/questions/${id}`),
  insert: (id: number) => post<{ inserted: boolean }>(`/api/questions/${id}/insert`),
  restoreBackup: (id: number) => post<{ verified: boolean }>(`/api/backups/${id}/restore`),
  detect: () => post<Record<string, unknown>>('/api/browser/detect'),
  inspect: () => post<Record<string, unknown>>('/api/browser/inspect'),
}

export interface WsEvent { type: string; data: Record<string, any>; ts?: number }

/** Live event stream with automatic reconnect. Returns a disposer. */
export function openEvents(onEvent: (e: WsEvent) => void, onConn: (up: boolean) => void): () => void {
  let ws: WebSocket | null = null
  let closed = false
  let delay = 800
  let ping: number | undefined
  const connect = async () => {
    if (closed) return
    try {
      const t = await ensureToken()
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/dashboard?token=${encodeURIComponent(t)}`)
    } catch {
      setTimeout(connect, delay); delay = Math.min(delay * 2, 10000); return
    }
    ws.onopen = () => { delay = 800; onConn(true); ping = window.setInterval(() => ws?.readyState === 1 && ws.send('ping'), 25000) }
    ws.onmessage = (ev) => { try { onEvent(JSON.parse(ev.data)) } catch { /* ignore */ } }
    ws.onclose = (ev) => {
      window.clearInterval(ping); onConn(false)
      if (ev.code === 4401) token = null
      if (!closed) { setTimeout(connect, delay); delay = Math.min(delay * 2, 10000) }
    }
  }
  connect()
  return () => { closed = true; window.clearInterval(ping); ws?.close() }
}
