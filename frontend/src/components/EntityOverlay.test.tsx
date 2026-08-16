import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import EntityOverlay from './EntityOverlay'

afterEach(cleanup)

describe('EntityOverlay', () => {
  it('shows entity metadata and source property references', () => {
    const { container } = render(<EntityOverlay entity={{ id: 'atlas', name: 'Atlas', type: 'System', definition: 'A release coordination service.', source_contexts: [{ property_id: 'property-1', text: 'Atlas coordinates release publication.' }], source_property_ids: ['property-1'], source_properties: [{ id: 'property-1', filename: 'release-plan.md', property_type: 'markdown', definition: 'The release plan is the source of truth.' }] }} onClose={() => undefined} />)
    expect(screen.getByRole('heading', { name: 'Atlas' })).toBeTruthy()
    expect(container.querySelector('.preview-frame')).toBeNull()
    expect(screen.getByText('System')).toBeTruthy()
    expect(screen.getByText('A release coordination service.')).toBeTruthy()
    expect(screen.getByText('Atlas coordinates release publication.')).toBeTruthy()
    expect(screen.getByText('property-1')).toBeTruthy()
    expect(screen.getByText('release-plan.md')).toBeTruthy()
    expect(screen.getByText('The release plan is the source of truth.')).toBeTruthy()
  })

  it('shows every incoming and outgoing entity relation', async () => {
    const onRelationSelect = vi.fn()
    const relations = [
      { source: 'atlas', target: 'neo4j', type: 'USES', kind: 'entity' as const, sourceLabel: 'Atlas', targetLabel: 'Neo4j', direction: 'outgoing' as const, relatedId: 'neo4j', relatedLabel: 'Neo4j' },
      { source: 'guide', target: 'atlas', type: 'DOCUMENTS', kind: 'entity' as const, sourceLabel: 'guide.md', targetLabel: 'Atlas', direction: 'incoming' as const, relatedId: 'guide', relatedLabel: 'guide.md' },
    ]
    render(<EntityOverlay entity={{ id: 'atlas', name: 'Atlas' }} relations={relations} onRelationSelect={onRelationSelect} onClose={() => undefined} />)

    expect(screen.getByText('RELATIONS')).toBeTruthy()
    expect(screen.getByText('2 relations')).toBeTruthy()
    await userEvent.setup().click(screen.getByRole('button', { name: 'USES outgoing Neo4j' }))
    expect(screen.getByRole('button', { name: 'DOCUMENTS incoming guide.md' })).toBeTruthy()
    expect(onRelationSelect).toHaveBeenCalledWith(relations[0])
  })
})
