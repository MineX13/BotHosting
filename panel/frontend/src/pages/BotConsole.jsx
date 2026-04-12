import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../api'

export default function BotConsole() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [bot, setBot] = useState(null)
  const [logs, setLogs] = useState('')
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState('')
  const [toast, setToast] = useState(null)
  const termRef = useRef(null)

  const fetchBot = () => {
    api.get(`/bots/${id}`).then(res => { setBot(res.data); setLoading(false) }).catch(() => setLoading(false))
  }

  const fetchLogs = () => {
    api.get(`/bots/${id}/logs?tail=300`).then(res => setLogs(res.data.logs || '')).catch(() => {})
  }

  useEffect(() => {
    fetchBot()
    fetchLogs()
    const interval = setInterval(() => { fetchBot(); fetchLogs() }, 5000)
    return () => clearInterval(interval)
  }, [id])

  useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
  }, [logs])

  const showToast = (msg, type = 'info') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const doAction = async (action) => {
    setActionLoading(action)
    try {
      await api.post(`/bots/${id}/${action}`)
      showToast(`Bot ${action}ed successfully`, 'success')
      setTimeout(fetchBot, 1000)
    } catch (err) {
      showToast(err.response?.data?.detail || `Failed to ${action}`, 'error')
    }
    setActionLoading('')
  }

  const doDelete = async () => {
    if (!confirm('Are you sure you want to permanently delete this bot and all its files?')) return
    setActionLoading('delete')
    try {
      await api.delete(`/bots/${id}`)
      showToast('Bot deleted', 'success')
      setTimeout(() => navigate('/'), 500)
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed to delete', 'error')
    }
    setActionLoading('')
  }

  if (loading) return <div className="loading-page"><div className="spinner" /><span>Loading...</span></div>
  if (!bot) return <div className="empty-state"><h3>Bot not found</h3></div>

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{bot.name}</h1>
          <p>{bot.runtime} • {bot.id}</p>
        </div>
        <span className={`status-badge ${bot.status}`}>
          <span className="dot" />
          {bot.status}
        </span>
      </div>

      {/* Resource Gauges */}
      <div className="gauge-grid" style={{ marginBottom: 20 }}>
        <div className="gauge-card">
          <div className="gauge-label">CPU Usage</div>
          <div className="gauge-value">
            {bot.stats?.cpu_percent?.toFixed(1) || '0.0'}
            <span className="gauge-unit">%</span>
          </div>
          <div className="gauge-bar">
            <div className="gauge-bar-fill" style={{ width: `${Math.min(bot.stats?.cpu_percent || 0, 100)}%` }} />
          </div>
        </div>
        <div className="gauge-card">
          <div className="gauge-label">Memory</div>
          <div className="gauge-value">
            {bot.stats?.memory_mb?.toFixed(0) || '0'}
            <span className="gauge-unit"> MB</span>
          </div>
          <div className="gauge-bar">
            <div className={`gauge-bar-fill ${(bot.stats?.memory_mb || 0) > bot.ram_limit_mb * 0.8 ? 'danger' : ''}`}
              style={{ width: `${Math.min(((bot.stats?.memory_mb || 0) / bot.ram_limit_mb) * 100, 100)}%` }} />
          </div>
        </div>
        <div className="gauge-card">
          <div className="gauge-label">CPU Limit</div>
          <div className="gauge-value">{bot.cpu_limit}<span className="gauge-unit"> cores</span></div>
        </div>
        <div className="gauge-card">
          <div className="gauge-label">Disk Limit</div>
          <div className="gauge-value">{(bot.disk_limit_mb / 1024).toFixed(1)}<span className="gauge-unit"> GB</span></div>
        </div>
      </div>

      {/* Power Controls */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header">
          <h2>Power Controls</h2>
          <div className="btn-group">
            <button className="btn btn-sm btn-secondary" onClick={() => navigate(`/bot/${id}/files`)}>📁 Files</button>
            <button className="btn btn-sm btn-secondary" onClick={() => navigate(`/bot/${id}/env`)}>🔧 Env Vars</button>
          </div>
        </div>
        <div className="btn-group">
          <button className="btn btn-success" disabled={actionLoading === 'start' || bot.status === 'running'} onClick={() => doAction('start')}>
            {actionLoading === 'start' ? '...' : '▶ Start'}
          </button>
          <button className="btn btn-warning" disabled={actionLoading === 'restart'} onClick={() => doAction('restart')}>
            {actionLoading === 'restart' ? '...' : '🔄 Restart'}
          </button>
          <button className="btn btn-secondary" disabled={actionLoading === 'stop' || bot.status === 'stopped'} onClick={() => doAction('stop')}>
            {actionLoading === 'stop' ? '...' : '⏹ Stop'}
          </button>
          <button className="btn btn-danger" disabled={actionLoading === 'delete'} onClick={doDelete}>
            {actionLoading === 'delete' ? '...' : '🗑 Delete'}
          </button>
        </div>
      </div>

      {/* Console */}
      <div className="card">
        <div className="card-header">
          <h2>Console Output</h2>
          <button className="btn btn-sm btn-ghost" onClick={fetchLogs}>↻ Refresh</button>
        </div>
        <div className="terminal" ref={termRef}>
          {logs || 'No logs available yet. Start the bot to see output.'}
        </div>
      </div>

      {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
    </div>
  )
}
