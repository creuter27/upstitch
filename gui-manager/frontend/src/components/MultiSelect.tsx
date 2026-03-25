import { useEffect, useRef, useState } from 'react'

interface Props {
  label: string
  options: string[]
  selected: Set<string>
  onChange: (next: Set<string>) => void
  minWidth?: string
}

export default function MultiSelect({ label, options, selected, onChange, minWidth = '120px' }: Props) {
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
        style={{ background: '#2d2d2d', border: '1px solid #3e3e3e', color: '#d4d4d4', minWidth }}
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
