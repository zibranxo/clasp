import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { Routes, Route, NavLink } from 'react-router-dom'
import { Key, LayoutDashboard, Activity, ShieldAlert, Settings, Terminal, LogIn, GripVertical } from 'lucide-react'
import { api } from './api'
import DashboardPage from './pages/DashboardPage'
import KeysPage from './pages/KeysPage'
import RoutingLogPage from './pages/RoutingLogPage'
import SettingsPage from './pages/SettingsPage'
import AnomaliesPage from './pages/AnomaliesPage'
import RoutingRulesPage from './pages/RoutingRulesPage'
import { usePostHog } from 'posthog-js/react'
import { TourProvider } from './contexts/TourContext'
import TourOverlay from './components/TourOverlay'
import { useUpdateStore } from './contexts/UpdateStore'

export interface RoutingEvent {
  id: string
  timestamp: number
  provider: string
  latency_ms: number
  tokens_used: number
  success: boolean
  error_msg?: string
}

export function formatTokens(tokens: number): string {
  if (tokens >= 100000) {
    return (tokens / 1000000).toFixed(1) + 'M'
  }
  return tokens.toLocaleString()
}

interface EventContextValue {
  events: RoutingEvent[]
  addEvent: (e: RoutingEvent) => void
  clearEvents: () => void
}

const EventContext = createContext<EventContextValue>({
  events: [],
  addEvent: () => {},
  clearEvents: () => {},
})

export const useEvents = () => useContext(EventContext)

function EventProvider({ children }: { children: ReactNode }) {
  const [events, setEvents] = useState<RoutingEvent[]>([])
  const maxEvents = 200

  useEffect(() => {
    // Load initial routing events
    api.listRoutingEvents()
      .then((history) => {
        setEvents(history.slice(0, maxEvents))
      })
      .catch((err) => {
        console.error('Failed to load routing logs:', err)
      })

    // Set up SSE for live events
    const eventSource = api.getLiveStream()

    const handleEvent = (event: MessageEvent) => {
      if (event.data) {
        try {
          const routingEvent = JSON.parse(event.data) as RoutingEvent
          setEvents(prev => [routingEvent, ...prev].slice(0, maxEvents))
        } catch (err) {
          console.error('Failed to parse routing event:', err)
        }
      }
    }

    eventSource.addEventListener('message', handleEvent)

    return () => {
      eventSource.close()
    }
  }, [])

  const addEvent = (e: RoutingEvent) => setEvents(prev => [e, ...prev].slice(0, maxEvents))

  const clearEvents = () => {
    api.clearRoutingEvents()
      .then(() => setEvents([]))
      .catch((err) => console.error('Failed to clear routing logs:', err))
  }

  return (
    <EventContext.Provider value={{ events, addEvent, clearEvents }}>
      {children}
    </EventContext.Provider>
  )
}

function Sidebar() {
  const { events } = useEvents()
  const recent = events.filter(e => e.success).length
  const posthog = usePostHog()
  const { update } = useUpdateStore()

  return (
    <aside className="w-[280px] bg-neo-bg border-r-3 border-neo-dark flex flex-col z-10 relative">
      <div className="p-6 flex items-center gap-3 border-b-3 border-neo-dark bg-white relative">
        <div className="w-10 h-10 bg-neo-yellow flex items-center justify-center border-3 border-neo-dark shadow-neo-sm">
          <Key className="w-6 h-6 text-neo-dark" />
        </div>
        <h1 className="text-2xl font-display font-black text-neo-dark tracking-tight uppercase">CLASP</h1>
        {update && (
          <button className="absolute right-4 top-1/2 -translate-y-1/2 bg-neo-pink text-white text-[10px] font-black font-display uppercase px-2 py-1 border-2 border-neo-dark shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] hover:translate-y-px hover:shadow-none transition-all cursor-pointer">
            Update
          </button>
        )}
      </div>
      
      <nav className="flex-1 px-4 py-6 space-y-3 font-display uppercase font-bold text-sm">
        <NavLink to="/" data-tour="tour-step-0" className={({isActive}) =>
          `flex items-center gap-3 px-4 py-3 border-3 transition-all duration-200 ${isActive ? 'bg-neo-yellow border-neo-dark shadow-neo-sm translate-x-[-2px] translate-y-[-2px] text-neo-dark' : 'bg-transparent border-transparent text-neo-dark hover:bg-white hover:border-neo-dark hover:shadow-neo-sm'}`
        }>
          <LayoutDashboard className="w-5 h-5" /> Dashboard
        </NavLink>
        <NavLink to="/keys" data-tour="tour-step-1" className={({isActive}) =>
          `flex items-center gap-3 px-4 py-3 border-3 transition-all duration-200 ${isActive ? 'bg-neo-yellow border-neo-dark shadow-neo-sm translate-x-[-2px] translate-y-[-2px] text-neo-dark' : 'bg-transparent border-transparent text-neo-dark hover:bg-white hover:border-neo-dark hover:shadow-neo-sm'}`
        }>
          <Key className="w-5 h-5" /> Provider Keys
        </NavLink>
        <NavLink to="/logs" className={({isActive}) =>
          `flex items-center gap-3 px-4 py-3 border-3 transition-all duration-200 ${isActive ? 'bg-neo-yellow border-neo-dark shadow-neo-sm translate-x-[-2px] translate-y-[-2px] text-neo-dark' : 'bg-transparent border-transparent text-neo-dark hover:bg-white hover:border-neo-dark hover:shadow-neo-sm'}`
        }>
          <Activity className="w-5 h-5" /> Routing Logs
        </NavLink>
        <NavLink to="/priority" data-tour="tour-step-5" className={({isActive}) =>
          `flex items-center gap-3 px-4 py-3 border-3 transition-all duration-200 ${isActive ? 'bg-neo-yellow border-neo-dark shadow-neo-sm translate-x-[-2px] translate-y-[-2px] text-neo-dark' : 'bg-transparent border-transparent text-neo-dark hover:bg-white hover:border-neo-dark hover:shadow-neo-sm'}`
        }>
          <GripVertical className="w-5 h-5" /> Priority
        </NavLink>
        <NavLink to="/anomalies" className={({isActive}) =>
          `flex items-center gap-3 px-4 py-3 border-3 transition-all duration-200 ${isActive ? 'bg-neo-yellow border-neo-dark shadow-neo-sm translate-x-[-2px] translate-y-[-2px] text-neo-dark' : 'bg-transparent border-transparent text-neo-dark hover:bg-white hover:border-neo-dark hover:shadow-neo-sm'}`
        }>
          <ShieldAlert className="w-5 h-5" /> Anomalies
        </NavLink>
        <NavLink to="/settings" className={({isActive}) =>
          `flex items-center gap-3 px-4 py-3 border-3 transition-all duration-200 ${isActive ? 'bg-neo-yellow border-neo-dark shadow-neo-sm translate-x-[-2px] translate-y-[-2px] text-neo-dark' : 'bg-transparent border-transparent text-neo-dark hover:bg-white hover:border-neo-dark hover:shadow-neo-sm'}`
        }>
          <Settings className="w-5 h-5" /> Settings
        </NavLink>
        
        <div className="pt-4">
          <button onClick={async () => {
              localStorage.removeItem('auth_session')
              try { await api.clearSession() } catch (e) { console.error(e) }
              if (posthog) posthog.reset()
              window.location.reload()
            }}
            className="w-full flex items-center gap-3 px-4 py-3 border-3 border-transparent transition-all duration-200 text-neo-dark hover:bg-neo-pink hover:text-white hover:border-neo-dark hover:shadow-neo-sm cursor-pointer"
          >
            <LogIn className="w-5 h-5" /> Sign Out
          </button>
        </div>
      </nav>
      
      <div className="p-5 m-4 bg-neo-green border-3 border-neo-dark shadow-neo-md relative overflow-hidden group">
        <div className="flex items-center gap-3 mb-3">
          <div className="relative">
            <div className="w-3 h-3 bg-white border-2 border-neo-dark relative z-10" />
            <div className="w-3 h-3 bg-white absolute inset-0 animate-ping opacity-75" />
          </div>
          <span className="text-xs font-display font-black text-neo-dark tracking-wide uppercase">Proxy Active</span>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-neo-light font-mono mb-3 bg-neo-dark px-2 py-1.5 border border-neo-dark">
          <Terminal className="w-3 h-3 text-neo-light" />
          localhost:8787
        </div>
        <div className="text-sm text-neo-dark font-black font-display flex justify-between items-center">
          <span>{recent} REQ</span>
          <span className="text-xs uppercase bg-white border border-neo-dark px-1">Today</span>
        </div>
      </div>
    </aside>
  )
}

function App() {
  const [session, setSession] = useState<{session_id: string, user_id: string, email?: string} | null>(() => {
    const saved = localStorage.getItem('auth_session')
    return saved ? JSON.parse(saved) : null
  })
  const [isLocked, setIsLocked] = useState<boolean | 'loading'>('loading')
  const [lockReason, setLockReason] = useState<string>('')
  const [proxyPort, setProxyPort] = useState<number>(8787)

  const posthog = usePostHog()

  const { update, showToast, toastExiting, closeToast, installUpdate, isInstalling, installProgress, checkForUpdates } = useUpdateStore()

  useEffect(() => {
    checkForUpdates()
  }, [])

  useEffect(() => {
    async function init() {
      try {
        // Load session from FastAPI
        const sess = await api.getSession()
        if (sess) {
          setSession(sess)
          localStorage.setItem('auth_session', JSON.stringify(sess))
        }

        // Get proxy port
        const port = await api.getProxyPort()
        setProxyPort(port)

        // For web version, we don't have the same locking mechanism as Tauri
        // So we'll just mark as not locked
        setIsLocked(false)
      } catch (e) {
        console.error('Initialization error:', e)
        setIsLocked(false) // Fail open for web version
      }
    }

    init()
  }, [])

  useEffect(() => {
    if (session && posthog) {
      posthog.identify(session.user_id, { email: session.email || session.user_id })
    }
  }, [session, posthog])

  if (isLocked === 'loading') {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-neo-bg text-neo-dark font-body">
        <div className="w-12 h-12 border-4 border-neo-dark border-t-neo-pink rounded-full animate-spin"></div>
      </div>
    )
  }

  if (isLocked === true) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-neo-bg text-neo-dark font-body">
        <div className="p-10 bg-white border-6 border-neo-dark shadow-neo-xl flex flex-col items-center max-w-sm text-center">
          <div className="w-20 h-20 bg-neo-pink flex items-center justify-center border-3 border-neo-dark shadow-neo-sm mb-6">
            <ShieldAlert className="w-10 h-10 text-white" />
          </div>
          <h1 className="text-3xl font-black font-display tracking-tight uppercase mb-4 text-[#ff2a85]">Action Required</h1>
          <p className="text-sm font-bold text-neo-dark/90 mb-8 px-2">{lockReason}</p>
          <button
            onClick={() => api.openBrowser("https://keyking.ledgion.in/download")}
            className="flex items-center gap-2 bg-neo-yellow text-neo-dark px-6 py-4 border-3 border-neo-dark font-display font-black uppercase hover:-translate-y-1 hover:shadow-neo-md transition-all w-full justify-center cursor-pointer"
          >
            Get Latest Version
          </button>
        </div>
      </div>
    )
  }

  if (!session) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-neo-bg text-neo-dark font-body">
        <div className="p-10 bg-white border-6 border-neo-dark shadow-neo-xl flex flex-col items-center max-w-sm text-center">
          <div className="w-20 h-20 bg-neo-yellow flex items-center justify-center border-3 border-neo-dark shadow-neo-sm mb-6">
            <Key className="w-10 h-10 text-neo-dark" />
          </div>
          <h1 className="text-4xl font-black font-display tracking-tight uppercase mb-2">CLASP</h1>
          <p className="text-sm font-medium text-neo-dark/80 mb-10">CLASP is running. Access the dashboard below.</p>
          <button
            onClick={() => {
              // For web version, just create a simple session
              const simpleSession = {
                session_id: 'web-' + Math.random().toString(36).substr(2, 9),
                user_id: 'web-user',
                email: 'web@example.com'
              }
              api.saveSession(simpleSession)
              setSession(simpleSession)
              localStorage.setItem('auth_session', JSON.stringify(simpleSession))
            }}
            className="flex items-center gap-2 bg-neo-pink text-white px-6 py-4 border-3 border-neo-dark font-display font-black uppercase hover:-translate-y-1 hover:shadow-neo-md transition-all w-full justify-center cursor-pointer"
          >
            <LogIn className="w-6 h-6" />
            Access Dashboard
          </button>
        </div>
      </div>
    )
  }

  return (
    <EventProvider>
      <TourProvider>
        <div className="flex h-screen bg-neo-bg text-neo-dark font-body selection:bg-neo-yellow selection:text-neo-dark">
          <Sidebar />
          <main className="flex-1 p-8 overflow-auto relative">
            <div className="max-w-6xl mx-auto space-y-8 animate-fade-in pb-12">
              <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/keys" element={<KeysPage />} />
                <Route path="/logs" element={<RoutingLogPage />} />
                <Route path="/priority" element={<RoutingRulesPage />} />
                <Route path="/anomalies" element={<AnomaliesPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
            </div>
            {/* Update Toast */}
            {showToast && (
              <div className={`fixed bottom-8 right-8 z-50 ${toastExiting ? 'animate-slide-out-right' : 'animate-slide-in-right'}`}>
                <div className="bg-neo-yellow border-4 border-neo-dark p-6 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] flex items-center gap-6">
                  <div>
                    <h3 className="text-xl font-black font-display uppercase tracking-tight text-neo-dark">Update v{update?.version}</h3>
                    {isInstalling ? (
                      <div className="w-full bg-white border-2 border-neo-dark h-4 mt-2 relative overflow-hidden">
                        <div className="bg-neo-green h-full transition-all duration-300" style={{ width: `${installProgress}%` }}></div>
                        <span className="absolute inset-0 flex items-center justify-center text-[10px] font-bold z-10">{installProgress}%</span>
                      </div>
                    ) : (
                      <p className="font-bold text-neo-dark/80 text-sm">A new version is ready to install.</p>
                    )}
                  </div>
                  {!isInstalling && (
                    <button onClick={installUpdate} className="bg-neo-pink text-white font-black font-display uppercase px-4 py-2 border-3 border-neo-dark shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:translate-y-1 hover:shadow-none transition-all cursor-pointer">
                      Update Now!!
                    </button>
                  )}
                  {!isInstalling && (
                    <button onClick={closeToast} className="text-neo-dark hover:text-neo-pink absolute top-2 right-2 font-black cursor-pointer">
                      X
                    </button>
                  )}
                </div>
              </div>
            )}
          </main>
          <TourOverlay />
        </div>
      </TourProvider>
    </EventProvider>
  )
}
}

export default App
