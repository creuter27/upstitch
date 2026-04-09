import { useEffect, useState, useCallback } from 'react'
import { getDesignRules, saveDesignRules, DesignRule, RuleAction, AnyCondition, SimpleCondition } from '../api'

// ---------------------------------------------------------------------------
// Constants — keep in sync with design_rule_engine.py
// ---------------------------------------------------------------------------

const COLUMNS = [
  'category', 'design', 'text', 'text2', 'textFont', 'textColor', 'original',
  'productVariant', 'productSize', 'productColor', 'sku_virtual', 'sku_physical',
]

const CONDITION_TYPES = [
  { value: 'contains',          label: 'enthält' },
  { value: 'not_contains',      label: 'enthält nicht' },
  { value: 'equals',            label: 'ist gleich' },
  { value: 'matches',           label: 'passt auf Muster (Regex)' },
  { value: 'is_empty',          label: 'ist leer' },
  { value: 'not_empty',         label: 'ist nicht leer' },
  { value: 'category_stitched', label: 'Kategorie = bestickt' },
]

const COMPOSITE_TYPES = [
  { value: 'all_of', label: 'Alle der folgenden (UND)' },
  { value: 'any_of', label: 'Eine der folgenden (ODER)' },
]

const ACTION_TYPES = [
  { value: 'clear',         label: 'Inhalt löschen' },
  { value: 'extract',       label: 'Wert extrahieren (Regex)' },
  { value: 'highlight',     label: 'Zelle hervorheben' },
  { value: 'note',          label: 'Notiz hinzufügen' },
  { value: 'resolve_color', label: 'Farbe normalisieren (Garnfarben)' },
]

const HIGHLIGHT_COLORS = [
  { value: 'yellow',    label: 'Gelb (geändert)' },
  { value: 'red_white', label: 'Rot / Weiß (Fehler)' },
]

// ---------------------------------------------------------------------------
// Tiny helpers
// ---------------------------------------------------------------------------

function uid() {
  return Math.random().toString(36).slice(2, 9)
}

function blankRule(): DesignRule {
  return {
    id: uid(),
    description: '',
    condition: { type: 'contains', column: 'original', values: [] } as SimpleCondition,
    actions: [],
  }
}

function blankAction(): RuleAction {
  return { type: 'clear', column: 'text' }
}

// ---------------------------------------------------------------------------
// Shared input styles
// ---------------------------------------------------------------------------

const inp: React.CSSProperties = {
  background: '#1e1e1e', color: '#d4d4d4',
  border: '1px solid #3e3e3e', borderRadius: 3,
  padding: '3px 6px', fontSize: 12, width: '100%',
}
const sel: React.CSSProperties = { ...inp, cursor: 'pointer' }
const label: React.CSSProperties = { fontSize: 11, color: '#9e9e9e', marginBottom: 2, display: 'block' }

// ---------------------------------------------------------------------------
// Select component
// ---------------------------------------------------------------------------

function Sel({
  value, onChange, options, style,
}: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  style?: React.CSSProperties
}) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)} style={{ ...sel, ...style }}>
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

// ---------------------------------------------------------------------------
// Condition editor (recursive — handles composites)
// ---------------------------------------------------------------------------

interface ConditionEditorProps {
  cond: AnyCondition
  onChange: (c: AnyCondition) => void
  depth?: number
}

function isComposite(c: AnyCondition): boolean {
  return 'all_of' in c || 'any_of' in c || 'not' in c
}

function getCompositeKey(c: AnyCondition): 'all_of' | 'any_of' | null {
  if ('all_of' in c) return 'all_of'
  if ('any_of' in c) return 'any_of'
  return null
}

function ConditionEditor({ cond, onChange, depth = 0 }: ConditionEditorProps) {
  const bg = depth % 2 === 0 ? '#252526' : '#2a2d2e'

  // Determine whether this condition is composite or simple
  const compKey = getCompositeKey(cond)
  const isComp  = isComposite(cond)
  const sc       = cond as SimpleCondition

  // Toggle between simple and composite
  function switchToComposite(key: 'all_of' | 'any_of') {
    onChange({ [key]: [{ type: 'contains', column: 'original', values: [] } as SimpleCondition] } as AnyCondition)
  }
  function switchToSimple() {
    onChange({ type: 'contains', column: 'original', values: [] } as SimpleCondition)
  }

  // Composite: add / change / remove child conditions
  const compChildren: AnyCondition[] = compKey ? ((cond as any)[compKey] as AnyCondition[]) : []

  function updateChild(i: number, c: AnyCondition) {
    const next = [...compChildren]
    next[i] = c
    onChange({ [compKey!]: next } as AnyCondition)
  }
  function addChild() {
    onChange({ [compKey!]: [...compChildren, { type: 'contains', column: 'original', values: [] } as SimpleCondition] } as AnyCondition)
  }
  function removeChild(i: number) {
    const next = compChildren.filter((_, idx) => idx !== i)
    onChange({ [compKey!]: next } as AnyCondition)
  }

  return (
    <div style={{ background: bg, border: '1px solid #3e3e3e', borderRadius: 4, padding: 8, marginBottom: 4 }}>
      {/* Top-row: mode selector */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center' }}>
        <span style={{ ...label, margin: 0, whiteSpace: 'nowrap' }}>Typ:</span>
        <select
          value={isComp ? (compKey ?? 'all_of') : sc.type}
          onChange={e => {
            const v = e.target.value
            if (v === 'all_of' || v === 'any_of') switchToComposite(v)
            else if (v === '__simple__') switchToSimple()
            else onChange({ ...sc, type: v as SimpleCondition['type'] })
          }}
          style={{ ...sel, flex: 1 }}
        >
          <optgroup label="Einfach">
            {CONDITION_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </optgroup>
          <optgroup label="Zusammengesetzt">
            {COMPOSITE_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </optgroup>
        </select>
      </div>

      {/* Composite: list of children */}
      {isComp && compKey && (
        <div style={{ paddingLeft: 8, borderLeft: '2px solid #3e3e3e' }}>
          {compChildren.map((child, i) => (
            <div key={i} style={{ display: 'flex', gap: 4, alignItems: 'flex-start', marginBottom: 4 }}>
              <div style={{ flex: 1 }}>
                <ConditionEditor cond={child} onChange={c => updateChild(i, c)} depth={depth + 1} />
              </div>
              <button
                onClick={() => removeChild(i)}
                title="Bedingung entfernen"
                style={{ background: 'none', border: 'none', color: '#858585', cursor: 'pointer', fontSize: 14, paddingTop: 6 }}
              >×</button>
            </div>
          ))}
          <button
            onClick={addChild}
            style={{ fontSize: 11, color: '#007acc', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          >+ Bedingung hinzufügen</button>
        </div>
      )}

      {/* Simple: column + value fields */}
      {!isComp && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
          {sc.type !== 'category_stitched' && (
            <div>
              <span style={label}>Spalte</span>
              <Sel
                value={sc.column ?? ''}
                onChange={v => onChange({ ...sc, column: v })}
                options={COLUMNS.map(c => ({ value: c, label: c }))}
              />
            </div>
          )}
          {(sc.type === 'contains' || sc.type === 'not_contains' || sc.type === 'equals') && (
            <div>
              <span style={label}>Werte (kommagetrennt)</span>
              <input
                style={inp}
                value={(sc.values ?? []).join(', ')}
                onChange={e => onChange({ ...sc, values: e.target.value.split(',').map(v => v.trim()).filter(Boolean) })}
                placeholder="z.B. kein name, no name"
              />
            </div>
          )}
          {sc.type === 'matches' && (
            <div>
              <span style={label}>Muster (Regex)</span>
              <input
                style={inp}
                value={sc.pattern ?? ''}
                onChange={e => onChange({ ...sc, pattern: e.target.value })}
                placeholder="z.B. \bM[0-9]{1,4}"
              />
            </div>
          )}
          {sc.type === 'matches' && (
            <div>
              <span style={label}>Flags (z.B. i für case-insensitive)</span>
              <input
                style={{ ...inp, width: 60 }}
                value={sc.flags ?? 'i'}
                onChange={e => onChange({ ...sc, flags: e.target.value })}
                maxLength={4}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Action editor
// ---------------------------------------------------------------------------

function ActionEditor({ action, onChange }: { action: RuleAction; onChange: (a: RuleAction) => void }) {
  const t = action.type

  return (
    <div style={{ background: '#2d2d2d', border: '1px solid #3e3e3e', borderRadius: 4, padding: 8, marginBottom: 4 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 6, marginBottom: 6 }}>
        <div>
          <span style={label}>Aktion</span>
          <Sel
            value={action.type}
            onChange={v => onChange({ type: v as RuleAction['type'], column: action.column })}
            options={ACTION_TYPES}
          />
        </div>
        {t !== 'resolve_color' && (
          <div>
            <span style={label}>{t === 'extract' ? 'Zielspalte' : 'Spalte'}</span>
            <Sel
              value={(t === 'extract' ? action.to_column : action.column) ?? ''}
              onChange={v => t === 'extract'
                ? onChange({ ...action, to_column: v })
                : onChange({ ...action, column: v })}
              options={COLUMNS.map(c => ({ value: c, label: c }))}
            />
          </div>
        )}
        {t === 'resolve_color' && (
          <div>
            <span style={label}>Spalte</span>
            <Sel
              value={action.column ?? 'textColor'}
              onChange={v => onChange({ ...action, column: v })}
              options={COLUMNS.map(c => ({ value: c, label: c }))}
            />
          </div>
        )}
      </div>

      {t === 'extract' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 80px', gap: 6, marginBottom: 6 }}>
          <div>
            <span style={label}>Aus Spalte</span>
            <Sel
              value={action.from_column ?? 'original'}
              onChange={v => onChange({ ...action, from_column: v })}
              options={COLUMNS.map(c => ({ value: c, label: c }))}
            />
          </div>
          <div>
            <span style={label}>Fallback-Spalte (wenn Zielspalte bereits gefüllt)</span>
            <Sel
              value={action.to_column_fallback ?? ''}
              onChange={v => onChange({ ...action, to_column_fallback: v || undefined })}
              options={[{ value: '', label: '— keiner —' }, ...COLUMNS.map(c => ({ value: c, label: c }))]}
            />
          </div>
          <div>
            <span style={label}>Gruppe</span>
            <input
              style={inp}
              type="number"
              min={0}
              max={9}
              value={action.group ?? 0}
              onChange={e => onChange({ ...action, group: parseInt(e.target.value) || 0 })}
            />
          </div>
          <div style={{ gridColumn: '1/-1' }}>
            <span style={label}>Muster (Regex)</span>
            <input
              style={inp}
              value={action.pattern ?? ''}
              onChange={e => onChange({ ...action, pattern: e.target.value })}
              placeholder="z.B. \bM[0-9]{1,4}"
            />
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', gridColumn: '1/-1' }}>
            <label style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12, color: '#d4d4d4', cursor: 'pointer' }}>
              <input type="checkbox" checked={action.strip !== false} onChange={e => onChange({ ...action, strip: e.target.checked })} />
              Leerzeichen entfernen
            </label>
            <label style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12, color: '#d4d4d4', cursor: 'pointer' }}>
              <input type="checkbox" checked={action.skip_if_same_case_insensitive === true} onChange={e => onChange({ ...action, skip_if_same_case_insensitive: e.target.checked || undefined })} />
              Überspringen wenn gleich (Groß-/Kleinschreibung ignoriert)
            </label>
            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: '#9e9e9e' }}>Schreibweise:</span>
              <Sel
                value={action.case ?? ''}
                onChange={v => onChange({ ...action, case: (v || undefined) as 'upper' | 'lower' | undefined })}
                options={[
                  { value: '', label: '— unverändert —' },
                  { value: 'upper', label: 'GROSSBUCHSTABEN' },
                  { value: 'lower', label: 'kleinbuchstaben' },
                ]}
                style={{ width: 'auto' }}
              />
            </div>
          </div>
          <div style={{ gridColumn: '1/-1' }}>
            <span style={label}>Auslassen wenn Wert (kommagetrennt)</span>
            <input
              style={inp}
              value={(action.skip_values ?? []).join(', ')}
              onChange={e => onChange({ ...action, skip_values: e.target.value.split(',').map(v => v.trim()).filter(Boolean) || undefined })}
              placeholder="z.B. ohne, x"
            />
          </div>
        </div>
      )}

      {t === 'highlight' && (
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <div style={{ flex: 1 }}>
            <span style={label}>Farbe</span>
            <Sel
              value={action.color ?? 'yellow'}
              onChange={v => onChange({ ...action, color: v as 'yellow' | 'red_white' })}
              options={HIGHLIGHT_COLORS}
            />
          </div>
          <label style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12, color: '#d4d4d4', cursor: 'pointer', paddingTop: 16 }}>
            <input type="checkbox" checked={action.skip_if_unchanged === true} onChange={e => onChange({ ...action, skip_if_unchanged: e.target.checked || undefined })} />
            Nur wenn sich Wert geändert hat
          </label>
        </div>
      )}

      {t === 'note' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 6, alignItems: 'end' }}>
          <div>
            <span style={label}>Notiztext ({'{prev_value}'} = Wert vor der Regel)</span>
            <input
              style={inp}
              value={action.text ?? ''}
              onChange={e => onChange({ ...action, text: e.target.value })}
              placeholder="z.B. war {prev_value}"
            />
          </div>
          <label style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12, color: '#d4d4d4', cursor: 'pointer', paddingBottom: 4 }}>
            <input type="checkbox" checked={action.skip_if_unchanged === true} onChange={e => onChange({ ...action, skip_if_unchanged: e.target.checked || undefined })} />
            Nur wenn geändert
          </label>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single rule editor
// ---------------------------------------------------------------------------

function RuleEditor({
  rule,
  index,
  total,
  onChange,
  onDelete,
  onMove,
}: {
  rule: DesignRule
  index: number
  total: number
  onChange: (r: DesignRule) => void
  onDelete: () => void
  onMove: (dir: -1 | 1) => void
}) {
  const [expanded, setExpanded] = useState(true)

  function addAction() {
    onChange({ ...rule, actions: [...rule.actions, blankAction()] })
  }
  function updateAction(i: number, a: RuleAction) {
    const next = [...rule.actions]
    next[i] = a
    onChange({ ...rule, actions: next })
  }
  function removeAction(i: number) {
    onChange({ ...rule, actions: rule.actions.filter((_, idx) => idx !== i) })
  }

  return (
    <div style={{
      background: '#252526', border: '1px solid #3e3e3e', borderRadius: 6,
      marginBottom: 8, overflow: 'hidden',
    }}>
      {/* Header */}
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px',
          background: '#2d2d2d', borderBottom: expanded ? '1px solid #3e3e3e' : 'none', cursor: 'pointer',
        }}
        onClick={() => setExpanded(e => !e)}
      >
        <span style={{ color: '#858585', fontSize: 11, userSelect: 'none' }}>{expanded ? '▼' : '▶'}</span>
        <span style={{ fontSize: 13, color: '#d4d4d4', flex: 1, fontWeight: 500 }}>
          {rule.description || rule.id || `Regel ${index + 1}`}
        </span>
        <span style={{ fontSize: 11, color: '#858585', background: '#1e1e1e', borderRadius: 3, padding: '1px 5px' }}>
          {rule.actions.length} Aktion{rule.actions.length !== 1 ? 'en' : ''}
        </span>
        {/* Reorder buttons */}
        <button
          disabled={index === 0}
          onClick={e => { e.stopPropagation(); onMove(-1) }}
          title="Nach oben"
          style={{ background: 'none', border: 'none', color: index === 0 ? '#444' : '#858585', cursor: index === 0 ? 'default' : 'pointer', fontSize: 14 }}
        >↑</button>
        <button
          disabled={index === total - 1}
          onClick={e => { e.stopPropagation(); onMove(1) }}
          title="Nach unten"
          style={{ background: 'none', border: 'none', color: index === total - 1 ? '#444' : '#858585', cursor: index === total - 1 ? 'default' : 'pointer', fontSize: 14 }}
        >↓</button>
        <button
          onClick={e => { e.stopPropagation(); if (window.confirm('Regel löschen?')) onDelete() }}
          title="Löschen"
          style={{ background: 'none', border: 'none', color: '#cf6679', cursor: 'pointer', fontSize: 14 }}
        >🗑</button>
      </div>

      {expanded && (
        <div style={{ padding: 10 }}>
          {/* ID + Description */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 8, marginBottom: 10 }}>
            <div>
              <span style={label}>ID (technisch, eindeutig)</span>
              <input
                style={inp}
                value={rule.id}
                onChange={e => onChange({ ...rule, id: e.target.value.replace(/\s/g, '_') })}
                placeholder="z.B. clear_no_name"
              />
            </div>
            <div>
              <span style={label}>Beschreibung</span>
              <input
                style={inp}
                value={rule.description ?? ''}
                onChange={e => onChange({ ...rule, description: e.target.value })}
                placeholder="Kurze Erklärung was diese Regel tut"
              />
            </div>
          </div>

          {/* for_each_column */}
          <div style={{ marginBottom: 10 }}>
            <span style={label}>Für jede Spalte ausführen (leer = nur einmal)</span>
            <input
              style={inp}
              value={(rule.for_each_column ?? []).join(', ')}
              onChange={e => {
                const cols = e.target.value.split(',').map(v => v.trim()).filter(Boolean)
                onChange({ ...rule, for_each_column: cols.length ? cols : undefined })
              }}
              placeholder="z.B. text, text2"
            />
          </div>

          {/* Condition */}
          <div style={{ marginBottom: 10 }}>
            <span style={{ ...label, fontSize: 12, color: '#c5a028', marginBottom: 4 }}>📋 Bedingung</span>
            <ConditionEditor
              cond={rule.condition ?? { type: 'not_empty', column: 'original' } as SimpleCondition}
              onChange={c => onChange({ ...rule, condition: c })}
            />
          </div>

          {/* Actions */}
          <div>
            <span style={{ ...label, fontSize: 12, color: '#4ec9b0', marginBottom: 4 }}>⚡ Aktionen</span>
            {rule.actions.map((action, i) => (
              <div key={i} style={{ display: 'flex', gap: 4 }}>
                <div style={{ flex: 1 }}>
                  <ActionEditor action={action} onChange={a => updateAction(i, a)} />
                </div>
                <button
                  onClick={() => removeAction(i)}
                  title="Aktion entfernen"
                  style={{ background: 'none', border: 'none', color: '#858585', cursor: 'pointer', fontSize: 16, alignSelf: 'flex-start', paddingTop: 8 }}
                >×</button>
              </div>
            ))}
            <button
              onClick={addAction}
              style={{ fontSize: 11, color: '#4ec9b0', background: 'none', border: 'none', cursor: 'pointer', padding: 0, marginTop: 2 }}
            >+ Aktion hinzufügen</button>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function DesignRulesPanel({ toolId }: { toolId: string }) {
  const [rules, setRules]       = useState<DesignRule[]>([])
  const [loading, setLoading]   = useState(true)
  const [dirty, setDirty]       = useState(false)
  const [saving, setSaving]     = useState(false)
  const [status, setStatus]     = useState<string | null>(null)
  const [error, setError]       = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    getDesignRules(toolId)
      .then(data => { setRules(data.rules); setDirty(false) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [toolId])

  const updateRule = useCallback((i: number, r: DesignRule) => {
    setRules(prev => { const next = [...prev]; next[i] = r; return next })
    setDirty(true)
    setStatus(null)
  }, [])

  const deleteRule = useCallback((i: number) => {
    setRules(prev => prev.filter((_, idx) => idx !== i))
    setDirty(true)
    setStatus(null)
  }, [])

  const moveRule = useCallback((i: number, dir: -1 | 1) => {
    setRules(prev => {
      const next = [...prev]
      const j = i + dir
      if (j < 0 || j >= next.length) return prev;
      [next[i], next[j]] = [next[j], next[i]]
      return next
    })
    setDirty(true)
  }, [])

  const addRule = useCallback(() => {
    setRules(prev => [...prev, blankRule()])
    setDirty(true)
    setStatus(null)
  }, [])

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const res = await saveDesignRules(toolId, rules)
      setDirty(false)
      setStatus(`✓ ${res.count} Regel${res.count !== 1 ? 'n' : ''} gespeichert`)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#858585', fontSize: 13 }}>
        Lade Regeln …
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#1e1e1e', overflow: 'hidden' }}>
      {/* Toolbar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '8px 16px',
        background: '#252526', borderBottom: '1px solid #3e3e3e', flexShrink: 0,
      }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: '#d4d4d4', flex: 1 }}>Design-Importregeln</span>
        <span style={{ fontSize: 11, color: '#858585' }}>{rules.length} Regel{rules.length !== 1 ? 'n' : ''}</span>
        <button
          onClick={addRule}
          style={{ fontSize: 12, padding: '4px 10px', borderRadius: 4, background: '#007acc', color: '#fff', border: 'none', cursor: 'pointer' }}
        >+ Neue Regel</button>
        <button
          onClick={handleSave}
          disabled={!dirty || saving}
          style={{
            fontSize: 12, padding: '4px 12px', borderRadius: 4, border: 'none', cursor: dirty && !saving ? 'pointer' : 'default',
            background: dirty && !saving ? '#388e3c' : '#2d2d2d',
            color: dirty && !saving ? '#fff' : '#555',
          }}
        >{saving ? 'Speichere …' : 'Speichern'}</button>
      </div>

      {/* Status / error bar */}
      {(status || error) && (
        <div style={{
          padding: '5px 16px', fontSize: 12, flexShrink: 0,
          background: error ? '#3b1515' : '#1a3a1a',
          color: error ? '#f48771' : '#89d185',
          borderBottom: '1px solid #3e3e3e',
        }}>
          {error ?? status}
        </div>
      )}

      {/* Dirty indicator */}
      {dirty && !saving && (
        <div style={{ padding: '3px 16px', fontSize: 11, color: '#c5a028', background: '#2a2400', flexShrink: 0 }}>
          Ungespeicherte Änderungen
        </div>
      )}

      {/* Rule list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
        {rules.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#555', fontSize: 13, paddingTop: 60 }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>📋</div>
            Keine Regeln vorhanden. Klicke „+ Neue Regel" um zu beginnen.
          </div>
        ) : (
          rules.map((rule, i) => (
            <RuleEditor
              key={rule.id + i}
              rule={rule}
              index={i}
              total={rules.length}
              onChange={r => updateRule(i, r)}
              onDelete={() => deleteRule(i)}
              onMove={dir => moveRule(i, dir)}
            />
          ))
        )}
      </div>
    </div>
  )
}
