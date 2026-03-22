import { useEffect, useState } from 'react'
import { getTool, Tool, ToolFunction } from '../api'
import { useStore } from '../store'

interface Props {
  toolId: string
}

function sendTerminalCommand(command: string) {
  window.dispatchEvent(new CustomEvent('terminal-command', { detail: { command } }))
}

export default function ToolPanel({ toolId }: Props) {
  const { openTab, toggleTerminal, terminalCollapsed } = useStore()
  const [tool, setTool] = useState<Tool | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState<string | null>(null)
  const [dryRun, setDryRun] = useState(false)
  const [isRunning, setIsRunning] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError('')
    getTool(toolId)
      .then(setTool)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [toolId])

  function runFunction(fn: ToolFunction) {
    if (fn.requires_confirm && confirming !== fn.name) {
      setConfirming(fn.name)
      return
    }
    setConfirming(null)
    if (!tool) return

    if (terminalCollapsed) toggleTerminal()

    const cmd = (dryRun && fn.supports_dry_run) ? `${fn.command} --dry-run` : fn.command

    setTimeout(() => {
      sendTerminalCommand(`pushd "${tool.path}"\r`)
      setTimeout(() => {
        sendTerminalCommand(`${cmd}\r`)
        setIsRunning(true)
      }, 100)
    }, 150)
  }

  function interrupt() {
    sendTerminalCommand('\x03')
    setIsRunning(false)
  }

  function openBrowserTab() {
    if (!tool?.start_url) return
    openTab({
      id: `browser-${tool.id}`,
      type: 'browser',
      title: tool.name,
      url: tool.start_url,
    })
  }

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-vscode-muted text-sm">
        Loading...
      </div>
    )
  }

  if (error || !tool) {
    return (
      <div className="h-full flex items-center justify-center text-red-400 text-sm">
        {error || 'Tool not found'}
      </div>
    )
  }

  const launchFn = tool.functions?.find((fn) => fn.is_launch)
  const otherFns = tool.functions?.filter((fn) => !fn.is_launch) ?? []

  return (
    <div className="h-full overflow-y-auto p-6" style={{ background: '#1e1e1e' }}>
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-vscode-text mb-1">{tool.name}</h1>
            <p className="text-vscode-muted text-sm">{tool.description}</p>
          </div>
          {tool.start_url && (
            <button
              onClick={openBrowserTab}
              className="shrink-0 px-3 py-1.5 rounded text-sm font-medium transition-colors"
              style={{ background: '#007acc', color: '#fff' }}
            >
              🌐 Open in Tab
            </button>
          )}
        </div>
      </div>

      {/* Divider */}
      <div style={{ height: '1px', background: '#3e3e3e', marginBottom: '20px' }} />

      {/* Controls row: dry run toggle + interrupt */}
      <div className="flex items-center justify-between mb-5">
        <label className="flex items-center gap-2 cursor-pointer select-none" onClick={() => setDryRun(!dryRun)}>
          <div
            className="relative inline-block w-10 h-5 rounded-full transition-colors"
            style={{ background: dryRun ? '#2472c8' : '#3e3e3e' }}
          >
            <div
              className="absolute top-0.5 w-4 h-4 rounded-full transition-transform"
              style={{
                background: '#fff',
                left: '2px',
                transform: dryRun ? 'translateX(20px)' : 'translateX(0)',
              }}
            />
          </div>
          <span className="text-sm font-medium" style={{ color: dryRun ? '#3b8eea' : '#858585' }}>
            dry_run
          </span>
        </label>

        {isRunning && (
          <button
            onClick={interrupt}
            className="px-3 py-1.5 rounded text-sm font-semibold"
            style={{ background: '#7f1d1d', color: '#fca5a5', border: '1px solid #991b1b' }}
          >
            ⬛ Interrupt
          </button>
        )}
      </div>

      {/* Launch button */}
      {launchFn && (
        <div className="mb-6">
          <button
            onClick={() => runFunction(launchFn)}
            className="w-full py-5 rounded-lg font-bold text-lg transition-colors"
            style={{
              background: confirming === launchFn.name
                ? (dryRun && launchFn.supports_dry_run ? '#1a3050' : '#14532d')
                : (dryRun && launchFn.supports_dry_run ? '#1a3a5c' : '#14532d'),
              border: `2px solid ${confirming === launchFn.name
                ? (dryRun && launchFn.supports_dry_run ? '#60a5fa' : '#86efac')
                : (dryRun && launchFn.supports_dry_run ? '#2472c8' : '#16a34a')}`,
              color: dryRun && launchFn.supports_dry_run ? '#60a5fa' : '#4ade80',
            }}
          >
            {confirming === launchFn.name ? (
              '⚠ Nochmal klicken zum Starten'
            ) : (
              <>
                ▶ {launchFn.name}
                {dryRun && launchFn.supports_dry_run && (
                  <span
                    className="ml-3 text-sm font-normal px-2 py-0.5 rounded"
                    style={{ background: '#1e3a5f', color: '#93c5fd' }}
                  >
                    dry_run
                  </span>
                )}
              </>
            )}
          </button>
          {launchFn.description && (
            <p className="mt-2 text-xs text-center" style={{ color: '#6b7280' }}>
              {launchFn.description}
            </p>
          )}
        </div>
      )}

      {/* Smaller function buttons */}
      {otherFns.length > 0 && (
        <div>
          {launchFn && (
            <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#6b7280' }}>
              Einzelne Schritte
            </div>
          )}
          <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
            {otherFns.map((fn) => {
              const isConfirming = confirming === fn.name
              const isDangerous = fn.requires_confirm

              const effectiveDryRun = dryRun && fn.supports_dry_run

              const bg = isConfirming
                ? '#5a2d0c'
                : isDangerous
                ? '#3a1a1a'
                : effectiveDryRun
                ? '#1a2a3a'
                : '#252526'

              const borderColor = isConfirming
                ? '#f97316'
                : isDangerous
                ? '#7f1d1d'
                : effectiveDryRun
                ? '#2472c8'
                : '#3e3e3e'

              const nameColor = isConfirming
                ? '#f97316'
                : isDangerous
                ? '#f87171'
                : effectiveDryRun
                ? '#60a5fa'
                : '#d4d4d4'

              return (
                <button
                  key={fn.name}
                  onClick={() => runFunction(fn)}
                  className="text-left p-3 rounded transition-colors"
                  style={{ background: bg, border: `1px solid ${borderColor}` }}
                >
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="text-xs font-semibold" style={{ color: nameColor }}>
                      {fn.name}
                    </span>
                    {effectiveDryRun && !isConfirming && (
                      <span
                        className="text-xs px-1.5 py-0.5 rounded"
                        style={{ background: '#1e3a5f', color: '#93c5fd' }}
                      >
                        dry_run
                      </span>
                    )}
                    {isDangerous && !isConfirming && !effectiveDryRun && (
                      <span
                        className="text-xs px-1.5 py-0.5 rounded"
                        style={{ background: '#7f1d1d', color: '#f87171' }}
                      >
                        confirm
                      </span>
                    )}
                    {isConfirming && (
                      <span
                        className="text-xs px-1.5 py-0.5 rounded"
                        style={{ background: '#7c2d12', color: '#fed7aa' }}
                      >
                        click again
                      </span>
                    )}
                  </div>
                  <div className="text-xs leading-relaxed" style={{ color: '#9ca3af' }}>
                    {fn.description}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Footer info */}
      <div className="mt-8 pt-4" style={{ borderTop: '1px solid #3e3e3e' }}>
        <div className="text-xs space-y-1" style={{ color: '#6b7280' }}>
          <div>
            <span className="opacity-60">Path: </span>
            <span className="font-mono">{tool.path}</span>
          </div>
          {tool.venv && (
            <div>
              <span className="opacity-60">Venv: </span>
              <span className="font-mono">{tool.path}/{tool.venv}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
