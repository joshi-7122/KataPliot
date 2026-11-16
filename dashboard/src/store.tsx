import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api, openEvents } from './api'
import type { Activity, LogRow, Prefs, QuestionRow, SessionRow, Status } from './types'

interface Store {
  backendUp: boolean
  status: Status | null
  session: SessionRow | null
  sessionError: string | null
  questions: QuestionRow[]
  logs: LogRow[]
  activity: Activity | null
  prefs: Prefs | null
  selectedId: number | null
  solutionTick: number
  toast: { kind: 'error' | 'info'; text: string } | null
  select: (id: number | null) => void
  notify: (kind: 'error' | 'info', text: string) => void
  refresh: () => Promise<void>
  savePrefs: (p: Partial<Prefs>) => Promise<void>
  run: <T>(fn: () => Promise<T>) => Promise<T | undefined>
}

const Ctx = createContext<Store | null>(null)
export const useStore = () => {
  const s = useContext(Ctx)
  if (!s) throw new Error('store missing')
  return s
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [backendUp, setBackendUp] = useState(false)
  const [status, setStatus] = useState<Status | null>(null)
  const [session, setSession] = useState<SessionRow | null>(null)
  const [sessionError, setSessionError] = useState<string | null>(null)
  const [questions, setQuestions] = useState<QuestionRow[]>([])
  const [logs, setLogs] = useState<LogRow[]>([])
  const [activity, setActivity] = useState<Activity | null>(null)
  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [solutionTick, setSolutionTick] = useState(0)
  const [toast, setToast] = useState<Store['toast']>(null)
  const sessionIdRef = useRef<number | null>(null)
  const toastTimer = useRef<number | undefined>(undefined)

  const notify = useCallback((kind: 'error' | 'info', text: string) => {
    setToast({ kind, text })
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(null), 6000)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [st, cur, pf] = await Promise.all([api.status(), api.current(), api.prefs()])
      setStatus(st); setPrefs(pf)
      setSession(cur.session); setQuestions(cur.questions); setLogs(cur.logs)
      setSessionError(cur.session?.error ?? null)
      sessionIdRef.current = cur.session?.id ?? null
      setBackendUp(true)
    } catch {
      setBackendUp(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const poll = window.setInterval(() => { api.status().then((s) => { setStatus(s); setBackendUp(true) }).catch(() => setBackendUp(false)) }, 8000)
    const dispose = openEvents((e) => {
      const d = e.data
      switch (e.type) {
        case 'hello':
        case 'extension':
          setStatus((s) => (s ? { ...s, extension: { connected: !!(d.connected ?? d.extension?.connected), info: d.info ?? s.extension.info } } : s))
          break
        case 'session':
          setStatus((s) => (s ? { ...s, agent: { session_id: d.session_id, state: d.state, current_question_id: d.current_question_id, config: d.config } } : s))
          setSessionError(d.error ?? null)
          if (d.session_id !== sessionIdRef.current || ['completed', 'stopped', 'failed'].includes(d.state)) refresh()
          break
        case 'question':
          if (d.session_id === sessionIdRef.current || sessionIdRef.current === null) {
            setQuestions((qs) => {
              const i = qs.findIndex((q) => q.id === d.id)
              const next = i >= 0 ? qs.map((q) => (q.id === d.id ? (d as QuestionRow) : q)) : [...qs, d as QuestionRow]
              return next.sort((a, b) => a.position - b.position)
            })
          } else refresh()
          break
        case 'activity':
          setActivity({ question_id: d.question_id, step: d.step, message: d.message })
          if (d.question_id) setQuestions((qs) => qs.map((q) => (q.id === d.question_id ? { ...q, activity: d.message } : q)))
          break
        case 'log':
          setLogs((l) => [...l.slice(-299), d as LogRow])
          break
        case 'solution':
          setSolutionTick((t) => t + 1)
          break
      }
    }, (up) => { if (up) refresh(); else setBackendUp(false) })
    return () => { window.clearInterval(poll); dispose() }
  }, [refresh])

  const savePrefs = useCallback(async (p: Partial<Prefs>) => {
    try { setPrefs(await api.savePrefs(p)) } catch (e) { notify('error', (e as Error).message) }
  }, [notify])

  const run = useCallback(async <T,>(fn: () => Promise<T>) => {
    try { return await fn() } catch (e) { notify('error', (e as Error).message); return undefined }
  }, [notify])

  const value = useMemo<Store>(() => ({
    backendUp, status, session, sessionError, questions, logs, activity, prefs, selectedId, solutionTick, toast,
    select: setSelectedId, notify, refresh, savePrefs, run,
  }), [backendUp, status, session, sessionError, questions, logs, activity, prefs, selectedId, solutionTick, toast, notify, refresh, savePrefs, run])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
