import { useState } from 'react'
import { useStore } from '../store'
import UserMenu from './UserMenu'

export default function TopBar() {
  const { openTab, toggleSidebar, sidebarCollapsed } = useStore()
  const [userMenuOpen, setUserMenuOpen] = useState(false)

  function openSettings() {
    openTab({ id: 'settings', type: 'settings', title: 'Settings' })
  }

  return (
    <div
      className="flex items-center px-3 shrink-0 z-10"
      style={{
        height: '48px',
        background: '#323233',
        borderBottom: '1px solid #3e3e3e',
      }}
    >
      {/* Sidebar toggle + logo */}
      <button
        onClick={toggleSidebar}
        className="mr-3 text-vscode-muted hover:text-vscode-text transition-colors p-1 rounded"
        title={sidebarCollapsed ? 'Show sidebar' : 'Hide sidebar'}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <rect x="1" y="3" width="14" height="1.5" rx="0.75" />
          <rect x="1" y="7.25" width="14" height="1.5" rx="0.75" />
          <rect x="1" y="11.5" width="14" height="1.5" rx="0.75" />
        </svg>
      </button>

      <span className="text-vscode-text font-semibold text-sm select-none">
        ⚙ Tool Manager
      </span>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Settings button */}
      <button
        onClick={openSettings}
        className="p-2 text-vscode-muted hover:text-vscode-text transition-colors rounded mr-1"
        title="Settings"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <path d="M9.1 1.1a1.1 1.1 0 0 0-2.2 0L6.6 2a5.9 5.9 0 0 0-1.4.6l-.8-.5a1.1 1.1 0 0 0-1.6 1.6l.5.8a5.9 5.9 0 0 0-.6 1.4l-.9.3a1.1 1.1 0 0 0 0 2.2l.9.3c.1.5.3.9.6 1.4l-.5.8a1.1 1.1 0 0 0 1.6 1.6l.8-.5c.4.3.9.5 1.4.6l.3.9a1.1 1.1 0 0 0 2.2 0l.3-.9a5.9 5.9 0 0 0 1.4-.6l.8.5a1.1 1.1 0 0 0 1.6-1.6l-.5-.8c.3-.4.5-.9.6-1.4l.9-.3a1.1 1.1 0 0 0 0-2.2l-.9-.3a5.9 5.9 0 0 0-.6-1.4l.5-.8a1.1 1.1 0 0 0-1.6-1.6l-.8.5a5.9 5.9 0 0 0-1.4-.6L9.1 1.1zM8 10a2 2 0 1 1 0-4 2 2 0 0 1 0 4z" />
        </svg>
      </button>

      {/* User menu */}
      <div className="relative">
        <button
          onClick={() => setUserMenuOpen(!userMenuOpen)}
          className="p-2 text-vscode-muted hover:text-vscode-text transition-colors rounded"
          title="User menu"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm-5 6s-1 0-1-1 1-4 6-4 6 3 6 4-1 1-1 1H3z" />
          </svg>
        </button>
        {userMenuOpen && (
          <UserMenu onClose={() => setUserMenuOpen(false)} />
        )}
      </div>
    </div>
  )
}
