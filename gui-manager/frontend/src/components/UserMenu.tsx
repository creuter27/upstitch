import { useEffect, useRef } from 'react'
import { useStore } from '../store'

interface Props {
  onClose: () => void
}

export default function UserMenu({ onClose }: Props) {
  const { username, logout } = useStore()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [onClose])

  function handleLogout() {
    logout()
    onClose()
  }

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full mt-1 w-44 rounded shadow-lg z-50"
      style={{ background: '#2d2d2d', border: '1px solid #3e3e3e' }}
    >
      <div className="px-3 py-2">
        <div className="font-semibold text-vscode-text text-sm">{username}</div>
      </div>
      <div style={{ height: '1px', background: '#3e3e3e' }} />
      <button
        onClick={handleLogout}
        className="w-full text-left px-3 py-2 text-sm text-vscode-text hover:bg-vscode-hover transition-colors"
      >
        Sign out
      </button>
    </div>
  )
}
