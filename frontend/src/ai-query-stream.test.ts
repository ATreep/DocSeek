import { describe, expect, it } from 'vitest'
import type { ChatMessage } from './components/AIQueryChat'
import { applyAIQueryEvent } from './ai-query-stream'

describe('applyAIQueryEvent', () => {
  it('updates the active assistant message with sources, deltas, and completion', () => {
    const initial: ChatMessage[] = [
      { role: 'user', content: 'What is connected?' },
      { role: 'assistant', content: '', streaming: true },
    ]
    const withSources = applyAIQueryEvent(initial, {
      type: 'sources',
      citations: [{ kind: 'entity', id: 'neo4j', label: 'Neo4j' }],
    })
    const withText = applyAIQueryEvent(withSources, {
      type: 'delta',
      content: 'Neo4j is connected.',
    })
    const completed = applyAIQueryEvent(withText, { type: 'done' })

    expect(completed[1]).toEqual({
      role: 'assistant',
      content: 'Neo4j is connected.',
      citations: [{ kind: 'entity', id: 'neo4j', label: 'Neo4j' }],
      streaming: false,
    })
  })
})
