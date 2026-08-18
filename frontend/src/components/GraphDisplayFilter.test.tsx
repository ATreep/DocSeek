import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Property } from '../api'
import type { GraphDisplaySelection } from '../graph-display-filter'
import GraphDisplayFilter from './GraphDisplayFilter'

const properties = [
  { id: 'one', filename: 'one.md', relative_path: 'properties/Group 1/one.md' },
  { id: 'two', filename: 'property-2.md', relative_path: 'properties/Group 2/property-2.md' },
] as Property[]

afterEach(cleanup)

function ControlledFilter({ onChange }: { onChange: (selection: GraphDisplaySelection) => void }) {
  const [selection, setSelection] = useState<GraphDisplaySelection>({ groupPaths: [], propertyIds: [] })
  return <GraphDisplayFilter
    properties={properties}
    selection={selection}
    onChange={next => {
      setSelection(next)
      onChange(next)
    }}
  />
}

describe('GraphDisplayFilter', () => {
  it('combines checked groups and properties and reports the selection count', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<ControlledFilter onChange={onChange} />)

    await user.click(screen.getByRole('button', { name: 'Filter graph display' }))
    expect(screen.getByRole('dialog', { name: 'Graph display filter' })).toBeTruthy()
    await user.click(screen.getByRole('checkbox', { name: 'Group 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'property-2.md' }))

    expect(onChange).toHaveBeenLastCalledWith({
      groupPaths: ['Group 1'],
      propertyIds: ['two'],
    })
    expect(screen.getByText('2', { selector: '.graph-filter-badge' })).toBeTruthy()
  })

  it('clears every selection to restore the complete graph', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<ControlledFilter onChange={onChange} />)
    await user.click(screen.getByRole('button', { name: 'Filter graph display' }))
    await user.click(screen.getByRole('checkbox', { name: 'Group 1' }))

    await user.click(screen.getByRole('button', { name: 'Clear graph display filter' }))

    expect(onChange).toHaveBeenLastCalledWith({ groupPaths: [], propertyIds: [] })
    expect(screen.getByText('All properties')).toBeTruthy()
  })

  it('keeps large selection counts inside a bounded badge', () => {
    render(<GraphDisplayFilter
      properties={properties}
      selection={{ groupPaths: [], propertyIds: Array.from({ length: 120 }, (_, index) => `property-${index}`) }}
      onChange={() => undefined}
    />)

    const badge = screen.getByText('99+', { selector: '.graph-filter-badge' })
    expect(badge.getAttribute('title')).toBe('120 selections')
  })
})
