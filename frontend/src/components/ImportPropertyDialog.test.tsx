import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ImportPropertyDialog from './ImportPropertyDialog'

afterEach(cleanup)

describe('ImportPropertyDialog', () => {
  it('imports with the suggested filename when the user accepts the default', async () => {
    const confirm = vi.fn()
    render(<ImportPropertyDialog open originalFilename="notes.md" defaultFilename="atlas-release-plan.md" busy={false} onCancel={() => undefined} onConfirm={confirm} />)

    expect(screen.getByText('IMPORT PROPERTY')).toBeTruthy()
    expect((screen.getByLabelText('Property filename') as HTMLInputElement).value).toBe('atlas-release-plan.md')
    await userEvent.click(screen.getByRole('button', { name: 'Import property' }))

    expect(confirm).toHaveBeenCalledWith('atlas-release-plan.md')
  })

  it('lets the user edit the suggested filename before importing', async () => {
    const confirm = vi.fn()
    render(<ImportPropertyDialog open originalFilename="notes.md" defaultFilename="atlas-release-plan.md" busy={false} onCancel={() => undefined} onConfirm={confirm} />)

    const input = screen.getByLabelText('Property filename')
    await userEvent.clear(input)
    await userEvent.type(input, 'atlas-launch-guide.md')
    await userEvent.click(screen.getByRole('button', { name: 'Import property' }))

    expect(confirm).toHaveBeenCalledWith('atlas-launch-guide.md')
  })

  it('cancels the import from both the close icon and Cancel button', async () => {
    const cancel = vi.fn()
    const { rerender } = render(<ImportPropertyDialog open originalFilename="notes.md" defaultFilename="notes.md" busy={false} onCancel={cancel} onConfirm={() => undefined} />)

    await userEvent.click(screen.getByRole('button', { name: 'Close import window' }))
    expect(cancel).toHaveBeenCalledTimes(1)

    rerender(<ImportPropertyDialog open originalFilename="notes.md" defaultFilename="notes.md" busy={false} onCancel={cancel} onConfirm={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(cancel).toHaveBeenCalledTimes(2)
  })
})
