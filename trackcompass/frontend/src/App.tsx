import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './layout/AppLayout'
import { RouteBoundary } from './components/ErrorBoundary'
import { useAuth } from './hooks/useAuth'
import Dashboard from './pages/Dashboard'
import Profile from './pages/Profile'
import Trends from './pages/Trends'
import Signals from './pages/Signals'
import Graph from './pages/Graph'
import Demand from './pages/Demand'
import Scorecard from './pages/Scorecard'
import Experiments from './pages/Experiments'
import Reports from './pages/Reports'
import Monitor from './pages/Monitor'
import Settings from './pages/Settings'
import Login from './pages/Login'

export default function App() {
  const { isAuthenticated } = useAuth()

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/*"
        element={
          isAuthenticated ? (
            <AppLayout>
              {/* 路由级错误边界：单个页面渲染失败只影响内容区，侧栏仍可用；
                  且随路由切换自动重置（见 RouteBoundary 的 key） */}
              <RouteBoundary>
                <Routes>
                  <Route path="/" element={<Navigate to="/dashboard" replace />} />
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/profile" element={<Profile />} />
                  <Route path="/trends" element={<Trends />} />
                  <Route path="/signals" element={<Signals />} />
                  <Route path="/graph" element={<Graph />} />
                  <Route path="/demand" element={<Demand />} />
                  <Route path="/scorecard" element={<Scorecard />} />
                  <Route path="/experiments" element={<Experiments />} />
                  <Route path="/reports" element={<Reports />} />
                  <Route path="/monitor" element={<Monitor />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="*" element={<Navigate to="/dashboard" replace />} />
                </Routes>
              </RouteBoundary>
            </AppLayout>
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
    </Routes>
  )
}
