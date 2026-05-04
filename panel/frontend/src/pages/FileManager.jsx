import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Editor from '@monaco-editor/react'
import api from '../api'

const EXT_LANG = {
  py: 'python', js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
  json: 'json', yml: 'yaml', yaml: 'yaml', md: 'markdown', html: 'html', css: 'css',
  sh: 'shell', sql: 'sql', xml: 'xml', txt: 'plaintext', env: 'ini', toml: 'ini',
  cfg: 'ini', ini: 'ini', log: 'plaintext', csv: 'plaintext',
}

function getLanguage(name) {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  return EXT_LANG[ext] || 'plaintext'
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

export default function FileManager() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [files, setFiles] = useState([])
  const [currentPath, setCurrentPath] = useState('/')
  const [selectedFile, setSelectedFile] = useState(null)
  const [fileContent, setFileContent] = useState('')
  const [editedContent, setEditedContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState(null)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newName, setNewName] = useState('')
  const [newIsDir, setNewIsDir] = useState(false)
  const uploadRef = useRef(null)

  const showToast = (msg, type = 'info') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const fetchFiles = (path = currentPath) => {
    setLoading(true)
    api.get(`/bots/${id}/files?path=${encodeURIComponent(path)}`)
      .then(res => { setFiles(res.data.items || []); setCurrentPath(path) })
      .catch(err => showToast(err.response?.data?.detail || 'Failed to load files', 'error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchFiles('/') }, [id])

  const openFile = (item) => {
    if (item.is_dir) {
      setSelectedFile(null)
      fetchFiles(item.path)
      return
    }
    if (!item.editable) {
      showToast('Binary files cannot be edited in the web editor', 'info')
      return
    }
    api.get(`/bots/${id}/files/content?path=${encodeURIComponent(item.path)}`)
      .then(res => {
        setSelectedFile(item)
        setFileContent(res.data.content)
        setEditedContent(res.data.content)
      })
      .catch(err => showToast(err.response?.data?.detail || 'Failed to open file', 'error'))
  }

  const saveFile = async () => {
    if (!selectedFile) return
    setSaving(true)
    try {
      await api.put(`/bots/${id}/files/content`, { path: selectedFile.path, content: editedContent })
      setFileContent(editedContent)
      showToast('File saved successfully', 'success')
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed to save file', 'error')
    }
    setSaving(false)
  }

  const deleteItem = async (item) => {
    const confirmMsg = item.is_dir
      ? `Delete folder "${item.name}" and all its contents?`
      : `Delete file "${item.name}"?`
    if (!confirm(confirmMsg)) return
    try {
      await api.delete(`/bots/${id}/files?path=${encodeURIComponent(item.path)}`)
      showToast('Deleted successfully', 'success')
      if (selectedFile?.path === item.path) setSelectedFile(null)
      fetchFiles()
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed to delete', 'error')
    }
  }

  const createItem = async () => {
    if (!newName.trim()) return
    const path = currentPath === '/' ? newName : `${currentPath}/${newName}`
    try {
      await api.post(`/bots/${id}/files/create`, { path, is_dir: newIsDir, content: '' })
      showToast(`${newIsDir ? 'Folder' : 'File'} created`, 'success')
      setShowCreateModal(false)
      setNewName('')
      fetchFiles()
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed to create', 'error')
    }
  }

  const uploadFiles = async (fileList) => {
    if (!fileList || fileList.length === 0) return
    const formData = new FormData()
    for (const f of fileList) {
      formData.append('files', f)
    }
    try {
      const res = await api.post(`/bots/${id}/files/upload?path=${encodeURIComponent(currentPath)}`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      showToast(`Uploaded ${res.data.count} file(s)`, 'success')
      fetchFiles()
    } catch (err) {
      showToast(err.response?.data?.detail || 'Upload failed', 'error')
    }
  }

  const goUp = () => {
    if (currentPath === '/' || currentPath === '') return
    const parts = currentPath.split('/')
    parts.pop()
    const parent = parts.join('/') || '/'
    setSelectedFile(null)
    fetchFiles(parent)
  }

  const breadcrumbs = currentPath === '/' ? ['root'] : ['root', ...currentPath.split('/').filter(Boolean)]

  const hasUnsaved = selectedFile && editedContent !== fileContent

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>📁 File Manager</h1>
          <p>Browse and edit your bot's files</p>
        </div>
        <div className="btn-group">
          <button className="btn btn-sm btn-secondary" onClick={() => navigate(`/bot/${id}`)}>← Console</button>
          <button className="btn btn-sm btn-primary" onClick={() => { setNewIsDir(false); setNewName(''); setShowCreateModal(true) }}>+ New File</button>
          <button className="btn btn-sm btn-secondary" onClick={() => { setNewIsDir(true); setNewName(''); setShowCreateModal(true) }}>+ New Folder</button>
          <button className="btn btn-sm btn-success" onClick={() => uploadRef.current?.click()}>📤 Upload Files</button>
        </div>
      </div>

      {/* Breadcrumb */}
      <div className="breadcrumb">
        {breadcrumbs.map((part, i) => (
          <span key={i}>
            {i > 0 && <span className="breadcrumb-sep">/</span>}
            <span className="breadcrumb-item" onClick={() => {
              if (i === 0) { setSelectedFile(null); fetchFiles('/') }
              else {
                const path = breadcrumbs.slice(1, i + 1).join('/')
                setSelectedFile(null)
                fetchFiles(path)
              }
            }}>
              {part}
            </span>
          </span>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: selectedFile ? '300px 1fr' : '1fr', gap: 16 }}>
        {/* File List */}
        <div className="file-tree">
          {currentPath !== '/' && (
            <div className="file-item" onClick={goUp}>
              <span className="file-icon">⬆</span>
              <span className="file-name">..</span>
            </div>
          )}
          {loading ? (
            <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
          ) : files.length === 0 ? (
            <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>Empty directory</div>
          ) : files.map(item => (
            <div key={item.path} className={`file-item ${selectedFile?.path === item.path ? 'active' : ''}`}
              onClick={() => openFile(item)}>
              <span className="file-icon">{item.is_dir ? '📁' : '📄'}</span>
              <span className="file-name">{item.name}</span>
              {!item.is_dir && <span className="file-size">{formatSize(item.size)}</span>}
              <div className="file-actions">
                <button className="btn btn-sm btn-ghost" onClick={(e) => { e.stopPropagation(); deleteItem(item) }} title="Delete">🗑</button>
              </div>
            </div>
          ))}
        </div>

        {/* Editor */}
        {selectedFile && (
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', borderBottom: '1px solid var(--border)' }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>
                {selectedFile.name}
                {hasUnsaved && <span style={{ color: 'var(--warning)', marginLeft: 8 }}>● unsaved</span>}
              </span>
              <button className="btn btn-sm btn-primary" disabled={saving || !hasUnsaved} onClick={saveFile}>
                {saving ? 'Saving...' : '💾 Save'}
              </button>
            </div>
            <Editor
              height="600px"
              language={getLanguage(selectedFile.name)}
              value={editedContent}
              onChange={(val) => setEditedContent(val || '')}
              theme="vs-dark"
              options={{
                minimap: { enabled: false },
                fontSize: 13,
                fontFamily: "'JetBrains Mono', monospace",
                lineNumbers: 'on',
                scrollBeyondLastLine: false,
                wordWrap: 'on',
                padding: { top: 12 },
              }}
            />
          </div>
        )}
      </div>

      {/* Create Modal */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>{newIsDir ? 'Create Folder' : 'Create File'}</h2>
            <div className="input-group">
              <label>Name</label>
              <input className="input" value={newName} onChange={e => setNewName(e.target.value)}
                placeholder={newIsDir ? 'folder_name' : 'filename.py'} autoFocus
                onKeyDown={e => e.key === 'Enter' && createItem()} />
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setShowCreateModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={createItem}>Create</button>
            </div>
          </div>
        </div>
      )}

      {/* Hidden upload input */}
      <input ref={uploadRef} type="file" multiple hidden onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />

      {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
    </div>
  )
}
