import { useEffect, useState } from 'react'
import { getManufacturers, getSheetExists, getTool, Manufacturer } from '../api'
import { useStore } from '../store'

interface Props {
  toolId: string
}

function sendTerminalCommand(command: string) {
  window.dispatchEvent(new CustomEvent('terminal-command', { detail: { command } }))
}

function loginUrl(reorderingURL: string): string {
  try {
    const u = new URL(reorderingURL)
    return `${u.protocol}//${u.host}/`
  } catch {
    return reorderingURL
  }
}

// Module-level caches — survive tab switches (component remounts)
const sheetExistsCache: Record<string, boolean> = {}
const selectedCodeCache: Record<string, string> = {}

export default function ReorderPanel({ toolId }: Props) {
  const { toggleTerminal, terminalCollapsed } = useStore()
  const [manufacturers, setManufacturers] = useState<Manufacturer[]>([])
  const [toolPath, setToolPath] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedCode, setSelectedCode] = useState<string>(selectedCodeCache[toolId] ?? '')
  const [dryRun, setDryRun] = useState(false)
  const [isRunning, setIsRunning] = useState(false)
  const [sheetExists, setSheetExists] = useState<boolean | null>(null)
  const [checkingSheet, setCheckingSheet] = useState(false)

  useEffect(() => {
    Promise.all([getTool(toolId), getManufacturers(toolId)])
      .then(([tool, mfrs]) => {
        setToolPath(tool.path)
        setManufacturers(mfrs)
        if (mfrs.length > 0 && !selectedCodeCache[toolId]) {
          setSelectedCode(mfrs[0].code)
        }
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [toolId])

  // Check sheet existence only when manufacturer changes; use cache to avoid re-fetching on tab switch
  useEffect(() => {
    if (!selectedCode) return
    const cacheKey = `${toolId}:${selectedCode}`
    if (cacheKey in sheetExistsCache) {
      setSheetExists(sheetExistsCache[cacheKey])
      return
    }
    setSheetExists(null)
    setCheckingSheet(true)
    getSheetExists(toolId, selectedCode)
      .then((exists) => {
        sheetExistsCache[cacheKey] = exists
        setSheetExists(exists)
      })
      .catch(() => {
        sheetExistsCache[cacheKey] = false
        setSheetExists(false)
      })
      .finally(() => setCheckingSheet(false))
  }, [selectedCode, toolId])

  const mfr = manufacturers.find((m) => m.code === selectedCode) ?? null

  function run(cmd: string) {
    if (!toolPath) return
    if (terminalCollapsed) toggleTerminal()
    setTimeout(() => {
      sendTerminalCommand(`pushd "${toolPath}"\r`)
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

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-vscode-muted text-sm">
        Loading...
      </div>
    )
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-red-400 text-sm">
        {error}
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto p-4" style={{ background: '#1e1e1e' }}>
      {/* Header row: title + supplier dropdown + dry_run + interrupt */}
      <div className="flex items-center gap-3 mb-1 flex-wrap">
        <h1 className="text-base font-semibold text-vscode-text shrink-0">Bestellung</h1>

        <select
          value={selectedCode}
          onChange={(e) => {
            selectedCodeCache[toolId] = e.target.value
            setSelectedCode(e.target.value)
          }}
          className="px-2 py-1 rounded text-xs"
          style={{ background: '#2d2d2d', border: '1px solid #3e3e3e', color: '#d4d4d4', outline: 'none' }}
        >
          {manufacturers.map((m) => (
            <option key={m.code} value={m.code}>
              {m.name} ({m.code})
            </option>
          ))}
        </select>

        <label className="flex items-center gap-1.5 cursor-pointer select-none ml-auto" onClick={() => setDryRun(!dryRun)}>
          <div
            className="relative inline-block w-8 h-4 rounded-full transition-colors"
            style={{ background: dryRun ? '#2472c8' : '#3e3e3e' }}
          >
            <div
              className="absolute top-0.5 w-3 h-3 rounded-full transition-transform"
              style={{ background: '#fff', left: '2px', transform: dryRun ? 'translateX(16px)' : 'translateX(0)' }}
            />
          </div>
          <span className="text-xs" style={{ color: dryRun ? '#3b8eea' : '#858585' }}>dry_run</span>
        </label>

        {isRunning && (
          <button
            onClick={interrupt}
            className="px-2 py-1 rounded text-xs font-semibold"
            style={{ background: '#7f1d1d', color: '#fca5a5', border: '1px solid #991b1b' }}
          >
            ⬛ Interrupt
          </button>
        )}
      </div>

      <p className="text-vscode-muted text-xs mb-4">Erst- oder Nachbestellung von einem Lieferanten</p>

      <div style={{ height: '1px', background: '#3e3e3e', marginBottom: '16px' }} />

      {mfr && (
        <div className="flex gap-2 flex-wrap">
          {/* Step 1 — Login */}
          <button
            onClick={() => run(`${mfr.pythonCmd} execution/b2b_cart.py setup --manufacturer ${mfr.code} --url ${loginUrl(mfr.reorderingURL)}`)}
            className="flex-1 text-left px-3 py-2 rounded transition-colors"
            style={{ background: '#252526', border: '1px solid #3e3e3e', minWidth: '140px' }}
          >
            <div className="text-xs font-semibold mb-0.5" style={{ color: '#d4d4d4' }}>
              1. Login
            </div>
            <div className="text-xs" style={{ color: '#6b7280' }}>
              (einmalig oder wenn ausgeloggt)
            </div>
          </button>

          {/* Step 2 — Explore */}
          <button
            onClick={() => {
              if (sheetExists === null) return
              const cmd = sheetExists
                ? `${mfr.pythonCmd} execution/b2b_cart.py explore --manufacturer ${mfr.code} --refresh`
                : `${mfr.pythonCmd} execution/b2b_cart.py explore --manufacturer ${mfr.code} --url ${mfr.reorderingURL}${mfr.useNoCrawl ? ' --no-crawl' : ''}`
              run(cmd)
            }}
            className="flex-1 px-3 py-2 rounded font-semibold text-left transition-colors"
            style={{
              background: dryRun ? '#1a3a5c' : '#14532d',
              border: `2px solid ${dryRun ? '#2472c8' : '#16a34a'}`,
              color: dryRun ? '#60a5fa' : '#4ade80',
              minWidth: '140px',
              opacity: sheetExists === null ? 0.6 : 1,
            }}
          >
            <div className="text-xs font-semibold mb-0.5 flex items-center gap-1.5">
              {checkingSheet ? '2. Prüfe Sheet…' : sheetExists ? '2. Sheet aktualisieren' : '2. Sheet anlegen'}
              {!checkingSheet && sheetExists !== null && (
                <span className="text-xs font-normal px-1 rounded" style={{ background: 'rgba(0,0,0,0.3)', opacity: 0.7 }}>
                  {sheetExists ? '--refresh' : mfr.useNoCrawl ? '--no-crawl' : '--url'}
                </span>
              )}
            </div>
            <div className="text-xs font-normal" style={{ color: 'inherit', opacity: 0.7 }}>
              bestellbare Produkte von der Lieferantenseite auslesen
            </div>
          </button>

          {/* Step 3 — Order */}
          <button
            onClick={() => {
              const cmd = dryRun
                ? `${mfr.pythonCmd} execution/b2b_cart.py order --manufacturer ${mfr.code} --dry-run`
                : `${mfr.pythonCmd} execution/b2b_cart.py order --manufacturer ${mfr.code}`
              run(cmd)
            }}
            className="flex-1 px-3 py-2 rounded font-semibold text-left transition-colors"
            style={{
              background: dryRun ? '#1a3a5c' : '#14532d',
              border: `2px solid ${dryRun ? '#2472c8' : '#16a34a'}`,
              color: dryRun ? '#60a5fa' : '#4ade80',
              minWidth: '140px',
            }}
          >
            <div className="text-xs font-semibold mb-0.5 flex items-center gap-1.5">
              3. Bestellen
              {dryRun && (
                <span className="text-xs font-normal px-1 rounded" style={{ background: '#1e3a5f', color: '#93c5fd' }}>
                  dry_run
                </span>
              )}
            </div>
            <div className="text-xs font-normal" style={{ color: 'inherit', opacity: 0.7 }}>
              neue Seite im Sheet anlegen → Artikel in den Warenkorb → Order
            </div>
          </button>
        </div>
      )}

      <div className="mt-6 pt-3" style={{ borderTop: '1px solid #3e3e3e' }}>
        <div className="text-xs font-mono" style={{ color: '#4b5563' }}>{toolPath}</div>
      </div>
    </div>
  )
}
