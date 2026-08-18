import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PropertyTree from './PropertyTree'

afterEach(cleanup)

describe('PropertyTree', () => {
  it('renders folders and selects a property within them', async () => {
    const select = vi.fn()
    render(<PropertyTree properties={[{ id: 'p1', filename: 'guide.md', property_type: 'markdown', relative_path: 'properties/references/guide.md', status: 'active' }]} onSelect={select} />)

    expect(screen.getByText('references')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /guide.md/i }))
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ id: 'p1' }))
  })

  it('collapses and unfolds each group independently', async () => {
    const user = userEvent.setup()
    render(<PropertyTree properties={[
      { id: 'p1', filename: 'guide.md', property_type: 'markdown', relative_path: 'properties/references/guide.md', status: 'active' },
      { id: 'p2', filename: 'invoice.pdf', property_type: 'pdf', relative_path: 'properties/finance/invoice.pdf', status: 'active' },
    ]} onSelect={() => undefined} />)

    const references = screen.getByRole('button', { name: 'Collapse references group' })
    expect(references.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('button', { name: /guide.md/i })).toBeTruthy()

    await user.click(references)

    expect(screen.queryByRole('button', { name: /guide.md/i })).toBeNull()
    expect(screen.getByRole('button', { name: /invoice.pdf/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Expand references group' }).getAttribute('aria-expanded')).toBe('false')

    await user.click(screen.getByRole('button', { name: 'Expand references group' }))
    expect(screen.getByRole('button', { name: /guide.md/i })).toBeTruthy()
  })
})
