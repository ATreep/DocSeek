import { afterEach, describe, expect, it, vi } from 'vitest'
import { request, streamRequest } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

function streamOf(...chunks: string[]) {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach(chunk => controller.enqueue(encoder.encode(chunk)))
      controller.close()
    },
  })
}

describe('streamRequest', () => {
  it('parses NDJSON events split across response chunks', async () => {
    vi.stubGlobal('localStorage', { getItem: () => null })
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        streamOf('{"type":"delta",', '"content":"Hi"}\n{"type":"done"}\n'),
        { status: 200 },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)
    const events: Array<Record<string, string>> = []

    await streamRequest<Record<string, string>>(
      '/stream',
      { method: 'POST', body: JSON.stringify({ query: 'Hi' }) },
      event => events.push(event),
    )

    expect(events).toEqual([
      { type: 'delta', content: 'Hi' },
      { type: 'done' },
    ])
  })
})

describe('request', () => {
  it('accepts successful responses without a JSON body', async () => {
    vi.stubGlobal('localStorage', { getItem: () => null })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))

    await expect(request('/property-import', { method: 'DELETE' })).resolves.toBeUndefined()
  })
})
