import { describe, expect, it } from 'vitest'
import { buildPropertyTree } from './property-tree'

describe('buildPropertyTree', () => {
  it('groups properties by their persisted directories', () => {
    const tree = buildPropertyTree([
      { id: 'root', filename: 'notes.md', property_type: 'markdown', relative_path: 'properties/notes.md', status: 'active' },
      { id: 'nested', filename: 'guide.md', property_type: 'markdown', relative_path: 'properties/references/guide.md', status: 'active' },
    ])

    expect(tree.properties.map(item => item.id)).toEqual(['root'])
    expect(tree.children).toHaveLength(1)
    expect(tree.children[0].name).toBe('references')
    expect(tree.children[0].properties.map(item => item.id)).toEqual(['nested'])
  })
})
