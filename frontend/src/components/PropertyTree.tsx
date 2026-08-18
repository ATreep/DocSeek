import { useState } from 'react'
import { ChevronDown, ChevronRight, FileText, Folder } from 'lucide-react'
import { Property } from '../api'
import { PropertyTreeNode, buildPropertyTree } from '../property-tree'
import { useDocSeekTranslation } from '../i18n'

type PropertyTreeProps = {
  properties: Property[]
  onSelect: (property: Property) => void
}

function TreeNodes({ node, depth, onSelect, collapsed, onToggle }: { node: PropertyTreeNode; depth: number; onSelect: (property: Property) => void; collapsed: Set<string>; onToggle: (path: string) => void }) {
  const { t } = useDocSeekTranslation()
  return <>
    {node.children.map(child => {
      const isCollapsed = collapsed.has(child.path)
      return <div key={child.path}>
        <button
          type="button"
          className="folder-row"
          style={{ paddingLeft: `${8 + depth * 14}px` }}
          aria-expanded={!isCollapsed}
          aria-label={`${t(isCollapsed ? 'Expand' : 'Collapse')} ${child.name} ${t('group')}`}
          onClick={() => onToggle(child.path)}
        >
          {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
          <Folder size={14} />
          <span>{child.name}</span>
        </button>
        {!isCollapsed && <TreeNodes node={child} depth={depth + 1} onSelect={onSelect} collapsed={collapsed} onToggle={onToggle} />}
      </div>
    })}
    {node.properties.map(property => <button key={property.id} className="property-row" style={{ paddingLeft: `${12 + depth * 14}px` }} onClick={() => onSelect(property)}><span className={`file-dot ${property.property_type}`} /><span><FileText size={13} /> {property.filename}</span><small>{t(property.status)}</small></button>)}
  </>
}

export default function PropertyTree({ properties, onSelect }: PropertyTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  function toggle(path: string) {
    setCollapsed(current => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }
  return <TreeNodes node={buildPropertyTree(properties)} depth={0} onSelect={onSelect} collapsed={collapsed} onToggle={toggle} />
}
