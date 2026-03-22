import { create } from 'zustand'
import { Tool } from './api'

export interface Tab {
  id: string
  type: 'tool' | 'file' | 'browser' | 'settings' | 'reorder'
  title: string
  // type-specific
  toolId?: string
  filePath?: string
  fileLanguage?: string
  url?: string
}

interface AppState {
  // Auth
  token: string | null
  username: string | null
  permissions: string[]
  setAuth: (token: string, username: string, permissions: string[]) => void
  logout: () => void

  // Tabs
  tabs: Tab[]
  activeTabId: string | null
  openTab: (tab: Tab) => void
  closeTab: (id: string) => void
  setActiveTab: (id: string) => void

  // Sidebar
  sidebarCollapsed: boolean
  toggleSidebar: () => void

  // Terminal
  terminalCollapsed: boolean
  toggleTerminal: () => void

  // Tools
  tools: Tool[]
  setTools: (tools: Tool[]) => void

  // Settings
  settings: Record<string, string>
  setSettings: (s: Record<string, string>) => void
}

export const useStore = create<AppState>((set, get) => ({
  // Auth — restore from localStorage on init
  token: localStorage.getItem('token'),
  username: localStorage.getItem('username'),
  permissions: JSON.parse(localStorage.getItem('permissions') || '[]'),

  setAuth: (token, username, permissions) => {
    localStorage.setItem('token', token)
    localStorage.setItem('username', username)
    localStorage.setItem('permissions', JSON.stringify(permissions))
    set({ token, username, permissions })
  },

  logout: () => {
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    localStorage.removeItem('permissions')
    set({ token: null, username: null, permissions: [], tabs: [], activeTabId: null })
  },

  // Tabs
  tabs: [],
  activeTabId: null,

  openTab: (tab) => {
    const { tabs } = get()
    const existing = tabs.find((t) => t.id === tab.id)
    if (existing) {
      set({ activeTabId: tab.id })
      return
    }
    set({ tabs: [...tabs, tab], activeTabId: tab.id })
  },

  closeTab: (id) => {
    const { tabs, activeTabId } = get()
    const newTabs = tabs.filter((t) => t.id !== id)
    let newActive = activeTabId
    if (activeTabId === id) {
      const idx = tabs.findIndex((t) => t.id === id)
      newActive = newTabs[idx]?.id ?? newTabs[idx - 1]?.id ?? null
    }
    set({ tabs: newTabs, activeTabId: newActive })
  },

  setActiveTab: (id) => set({ activeTabId: id }),

  // Sidebar
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

  // Terminal
  terminalCollapsed: false,
  toggleTerminal: () => set((s) => ({ terminalCollapsed: !s.terminalCollapsed })),

  // Tools
  tools: [],
  setTools: (tools) => set({ tools }),

  // Settings
  settings: {},
  setSettings: (settings) => set({ settings }),
}))
