import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RelationOverlay from './RelationOverlay'

afterEach(cleanup)

const relation = {
  source: 'atlas',
  target: 'neo4j',
  type: 'USES',
  kind: 'entity' as const,
  sourceLabel: 'Atlas',
  targetLabel: 'Neo4j',
}

describe('RelationOverlay', () => {
  it('shows every stored field of a directed relation', () => {
    render(<RelationOverlay relation={relation} onClose={() => undefined} />)

    expect(screen.getByRole('dialog', { name: 'Relation detail' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'USES' })).toBeTruthy()
    expect(screen.getByText('Atlas')).toBeTruthy()
    expect(screen.getAllByText('atlas')).toHaveLength(2)
    expect(screen.getByText('Neo4j')).toBeTruthy()
    expect(screen.getAllByText('neo4j')).toHaveLength(2)
    expect(screen.getByText('Entity graph')).toBeTruthy()
  })

  it('dismisses the relation window', async () => {
    const onClose = vi.fn()
    render(<RelationOverlay relation={relation} onClose={onClose} />)

    await userEvent.setup().click(screen.getByRole('button', { name: 'Close relation detail' }))

    expect(onClose).toHaveBeenCalledOnce()
  })
})
