import { describe, expect, it } from 'vitest'
import { relationsForNode, resolveGraphRelation } from './graph-relations'

const graph = {
  nodes: [
    { id: 'atlas', name: 'Atlas' },
    { id: 'neo4j', name: 'Neo4j' },
    { id: 'guide', filename: 'guide.md' },
  ],
  edges: [
    { source: 'atlas', target: 'neo4j', type: 'USES' },
    { source: 'guide', target: 'atlas', type: 'DOCUMENTS' },
  ],
}

describe('graph relations', () => {
  it('resolves display labels for a directed relation', () => {
    expect(resolveGraphRelation(graph, graph.edges[0], 'entity')).toEqual({
      source: 'atlas',
      target: 'neo4j',
      type: 'USES',
      kind: 'entity',
      sourceLabel: 'Atlas',
      targetLabel: 'Neo4j',
    })
  })

  it('returns every incoming and outgoing relation for a node', () => {
    expect(relationsForNode(graph, 'atlas', 'entity')).toEqual([
      expect.objectContaining({
        type: 'USES',
        direction: 'outgoing',
        relatedId: 'neo4j',
        relatedLabel: 'Neo4j',
      }),
      expect.objectContaining({
        type: 'DOCUMENTS',
        direction: 'incoming',
        relatedId: 'guide',
        relatedLabel: 'guide.md',
      }),
    ])
  })
})
