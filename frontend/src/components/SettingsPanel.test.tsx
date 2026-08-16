import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import SettingsPanel from './SettingsPanel'

vi.mock('../api', () => ({
  request: vi.fn(async (path: string) => {
    if (path.includes('/validate')) return { ready: true, dimensions: 1024 }
    if (path === '/profile') return { username: 'admin', groups: [], roles: [], capabilities: [], preferences: {} }
    if (path === '/system/config') return { routes: {}, entity_schema: 'DocSeekEntity', entity_prompt: 'Extract', neo4j: { property_database: 'property_graph', entity_database: 'entity_graph', use_neo4j: false }, mcp: { enabled: true } }
    if (path === '/system/providers') return [
      { id: 'provider-1', name: 'Embedding', provider_type: 'embedding', model: 'bge-m3', base_url: 'https://provider.test/v1', secret_configured: true },
      { id: 'provider-2', name: 'LLM', provider_type: 'llm', model: 'chat-model', base_url: 'https://provider.test/v1', secret_configured: true },
    ]
    if (path === '/system/neo4j/check') return { ready: false, fallback: true, message: 'local fallback' }
    if (path === '/system/storage/check') return { writable: true, data_dir: './data', projects_dir: './data/projects' }
    if (path === '/admin/users') return [{ id: 'user-1', username: 'analyst', disabled: false }]
    if (path === '/admin/groups') return [{ id: 'group-1', name: 'Readers' }]
    if (path === '/admin/roles') return [{ id: 'role-1', name: 'Reader', immutable: true, capabilities: ['project.view'] }]
    return {}
  }),
}))

describe('SettingsPanel', () => {
  it('shows system, access, and profile management sections', async () => {
    render(<SettingsPanel onClose={() => undefined} />)
    expect(await screen.findByRole('dialog', { name: 'Settings' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'System' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Access' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Profile' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Validate provider Embedding' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Edit provider Embedding' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove provider Embedding' })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: 'Entity extraction LLM' })).toBeTruthy()
    await screen.getByRole('button', { name: 'Validate provider Embedding' }).click()
    expect(await screen.findByText(/Ready · 1024d/)).toBeTruthy()
    await screen.getByRole('button', { name: 'Access' }).click()
    expect(screen.getByRole('combobox', { name: 'Member user' })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: 'Member group' })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: 'Role group' })).toBeTruthy()
    const viewProjects = screen.getByRole('checkbox', { name: 'View projects' }) as HTMLInputElement
    expect(viewProjects).toBeTruthy()
    await viewProjects.click()
    expect(viewProjects.checked).toBe(true)
    expect(screen.getByRole('checkbox', { name: 'Run AI Query' })).toBeTruthy()
    expect(screen.queryByPlaceholderText('project.view, property.view')).toBeNull()
    await screen.getByRole('button', { name: 'Profile' }).click()
    expect(screen.getByRole('checkbox', { name: 'Compact density' })).toBeTruthy()
  })
})
