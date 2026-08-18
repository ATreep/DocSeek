import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RegroupConfirmationDialog from './RegroupConfirmationDialog'

afterEach(cleanup)

describe('RegroupConfirmationDialog', () => {
  it('uses a wider default desktop width for property tree comparison', () => {
    render(<RegroupConfirmationDialog
      open
      busy={false}
      proposal={null}
      onClose={() => undefined}
      onConfirm={() => undefined}
    />)

    expect(screen.getByRole('dialog', { name: 'Confirm property tree changes' }).style.getPropertyValue('--regroup-confirm-width'))
      .toBe('980px')
  })

  it('shows tree and filename diffs and submits an edited filename', async () => {
    const confirm = vi.fn()
    render(<RegroupConfirmationDialog
      open
      busy={false}
      proposal={{
        catalog_signature: 'a'.repeat(64),
        changes: [{
          property_id: 'manual',
          current_directory: 'Legacy',
          proposed_directory: 'Product/Atlas',
          current_filename: 'manual.md',
          proposed_filename: 'atlas-guide.md',
          definition: 'The Atlas user manual.',
          changed: true,
        }],
      }}
      onClose={() => undefined}
      onConfirm={confirm}
    />)

    expect(screen.getByText('Legacy')).toBeTruthy()
    expect(screen.getByText('Product/Atlas')).toBeTruthy()
    const filename = screen.getByLabelText('Proposed filename for manual.md')
    await userEvent.clear(filename)
    await userEvent.type(filename, 'atlas-user-guide.md')
    await userEvent.click(screen.getByRole('button', { name: 'Apply confirmed changes' }))

    expect(confirm).toHaveBeenCalledWith([{
      property_id: 'manual',
      directory: 'Product/Atlas',
      filename: 'atlas-user-guide.md',
    }])
  })
})
