import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { Sidebar } from './components/Sidebar'
import type { View } from './components/Sidebar'
import { SolutionPanel } from './components/SolutionPanel'
import { HistoryPage } from './pages/History'
import { ModulePage } from './pages/ModulePage'
import { Overview } from './pages/Overview'
import { SettingsPage } from './pages/Settings'
import { StoreProvider, useStore } from './store'

function Shell() {
  const [view, setView] = useState<View>('overview')
  const { backendUp, toast } = useStore()
  return (
    <div className="app-bg flex h-full">
      <Sidebar view={view} onView={setView} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        {!backendUp && (
          <div className="border-b border-warn/25 bg-warn/10 px-6 py-2 text-xs text-warn">
            Backend not reachable. Start it with <span className="font-mono">./run.sh</span> — the dashboard reconnects automatically.
          </div>
        )}
        <AnimatePresence mode="wait">
          <motion.div key={view} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}
            className="mx-auto max-w-[1280px] px-6 py-7 max-md:px-4">
            {view === 'overview' && <Overview />}
            {(view === 'codekata' || view === 'sqlkata' || view === 'webkata' || view === 'fixthecode') && <ModulePage module={view} />}
            {view === 'history' && <HistoryPage />}
            {view === 'settings' && <SettingsPage />}
          </motion.div>
        </AnimatePresence>
      </main>
      <SolutionPanel />
      <AnimatePresence>
        {toast && (
          <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 12 }}
            className={`fixed bottom-5 left-1/2 z-50 max-w-[min(560px,90vw)] -translate-x-1/2 rounded-xl border px-4 py-2.5 text-[13px] shadow-2xl backdrop-blur-xl
              ${toast.kind === 'error' ? 'border-bad/40 bg-[#2a1416]/90 text-[#ffb4b4]' : 'border-line-strong bg-ink-800/90 text-fg'}`}>
            {toast.text}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default function App() {
  return <StoreProvider><Shell /></StoreProvider>
}
