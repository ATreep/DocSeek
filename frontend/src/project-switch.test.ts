import { describe, expect, it, vi } from 'vitest'
import { canLeaveProject, closeMcpBeforeSwitch } from './project-switch'

describe('canLeaveProject', () => {
  it('blocks switching or closing while a candidate build is locked', () => {
    expect(canLeaveProject(true)).toBe(false)
    expect(canLeaveProject(false)).toBe(true)
  })
})

describe('closeMcpBeforeSwitch', () => {
  it('closes the current project MCP endpoint before changing projects', async () => {
    const request = vi.fn().mockResolvedValue({})
    await closeMcpBeforeSwitch('project-a', true, request)
    expect(request).toHaveBeenCalledWith('/projects/project-a/mcp/close', { method: 'POST' })
  })

  it('does not call the API when MCP is already closed', async () => {
    const request = vi.fn()
    await closeMcpBeforeSwitch('project-a', false, request)
    expect(request).not.toHaveBeenCalled()
  })
})
