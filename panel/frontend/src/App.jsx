import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './context/AuthContext'
import Sidebar from './components/Sidebar'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import BotConsole from './pages/BotConsole'
import FileManager from './pages/FileManager'
import Environment from './pages/Environment'
import CreateBot from './pages/CreateBot'
import AdminDashboard from './pages/AdminDashboard'

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="loading-page">
        <div className="spinner" />
        <span>Loading...</span>
      </div>
    )
  }

  if (!user) return <Navigate to="/login" />
  return children
}

function AppLayout({ children }) {
  return (
    <div className="app-layout">
      <Sidebar />
      <main className="main-content">{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />

      <Route path="/" element={
        <ProtectedRoute>
          <AppLayout><Dashboard /></AppLayout>
        </ProtectedRoute>
      } />

      <Route path="/bot/:id" element={
        <ProtectedRoute>
          <AppLayout><BotConsole /></AppLayout>
        </ProtectedRoute>
      } />

      <Route path="/bot/:id/files" element={
        <ProtectedRoute>
          <AppLayout><FileManager /></AppLayout>
        </ProtectedRoute>
      } />

      <Route path="/bot/:id/env" element={
        <ProtectedRoute>
          <AppLayout><Environment /></AppLayout>
        </ProtectedRoute>
      } />

      <Route path="/create" element={
        <ProtectedRoute>
          <AppLayout><CreateBot /></AppLayout>
        </ProtectedRoute>
      } />

      <Route path="/admin" element={
        <ProtectedRoute>
          <AppLayout><AdminDashboard /></AppLayout>
        </ProtectedRoute>
      } />

      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  )
}
