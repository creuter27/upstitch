import { useEffect, useState } from 'react'
import { getSettings, updateSetting } from '../api'
import { useStore } from '../store'

export default function SettingsPage() {
  const { setSettings } = useStore()
  const [values, setValues] = useState<Record<string, string>>({})
  const [original, setOriginal] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')

  useEffect(() => {
    setLoading(true)
    getSettings()
      .then((data) => {
        setValues(data)
        setOriginal(data)
        setSettings(data)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  async function handleSave() {
    setSaveStatus('saving')
    try {
      const changed = Object.entries(values).filter(
        ([k, v]) => original[k] !== v
      )
      for (const [key, value] of changed) {
        await updateSetting(key, value)
      }
      setOriginal(values)
      setSettings(values)
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
    } catch (e) {
      console.error(e)
      setSaveStatus('error')
    }
  }

  function handleChange(key: string, value: string) {
    setValues((prev) => ({ ...prev, [key]: value }))
    setSaveStatus('idle')
  }

  const isDirty = Object.entries(values).some(([k, v]) => original[k] !== v)

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-vscode-muted text-sm">
        Loading settings...
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto p-8" style={{ background: '#1e1e1e' }}>
      <div className="max-w-xl">
        <h1 className="text-xl font-semibold text-vscode-text mb-1">Settings</h1>
        <p className="text-vscode-muted text-sm mb-8">Application configuration</p>

        <div style={{ height: '1px', background: '#3e3e3e', marginBottom: '32px' }} />

        {/* Settings form */}
        <div className="space-y-6">
          <div>
            <label className="block text-xs font-semibold text-vscode-muted uppercase tracking-wider mb-2">
              Default Start URL
            </label>
            <input
              type="text"
              value={values.start_url || ''}
              onChange={(e) => handleChange('start_url', e.target.value)}
              placeholder="https://google.com"
              className="w-full px-3 py-2 rounded text-vscode-text text-sm outline-none"
              style={{ background: '#3c3c3c', border: '1px solid #555' }}
            />
            <p className="mt-1.5 text-xs text-vscode-muted">
              URL opened when clicking "Open in Tab" without a specific start URL.
            </p>
          </div>
        </div>

        {/* Save button */}
        <div className="mt-8 flex items-center gap-4">
          <button
            onClick={handleSave}
            disabled={!isDirty || saveStatus === 'saving'}
            className="px-4 py-2 rounded text-sm font-medium transition-colors"
            style={{
              background: isDirty ? '#007acc' : '#3e3e3e',
              color: isDirty ? '#fff' : '#858585',
              cursor: isDirty ? 'pointer' : 'default',
            }}
          >
            {saveStatus === 'saving' ? 'Saving...' : 'Save Settings'}
          </button>
          {saveStatus === 'saved' && (
            <span className="text-sm" style={{ color: '#4ec9b0' }}>
              ✓ Settings saved
            </span>
          )}
          {saveStatus === 'error' && (
            <span className="text-sm text-red-400">Failed to save</span>
          )}
        </div>
      </div>
    </div>
  )
}
