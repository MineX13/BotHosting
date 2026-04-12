import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import api from '../api'

export default function Dashboard() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [bots, setBots] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/bots')
      .then(res => setBots(res.data.bots || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const runningCount = bots.filter(b => b.status === 'running').length

  if (loading) {
    return <div className="loading-page"><div className="spinner" /><span>Loading bots...</span></div>
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p>Welcome back, {user?.username}. Manage your hosted bots below.</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/create')}>
          ➕ Create Bot
        </button>
      </div>

      <div className="stat-row">
        <div className="stat-card">
          <div className="stat-label">Total Bots</div>
          <div className="stat-value">{bots.length}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Running</div>
          <div className="stat-value" style={{ color: 'var(--success)' }}>{runningCount}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Stopped</div>
          <div className="stat-value" style={{ color: 'var(--danger)' }}>{bots.length - runningCount}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Bot Limit</div>
          <div className="stat-value">{user?.limits?.max_bots || 3}</div>
        </div>
      </div>

      {bots.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">🤖</div>
          <h3>No bots yet</h3>
          <p>Deploy your first Discord bot by uploading a ZIP file with your code.</p>
          <button className="btn btn-primary" onClick={() => navigate('/create')}>
            Create Your First Bot
          </button>
        </div>
      ) : (
        <div className="card-grid">
          {bots.map(bot => (
            <div
              key={bot.id}
              className="bot-card"
              data-status={bot.status}
              onClick={() => navigate(`/bot/${bot.id}`)}
            >
              <div className="bot-card-top">
                <div>
                  <div className="bot-card-name">{bot.name}</div>
                  <div className="bot-card-runtime">{bot.runtime} • {bot.id.slice(0, 8)}</div>
                </div>
                <span className={`status-badge ${bot.status}`}>
                  <span className="dot" />
                  {bot.status}
                </span>
              </div>
              <div className="bot-card-stats">
                <div className="bot-card-stat">
                  <strong>{bot.stats?.cpu_percent?.toFixed(1) || '0.0'}%</strong> CPU
                </div>
                <div className="bot-card-stat">
                  <strong>{bot.stats?.memory_mb?.toFixed(0) || '0'}</strong> MB RAM
                </div>
                <div className="bot-card-stat">
                  <strong>{bot.cpu_limit}</strong> cores
                </div>
                <div className="bot-card-stat">
                  <strong>{(bot.disk_limit_mb / 1024).toFixed(1)}</strong> GB disk
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
