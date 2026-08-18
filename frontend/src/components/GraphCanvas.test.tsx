import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import GraphCanvas from './GraphCanvas'

const graphEngine = vi.hoisted(() => {
  const selectedElement = { animate: vi.fn(), nonempty: () => true, select: vi.fn() }
  const visibleNodeCollection = { length: 1 }
  const nodeCollection = { forEach: vi.fn(), filter: vi.fn(() => visibleNodeCollection), unselect: vi.fn() }
  const edgeCollection = { forEach: vi.fn() }
  const layoutRun = vi.fn()
  const instance = {
    destroy: vi.fn(),
    fit: vi.fn(),
    getElementById: vi.fn(() => selectedElement),
    layout: vi.fn(() => ({ run: layoutRun })),
    nodes: vi.fn(() => nodeCollection),
    edges: vi.fn(() => edgeCollection),
    on: vi.fn(),
    zoom: vi.fn(() => 1),
  }
  const use = vi.fn()
  const cytoscape = Object.assign(vi.fn(() => instance), { use })
  return { cytoscape, use, instance, selectedElement, nodeCollection, visibleNodeCollection, edgeCollection, layoutRun }
})

vi.mock('cytoscape', () => ({ default: graphEngine.cytoscape }))
vi.mock('cytoscape-fcose', () => ({ default: {} }))

afterEach(() => {
  cleanup()
  graphEngine.cytoscape.mockClear()
  graphEngine.instance.fit.mockClear()
  graphEngine.instance.getElementById.mockClear()
  graphEngine.instance.layout.mockClear()
  graphEngine.instance.nodes.mockClear()
  graphEngine.instance.edges.mockClear()
  graphEngine.instance.on.mockClear()
  graphEngine.selectedElement.select.mockClear()
  graphEngine.nodeCollection.unselect.mockClear()
  graphEngine.nodeCollection.filter.mockClear()
  graphEngine.nodeCollection.forEach.mockReset()
  graphEngine.edgeCollection.forEach.mockReset()
  graphEngine.layoutRun.mockClear()
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

  it('scales nodes by importance and progressively reveals labels and relations', async () => {
    render(<GraphCanvas
      kind="entity"
      graph={{
        nodes: [
          { id: 'leaf-one', name: 'Neo4j', source_property_ids: ['p1'] },
          { id: 'hub', name: 'Atlas', source_property_ids: ['p1', 'p2', 'p3'] },
          { id: 'leaf-two', name: 'LangGraph', source_property_ids: ['p2'] },
          { id: 'leaf-three', name: 'SQLite', source_property_ids: ['p3'] },
          { id: 'isolated', name: 'Orion', source_property_ids: ['p4'] },
        ],
        edges: [
          { source: 'hub', target: 'leaf-one', type: 'USES' },
          { source: 'hub', target: 'leaf-two', type: 'USES' },
          { source: 'hub', target: 'leaf-three', type: 'USES' },
        ],
      }}
      onSelect={() => undefined}
    />)
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalled())

    const calls = graphEngine.cytoscape.mock.calls as unknown as Array<[Record<string, any>]>
    const options = calls[calls.length - 1]?.[0]
    const hub = options?.elements.find((element: any) => element.data.id === 'hub')
    const isolated = options?.elements.find((element: any) => element.data.id === 'isolated')
    expect(hub).toMatchObject({ data: { degree: 3, size: 78 }, classes: expect.stringContaining('important') })
    expect(isolated).toMatchObject({ data: { degree: 0, size: 34 } })
    expect(options?.style).toEqual(expect.arrayContaining([
      expect.objectContaining({ selector: 'node', style: expect.objectContaining({ width: 'data(size)', 'text-opacity': 0 }) }),
      expect.objectContaining({ selector: 'node.important', style: expect.objectContaining({ 'text-opacity': 1 }) }),
      expect.objectContaining({ selector: 'edge', style: expect.objectContaining({ 'text-opacity': 0 }) }),
      expect.objectContaining({ selector: 'edge.selection-edge', style: expect.objectContaining({ 'text-opacity': 1 }) }),
    ]))
  })

  it('uses generous spacing so labeled nodes and edges remain legible', async () => {
    render(<GraphCanvas kind="entity" graph={{ nodes: [{ id: 'one', name: 'One' }, { id: 'two', name: 'Two' }], edges: [{ source: 'one', target: 'two', type: 'RELATED' }] }} onSelect={() => undefined} />)
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalled())

    const options = (graphEngine.cytoscape.mock.calls as unknown as Array<[Record<string, any>]>)[graphEngine.cytoscape.mock.calls.length - 1]?.[0]
    if (!options) throw new Error('Cytoscape options were not captured')
    expect(options.layout).toMatchObject({
      name: 'fcose',
      animate: false,
      padding: 80,
      packComponents: true,
      nodeDimensionsIncludeLabels: true,
    })
    expect(options.layout.nodeRepulsion()).toBeGreaterThanOrEqual(22000)
    expect(options.layout.idealEdgeLength()).toBeGreaterThanOrEqual(220)
    expect(options.layout.nodeSeparation).toBeGreaterThanOrEqual(100)
    expect(options.layout.tilingPaddingHorizontal).toBeGreaterThanOrEqual(100)
    expect(options.layout.tilingPaddingVertical).toBeGreaterThanOrEqual(100)
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

  it('sorts entity nodes by importance, shows connection counts, and filters important nodes', async () => {
    const user = userEvent.setup()
    render(<GraphCanvas
      kind="entity"
      graph={{
        nodes: [
          { id: 'leaf-one', name: 'Neo4j' },
          { id: 'leaf-two', name: 'LangGraph' },
          { id: 'hub', name: 'Atlas' },
          { id: 'leaf-three', name: 'SQLite' },
          { id: 'isolated', name: 'Orion' },
        ],
        edges: [
          { source: 'hub', target: 'leaf-one', type: 'USES' },
          { source: 'hub', target: 'leaf-two', type: 'USES' },
          { source: 'hub', target: 'leaf-three', type: 'USES' },
        ],
      }}
      onSelect={() => undefined}
    />)

    const index = screen.getByRole('navigation', { name: 'Entity nodes' })
    expect(within(index).getAllByRole('button')[0].getAttribute('aria-label')).toBe('Atlas')
    expect(within(index).getByText('3 connections')).toBeTruthy()
    expect(screen.queryByRole('combobox', { name: 'Importance filter' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Filter graph display' }))
    const importanceFilter = screen.getByRole('combobox', { name: 'Importance filter' })
    expect(within(importanceFilter).getByRole('option', { name: 'Top 20' })).toBeTruthy()
    expect(within(importanceFilter).getByRole('option', { name: 'Top 50' })).toBeTruthy()

    await user.selectOptions(importanceFilter, 'important')

    expect(within(index).getByRole('button', { name: 'Atlas' })).toBeTruthy()
    expect(within(index).queryByRole('button', { name: 'Neo4j' })).toBeNull()
    expect(graphEngine.instance.layout).toHaveBeenCalled()
  })

  it('does not re-run layout when selecting a node', async () => {
    render(<GraphCanvas
      kind="entity"
      graph={{
        nodes: [{ id: 'atlas', name: 'Atlas' }, { id: 'neo4j', name: 'Neo4j' }],
        edges: [{ source: 'atlas', target: 'neo4j', type: 'USES' }],
      }}
      onSelect={() => undefined}
    />)
    graphEngine.instance.layout.mockClear()

    await userEvent.setup().click(screen.getByRole('button', { name: 'Atlas' }))

    expect(graphEngine.instance.layout).not.toHaveBeenCalled()
  })

  it('emphasizes the selected node and its one-hop neighborhood while fading unrelated elements', async () => {
    const nodeElements = ['one', 'two', 'three'].map(id => ({
      id: () => id,
      style: vi.fn(),
      toggleClass: vi.fn(),
    }))
    const edgeElements = [
      { source: 'one', target: 'two' },
      { source: 'two', target: 'three' },
    ].map(data => ({
      data: (key: 'source' | 'target') => data[key],
      style: vi.fn(),
      toggleClass: vi.fn(),
    }))
    graphEngine.nodeCollection.forEach.mockImplementation(callback => nodeElements.forEach(callback))
    graphEngine.edgeCollection.forEach.mockImplementation(callback => edgeElements.forEach(callback))

    render(<GraphCanvas
      kind="property"
      graph={{
        nodes: [
          { id: 'one', filename: 'one.md' },
          { id: 'two', filename: 'two.md' },
          { id: 'three', filename: 'three.md' },
        ],
        edges: [
          { source: 'one', target: 'two', type: 'LINKS_TO' },
          { source: 'two', target: 'three', type: 'DEPENDS_ON' },
        ],
      }}
      onSelect={() => undefined}
    />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'one.md' }))

    expect(nodeElements[0].toggleClass).toHaveBeenCalledWith('selection-faded', false)
    expect(nodeElements[0].toggleClass).toHaveBeenCalledWith('selection-neighbor', false)
    expect(nodeElements[1].toggleClass).toHaveBeenCalledWith('selection-faded', false)
    expect(nodeElements[1].toggleClass).toHaveBeenCalledWith('selection-neighbor', true)
    expect(nodeElements[2].toggleClass).toHaveBeenCalledWith('selection-faded', true)
    expect(edgeElements[0].toggleClass).toHaveBeenCalledWith('selection-edge', true)
    expect(edgeElements[0].toggleClass).toHaveBeenCalledWith('selection-faded', false)
    expect(edgeElements[1].toggleClass).toHaveBeenCalledWith('selection-edge', false)
    expect(edgeElements[1].toggleClass).toHaveBeenCalledWith('selection-faded', true)

    const cytoscapeCalls = graphEngine.cytoscape.mock.calls as unknown as Array<[Record<string, any>]>
    const options = cytoscapeCalls[cytoscapeCalls.length - 1]?.[0]
    expect(options?.style).toEqual(expect.arrayContaining([
      expect.objectContaining({ selector: 'node.selection-neighbor' }),
      expect.objectContaining({ selector: 'edge.selection-edge' }),
      expect.objectContaining({ selector: '.selection-faded' }),
    ]))
  })

  it('clears node emphasis when the blank graph canvas is tapped', async () => {
    const nodeElements = ['one', 'two'].map(id => ({
      id: () => id,
      style: vi.fn(),
      toggleClass: vi.fn(),
    }))
    const edgeElement = {
      data: (key: 'source' | 'target') => ({ source: 'one', target: 'two' })[key],
      style: vi.fn(),
      toggleClass: vi.fn(),
    }
    graphEngine.nodeCollection.forEach.mockImplementation(callback => nodeElements.forEach(callback))
    graphEngine.edgeCollection.forEach.mockImplementation(callback => [edgeElement].forEach(callback))

    render(<GraphCanvas
      kind="property"
      graph={{
        nodes: [{ id: 'one', filename: 'one.md' }, { id: 'two', filename: 'two.md' }],
        edges: [{ source: 'one', target: 'two', type: 'LINKS_TO' }],
      }}
      onSelect={() => undefined}
    />)
    const item = screen.getByRole('button', { name: 'one.md' })
    await userEvent.setup().click(item)
    nodeElements.forEach(node => node.toggleClass.mockClear())
    edgeElement.toggleClass.mockClear()
    graphEngine.nodeCollection.unselect.mockClear()

    const backgroundTap = graphEngine.instance.on.mock.calls.find(call => call[0] === 'tap' && call.length === 2)?.[1] as ((event: { target: unknown }) => void) | undefined
    expect(backgroundTap).toBeTypeOf('function')
    await act(async () => backgroundTap?.({ target: graphEngine.instance }))

    expect(item.classList.contains('selected')).toBe(false)
    expect(graphEngine.nodeCollection.unselect).toHaveBeenCalled()
    expect(nodeElements[0].toggleClass).toHaveBeenCalledWith('selection-neighbor', false)
    expect(nodeElements[0].toggleClass).toHaveBeenCalledWith('selection-faded', false)
    expect(edgeElement.toggleClass).toHaveBeenCalledWith('selection-edge', false)
    expect(edgeElement.toggleClass).toHaveBeenCalledWith('selection-faded', false)
  })

  it('clears node emphasis when resetting the graph layout', async () => {
    const nodeElements = ['one', 'two'].map(id => ({
      id: () => id,
      style: vi.fn(),
      toggleClass: vi.fn(),
    }))
    const edgeElement = {
      data: (key: 'source' | 'target') => ({ source: 'one', target: 'two' })[key],
      style: vi.fn(),
      toggleClass: vi.fn(),
    }
    graphEngine.nodeCollection.forEach.mockImplementation(callback => nodeElements.forEach(callback))
    graphEngine.edgeCollection.forEach.mockImplementation(callback => [edgeElement].forEach(callback))

    render(<GraphCanvas
      kind="entity"
      graph={{
        nodes: [{ id: 'one', name: 'One' }, { id: 'two', name: 'Two' }],
        edges: [{ source: 'one', target: 'two', type: 'LINKS_TO' }],
      }}
      onSelect={() => undefined}
    />)
    const item = screen.getByRole('button', { name: 'One' })
    await userEvent.setup().click(item)
    nodeElements.forEach(node => node.toggleClass.mockClear())
    edgeElement.toggleClass.mockClear()
    graphEngine.nodeCollection.unselect.mockClear()

    await userEvent.setup().click(screen.getByRole('button', { name: 'Reset layout' }))

    expect(item.classList.contains('selected')).toBe(false)
    expect(graphEngine.nodeCollection.unselect).toHaveBeenCalled()
    expect(nodeElements[1].toggleClass).toHaveBeenCalledWith('selection-neighbor', false)
    expect(nodeElements[1].toggleClass).toHaveBeenCalledWith('selection-faded', false)
    expect(edgeElement.toggleClass).toHaveBeenCalledWith('selection-edge', false)
    expect(edgeElement.toggleClass).toHaveBeenCalledWith('selection-faded', false)
    expect(graphEngine.layoutRun).toHaveBeenCalled()
  })

  it('marks the node index as a scrollable list', () => {
    render(<GraphCanvas kind="entity" graph={{ nodes: [{ id: 'one', name: 'One' }], edges: [] }} onSelect={() => undefined} />)
    expect(screen.getByRole('list').classList.contains('graph-index-list')).toBe(true)
  })

  it('anchors the display filter at the trailing edge of the graph toolbar', () => {
    const properties = [
      { id: 'one', filename: 'one.md', relative_path: 'properties/Group 1/one.md' },
    ] as any[]
    render(<GraphCanvas
      kind="property"
      graph={{ nodes: [{ id: 'one', filename: 'one.md' }], edges: [] }}
      properties={properties}
      displaySelection={{ groupPaths: [], propertyIds: [] }}
      onDisplaySelectionChange={() => undefined}
      onSelect={() => undefined}
    />)

    const filterButton = screen.getByRole('button', { name: 'Filter graph display' })
    const filterWrapper = filterButton.closest('.graph-display-filter')
    const toolbarActions = filterButton.closest('.icon-actions')

    expect(toolbarActions?.lastElementChild).toBe(filterWrapper)
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

  it('preserves the graph engine when equivalent graph data is received', async () => {
    const firstGraph = {
      nodes: [{ id: 'one', name: 'One' }, { id: 'two', name: 'Two' }],
      edges: [{ source: 'one', target: 'two', type: 'RELATED' }],
    }
    const { rerender } = render(<GraphCanvas kind="entity" graph={firstGraph} onSelect={() => undefined} />)
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalledTimes(1))

    rerender(<GraphCanvas
      kind="entity"
      graph={{
        nodes: firstGraph.nodes.map(node => ({ ...node })),
        edges: firstGraph.edges.map(edge => ({ ...edge })),
      }}
      onSelect={() => undefined}
    />)

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

  it('filters entity nodes by the union of selected source properties', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: [
        { id: 'entity-one', name: 'Entity One', source_property_ids: ['one'] },
        { id: 'entity-two', name: 'Entity Two', source_property_ids: ['two'] },
      ],
      edges: [{ source: 'entity-one', target: 'entity-two', type: 'RELATED' }],
    }
    const properties = [
      { id: 'one', filename: 'one.md', relative_path: 'properties/Group 1/one.md' },
      { id: 'two', filename: 'two.md', relative_path: 'properties/Group 2/two.md' },
    ] as any[]

    render(<GraphCanvas
      kind="entity"
      graph={graph}
      properties={properties}
      displaySelection={{ groupPaths: ['Group 1'], propertyIds: [] }}
      onDisplaySelectionChange={() => undefined}
      onSelect={() => undefined}
    />)

    expect(screen.getByRole('button', { name: 'Entity One' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Entity Two' })).toBeNull()
    expect(screen.getByText('1 of 2 nodes · 0 of 1 relations')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Filter graph display' }))
    expect((screen.getByRole('checkbox', { name: 'Group 1' }) as HTMLInputElement).checked).toBe(true)
  })

  it('does not recreate Cytoscape when the display selection changes', async () => {
    const user = userEvent.setup()
    const graph = { nodes: [{ id: 'one', filename: 'one.md' }, { id: 'two', filename: 'two.md' }], edges: [] }
    const properties = [
      { id: 'one', filename: 'one.md', relative_path: 'properties/Group 1/one.md' },
      { id: 'two', filename: 'two.md', relative_path: 'properties/Group 2/two.md' },
    ] as any[]

    function ControlledCanvas() {
      const [selection, setSelection] = useState({ groupPaths: [] as string[], propertyIds: [] as string[] })
      return <GraphCanvas
        kind="property"
        graph={graph}
        properties={properties}
        displaySelection={selection}
        onDisplaySelectionChange={setSelection}
        onSelect={() => undefined}
      />
    }

    render(<ControlledCanvas />)
    await waitFor(() => expect(graphEngine.cytoscape).toHaveBeenCalledTimes(1))
    await user.click(screen.getByRole('button', { name: 'Filter graph display' }))
    await user.click(screen.getByRole('checkbox', { name: 'Group 1' }))

    expect(graphEngine.cytoscape).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'two.md' })).toBeNull()
    expect(graphEngine.instance.fit).toHaveBeenCalledWith(graphEngine.visibleNodeCollection, 80)
  })
})
