import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../api'

export default function Environment() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [vars, setVars] = useState({})
  const [tokenVars, setTokenVars] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState(null)
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')

  const showToast = (msg, type = 'info') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const fetchEnv = () => {
    setLoading(true)
    api.get(`/bots/${id}/env`)
      .then(res => {
        setVars(res.data.variables || {})
        setTokenVars(res.data.token_vars || [])
      })
      .catch(err => showToast(err.response?.data?.detail || 'Failed to load env', 'error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchEnv() }, [id])

  const updateVar = (key, value) => {
    setVars(prev => ({ ...prev, [key]: value }))
  }

  const deleteVar = (key) => {
    if (tokenVars.includes(key.toUpperCase())) {
      showToast('Token variables cannot be deleted', 'error')
      return
    }
    setVars(prev => {
      const copy = { ...prev }
      delete copy[key]
      return copy
    })
  }

  const addVar = () => {
    if (!newKey.trim()) return
    if (tokenVars.includes(newKey.toUpperCase())) {
      showToast('Cannot override token variables', 'error')
      return
    }
    setVars(prev => ({ ...prev, [newKey.trim()]: newValue }))
    setNewKey('')
    setNewValue('')
  }

  const saveAll = async () => {
    setSaving(true)
    try {
      await api.put(`/bots/${id}/env`, { variables: vars })
      showToast('Environment variables saved! Restart your bot to apply changes.', 'success')
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed to save', 'error')
    }
    setSaving(false)
  }

  const isTokenVar = (key) => tokenVars.includes(key.toUpperCase())

  if (loading) return <div className="loading-page"><div className="spinner" /><span>Loading...</span></div>

  const entries = Object.entries(vars)

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>🔧 Environment Variables</h1>
          <p>Configure runtime environment variables for your bot</p>
        </div>
        <div className="btn-group">
          <button className="btn btn-sm btn-secondary" onClick={() => navigate(`/bot/${id}`)}>← Console</button>
          <button className="btn btn-primary" onClick={saveAll} disabled={saving}>
            {saving ? 'Saving...' : '💾 Save Changes'}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header">
          <h2>Variables ({entries.length})</h2>
        </div>

        <div style={{ marginBottom: 8, fontSize: 12, color: 'var(--text-muted)' }}>
          ⚠️ Token variables (BOT_TOKEN, DISCORD_TOKEN, etc.) are managed automatically and masked for security.
        </div>

        {entries.length === 0 ? (
          <div className="empty-state" style={{ padding: 30 }}>
            <p>No environment variables set yet. Add your first one below.</p>
          </div>
        ) : (
          entries.map(([key, value]) => (
            <div key={key} className="env-row">
              <input
                className="input"
                value={key}
                disabled
                style={{ opacity: isTokenVar(key) ? 0.5 : 1 }}
              />
              <input
                className="input"
                value={value}
                onChange={(e) => updateVar(key, e.target.value)}
                disabled={isTokenVar(key)}
                placeholder="value"
                style={{
                  opacity: isTokenVar(key) ? 0.5 : 1,
                  fontFamily: isTokenVar(key) ? 'inherit' : "'JetBrains Mono', monospace",
                }}
              />
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => deleteVar(key)}
                disabled={isTokenVar(key)}
                title={isTokenVar(key) ? 'Token variables cannot be deleted' : 'Delete variable'}
                style={{ opacity: isTokenVar(key) ? 0.3 : 1 }}
              >
                🗑
              </button>
            </div>
          ))
        )}
      </div>

      {/* Add New Variable */}
      <div className="card">
        <div className="card-header">
          <h2>Add Variable</h2>
        </div>
        <div className="env-row">
          <input
            className="input"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ''))}
            placeholder="VARIABLE_NAME"
            onKeyDown={(e) => e.key === 'Enter' && addVar()}
          />
          <input
            className="input"
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            placeholder="value"
            style={{ fontFamily: "'JetBrains Mono', monospace" }}
            onKeyDown={(e) => e.key === 'Enter' && addVar()}
          />
          <button className="btn btn-sm btn-primary" onClick={addVar}>+ Add</button>
        </div>
      </div>

      {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
    </div>
  )
}
