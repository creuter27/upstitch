import { useState, FormEvent } from 'react'
import { login } from '../api'
import { useStore } from '../store'

export default function LoginPage() {
  const setAuth = useStore((s) => s.setAuth)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const data = await login(username, password)
      setAuth(data.access_token, data.username, data.permissions)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-vscode-bg flex items-center justify-center">
      <div
        className="w-full max-w-sm rounded-lg p-8"
        style={{ background: '#252526', border: '1px solid #3e3e3e' }}
      >
        <div className="mb-8 text-center">
          <div className="text-2xl font-semibold text-vscode-text mb-1">
            ⚙ Tool Manager
          </div>
          <div className="text-vscode-muted text-sm">Sign in to continue</div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs text-vscode-muted mb-1 uppercase tracking-wide">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 rounded text-vscode-text text-sm outline-none focus:ring-1"
              style={{
                background: '#3c3c3c',
                border: '1px solid #555',
              }}
              autoFocus
              autoComplete="username"
              required
            />
          </div>

          <div>
            <label className="block text-xs text-vscode-muted mb-1 uppercase tracking-wide">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 rounded text-vscode-text text-sm outline-none focus:ring-1"
              style={{ background: '#3c3c3c', border: '1px solid #555' }}
              autoComplete="current-password"
              required
            />
          </div>

          {error && (
            <div className="text-red-400 text-xs py-2 px-3 rounded" style={{ background: '#3a1a1a' }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 rounded text-sm font-medium transition-colors"
            style={{
              background: loading ? '#555' : '#007acc',
              color: '#fff',
              cursor: loading ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
