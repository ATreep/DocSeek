import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PropertyOverlay from './PropertyOverlay'

afterEach(cleanup)

describe('PropertyOverlay preview', () => {
  it('renders an uploaded image from the authenticated raw endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['image']) }))
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:image-preview'), revokeObjectURL: vi.fn() })
    render(<PropertyOverlay property={{ id: 'p1', project_id: 'project-1', filename: 'diagram.png', property_type: 'image', relative_path: 'properties/diagram.png', status: 'active' }} onClose={() => undefined} />)
    await waitFor(() => expect(screen.getByRole('img', { name: 'diagram.png' }).getAttribute('src')).toBe('blob:image-preview'))
  })

  it('exposes lock-aware property mutation actions', () => {
    const onReplace = vi.fn()
    const view = render(<PropertyOverlay property={{ id: 'p1', project_id: 'project-1', filename: 'diagram.png', property_type: 'image', relative_path: 'properties/diagram.png', status: 'active' }} locked={false} onClose={() => undefined} onRename={() => undefined} onReplace={onReplace} onRemove={() => undefined} />)
    const actions = within(view.container.querySelector('.overlay-actions') as HTMLElement)
    expect(actions.getAllByRole('button').map(button => button.getAttribute('aria-label'))).toEqual([
      'Rename property',
      'Replace property',
      'Remove property',
    ])
    expect(screen.getByRole('button', { name: 'Rename property' }).hasAttribute('disabled')).toBe(false)
    expect(screen.getByRole('button', { name: 'Replace property' }).hasAttribute('disabled')).toBe(false)
    expect(screen.getByRole('button', { name: 'Remove property' }).hasAttribute('disabled')).toBe(false)
    render(<PropertyOverlay property={{ id: 'p2', project_id: 'project-1', filename: 'locked.txt', property_type: 'text', relative_path: 'properties/locked.txt', status: 'queued' }} locked onClose={() => undefined} onRename={() => undefined} onReplace={() => undefined} onRemove={() => undefined} />)
    expect(screen.getAllByRole('button', { name: 'Rename property' })[1].hasAttribute('disabled')).toBe(true)
    expect(screen.getAllByRole('button', { name: 'Replace property' })[1].hasAttribute('disabled')).toBe(true)
  })

  it('opens property replacement from the preview action row', async () => {
    const onReplace = vi.fn()
    render(<PropertyOverlay property={{ id: 'p1', project_id: 'project-1', filename: 'notes.md', property_type: 'markdown', relative_path: 'properties/notes.md', status: 'active' }} onClose={() => undefined} onRename={() => undefined} onReplace={onReplace} />)

    await userEvent.click(screen.getByRole('button', { name: 'Replace property' }))

    expect(onReplace).toHaveBeenCalledTimes(1)
  })

  it('does not render generated filename suggestions as property attributes', () => {
    render(<PropertyOverlay property={{ id: 'p3', project_id: 'project-1', filename: 'notes.md', property_type: 'markdown', relative_path: 'properties/notes.md', status: 'active' }} onClose={() => undefined} />)
    expect(screen.queryByText('Suggested name')).toBeNull()
  })

  it('shows every entity owned by the property and opens the selected entity', async () => {
    const onEntitySelect = vi.fn()
    const entities = [
      { id: 'atlas', name: 'Atlas', type: 'Product', definition: 'A document intelligence product.', source_property_ids: ['p4'] },
      { id: 'neo4j', name: 'Neo4j', type: 'Technology', definition: 'The graph database used by Atlas.', source_property_ids: ['p4'] },
    ]

    const view = render(
      <PropertyOverlay
        property={{ id: 'p4', project_id: 'project-1', filename: 'atlas-readme.md', property_type: 'markdown', relative_path: 'properties/atlas-readme.md', status: 'active' }}
        entities={entities}
        onEntitySelect={onEntitySelect}
        onClose={() => undefined}
      />,
    )
    const preview = within(view.container)

    expect(preview.getByText('OWNED ENTITIES')).toBeTruthy()
    expect(preview.getByText('2 entities')).toBeTruthy()
    expect(preview.getByText('A document intelligence product.')).toBeTruthy()
    expect(preview.getByText('The graph database used by Atlas.')).toBeTruthy()

    await userEvent.click(preview.getByRole('button', { name: /Atlas Product/i }))
    expect(onEntitySelect).toHaveBeenCalledWith(entities[0])
  })

  it('shows every property relation and opens its detail', async () => {
    const onRelationSelect = vi.fn()
    const relations = [
      { source: 'p4', target: 'architecture', type: 'REFERENCES', kind: 'property' as const, sourceLabel: 'atlas-readme.md', targetLabel: 'architecture.md', direction: 'outgoing' as const, relatedId: 'architecture', relatedLabel: 'architecture.md' },
      { source: 'release', target: 'p4', type: 'DEPENDS_ON', kind: 'property' as const, sourceLabel: 'release.md', targetLabel: 'atlas-readme.md', direction: 'incoming' as const, relatedId: 'release', relatedLabel: 'release.md' },
    ]
    render(
      <PropertyOverlay
        property={{ id: 'p4', project_id: 'project-1', filename: 'atlas-readme.md', property_type: 'markdown', relative_path: 'properties/atlas-readme.md', status: 'active' }}
        relations={relations}
        onRelationSelect={onRelationSelect}
        onClose={() => undefined}
      />,
    )

    expect(screen.getByText('RELATIONS')).toBeTruthy()
    expect(screen.getByText('2 relations')).toBeTruthy()
    await userEvent.setup().click(screen.getByRole('button', { name: 'REFERENCES outgoing architecture.md' }))
    expect(screen.getByRole('button', { name: 'DEPENDS_ON incoming release.md' })).toBeTruthy()
    expect(onRelationSelect).toHaveBeenCalledWith(relations[0])
  })
})
