import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ProjectShell from './ProjectShell'

const apiMocks = vi.hoisted(() => ({
  request: vi.fn(),
  streamRequest: vi.fn(),
  logout: vi.fn(),
}))

const componentMocks = vi.hoisted(() => ({
  graphCanvas: vi.fn(),
}))

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  ...apiMocks,
}))

vi.mock('./GraphCanvas', () => ({
  default: (props: Record<string, unknown>) => {
    componentMocks.graphCanvas(props)
    return <div aria-label="Mock graph canvas" />
  },
}))

beforeEach(() => {
  apiMocks.request.mockImplementation(async (path: string) => {
    if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
    if (path === '/system/import-provider-readiness') return { ready: true, missing_routes: [], can_configure: true }
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

describe('ProjectShell import provider readiness', () => {
  it('blocks file selection and opens provider settings when an import route is missing', async () => {
    apiMocks.request.mockImplementation(async (path: string) => {
      if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
      if (path === '/system/import-provider-readiness') {
        return {
          ready: false,
          missing_routes: [
            { key: 'dg_agent_route', label: 'DG-Agent LLM', provider_type: 'llm' },
            { key: 'entity_agent_route', label: 'Entity Extraction Agent', provider_type: 'llm' },
            { key: 'shared_embedding_route', label: 'Shared Embedding Model', provider_type: 'embedding' },
          ],
          can_configure: true,
        }
      }
      if (path.endsWith('/properties')) return []
      if (path.includes('/graphs/')) return { nodes: [], edges: [], snapshot_id: 'active' }
      if (path.endsWith('/processing')) return { locked: false, status: 'idle' }
      throw new Error(`Unexpected request: ${path}`)
    })
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    await user.click(await screen.findByRole('button', { name: 'Add property' }))

    expect(await screen.findByRole('dialog', { name: 'Model providers required' })).toBeTruthy()
    expect(screen.getByText('Definition Generation Agent')).toBeTruthy()
    expect(screen.queryByText('DG-Agent LLM')).toBeNull()
    expect(screen.getByText('Entity Extraction Agent')).toBeTruthy()
    expect(screen.getByText('Shared Embedding Model')).toBeTruthy()
    expect(screen.queryByLabelText('Property files')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Configure providers' }))
    expect(await screen.findByRole('dialog', { name: 'Settings' })).toBeTruthy()
  })

  it('opens file selection immediately when every import route is ready', async () => {
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    await user.click(await screen.findByRole('button', { name: 'Add property' }))

    expect(await screen.findByLabelText('Property files')).toBeTruthy()
    expect(screen.queryByRole('dialog', { name: 'Model providers required' })).toBeNull()
  })
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

describe('ProjectShell project controls layout', () => {
  it('places project creation inside the project selector and lets search occupy the middle', async () => {
    const view = render(<ProjectShell onLogout={() => undefined} />)

    await screen.findByTitle('Select project')
    const projectSelector = view.container.querySelector('.project-select') as HTMLElement
    const search = view.container.querySelector('.global-search') as HTMLElement
    const topActions = view.container.querySelector('.top-actions') as HTMLElement

    expect(within(projectSelector).getByRole('button', { name: 'Create project' })).toBeTruthy()
    expect(view.container.querySelector('.top-create-button')).toBeNull()
    expect(projectSelector.nextElementSibling).toBe(search)
    expect(search.nextElementSibling).toBe(topActions)
  })

  it('places project actions beside the project name and exposes a labeled tree revision button', async () => {
    const view = render(<ProjectShell onLogout={() => undefined} />)

    await screen.findByRole('heading', { name: 'Test project' })
    const titleRow = view.container.querySelector('.project-title-row') as HTMLElement
    const titleActions = view.container.querySelector('.project-title-actions') as HTMLElement
    const treeActions = view.container.querySelector('.tree-actions') as HTMLElement

    expect(within(titleRow).getByRole('heading', { name: 'Test project' })).toBeTruthy()
    expect(within(titleActions).getByRole('button', { name: 'Rename project' })).toBeTruthy()
    expect(within(titleActions).getByRole('button', { name: 'Delete project' })).toBeTruthy()
    expect(within(treeActions).getByRole('button', { name: 'Revise Project Tree' })).toBeTruthy()
  })
})

describe('ProjectShell graph-aware search', () => {
  it('does not render a keyboard shortcut badge in the search bar', async () => {
    apiMocks.request.mockImplementation(async (path: string) => {
      if (path === '/projects') return []
      throw new Error(`Unexpected request: ${path}`)
    })

    render(<ProjectShell onLogout={() => undefined} />)

    await screen.findByPlaceholderText('Search properties and entities')
    expect(screen.queryByText('⌘ K')).toBeNull()
  })

  it('labels direct and relation-expanded results', async () => {
    apiMocks.request.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
      if (path.endsWith('/ai-query/history')) return { messages: [] }
      if (path.endsWith('/properties')) return []
      if (path.includes('/graphs/')) return { nodes: [], edges: [], snapshot_id: 'active' }
      if (path.endsWith('/processing')) return { locked: false, status: 'idle' }
      if (path.endsWith('/search')) {
        expect(options?.body).toBe(JSON.stringify({ query: 'Atlas' }))
        return {
          properties: [{ id: 'manual', filename: 'manual.md', retrieval_reason: 'Direct match', retrieval_path: ['manual.md'] }],
          entities: [{ id: 'neo4j', name: 'Neo4j', retrieval_reason: 'Related through USES', retrieval_path: ['Atlas', 'USES', 'Neo4j'] }],
        }
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    const search = await screen.findByPlaceholderText('Search properties and entities')
    await user.type(search, 'Atlas')
    await user.click(screen.getByRole('button', { name: 'Search project' }))

    expect(await screen.findByText('Direct match')).toBeTruthy()
    expect(screen.getByText('Related through USES')).toBeTruthy()
  })
})

describe('ProjectShell graph display filters', () => {
  it('retains independent selections for the property and entity graph tabs', async () => {
    const properties = [
      { id: 'one', filename: 'one.md', relative_path: 'properties/Group 1/one.md', property_type: 'document', status: 'active' },
      { id: 'two', filename: 'two.md', relative_path: 'properties/Group 2/two.md', property_type: 'document', status: 'active' },
    ]
    apiMocks.request.mockImplementation(async (path: string) => {
      if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
      if (path.endsWith('/ai-query/history')) return { messages: [] }
      if (path.endsWith('/properties')) return properties
      if (path.includes('/graphs/property')) return { nodes: properties, edges: [], snapshot_id: 'active' }
      if (path.includes('/graphs/entity')) return {
        nodes: [{ id: 'entity-one', name: 'Entity One', source_property_ids: ['one'] }],
        edges: [],
        snapshot_id: 'active',
      }
      if (path.endsWith('/processing')) return { locked: false, status: 'idle' }
      throw new Error(`Unexpected request: ${path}`)
    })
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    await waitFor(() => {
      const propertyCall = [...componentMocks.graphCanvas.mock.calls]
        .reverse()
        .find(call => call[0].kind === 'property')?.[0]
      expect(propertyCall?.properties).toEqual(properties)
    })
    const propertyProps = [...componentMocks.graphCanvas.mock.calls]
      .reverse()
      .find(call => call[0].kind === 'property')?.[0]
    act(() => propertyProps.onDisplaySelectionChange({ groupPaths: ['Group 1'], propertyIds: [] }))

    await user.click(screen.getByRole('button', { name: 'Entity Graph' }))
    const entityProps = [...componentMocks.graphCanvas.mock.calls]
      .reverse()
      .find(call => call[0].kind === 'entity')?.[0]
    expect(entityProps.properties).toEqual(properties)
    expect(entityProps.displaySelection).toEqual({ groupPaths: [], propertyIds: [] })
    act(() => entityProps.onDisplaySelectionChange({ groupPaths: [], propertyIds: ['two'] }))

    await user.click(screen.getByRole('button', { name: 'Property Graph' }))
    const restoredPropertyProps = [...componentMocks.graphCanvas.mock.calls]
      .reverse()
      .find(call => call[0].kind === 'property')?.[0]
    expect(restoredPropertyProps.displaySelection).toEqual({
      groupPaths: ['Group 1'],
      propertyIds: [],
    })
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
        llm_response: '{"entities":[{"id":"错误 identifier","name":"北京大学"}]}',
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
    expect(dialog.textContent).toContain('Original LLM response')
    expect(dialog.textContent).toContain('错误 identifier')

    await user.click(screen.getByRole('button', { name: 'Copy error detail' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining('ProviderError: request failed')))
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('错误 identifier'))
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
      if (path === '/projects/project-1/properties/regroup') return {
        catalog_signature: 'a'.repeat(64),
        changes: [{
          property_id: 'atlas',
          current_directory: 'Atlas',
          proposed_directory: 'Product/Atlas/Guides',
          current_filename: 'atlas-manual.md',
          proposed_filename: 'atlas-guide.md',
          definition: 'A user manual for Atlas.',
          changed: true,
        }],
      }
      if (path === '/projects/project-1/properties/regroup/confirm') return { properties }
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

    await user.click(await screen.findByRole('button', { name: 'Revise Project Tree' }))
    const revision = screen.getByRole('textbox', { name: 'Arrangement revision' })
    await user.type(revision, 'Group Atlas documents by purpose.')
    await user.click(screen.getByRole('button', { name: 'Review re-grouping' }))

    await waitFor(() => expect(apiMocks.request).toHaveBeenCalledWith(
      '/projects/project-1/properties/regroup',
      {
        method: 'POST',
        body: JSON.stringify({ revision_prompt: 'Group Atlas documents by purpose.' }),
      },
    ))
    const filename = await screen.findByLabelText('Proposed filename for atlas-manual.md')
    await user.clear(filename)
    await user.type(filename, 'atlas-user-guide.md')
    await user.click(screen.getByRole('button', { name: 'Apply confirmed changes' }))
    await waitFor(() => expect(apiMocks.request).toHaveBeenCalledWith(
      '/projects/project-1/properties/regroup/confirm',
      {
        method: 'POST',
        body: JSON.stringify({
          catalog_signature: 'a'.repeat(64),
          items: [{
            property_id: 'atlas',
            directory: 'Product/Atlas/Guides',
            filename: 'atlas-user-guide.md',
          }],
        }),
      },
    ))
  })
})

describe('ProjectShell multiple property import', () => {
  it('selects files by clicking or dropping on the upload surface and centers the count', async () => {
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    await user.click(await screen.findByRole('button', { name: 'Add property' }))
    const dropzone = await screen.findByRole('button', { name: 'Choose property files' })
    const input = screen.getByLabelText('Property files') as HTMLInputElement
    expect(input.classList.contains('visually-hidden')).toBe(true)

    await user.upload(input, [
      new File(['Atlas'], 'atlas.md', { type: 'text/markdown' }),
      new File(['Nova'], 'nova.md', { type: 'text/markdown' }),
    ])
    expect(screen.getByText('2 files selected').classList.contains('dropzone-count')).toBe(true)
    const selectedFiles = screen.getByRole('list', { name: 'Selected files' })
    expect(within(selectedFiles).getByText('atlas.md')).toBeTruthy()
    expect(within(selectedFiles).getByText('nova.md')).toBeTruthy()

    fireEvent.drop(dropzone, {
      dataTransfer: {
        files: [new File(['Orion'], 'orion.md', { type: 'text/markdown' })],
      },
    })
    expect(screen.getByText('1 file selected')).toBeTruthy()
    expect(within(selectedFiles).getByText('orion.md')).toBeTruthy()
    expect(within(selectedFiles).queryByText('atlas.md')).toBeNull()
  })

  it('ignores folders dropped into the ingest property window', async () => {
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    await user.click(await screen.findByRole('button', { name: 'Add property' }))
    const dropzone = await screen.findByRole('button', { name: 'Choose property files' })
    const folder = new File([], 'product-docs')
    const document = new File(['Atlas'], 'atlas.md', { type: 'text/markdown' })

    fireEvent.drop(dropzone, {
      dataTransfer: {
        files: [folder, document],
        items: [
          {
            kind: 'file',
            getAsFile: () => folder,
            webkitGetAsEntry: () => ({ isDirectory: true }),
          },
          {
            kind: 'file',
            getAsFile: () => document,
            webkitGetAsEntry: () => ({ isDirectory: false }),
          },
        ],
      },
    })

    expect(screen.getByText('1 file selected')).toBeTruthy()
    const selectedFiles = screen.getByRole('list', { name: 'Selected files' })
    expect(within(selectedFiles).getByText('atlas.md')).toBeTruthy()
    expect(within(selectedFiles).queryByText('product-docs')).toBeNull()
  })

  it('removes one selected file and excludes it from import preparation', async () => {
    apiMocks.streamRequest.mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    await user.click(await screen.findByRole('button', { name: 'Add property' }))
    const input = screen.getByLabelText('Property files') as HTMLInputElement
    await user.upload(input, [
      new File(['Atlas'], 'atlas.md', { type: 'text/markdown' }),
      new File(['Nova'], 'nova.md', { type: 'text/markdown' }),
      new File(['Orion'], 'orion.md', { type: 'text/markdown' }),
    ])

    await user.click(screen.getByRole('button', { name: 'Remove nova.md' }))

    const selectedFiles = screen.getByRole('list', { name: 'Selected files' })
    expect(screen.getByText('2 files selected')).toBeTruthy()
    expect(within(selectedFiles).queryByText('nova.md')).toBeNull()
    expect(within(selectedFiles).getByText('atlas.md')).toBeTruthy()
    expect(within(selectedFiles).getByText('orion.md')).toBeTruthy()

    const uploadForm = input.closest('form')
    if (!uploadForm) throw new Error('Upload form was not rendered')
    fireEvent.submit(uploadForm)
    await waitFor(() => expect(apiMocks.streamRequest).toHaveBeenCalledTimes(1))

    const stagedForm = apiMocks.streamRequest.mock.calls[0][1].body as FormData
    expect(
      stagedForm.getAll('files').map((file) => (file as File).name),
    ).toEqual(['atlas.md', 'orion.md'])
  })

  it('shows Definition Generation Agent progress for each file before review', async () => {
    const stagedItems = [
      {
        import_id: 'import-atlas',
        original_filename: 'atlas.md',
        suggested_filename: 'atlas-guide.md',
        definition: 'An introduction to Atlas.',
        property_type: 'markdown',
      },
      {
        import_id: 'import-nova',
        original_filename: 'nova.md',
        suggested_filename: 'nova-guide.md',
        definition: 'An introduction to Nova.',
        property_type: 'markdown',
      },
    ]
    let continueStream: (() => void) | undefined
    const streamGate = new Promise<void>((resolve) => {
      continueStream = resolve
    })
    apiMocks.request.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/projects') return [{ id: 'project-1', name: 'Test project' }]
      if (path === '/system/import-provider-readiness') return { ready: true, missing_routes: [], can_configure: true }
      if (path === '/projects/project-1/property-import-batches' && options?.method === 'POST') {
        return new Promise(() => undefined)
      }
      if (path === '/projects/project-1/property-import-batches/batch-1/confirm') {
        return { job_id: 'job-1', status: 'queued', properties: [] }
      }
      if (path.endsWith('/properties')) return []
      if (path.includes('/graphs/')) return { nodes: [], edges: [], snapshot_id: 'active' }
      if (path.endsWith('/processing')) return { locked: false, status: 'idle' }
      throw new Error(`Unexpected request: ${path}`)
    })
    apiMocks.streamRequest.mockImplementation(async (
      path: string,
      options: RequestInit,
      onEvent: (event: Record<string, unknown>) => void,
    ) => {
      expect(path).toBe('/projects/project-1/property-import-batches/stream')
      expect(options.method).toBe('POST')
      onEvent({ type: 'batch_started', batch_id: 'batch-1', total: 2 })
      onEvent({ type: 'file_started', batch_id: 'batch-1', index: 1, total: 2, filename: 'atlas.md' })
      await streamGate
      onEvent({ type: 'file_completed', batch_id: 'batch-1', index: 1, total: 2, filename: 'atlas.md', item: stagedItems[0] })
      onEvent({ type: 'file_started', batch_id: 'batch-1', index: 2, total: 2, filename: 'nova.md' })
      onEvent({ type: 'file_completed', batch_id: 'batch-1', index: 2, total: 2, filename: 'nova.md', item: stagedItems[1] })
      onEvent({ type: 'batch_completed', batch_id: 'batch-1', status: 'awaiting_confirmation', total: 2, items: stagedItems })
    })
    const user = userEvent.setup()
    render(<ProjectShell onLogout={() => undefined} />)

    const addButton = await screen.findByRole('button', { name: 'Add property' }) as HTMLButtonElement
    await waitFor(() => expect(addButton.disabled).toBe(false))
    await user.click(addButton)
    const input = screen.getByLabelText('Property files') as HTMLInputElement
    expect(input.multiple).toBe(true)
    await user.upload(input, [
      new File(['Atlas'], 'atlas.md', { type: 'text/markdown' }),
      new File(['Nova'], 'nova.md', { type: 'text/markdown' }),
    ])
    expect(input.files).toHaveLength(2)
    const uploadForm = input.closest('form')
    if (!uploadForm) throw new Error('Upload form was not rendered')
    fireEvent.submit(uploadForm)

    expect(await screen.findByText('Definition Generation Agent')).toBeTruthy()
    expect(screen.getByText('Preparing 1 of 2 properties')).toBeTruthy()
    expect(within(screen.getByRole('status')).getByText('atlas.md')).toBeTruthy()
    expect(screen.getByRole('progressbar', {
      name: 'Definition Generation Agent preparation progress',
    })).toBeTruthy()

    continueStream?.()
    await screen.findByText('IMPORT PROPERTIES')
    const stageCall = apiMocks.streamRequest.mock.calls.find(
      ([path]) => path === '/projects/project-1/property-import-batches/stream',
    )
    expect(stageCall).toBeTruthy()
    const stagedForm = stageCall?.[1]?.body as FormData
    expect(stagedForm.getAll('files')).toHaveLength(2)

    const atlasInput = screen.getByLabelText('Property filename for atlas.md')
    await user.clear(atlasInput)
    await user.type(atlasInput, 'atlas-manual.md')
    await user.click(screen.getByRole('button', { name: 'Import 2 properties' }))

    await waitFor(() => expect(apiMocks.request).toHaveBeenCalledWith(
      '/projects/project-1/property-import-batches/batch-1/confirm',
      {
        method: 'POST',
        body: JSON.stringify({
          items: [
            { import_id: 'import-atlas', filename: 'atlas-manual.md' },
            { import_id: 'import-nova', filename: 'nova-guide.md' },
          ],
        }),
      },
    ))
  })
})
