import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, FileText, Folder } from 'lucide-react'
import { Property } from '../api'
import { PropertyTreeNode, buildPropertyTree } from '../property-tree'
import { useDocSeekTranslation } from '../i18n'

type PropertyTreeProps = {
  properties: Property[]
  onSelect: (property: Property) => void
  selectionMode?: boolean
  selectedPropertyIds?: Set<string>
  onSelectionChange?: (propertyIds: Set<string>) => void
}

const EMPTY_PROPERTY_SELECTION = new Set<string>()

function SelectionCheckbox({ checked, indeterminate = false, label, onChange }: { checked: boolean; indeterminate?: boolean; label: string; onChange: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (input.current) input.current.indeterminate = indeterminate
  }, [indeterminate])
  return <input
    ref={input}
    type="checkbox"
    className="property-selection-checkbox"
    checked={checked}
    aria-label={label}
    aria-checked={indeterminate ? 'mixed' : checked}
    onChange={onChange}
    onClick={event => event.stopPropagation()}
  />
}

function TreeNodes({ node, depth, onSelect, collapsed, onToggle, selectionMode, selectedPropertyIds, onSelectionChange }: { node: PropertyTreeNode; depth: number; onSelect: (property: Property) => void; collapsed: Set<string>; onToggle: (path: string) => void; selectionMode: boolean; selectedPropertyIds: Set<string>; onSelectionChange: (propertyIds: Set<string>) => void }) {
  const { t } = useDocSeekTranslation()
  const descendantProperties = (treeNode: PropertyTreeNode): Property[] => [
    ...treeNode.properties,
    ...treeNode.children.flatMap(descendantProperties),
  ]
  const toggleProperties = (items: Property[]) => {
    const next = new Set(selectedPropertyIds)
    const allSelected = items.length > 0 && items.every(item => next.has(item.id))
    items.forEach(item => {
      if (allSelected) next.delete(item.id)
      else next.add(item.id)
    })
    onSelectionChange(next)
  }
  return <>
    {node.children.map(child => {
      const isCollapsed = collapsed.has(child.path)
      const childProperties = descendantProperties(child)
      const selectedCount = childProperties.filter(item => selectedPropertyIds.has(item.id)).length
      return <div key={child.path}>
        <div className="folder-row" style={{ paddingLeft: `${8 + depth * 14}px` }}>
          <button
            type="button"
            className="folder-toggle"
            aria-expanded={!isCollapsed}
            aria-label={`${t(isCollapsed ? 'Expand' : 'Collapse')} ${child.name} ${t('group')}`}
            onClick={() => onToggle(child.path)}
          >
            {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
            <Folder size={14} />
            <span>{child.name}</span>
          </button>
          {selectionMode && <SelectionCheckbox
            checked={selectedCount === childProperties.length && childProperties.length > 0}
            indeterminate={selectedCount > 0 && selectedCount < childProperties.length}
            label={`${t('Select')} ${child.name} ${t('group')}`}
            onChange={() => toggleProperties(childProperties)}
          />}
        </div>
        {!isCollapsed && <TreeNodes node={child} depth={depth + 1} onSelect={onSelect} collapsed={collapsed} onToggle={onToggle} selectionMode={selectionMode} selectedPropertyIds={selectedPropertyIds} onSelectionChange={onSelectionChange} />}
      </div>
    })}
    {node.properties.map(property => selectionMode ? <label key={property.id} className="property-row" style={{ paddingLeft: `${12 + depth * 14}px` }}>
      <SelectionCheckbox
        checked={selectedPropertyIds.has(property.id)}
        label={`${t('Select')} ${property.filename}`}
        onChange={() => toggleProperties([property])}
      />
      <span className={`file-dot ${property.property_type}`} />
      <span><FileText size={13} /> {property.filename}</span>
      <small>{t(property.status)}</small>
    </label> : <button key={property.id} className="property-row" style={{ paddingLeft: `${12 + depth * 14}px` }} onClick={() => onSelect(property)}><span className={`file-dot ${property.property_type}`} /><span><FileText size={13} /> {property.filename}</span><small>{t(property.status)}</small></button>)}
  </>
}

export default function PropertyTree({ properties, onSelect, selectionMode = false, selectedPropertyIds = EMPTY_PROPERTY_SELECTION, onSelectionChange = () => undefined }: PropertyTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  function toggle(path: string) {
    setCollapsed(current => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }
  return <TreeNodes node={buildPropertyTree(properties)} depth={0} onSelect={onSelect} collapsed={collapsed} onToggle={toggle} selectionMode={selectionMode} selectedPropertyIds={selectedPropertyIds} onSelectionChange={onSelectionChange} />
}
