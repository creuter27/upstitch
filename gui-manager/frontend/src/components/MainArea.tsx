import { useStore } from '../store'
import ToolPanel from './ToolPanel'
import FileEditor from './FileEditor'
import BrowserTab from './BrowserTab'
import SettingsPage from './SettingsPage'
import ReorderPanel from './ReorderPanel'

const TYPE_ICON: Record<string, string> = {
  tool: '🔧',
  file: '📄',
  browser: '🌐',
  settings: '⚙',
}

export default function MainArea() {
  const { tabs, activeTabId, closeTab, setActiveTab } = useStore()

  const activeTab = tabs.find((t) => t.id === activeTabId)

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: '#1e1e1e' }}>
      {/* Tab bar */}
      {tabs.length > 0 && (
        <div
          className="flex overflow-x-auto shrink-0"
          style={{ background: '#252526', borderBottom: '1px solid #3e3e3e', minHeight: '35px' }}
        >
          {tabs.map((tab) => {
            const isActive = tab.id === activeTabId
            return (
              <div
                key={tab.id}
                className="flex items-center gap-1 px-3 cursor-pointer shrink-0 select-none"
                style={{
                  background: isActive ? '#1e1e1e' : '#2d2d2d',
                  borderRight: '1px solid #3e3e3e',
                  borderTop: isActive ? '1px solid #007acc' : '1px solid transparent',
                  minHeight: '35px',
                  maxWidth: '200px',
                }}
                onClick={() => setActiveTab(tab.id)}
              >
                <span className="text-xs opacity-70">{TYPE_ICON[tab.type] || '📄'}</span>
                <span
                  className="text-xs truncate"
                  style={{ color: isActive ? '#fff' : '#858585', maxWidth: '140px' }}
                >
                  {tab.title}
                </span>
                <button
                  className="ml-1 text-vscode-muted hover:text-vscode-text text-xs rounded px-0.5 shrink-0"
                  onClick={(e) => {
                    e.stopPropagation()
                    closeTab(tab.id)
                  }}
                  title="Close tab"
                >
                  ×
                </button>
              </div>
            )
          })}
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {!activeTab ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center text-vscode-muted">
              <div className="text-4xl mb-3 opacity-30">⚙</div>
              <div className="text-sm">Select a tool or file from the sidebar</div>
            </div>
          </div>
        ) : activeTab.type === 'tool' && activeTab.toolId ? (
          <ToolPanel toolId={activeTab.toolId} />
        ) : activeTab.type === 'file' && activeTab.filePath ? (
          <FileEditor
            filePath={activeTab.filePath}
            language={activeTab.fileLanguage}
          />
        ) : activeTab.type === 'browser' && activeTab.url ? (
          <BrowserTab url={activeTab.url} />
        ) : activeTab.type === 'reorder' && activeTab.toolId ? (
          <ReorderPanel toolId={activeTab.toolId} />
        ) : activeTab.type === 'settings' ? (
          <SettingsPage />
        ) : null}
      </div>
    </div>
  )
}
