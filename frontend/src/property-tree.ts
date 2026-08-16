import { Property } from './api'

export type PropertyTreeNode = {
  name: string
  path: string
  properties: Property[]
  children: PropertyTreeNode[]
}

export function buildPropertyTree(properties: Property[]): PropertyTreeNode {
  const root: PropertyTreeNode = { name: '', path: '', properties: [], children: [] }
  for (const property of properties) {
    const directories = property.relative_path.split('/').slice(1, -1).filter(Boolean)
    let current = root
    for (const directory of directories) {
      const path = current.path ? `${current.path}/${directory}` : directory
      let child = current.children.find(item => item.name === directory)
      if (!child) {
        child = { name: directory, path, properties: [], children: [] }
        current.children.push(child)
      }
      current = child
    }
    current.properties.push(property)
  }
  const sortNode = (node: PropertyTreeNode) => {
    node.children.sort((left, right) => left.name.localeCompare(right.name))
    node.properties.sort((left, right) => left.filename.localeCompare(right.filename))
    node.children.forEach(sortNode)
  }
  sortNode(root)
  return root
}
