import { lazy, Suspense, useState, useMemo, useCallback } from 'react'
import { Routes, Route } from 'react-router-dom'
import { ThemeProvider, CssBaseline } from '@mui/material'
import { createAppTheme } from './theme'
import type { ExecutionMode } from './api/client'
import AppLayout from './layout/AppLayout'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const Status = lazy(() => import('./pages/Status'))
const Demo = lazy(() => import('./pages/Demo'))
const Interactive = lazy(() => import('./pages/Interactive'))
const Memory = lazy(() => import('./pages/Memory'))
const Metrics = lazy(() => import('./pages/Metrics'))
const Benchmark = lazy(() => import('./pages/Benchmark'))
const Preferences = lazy(() => import('./pages/Preferences'))
const Sessions = lazy(() => import('./pages/Sessions'))

function loadMode(): ExecutionMode {
  const stored = localStorage.getItem('sage-mode')
  if (stored === 'offline' || stored === 'qwen' || stored === 'cloud') return stored
  return 'offline'
}

function loadDarkMode(): boolean {
  return localStorage.getItem('sage-dark') === 'true'
}

export default function App() {
  const [mode, setModeRaw] = useState<ExecutionMode>(loadMode)
  const [darkMode, setDarkModeRaw] = useState(loadDarkMode)

  const setMode = useCallback((m: ExecutionMode) => {
    setModeRaw(m)
    localStorage.setItem('sage-mode', m)
  }, [])

  const setDarkMode = useCallback((d: boolean) => {
    setDarkModeRaw(d)
    localStorage.setItem('sage-dark', String(d))
  }, [])

  const theme = useMemo(() => createAppTheme(darkMode ? 'dark' : 'light'), [darkMode])

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Suspense fallback={null}>
        <Routes>
          <Route element={<AppLayout mode={mode} onModeChange={setMode} darkMode={darkMode} onDarkModeChange={setDarkMode} />}>
            <Route path="/" element={<Dashboard mode={mode} />} />
            <Route path="/status" element={<Status mode={mode} />} />
            <Route path="/demo" element={<Demo mode={mode} />} />
            <Route path="/interactive" element={<Interactive mode={mode} />} />
            <Route path="/memory" element={<Memory mode={mode} />} />
            <Route path="/metrics" element={<Metrics mode={mode} />} />
            <Route path="/benchmark" element={<Benchmark mode={mode} />} />
            <Route path="/preferences" element={<Preferences mode={mode} />} />
            <Route path="/sessions" element={<Sessions mode={mode} />} />
          </Route>
        </Routes>
      </Suspense>
    </ThemeProvider>
  )
}
