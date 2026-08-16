import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ProjectEmptyState from './ProjectEmptyState'

describe('ProjectEmptyState', () => {
  it('creates the first project from the empty workspace', async () => {
    const user = userEvent.setup()
    const createProject = vi.fn().mockResolvedValue(true)
    render(<ProjectEmptyState busy={false} onCreate={createProject} />)

    await user.type(screen.getByLabelText('Project name'), 'Knowledge Base')
    await user.click(screen.getByRole('button', { name: 'Create project' }))

    expect(createProject).toHaveBeenCalledWith('Knowledge Base')
    expect((screen.getByLabelText('Project name') as HTMLInputElement).value).toBe('')
  })
})
