import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PropertyRenameDialog from './PropertyRenameDialog'

afterEach(cleanup)

describe('PropertyRenameDialog', () => {
  it('submits the generated filename when the user accepts the default', async () => {
    const submit = vi.fn()
    render(<PropertyRenameDialog open currentFilename="notes.md" defaultFilename="atlas-release-plan.md" busy={false} onClose={() => undefined} onSubmit={submit} />)

    expect((screen.getByLabelText('Property filename') as HTMLInputElement).value).toBe('atlas-release-plan.md')
    await userEvent.click(screen.getByRole('button', { name: 'Rename property' }))

    expect(submit).toHaveBeenCalledWith('atlas-release-plan.md')
  })

  it('lets the user edit the generated filename before renaming', async () => {
    const submit = vi.fn()
    render(<PropertyRenameDialog open currentFilename="notes.md" defaultFilename="atlas-release-plan.md" busy={false} onClose={() => undefined} onSubmit={submit} />)

    const input = screen.getByLabelText('Property filename')
    await userEvent.clear(input)
    await userEvent.type(input, 'atlas-launch-guide.md')
    await userEvent.click(screen.getByRole('button', { name: 'Rename property' }))

    expect(submit).toHaveBeenCalledWith('atlas-launch-guide.md')
  })
})
