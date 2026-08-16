import { describe, expect, it } from 'vitest'
import { resolveAIQueryCitation } from './ai-query-citation'

describe('resolveAIQueryCitation', () => {
  const properties = [
    { id: 'p1', project_id: 'project', filename: 'plan.md', property_type: 'markdown', status: 'active', relative_path: 'properties/plan.md' },
  ]
  const entities = [
    { id: 'e1', name: 'Neo4j', definition: 'A graph database.' },
  ]

  it('resolves property and entity citations to their matching graph records', () => {
    expect(resolveAIQueryCitation({ kind: 'property', id: 'p1', label: 'plan.md' }, properties, entities)).toEqual({
      kind: 'property', value: properties[0],
    })
    expect(resolveAIQueryCitation({ kind: 'entity', id: 'e1', label: 'Neo4j' }, properties, entities)).toEqual({
      kind: 'entity', value: entities[0],
    })
  })
})
