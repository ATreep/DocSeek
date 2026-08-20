import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useDocSeekTranslation } from '../i18n'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import fcose from 'cytoscape-fcose'
import { CircleDot, Focus, Folder, Maximize2, RefreshCcw, Search, ZoomIn, ZoomOut } from 'lucide-react'
import type { Property } from '../api'
import {
  EMPTY_GRAPH_DISPLAY_SELECTION,
  filterGraphByPropertyIds,
  resolveDisplayPropertyIds,
  type GraphDisplaySelection,
} from '../graph-display-filter'
import {
  graphNodeLabel,
  resolveGraphRelation,
  type GraphData,
  type GraphNode,
  type GraphRelationDetail,
} from '../graph-relations'
import { calculateGraphImportance } from '../graph-importance'
import GraphDisplayFilter, { type GraphImportanceFilter } from './GraphDisplayFilter'

cytoscape.use(fcose as cytoscape.Ext)

const graphStyle = [
  { selector: 'node', style: { 'background-color': '#1e7a5a', label: 'data(label)', color: '#253944', 'font-size': '10px', 'font-weight': 600, 'text-wrap': 'wrap', 'text-max-width': '125px', 'text-valign': 'bottom', 'text-margin-y': '10px', 'text-opacity': 0, width: 'data(size)', height: 'data(size)', 'border-width': '2px', 'border-color': '#ffffff', 'transition-property': 'width, height, border-width, opacity', 'transition-duration': '120ms' } },
  { selector: 'node.entity', style: { 'background-color': '#3b7ca7' } },
  { selector: 'node.group', style: { 'background-color': '#c99b2e', shape: 'round-rectangle', width: 'data(size)', height: '32px', 'border-color': '#fff7dd' } },
  { selector: 'node.important', style: { 'text-opacity': 1, 'border-color': '#b9cdd7', 'border-width': '4px', 'underlay-color': '#3b7ca7', 'underlay-opacity': 0.1, 'underlay-padding': '8px' } },
  { selector: 'node.hovered, node.search-match, node.zoom-label-visible, node.selection-neighbor, node:selected', style: { 'text-opacity': 1 } },
  { selector: 'node.selection-neighbor', style: { 'border-color': '#d2aa48', 'border-width': '3px' } },
  { selector: 'node:selected', style: { 'border-color': '#d2aa48', 'border-width': '4px', 'underlay-color': '#d2aa48', 'underlay-opacity': 0.14, 'underlay-padding': '8px' } },
  { selector: 'edge', style: { width: '1px', opacity: 0.32, 'line-color': '#9cafb1', 'target-arrow-color': '#9cafb1', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', label: 'data(type)', color: '#63767c', 'font-size': '9px', 'text-opacity': 0, 'text-background-color': '#ffffff', 'text-background-opacity': 0, 'text-background-padding': '3px', 'transition-property': 'width, opacity, line-color', 'transition-duration': '120ms' } },
  { selector: 'edge.selection-edge', style: { width: '2.8px', opacity: 1, 'line-color': '#c99b2e', 'target-arrow-color': '#c99b2e', color: '#795d19', 'text-opacity': 1, 'text-background-opacity': 0.94 } },
  { selector: 'edge:selected', style: { width: '3px', opacity: 1, 'line-color': '#d2aa48', 'target-arrow-color': '#d2aa48', color: '#795d19', 'text-opacity': 1, 'text-background-opacity': 0.94 } },
  { selector: '.selection-faded', style: { opacity: 0.14 } },
] as unknown as cytoscape.StylesheetJson

const graphLayout = (randomize = true) => ({
  name: 'fcose' as const,
  quality: 'proof' as const,
  randomize,
  animate: false,
  padding: 80,
  packComponents: true,
  nodeRepulsion: () => 24000,
  idealEdgeLength: () => 220,
  nodeDimensionsIncludeLabels: true,
  nodeSeparation: 120,
  tilingPaddingHorizontal: 120,
  tilingPaddingVertical: 120,
  gravity: 0.12,
  numIter: 1500,
})

function graphContentSignature(graph: GraphData): string {
  const nodes = graph.nodes.map(node => ({
    id: node.id,
    label: graphNodeLabel(node),
    propertyType: node.property_type || '',
    nodeType: node.node_type || '',
    groupPath: node.group_path || '',
    type: node.type || '',
    sourcePropertyIds: [...(node.source_property_ids || [])].sort(),
  })).sort((left, right) => left.id.localeCompare(right.id))
  const edges = graph.edges.map(edge => `${edge.source}\u0000${edge.type}\u0000${edge.target}`).sort()
  return JSON.stringify({ nodes, edges })
}

function isWithinImportanceFilter(
  rank: number,
  important: boolean,
  filter: GraphImportanceFilter,
): boolean {
  if (filter === 'important') return important
  if (filter === 'top20') return rank <= 20
  if (filter === 'top50') return rank <= 50
  return true
}

function updateZoomLabelVisibility(engine: Core) {
  const showOrdinaryLabels = engine.zoom() >= 1.15
  engine.nodes().forEach(node => {
    node.toggleClass('zoom-label-visible', showOrdinaryLabels)
  })
}

function applySelectionEmphasis(engine: Core, selectedNodeId: string | null) {
  const emphasizedNodeIds = new Set(selectedNodeId ? [selectedNodeId] : [])
  engine.edges().forEach(edge => {
    const source = String(edge.data('source'))
    const target = String(edge.data('target'))
    const connected = Boolean(selectedNodeId && (source === selectedNodeId || target === selectedNodeId))
    if (connected) {
      emphasizedNodeIds.add(source)
      emphasizedNodeIds.add(target)
    }
    edge.toggleClass('selection-edge', connected)
    edge.toggleClass('selection-faded', Boolean(selectedNodeId) && !connected)
  })
  engine.nodes().forEach(node => {
    const emphasized = emphasizedNodeIds.has(node.id())
    node.toggleClass('selection-neighbor', Boolean(selectedNodeId) && emphasized && node.id() !== selectedNodeId)
    node.toggleClass('selection-faded', Boolean(selectedNodeId) && !emphasized)
  })
}

type GraphCanvasProps = {
  graph: GraphData
  kind: 'property' | 'entity'
  onSelect: (node: GraphNode) => void
  onRelationSelect?: (relation: GraphRelationDetail) => void
  properties?: Property[]
  displaySelection?: GraphDisplaySelection
  onDisplaySelectionChange?: (selection: GraphDisplaySelection) => void
}

export default function GraphCanvas({
  graph,
  kind,
  onSelect,
  onRelationSelect,
  properties = [],
  displaySelection = EMPTY_GRAPH_DISPLAY_SELECTION,
  onDisplaySelectionChange,
}: GraphCanvasProps) {
  const { t } = useDocSeekTranslation()
  const [filter, setFilter] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [importanceFilter, setImportanceFilter] = useState<GraphImportanceFilter>('all')
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Core | null>(null)
  const graphDataRef = useRef(graph)
  const lastLayoutFilterRef = useRef<{ engine: Core; filterKey: string } | null>(null)
  const onSelectRef = useRef(onSelect)
  const onRelationSelectRef = useRef(onRelationSelect)
  graphDataRef.current = graph
  useEffect(() => { onSelectRef.current = onSelect }, [onSelect])
  useEffect(() => { onRelationSelectRef.current = onRelationSelect }, [onRelationSelect])
  const nodeLabel = useCallback((node: GraphNode) => (
    node.node_type === 'group' && node.group_path === ''
      ? t('Root')
      : graphNodeLabel(node)
  ), [t])
  const graphSignature = graphContentSignature(graph)
  const importance = useMemo(
    () => calculateGraphImportance(graph, kind),
    [graphSignature, kind],
  )
  const displayGraph = useMemo(() => {
    if (!displaySelection.groupPaths.length && !displaySelection.propertyIds.length) return graph
    return filterGraphByPropertyIds(
      graph,
      kind,
      resolveDisplayPropertyIds(properties, displaySelection),
    )
  }, [displaySelection, graph, kind, properties])
  const importanceFilteredNodes = useMemo(() => {
    const filtered = displayGraph.nodes.filter(node => {
      if (kind === 'property' && node.node_type === 'group') return true
      const metric = importance.get(node.id)
      return metric
        ? isWithinImportanceFilter(metric.rank, metric.important, importanceFilter)
        : importanceFilter === 'all'
    })
    if (kind !== 'entity') return filtered
    return [...filtered].sort((left, right) => (
      (importance.get(left.id)?.rank || Number.MAX_SAFE_INTEGER)
      - (importance.get(right.id)?.rank || Number.MAX_SAFE_INTEGER)
    ))
  }, [displayGraph.nodes, importance, importanceFilter, kind])
  const nodes = useMemo(
    () => importanceFilteredNodes.filter(node => nodeLabel(node).toLowerCase().includes(filter.toLowerCase())),
    [importanceFilteredNodes, filter, nodeLabel],
  )
  const visibleEdges = useMemo(() => {
    const visibleIds = new Set(nodes.map(node => node.id))
    return displayGraph.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target))
  }, [displayGraph.edges, nodes])
  const displaySelectionKey = useMemo(() => JSON.stringify({
    groupPaths: [...displaySelection.groupPaths].sort(),
    propertyIds: [...displaySelection.propertyIds].sort(),
  }), [displaySelection.groupPaths, displaySelection.propertyIds])
  const layoutFilterKey = `${displaySelectionKey}:${importanceFilter}`
  const elements = useMemo<ElementDefinition[]>(() => {
    return [
      ...graph.nodes.map(node => {
        const metric = importance.get(node.id)
        return {
          data: {
            id: node.id,
            label: nodeLabel(node),
            property_type: node.property_type || kind,
            degree: metric?.degree || 0,
            importance: metric?.score || 0,
            rank: metric?.rank || graph.nodes.length,
            size: node.node_type === 'group' ? Math.max(metric?.size || 34, 44) : metric?.size || 34,
          },
          classes: `${node.node_type === 'group' ? 'group' : kind}${metric?.important ? ' important' : ''}`,
        }
      }),
      ...graph.edges
        .map((edge, index) => ({ data: { id: `${edge.source}-${edge.target}-${edge.type}-${index}`, source: edge.source, target: edge.target, type: edge.type } })),
    ]
  }, [graphSignature, importance, kind, nodeLabel])

  useEffect(() => {
    if (!canvasRef.current || !graph.nodes.length) return
    const engine = cytoscape({
      container: canvasRef.current,
      elements,
      style: graphStyle,
      layout: graphLayout(),
      minZoom: 0.35,
      maxZoom: 2.3,
    })
    graphRef.current = engine
    engine.on('tap', 'node', event => {
      const selected = graphDataRef.current.nodes.find(node => node.id === event.target.id())
      if (selected) selectNode(selected)
    })
    engine.on('tap', 'edge', event => {
      const edge = {
        source: String(event.target.data('source')),
        target: String(event.target.data('target')),
        type: String(event.target.data('type')),
      }
      const relation = resolveGraphRelation(graphDataRef.current, edge, kind)
      const sourceNode = graphDataRef.current.nodes.find(node => node.id === edge.source)
      const targetNode = graphDataRef.current.nodes.find(node => node.id === edge.target)
      onRelationSelectRef.current?.({
        ...relation,
        sourceLabel: sourceNode ? nodeLabel(sourceNode) : relation.sourceLabel,
        targetLabel: targetNode ? nodeLabel(targetNode) : relation.targetLabel,
      })
    })
    engine.on('tap', event => {
      if (event.target === engine) clearNodeSelection()
    })
    engine.on('mouseover', 'node', event => event.target.addClass('hovered'))
    engine.on('mouseout', 'node', event => event.target.removeClass('hovered'))
    engine.on('zoom', () => updateZoomLabelVisibility(engine))
    updateZoomLabelVisibility(engine)
    return () => {
      engine.destroy()
      if (graphRef.current === engine) graphRef.current = null
    }
  }, [elements, kind, nodeLabel])

  useEffect(() => {
    const engine = graphRef.current
    if (!engine) return
    const visibleIds = new Set(nodes.map(node => node.id))
    const matchingIds = new Set(
      filter.trim()
        ? graph.nodes
          .filter(node => nodeLabel(node).toLowerCase().includes(filter.toLowerCase()))
          .map(node => node.id)
        : [],
    )
    engine.nodes().forEach(node => {
      node.style('display', visibleIds.has(node.id()) ? 'element' : 'none')
      node.toggleClass('search-match', matchingIds.has(node.id()))
    })
    engine.edges().forEach(edge => {
      const source = edge.data('source') as string
      const target = edge.data('target') as string
      edge.style('display', visibleIds.has(source) && visibleIds.has(target) ? 'element' : 'none')
    })
  }, [filter, graph.nodes, nodeLabel, nodes])

  useEffect(() => {
    const engine = graphRef.current
    if (!engine) return
    const visibleNodeIds = new Set(nodes.map(node => node.id))
    applySelectionEmphasis(
      engine,
      selectedNodeId && visibleNodeIds.has(selectedNodeId) ? selectedNodeId : null,
    )
  }, [elements, nodes, selectedNodeId])

  useEffect(() => {
    const engine = graphRef.current
    if (!engine) return
    const previous = lastLayoutFilterRef.current
    if (previous?.engine === engine && previous.filterKey === layoutFilterKey) return
    lastLayoutFilterRef.current = { engine, filterKey: layoutFilterKey }
    const filterActive = Boolean(
      displaySelection.groupPaths.length
      || displaySelection.propertyIds.length
      || importanceFilter !== 'all'
    )
    if (!previous && !filterActive) return
    engine.layout(graphLayout(false)).run()
    if (!filterActive) {
      engine.fit(undefined, 80)
      return
    }
    const displayNodeIds = new Set(nodes.map(node => node.id))
    const visibleNodeElements = engine.nodes().filter(node => displayNodeIds.has(node.id()))
    if (visibleNodeElements.length) engine.fit(visibleNodeElements, 80)
  }, [displaySelection.groupPaths.length, displaySelection.propertyIds.length, elements, importanceFilter, layoutFilterKey, nodes])

  function selectNode(node: GraphNode) {
    setSelectedNodeId(node.id)
    const engine = graphRef.current
    if (engine) {
      engine.nodes().unselect()
      const selected = engine.getElementById(node.id)
      if (selected.nonempty()) selected.select()
    }
    onSelectRef.current(node)
  }

  function clearNodeSelection() {
    setSelectedNodeId(null)
    const engine = graphRef.current
    if (!engine) return
    engine.nodes().unselect()
    applySelectionEmphasis(engine, null)
  }

  function fitAll() {
    graphRef.current?.fit(undefined, 80)
    setFilter('')
    setSearchOpen(false)
  }

  function adjustZoom(delta: number) {
    const engine = graphRef.current
    if (!engine) return
    engine.zoom(Math.max(0.35, Math.min(2.3, engine.zoom() + delta)))
  }

  function focusFirstNode() {
    const node = nodes[0]
    if (!node) return
    selectNode(node)
    const element = graphRef.current?.getElementById(node.id)
    if (element?.nonempty()) graphRef.current?.animate({ fit: { eles: element, padding: 96 }, duration: 180 })
  }

  function resetLayout() {
    const engine = graphRef.current
    clearNodeSelection()
    engine?.layout(graphLayout()).run()
    fitAll()
  }

  return <section className="graph-panel">
    <div className="panel-toolbar"><div><strong>{t(kind === 'property' ? 'Property Graph' : 'Entity Graph')}</strong><span className="count-label">{nodes.length} {t('of')} {graph.nodes.length} {t('nodes')} · {visibleEdges.length} {t('of')} {graph.edges.length} {t('relations')}</span></div><div className="icon-actions">{searchOpen && <input aria-label={t('Graph filter')} placeholder={t('Filter graph nodes')} value={filter} onChange={event => setFilter(event.target.value)} autoFocus />}<button type="button" aria-label={t('Search graph')} title={t('Search graph')} onClick={() => setSearchOpen(open => !open)}><Search size={15} /></button><button type="button" aria-label={t('Zoom out')} title={t('Zoom out')} onClick={() => adjustZoom(-0.2)}><ZoomOut size={15} /></button><button type="button" aria-label={t('Zoom in')} title={t('Zoom in')} onClick={() => adjustZoom(0.2)}><ZoomIn size={15} /></button><button type="button" aria-label={t('Fit all')} title={t('Fit all')} onClick={fitAll}><Maximize2 size={15} /></button><button type="button" aria-label={t('Focus selection')} title={t('Focus selection')} onClick={focusFirstNode}><Focus size={15} /></button><button type="button" aria-label={t('Reset layout')} title={t('Reset layout')} onClick={resetLayout}><RefreshCcw size={15} /></button><GraphDisplayFilter properties={properties} selection={displaySelection} onChange={onDisplaySelectionChange} importanceFilter={importanceFilter} onImportanceFilterChange={setImportanceFilter} /></div></div>
    {graph.nodes.length ? <div className="graph-body"><nav className="graph-index" aria-label={`${t(kind === 'property' ? 'Property' : 'Entity')} ${t('nodes')}`}><div className="graph-index-heading">{t(kind === 'property' ? 'Property tree nodes' : 'Entities')}<span>{nodes.length}</span></div><ul className="graph-index-list">{nodes.map(node => { const degree = importance.get(node.id)?.degree || 0; const isGroup = node.node_type === 'group'; return <li key={node.id}><button type="button" aria-label={nodeLabel(node)} className={`graph-list-item ${isGroup ? 'group' : kind} ${selectedNodeId === node.id ? 'selected' : ''}`} onClick={() => selectNode(node)}>{isGroup ? <Folder size={14} /> : <CircleDot size={14} />}<span>{nodeLabel(node)}</span><small>{kind === 'entity' ? `${degree} ${t(degree === 1 ? 'connection' : 'connections')}` : isGroup ? t('Group') : node.property_type || node.type || t(kind)}</small></button></li> })}</ul></nav><div className="graph-visual"><div ref={canvasRef} className="graph-canvas" role="application" aria-label={`${t('Interactive')} ${t(kind)} ${t('node-edge graph')}`} />{!nodes.length && <div className="graph-filter-empty"><CircleDot size={18} /> {t('No matching nodes')}</div>}</div></div> : <div className="empty-state"><CircleDot size={28} /><strong>{t('Your graph is waiting')}</strong><span>{t('Upload a property to create the first candidate snapshot.')}</span></div>}
  </section>
}
