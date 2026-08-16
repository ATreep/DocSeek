import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ProjectShell from './ProjectShell'

const apiMocks = vi.hoisted(() => ({
  request: vi.fn(),
  streamRequest: vi.fn(),
  logout: vi.fn(),
}))

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  ...apiMocks,
}))

vi.mock('./GraphCanvas', () => ({
  default: () => <div aria-label="Mock graph canvas" />,
}))

beforeEach(() => {
  apiMocks.request.mockImplementation(async (path: string) => {
    if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
    if (path.endsWith('/ai-query/history')) return { messages: [] }
    if (path.endsWith('/properties')) return []
    if (path.includes('/graphs/')) return { nodes: [], edges: [], snapshot_id: 'active' }
    if (path.endsWith('/processing')) return { locked: false, status: 'idle' }
    throw new Error(`Unexpected request: ${path}`)
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ProjectShell empty project catalog', () => {
  it('shows the project creation view after loading an empty catalog', async () => {
    apiMocks.request.mockImplementation(async (path: string) => {
      if (path === '/projects') return []
      throw new Error(`Unexpected request: ${path}`)
    })

    render(<ProjectShell onLogout={() => undefined} />)

    expect(
      await screen.findByRole('heading', { name: 'Create your first project' }),
    ).toBeTruthy()
    expect(screen.getByLabelText('Project name')).toBeTruthy()
    expect(screen.queryByText('Invalid or expired session')).toBeNull()
  })
})

describe('ProjectShell AI Query history', () => {
  it('restores the active users project history with citations', async () => {
    apiMocks.request.mockImplementation(async (path: string) => {
      if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
      if (path.endsWith('/ai-query/history')) return {
        messages: [
          { role: 'user', content: 'What is Atlas?' },
          {
            role: 'assistant',
            content: '**Atlas** is a product.',
            citations: [{ kind: 'entity', id: 'atlas', label: 'Atlas' }],
          },
        ],
      }
      if (path.endsWith('/properties')) return []
      if (path.includes('/graphs/')) return { nodes: [], edges: [], snapshot_id: 'active' }
      if (path.endsWith('/processing')) return { locked: false, status: 'idle' }
      throw new Error(`Unexpected request: ${path}`)
    })
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    await user.click(await screen.findByRole('button', { name: 'AI Query' }))

    expect(await screen.findByText('What is Atlas?')).toBeTruthy()
    expect(screen.getByText('Atlas', { selector: 'strong' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Open entity Atlas' })).toBeTruthy()
  })

  it('clears the active users persisted project history', async () => {
    apiMocks.request.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
      if (path.endsWith('/ai-query/history') && options?.method === 'DELETE') return undefined
      if (path.endsWith('/ai-query/history')) return {
        messages: [
          { role: 'user', content: 'Saved question' },
          { role: 'assistant', content: 'Saved answer' },
        ],
      }
      if (path.endsWith('/properties')) return []
      if (path.includes('/graphs/')) return { nodes: [], edges: [], snapshot_id: 'active' }
      if (path.endsWith('/processing')) return { locked: false, status: 'idle' }
      throw new Error(`Unexpected request: ${path}`)
    })
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)
    await user.click(await screen.findByRole('button', { name: 'AI Query' }))
    await screen.findByText('Saved answer')

    await user.click(screen.getByRole('button', { name: 'Clear chat history' }))

    await waitFor(() => expect(apiMocks.request).toHaveBeenCalledWith(
      '/projects/project-1/ai-query/history',
      { method: 'DELETE' },
    ))
    await screen.findByText('Start a grounded conversation')
  })

  it('sends every completed chat turn with a follow-up query', async () => {
    apiMocks.streamRequest.mockImplementation(async (_path, _options, onEvent) => {
      const answer = apiMocks.streamRequest.mock.calls.length === 1
        ? 'Hatsume uses multiple agents.'
        : 'Your previous question was about multi-agent projects.'
      onEvent({ type: 'delta', content: answer })
      onEvent({ type: 'done' })
    })
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    await user.click(await screen.findByRole('button', { name: 'AI Query' }))
    const textbox = await screen.findByRole('textbox', { name: 'Ask AI Query' })
    await user.type(textbox, 'Which projects use multiple agents?')
    await user.click(screen.getByRole('button', { name: 'Send query' }))
    await screen.findByText('Hatsume uses multiple agents.')

    await user.type(textbox, 'What did I just ask?')
    await user.click(screen.getByRole('button', { name: 'Send query' }))
    await waitFor(() => expect(apiMocks.streamRequest).toHaveBeenCalledTimes(2))

    const requestOptions = apiMocks.streamRequest.mock.calls[1][1] as RequestInit
    expect(JSON.parse(requestOptions.body as string)).toEqual({
      query: 'What did I just ask?',
      history: [
        { role: 'user', content: 'Which projects use multiple agents?' },
        { role: 'assistant', content: 'Hatsume uses multiple agents.' },
      ],
    })
  })
})

describe('ProjectShell processing failure details', () => {
  it('opens and copies the recorded error stack from a failed banner', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    apiMocks.request.mockImplementation(async (path: string) => {
      if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
      if (path.endsWith('/properties')) return []
      if (path.includes('/graphs/')) return { nodes: [], edges: [], snapshot_id: 'active' }
      if (path.endsWith('/processing')) return {
        locked: false,
        status: 'failed',
        stage: 'failed',
        error: 'Provider request failed',
        error_detail: 'Traceback (most recent call last):\n  File "pipeline.py", line 1\nProviderError: request failed',
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    const user = userEvent.setup()
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    render(<ProjectShell onLogout={() => undefined} />)

    await user.click(await screen.findByRole('button', { name: 'Show error detail' }))
    const dialog = screen.getByRole('dialog', { name: 'Processing error detail' })
    expect(dialog.textContent).toContain('Traceback (most recent call last)')

    await user.click(screen.getByRole('button', { name: 'Copy error detail' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining('ProviderError: request failed')))
    await screen.findByText('Copied to clipboard')
  })
})

describe('ProjectShell graph relation previews', () => {
  it('opens a property relation and switches cleanly to another property preview', async () => {
    const properties = [
      { id: 'plan', project_id: 'project-1', filename: 'plan.md', property_type: 'markdown', relative_path: 'properties/plan.md', status: 'active' },
      { id: 'architecture', project_id: 'project-1', filename: 'architecture.md', property_type: 'markdown', relative_path: 'properties/architecture.md', status: 'active' },
    ]
    apiMocks.request.mockImplementation(async (path: string) => {
      if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
      if (path.endsWith('/properties')) return properties
      if (path.includes('/graphs/property')) return {
        nodes: properties,
        edges: [{ source: 'plan', target: 'architecture', type: 'REFERENCES' }],
        snapshot_id: 'active',
      }
      if (path.includes('/graphs/entity')) return { nodes: [], edges: [], snapshot_id: 'active' }
      if (path.endsWith('/processing')) return { locked: false, status: 'idle' }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ text: async () => 'Preview content' }))
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    const planRow = (await screen.findByText('plan.md')).closest('button')
    if (!planRow) throw new Error('Plan property row was not rendered as a button')
    await user.click(planRow)
    await user.click(screen.getByRole('button', { name: 'REFERENCES outgoing architecture.md' }))
    expect(screen.getByRole('dialog', { name: 'Relation detail' })).toBeTruthy()

    const architectureRow = screen.getAllByText('architecture.md')[0].closest('button')
    if (!architectureRow) throw new Error('Architecture property row was not rendered as a button')
    await user.click(architectureRow)

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Relation detail' })).toBeNull())
    expect(screen.getByRole('heading', { name: 'architecture.md' })).toBeTruthy()
  })
})

describe('ProjectShell property grouping', () => {
  const properties = [
    {
      id: 'atlas',
      project_id: 'project-1',
      filename: 'atlas-manual.md',
      property_type: 'markdown',
      definition: 'A user manual for Atlas.',
      relative_path: 'properties/Atlas/atlas-manual.md',
      status: 'active',
    },
  ]

  function mockProjectWithProperties() {
    apiMocks.request.mockImplementation(async (path: string) => {
      if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
      if (path === '/projects/project-1/properties/regroup') return { properties }
      if (path.endsWith('/properties')) return properties
      if (path.includes('/graphs/')) return { nodes: [], edges: [], snapshot_id: 'active' }
      if (path.endsWith('/processing')) return { locked: false, status: 'idle' }
      throw new Error(`Unexpected request: ${path}`)
    })
  }

  it('does not expose a single-property Move action in the preview', async () => {
    mockProjectWithProperties()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ text: async () => 'Atlas manual' }))
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    const propertyRow = (await screen.findByText('atlas-manual.md')).closest('button')
    if (!propertyRow) throw new Error('Property row was not rendered as a button')
    await user.click(propertyRow)

    expect(screen.queryByRole('button', { name: 'Move property' })).toBeNull()
  })

  it('sends a tree rearrangement revision from the left property tree', async () => {
    mockProjectWithProperties()
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    await user.click(await screen.findByRole('button', { name: 'Re-group properties' }))
    const revision = screen.getByRole('textbox', { name: 'Arrangement revision' })
    await user.type(revision, 'Group Atlas documents by purpose.')
    await user.click(screen.getByRole('button', { name: 'Apply re-grouping' }))

    await waitFor(() => expect(apiMocks.request).toHaveBeenCalledWith(
      '/projects/project-1/properties/regroup',
      {
        method: 'POST',
        body: JSON.stringify({ revision_prompt: 'Group Atlas documents by purpose.' }),
      },
    ))
  })
})
