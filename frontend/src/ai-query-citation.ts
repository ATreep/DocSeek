import type { Property } from './api'
import type { ChatCitation } from './components/AIQueryChat'
import type { Entity } from './components/EntityOverlay'

export type ResolvedAIQueryCitation =
  | { kind: 'property'; value: Property }
  | { kind: 'entity'; value: Entity }

export function resolveAIQueryCitation(
  citation: ChatCitation,
  properties: Property[],
  entities: Entity[],
): ResolvedAIQueryCitation | null {
  if (!citation.id) return null
  if (citation.kind === 'property') {
    const property = properties.find(item => item.id === citation.id)
    return property ? { kind: 'property', value: property } : null
  }
  if (citation.kind === 'entity') {
    const entity = entities.find(item => item.id === citation.id)
    return entity ? { kind: 'entity', value: entity } : null
  }
  return null
}
