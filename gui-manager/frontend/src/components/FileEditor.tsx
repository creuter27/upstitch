import { useEffect, useRef, useState, useCallback } from 'react'
import Editor, { OnMount } from '@monaco-editor/react'
import { readFile, writeFile } from '../api'

interface Props {
  filePath: string
  language?: string
}

// Module-level cache — survives tab switches (component remounts)
const draftCache: Record<string, string> = {}
export const dirtyPaths = new Set<string>()

export default function FileEditor({ filePath, language }: Props) {
  const [content, setContent] = useState('')
  const [originalContent, setOriginalContent] = useState('')
  const [detectedLanguage, setDetectedLanguage] = useState(language || 'plaintext')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saveStatus, setSaveStatus] = useState<'saved' | 'unsaved' | 'saving' | 'error'>('saved')
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)

  useEffect(() => {
    setLoading(true)
    setError('')
    readFile(filePath)
      .then((data) => {
        const draft = draftCache[filePath]
        const serverContent = data.content
        setOriginalContent(serverContent)
        setDetectedLanguage(language || data.language)
        if (draft !== undefined && draft !== serverContent) {
          setContent(draft)
          setSaveStatus('unsaved')
        } else {
          setContent(serverContent)
          setSaveStatus('saved')
        }
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [filePath])

  const handleSave = useCallback(async () => {
    setSaveStatus('saving')
    try {
      await writeFile(filePath, content)
      setOriginalContent(content)
      delete draftCache[filePath]
      dirtyPaths.delete(filePath)
      setSaveStatus('saved')
    } catch (e) {
      setSaveStatus('error')
      console.error('Save failed:', e)
    }
  }, [filePath, content])

  // Ctrl+S / Cmd+S handler
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        handleSave()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [handleSave])

  function handleEditorChange(value: string | undefined) {
    const newValue = value ?? ''
    setContent(newValue)
    if (newValue !== originalContent) {
      draftCache[filePath] = newValue
      dirtyPaths.add(filePath)
      setSaveStatus('unsaved')
    } else {
      delete draftCache[filePath]
      dirtyPaths.delete(filePath)
      setSaveStatus('saved')
    }
  }

  function handleEditorMount(editor: Parameters<OnMount>[0]) {
    editorRef.current = editor
  }

  const statusColor = {
    saved: '#4ec9b0',
    unsaved: '#f97316',
    saving: '#858585',
    error: '#f87171',
  }[saveStatus]

  const statusLabel = {
    saved: '✓ Saved',
    unsaved: '● Unsaved',
    saving: 'Saving...',
    error: '✗ Save failed',
  }[saveStatus]

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-vscode-muted text-sm">
        Loading {filePath}...
      </div>
    )
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-red-400 text-sm p-8">
        {error}
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div
        className="flex items-center justify-between px-4 py-1.5 shrink-0"
        style={{ background: '#2d2d2d', borderBottom: '1px solid #3e3e3e' }}
      >
        <div className="flex items-center gap-3">
          <span className="text-xs text-vscode-muted font-mono truncate max-w-xs">
            {filePath}
          </span>
          <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: '#3e3e3e', color: '#858585' }}>
            {detectedLanguage}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs" style={{ color: statusColor }}>
            {statusLabel}
          </span>
          <button
            onClick={handleSave}
            disabled={saveStatus === 'saving' || saveStatus === 'saved'}
            className="px-3 py-1 rounded text-xs font-medium transition-colors"
            style={{
              background: saveStatus === 'unsaved' ? '#007acc' : '#3e3e3e',
              color: saveStatus === 'unsaved' ? '#fff' : '#858585',
              cursor: saveStatus === 'unsaved' ? 'pointer' : 'default',
            }}
          >
            Save
          </button>
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 overflow-hidden">
        <Editor
          height="100%"
          language={detectedLanguage}
          value={content}
          theme="vs-dark"
          onChange={handleEditorChange}
          onMount={handleEditorMount}
          options={{
            fontSize: 13,
            minimap: { enabled: false },
            wordWrap: 'on',
            lineNumbers: 'on',
            scrollBeyondLastLine: false,
            automaticLayout: true,
            tabSize: 2,
            insertSpaces: true,
          }}
        />
      </div>
    </div>
  )
}
