import { useEffect, useState } from 'react'
import {
  getManufacturers, getSheetExists, getTool, Manufacturer,
  getAddStockPreview, updateInventoryStock, AddStockPreviewItem,
} from '../api'
import { useStore } from '../store'
import { prefillInventoryImport } from './InventoryPanel'

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
  const { toggleTerminal, terminalCollapsed, openTab } = useStore()
  const [manufacturers, setManufacturers] = useState<Manufacturer[]>([])
  const [toolPath, setToolPath] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedCode, setSelectedCode] = useState<string>(selectedCodeCache[toolId] ?? '')
  const [dryRun, setDryRun] = useState(false)
  const [isRunning, setIsRunning] = useState(false)
  const [sheetExists, setSheetExists] = useState<boolean | null>(null)
  const [checkingSheet, setCheckingSheet] = useState(false)

  // Add-stock state
  const [stockPreviewLoading, setStockPreviewLoading] = useState(false)
  const [stockPreviewError, setStockPreviewError]     = useState('')
  const [stockPreviewData, setStockPreviewData]       = useState<{
    tab: string
    items: AddStockPreviewItem[]
    errors: { sku?: string; error: string }[]
  } | null>(null)
  const [stockApplying, setStockApplying]             = useState(false)
  const [stockApplyProgress, setStockApplyProgress]   = useState<{
    done: number; total: number; currentSku: string
  } | null>(null)
  const [stockApplyResults, setStockApplyResults]     = useState<{
    sku: string; success: boolean; newStock?: number; error?: string
  }[] | null>(null)
  const [stockChecked, setStockChecked]               = useState<Set<string>>(new Set())

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
      sendTerminalCommand(`pushd "${toolPath}" && ${cmd}\r`)
      setIsRunning(true)
    }, 150)
  }

  function interrupt() {
    sendTerminalCommand('\x03')
    setIsRunning(false)
  }

  async function handleAddStock() {
    if (!selectedCode) return
    setStockPreviewLoading(true)
    setStockPreviewError('')
    setStockPreviewData(null)
    setStockApplyResults(null)
    setStockApplyProgress(null)
    try {
      const data = await getAddStockPreview(toolId, selectedCode)
      setStockPreviewData(data)
      setStockChecked(new Set(data.items.map((it) => it.sku)))
    } catch (e) {
      setStockPreviewError(String(e))
    } finally {
      setStockPreviewLoading(false)
    }
  }

  async function handleConfirmApply() {
    if (!stockPreviewData || !selectedCode) return
    setStockApplying(true)
    setStockApplyResults(null)

    const items = stockPreviewData.items.filter((it) => stockChecked.has(it.sku))
    const results: { sku: string; success: boolean; newStock?: number; error?: string }[] = []

    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      setStockApplyProgress({ done: i, total: items.length, currentSku: item.sku })
      try {
        const res = await updateInventoryStock(
          toolId, item.sku, item.billbeeId,
          item.qty, undefined,
          `B2B order ${selectedCode}`,
        )
        results.push({ sku: item.sku, success: true, newStock: res.newStock })
      } catch (e) {
        results.push({ sku: item.sku, success: false, error: String(e) })
      }
    }

    setStockApplyProgress(null)
    setStockApplyResults(results)
    setStockApplying(false)
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

          {/* Step 3b — Fill cart only */}
          <button
            onClick={() => run(`${mfr.pythonCmd} execution/b2b_cart.py fill-cart --manufacturer ${mfr.code}`)}
            className="flex-1 px-3 py-2 rounded font-semibold text-left transition-colors"
            style={{
              background: '#1c2a1c',
              border: '2px solid #2d4a2d',
              color: '#86efac',
              minWidth: '140px',
            }}
          >
            <div className="text-xs font-semibold mb-0.5">3b. Warenkorb füllen</div>
            <div className="text-xs font-normal" style={{ color: 'inherit', opacity: 0.7 }}>
              letzten Order-Tab erneut in den Warenkorb laden
            </div>
          </button>

          {/* Step 4 — Update Billbee stock */}
          <button
            onClick={handleAddStock}
            disabled={stockPreviewLoading || !selectedCode}
            className="flex-1 px-3 py-2 rounded font-semibold text-left transition-colors"
            style={{
              background: stockPreviewLoading ? '#2a1f3a' : '#1e1a3a',
              border: `2px solid ${stockPreviewLoading ? '#6d28d9' : '#7c3aed'}`,
              color: stockPreviewLoading ? '#c4b5fd' : '#a78bfa',
              minWidth: '140px',
              opacity: (!selectedCode) ? 0.5 : 1,
              cursor: (stockPreviewLoading || !selectedCode) ? 'default' : 'pointer',
            }}
          >
            <div className="text-xs font-semibold mb-0.5">
              {stockPreviewLoading ? '4. Lädt Vorschau…' : '4. Lagerbestand in Billbee buchen'}
            </div>
            <div className="text-xs font-normal" style={{ color: 'inherit', opacity: 0.7 }}>
              bestellte Mengen aus neuestem Order-Tab zu Billbee-Lager hinzufügen
            </div>
          </button>
        </div>
      )}

      {/* Add-stock error (outside modal, if preview itself failed) */}
      {stockPreviewError && (
        <div className="mt-4 p-3 rounded text-xs" style={{ background: '#2d1515', border: '1px solid #7f1d1d' }}>
          <div className="font-semibold mb-1" style={{ color: '#f87171' }}>Fehler beim Laden der Vorschau</div>
          <div className="font-mono break-all" style={{ color: '#fca5a5' }}>{stockPreviewError}</div>
        </div>
      )}

      {/* Add-stock modal — confirm → applying → results */}
      {stockPreviewData && (() => {
        const phase = stockApplying ? 'applying' : stockApplyResults !== null ? 'results' : 'confirm'
        const successCount = stockApplyResults?.filter((r) => r.success).length ?? 0
        const failCount    = stockApplyResults?.filter((r) => !r.success).length ?? 0

        return (
          <div
            className="fixed inset-0 flex items-center justify-center z-50"
            style={{ background: 'rgba(0,0,0,0.75)' }}
            onClick={(e) => {
              if (e.target === e.currentTarget && phase === 'confirm')
                setStockPreviewData(null)
            }}
          >
            <div
              className="rounded-lg p-5 mx-4"
              style={{
                background: '#252526', border: '1px solid #3e3e3e',
                width: '100%', maxWidth: '720px', maxHeight: '85vh',
                display: 'flex', flexDirection: 'column',
              }}
            >
              {/* Header */}
              <div className="text-sm font-semibold mb-0.5" style={{ color: '#d4d4d4' }}>
                Lagerbestand in Billbee buchen
              </div>
              <div className="text-xs mb-3" style={{ color: '#858585' }}>
                Tab: <span style={{ color: '#9ca3af' }}>{stockPreviewData.tab}</span>
                {' · '}{stockPreviewData.items.length} Artikel
              </div>

              {/* ── Phase: confirm ── */}
              {phase === 'confirm' && (
                <>
                  {stockPreviewData.items.length === 0 ? (
                    <div className="text-xs py-4 text-center" style={{ color: '#6b7280' }}>
                      Keine Artikel zum Einbuchen (keine Zeilen mit «add to Billbee stock» angehakt und Qty &gt; 0).
                    </div>
                  ) : (
                    <div className="overflow-auto flex-1 mb-3" style={{ maxHeight: '380px' }}>
                      <table className="w-full text-xs border-collapse">
                        <thead>
                          <tr style={{ borderBottom: '1px solid #3e3e3e' }}>
                            <th className="px-2 py-1.5">
                              <input
                                type="checkbox"
                                checked={stockPreviewData.items.every((it) => stockChecked.has(it.sku))}
                                onChange={(e) => setStockChecked(
                                  e.target.checked
                                    ? new Set(stockPreviewData.items.map((it) => it.sku))
                                    : new Set()
                                )}
                                style={{ cursor: 'pointer', accentColor: '#7c3aed' }}
                              />
                            </th>
                            {['Billbee akt.', 'Sheet akt.', 'Ziel', 'Bestellt', 'Neu in Billbee', 'SKU'].map((h) => (
                              <th key={h} className="px-2 py-1.5 font-semibold"
                                style={{ color: '#858585', whiteSpace: 'nowrap', textAlign: h === 'SKU' ? 'left' : 'right' }}>
                                {h}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {stockPreviewData.items.map((item) => {
                            const checked = stockChecked.has(item.sku)
                            return (
                              <tr
                                key={item.sku}
                                style={{ borderBottom: '1px solid #2d2d2d', opacity: checked ? 1 : 0.35, cursor: 'pointer' }}
                                onClick={() => setStockChecked((prev) => {
                                  const next = new Set(prev)
                                  checked ? next.delete(item.sku) : next.add(item.sku)
                                  return next
                                })}
                              >
                                <td className="px-2 py-1.5 text-center">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => {}}
                                    style={{ cursor: 'pointer', accentColor: '#7c3aed' }}
                                  />
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono" style={{ color: '#d4d4d4' }}>
                                  {item.billbeeStock ?? '?'}
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono" style={{ color: '#9ca3af' }}>
                                  {item.sheetStockCurrent ?? '?'}
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono" style={{ color: '#9ca3af' }}>
                                  {item.sheetStockTarget ?? '?'}
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono font-semibold" style={{ color: '#60a5fa' }}>
                                  +{item.qty}
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono font-semibold" style={{ color: '#4ade80' }}>
                                  {item.newStock ?? '?'}
                                </td>
                                <td className="px-2 py-1.5 font-mono" style={{ color: '#d4d4d4' }}>{item.sku}</td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {stockPreviewData.errors.length > 0 && (
                    <div className="mb-3 p-2 rounded text-xs" style={{ background: '#2d1f0a', border: '1px solid #92400e' }}>
                      <div className="font-semibold mb-1" style={{ color: '#f97316' }}>Warnungen</div>
                      {stockPreviewData.errors.map((e, i) => (
                        <div key={i} style={{ color: '#fed7aa' }}>
                          {e.sku && <span className="font-mono">{e.sku}: </span>}{e.error}
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center gap-3 justify-end shrink-0">
                    <button onClick={() => setStockPreviewData(null)}
                      className="px-4 py-1.5 rounded text-xs"
                      style={{ background: '#3e3e3e', color: '#d4d4d4', cursor: 'pointer' }}>
                      Abbrechen
                    </button>
                    <button onClick={handleConfirmApply}
                      disabled={stockChecked.size === 0}
                      className="px-4 py-1.5 rounded text-xs font-semibold"
                      style={{
                        background: stockChecked.size === 0 ? '#3b2a6a' : '#7c3aed',
                        color: '#fff',
                        cursor: stockChecked.size === 0 ? 'default' : 'pointer',
                      }}>
                      Bestätigen ({stockChecked.size})
                    </button>
                  </div>
                </>
              )}

              {/* ── Phase: applying ── */}
              {phase === 'applying' && (
                <div className="flex-1 flex flex-col justify-center py-6">
                  {stockApplyProgress ? (
                    <>
                      <div className="text-xs mb-3 text-center" style={{ color: '#9ca3af' }}>
                        Artikel {stockApplyProgress.done + 1} / {stockApplyProgress.total}
                      </div>
                      <div style={{ height: '2px', background: '#2d2d2d', borderRadius: '1px', marginBottom: '12px' }}>
                        <div style={{
                          height: '100%', borderRadius: '1px', background: '#7c3aed',
                          width: `${Math.round((stockApplyProgress.done / stockApplyProgress.total) * 100)}%`,
                          transition: 'width 0.2s ease',
                        }} />
                      </div>
                      <div className="text-xs text-center font-mono" style={{ color: '#d4d4d4' }}>
                        {stockApplyProgress.currentSku}
                      </div>
                    </>
                  ) : (
                    <div className="text-xs text-center" style={{ color: '#9ca3af' }}>Startet…</div>
                  )}
                </div>
              )}

              {/* ── Phase: results ── */}
              {phase === 'results' && stockApplyResults && (
                <>
                  {/* Summary bar */}
                  <div className="flex items-center gap-4 mb-3 px-1">
                    <span className="text-xs font-semibold" style={{ color: '#4ade80' }}>
                      ✓ {successCount} aktualisiert
                    </span>
                    {failCount > 0 && (
                      <span className="text-xs font-semibold" style={{ color: '#f87171' }}>
                        ✗ {failCount} Fehler
                      </span>
                    )}
                  </div>
                  <div className="overflow-auto flex-1 mb-3" style={{ maxHeight: '380px' }}>
                    <table className="w-full text-xs border-collapse">
                      <thead>
                        <tr style={{ borderBottom: '1px solid #3e3e3e' }}>
                          <th className="px-2 py-1.5 text-left font-semibold" style={{ color: '#858585' }}>SKU</th>
                          <th className="px-2 py-1.5 text-right font-semibold" style={{ color: '#858585' }}>Neu in Billbee</th>
                          <th className="px-2 py-1.5 text-left font-semibold" style={{ color: '#858585' }}>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {stockApplyResults.map((r) => (
                          <tr key={r.sku} style={{ borderBottom: '1px solid #2d2d2d' }}>
                            <td className="px-2 py-1.5 font-mono" style={{ color: '#d4d4d4' }}>{r.sku}</td>
                            <td className="px-2 py-1.5 text-right font-mono font-semibold"
                              style={{ color: r.success ? '#4ade80' : '#6b7280' }}>
                              {r.success && r.newStock !== undefined ? r.newStock : '—'}
                            </td>
                            <td className="px-2 py-1.5 text-xs" style={{ color: r.success ? '#4ade80' : '#f87171' }}>
                              {r.success ? '✓ OK' : `✗ ${r.error ?? 'Fehler'}`}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="flex items-center gap-3 justify-end shrink-0">
                    <button
                      onClick={() => {
                        prefillInventoryImport({
                          toolId,
                          manufacturer: selectedCode,
                          sheet: `${selectedCode} Orders`,
                          tab: stockPreviewData.tab,
                          skuCol: 'SKU',
                          qtyCol: 'Qty',
                          mode: 'add',
                        })
                        openTab({ id: `panel-${toolId}-inventory`, type: 'inventory', title: 'Lagerbestand', toolId })
                        setStockPreviewData(null); setStockApplyResults(null)
                      }}
                      className="px-4 py-1.5 rounded text-xs font-semibold"
                      style={{ background: '#2d1f4a', border: '1px solid #7c3aed', color: '#c4b5fd', cursor: 'pointer' }}>
                      In Lagerbestand öffnen →
                    </button>
                    <button
                      onClick={() => { setStockPreviewData(null); setStockApplyResults(null) }}
                      className="px-4 py-1.5 rounded text-xs font-semibold"
                      style={{ background: '#14532d', border: '1px solid #16a34a', color: '#4ade80', cursor: 'pointer' }}>
                      Schließen
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        )
      })()}

      <div className="mt-6 pt-3" style={{ borderTop: '1px solid #3e3e3e' }}>
        <div className="text-xs font-mono" style={{ color: '#4b5563' }}>{toolPath}</div>
      </div>
    </div>
  )
}
