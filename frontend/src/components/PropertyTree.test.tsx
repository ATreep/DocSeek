import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PropertyTree from './PropertyTree'

describe('PropertyTree', () => {
  it('renders folders and selects a property within them', async () => {
    const select = vi.fn()
    render(<PropertyTree properties={[{ id: 'p1', filename: 'guide.md', property_type: 'markdown', relative_path: 'properties/references/guide.md', status: 'active' }]} onSelect={select} />)

    expect(screen.getByText('references')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /guide.md/i }))
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ id: 'p1' }))
  })
})
