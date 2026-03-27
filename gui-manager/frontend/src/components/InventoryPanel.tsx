import { useEffect, useMemo, useState } from 'react'
import {
  getInventoryManufacturers, getInventoryProducts, getInventoryProductsFromBillbee,
  queryInventoryStock, updateInventoryStock, getSheetImport, getSheetTabs,
  InventoryProduct, SheetImportItem,
} from '../api'
import MultiSelect from './MultiSelect'

interface Props {
  toolId: string
}

// ---------------------------------------------------------------------------
// Module-level caches — survive tab switches
// ---------------------------------------------------------------------------
interface FilterState { cat: Set<string>; size: Set<string>; color: Set<string>; variant: Set<string> }
const emptyFilters = (): FilterState => ({ cat: new Set(), size: new Set(), color: new Set(), variant: new Set() })

const productCache:  Record<string, InventoryProduct[]>          = {}
const stockCache:    Record<string, Record<string, number|null>> = {}
const pendingCache:  Record<string, Record<string, number>>      = {}
const mfrSelCache:   Record<string, Set<string>>                 = {}
const filterCache:   Record<string, FilterState>                 = {}

interface ImportFormState { manufacturer: string; sheet: string; tab: string; skuCol: string; qtyCol: string; mode: string }
const importFormCache: Record<string, ImportFormState> = {}

// External prefill — called from other panels (e.g. ReorderPanel) before navigating here
interface PendingPrefill extends ImportFormState { toolId: string }
let _pendingPrefill: PendingPrefill | null = null
export function prefillInventoryImport(p: PendingPrefill) {
  _pendingPrefill = p
  importFormCache[p.toolId] = p
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getStockColor(current: number, target: number | null): string {
  if (target === null || target === 0) return '#d4d4d4'
  const r = current / target
  if (r > 1.2) return '#4ade80'
  if (r > 0.8) return '#facc15'
  if (r > 0.2) return '#f97316'
  return '#f87171'
}

// ---------------------------------------------------------------------------
// Progress bar components
// ---------------------------------------------------------------------------
function IndeterminateBar({ color = '#007acc' }: { color?: string }) {
  return (
    <>
      <style>{`@keyframes inv-slide{0%{left:-40%}100%{left:110%}}`}</style>
      <div style={{ height: '2px', background: '#2d2d2d', position: 'relative', overflow: 'hidden' }}>
        <div style={{
          position: 'absolute', height: '100%', width: '40%',
          background: color, animation: 'inv-slide 1.2s ease-in-out infinite',
        }} />
      </div>
    </>
  )
}

function DeterminateBar({ done, total, color = '#007acc' }: { done: number; total: number; color?: string }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div style={{ height: '2px', background: '#2d2d2d' }}>
      <div style={{ height: '100%', width: `${pct}%`, background: color, transition: 'width 0.15s ease' }} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Confirmation modal
// ---------------------------------------------------------------------------
interface ConfirmModalProps {
  pending: Record<string, number>
  stockMap: Record<string, number | null>
  products: InventoryProduct[]
  onConfirm: () => void
  onCancel: () => void
  saving: boolean
  saveProgress: { done: number; total: number } | null
  saveError: string
}

function ConfirmModal({ pending, stockMap, products, onConfirm, onCancel, saving, saveProgress, saveError }: ConfirmModalProps) {
  const skuMap = Object.fromEntries(products.map((p) => [p.sku, p]))
  const entries = Object.entries(pending)
  return (
    <div
      className="fixed inset-0 flex items-center justify-center z-50"
      style={{ background: 'rgba(0,0,0,0.75)' }}
      onClick={(e) => { if (e.target === e.currentTarget && !saving) onCancel() }}
    >
      <div className="rounded-lg p-5 w-full max-w-lg mx-4" style={{ background: '#252526', border: '1px solid #3e3e3e', maxHeight: '80vh', display: 'flex', flexDirection: 'column' }}>
        <div className="text-sm font-semibold mb-1" style={{ color: '#d4d4d4' }}>
          Lagerbestand speichern
        </div>
        <div className="text-xs mb-3" style={{ color: '#858585' }}>
          Folgende Änderungen werden in Billbee gespeichert:
        </div>

        <div className="overflow-y-auto flex-1 mb-3" style={{ maxHeight: '300px' }}>
          {entries.map(([sku, newQty], idx) => {
            const current = stockMap[sku]
            const p = skuMap[sku]
            const saved = saveProgress && idx < saveProgress.done
            return (
              <div
                key={sku}
                className="flex items-center gap-3 py-1.5 text-xs"
                style={{ borderBottom: '1px solid #2d2d2d', opacity: saved ? 0.45 : 1 }}
              >
                <span className="font-mono flex-1 truncate" style={{ color: '#d4d4d4' }} title={sku}>{sku}</span>
                {p?.title && <span className="truncate max-w-[140px]" style={{ color: '#6b7280' }}>{p.title}</span>}
                <span className="shrink-0 font-semibold" style={{ color: '#f97316' }}>
                  {current !== null && current !== undefined ? current : '?'}
                </span>
                <span style={{ color: '#4b5563' }}>→</span>
                <span className="shrink-0 font-semibold" style={{ color: '#4ade80' }}>{newQty}</span>
              </div>
            )
          })}
        </div>

        {/* Save progress bar */}
        {saving && saveProgress && (
          <div className="mb-3">
            <DeterminateBar done={saveProgress.done} total={saveProgress.total} color="#4ade80" />
            <div className="text-xs mt-1" style={{ color: '#858585' }}>
              {saveProgress.done} / {saveProgress.total} gespeichert
            </div>
          </div>
        )}

        {saveError && (
          <div className="mb-3 text-xs px-3 py-2 rounded" style={{ background: '#2d1515', color: '#f87171', whiteSpace: 'pre-wrap' }}>
            {saveError}
          </div>
        )}

        <div className="flex items-center gap-3 justify-end">
          <button
            onClick={onCancel}
            disabled={saving}
            className="px-4 py-1.5 rounded text-xs"
            style={{ background: '#3e3e3e', color: saving ? '#858585' : '#d4d4d4', cursor: saving ? 'default' : 'pointer' }}
          >
            Abbrechen
          </button>
          <button
            onClick={onConfirm}
            disabled={saving}
            className="px-4 py-1.5 rounded text-xs font-semibold"
            style={{ background: saving ? '#004f8c' : '#007acc', color: '#fff', cursor: saving ? 'default' : 'pointer' }}
          >
            {saving
              ? (saveProgress ? `Speichert… (${saveProgress.done}/${saveProgress.total})` : 'Speichert…')
              : `Bestätigen (${entries.length})`
            }
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------
export default function InventoryPanel({ toolId }: Props) {
  const [allManufacturers, setAllManufacturers] = useState<string[]>([])
  const [selectedManufacturers, setSelectedManufacturers] = useState<Set<string>>(
    () => mfrSelCache[toolId] ?? new Set()
  )

  const [products, setProducts]   = useState<InventoryProduct[]>(() => productCache[toolId] ?? [])
  const [stockMap, setStockMap]   = useState<Record<string, number | null>>(() => stockCache[toolId] ?? {})
  const [pending, setPending]     = useState<Record<string, number>>(() => pendingCache[toolId] ?? {})
  const [sheetErrors, setSheetErrors] = useState<{ manufacturer: string; error: string }[]>([])

  const [loadingProducts, setLoadingProducts] = useState(false)
  const [loadingBillbee, setLoadingBillbee]   = useState(false)
  const [loadError, setLoadError]             = useState('')

  const cached = filterCache[toolId] ?? emptyFilters()
  const [filterCat,     setFilterCat]     = useState<Set<string>>(() => cached.cat)
  const [filterSize,    setFilterSize]    = useState<Set<string>>(() => cached.size)
  const [filterColor,   setFilterColor]   = useState<Set<string>>(() => cached.color)
  const [filterVariant, setFilterVariant] = useState<Set<string>>(() => cached.variant)

  const [loadingStock, setLoadingStock] = useState(false)
  const [stockError, setStockError]     = useState('')

  const [selected, setSelected]   = useState<InventoryProduct | null>(null)
  const [editValue, setEditValue] = useState('')

  const [showModal, setShowModal]         = useState(false)
  const [modalSaving, setModalSaving]     = useState(false)
  const [saveProgress, setSaveProgress]   = useState<{ done: number; total: number } | null>(null)
  const [modalError, setModalError]       = useState('')

  // Sheet import form + modal
  const SHEET_HISTORY_KEY = 'upstitch:sheetImportHistory'
  const MAX_SHEET_HISTORY = 15
  function loadSheetHistory(): string[] {
    try { return JSON.parse(localStorage.getItem(SHEET_HISTORY_KEY) ?? '[]') } catch { return [] }
  }
  function addToSheetHistory(sheet: string) {
    if (!sheet.trim()) return
    const prev = loadSheetHistory().filter((s) => s !== sheet)
    localStorage.setItem(SHEET_HISTORY_KEY, JSON.stringify([sheet, ...prev].slice(0, MAX_SHEET_HISTORY)))
    setSheetHistory([sheet, ...prev].slice(0, MAX_SHEET_HISTORY))
  }

  const _imp = () => importFormCache[toolId] ?? { manufacturer: '', sheet: '', tab: '', skuCol: 'SKU', qtyCol: 'Qty', mode: 'add' }
  const [importOpen,        setImportOpen]        = useState(false)
  const [importMfr,         setImportMfr]         = useState(() => _imp().manufacturer)
  const [importSheet,       setImportSheet]       = useState(() => _imp().sheet)
  const [importTab,         setImportTab]         = useState(() => _imp().tab)
  const [importSkuCol,      setImportSkuCol]      = useState(() => _imp().skuCol)
  const [importQtyCol,      setImportQtyCol]      = useState(() => _imp().qtyCol)
  const [importMode,        setImportMode]        = useState(() => _imp().mode)
  const [sheetHistory,      setSheetHistory]      = useState<string[]>(() => loadSheetHistory())
  const [availableTabs,     setAvailableTabs]     = useState<string[]>([])
  const [tabsLoading,       setTabsLoading]       = useState(false)
  const [importLoading,      setImportLoading]      = useState(false)
  const [importStockLoading, setImportStockLoading] = useState(false)
  const [importError,        setImportError]        = useState('')
  const [importPreviewData,  setImportPreviewData]  = useState<{ items: SheetImportItem[]; errors: { sku?: string; error: string }[] } | null>(null)
  const [importChecked,      setImportChecked]      = useState<Set<string>>(new Set())
  const [importPhase,        setImportPhase]        = useState<'confirm' | 'applying' | 'results'>('confirm')
  const [importProgress,     setImportProgress]     = useState<{ done: number; total: number; currentSku: string } | null>(null)
  const [importResults,      setImportResults]      = useState<{ sku: string; success: boolean; newStock?: number; error?: string }[] | null>(null)
  const [importVerifying,    setImportVerifying]    = useState(false)
  const [importVerifyStocks, setImportVerifyStocks] = useState<Record<string, number | null>>({})

  // Helpers to update caches alongside state
  function setAndCacheMfr(next: Set<string>) { mfrSelCache[toolId] = next; setSelectedManufacturers(next) }
  function setAndCacheFilter(field: keyof FilterState, next: Set<string>) {
    if (!filterCache[toolId]) filterCache[toolId] = emptyFilters()
    filterCache[toolId][field] = next
    if (field === 'cat')     setFilterCat(next)
    if (field === 'size')    setFilterSize(next)
    if (field === 'color')   setFilterColor(next)
    if (field === 'variant') setFilterVariant(next)
  }
  function setAndCachePending(next: Record<string, number>) { pendingCache[toolId] = next; setPending(next) }

  // Load manufacturer list
  useEffect(() => {
    getInventoryManufacturers(toolId)
      .then((codes) => {
        setAllManufacturers(codes)
        if (!mfrSelCache[toolId]) setAndCacheMfr(new Set())
      })
      .catch((e) => setLoadError(String(e)))
  }, [toolId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Drain external prefill (e.g. from ReorderPanel)
  useEffect(() => {
    if (!_pendingPrefill || _pendingPrefill.toolId !== toolId) return
    const p = _pendingPrefill
    _pendingPrefill = null
    setImportMfr(p.manufacturer)
    setImportSheet(p.sheet)
    setImportTab(p.tab)
    setImportSkuCol(p.skuCol)
    setImportQtyCol(p.qtyCol)
    setImportMode(p.mode)
    setImportOpen(true)
    setAvailableTabs(p.tab ? [p.tab] : [])
    // Fetch full tab list in background
    if (p.sheet) getSheetTabs(toolId, p.sheet).then((d) => setAvailableTabs(d.tabs)).catch(() => {})
  }) // no dep array — runs after every render; exits immediately once drained

  // Derived filter options
  const uniqueCategories = useMemo(() => [...new Set(products.map((p) => p.category).filter(Boolean))].sort(), [products])
  const uniqueSizes      = useMemo(() => [...new Set(products.map((p) => p.size).filter(Boolean))].sort(), [products])
  const uniqueColors     = useMemo(() => [...new Set(products.map((p) => p.color).filter(Boolean))].sort(), [products])
  const uniqueVariants   = useMemo(() => [...new Set(products.map((p) => p.variant).filter(Boolean))].sort(), [products])

  const filteredProducts = useMemo(() => {
    return products
      .filter((p) => {
        if (filterCat.size     > 0 && !filterCat.has(p.category))   return false
        if (filterSize.size    > 0 && !filterSize.has(p.size))       return false
        if (filterColor.size   > 0 && !filterColor.has(p.color))     return false
        if (filterVariant.size > 0 && !filterVariant.has(p.variant)) return false
        return true
      })
      .sort((a, b) => a.sku.localeCompare(b.sku))
  }, [products, filterCat, filterSize, filterColor, filterVariant])

  const hasPending   = Object.keys(pending).length > 0
  const pendingCount = Object.keys(pending).length

  async function handleUpdate() {
    if (selectedManufacturers.size === 0) return
    setLoadingProducts(true)
    setLoadError(''); setSheetErrors([])
    setProducts([]); setStockMap({}); setAndCachePending({})
    productCache[toolId] = []; stockCache[toolId] = {}
    setSelected(null); setEditValue('')
    try {
      const data = await getInventoryProducts(toolId, [...selectedManufacturers])
      productCache[toolId] = data.products
      stockCache[toolId] = {}
      setProducts(data.products); setStockMap({})
      setSheetErrors(data.errors ?? [])
    } catch (e) {
      setLoadError(String(e))
    } finally {
      setLoadingProducts(false)
    }
  }

  async function handleUpdateFromBillbee() {
    if (selectedManufacturers.size === 0) return
    setLoadingBillbee(true)
    setLoadError(''); setSheetErrors([])
    setProducts([]); setStockMap({}); setAndCachePending({})
    productCache[toolId] = []; stockCache[toolId] = {}
    setSelected(null); setEditValue('')
    try {
      const data = await getInventoryProductsFromBillbee(toolId, [...selectedManufacturers])
      productCache[toolId] = data.products
      stockCache[toolId] = {}
      setProducts(data.products); setStockMap({})
      setSheetErrors(data.errors ?? [])
    } catch (e) {
      setLoadError(String(e))
    } finally {
      setLoadingBillbee(false)
    }
  }

  async function handleFetchStock() {
    if (filteredProducts.length === 0) return
    setLoadingStock(true); setStockError('')
    try {
      const toQuery = filteredProducts.map((p) => ({ sku: p.sku, billbeeId: p.billbeeId }))
      const data = await queryInventoryStock(toolId, toQuery)
      setStockMap((prev) => {
        const next = { ...prev }
        for (const [sku, result] of Object.entries(data.stocks)) next[sku] = result.stock
        stockCache[toolId] = next
        return next
      })
    } catch (e) {
      setStockError(String(e))
    } finally {
      setLoadingStock(false)
    }
  }

  function handleSelect(p: InventoryProduct) {
    setSelected(p)
    const pendingVal = pending[p.sku]
    if (pendingVal !== undefined) {
      setEditValue(String(pendingVal))
    } else {
      const stock = stockMap[p.sku]
      setEditValue(p.sku in stockMap && stock !== null ? String(stock) : '')
    }
  }

  function handleStage() {
    if (!selected) return
    const newQty = parseFloat(editValue)
    if (isNaN(newQty)) return
    const currentStock = stockMap[selected.sku]
    if (currentStock !== null && currentStock !== undefined && newQty === currentStock) {
      const next = { ...pending }
      delete next[selected.sku]
      setAndCachePending(next)
    } else {
      setAndCachePending({ ...pending, [selected.sku]: newQty })
    }
  }

  function handleRemovePending(sku: string) {
    const next = { ...pending }
    delete next[sku]
    setAndCachePending(next)
    if (selected?.sku === sku) {
      const stock = stockMap[sku]
      setEditValue(sku in stockMap && stock !== null ? String(stock) : '')
    }
  }

  function handleResetPending() {
    if (!window.confirm(`Alle ${pendingCount} ausstehenden Änderungen verwerfen?`)) return
    setAndCachePending({})
  }

  async function handleConfirmSave() {
    setModalSaving(true); setModalError('')
    const entries = Object.entries(pending)
    setSaveProgress({ done: 0, total: entries.length })
    const productMap = Object.fromEntries(products.map((p) => [p.sku, p]))
    const newStocks: Record<string, number> = {}
    const errors: string[] = []
    for (let i = 0; i < entries.length; i++) {
      const [sku, newQty] = entries[i]
      const p = productMap[sku]
      if (!p) { errors.push(`${sku}: product not found`); setSaveProgress({ done: i + 1, total: entries.length }); continue }
      try {
        const result = await updateInventoryStock(toolId, sku, p.billbeeId, undefined, newQty)
        newStocks[sku] = result.newStock
      } catch (e) {
        errors.push(`${sku}: ${String(e)}`)
      }
      setSaveProgress({ done: i + 1, total: entries.length })
    }
    if (errors.length > 0) {
      setModalError(errors.join('\n'))
      setModalSaving(false)
      setSaveProgress(null)
      return
    }
    setStockMap((prev) => {
      const next = { ...prev, ...newStocks }
      stockCache[toolId] = next
      return next
    })
    setAndCachePending({})
    if (selected && newStocks[selected.sku] !== undefined) {
      setEditValue(String(newStocks[selected.sku]))
    }
    setModalSaving(false)
    setSaveProgress(null)
    setShowModal(false)
  }

  function saveImportForm() {
    importFormCache[toolId] = { manufacturer: importMfr, sheet: importSheet, tab: importTab, skuCol: importSkuCol, qtyCol: importQtyCol, mode: importMode }
  }

  async function fetchTabsForSheet(sheet: string) {
    if (!sheet.trim()) { setAvailableTabs([]); return }
    setTabsLoading(true)
    try {
      const data = await getSheetTabs(toolId, sheet)
      setAvailableTabs(data.tabs)
      // Auto-select tab if only one exists or previously selected tab is no longer valid
      if (data.tabs.length > 0 && !data.tabs.includes(importTab)) {
        setImportTab(data.tabs[data.tabs.length - 1]) // default to last tab (usually newest)
      }
    } catch {
      setAvailableTabs([])
    } finally {
      setTabsLoading(false)
    }
  }

  async function handleImportPreview() {
    saveImportForm()
    setImportLoading(true); setImportError('')
    setImportPreviewData(null); setImportResults(null); setImportProgress(null); setImportVerifyStocks({})
    try {
      // Step 1: read sheet + resolve billbeeIds — fast (~2 s), shows table immediately
      const data = await getSheetImport(toolId, importMfr, importSheet, importTab, importSkuCol, importQtyCol)
      addToSheetHistory(importSheet)
      setImportPreviewData(data)
      setImportChecked(new Set(data.items.map((i) => i.sku)))
      setImportPhase('confirm')
      setImportLoading(false)

      // Step 2: fetch live Billbee stock — fills in the Billbee column
      if (data.items.length > 0) {
        setImportStockLoading(true)
        try {
          const stockData = await queryInventoryStock(toolId, data.items.map((i) => ({ sku: i.sku, billbeeId: i.billbeeId })))
          const updatedItems = data.items.map((item) => ({
            ...item,
            billbeeStock: stockData.stocks[item.sku]?.stock ?? null,
          }))
          setImportPreviewData({ ...data, items: updatedItems })
        } catch (e) {
          setImportError(`Billbee Lagerbestand konnte nicht geladen werden: ${String(e)}`)
        } finally {
          setImportStockLoading(false)
        }
      }
    } catch (e) {
      setImportError(String(e))
      setImportLoading(false)
    }
  }

  async function handleImportApply() {
    if (!importPreviewData) return
    const toApply = importPreviewData.items.filter((i) => importChecked.has(i.sku))
    setImportPhase('applying')
    setImportResults([])
    setImportProgress({ done: 0, total: toApply.length, currentSku: toApply[0]?.sku ?? '' })
    const results: { sku: string; success: boolean; newStock?: number; error?: string }[] = []
    for (let i = 0; i < toApply.length; i++) {
      const item = toApply[i]
      setImportProgress({ done: i, total: toApply.length, currentSku: item.sku })
      try {
        const delta  = importMode === 'add' ? item.qty : importMode === 'subtract' ? -item.qty : undefined
        const newQty = importMode === 'set' ? item.qty : undefined
        const result = await updateInventoryStock(toolId, item.sku, item.billbeeId, delta, newQty, `GUI: sheet import (${importMode})`)
        results.push({ sku: item.sku, success: true, newStock: result.newStock })
      } catch (e) {
        results.push({ sku: item.sku, success: false, error: String(e) })
      }
      setImportResults([...results])
    }
    setImportProgress({ done: toApply.length, total: toApply.length, currentSku: '' })
    setImportPhase('results')
    const newStocks: Record<string, number> = {}
    for (const r of results) {
      if (r.success && r.newStock !== undefined) newStocks[r.sku] = r.newStock
    }
    if (Object.keys(newStocks).length > 0) {
      setStockMap((prev) => {
        const next = { ...prev, ...newStocks }
        stockCache[toolId] = next
        return next
      })
    }
  }

  async function handleImportVerify() {
    if (!importResults || !importPreviewData) return
    const productMap = Object.fromEntries(importPreviewData.items.map((i) => [i.sku, i]))
    // Pre-fill all result items with null so the column shows "…" immediately
    const pending: Record<string, number | null> = {}
    for (const r of importResults) pending[r.sku] = null
    setImportVerifyStocks(pending)
    setImportVerifying(true)
    try {
      const toQuery = importResults.map((r) => ({ sku: r.sku, billbeeId: productMap[r.sku].billbeeId }))
      const stockData = await queryInventoryStock(toolId, toQuery)
      const verified: Record<string, number | null> = {}
      for (const r of importResults) verified[r.sku] = stockData.stocks[r.sku]?.stock ?? null
      setImportVerifyStocks(verified)
    } catch (e) {
      setImportError(`Prüfung fehlgeschlagen: ${String(e)}`)
    } finally {
      setImportVerifying(false)
    }
  }

  function adjustEdit(delta: number) {
    const current = parseFloat(editValue)
    setEditValue(String(isNaN(current) ? delta : current + delta))
  }

  const stockFetched         = (sku: string) => sku in stockMap
  const selectedFetched      = selected ? stockFetched(selected.sku) : false
  const currentStock         = selected && selectedFetched ? stockMap[selected.sku] : null
  const canEdit              = selectedFetched && currentStock !== null
  const pendingQty           = editValue !== '' ? parseFloat(editValue) : null
  const pendingDelta         = (pendingQty !== null && !isNaN(pendingQty) && currentStock !== null)
    ? pendingQty - currentStock : null
  const isPendingForSelected = selected ? selected.sku in pending : false
  const hasPendingChange     = pendingQty !== null && !isNaN(pendingQty) &&
    (currentStock === null || pendingQty !== currentStock) &&
    pendingQty !== (selected ? pending[selected.sku] : undefined)

  const hasProducts = products.length > 0
  const isLoading   = loadingProducts || loadingBillbee

  return (
    <div className="h-full flex flex-col overflow-hidden" style={{ background: '#1e1e1e' }}>

      {/* Header */}
      <div className="px-4 pt-4 pb-3 shrink-0">
        <div className="flex items-center gap-3 mb-1 flex-wrap">
          <h1 className="text-base font-semibold text-vscode-text shrink-0">Lagerbestand</h1>
          <MultiSelect
            label="Hersteller"
            options={allManufacturers}
            selected={selectedManufacturers}
            onChange={setAndCacheMfr}
            minWidth="140px"
          />
          <button
            onClick={handleUpdate}
            disabled={isLoading || selectedManufacturers.size === 0}
            className="px-3 py-1 rounded text-xs font-semibold"
            style={{
              background: (isLoading || selectedManufacturers.size === 0) ? '#3e3e3e' : '#007acc',
              color:      (isLoading || selectedManufacturers.size === 0) ? '#858585' : '#fff',
              cursor:     (isLoading || selectedManufacturers.size === 0) ? 'default' : 'pointer',
            }}
          >
            {loadingProducts ? 'Lädt…' : 'Update vom Google Sheet (schnell)'}
          </button>
          <button
            onClick={handleUpdateFromBillbee}
            disabled={isLoading || selectedManufacturers.size === 0}
            className="px-3 py-1 rounded text-xs font-semibold"
            style={{
              background: (isLoading || selectedManufacturers.size === 0) ? '#3e3e3e' : '#1a3a5c',
              border:     `1px solid ${(isLoading || selectedManufacturers.size === 0) ? '#3e3e3e' : '#2472c8'}`,
              color:      (isLoading || selectedManufacturers.size === 0) ? '#858585' : '#60a5fa',
              cursor:     (isLoading || selectedManufacturers.size === 0) ? 'default' : 'pointer',
            }}
          >
            {loadingBillbee ? 'Lädt von Billbee…' : 'Update von Billbee (langsam)'}
          </button>
          <button
            onClick={() => setImportOpen((v) => !v)}
            className="px-3 py-1 rounded text-xs font-semibold"
            style={{
              background: importOpen ? '#3b1d5c' : '#2d1f4a',
              border: `1px solid ${importOpen ? '#7c3aed' : '#4c2a8a'}`,
              color: importOpen ? '#c4b5fd' : '#a78bfa',
            }}
          >
            {importOpen ? '▲ Sheet importieren' : '▼ Sheet importieren'}
          </button>
          {hasProducts && !isLoading && (
            <span className="ml-auto text-xs" style={{ color: '#4b5563' }}>
              {filteredProducts.length} / {products.length}
            </span>
          )}
        </div>
        <p className="text-vscode-muted text-xs">Lagerbestand aus Billbee Artikelmanager Sheets</p>
      </div>

      {/* Product-load progress bar */}
      {loadingProducts && <IndeterminateBar color="#007acc" />}
      {loadingBillbee  && <IndeterminateBar color="#2472c8" />}

      {/* Sheet import form */}
      {importOpen && (
        <>
          <div style={{ height: '1px', background: '#3e3e3e', margin: '0 16px' }} />
          <div className="px-4 py-3 shrink-0" style={{ background: '#1c1529' }}>
            <div className="flex flex-wrap gap-3 items-end">

              {/* Hersteller */}
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: '#858585' }}>Hersteller</label>
                <select
                  value={importMfr}
                  onChange={(e) => setImportMfr(e.target.value)}
                  className="px-2 py-1 rounded text-xs"
                  style={{ background: '#252526', border: '1px solid #3e3e3e', color: '#d4d4d4', minWidth: '80px' }}
                >
                  <option value="">— wählen —</option>
                  {allManufacturers.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>

              {/* Sheet — combobox with history */}
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: '#858585' }}>Sheet-Name</label>
                <input
                  list="sheet-history-list"
                  value={importSheet}
                  onChange={(e) => setImportSheet(e.target.value)}
                  onBlur={(e) => { if (e.target.value.trim()) fetchTabsForSheet(e.target.value.trim()) }}
                  onKeyDown={(e) => { if (e.key === 'Enter') fetchTabsForSheet(importSheet.trim()) }}
                  placeholder="z.B. TRX Orders"
                  className="px-2 py-1 rounded text-xs"
                  style={{ background: '#252526', border: '1px solid #3e3e3e', color: '#d4d4d4', width: '180px', outline: 'none' }}
                />
                <datalist id="sheet-history-list">
                  {sheetHistory.map((s) => <option key={s} value={s} />)}
                </datalist>
              </div>

              {/* Tab — dropdown auto-populated from sheet */}
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: '#858585' }}>
                  Tab{tabsLoading ? <span style={{ color: '#7c3aed' }}> …</span> : ''}
                </label>
                {availableTabs.length > 0 ? (
                  <select
                    value={importTab}
                    onChange={(e) => setImportTab(e.target.value)}
                    className="px-2 py-1 rounded text-xs"
                    style={{ background: '#252526', border: '1px solid #3e3e3e', color: '#d4d4d4', width: '200px' }}
                  >
                    {availableTabs.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                ) : (
                  <input
                    value={importTab}
                    onChange={(e) => setImportTab(e.target.value)}
                    placeholder="z.B. Order 2025-03-01"
                    className="px-2 py-1 rounded text-xs"
                    style={{ background: '#252526', border: '1px solid #3e3e3e', color: tabsLoading ? '#5a5a5a' : '#d4d4d4', width: '200px', outline: 'none' }}
                    disabled={tabsLoading}
                  />
                )}
              </div>

              {/* SKU col */}
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: '#858585' }}>SKU-Spalte</label>
                <input
                  value={importSkuCol}
                  onChange={(e) => setImportSkuCol(e.target.value)}
                  className="px-2 py-1 rounded text-xs"
                  style={{ background: '#252526', border: '1px solid #3e3e3e', color: '#d4d4d4', width: '80px', outline: 'none' }}
                />
              </div>

              {/* Qty col */}
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: '#858585' }}>Menge-Spalte</label>
                <input
                  value={importQtyCol}
                  onChange={(e) => setImportQtyCol(e.target.value)}
                  className="px-2 py-1 rounded text-xs"
                  style={{ background: '#252526', border: '1px solid #3e3e3e', color: '#d4d4d4', width: '80px', outline: 'none' }}
                />
              </div>

              {/* Mode */}
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: '#858585' }}>Modus</label>
                <div className="flex rounded overflow-hidden" style={{ border: '1px solid #3e3e3e' }}>
                  {(['add', 'subtract', 'set'] as const).map((m) => (
                    <button
                      key={m}
                      onClick={() => setImportMode(m)}
                      className="px-3 py-1 text-xs"
                      style={{
                        background: importMode === m ? '#4c2a8a' : '#252526',
                        color:      importMode === m ? '#c4b5fd' : '#858585',
                        borderRight: m !== 'set' ? '1px solid #3e3e3e' : 'none',
                      }}
                    >
                      {m === 'add' ? '+addieren' : m === 'subtract' ? '−subtrahieren' : '=setzen'}
                    </button>
                  ))}
                </div>
              </div>

              {/* Preview button */}
              <button
                onClick={handleImportPreview}
                disabled={importLoading || !importMfr || !importSheet || !importTab}
                className="px-4 py-1.5 rounded text-xs font-semibold self-end"
                style={{
                  background: (importLoading || !importMfr || !importSheet || !importTab) ? '#3e3e3e' : '#4c2a8a',
                  color:      (importLoading || !importMfr || !importSheet || !importTab) ? '#858585' : '#c4b5fd',
                  cursor:     (importLoading || !importMfr || !importSheet || !importTab) ? 'default' : 'pointer',
                }}
              >
                {importLoading ? 'Lädt…' : 'Vorschau laden'}
              </button>
            </div>

            {importLoading && <div className="mt-2"><IndeterminateBar color="#7c3aed" /></div>}
            {importError && (
              <div className="mt-2 text-xs px-3 py-2 rounded" style={{ background: '#2d1515', color: '#f87171' }}>
                {importError}
              </div>
            )}
          </div>
        </>
      )}

      {importPreviewData ? (
        <>
          {/* ── Import preview — inline, replaces product list ── */}
          <div style={{ height: '1px', background: '#4c2a8a', margin: '0' }} />

          {/* Info bar */}
          <div className="px-4 py-2 shrink-0 flex items-center gap-2 flex-wrap" style={{ background: '#1c1529' }}>
            <span className="text-xs" style={{ color: '#858585' }}>
              <span style={{ color: '#c4b5fd' }}>{importSheet}</span> / {importTab} · {importMfr} · Modus:{' '}
              <strong style={{ color: '#a78bfa' }}>
                {importMode === 'add' ? 'addieren' : importMode === 'subtract' ? 'subtrahieren' : 'setzen'}
              </strong>
            </span>
            {importPhase === 'confirm' && !importStockLoading && (
              <button
                onClick={() => setImportPreviewData(null)}
                className="ml-auto text-xs px-2 py-0.5 rounded"
                style={{ background: '#2d2d2d', color: '#858585' }}
              >✕ Schließen</button>
            )}
          </div>

          {/* Billbee stock loading bar */}
          {importStockLoading && (
            <>
              <IndeterminateBar color="#7c3aed" />
              <div className="px-4 pb-1 text-xs shrink-0" style={{ color: '#a78bfa' }}>
                Lade Billbee Lagerbestand ({importPreviewData.items.length} Artikel)…
              </div>
            </>
          )}

          {/* Skipped rows warning */}
          {importPhase === 'confirm' && importPreviewData.errors.length > 0 && (
            <div className="mx-4 mb-1 px-3 py-1.5 rounded text-xs shrink-0" style={{ background: '#2d1f0a', border: '1px solid #92400e', color: '#fed7aa' }}>
              <strong style={{ color: '#f97316' }}>{importPreviewData.errors.length} übersprungen</strong>
              {' (SKU nicht gefunden oder ungültige Menge)'}
            </div>
          )}

          {/* Table column headers */}
          <div className="px-4 flex items-center gap-2 text-xs shrink-0" style={{ color: '#6b7280', paddingTop: '6px', paddingBottom: '4px' }}>
            {importPhase === 'confirm' ? (
              <input
                type="checkbox"
                checked={importChecked.size === importPreviewData.items.length && importPreviewData.items.length > 0}
                onChange={(e) => setImportChecked(e.target.checked ? new Set(importPreviewData.items.map((i) => i.sku)) : new Set())}
                style={{ accentColor: '#7c3aed', cursor: 'pointer' }}
              />
            ) : (
              <span style={{ display: 'inline-block', width: '13px' }} />
            )}
            <span style={{ width: '44px', textAlign: 'right' }}>Billbee</span>
            <span style={{ width: '50px', textAlign: 'right' }}>Menge</span>
            <span style={{ width: '50px', textAlign: 'right' }}>
              {importPhase === 'results' ? 'Gesetzt' : 'Neu'}
            </span>
            {importPhase === 'results' && (
              <span style={{ width: '44px', textAlign: 'right' }}>Ist</span>
            )}
            <span className="flex-1 pl-2">SKU</span>
          </div>

          {/* Applying progress bar */}
          {importPhase === 'applying' && importProgress && (
            <div className="px-4 pb-2 shrink-0">
              <DeterminateBar done={importProgress.done} total={importProgress.total} color="#7c3aed" />
              <div className="flex items-center justify-between mt-1 text-xs" style={{ color: '#858585' }}>
                <span>{importProgress.done} / {importProgress.total}</span>
                <span className="font-mono" style={{ color: '#a78bfa' }}>{importProgress.currentSku}</span>
              </div>
            </div>
          )}

          {/* Items — takes up remaining flex space */}
          <div className="flex-1 overflow-y-auto px-4 min-h-0">
            {importPreviewData.items.map((item) => {
              const checked   = importChecked.has(item.sku)
              const bs        = item.billbeeStock
              const computed  = bs !== null
                ? (importMode === 'add' ? bs + item.qty : importMode === 'subtract' ? bs - item.qty : item.qty)
                : null
              const result    = importResults?.find((r) => r.sku === item.sku)
              const isCurrent = importPhase === 'applying' && importProgress?.currentSku === item.sku
              return (
                <div
                  key={item.sku}
                  onClick={importPhase === 'confirm' ? () => setImportChecked((prev) => {
                    const next = new Set(prev); checked ? next.delete(item.sku) : next.add(item.sku); return next
                  }) : undefined}
                  className="flex items-center gap-2 py-1.5 text-xs"
                  style={{
                    borderBottom: '1px solid #2d1f4a',
                    cursor: importPhase === 'confirm' ? 'pointer' : 'default',
                    opacity: importPhase === 'confirm' ? (checked ? 1 : 0.35) : 1,
                    background: isCurrent ? '#2d1f4a' : 'transparent',
                  }}
                >
                  {importPhase === 'confirm' ? (
                    <input type="checkbox" readOnly checked={checked}
                      style={{ accentColor: '#7c3aed', pointerEvents: 'none' }}
                    />
                  ) : (
                    <span style={{ display: 'inline-block', width: '13px', textAlign: 'center', color: result?.success ? '#4ade80' : result ? '#f87171' : isCurrent ? '#a78bfa' : '#4b5563' }}>
                      {result ? (result.success ? '✓' : '✗') : isCurrent ? '→' : '·'}
                    </span>
                  )}
                  <span className="shrink-0" style={{ color: '#9ca3af', width: '44px', textAlign: 'right' }}>
                    {bs !== null ? bs : '…'}
                  </span>
                  <span className="shrink-0 font-semibold" style={{ color: '#a78bfa', width: '50px', textAlign: 'right' }}>
                    {importMode === 'subtract' ? `−${item.qty}` : importMode === 'add' ? `+${item.qty}` : item.qty}
                  </span>
                  <span className="shrink-0 font-semibold" style={{
                    width: '50px', textAlign: 'right',
                    color: importPhase === 'results' ? (result?.success ? '#4ade80' : '#f87171') : '#4ade80',
                  }}>
                    {importPhase === 'results'
                      ? (result ? (result.success ? result.newStock : '✗') : '—')
                      : (computed !== null ? computed : '…')}
                  </span>
                  {importPhase === 'results' && (() => {
                    const verified = importVerifyStocks[item.sku]
                    if (!(item.sku in importVerifyStocks)) return <span style={{ width: '44px', display: 'inline-block' }} />
                    const matches = verified !== null && result?.newStock !== undefined && verified === result?.newStock
                    return (
                      <span className="shrink-0 font-semibold" style={{
                        width: '44px', textAlign: 'right',
                        color: verified === null ? '#6b7280' : matches ? '#4ade80' : '#f97316',
                      }}>
                        {verified === null ? '…' : verified}
                      </span>
                    )
                  })()}
                  <span className="font-mono flex-1 truncate pl-2" style={{ color: '#d4d4d4' }} title={item.sku}>{item.sku}</span>
                </div>
              )
            })}
          </div>

          {/* Phase footer */}
          {importPhase === 'confirm' && (
            <div className="px-4 py-2 shrink-0 flex items-center justify-end gap-3" style={{ borderTop: '1px solid #4c2a8a', background: '#1c1529' }}>
              <button
                onClick={() => setImportPreviewData(null)}
                className="px-4 py-1.5 rounded text-xs"
                style={{ background: '#3e3e3e', color: '#d4d4d4' }}
              >Abbrechen</button>
              <button
                onClick={handleImportApply}
                disabled={importChecked.size === 0 || importStockLoading}
                className="px-4 py-1.5 rounded text-xs font-semibold"
                style={{
                  background: (importChecked.size === 0 || importStockLoading) ? '#3e3e3e' : '#4c2a8a',
                  color:      (importChecked.size === 0 || importStockLoading) ? '#858585' : '#c4b5fd',
                  cursor:     (importChecked.size === 0 || importStockLoading) ? 'default' : 'pointer',
                }}
              >Bestätigen ({importChecked.size})</button>
            </div>
          )}
          {importPhase === 'results' && importResults && (
            <>
              {importVerifying && <IndeterminateBar color="#7c3aed" />}
              <div className="px-4 py-2 shrink-0 flex items-center gap-3" style={{ borderTop: '1px solid #4c2a8a', background: '#1c1529' }}>
                <span className="text-xs">
                  <span style={{ color: '#4ade80' }}>✓ {importResults.filter((r) => r.success).length} aktualisiert</span>
                  {importResults.some((r) => !r.success) && (
                    <span style={{ color: '#f87171' }}> · ✗ {importResults.filter((r) => !r.success).length} Fehler</span>
                  )}
                </span>
                <button
                  onClick={handleImportVerify}
                  disabled={importVerifying}
                  className="px-4 py-1.5 rounded text-xs font-semibold"
                  style={{
                    background: importVerifying ? '#3e3e3e' : '#1a3a5c',
                    border: `1px solid ${importVerifying ? '#3e3e3e' : '#2472c8'}`,
                    color: importVerifying ? '#858585' : '#60a5fa',
                    cursor: importVerifying ? 'default' : 'pointer',
                  }}
                >{importVerifying ? 'Prüft…' : 'Prüfen'}</button>
                <button
                  onClick={() => setImportPreviewData(null)}
                  disabled={importVerifying}
                  className="ml-auto px-4 py-1.5 rounded text-xs font-semibold"
                  style={{ background: '#4c2a8a', color: importVerifying ? '#858585' : '#c4b5fd', cursor: importVerifying ? 'default' : 'pointer' }}
                >Schließen</button>
              </div>
            </>
          )}
        </>
      ) : (
        <>
          {/* ── Normal view: filter row + product list ── */}
          {hasProducts && (
            <>
              <div style={{ height: '1px', background: '#3e3e3e', margin: '0 16px' }} />
              <div className="px-4 py-2 shrink-0 flex items-center gap-2 flex-wrap">
                <MultiSelect label="Kategorie" options={uniqueCategories} selected={filterCat}     onChange={(v) => setAndCacheFilter('cat',     v)} />
                <MultiSelect label="Größe"     options={uniqueSizes}      selected={filterSize}    onChange={(v) => setAndCacheFilter('size',    v)} />
                <MultiSelect label="Farbe"     options={uniqueColors}     selected={filterColor}   onChange={(v) => setAndCacheFilter('color',   v)} />
                <MultiSelect label="Variante"  options={uniqueVariants}   selected={filterVariant} onChange={(v) => setAndCacheFilter('variant', v)} />
                <button
                  onClick={handleFetchStock}
                  disabled={loadingStock || filteredProducts.length === 0}
                  className="ml-auto px-3 py-1 rounded text-xs font-semibold shrink-0"
                  style={{
                    background: loadingStock ? '#3e3e3e' : '#14532d',
                    border:     `1px solid ${loadingStock ? '#4b5563' : '#16a34a'}`,
                    color:      loadingStock ? '#858585' : '#4ade80',
                    cursor:     loadingStock ? 'default' : 'pointer',
                  }}
                >
                  {loadingStock ? `Lädt Stock… (${filteredProducts.length})` : 'akt. Lagerbestand abfragen'}
                </button>
              </div>
              {loadingStock && <IndeterminateBar color="#16a34a" />}
              {stockError && <div className="px-4 pb-1 text-xs" style={{ color: '#f87171' }}>{stockError}</div>}
            </>
          )}

          <div style={{ height: '1px', background: '#3e3e3e', margin: '0 16px' }} />

          <div className="flex-1 overflow-y-auto px-4 py-3 min-h-0">
            {isLoading && (
              <div className="text-vscode-muted text-sm py-4">
                {loadingBillbee
                  ? 'Lade Produkte von Billbee API… (kann mehrere Minuten dauern)'
                  : 'Lade Produkte aus Google Sheets…'}
              </div>
            )}
            {!isLoading && loadError && (
              <div className="p-3 rounded text-xs" style={{ background: '#2d1515', border: '1px solid #7f1d1d' }}>
                <div className="font-semibold mb-1" style={{ color: '#f87171' }}>Fehler beim Laden</div>
                <div className="font-mono break-all" style={{ color: '#fca5a5' }}>{loadError}</div>
              </div>
            )}
            {!isLoading && !loadError && !hasProducts && (
              sheetErrors.length > 0 ? (
                <div className="p-3 rounded text-xs" style={{ background: '#2d1f0a', border: '1px solid #92400e' }}>
                  <div className="font-semibold mb-2" style={{ color: '#f97316' }}>Sheet-Fehler ({sheetErrors.length})</div>
                  {sheetErrors.map((e) => (
                    <div key={e.manufacturer} className="mb-1">
                      <span className="font-semibold" style={{ color: '#fdba74' }}>{e.manufacturer}: </span>
                      <span className="font-mono break-all" style={{ color: '#fed7aa' }}>{e.error}</span>
                    </div>
                  ))}
                  <div className="mt-2" style={{ color: '#a16207' }}>
                    Tab „ProductList" oder „downloaded" muss im Sheet vorhanden sein.
                  </div>
                </div>
              ) : (
                <div className="text-vscode-muted text-xs py-2">
                  Wähle Hersteller und klicke «Update» um Produkte zu laden.
                </div>
              )
            )}
            {!isLoading && hasProducts && sheetErrors.length > 0 && (
              <div className="mb-3 px-3 py-2 rounded text-xs" style={{ background: '#2d1f0a', border: '1px solid #92400e' }}>
                <span className="font-semibold" style={{ color: '#f97316' }}>Teilweise Fehler: </span>
                <span style={{ color: '#fed7aa' }}>{sheetErrors.map((e) => `${e.manufacturer} (${e.error})`).join(' · ')}</span>
              </div>
            )}
            {!isLoading && hasProducts && filteredProducts.length === 0 && (
              <div className="text-vscode-muted text-xs py-2">Keine Produkte entsprechen dem Filter.</div>
            )}
            {filteredProducts.length > 0 && (
              <div className="flex flex-col gap-0.5">
                {filteredProducts.map((p) => {
                  const fetched    = stockFetched(p.sku)
                  const stock      = fetched ? stockMap[p.sku] : null
                  const pendingVal = pending[p.sku]
                  const isSelected = selected?.sku === p.sku
                  const color      = (fetched && stock !== null) ? getStockColor(stock, p.stockTarget) : '#3e3e3e'
                  return (
                    <button
                      key={p.sku}
                      onClick={() => handleSelect(p)}
                      className="w-full text-left px-3 py-1.5 rounded flex items-center gap-2 hover:bg-vscode-hover transition-colors"
                      style={{
                        background: isSelected ? '#264f78' : 'transparent',
                        border:     isSelected ? '1px solid #007acc' : '1px solid transparent',
                      }}
                    >
                      <span className="text-xs font-semibold shrink-0 text-right" style={{ color, minWidth: '28px' }}>
                        {fetched && stock !== null ? String(stock) : ''}
                      </span>
                      <span className="text-xs shrink-0" style={{ color: '#9ca3af', width: '36px', textAlign: 'left' }}>
                        {fetched && stock !== null && p.stockTarget !== null ? `(${p.stockTarget})` : ''}
                      </span>
                      <span className="text-xs font-bold shrink-0" style={{ color: pendingVal !== undefined ? '#d4d4d4' : 'transparent', width: '14px', textAlign: 'center' }}>→</span>
                      <span className="text-xs font-semibold shrink-0" style={{ color: '#60a5fa', width: '28px', textAlign: 'right' }}>
                        {pendingVal !== undefined ? String(pendingVal) : ''}
                      </span>
                      <span className="text-xs shrink-0" style={{
                        width: '40px', textAlign: 'left',
                        color: (pendingVal !== undefined && fetched && stock !== null)
                          ? (pendingVal - stock > 0 ? '#4ade80' : '#f87171') : 'transparent',
                      }}>
                        {pendingVal !== undefined && fetched && stock !== null
                          ? (() => { const d = pendingVal - stock; return `(${d > 0 ? '+' : ''}${d})` })()
                          : '()'}
                      </span>
                      <span className="flex-1 font-mono text-xs truncate" style={{ color: isSelected ? '#fff' : '#d4d4d4' }} title={p.sku}>
                        {p.sku}
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </>
      )}

      {/* Pending changes save bar */}
      {hasPending && (
        <>
          <div style={{ height: '1px', background: '#3e3e3e', margin: '0 16px' }} />
          <div className="px-4 py-2 shrink-0 flex items-center gap-3" style={{ background: '#1a2a3a' }}>
            <span className="text-xs" style={{ color: '#60a5fa' }}>
              {pendingCount} ausstehende Änderung{pendingCount !== 1 ? 'en' : ''}
            </span>
            <button
              onClick={handleResetPending}
              className="px-3 py-1.5 rounded text-xs"
              style={{ background: '#2d1515', border: '1px solid #7f1d1d', color: '#f87171' }}
            >
              Zurücksetzen
            </button>
            <button
              onClick={() => { setModalError(''); setSaveProgress(null); setShowModal(true) }}
              className="ml-auto px-4 py-1.5 rounded text-xs font-semibold"
              style={{ background: '#007acc', color: '#fff' }}
            >
              Speichern…
            </button>
          </div>
        </>
      )}

      {/* Edit area */}
      {selected && (
        <>
          <div style={{ height: '1px', background: '#3e3e3e', margin: '0 16px' }} />
          <div className="px-4 py-3 shrink-0" style={{ background: '#252526' }}>
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="font-mono text-xs font-semibold" style={{ color: '#d4d4d4' }}>{selected.sku}</span>
              {selected.title && (
                <span className="text-xs truncate max-w-xs" style={{ color: '#858585' }}>{selected.title}</span>
              )}
              {isPendingForSelected && (
                <button
                  onClick={() => handleRemovePending(selected.sku)}
                  className="ml-auto text-xs px-2 py-0.5 rounded"
                  style={{ background: '#1e3a5f', color: '#60a5fa', border: '1px solid #2463ae' }}
                >
                  Änderung verwerfen
                </button>
              )}
            </div>

            {!canEdit ? (
              <div className="flex items-center gap-3 opacity-40">
                <span className="text-xs" style={{ color: '#858585' }}>Lagerbestand:</span>
                <button disabled className="w-7 h-7 rounded text-sm font-bold flex items-center justify-center"
                  style={{ background: '#2d2d2d', border: '1px solid #3e3e3e', color: '#5a5a5a', cursor: 'default' }}>−</button>
                <input disabled value="" placeholder="?"
                  className="px-2 py-1 rounded text-sm font-semibold w-20 text-center"
                  style={{ background: '#1a1a1a', border: '1px solid #2d2d2d', color: '#5a5a5a' }} />
                <button disabled className="w-7 h-7 rounded text-sm font-bold flex items-center justify-center"
                  style={{ background: '#2d2d2d', border: '1px solid #3e3e3e', color: '#5a5a5a', cursor: 'default' }}>+</button>
                <span className="text-xs ml-1" style={{ color: '#5a5a5a' }}>«akt. Lagerbestand abfragen» zuerst</span>
              </div>
            ) : (
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs" style={{ color: '#858585' }}>Lagerbestand:</span>
                <button onClick={() => adjustEdit(-1)} className="w-7 h-7 rounded text-sm font-bold flex items-center justify-center"
                  style={{ background: '#3a1a1a', border: '1px solid #7f1d1d', color: '#f87171' }}>−</button>
                <input
                  type="number"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  className="px-2 py-1 rounded text-sm font-semibold w-20 text-center"
                  style={{ background: '#1e1e1e', border: '1px solid #3e3e3e', color: '#d4d4d4', outline: 'none' }}
                />
                <button onClick={() => adjustEdit(+1)} className="w-7 h-7 rounded text-sm font-bold flex items-center justify-center"
                  style={{ background: '#14532d', border: '1px solid #16a34a', color: '#4ade80' }}>+</button>
                {pendingDelta !== null && pendingDelta !== 0 && (
                  <span className="text-xs" style={{ color: pendingDelta > 0 ? '#4ade80' : '#f87171' }}>
                    {pendingDelta > 0 ? `+${pendingDelta}` : pendingDelta}
                  </span>
                )}
                <button
                  onClick={handleStage}
                  disabled={pendingQty === null || isNaN(pendingQty ?? NaN) || !hasPendingChange}
                  className="px-3 py-1 rounded text-xs font-semibold"
                  style={{
                    background: (pendingQty === null || isNaN(pendingQty ?? NaN) || !hasPendingChange) ? '#3e3e3e' : '#1a3a5c',
                    border:     `1px solid ${(pendingQty === null || isNaN(pendingQty ?? NaN) || !hasPendingChange) ? '#3e3e3e' : '#2472c8'}`,
                    color:      (pendingQty === null || isNaN(pendingQty ?? NaN) || !hasPendingChange) ? '#858585' : '#60a5fa',
                    cursor:     (pendingQty === null || isNaN(pendingQty ?? NaN) || !hasPendingChange) ? 'default' : 'pointer',
                  }}
                >
                  Vormerken
                </button>
              </div>
            )}
          </div>
        </>
      )}

      {/* Confirmation modal */}
      {showModal && (
        <ConfirmModal
          pending={pending}
          stockMap={stockMap}
          products={products}
          onConfirm={handleConfirmSave}
          onCancel={() => { if (!modalSaving) setShowModal(false) }}
          saving={modalSaving}
          saveProgress={saveProgress}
          saveError={modalError}
        />
      )}

    </div>
  )
}
