interface Props {
  url: string
}

export default function BrowserTab({ url }: Props) {
  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* URL bar */}
      <div
        className="flex items-center gap-3 px-4 py-2 shrink-0"
        style={{ background: '#2d2d2d', borderBottom: '1px solid #3e3e3e' }}
      >
        <span className="text-xs text-vscode-muted font-mono flex-1 truncate">{url}</span>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-vscode-accent hover:text-vscode-accent-hover transition-colors shrink-0"
        >
          Open externally ↗
        </a>
      </div>

      {/* iframe */}
      <div className="flex-1 overflow-hidden">
        <iframe
          src={url}
          className="w-full h-full border-none"
          title="Browser Tab"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
        />
      </div>
    </div>
  )
}
