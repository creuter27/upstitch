import { useEffect, useRef, useCallback } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { useStore } from '../store'

const WS_URL = import.meta.env.DEV
  ? 'ws://localhost:8000/ws/terminal'
  : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/terminal`

export default function TerminalPanel() {
  const { terminalCollapsed, toggleTerminal } = useStore()
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const mountedRef = useRef(false)
  const fitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const sendToTerminal = useCallback((command: string) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'input', data: command }))
    }
  }, [])

  // Listen for terminal-command custom events from ToolPanel
  useEffect(() => {
    function handler(e: Event) {
      const detail = (e as CustomEvent).detail
      if (detail?.command) {
        sendToTerminal(detail.command)
      }
    }
    window.addEventListener('terminal-command', handler)
    return () => window.removeEventListener('terminal-command', handler)
  }, [sendToTerminal])

  // Initialize terminal
  useEffect(() => {
    if (mountedRef.current) return
    if (!containerRef.current) return
    mountedRef.current = true

    const term = new Terminal({
      theme: {
        background: '#1e1e1e',
        foreground: '#d4d4d4',
        cursor: '#d4d4d4',
        selectionBackground: '#264f78',
        black: '#1e1e1e',
        brightBlack: '#858585',
        red: '#cd3131',
        brightRed: '#f14c4c',
        green: '#0dbc79',
        brightGreen: '#23d18b',
        yellow: '#e5e510',
        brightYellow: '#f5f543',
        blue: '#2472c8',
        brightBlue: '#3b8eea',
        magenta: '#bc3fbc',
        brightMagenta: '#d670d6',
        cyan: '#11a8cd',
        brightCyan: '#29b8db',
        white: '#e5e5e5',
        brightWhite: '#e5e5e5',
      },
      fontSize: 13,
      fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", Consolas, monospace',
      cursorBlink: true,
      allowProposedApi: true,
    })
    termRef.current = term

    const fitAddon = new FitAddon()
    fitAddonRef.current = fitAddon
    term.loadAddon(fitAddon)

    const webLinksAddon = new WebLinksAddon()
    term.loadAddon(webLinksAddon)

    term.open(containerRef.current)
    fitAddon.fit()

    // Connect WebSocket
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws
    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      const { cols, rows } = term
      ws.send(JSON.stringify({ type: 'resize', cols, rows }))
    }

    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(e.data))
      } else if (typeof e.data === 'string') {
        term.write(e.data)
      }
    }

    ws.onerror = () => {
      term.writeln('\r\n\x1b[31m[Connection error]\x1b[0m')
    }

    ws.onclose = () => {
      term.writeln('\r\n\x1b[33m[Terminal disconnected]\x1b[0m')
    }

    // Terminal input → WebSocket
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }))
      }
    })

    // Terminal resize → WebSocket
    term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols, rows }))
      }
    })

    // Ctrl-C: copy selection if text is selected; otherwise send \x03 (interrupt)
    // Ctrl-V: paste clipboard text into terminal
    term.attachCustomKeyEventHandler((e: KeyboardEvent) => {
      if (e.type !== 'keydown') return true
      if (e.ctrlKey && e.key === 'c' && term.hasSelection()) {
        navigator.clipboard.writeText(term.getSelection()).catch(() => {})
        return false
      }
      if (e.ctrlKey && e.key === 'v') {
        navigator.clipboard.readText().then((text) => sendToTerminal(text)).catch(() => {})
        return false
      }
      return true
    })

    // ResizeObserver to auto-fit — debounced so xterm only resizes after the
    // drag settles, preventing corrupted output during active panel resize.
    // After fit: explicitly send the new dimensions to the PTY backend so it
    // issues SIGWINCH, then force-refresh the terminal viewport so existing
    // output is redrawn at the correct cursor position.
    const resizeObserver = new ResizeObserver(() => {
      if (fitTimerRef.current) clearTimeout(fitTimerRef.current)
      fitTimerRef.current = setTimeout(() => {
        fitAddon.fit()
        const { cols, rows } = term
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'resize', cols, rows }))
        }
        term.refresh(0, rows - 1)
      }, 150)
    })
    if (containerRef.current) {
      resizeObserver.observe(containerRef.current)
    }

    return () => {
      resizeObserver.disconnect()
      if (fitTimerRef.current) clearTimeout(fitTimerRef.current)
      ws.close()
      term.dispose()
      mountedRef.current = false
    }
  }, [])

  // Fit when terminal panel is expanded, then sync PTY size and redraw
  useEffect(() => {
    if (!terminalCollapsed && fitAddonRef.current && termRef.current) {
      setTimeout(() => {
        fitAddonRef.current?.fit()
        const term = termRef.current!
        const { cols, rows } = term
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'resize', cols, rows }))
        }
        term.refresh(0, rows - 1)
      }, 150)
    }
  }, [terminalCollapsed])

  return (
    <div className="h-full flex flex-col overflow-hidden" style={{ background: '#1e1e1e' }}>
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-1 shrink-0"
        style={{ background: '#2d2d2d', borderTop: '1px solid #3e3e3e', minHeight: '32px' }}
      >
        <span className="text-xs font-semibold text-vscode-muted uppercase tracking-wider">
          Terminal
        </span>
        <button
          onClick={toggleTerminal}
          className="text-vscode-muted hover:text-vscode-text transition-colors text-xs p-1"
          title="Collapse terminal"
        >
          ∨
        </button>
      </div>

      {/* xterm container */}
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden"
        style={{ padding: '4px 8px' }}
      />
    </div>
  )
}
