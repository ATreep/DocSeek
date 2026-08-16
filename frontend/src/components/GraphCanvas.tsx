import { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import { CircleDot, Focus, Maximize2, RefreshCcw, Search, ZoomIn, ZoomOut } from 'lucide-react'
import {
  graphNodeLabel,
  resolveGraphRelation,
  type GraphData,
  type GraphNode,
  type GraphRelationDetail,
} from '../graph-relations'

const graphStyle = [
  { selector: 'node', style: { 'background-color': '#1e7a5a', label: 'data(label)', color: '#253944', 'font-size': '10px', 'font-weight': 600, 'text-wrap': 'wrap', 'text-max-width': '125px', 'text-valign': 'bottom', 'text-margin-y': '10px', width: '40px', height: '40px', 'border-width': '2px', 'border-color': '#ffffff' } },
  { selector: 'node.entity', style: { 'background-color': '#3b7ca7' } },
  { selector: 'node:selected', style: { 'border-color': '#d2aa48', 'border-width': '4px' } },
  { selector: 'edge', style: { width: '1.6px', 'line-color': '#9cafb1', 'target-arrow-color': '#9cafb1', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', label: 'data(type)', color: '#63767c', 'font-size': '9px', 'text-background-color': '#ffffff', 'text-background-opacity': 0.94, 'text-background-padding': '3px' } },
  { selector: 'edge:selected', style: { width: '3px', 'line-color': '#d2aa48', 'target-arrow-color': '#d2aa48', color: '#795d19' } },
] as unknown as cytoscape.StylesheetJson

const graphLayout = () => ({
  name: 'cose' as const,
  animate: false,
  padding: 80,
  nodeRepulsion: () => 24000,
  idealEdgeLength: () => 220,
  nodeOverlap: 48,
  nodeDimensionsIncludeLabels: true,
  componentSpacing: 260,
  gravity: 0.12,
  numIter: 1500,
})

type GraphCanvasProps = {
  graph: GraphData
  kind: 'property' | 'entity'
  onSelect: (node: GraphNode) => void
  onRelationSelect?: (relation: GraphRelationDetail) => void
}

export default function GraphCanvas({ graph, kind, onSelect, onRelationSelect }: GraphCanvasProps) {
  const [filter, setFilter] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Core | null>(null)
  const onSelectRef = useRef(onSelect)
  const onRelationSelectRef = useRef(onRelationSelect)
  useEffect(() => { onSelectRef.current = onSelect }, [onSelect])
  useEffect(() => { onRelationSelectRef.current = onRelationSelect }, [onRelationSelect])
  const nodes = useMemo(() => graph.nodes.filter(node => graphNodeLabel(node).toLowerCase().includes(filter.toLowerCase())), [filter, graph.nodes])
  const elements = useMemo<ElementDefinition[]>(() => {
    return [
      ...graph.nodes.map(node => ({ data: { id: node.id, label: graphNodeLabel(node), property_type: node.property_type || kind }, classes: kind })),
      ...graph.edges
        .map((edge, index) => ({ data: { id: `${edge.source}-${edge.target}-${edge.type}-${index}`, source: edge.source, target: edge.target, type: edge.type } })),
    ]
  }, [graph.edges, graph.nodes, kind])

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
      const selected = graph.nodes.find(node => node.id === event.target.id())
      if (selected) selectNode(selected)
    })
    engine.on('tap', 'edge', event => {
      const edge = {
        source: String(event.target.data('source')),
        target: String(event.target.data('target')),
        type: String(event.target.data('type')),
      }
      onRelationSelectRef.current?.(resolveGraphRelation(graph, edge, kind))
    })
    return () => {
      engine.destroy()
      if (graphRef.current === engine) graphRef.current = null
    }
  }, [elements, graph.nodes])

  useEffect(() => {
    const engine = graphRef.current
    if (!engine) return
    const visibleIds = new Set(nodes.map(node => node.id))
    engine.nodes().forEach(node => { node.style('display', visibleIds.has(node.id()) ? 'element' : 'none') })
    engine.edges().forEach(edge => {
      const source = edge.data('source') as string
      const target = edge.data('target') as string
      edge.style('display', visibleIds.has(source) && visibleIds.has(target) ? 'element' : 'none')
    })
  }, [nodes])

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
    engine?.layout(graphLayout()).run()
    fitAll()
  }

  return <section className="graph-panel">
    <div className="panel-toolbar"><div><strong>{kind === 'property' ? 'Property Graph' : 'Entity Graph'}</strong><span className="count-label">{nodes.length} of {graph.nodes.length} nodes · {graph.edges.length} relations</span></div><div className="icon-actions">{searchOpen && <input aria-label="Graph filter" placeholder="Filter graph nodes" value={filter} onChange={event => setFilter(event.target.value)} autoFocus />}<button type="button" aria-label="Search graph" title="Search graph" onClick={() => setSearchOpen(open => !open)}><Search size={15} /></button><button type="button" aria-label="Zoom out" title="Zoom out" onClick={() => adjustZoom(-0.2)}><ZoomOut size={15} /></button><button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => adjustZoom(0.2)}><ZoomIn size={15} /></button><button type="button" aria-label="Fit all" title="Fit all" onClick={fitAll}><Maximize2 size={15} /></button><button type="button" aria-label="Focus selection" title="Focus selection" onClick={focusFirstNode}><Focus size={15} /></button><button type="button" aria-label="Reset layout" title="Reset layout" onClick={resetLayout}><RefreshCcw size={15} /></button></div></div>
    {graph.nodes.length ? <div className="graph-body"><nav className="graph-index" aria-label={`${kind === 'property' ? 'Property' : 'Entity'} nodes`}><div className="graph-index-heading">{kind === 'property' ? 'Properties' : 'Entities'}<span>{graph.nodes.length}</span></div><ul className="graph-index-list">{graph.nodes.map(node => <li key={node.id}><button type="button" aria-label={graphNodeLabel(node)} className={`graph-list-item ${kind} ${selectedNodeId === node.id ? 'selected' : ''}`} onClick={() => selectNode(node)}><CircleDot size={14} /><span>{graphNodeLabel(node)}</span><small>{node.property_type || node.type || kind}</small></button></li>)}</ul></nav><div className="graph-visual"><div ref={canvasRef} className="graph-canvas" role="application" aria-label={`Interactive ${kind} node-edge graph`} />{!nodes.length && <div className="graph-filter-empty"><CircleDot size={18} /> No matching nodes</div>}</div></div> : <div className="empty-state"><CircleDot size={28} /><strong>Your graph is waiting</strong><span>Upload a property to create the first candidate snapshot.</span></div>}
  </section>
}
