import { describe, expect, it, vi } from 'vitest'
import { cancelProcessing, retryProcessing } from './processing-actions'

describe('processing actions', () => {
  it('cancels the active project job', async () => {
    const request = vi.fn().mockResolvedValue({ status: 'cancelled' })
    await cancelProcessing('project-a', request)
    expect(request).toHaveBeenCalledWith('/projects/project-a/processing/cancel', { method: 'POST' })
  })

  it('retries a failed property job', async () => {
    const request = vi.fn().mockResolvedValue({ status: 'queued' })
    await retryProcessing('project-a', 'property-a', request)
    expect(request).toHaveBeenCalledWith('/projects/project-a/processing/retry', { method: 'POST', body: JSON.stringify({ property_id: 'property-a' }) })
  })
})
