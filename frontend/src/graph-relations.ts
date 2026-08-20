export type GraphKind = 'property' | 'entity'

export type GraphNode = {
  id: string
  name?: string
  filename?: string
  property_type?: string
  node_type?: 'property' | 'group'
  group_path?: string
  source_property_ids?: string[]
  type?: string
  [key: string]: unknown
}

export type GraphEdge = {
  source: string
  target: string
  type: string
}

export type GraphData = {
  nodes: GraphNode[]
  edges: GraphEdge[]
  snapshot_id?: string | null
}

export type GraphRelationDetail = GraphEdge & {
  kind: GraphKind
  sourceLabel: string
  targetLabel: string
  direction?: 'incoming' | 'outgoing'
  relatedId?: string
  relatedLabel?: string
}

export function graphNodeLabel(node: GraphNode): string {
  return node.name || node.filename || node.id
}

export function resolveGraphRelation(
  graph: GraphData,
  edge: GraphEdge,
  kind: GraphKind,
): GraphRelationDetail {
  const labels = new Map(graph.nodes.map(node => [node.id, graphNodeLabel(node)]))
  return {
    ...edge,
    kind,
    sourceLabel: labels.get(edge.source) || edge.source,
    targetLabel: labels.get(edge.target) || edge.target,
  }
}

export function relationsForNode(
  graph: GraphData,
  nodeId: string,
  kind: GraphKind,
): GraphRelationDetail[] {
  return graph.edges.flatMap(edge => {
    if (edge.source !== nodeId && edge.target !== nodeId) return []
    const relation = resolveGraphRelation(graph, edge, kind)
    const outgoing = edge.source === nodeId
    return [{
      ...relation,
      direction: outgoing ? 'outgoing' : 'incoming',
      relatedId: outgoing ? edge.target : edge.source,
      relatedLabel: outgoing ? relation.targetLabel : relation.sourceLabel,
    }]
  })
}
