import type { ChatMessage } from './components/AIQueryChat'

export type Citation = {
  kind?: 'property' | 'entity' | string
  id?: string
  label?: string
}

export type AIQueryStreamEvent =
  | { type: 'sources'; citations: Citation[]; retrieved?: Record<string, unknown> }
  | { type: 'delta'; content: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

export function applyAIQueryEvent(
  messages: ChatMessage[],
  event: AIQueryStreamEvent,
): ChatMessage[] {
  let activeIndex = -1
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'assistant' && messages[index].streaming) {
      activeIndex = index
      break
    }
  }
  if (activeIndex < 0) return messages

  const active = messages[activeIndex]
  let updated = active
  if (event.type === 'sources') {
    updated = { ...active, citations: event.citations }
  } else if (event.type === 'delta') {
    updated = { ...active, content: active.content + event.content }
  } else if (event.type === 'done') {
    updated = { ...active, streaming: false }
  } else if (event.type === 'error') {
    updated = {
      ...active,
      content: active.content || event.message,
      streaming: false,
    }
  }

  return messages.map((message, index) =>
    index === activeIndex ? updated : message,
  )
}
