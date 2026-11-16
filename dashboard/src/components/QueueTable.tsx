import { motion } from 'framer-motion'
import { CornerDownRight, ListOrdered } from 'lucide-react'
import { useStore } from '../store'
import type { QuestionRow } from '../types'
import { MODULES } from '../types'
import { Empty, Panel, StatusPill } from './ui'

const moduleLabel = (m: string) => MODULES.find((x) => x.id === m)?.label ?? m

export function QueueTable({ rows, title = 'Question queue', empty }: { rows?: QuestionRow[]; title?: string; empty?: string }) {
  const { questions, selectedId, select } = useStore()
  const list = rows ?? questions
  return (
    <Panel title={title} icon={<ListOrdered size={14} />} action={<span className="text-[11.5px] text-fg-mute">{list.length} item{list.length === 1 ? '' : 's'}</span>}>
      {list.length === 0 ? (
        <Empty title="Queue is empty" hint={empty ?? 'Open a GUVI question list in Chrome and press Start Session.'} />
      ) : (
        <div className="max-h-[420px] overflow-y-auto">
          <table className="w-full border-collapse text-left">
            <thead className="sticky top-0 z-10 bg-ink-850/95 backdrop-blur">
              <tr className="label [&>th]:px-4 [&>th]:py-2 [&>th]:font-medium">
                <th>Question</th><th className="max-lg:hidden">Module</th><th className="max-xl:hidden">Difficulty</th><th>Status</th><th className="max-md:hidden">Solution</th>
              </tr>
            </thead>
            <tbody>
              {list.map((q) => (
                <motion.tr key={q.id} layout="position" onClick={() => select(q.id)}
                  className={`cursor-pointer border-t hairline transition-colors hover:bg-white/[0.035] ${selectedId === q.id ? 'bg-accent/[0.07]' : ''}`}>
                  <td className="max-w-0 px-4 py-2.5">
                    <div className="truncate text-[13px] text-fg">{q.title}</div>
                    <div className="mt-0.5 flex items-center gap-1 truncate text-[11.5px] text-fg-mute">
                      <CornerDownRight size={10} className="shrink-0" />
                      <span className="truncate">{q.error ?? q.activity ?? '—'}</span>
                    </div>
                  </td>
                  <td className="px-4 text-xs text-fg-dim max-lg:hidden">{moduleLabel(q.module)}</td>
                  <td className="px-4 text-xs capitalize text-fg-dim max-xl:hidden">{q.difficulty ?? '—'}</td>
                  <td className="px-4"><StatusPill status={q.status} /></td>
                  <td className="px-4 text-xs text-fg-dim max-md:hidden">
                    {q.inserted ? 'Inserted in GUVI' : q.status === 'code_ready' || q.status === 'needs_review' ? 'Prepared' : '—'}
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
