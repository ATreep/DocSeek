import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ImportPropertyDialog from './ImportPropertyDialog'

afterEach(cleanup)

const items = [
  {
    import_id: 'import-atlas',
    original_filename: 'notes.md',
    suggested_filename: 'atlas-guide.md',
    definition: 'An introduction to Atlas including setup and usage.',
  },
  {
    import_id: 'import-nova',
    original_filename: 'readme.md',
    suggested_filename: 'nova-readme.md',
    definition: 'An overview of Nova including installation and FAQs.',
  },
]

describe('ImportPropertyDialog', () => {
  it('shows every generated definition and confirms all suggested filenames together', async () => {
    const confirm = vi.fn()
    render(<ImportPropertyDialog open items={items} busy={false} onCancel={() => undefined} onConfirm={confirm} />)

    expect(screen.getByText('IMPORT PROPERTIES')).toBeTruthy()
    expect(screen.getByText(items[0].definition)).toBeTruthy()
    expect(screen.getByText(items[1].definition)).toBeTruthy()
    expect((screen.getByLabelText('Property filename for notes.md') as HTMLInputElement).value).toBe('atlas-guide.md')
    expect((screen.getByLabelText('Property filename for readme.md') as HTMLInputElement).value).toBe('nova-readme.md')

    await userEvent.click(screen.getByRole('button', { name: 'Import 2 properties' }))

    expect(confirm).toHaveBeenCalledWith([
      { import_id: 'import-atlas', filename: 'atlas-guide.md' },
      { import_id: 'import-nova', filename: 'nova-readme.md' },
    ])
  })

  it('lets the user edit each suggested filename before importing', async () => {
    const confirm = vi.fn()
    render(<ImportPropertyDialog open items={items} busy={false} onCancel={() => undefined} onConfirm={confirm} />)

    const atlasInput = screen.getByLabelText('Property filename for notes.md')
    const novaInput = screen.getByLabelText('Property filename for readme.md')
    await userEvent.clear(atlasInput)
    await userEvent.type(atlasInput, 'atlas-manual.md')
    await userEvent.clear(novaInput)
    await userEvent.type(novaInput, 'nova-guide.md')
    await userEvent.click(screen.getByRole('button', { name: 'Import 2 properties' }))

    expect(confirm).toHaveBeenCalledWith([
      { import_id: 'import-atlas', filename: 'atlas-manual.md' },
      { import_id: 'import-nova', filename: 'nova-guide.md' },
    ])
  })

  it('blocks confirmation when a filename is empty or duplicated', async () => {
    render(<ImportPropertyDialog open items={items} busy={false} onCancel={() => undefined} onConfirm={() => undefined} />)
    const confirmButton = screen.getByRole('button', { name: 'Import 2 properties' }) as HTMLButtonElement
    const atlasInput = screen.getByLabelText('Property filename for notes.md')
    const novaInput = screen.getByLabelText('Property filename for readme.md')

    await userEvent.clear(atlasInput)
    expect(confirmButton.disabled).toBe(true)
    await userEvent.type(atlasInput, 'same.md')
    await userEvent.clear(novaInput)
    await userEvent.type(novaInput, 'SAME.md')

    expect(confirmButton.disabled).toBe(true)
    expect(screen.getByText('Each property needs a unique filename.')).toBeTruthy()
  })

  it('warns when extracted property content exceeds the supported review thresholds', () => {
    render(<ImportPropertyDialog
      open
      items={[{
        ...items[0],
        word_count: 15_001,
        character_count: 60_001,
        oversized: true,
        reasons: ['word_count', 'character_count'],
      }]}
      busy={false}
      onCancel={() => undefined}
      onConfirm={() => undefined}
    />)

    expect(screen.getByRole('alert').textContent).toContain('15,001 words')
    expect(screen.getByRole('alert').textContent).toContain('60,001 characters')
  })

  it('cancels the complete batch from both close controls', async () => {
    const cancel = vi.fn()
    const { rerender } = render(<ImportPropertyDialog open items={items} busy={false} onCancel={cancel} onConfirm={() => undefined} />)

    await userEvent.click(screen.getByRole('button', { name: 'Close import window' }))
    expect(cancel).toHaveBeenCalledTimes(1)

    rerender(<ImportPropertyDialog open items={items} busy={false} onCancel={cancel} onConfirm={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(cancel).toHaveBeenCalledTimes(2)
  })
})
