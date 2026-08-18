import type { Property } from './api'
import type { GraphData, GraphKind, GraphNode } from './graph-relations'

export type GraphDisplaySelection = {
  groupPaths: string[]
  propertyIds: string[]
}

export type GraphDisplayGroup = {
  name: string
  path: string
  depth: number
  propertyIds: string[]
}

export const EMPTY_GRAPH_DISPLAY_SELECTION: GraphDisplaySelection = {
  groupPaths: [],
  propertyIds: [],
}

function propertyGroupSegments(property: Property): string[] {
  const parts = property.relative_path.split('/').filter(Boolean)
  if (parts[0] === 'properties') parts.shift()
  parts.pop()
  return parts
}

export function graphDisplayGroups(properties: Property[]): GraphDisplayGroup[] {
  const groups = new Map<string, GraphDisplayGroup>()
  for (const property of properties) {
    const segments = propertyGroupSegments(property)
    for (let index = 0; index < segments.length; index += 1) {
      const path = segments.slice(0, index + 1).join('/')
      const group = groups.get(path) || {
        name: segments[index],
        path,
        depth: index,
        propertyIds: [],
      }
      if (!group.propertyIds.includes(property.id)) group.propertyIds.push(property.id)
      groups.set(path, group)
    }
  }
  return Array.from(groups.values())
    .map(group => ({
      ...group,
      propertyIds: [...group.propertyIds].sort(),
    }))
    .sort((left, right) => left.path.localeCompare(right.path))
}

export function resolveDisplayPropertyIds(
  properties: Property[],
  selection: GraphDisplaySelection,
): Set<string> {
  if (!selection.groupPaths.length && !selection.propertyIds.length) {
    return new Set(properties.map(property => property.id))
  }
  const availablePropertyIds = new Set(properties.map(property => property.id))
  const selected = new Set(
    selection.propertyIds.filter(propertyId => availablePropertyIds.has(propertyId)),
  )
  const groupsByPath = new Map(
    graphDisplayGroups(properties).map(group => [group.path, group.propertyIds]),
  )
  for (const path of selection.groupPaths) {
    for (const propertyId of groupsByPath.get(path) || []) selected.add(propertyId)
  }
  return selected
}

function entitySourcePropertyIds(node: GraphNode): string[] {
  const direct = Array.isArray(node.source_property_ids)
    ? node.source_property_ids.filter((value): value is string => typeof value === 'string')
    : []
  const sources = Array.isArray(node.source_properties)
    ? node.source_properties.flatMap(source => {
      if (!source || typeof source !== 'object') return []
      const id = (source as { id?: unknown }).id
      return typeof id === 'string' ? [id] : []
    })
    : []
  return [...new Set([...direct, ...sources])]
}

export function filterGraphByPropertyIds(
  graph: GraphData,
  kind: GraphKind,
  propertyIds: Set<string>,
): GraphData {
  const nodes = graph.nodes.filter(node => (
    kind === 'property'
      ? propertyIds.has(node.id)
      : entitySourcePropertyIds(node).some(propertyId => propertyIds.has(propertyId))
  ))
  const nodeIds = new Set(nodes.map(node => node.id))
  return {
    ...graph,
    nodes,
    edges: graph.edges.filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target)),
  }
}
