import { useState } from 'react'
import { useStore } from '../store'
import { getToolFileTree, FileTreeNode } from '../api'

interface FileTreeProps {
  nodes: FileTreeNode[]
  depth: number
  onFileClick: (node: FileTreeNode) => void
  activePath: string | null
}

function FileTreeView({ nodes, depth, onFileClick, activePath }: FileTreeProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  function toggle(path: string) {
    setExpanded((prev) => ({ ...prev, [path]: !prev[path] }))
  }

  return (
    <>
      {nodes.map((node) => {
        const isExpanded = expanded[node.path]
        const isActive = activePath === node.path
        const paddingLeft = `${8 + depth * 12}px`

        if (node.type === 'dir') {
          return (
            <div key={node.path}>
              <button
                onClick={() => toggle(node.path)}
                className="w-full text-left flex items-center gap-1 py-0.5 hover:bg-vscode-hover text-vscode-text text-xs"
                style={{ paddingLeft }}
              >
                <span className="opacity-60 text-xs">{isExpanded ? '▼' : '▶'}</span>
                <span className="opacity-70 mr-1">📁</span>
                <span>{node.name}</span>
              </button>
              {isExpanded && node.children && (
                <FileTreeView
                  nodes={node.children}
                  depth={depth + 1}
                  onFileClick={onFileClick}
                  activePath={activePath}
                />
              )}
            </div>
          )
        }

        return (
          <button
            key={node.path}
            onClick={() => onFileClick(node)}
            className="w-full text-left flex items-center gap-1 py-0.5 hover:bg-vscode-hover text-xs"
            style={{
              paddingLeft,
              background: isActive ? '#264f78' : 'transparent',
              color: isActive ? '#fff' : '#d4d4d4',
            }}
          >
            <span className="opacity-50 mr-1">📄</span>
            <span>{node.name}</span>
          </button>
        )
      })}
    </>
  )
}

export default function Sidebar() {
  const { tools, openTab, activeTabId, toggleSidebar } = useStore()
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({})
  const [fileTrees, setFileTrees] = useState<Record<string, FileTreeNode[]>>({})
  const [loadingTree, setLoadingTree] = useState<Record<string, boolean>>({})

  // Derive active path from active tab
  const tabs = useStore((s) => s.tabs)
  const activeTab = tabs.find((t) => t.id === activeTabId)
  const activePath = activeTab?.type === 'file' ? activeTab.filePath ?? null : null

  async function toggleTool(toolId: string) {
    const nowExpanded = !expandedTools[toolId]
    setExpandedTools((prev) => ({ ...prev, [toolId]: nowExpanded }))
    if (nowExpanded && !fileTrees[toolId]) {
      setLoadingTree((prev) => ({ ...prev, [toolId]: true }))
      try {
        const tree = await getToolFileTree(toolId)
        setFileTrees((prev) => ({ ...prev, [toolId]: tree }))
      } catch (e) {
        console.error('Failed to load file tree', e)
      } finally {
        setLoadingTree((prev) => ({ ...prev, [toolId]: false }))
      }
    }
  }

  function openToolPanel(toolId: string, toolName: string) {
    openTab({ id: `tool-${toolId}`, type: 'tool', title: toolName, toolId })
  }

  function openReorderTab(toolId: string) {
    openTab({ id: `reorder-${toolId}`, type: 'reorder', title: 'Bestellung', toolId })
  }

  function openFile(node: FileTreeNode) {
    openTab({
      id: `file-${node.path}`,
      type: 'file',
      title: node.name,
      filePath: node.path,
    })
  }

  return (
    <div
      className="h-full flex flex-col overflow-hidden"
      style={{ background: '#252526', borderRight: '1px solid #3e3e3e' }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 shrink-0"
        style={{ borderBottom: '1px solid #3e3e3e', minHeight: '36px' }}
      >
        <span className="text-xs font-semibold text-vscode-muted uppercase tracking-wider">
          Tools
        </span>
        <button
          onClick={toggleSidebar}
          className="text-vscode-muted hover:text-vscode-text transition-colors text-xs p-1"
          title="Collapse sidebar"
        >
          ←
        </button>
      </div>

      {/* Tool list — flat sorted entries */}
      <div className="flex-1 overflow-y-auto py-1">
        {tools.length === 0 && (
          <div className="px-4 py-3 text-vscode-muted text-xs">No tools loaded</div>
        )}
        {(() => {
          // Reorder (🛒) entries always appear before tool (🔧) entries.
          // Within each group, sort by sidebar_order.
          type Entry =
            | { kind: 'tool'; tool: typeof tools[0] }
            | { kind: 'reorder'; tool: typeof tools[0] }
          const reorderEntries: Entry[] = []
          const toolEntries: Entry[] = []
          for (const tool of tools) {
            toolEntries.push({ kind: 'tool', tool })
            if (tool.reorder) {
              reorderEntries.push({ kind: 'reorder', tool })
            }
          }
          const byOrder = (a: Entry, b: Entry) =>
            (a.tool.sidebar_order ?? 99) - (b.tool.sidebar_order ?? 99)
          reorderEntries.sort(byOrder)
          toolEntries.sort(byOrder)
          const entries: Entry[] = [...reorderEntries, ...toolEntries]

          return entries.map((entry) => {
            if (entry.kind === 'reorder') {
              const { tool } = entry
              const isActive = activeTab?.id === `reorder-${tool.id}`
              return (
                <div
                  key={`reorder-${tool.id}`}
                  className="flex items-center hover:bg-vscode-hover"
                  style={{ background: isActive ? '#37373d' : 'transparent' }}
                >
                  <div className="w-6 shrink-0" />
                  <button
                    onClick={() => openReorderTab(tool.id)}
                    className="flex-1 text-left py-1 pr-2 text-sm truncate"
                    style={{ color: isActive ? '#fff' : '#d4d4d4' }}
                  >
                    🛒 Bestellung
                  </button>
                </div>
              )
            }

            const { tool } = entry
            const isToolActive = activeTab?.type === 'tool' && activeTab.toolId === tool.id
            const isExpanded = expandedTools[tool.id]
            return (
              <div key={`tool-${tool.id}`}>
                <div
                  className="flex items-center hover:bg-vscode-hover"
                  style={{ background: isToolActive ? '#37373d' : 'transparent' }}
                >
                  <button
                    onClick={() => toggleTool(tool.id)}
                    className="p-1 text-vscode-muted hover:text-vscode-text text-xs w-6 shrink-0"
                    style={{ paddingLeft: '6px' }}
                  >
                    {isExpanded ? '▼' : '▶'}
                  </button>
                  <button
                    onClick={() => openToolPanel(tool.id, tool.name)}
                    className="flex-1 text-left py-1 pr-2 text-sm text-vscode-text truncate"
                    style={{ color: isToolActive ? '#fff' : '#d4d4d4' }}
                  >
                    🔧 {tool.name}
                  </button>
                </div>
                {isExpanded && (
                  <div>
                    {loadingTree[tool.id] ? (
                      <div className="text-vscode-muted text-xs px-8 py-1">Loading...</div>
                    ) : fileTrees[tool.id] ? (
                      <FileTreeView
                        nodes={fileTrees[tool.id]}
                        depth={1}
                        onFileClick={openFile}
                        activePath={activePath}
                      />
                    ) : (
                      <div className="text-vscode-muted text-xs px-8 py-1">No files</div>
                    )}
                  </div>
                )}
              </div>
            )
          })
        })()}
      </div>

      {/* Footer */}
      <div
        className="px-3 py-2 shrink-0 text-vscode-muted text-xs"
        style={{ borderTop: '1px solid #3e3e3e' }}
      >
        v0.1.0
      </div>
    </div>
  )
}
