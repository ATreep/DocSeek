import { graphNodeLabel, type GraphData, type GraphKind } from './graph-relations'

export type NodeImportance = {
  degree: number
  pageRank: number
  sourceCount: number
  score: number
  size: number
  important: boolean
  rank: number
}

const GENERIC_ENTITY_NAMES = new Set([
  'coding',
  'computer',
  'data',
  'java',
  'network',
  'pc',
  'software',
  'system',
  'technology',
  'user',
  'users',
])

const PAGE_RANK_DAMPING = 0.85
const PAGE_RANK_ITERATIONS = 24

function normalize(values: Map<string, number>): Map<string, number> {
  const rows = [...values.values()]
  const minimum = Math.min(...rows)
  const maximum = Math.max(...rows)
  if (!Number.isFinite(minimum) || maximum <= minimum) {
    return new Map([...values].map(([id]) => [id, 0]))
  }
  return new Map([...values].map(([id, value]) => [id, (value - minimum) / (maximum - minimum)]))
}

function calculatePageRank(nodeIds: string[], adjacency: Map<string, Set<string>>): Map<string, number> {
  if (!nodeIds.length) return new Map()
  const nodeCount = nodeIds.length
  let ranks = new Map(nodeIds.map(id => [id, 1 / nodeCount]))
  for (let iteration = 0; iteration < PAGE_RANK_ITERATIONS; iteration += 1) {
    const next = new Map(nodeIds.map(id => [id, (1 - PAGE_RANK_DAMPING) / nodeCount]))
    for (const id of nodeIds) {
      const neighbors = adjacency.get(id) || new Set<string>()
      const rank = ranks.get(id) || 0
      if (!neighbors.size) {
        const share = PAGE_RANK_DAMPING * rank / nodeCount
        for (const targetId of nodeIds) next.set(targetId, (next.get(targetId) || 0) + share)
        continue
      }
      const share = PAGE_RANK_DAMPING * rank / neighbors.size
      for (const targetId of neighbors) next.set(targetId, (next.get(targetId) || 0) + share)
    }
    ranks = next
  }
  return ranks
}

function genericEntityPenalty(label: string, kind: GraphKind): number {
  if (kind !== 'entity') return 0
  return GENERIC_ENTITY_NAMES.has(label.trim().toLocaleLowerCase()) ? 0.15 : 0
}

export function calculateGraphImportance(graph: GraphData, kind: GraphKind): Map<string, NodeImportance> {
  const nodeIds = graph.nodes.map(node => node.id)
  if (!nodeIds.length) return new Map()
  const nodeIdSet = new Set(nodeIds)
  const adjacency = new Map(nodeIds.map(id => [id, new Set<string>()]))
  for (const edge of graph.edges) {
    if (!nodeIdSet.has(edge.source) || !nodeIdSet.has(edge.target) || edge.source === edge.target) continue
    adjacency.get(edge.source)?.add(edge.target)
    adjacency.get(edge.target)?.add(edge.source)
  }

  const degrees = new Map(nodeIds.map(id => [id, adjacency.get(id)?.size || 0]))
  const logDegrees = new Map([...degrees].map(([id, degree]) => [id, Math.log1p(degree)]))
  const normalizedDegrees = normalize(logDegrees)
  const pageRanks = calculatePageRank(nodeIds, adjacency)
  const normalizedPageRanks = normalize(pageRanks)
  const sourceCounts = new Map(graph.nodes.map(node => [
    node.id,
    kind === 'property' ? 1 : new Set(node.source_property_ids || []).size,
  ]))
  const normalizedSources = normalize(new Map(
    [...sourceCounts].map(([id, count]) => [id, Math.log1p(count)]),
  ))

  const scored = graph.nodes.map(node => {
    const degreeScore = normalizedDegrees.get(node.id) || 0
    const score = Math.min(1,
      0.60 * degreeScore
      + 0.25 * (normalizedPageRanks.get(node.id) || 0)
      + 0.15 * (normalizedSources.get(node.id) || 0)
      - genericEntityPenalty(graphNodeLabel(node), kind)
    )
    return {
      id: node.id,
      label: graphNodeLabel(node),
      degree: degrees.get(node.id) || 0,
      pageRank: pageRanks.get(node.id) || 0,
      sourceCount: sourceCounts.get(node.id) || 0,
      score,
      size: Math.round(34 + 44 * degreeScore),
    }
  }).sort((left, right) => (
    right.score - left.score
    || right.degree - left.degree
    || left.label.localeCompare(right.label)
  ))
  const importantCount = Math.max(1, Math.ceil(scored.length * 0.1))

  return new Map(scored.map((row, index) => [row.id, {
    degree: row.degree,
    pageRank: row.pageRank,
    sourceCount: row.sourceCount,
    score: row.score,
    size: row.size,
    important: index < importantCount,
    rank: index + 1,
  }]))
}
