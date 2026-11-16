export type Module = 'codekata' | 'sqlkata' | 'webkata' | 'fixthecode'
export type QStatus =
  | 'pending' | 'processing' | 'researching' | 'generating' | 'validating' | 'inserting'
  | 'code_ready' | 'needs_review' | 'failed' | 'skipped'
export type SessionState =
  | 'idle' | 'running' | 'paused' | 'waiting_review' | 'stopping' | 'stopped' | 'completed' | 'interrupted' | 'failed'

export interface SessionConfig {
  module: Module
  language: string
  review_mode: 'prepare_only' | 'insert_and_continue' | 'review_each' | 'auto_submit'
  discovery: 'listing' | 'current' | 'urls'
  urls: string[]
  max_questions: number
  skip_already_prepared: boolean
  skip_solved_on_guvi?: boolean
  use_web_search: boolean
}

export interface AgentSnapshot {
  session_id: number | null
  state: SessionState
  current_question_id: number | null
  config: SessionConfig | null
}

export interface Status {
  extension: { connected: boolean; info: Record<string, unknown> }
  agent: AgentSnapshot
  llm: {
    provider: string
    model?: string
    full_model?: string
    effort?: string
    error?: string
    keys?: number
    active_key?: number
    active_label?: string
    keys_available?: number
    chain?: { label: string; model: string; provider: string; available: boolean; note?: string }[]
  }
  search: { provider: string; configured: boolean }
  sandbox: { backend: string }
  stats: { total: number; ready: number; review: number; failed: number; pending: number; inserted: number }
}

export interface QuestionRow {
  id: number
  session_id: number
  position: number
  url: string | null
  title: string
  module: Module
  difficulty: string | null
  status: QStatus
  activity: string | null
  error: string | null
  inserted: number
  updated_at: number
}

export interface SessionRow {
  id: number
  module: Module
  state: SessionState
  error: string | null
  created_at: number
  updated_at: number
  total?: number
  ready?: number
}

export interface LogRow { id: number; level: string; step: string | null; message: string; created_at: number; question_id: number | null }
export interface Activity { question_id: number | null; step: string | null; message: string }

export interface Check { name: string; status: 'pass' | 'fail' | 'warn' | 'skipped'; detail: string }
export interface Reference { title: string; url: string; equivalent: boolean; confidence: number; notes: string }
export interface Solution {
  id: number
  attempt: number
  language: string
  code: string
  analysis: {
    understanding: string; approach: string; algorithm_explanation: string
    time_complexity: string; space_complexity: string; edge_cases: string[]; language_note?: string | null
  }
  references: Reference[]
  validation: { verdict: 'passed' | 'failed' | 'inconclusive'; checks: Check[]; sandbox: string }
}
export interface QuestionDetail {
  row: QuestionRow & {
    question: null | {
      title: string; statement: string; input_format: string; output_format: string; constraints: string
      samples: { input: string; output: string }[]; language: string | null; raw_text: string; extraction_warnings: string[]
    }
  }
  solution: Solution | null
  backups: { id: number; reason: string; content: string; created_at: number }[]
}

export interface Prefs {
  preferred_language: string
  review_mode: SessionConfig['review_mode']
  max_questions: number
  use_web_search: boolean
  selector_overrides: Record<string, { problemSelector?: string; titleSelector?: string; editorSelector?: string }>
}

export const MODULES: { id: Module; label: string; blurb: string }[] = [
  { id: 'codekata', label: 'CodeKata', blurb: 'Programming problems in C, C++, Java and Python.' },
  { id: 'sqlkata', label: 'SQLKata', blurb: 'Queries validated against the sample tables on the page.' },
  { id: 'webkata', label: 'WebKata', blurb: 'HTML, CSS and JavaScript challenges.' },
  { id: 'fixthecode', label: 'FixTheCode', blurb: 'Finds bugs in the provided code with minimal edits.' },
]
