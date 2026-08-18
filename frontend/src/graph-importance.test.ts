import { describe, expect, it } from 'vitest'
import { calculateGraphImportance } from './graph-importance'

describe('calculateGraphImportance', () => {
  it('scales node size logarithmically from degree and marks the highest-ranked hubs', () => {
    const importance = calculateGraphImportance({
      nodes: [
        { id: 'hub', name: 'Atlas', source_property_ids: ['p1', 'p2', 'p3'] },
        { id: 'leaf-1', name: 'Neo4j', source_property_ids: ['p1'] },
        { id: 'leaf-2', name: 'LangGraph', source_property_ids: ['p2'] },
        { id: 'leaf-3', name: 'SQLite', source_property_ids: ['p3'] },
        { id: 'isolated', name: 'Orion', source_property_ids: ['p4'] },
      ],
      edges: [
        { source: 'hub', target: 'leaf-1', type: 'USES' },
        { source: 'hub', target: 'leaf-2', type: 'USES' },
        { source: 'hub', target: 'leaf-3', type: 'USES' },
      ],
    }, 'entity')

    expect(importance.get('hub')).toMatchObject({
      degree: 3,
      sourceCount: 3,
      size: 78,
      important: true,
      rank: 1,
    })
    expect(importance.get('leaf-1')?.size).toBeGreaterThanOrEqual(34)
    expect(importance.get('leaf-1')?.size).toBeLessThan(78)
    expect(importance.get('isolated')?.size).toBe(34)
    expect(importance.get('hub')?.score).toBeGreaterThan(importance.get('leaf-1')?.score || 0)
  })

  it('penalizes generic entity names when structural evidence is otherwise equal', () => {
    const importance = calculateGraphImportance({
      nodes: [
        { id: 'java', name: 'Java', source_property_ids: ['p1'] },
        { id: 'atlas', name: 'Atlas Platform', source_property_ids: ['p2'] },
        { id: 'java-leaf', name: 'JVM', source_property_ids: ['p1'] },
        { id: 'atlas-leaf', name: 'Atlas Search', source_property_ids: ['p2'] },
      ],
      edges: [
        { source: 'java', target: 'java-leaf', type: 'USES' },
        { source: 'atlas', target: 'atlas-leaf', type: 'USES' },
      ],
    }, 'entity')

    expect(importance.get('java')?.degree).toBe(importance.get('atlas')?.degree)
    expect(importance.get('java')?.pageRank).toBeCloseTo(importance.get('atlas')?.pageRank || 0)
    expect(importance.get('java')?.score).toBeLessThan(importance.get('atlas')?.score || 0)
  })

  it('does not apply the generic entity penalty to property nodes', () => {
    const importance = calculateGraphImportance({
      nodes: [{ id: 'user', filename: 'user.md' }, { id: 'atlas', filename: 'atlas.md' }],
      edges: [{ source: 'user', target: 'atlas', type: 'RELATED' }],
    }, 'property')

    expect(importance.get('user')?.score).toBeCloseTo(importance.get('atlas')?.score || 0)
  })
})
