const IS_DEV = import.meta.env.DEV
const BASE_URL = IS_DEV ? 'http://localhost:8000/api' : '/api'

function getToken(): string | null {
  return localStorage.getItem('token')
}

async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  return res
}

export async function login(username: string, password: string): Promise<{
  access_token: string
  username: string
  permissions: string[]
}> {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Login failed: ${text}`)
  }
  return res.json()
}

export async function getMe(): Promise<{
  id: number
  username: string
  is_active: boolean
  permissions: string[]
}> {
  const res = await apiFetch('/auth/me')
  return res.json()
}

export async function getTools(): Promise<Tool[]> {
  const res = await apiFetch('/tools')
  return res.json()
}

export async function getTool(id: string): Promise<Tool> {
  const res = await apiFetch(`/tools/${id}`)
  return res.json()
}

export async function getManufacturers(toolId: string): Promise<Manufacturer[]> {
  const res = await apiFetch(`/tools/${toolId}/manufacturers`)
  return res.json()
}

export async function getSheetExists(toolId: string, code: string): Promise<boolean> {
  const res = await apiFetch(`/tools/${toolId}/manufacturers/${code}/sheet-exists`)
  const data = await res.json()
  return data.exists
}

export async function getAddStockPreview(
  toolId: string,
  code: string,
  tab?: string,
): Promise<{
  tab: string
  items: AddStockPreviewItem[]
  errors: { sku?: string; error: string }[]
}> {
  const params = tab ? `?tab=${encodeURIComponent(tab)}` : ''
  const res = await apiFetch(`/tools/${toolId}/manufacturers/${code}/add-stock-preview${params}`)
  return res.json()
}

export async function postAddStockApply(
  toolId: string,
  code: string,
  items: { sku: string; billbeeId: number; qty: number }[],
): Promise<{ ok: boolean; updated: number; errors: { sku: string; error: string }[] }> {
  const res = await apiFetch(`/tools/${toolId}/manufacturers/${code}/add-stock-apply`, {
    method: 'POST',
    body: JSON.stringify({ items }),
  })
  return res.json()
}

export async function getPackaging(toolId: string): Promise<{
  mappings: PackagingMapping[]
  packageTypes: PackageType[]
}> {
  const res = await apiFetch(`/tools/${toolId}/packaging`)
  return res.json()
}

export async function updatePackaging(toolId: string, comboKey: string, name: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(`/tools/${toolId}/packaging/update`, {
    method: 'POST',
    body: JSON.stringify({ comboKey, name }),
  })
  return res.json()
}

export async function getToolFileTree(id: string): Promise<FileTreeNode[]> {
  const res = await apiFetch(`/tools/${id}/filetree`)
  return res.json()
}

export async function readFile(path: string): Promise<{
  path: string
  content: string
  language: string
}> {
  const res = await apiFetch(`/files/read?path=${encodeURIComponent(path)}`)
  return res.json()
}

export async function writeFile(path: string, content: string): Promise<{ ok: boolean }> {
  const res = await apiFetch('/files/write', {
    method: 'POST',
    body: JSON.stringify({ path, content }),
  })
  return res.json()
}

export async function getSettings(): Promise<Record<string, string>> {
  const res = await apiFetch('/settings')
  return res.json()
}

export async function updateSetting(key: string, value: string): Promise<{ ok: boolean }> {
  const res = await apiFetch('/settings', {
    method: 'PUT',
    body: JSON.stringify({ key, value }),
  })
  return res.json()
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ToolFunction {
  name: string
  command: string
  description: string
  requires_confirm?: boolean
  is_launch?: boolean
  supports_dry_run?: boolean
}

export interface ToolPanel {
  id: string
  name: string
  panel_type: string
}

export interface Tool {
  id: string
  name: string
  path: string
  description: string
  start_url?: string
  venv?: string
  reorder?: boolean
  sidebar_order?: number
  reorder_order?: number
  functions?: ToolFunction[]
  file_tree?: string[]
  panels?: ToolPanel[]
  permissions_required?: string[]
}

export interface PackagingMapping {
  comboKey: string
  name: string
  id: number | null
  setAt: string | null
}

export interface PackageType {
  name: string
  id: number | null
}

export interface Manufacturer {
  code: string
  name: string
  reorderingURL: string
  useNoCrawl: boolean
  pythonCmd: string
}

export interface AddStockPreviewItem {
  sku: string
  billbeeId: number
  billbeeStock: number | null
  sheetStockCurrent: number | null
  sheetStockTarget: number | null
  qty: number
  newStock: number | null
}

export interface FileTreeNode {
  name: string
  path: string
  type: 'file' | 'dir'
  children?: FileTreeNode[]
}

export interface InventoryProduct {
  sku: string
  billbeeId: number
  title?: string
  category: string
  size: string
  color: string
  variant: string
  stockTarget: number | null
}

// ---------------------------------------------------------------------------
// Inventory API
// ---------------------------------------------------------------------------

export async function getInventoryManufacturers(toolId: string): Promise<string[]> {
  const res = await apiFetch(`/tools/${toolId}/inventory/manufacturers`)
  return res.json()
}

export async function getInventoryProducts(
  toolId: string,
  manufacturers: string[],
): Promise<{ products: InventoryProduct[]; errors: { manufacturer: string; error: string }[] }> {
  const params = new URLSearchParams({ manufacturers: manufacturers.join(',') })
  const res = await apiFetch(`/tools/${toolId}/inventory/products?${params}`)
  return res.json()
}

export async function getInventoryProductsFromBillbee(
  toolId: string,
  manufacturers: string[],
): Promise<{ products: InventoryProduct[]; errors: { manufacturer: string; error: string }[] }> {
  const params = new URLSearchParams({ manufacturers: manufacturers.join(',') })
  const res = await apiFetch(`/tools/${toolId}/inventory/products/billbee?${params}`)
  return res.json()
}

export async function queryInventoryStock(
  toolId: string,
  products: { sku: string; billbeeId: number }[],
): Promise<{ stocks: Record<string, { stock: number; stockId: number }>; errors: string[] }> {
  const res = await apiFetch(`/tools/${toolId}/inventory/stock/query`, {
    method: 'POST',
    body: JSON.stringify({ products }),
  })
  return res.json()
}

export interface SheetImportItem {
  sku: string
  billbeeId: number
  billbeeStock: number | null
  qty: number
}

export async function getSheetTabs(toolId: string, sheet: string): Promise<{ tabs: string[]; error: string | null }> {
  const params = new URLSearchParams({ sheet })
  const res = await apiFetch(`/tools/${toolId}/inventory/sheet-tabs?${params}`)
  return res.json()
}

export async function getSheetImport(
  toolId: string,
  manufacturer: string,
  sheet: string,
  tab: string,
  skuCol: string,
  qtyCol: string,
): Promise<{ items: SheetImportItem[]; errors: { sku?: string; error: string }[] }> {
  const params = new URLSearchParams({ sheet, tab, sku_col: skuCol, qty_col: qtyCol, manufacturer })
  const res = await apiFetch(`/tools/${toolId}/inventory/sheet-import?${params}`)
  return res.json()
}

export async function updateInventoryStock(
  toolId: string,
  sku: string,
  billbeeId: number,
  delta?: number,
  newQuantity?: number,
  reason?: string,
): Promise<{ ok: boolean; sku: string; previousStock: number; newStock: number }> {
  const res = await apiFetch(`/tools/${toolId}/inventory/stock/update`, {
    method: 'POST',
    body: JSON.stringify({ sku, billbeeId, delta, newQuantity, reason }),
  })
  return res.json()
}
