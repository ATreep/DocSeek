export type Project = { id: string; name: string; processing?: boolean }
export type Property = { id: string; project_id?: string; filename: string; property_type: string; definition?: string; status: string; relative_path: string }
export type PropertyUploadResult = { import_id: string; status: 'awaiting_confirmation'; property_type: string; original_filename: string; suggested_filename: string; definition: string }
export type PropertyImportConfirmResult = { property_id: string; job_id: string; status: string; property_type: string }
export type PropertyImportBatchItem = { import_id: string; property_type: string; original_filename: string; suggested_filename: string; definition: string; word_count: number; character_count: number; oversized: boolean; reasons: Array<'word_count' | 'character_count'> }
export type PropertyImportBatchResult = { batch_id: string; status: 'awaiting_confirmation'; items: PropertyImportBatchItem[] }
export type PropertyImportBatchStreamEvent =
  | { type: 'batch_started'; batch_id: string; total: number }
  | { type: 'file_started'; batch_id: string; index: number; total: number; filename: string }
  | { type: 'keepalive'; batch_id: string; index: number; total: number; filename: string }
  | { type: 'file_analyzed'; batch_id: string; index: number; total: number; filename: string; item: Omit<PropertyImportBatchItem, 'suggested_filename'> }
  | { type: 'filename_generation_started'; batch_id: string; total: number }
  | { type: 'filename_generation_keepalive'; batch_id: string; total: number }
  | { type: 'file_completed'; batch_id: string; index: number; total: number; filename: string; item: PropertyImportBatchItem }
  | ({ type: 'batch_completed'; total: number } & PropertyImportBatchResult)
  | { type: 'error'; batch_id: string; message: string }
export type ConfirmedPropertyImport = { import_id: string; filename: string }
export type PropertyImportBatchConfirmResult = { job_id: string; status: string; properties: Array<{ property_id: string; job_id: string; status: string; property_type: string; filename: string }> }
export type RegroupProposalChange = { property_id: string; current_directory: string; proposed_directory: string; current_filename: string; proposed_filename: string; definition: string; changed: boolean }
export type RegroupProposal = { catalog_signature: string; changes: RegroupProposalChange[]; job_id?: string }
export type RegroupConfirmationItem = { property_id: string; directory: string; filename: string }

const API = '/api'

function authenticatedOptions(options: RequestInit): RequestInit {
  const token = localStorage.getItem('docseek-token')
  return {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      'X-DocSeek-Language': displayLanguageName(),
      ...(options.headers || {}),
    },
  }
}

async function responseError(response: Response): Promise<Error> {
  const detail = await response.json().catch(() => ({ detail: response.statusText }))
  return new Error(detail.detail || response.statusText)
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, authenticatedOptions(options))
  if (!response.ok) {
    throw await responseError(response)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export async function streamRequest<T>(
  path: string,
  options: RequestInit,
  onEvent: (event: T) => void,
): Promise<void> {
  const response = await fetch(`${API}${path}`, authenticatedOptions(options))
  if (!response.ok) throw await responseError(response)
  if (!response.body) throw new Error('Streaming response has no body')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line) as T)
    }
    if (done) break
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as T)
}

export async function login(username: string, password: string) {
  const result = await request<{ token: string; user: { username: string } }>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })
  localStorage.setItem('docseek-token', result.token)
  return result
}

export async function logout() {
  try { await request('/auth/logout', { method: 'POST' }) } catch { /* An expired session is already logged out server-side. */ } finally { localStorage.removeItem('docseek-token') }
}
import { displayLanguageName } from './i18n'
