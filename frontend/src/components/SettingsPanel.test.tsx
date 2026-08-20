import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { request } from '../api'
import SettingsPanel, { normalizeSystemConfig } from './SettingsPanel'

vi.mock('../api', () => ({
  request: vi.fn(async (path: string, _options?: RequestInit) => {
    if (path.includes('/validate')) return { ready: true, dimensions: 1024 }
    if (path === '/profile') return { username: 'admin', groups: [], roles: [], capabilities: [], preferences: {} }
    if (path === '/system/config') return {
      routes: {},
      entity_schema: 'DocSeekEntity',
      entity_prompt: 'Extract',
      neo4j: { property_database: 'property_graph', entity_database: 'entity_graph', use_neo4j: false },
      mcp: { enabled: true },
      retrieval: {
        ai_query: { property_limit: 15, entity_limit: 15, total_node_limit: 30 },
        search: { property_limit: 30, entity_limit: 30 },
      },
    }
    if (path === '/system/providers') return [
      { id: 'provider-1', name: 'Embedding', provider_type: 'embedding', model: 'bge-m3', base_url: 'https://provider.test/v1', secret_configured: true },
      { id: 'provider-2', name: 'LLM', provider_type: 'llm', model: 'chat-model', base_url: 'https://provider.test/v1', secret_configured: true },
    ]
    if (path === '/system/llm-invocations?limit=50') return [
      {
        id: 'log-1',
        request_time: '2026-08-18T01:00:00+00:00',
        response_time: '2026-08-18T01:00:01+00:00',
        duration_ms: 1000,
        model: 'chat-model',
        route_key: 'dg_agent_route',
        profile_id: 'provider-2',
        status: 'success',
        request_prompt: '[{"role":"user","content":"Define this"}]',
        response_output: 'A concise definition.',
      },
    ]
    if (path === '/system/neo4j/check') return { ready: false, fallback: true, message: 'local fallback' }
    if (path === '/system/storage/check') return { writable: true, data_dir: './data', projects_dir: './data/projects' }
    if (path === '/admin/users') return [{ id: 'user-1', username: 'analyst', disabled: false }]
    if (path === '/admin/groups') return [{ id: 'group-1', name: 'Readers' }]
    if (path === '/admin/roles') return [{ id: 'role-1', name: 'Reader', immutable: true, capabilities: ['project.view'] }]
    return {}
  }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('SettingsPanel', () => {
  it('uses retrieval defaults for a response from an older backend process', () => {
    const normalized = normalizeSystemConfig({
      routes: {},
      entity_schema: 'DocSeekEntity',
      entity_prompt: 'Extract',
      neo4j: {
        property_database: 'property_graph',
        entity_database: 'entity_graph',
        use_neo4j: false,
      },
      mcp: { enabled: true },
    })

    expect(normalized.retrieval).toEqual({
      ai_query: {
        property_limit: 15,
        entity_limit: 15,
        total_node_limit: 30,
      },
      search: {
        property_limit: 30,
        entity_limit: 30,
      },
    })
    expect(normalized.batch_llm_concurrency).toBe(50)
    expect(normalized.ai_query_history_compaction_token_threshold).toBe(150_000)
  })

  it('shows system, access, and profile management sections', async () => {
    render(<SettingsPanel onClose={() => undefined} />)
    expect(await screen.findByRole('dialog', { name: 'Settings' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'System' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Logs' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Access' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Profile' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Validate provider Embedding' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Edit provider Embedding' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove provider Embedding' })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: 'Definition Generation Agent' })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: 'Group Arrangement Agent' })).toBeTruthy()
    expect(screen.queryByRole('combobox', { name: 'Property Graph Building Agent' })).toBeNull()
    expect(screen.getByRole('combobox', { name: 'Entity Extraction Agent' })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: 'Shared Embedding Model' })).toBeTruthy()
    expect(screen.queryByRole('combobox', { name: 'DG-Agent LLM' })).toBeNull()
    expect(screen.queryByRole('combobox', { name: 'GA-Agent LLM' })).toBeNull()
    expect(screen.queryByRole('combobox', { name: 'PGB-Agent LLM' })).toBeNull()
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
    expect(screen.queryByRole('checkbox', { name: 'Compact density' })).toBeNull()
  })

  it('loads LLM invocation details only when the logs tab is opened', async () => {
    const user = userEvent.setup()
    render(<SettingsPanel onClose={() => undefined} />)

    await screen.findByRole('dialog', { name: 'Settings' })
    expect(request).not.toHaveBeenCalledWith('/system/llm-invocations?limit=50')

    await user.click(screen.getByRole('button', { name: 'Logs' }))

    expect(await screen.findByText('Definition Generation Agent')).toBeTruthy()
    expect(screen.getByText('Completed · 1000 ms')).toBeTruthy()
    await user.click(screen.getByText('Definition Generation Agent'))
    expect(screen.getByText('A concise definition.')).toBeTruthy()
  })

  it('focuses a requested model route when opened from the import alert', async () => {
    render(
      <SettingsPanel
        onClose={() => undefined}
        focusRouteKey="entity_agent_route"
      />,
    )

    const route = await screen.findByRole('combobox', {
      name: 'Entity Extraction Agent',
    })
    await waitFor(() => expect(document.activeElement).toBe(route))
  })

  it('does not expose retrieval limits in Settings', async () => {
    render(<SettingsPanel onClose={() => undefined} />)

    await screen.findByRole('dialog', { name: 'Settings' })
    expect(screen.queryByRole('spinbutton', { name: 'AI Query property limit' })).toBeNull()
    expect(screen.queryByRole('spinbutton', { name: 'Search property limit' })).toBeNull()
  })
})
