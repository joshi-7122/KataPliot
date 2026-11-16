import { AnimatePresence, motion } from 'framer-motion'
import { Check, CircleAlert, CircleCheck, CircleDashed, CircleX, Copy, ExternalLink, FileInput, RotateCcw, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import type { Check as CheckT, QuestionDetail } from '../types'
import { StatusPill, timeAgo } from './ui'

const VERDICT = {
  passed: { label: 'Validated on samples', color: '#4cc38a' },
  failed: { label: 'Validation failing', color: '#ef6b6b' },
  inconclusive: { label: 'Not fully validated', color: '#e5a54b' },
}

function CheckIcon({ s }: { s: CheckT['status'] }) {
  if (s === 'pass') return <CircleCheck size={14} className="text-ok" />
  if (s === 'fail') return <CircleX size={14} className="text-bad" />
  if (s === 'warn') return <CircleAlert size={14} className="text-warn" />
  return <CircleDashed size={14} className="text-fg-mute" />
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="label mb-1.5">{title}</div>
      <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-fg-dim">{children}</div>
    </div>
  )
}

function Code({ code }: { code: string }) {
  const lines = code.replace(/\n$/, '').split('\n')
  return (
    <div className="overflow-auto rounded-lg border hairline bg-black/40 py-3 font-mono text-[12px] leading-[1.65]" style={{ maxHeight: 460 }}>
      <table className="border-collapse">
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td className="select-none px-3 text-right align-top text-fg-mute/60">{i + 1}</td>
              <td className="whitespace-pre pr-4 text-[#d6d9e4]">{l || ' '}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function SolutionPanel() {
  const { selectedId, select, solutionTick, questions, run, notify } = useStore()
  const [detail, setDetail] = useState<QuestionDetail | null>(null)
  const [tab, setTab] = useState<'solution' | 'problem' | 'validation' | 'backups'>('solution')
  const [copied, setCopied] = useState(false)
  const [inserting, setInserting] = useState(false)
  const liveRow = questions.find((q) => q.id === selectedId)

  useEffect(() => {
    if (selectedId == null) { setDetail(null); return }
    let dead = false
    api.question(selectedId).then((d) => { if (!dead) setDetail(d) }).catch(() => { if (!dead) setDetail(null) })
    return () => { dead = true }
  }, [selectedId, solutionTick, liveRow?.status, liveRow?.inserted])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') select(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [select])

  const sol = detail?.solution
  const q = detail?.row.question
  const verdict = sol ? VERDICT[sol.validation.verdict] : null

  const copy = async () => {
    if (!sol) return
    await navigator.clipboard.writeText(sol.code)
    setCopied(true); setTimeout(() => setCopied(false), 1500)
  }
  const insert = async () => {
    if (!detail) return
    setInserting(true)
    const r = await run(() => api.insert(detail.row.id))
    setInserting(false)
    if (r) notify(r.inserted ? 'info' : 'error', r.inserted ? 'Inserted into the GUVI editor. Review it there, then run/submit yourself.' : 'Could not verify the insertion — check the GUVI tab.')
  }

  return (
    <AnimatePresence>
      {selectedId != null && (
        <>
          <motion.div className="fixed inset-0 z-30 bg-black/45 backdrop-blur-[2px]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => select(null)} />
          <motion.aside className="fixed inset-y-0 right-0 z-40 flex w-[min(760px,100vw)] flex-col border-l hairline bg-ink-900/95 shadow-2xl backdrop-blur-2xl"
            initial={{ x: 40, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 40, opacity: 0 }} transition={{ type: 'spring', stiffness: 380, damping: 36 }}>
            <header className="flex items-start gap-3 border-b hairline px-5 py-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  {(liveRow ?? detail?.row) && <StatusPill status={(liveRow ?? detail!.row).status} />}
                  {verdict && <span className="text-[11.5px]" style={{ color: verdict.color }}>{verdict.label}</span>}
                </div>
                <h2 className="mt-1.5 truncate text-[16px] font-semibold tracking-tight">{detail?.row.title ?? liveRow?.title ?? 'Loading…'}</h2>
                {detail?.row.url && (
                  <a href={detail.row.url} target="_blank" rel="noreferrer" className="mt-0.5 inline-flex max-w-full items-center gap-1 truncate text-xs text-fg-mute hover:text-accent">
                    <ExternalLink size={11} className="shrink-0" /><span className="truncate">{detail.row.url}</span>
                  </a>
                )}
              </div>
              <button className="btn !h-8 !w-8 !p-0" onClick={() => select(null)} aria-label="Close"><X size={15} /></button>
            </header>

            <div className="flex items-center gap-1 border-b hairline px-4">
              {(['solution', 'problem', 'validation', 'backups'] as const).map((t) => (
                <button key={t} onClick={() => setTab(t)}
                  className={`relative h-10 px-3 text-[12.5px] capitalize transition-colors ${tab === t ? 'text-fg' : 'text-fg-mute hover:text-fg-dim'}`}>
                  {t === 'problem' ? 'Problem statement' : t}
                  {tab === t && <motion.span layoutId="tab-underline" className="absolute inset-x-2 -bottom-px h-[2px] rounded-full bg-gradient-to-r from-accent to-accent-2" />}
                </button>
              ))}
              <div className="ml-auto flex items-center gap-2 py-1.5">
                <button className="btn" onClick={copy} disabled={!sol}>{copied ? <Check size={13} className="text-ok" /> : <Copy size={13} />}{copied ? 'Copied' : 'Copy Code'}</button>
                <button className="btn btn-primary" onClick={insert} disabled={!sol || inserting}><FileInput size={13} />{inserting ? 'Inserting…' : 'Insert Into GUVI'}</button>
              </div>
            </div>

            <div className="flex-1 space-y-5 overflow-y-auto p-5">
              {tab === 'solution' && (sol ? (
                <>
                  {sol.analysis.language_note && <div className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn">{sol.analysis.language_note}</div>}
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <Chip k="Language" v={sol.language} /><Chip k="Time" v={sol.analysis.time_complexity} /><Chip k="Space" v={sol.analysis.space_complexity} /><Chip k="Attempt" v={String(sol.attempt)} />
                  </div>
                  <Code code={sol.code} />
                  <Section title="Approach">{sol.analysis.approach}</Section>
                  <Section title="Algorithm explanation">{sol.analysis.algorithm_explanation}</Section>
                  {sol.analysis.edge_cases.length > 0 && (
                    <Section title="Edge cases considered"><ul className="list-disc space-y-0.5 pl-4">{sol.analysis.edge_cases.map((e, i) => <li key={i}>{e}</li>)}</ul></Section>
                  )}
                  {sol.references.length > 0 && (
                    <div>
                      <div className="label mb-1.5">Web references</div>
                      <div className="space-y-1.5">
                        {sol.references.slice(0, 5).map((r, i) => (
                          <a key={i} href={r.url} target="_blank" rel="noreferrer" className="block rounded-lg border hairline bg-white/[0.02] px-3 py-2 transition-colors hover:border-line-strong">
                            <div className="flex items-center gap-2 text-[12.5px] text-fg">
                              <span className="truncate">{r.title}</span>
                              <span className={`ml-auto shrink-0 rounded-full px-1.5 py-px text-[10.5px] ${r.equivalent ? 'bg-ok/15 text-ok' : 'bg-white/[0.06] text-fg-mute'}`}>
                                {r.equivalent ? `Equivalent · ${Math.round(r.confidence * 100)}%` : 'Not equivalent'}
                              </span>
                            </div>
                            <div className="mt-0.5 text-[11.5px] text-fg-mute">{r.notes}</div>
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : <Pending text={detail?.row.error ?? 'No solution yet — the agent has not finished this question.'} />)}

              {tab === 'problem' && (q ? (
                <>
                  <Section title="Problem statement">{q.statement || q.raw_text}</Section>
                  {q.input_format && <Section title="Input format">{q.input_format}</Section>}
                  {q.output_format && <Section title="Output format">{q.output_format}</Section>}
                  {q.constraints && <Section title="Constraints">{q.constraints}</Section>}
                  {q.samples.map((s, i) => (
                    <div key={i} className="grid grid-cols-2 gap-3">
                      <div><div className="label mb-1.5">Sample input {i + 1}</div><pre className="overflow-auto rounded-lg border hairline bg-black/30 p-2.5 font-mono text-xs text-fg-dim">{s.input}</pre></div>
                      <div><div className="label mb-1.5">Sample output {i + 1}</div><pre className="overflow-auto rounded-lg border hairline bg-black/30 p-2.5 font-mono text-xs text-fg-dim">{s.output}</pre></div>
                    </div>
                  ))}
                  {q.extraction_warnings.length > 0 && <Section title="Extraction notes">{q.extraction_warnings.join('\n')}</Section>}
                </>
              ) : <Pending text="The question has not been extracted yet." />)}

              {tab === 'validation' && (sol ? (
                <>
                  <div className="rounded-lg border hairline bg-white/[0.02] px-3 py-2.5 text-xs text-fg-dim">
                    Checked locally in the <span className="font-mono text-fg">{sol.validation.sandbox}</span> sandbox. This is evidence, not a verdict — only GUVI can accept a submission.
                  </div>
                  <div className="space-y-1.5">
                    {sol.validation.checks.map((c, i) => (
                      <div key={i} className="rounded-lg border hairline bg-white/[0.02] px-3 py-2">
                        <div className="flex items-center gap-2 text-[12.5px] text-fg"><CheckIcon s={c.status} />{c.name}</div>
                        {c.detail && <pre className="mt-1 whitespace-pre-wrap pl-[22px] font-mono text-[11.5px] leading-relaxed text-fg-mute">{c.detail}</pre>}
                      </div>
                    ))}
                  </div>
                </>
              ) : <Pending text="Nothing has been validated yet." />)}

              {tab === 'backups' && (detail && detail.backups.length > 0 ? (
                <div className="space-y-3">
                  <div className="text-xs text-fg-mute">Copies of the GUVI editor taken before KataPilot changed it. Restoring writes the copy back into the editor.</div>
                  {detail.backups.map((b) => (
                    <div key={b.id} className="rounded-lg border hairline bg-white/[0.02]">
                      <div className="flex items-center gap-2 border-b hairline px-3 py-2 text-xs text-fg-dim">
                        <span className="font-mono">{b.reason.replaceAll('_', ' ')}</span><span className="text-fg-mute">· {timeAgo(b.created_at)}</span>
                        <button className="btn ml-auto !h-7" onClick={async () => { const r = await run(() => api.restoreBackup(b.id)); if (r) notify('info', 'Backup restored into the editor.') }}>
                          <RotateCcw size={12} />Restore
                        </button>
                      </div>
                      <pre className="max-h-40 overflow-auto p-3 font-mono text-[11.5px] text-fg-mute">{b.content || '(editor was empty)'}</pre>
                    </div>
                  ))}
                </div>
              ) : <Pending text="No editor backups for this question." />)}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}

function Chip({ k, v }: { k: string; v: string }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-md border hairline bg-white/[0.03] px-2 py-1">
      <span className="text-fg-mute">{k}</span><span className="truncate font-mono text-fg">{v}</span>
    </span>
  )
}
function Pending({ text }: { text: string }) {
  return <div className="grid place-items-center py-16 text-center text-[13px] text-fg-mute">{text}</div>
}
