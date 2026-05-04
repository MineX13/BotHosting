import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api'

export default function CreateBot() {
  const navigate = useNavigate()
  const fileRef = useRef(null)
  const [form, setForm] = useState({ name: '', token: '', runtime: 'python' })
  const [file, setFile] = useState(null)
  const [creating, setCreating] = useState(false)
  const [toast, setToast] = useState(null)
  const [dragOver, setDragOver] = useState(false)

  const showToast = (msg, type = 'info') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 4000)
  }

  const handleFile = (f) => {
    if (!f) return
    if (!f.name.endsWith('.zip')) {
      showToast('Please upload a .zip file', 'error')
      return
    }
    if (f.size > 50 * 1024 * 1024) {
      showToast('File too large (max 50MB)', 'error')
      return
    }
    setFile(f)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files[0]
    handleFile(f)
  }

  const handleSubmit = async () => {
    if (!file) { showToast('Please upload your bot code as a ZIP file', 'error'); return }
    if (!form.token.trim()) { showToast('Bot token is required', 'error'); return }

    setCreating(true)
    try {
      const formData = new FormData()
      formData.append('zip_file', file)
      formData.append('token', form.token.trim())
      formData.append('runtime', form.runtime)
      formData.append('name', form.name.trim())

      const res = await api.post('/bots', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })

      showToast('Bot deployed successfully!', 'success')
      setTimeout(() => navigate(`/bot/${res.data.bot_id}`), 1000)
    } catch (err) {
      showToast(err.response?.data?.detail || 'Deployment failed', 'error')
    }
    setCreating(false)
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Create New Bot</h1>
          <p>Deploy a new Discord bot from a ZIP file</p>
        </div>
      </div>

      <div style={{ maxWidth: 640 }}>
        {/* Upload Zone */}
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="card-header"><h2>1. Upload Code</h2></div>
          <div
            style={{
              border: `2px dashed ${dragOver ? 'var(--accent)' : 'var(--border)'}`,
              borderRadius: 'var(--radius)',
              padding: 40,
              textAlign: 'center',
              cursor: 'pointer',
              transition: 'all 0.2s',
              background: dragOver ? 'var(--accent-glow)' : 'transparent',
            }}
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
          >
            <input ref={fileRef} type="file" accept=".zip" hidden onChange={(e) => handleFile(e.target.files[0])} />
            {file ? (
              <div>
                <div style={{ fontSize: 32, marginBottom: 8 }}>📦</div>
                <div style={{ fontWeight: 600 }}>{file.name}</div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
                  {(file.size / 1024).toFixed(0)} KB — Click to change
                </div>
              </div>
            ) : (
              <div>
                <div style={{ fontSize: 32, marginBottom: 8 }}>📁</div>
                <div style={{ fontWeight: 500, color: 'var(--text-secondary)' }}>
                  Drag & drop your .zip file here
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
                  or click to browse • Max 50MB
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Bot Details */}
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="card-header"><h2>2. Bot Details</h2></div>

          <div className="input-group">
            <label>Bot Name (optional)</label>
            <input className="input" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
              placeholder="My Discord Bot" />
          </div>

          <div className="input-group">
            <label>Bot Token *</label>
            <input className="input" type="password" value={form.token}
              onChange={e => setForm({ ...form, token: e.target.value })}
              placeholder="Your Discord bot token (encrypted before storage)" />
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              🔒 Your token is encrypted with AES-256-GCM and never stored in plaintext.
            </div>
          </div>

          <div className="input-group">
            <label>Runtime</label>
            <select className="select" value={form.runtime} onChange={e => setForm({ ...form, runtime: e.target.value })}>
              <option value="python">Python</option>
              <option value="node">Node.js</option>
            </select>
          </div>
        </div>

        {/* Deploy */}
        <button className="btn btn-primary btn-lg" style={{ width: '100%' }}
          disabled={creating || !file || !form.token.trim()}
          onClick={handleSubmit}>
          {creating ? (
            <><div className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} /> Deploying...</>
          ) : (
            '🚀 Deploy Bot'
          )}
        </button>
      </div>

      {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
    </div>
  )
}
