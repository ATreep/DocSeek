import { useEffect, useMemo, useRef, useState } from 'react'
import { FileText, Folder, ListFilter } from 'lucide-react'
import { useDocSeekTranslation } from '../i18n'
import type { Property } from '../api'
import './GraphDisplayFilter.css'
import {
  graphDisplayGroups,
  type GraphDisplaySelection,
} from '../graph-display-filter'

type GraphDisplayFilterProps = {
  properties: Property[]
  selection: GraphDisplaySelection
  onChange?: (selection: GraphDisplaySelection) => void
  importanceFilter?: GraphImportanceFilter
  onImportanceFilterChange?: (filter: GraphImportanceFilter) => void
}

export type GraphImportanceFilter = 'all' | 'important' | 'top20' | 'top50'

function propertyDirectory(property: Property): string {
  const parts = property.relative_path.split('/').filter(Boolean)
  if (parts[0] === 'properties') parts.shift()
  parts.pop()
  return parts.join('/')
}

export default function GraphDisplayFilter({
  properties,
  selection,
  onChange,
  importanceFilter = 'all',
  onImportanceFilterChange,
}: GraphDisplayFilterProps) {
  const { t } = useDocSeekTranslation()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const groups = useMemo(() => graphDisplayGroups(properties), [properties])
  const sortedProperties = useMemo(
    () => [...properties].sort((left, right) => left.filename.localeCompare(right.filename)),
    [properties],
  )
  const selectionCount = selection.groupPaths.length
    + selection.propertyIds.length
    + (importanceFilter === 'all' ? 0 : 1)
  const badgeLabel = selectionCount > 99 ? '99+' : String(selectionCount)

  useEffect(() => {
    if (!open) return
    function closeOnOutsideClick(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  function toggleGroup(path: string) {
    const groupPaths = selection.groupPaths.includes(path)
      ? selection.groupPaths.filter(item => item !== path)
      : [...selection.groupPaths, path].sort()
    onChange?.({ ...selection, groupPaths })
  }

  function toggleProperty(propertyId: string) {
    const propertyIds = selection.propertyIds.includes(propertyId)
      ? selection.propertyIds.filter(item => item !== propertyId)
      : [...selection.propertyIds, propertyId].sort()
    onChange?.({ ...selection, propertyIds })
  }

  return <div className="graph-display-filter" ref={rootRef}>
    <button
      type="button"
      className={selectionCount ? 'active' : ''}
      aria-label={t('Filter graph display')}
      aria-expanded={open}
      title={t('Filter graph display')}
      onClick={() => setOpen(current => !current)}
    >
      <ListFilter size={15} />
      {selectionCount > 0 && <span className={`graph-filter-badge${selectionCount > 99 ? ' large' : ''}`} title={`${selectionCount} ${t('selections')}`}>{badgeLabel}</span>}
    </button>
    {open && <div className="graph-display-filter-popover" role="dialog" aria-label={t('Graph display filter')}>
      <header>
        <div>
          <strong>{t('Display filter')}</strong>
          <small>{selectionCount ? `${selectionCount} ${t('selected')}` : t('All properties')}</small>
        </div>
        <button
          type="button"
          className="graph-filter-clear"
          aria-label={t('Clear graph display filter')}
          disabled={!selectionCount}
          onClick={() => {
            onChange?.({ groupPaths: [], propertyIds: [] })
            onImportanceFilterChange?.('all')
          }}
        >
          {t('Clear')}
        </button>
      </header>
      <div className="graph-display-filter-options">
        <section aria-labelledby="graph-filter-importance-heading">
          <h3 id="graph-filter-importance-heading">{t('Importance')}</h3>
          <label className="graph-filter-importance">
            <span>{t('Show nodes')}</span>
            <select
              aria-label={t('Importance filter')}
              value={importanceFilter}
              onChange={event => onImportanceFilterChange?.(event.target.value as GraphImportanceFilter)}
            >
              <option value="all">{t('All')}</option>
              <option value="important">{t('Important')}</option>
              <option value="top20">Top 20</option>
              <option value="top50">Top 50</option>
            </select>
          </label>
        </section>
        {groups.length > 0 && <section aria-labelledby="graph-filter-groups-heading">
          <h3 id="graph-filter-groups-heading">{t('Groups')}</h3>
          {groups.map(group => <label
            className="graph-filter-option"
            key={group.path}
            style={{ paddingLeft: `${10 + group.depth * 16}px` }}
          >
            <input
              type="checkbox"
              aria-label={group.path}
              checked={selection.groupPaths.includes(group.path)}
              onChange={() => toggleGroup(group.path)}
            />
            <Folder size={14} />
            <span>{group.name}</span>
            <small>{group.propertyIds.length}</small>
          </label>)}
        </section>}
        {sortedProperties.length > 0 && <section aria-labelledby="graph-filter-properties-heading">
          <h3 id="graph-filter-properties-heading">{t('Properties')}</h3>
          {sortedProperties.map(property => <label className="graph-filter-option" key={property.id}>
            <input
              type="checkbox"
              aria-label={property.filename}
              checked={selection.propertyIds.includes(property.id)}
              onChange={() => toggleProperty(property.id)}
            />
            <FileText size={14} />
            <span>
              {property.filename}
              <small>{propertyDirectory(property) || t('Root')}</small>
            </span>
          </label>)}
        </section>}
      </div>
    </div>}
  </div>
}
