import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import GraphCanvas from './GraphCanvas'

const graphEngine = vi.hoisted(() => {
  const selectedElement = { animate: vi.fn(), nonempty: () => true, select: vi.fn() }
  const nodeCollection = { forEach: vi.fn(), unselect: vi.fn() }
  const edgeCollection = { forEach: vi.fn() }
  const instance = {
    destroy: vi.fn(),
    fit: vi.fn(),
    getElementById: vi.fn(() => selectedElement),
    nodes: vi.fn(() => nodeCollection),
    edges: vi.fn(() => edgeCollection),
    on: vi.fn(),
    zoom: vi.fn(() => 1),
  }
  return { cytoscape: vi.fn(() => instance), instance, selectedElement, nodeCollection, edgeCollection }
})

vi.mock('cytoscape', () => ({ default: graphEngine.cytoscape }))

afterEach(() => {
  cleanup()
  graphEngine.cytoscape.mockClear()
  graphEngine.instance.fit.mockClear()
  graphEngine.instance.getElementById.mockClear()
  graphEngine.instance.nodes.mockClear()
  graphEngine.instance.edges.mockClear()
  graphEngine.instance.on.mockClear()
  graphEngine.selectedElement.select.mockClear()
  graphEngine.nodeCollection.unselect.mockClear()
})

describe('GraphCanvas controls', () => {
  it('renders nodes and edges through Cytoscape', async () => {
    render(<GraphCanvas kind="property" graph={{ nodes: [{ id: 'one', filename: 'one.md' }, { id: 'two', filename: 'two.md' }], edges: [{ source: 'one', target: 'two', type: 'RELATED' }] }} onSelect={() => undefined} />)
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalled())
    const options = (graphEngine.cytoscape.mock.calls as unknown as Array<[Record<string, any>]>)[graphEngine.cytoscape.mock.calls.length - 1]?.[0]
    if (!options) throw new Error('Cytoscape options were not captured')
    expect(options.container).toBeInstanceOf(HTMLDivElement)
    expect(options.elements).toEqual(expect.arrayContaining([
      expect.objectContaining({ data: expect.objectContaining({ id: 'one', label: 'one.md' }) }),
      expect.objectContaining({ data: expect.objectContaining({ id: 'two', label: 'two.md' }) }),
      expect.objectContaining({ data: expect.objectContaining({ source: 'one', target: 'two', type: 'RELATED' }) }),
    ]))
  })

  it('uses generous spacing so labeled nodes and edges remain legible', async () => {
    render(<GraphCanvas kind="entity" graph={{ nodes: [{ id: 'one', name: 'One' }, { id: 'two', name: 'Two' }], edges: [{ source: 'one', target: 'two', type: 'RELATED' }] }} onSelect={() => undefined} />)
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalled())

    const options = (graphEngine.cytoscape.mock.calls as unknown as Array<[Record<string, any>]>)[graphEngine.cytoscape.mock.calls.length - 1]?.[0]
    if (!options) throw new Error('Cytoscape options were not captured')
    expect(options.layout).toMatchObject({ name: 'cose', padding: 80 })
    expect(options.layout.nodeRepulsion()).toBeGreaterThanOrEqual(22000)
    expect(options.layout.idealEdgeLength()).toBeGreaterThanOrEqual(220)
    expect(options.layout.componentSpacing).toBeGreaterThanOrEqual(260)
    expect(options.layout.nodeDimensionsIncludeLabels).toBe(true)
  })

  it('lists every graph node and synchronizes list selection with Cytoscape', async () => {
    const onSelect = vi.fn()
    const graph = { nodes: [{ id: 'one', filename: 'one.md' }, { id: 'two', filename: 'two.md' }], edges: [] }
    render(<GraphCanvas kind="property" graph={graph} onSelect={onSelect} />)
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalled())
    expect(screen.getByRole('navigation', { name: 'Property nodes' })).toBeTruthy()
    const item = screen.getByRole('button', { name: 'two.md' })
    await userEvent.setup().click(item)
    expect(onSelect).toHaveBeenCalledWith(graph.nodes[1])
    expect(graphEngine.nodeCollection.unselect).toHaveBeenCalled()
    expect(graphEngine.selectedElement.select).toHaveBeenCalled()
    expect(item.classList.contains('selected')).toBe(true)
  })

  it('marks the node index as a scrollable list', () => {
    render(<GraphCanvas kind="entity" graph={{ nodes: [{ id: 'one', name: 'One' }], edges: [] }} onSelect={() => undefined} />)
    expect(screen.getByRole('list').classList.contains('graph-index-list')).toBe(true)
  })

  it('filters the existing Cytoscape graph and fits the canvas', async () => {
    const user = userEvent.setup()
    render(<GraphCanvas kind="property" graph={{ nodes: [{ id: 'one', filename: 'one.md' }, { id: 'two', filename: 'two.md' }], edges: [] }} onSelect={() => undefined} />)
    await user.click(screen.getByRole('button', { name: 'Search graph' }))
    await user.type(screen.getByPlaceholderText('Filter graph nodes'), 'one')
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalledTimes(1))
    await user.click(screen.getByRole('button', { name: 'Fit all' }))
    expect(graphEngine.instance.fit).toHaveBeenCalled()
  })

  it('does not duplicate relationship records beneath the visual graph', () => {
    render(<GraphCanvas kind="property" graph={{ nodes: [{ id: 'one', filename: 'one.md' }, { id: 'two', filename: 'two.md' }], edges: [{ source: 'one', target: 'two', type: 'RELATED' }] }} onSelect={() => undefined} />)
    expect(screen.queryByLabelText('Graph relationships')).toBeNull()
  })

  it('resolves and selects a tapped Cytoscape edge', async () => {
    const onRelationSelect = vi.fn()
    const graph = {
      nodes: [{ id: 'atlas', name: 'Atlas' }, { id: 'neo4j', name: 'Neo4j' }],
      edges: [{ source: 'atlas', target: 'neo4j', type: 'USES' }],
    }
    render(<GraphCanvas kind="entity" graph={graph} onSelect={() => undefined} onRelationSelect={onRelationSelect} />)
    await waitFor(() => expect(graphEngine.instance.on).toHaveBeenCalled())
    const edgeTap = graphEngine.instance.on.mock.calls.find(call => call[1] === 'edge')?.[2] as ((event: { target: { data: (key: string) => string } }) => void) | undefined
    const edgeData = graph.edges[0]

    edgeTap?.({ target: { data: (key: string) => edgeData[key as keyof typeof edgeData] } })

    expect(edgeTap).toBeTypeOf('function')
    expect(onRelationSelect).toHaveBeenCalledWith({
      ...edgeData,
      kind: 'entity',
      sourceLabel: 'Atlas',
      targetLabel: 'Neo4j',
    })
  })

  it('does not recreate the graph when only the parent callback identity changes', async () => {
    const graph = { nodes: [{ id: 'one', filename: 'one.md' }], edges: [] }
    const { rerender } = render(<GraphCanvas kind="property" graph={graph} onSelect={() => undefined} />)
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalledTimes(1))
    rerender(<GraphCanvas kind="property" graph={graph} onSelect={() => undefined} />)
    expect(graphEngine.cytoscape).toHaveBeenCalledTimes(1)
  })

  it('does not rebuild the graph while filtering and clearing the search', async () => {
    const user = userEvent.setup()
    const graph = { nodes: [{ id: 'one', filename: 'one.md' }, { id: 'two', filename: 'two.md' }], edges: [] }
    render(<GraphCanvas kind="property" graph={graph} onSelect={() => undefined} />)
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalledTimes(1))
    await user.click(screen.getByRole('button', { name: 'Search graph' }))
    const input = screen.getByPlaceholderText('Filter graph nodes')
    await user.type(input, 'one')
    await user.clear(input)
    expect(graphEngine.cytoscape).toHaveBeenCalledTimes(1)
  })
})
