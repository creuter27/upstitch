import { useEffect, useMemo, useRef, useState } from 'react'
import { getPackaging, updatePackaging, PackagingMapping, PackageType } from '../api'

interface Props {
  toolId: string
}

// ---------------------------------------------------------------------------
// Multi-select checkbox dropdown
// ---------------------------------------------------------------------------

interface MultiSelectProps {
  label: string
  options: string[]
  selected: Set<string>
  onChange: (next: Set<string>) => void
}

function MultiSelect({ label, options, selected, onChange }: MultiSelectProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onOutside)
    return () => document.removeEventListener('mousedown', onOutside)
  }, [])

  function toggle(opt: string) {
    const next = new Set(selected)
    if (next.has(opt)) next.delete(opt)
    else next.add(opt)
    onChange(next)
  }

  const display = selected.size === 0 ? `All (${options.length})` : `${selected.size} selected`

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button
        onClick={() => setOpen(!open)}
        className="px-2 py-1 rounded text-xs flex items-center gap-1"
        style={{ background: '#2d2d2d', border: '1px solid #3e3e3e', color: '#d4d4d4', minWidth: '120px' }}
      >
        <span className="flex-1 text-left">{label}: {display}</span>
        <span style={{ opacity: 0.5 }}>{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div
          style={{
            position: 'absolute', top: '100%', left: 0, zIndex: 50, minWidth: '200px',
            background: '#2d2d2d', border: '1px solid #3e3e3e', borderRadius: '4px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.4)', maxHeight: '240px', overflowY: 'auto',
            marginTop: '2px',
          }}
        >
          {selected.size > 0 && (
            <button
              onClick={() => onChange(new Set())}
              className="w-full text-left px-3 py-1.5 text-xs hover:bg-vscode-hover"
              style={{ color: '#858585', borderBottom: '1px solid #3e3e3e' }}
            >
              Clear filter
            </button>
          )}
          {options.map((opt) => (
            <label
              key={opt}
              className="flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer hover:bg-vscode-hover"
              style={{ color: '#d4d4d4' }}
            >
              <input
                type="checkbox"
                checked={selected.has(opt)}
                onChange={() => toggle(opt)}
                style={{ accentColor: '#007acc' }}
              />
              {opt}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function PackagingPanel({ toolId }: Props) {
  const [mappings, setMappings] = useState<PackagingMapping[]>([])
  const [packageTypes, setPackageTypes] = useState<PackageType[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filterTypes, setFilterTypes] = useState<Set<string>>(new Set())
  const [filterProducts, setFilterProducts] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<PackagingMapping | null>(null)
  const [editName, setEditName] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  useEffect(() => {
    getPackaging(toolId)
      .then((data) => {
        setMappings(data.mappings)
        setPackageTypes(data.packageTypes)
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [toolId])

  // All unique package type names from package_types.yaml
  const allTypeNames = useMemo(() => packageTypes.map((pt) => pt.name), [packageTypes])

  // All unique product identifiers (Mfr|Cat|Size) extracted from combo keys
  const allProducts = useMemo(() => {
    const set = new Set<string>()
    for (const m of mappings) {
      for (const part of m.comboKey.split(' + ')) {
        const segments = part.split('|')
        if (segments.length >= 3) {
          set.add(segments.slice(0, 3).join('|'))
        }
      }
    }
    return [...set].sort()
  }, [mappings])

  // Filtered + sorted list
  const filtered = useMemo(() => {
    return mappings
      .filter((m) => {
        if (filterTypes.size > 0 && !filterTypes.has(m.name)) return false
        if (filterProducts.size > 0) {
          const keyProducts = m.comboKey
            .split(' + ')
            .map((p) => p.split('|').slice(0, 3).join('|'))
          if (!keyProducts.some((kp) => filterProducts.has(kp))) return false
        }
        return true
      })
      .sort((a, b) => a.comboKey.localeCompare(b.comboKey))
  }, [mappings, filterTypes, filterProducts])

  function handleSelect(m: PackagingMapping) {
    setSelected(m)
    setEditName(m.name)
    setSaveError('')
  }

  async function handleSave() {
    if (!selected) return
    setSaving(true)
    setSaveError('')
    try {
      await updatePackaging(toolId, selected.comboKey, editName)
      const updated = { ...selected, name: editName }
      setMappings((prev) => prev.map((m) => (m.comboKey === selected.comboKey ? updated : m)))
      setSelected(updated)
    } catch (e) {
      setSaveError(String(e))
    } finally {
      setSaving(false)
    }
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

  const typeColors: Record<string, string> = {
    'Kleinpaket-Klein': '#1d4ed8',
    'Kleinpaket-Max': '#15803d',
    'OP303': '#7c3aed',
    'Vogelhauskarton': '#b45309',
    'Kapu-karton': '#be185d',
  }

  function typeBadgeStyle(name: string) {
    const color = typeColors[name] ?? '#374151'
    return { background: color, color: '#fff', padding: '1px 6px', borderRadius: '3px', fontSize: '11px', fontWeight: 600, whiteSpace: 'nowrap' as const }
  }

  return (
    <div className="h-full flex flex-col overflow-hidden" style={{ background: '#1e1e1e' }}>
      {/* Header */}
      <div className="px-4 pt-4 pb-2 shrink-0">
        <div className="flex items-center gap-3 mb-1 flex-wrap">
          <h1 className="text-base font-semibold text-vscode-text shrink-0">Verpackung</h1>
          <div className="flex items-center gap-2 flex-wrap">
            <MultiSelect
              label="Typ"
              options={allTypeNames}
              selected={filterTypes}
              onChange={setFilterTypes}
            />
            <MultiSelect
              label="Produkt"
              options={allProducts}
              selected={filterProducts}
              onChange={setFilterProducts}
            />
          </div>
          <span className="ml-auto text-xs" style={{ color: '#4b5563' }}>
            {filtered.length} / {mappings.length}
          </span>
        </div>
        <p className="text-vscode-muted text-xs">Verpackungstyp-Zuordnung pro Produktkombination</p>
      </div>

      <div style={{ height: '1px', background: '#3e3e3e', margin: '0 16px' }} />

      {/* List */}
      <div className="flex-1 overflow-y-auto px-4 py-2 min-h-0">
        {filtered.length === 0 ? (
          <div className="text-vscode-muted text-xs py-4">No entries match the current filter.</div>
        ) : (
          <div className="flex flex-col gap-0.5">
            {filtered.map((m) => {
              const isSelected = selected?.comboKey === m.comboKey
              return (
                <button
                  key={m.comboKey}
                  onClick={() => handleSelect(m)}
                  className="w-full text-left px-3 py-2 rounded flex items-center gap-3 hover:bg-vscode-hover transition-colors"
                  style={{
                    background: isSelected ? '#264f78' : 'transparent',
                    border: isSelected ? '1px solid #007acc' : '1px solid transparent',
                  }}
                >
                  <span
                    className="flex-1 font-mono text-xs truncate"
                    style={{ color: isSelected ? '#fff' : '#d4d4d4' }}
                    title={m.comboKey}
                  >
                    {m.comboKey}
                  </span>
                  <span style={typeBadgeStyle(m.name)}>{m.name}</span>
                  {m.setAt && (
                    <span className="text-xs shrink-0" style={{ color: '#4b5563', minWidth: '80px', textAlign: 'right' }}>
                      {m.setAt.slice(0, 10)}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* Edit area */}
      {selected && (
        <>
          <div style={{ height: '1px', background: '#3e3e3e', margin: '0 16px' }} />
          <div className="px-4 py-3 shrink-0" style={{ background: '#252526' }}>
            <div className="text-xs mb-2" style={{ color: '#858585' }}>
              Bearbeiten:
              <span className="font-mono ml-1" style={{ color: '#d4d4d4' }} title={selected.comboKey}>
                {selected.comboKey.length > 80 ? selected.comboKey.slice(0, 80) + '…' : selected.comboKey}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <select
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="px-2 py-1 rounded text-xs"
                style={{ background: '#2d2d2d', border: '1px solid #3e3e3e', color: '#d4d4d4', outline: 'none' }}
              >
                {packageTypes.map((pt) => (
                  <option key={pt.name} value={pt.name}>{pt.name}</option>
                ))}
              </select>
              <button
                onClick={handleSave}
                disabled={saving || editName === selected.name}
                className="px-3 py-1 rounded text-xs font-semibold transition-colors"
                style={{
                  background: saving || editName === selected.name ? '#3e3e3e' : '#007acc',
                  color: saving || editName === selected.name ? '#858585' : '#fff',
                  cursor: saving || editName === selected.name ? 'default' : 'pointer',
                }}
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
              {saveError && (
                <span className="text-xs" style={{ color: '#f87171' }}>{saveError}</span>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
