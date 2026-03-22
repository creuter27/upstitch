import { useEffect } from 'react'
import { PanelGroup, Panel, PanelResizeHandle } from 'react-resizable-panels'
import { useStore } from './store'
import { getTools } from './api'
import LoginPage from './components/LoginPage'
import TopBar from './components/TopBar'
import Sidebar from './components/Sidebar'
import MainArea from './components/MainArea'
import TerminalPanel from './components/TerminalPanel'

export default function App() {
  const { token, setTools, logout, sidebarCollapsed, terminalCollapsed } = useStore()

  useEffect(() => {
    if (!token) return
    getTools()
      .then(setTools)
      .catch((err) => {
        console.error(err)
        if (String(err.message).includes('HTTP 401')) {
          logout()
        }
      })
  }, [token])

  if (!token) {
    return <LoginPage />
  }

  return (
    <div className="flex flex-col h-screen bg-vscode-bg text-vscode-text overflow-hidden">
      <TopBar />
      <div className="flex flex-1 overflow-hidden">
        <PanelGroup direction="horizontal" className="flex-1">
          {/* Sidebar */}
          {!sidebarCollapsed && (
            <>
              <Panel
                defaultSize={18}
                minSize={12}
                maxSize={35}
                className="overflow-hidden"
              >
                <Sidebar />
              </Panel>
              <PanelResizeHandle className="w-1 bg-vscode-border hover:bg-vscode-accent transition-colors cursor-col-resize" />
            </>
          )}

          {/* Main content + terminal */}
          <Panel className="overflow-hidden flex flex-col">
            <PanelGroup direction="vertical" className="flex-1">
              {/* Main content area */}
              <Panel
                defaultSize={terminalCollapsed ? 100 : 70}
                minSize={30}
                className="overflow-hidden"
              >
                <MainArea />
              </Panel>

              {/* Terminal panel */}
              {!terminalCollapsed && (
                <>
                  <PanelResizeHandle className="h-1 bg-vscode-border hover:bg-vscode-accent transition-colors cursor-row-resize" />
                  <Panel
                    defaultSize={30}
                    minSize={10}
                    maxSize={70}
                    className="overflow-hidden"
                  >
                    <TerminalPanel />
                  </Panel>
                </>
              )}
            </PanelGroup>
          </Panel>
        </PanelGroup>
      </div>
    </div>
  )
}
