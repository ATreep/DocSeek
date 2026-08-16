import { describe, expect, it } from 'vitest'
import { entitiesOwnedByProperty } from './property-entities'

describe('entitiesOwnedByProperty', () => {
  it('returns only entities whose source properties include the selected property', () => {
    const entities = [
      { id: 'atlas', name: 'Atlas', source_property_ids: ['readme', 'architecture'] },
      { id: 'neo4j', name: 'Neo4j', source_property_ids: ['architecture'] },
      { id: 'beacon', name: 'Beacon', source_property_ids: ['release-notes'] },
    ]

    expect(entitiesOwnedByProperty('architecture', entities)).toEqual([
      entities[0],
      entities[1],
    ])
  })
})
