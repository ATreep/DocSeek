import { describe, expect, it } from 'vitest'
import type { Property } from './api'
import {
  filterGraphByPropertyIds,
  graphDisplayGroups,
  resolveDisplayPropertyIds,
  type GraphDisplaySelection,
} from './graph-display-filter'

const properties = [
  { id: 'group-1-property', filename: 'one.md', relative_path: 'properties/Group 1/one.md' },
  { id: 'property-2', filename: 'two.md', relative_path: 'properties/Other/two.md' },
  { id: 'group-3-property', filename: 'three.md', relative_path: 'properties/Group 3/Research/three.md' },
  { id: 'unselected-property', filename: 'four.md', relative_path: 'properties/Other/four.md' },
] as Property[]

describe('graph display filtering', () => {
  it('derives nested groups with every descendant property', () => {
    expect(graphDisplayGroups(properties)).toEqual([
      { name: 'Group 1', path: 'Group 1', depth: 0, propertyIds: ['group-1-property'] },
      { name: 'Group 3', path: 'Group 3', depth: 0, propertyIds: ['group-3-property'] },
      { name: 'Research', path: 'Group 3/Research', depth: 1, propertyIds: ['group-3-property'] },
      { name: 'Other', path: 'Other', depth: 0, propertyIds: ['property-2', 'unselected-property'] },
    ])
  })

  it('resolves groups and individual properties as a union', () => {
    const selection: GraphDisplaySelection = {
      groupPaths: ['Group 1', 'Group 3'],
      propertyIds: ['property-2'],
    }

    expect(resolveDisplayPropertyIds(properties, selection)).toEqual(new Set([
      'group-1-property',
      'group-3-property',
      'property-2',
    ]))
  })

  it('uses an empty selection to show every property', () => {
    expect(resolveDisplayPropertyIds(properties, { groupPaths: [], propertyIds: [] }))
      .toEqual(new Set(properties.map(property => property.id)))
  })

  it('filters property nodes and keeps only edges with two visible endpoints', () => {
    const graph = {
      nodes: properties.map(property => ({ id: property.id, filename: property.filename })),
      edges: [
        { source: 'group-1-property', target: 'property-2', type: 'SUPPORTS' },
        { source: 'group-1-property', target: 'unselected-property', type: 'IGNORED' },
      ],
    }

    expect(filterGraphByPropertyIds(
      graph,
      'property',
      new Set(['group-1-property', 'property-2']),
    )).toMatchObject({
      nodes: [graph.nodes[0], graph.nodes[1]],
      edges: [graph.edges[0]],
    })
  })

  it('keeps the selected property group ancestry visible', () => {
    const graph = {
      nodes: [
        { id: 'group:/', name: 'Root', node_type: 'group' as const },
        { id: 'group:Group 3', name: 'Group 3', node_type: 'group' as const },
        { id: 'group:Group 3/Research', name: 'Research', node_type: 'group' as const },
        { id: 'group-3-property', filename: 'three.md', node_type: 'property' as const },
        { id: 'unselected-property', filename: 'four.md', node_type: 'property' as const },
      ],
      edges: [
        { source: 'group:/', target: 'group:Group 3', type: 'CONTAINS_GROUP' },
        { source: 'group:Group 3', target: 'group:Group 3/Research', type: 'CONTAINS_GROUP' },
        { source: 'group:Group 3/Research', target: 'group-3-property', type: 'CONTAINS_PROPERTY' },
        { source: 'group:/', target: 'unselected-property', type: 'CONTAINS_PROPERTY' },
      ],
    }

    expect(filterGraphByPropertyIds(
      graph,
      'property',
      new Set(['group-3-property']),
    )).toEqual({
      nodes: graph.nodes.slice(0, 4),
      edges: graph.edges.slice(0, 3),
    })
  })

  it('filters entity nodes by source ownership and applies the same edge rule', () => {
    const graph = {
      nodes: [
        { id: 'entity-1', name: 'One', source_property_ids: ['group-1-property'] },
        { id: 'entity-2', name: 'Two', source_property_ids: ['property-2', 'unselected-property'] },
        { id: 'entity-3', name: 'Three', source_property_ids: ['unselected-property'] },
      ],
      edges: [
        { source: 'entity-1', target: 'entity-2', type: 'VISIBLE' },
        { source: 'entity-2', target: 'entity-3', type: 'HIDDEN' },
      ],
    }

    expect(filterGraphByPropertyIds(
      graph,
      'entity',
      new Set(['group-1-property', 'property-2']),
    )).toMatchObject({
      nodes: [graph.nodes[0], graph.nodes[1]],
      edges: [graph.edges[0]],
    })
  })
})
