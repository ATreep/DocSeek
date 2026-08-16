import { FileText, Folder } from 'lucide-react'
import { Property } from '../api'
import { PropertyTreeNode, buildPropertyTree } from '../property-tree'

type PropertyTreeProps = {
  properties: Property[]
  onSelect: (property: Property) => void
}

function TreeNodes({ node, depth, onSelect }: { node: PropertyTreeNode; depth: number; onSelect: (property: Property) => void }) {
  return <>
    {node.children.map(child => <div key={child.path}><div className="folder-row" style={{ paddingLeft: `${12 + depth * 14}px` }}><Folder size={14} /><span>{child.name}</span></div><TreeNodes node={child} depth={depth + 1} onSelect={onSelect} /></div>)}
    {node.properties.map(property => <button key={property.id} className="property-row" style={{ paddingLeft: `${12 + depth * 14}px` }} onClick={() => onSelect(property)}><span className={`file-dot ${property.property_type}`} /><span><FileText size={13} /> {property.filename}</span><small>{property.status}</small></button>)}
  </>
}

export default function PropertyTree({ properties, onSelect }: PropertyTreeProps) {
  return <TreeNodes node={buildPropertyTree(properties)} depth={0} onSelect={onSelect} />
}
