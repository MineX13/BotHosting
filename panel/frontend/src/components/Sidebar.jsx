import { NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Sidebar() {
  const { user, logout } = useAuth()
  const location = useLocation()

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-icon">M</div>
        <div>
          <h1>MineNodes</h1>
          <span>Bot Panel</span>
        </div>
      </div>

      <nav className="sidebar-nav">
        <div className="sidebar-section">
          <div className="sidebar-section-label">Navigation</div>
          <NavLink to="/" className={({ isActive }) => `sidebar-link ${isActive && location.pathname === '/' ? 'active' : ''}`}>
            <span className="icon">📊</span> Dashboard
          </NavLink>
          <NavLink to="/create" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}>
            <span className="icon">➕</span> Create Bot
          </NavLink>
        </div>

        {user?.is_admin && (
          <div className="sidebar-section">
            <div className="sidebar-section-label">Administration</div>
            <NavLink to="/admin" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}>
              <span className="icon">⚙️</span> Admin Panel
            </NavLink>
          </div>
        )}
      </nav>

      <div className="sidebar-user">
        {user?.avatar && (
          <img src={user.avatar} alt="" className="sidebar-avatar" />
        )}
        <div className="sidebar-user-info">
          <div className="sidebar-user-name">{user?.username || 'User'}</div>
          <div className="sidebar-user-role">{user?.is_admin ? 'Administrator' : 'User'}</div>
        </div>
        <button className="sidebar-logout-btn" onClick={logout} title="Logout">
          🚪
        </button>
      </div>
    </aside>
  )
}
