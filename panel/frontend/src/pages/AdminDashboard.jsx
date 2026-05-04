import { useState, useEffect } from 'react'
import { useAuth } from '../context/AuthContext'
import api from '../api'

export default function AdminDashboard() {
  const { user } = useAuth()
  const [users, setUsers] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState(null)
  const [selectedUser, setSelectedUser] = useState(null)
  const [limitsForm, setLimitsForm] = useState({})
  const [savingLimits, setSavingLimits] = useState(false)

  const showToast = (msg, type = 'info') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  useEffect(() => {
    if (!user?.is_admin) return
    Promise.all([
      api.get('/admin/users'),
      api.get('/admin/stats'),
    ]).then(([usersRes, statsRes]) => {
      setUsers(usersRes.data.users || [])
      setStats(statsRes.data)
    }).catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const toggleSuspend = async (u) => {
    const action = u.suspended ? 'unsuspend' : 'suspend'
    try {
      await api.post(`/admin/users/${u.id}/${action}`)
      showToast(`User ${action}ed`, 'success')
      setUsers(prev => prev.map(x => x.id === u.id ? { ...x, suspended: !x.suspended } : x))
    } catch (err) {
      showToast(err.response?.data?.detail || `Failed to ${action}`, 'error')
    }
  }

  const openLimits = async (u) => {
    try {
      const res = await api.get(`/admin/users/${u.id}/limits`)
      setSelectedUser(u)
      setLimitsForm({
        max_bots: res.data.max_bots,
        max_ram_mb: res.data.max_ram_mb,
        max_cpu: res.data.max_cpu,
        max_disk_mb: res.data.max_disk_mb,
      })
    } catch (err) {
      showToast('Failed to load limits', 'error')
    }
  }

  const saveLimits = async () => {
    if (!selectedUser) return
    setSavingLimits(true)
    try {
      await api.put(`/admin/users/${selectedUser.id}/limits`, limitsForm)
      showToast('Limits updated', 'success')
      setSelectedUser(null)
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed to save limits', 'error')
    }
    setSavingLimits(false)
  }

  if (!user?.is_admin) {
    return <div className="empty-state"><h3>Access Denied</h3><p>Admin privileges required.</p></div>
  }

  if (loading) return <div className="loading-page"><div className="spinner" /><span>Loading admin data...</span></div>

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>⚙️ Admin Dashboard</h1>
          <p>System overview and user management</p>
        </div>
      </div>

      {/* System Stats */}
      {stats && (
        <div className="stat-row">
          <div className="stat-card">
            <div className="stat-label">Total Bots</div>
            <div className="stat-value">{stats.bots?.total_bots || 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Running</div>
            <div className="stat-value" style={{ color: 'var(--success)' }}>{stats.bots?.running_bots || 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Users</div>
            <div className="stat-value">{stats.bots?.total_users || 0}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">CPU Usage</div>
            <div className="stat-value">{stats.system?.cpu_percent || 0}<span className="gauge-unit">%</span></div>
          </div>
          <div className="stat-card">
            <div className="stat-label">RAM</div>
            <div className="stat-value">
              {stats.system?.memory_used_mb ? Math.round(stats.system.memory_used_mb) : 0}
              <span className="gauge-unit"> MB</span>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Disk</div>
            <div className="stat-value">
              {stats.system?.disk_used_gb || 0}
              <span className="gauge-unit"> / {stats.system?.disk_total_gb || 0} GB</span>
            </div>
          </div>
        </div>
      )}

      {/* Users Table */}
      <div className="card">
        <div className="card-header">
          <h2>Users ({users.length})</h2>
        </div>
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>User ID</th>
                <th>Bots</th>
                <th>Max Bots</th>
                <th>RAM</th>
                <th>CPU</th>
                <th>Disk</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id}>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{u.id}</td>
                  <td>{u.bot_count}</td>
                  <td>{u.max_bots}</td>
                  <td>{u.max_ram_mb} MB</td>
                  <td>{u.max_cpu}</td>
                  <td>{u.max_disk_mb} MB</td>
                  <td>
                    <span className={`status-badge ${u.suspended ? 'stopped' : 'running'}`}>
                      <span className="dot" />
                      {u.suspended ? 'Suspended' : 'Active'}
                    </span>
                  </td>
                  <td>
                    <div className="btn-group">
                      <button className="btn btn-sm btn-secondary" onClick={() => openLimits(u)}>⚙️ Limits</button>
                      <button className={`btn btn-sm ${u.suspended ? 'btn-success' : 'btn-warning'}`}
                        onClick={() => toggleSuspend(u)}>
                        {u.suspended ? '✅ Unsuspend' : '🚫 Suspend'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Limits Modal */}
      {selectedUser && (
        <div className="modal-overlay" onClick={() => setSelectedUser(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Set Limits — User {selectedUser.id}</h2>

            <div className="input-group">
              <label>Max Bots</label>
              <input className="input" type="number" min="1" value={limitsForm.max_bots || ''}
                onChange={e => setLimitsForm({ ...limitsForm, max_bots: parseInt(e.target.value) || 1 })} />
            </div>

            <div className="input-group">
              <label>Max RAM per bot (MB)</label>
              <input className="input" type="number" min="64" value={limitsForm.max_ram_mb || ''}
                onChange={e => setLimitsForm({ ...limitsForm, max_ram_mb: parseInt(e.target.value) || 64 })} />
            </div>

            <div className="input-group">
              <label>Max CPU per bot (cores)</label>
              <input className="input" type="number" min="0.1" step="0.1" value={limitsForm.max_cpu || ''}
                onChange={e => setLimitsForm({ ...limitsForm, max_cpu: parseFloat(e.target.value) || 0.1 })} />
            </div>

            <div className="input-group">
              <label>Max Disk per bot (MB)</label>
              <input className="input" type="number" min="100" value={limitsForm.max_disk_mb || ''}
                onChange={e => setLimitsForm({ ...limitsForm, max_disk_mb: parseInt(e.target.value) || 100 })} />
            </div>

            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setSelectedUser(null)}>Cancel</button>
              <button className="btn btn-primary" onClick={saveLimits} disabled={savingLimits}>
                {savingLimits ? 'Saving...' : '💾 Save Limits'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
    </div>
  )
}
