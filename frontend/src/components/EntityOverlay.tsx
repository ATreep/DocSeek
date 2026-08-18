import { X } from 'lucide-react'
import { useDocSeekTranslation } from '../i18n'
import type { GraphRelationDetail } from '../graph-relations'
import FloatingWindow from './FloatingWindow'
import RelationList from './RelationList'

type EntitySource = { id: string; filename?: string; property_type?: string; definition?: string }
type EntityMention = { property_id?: string; text?: string }

export type Entity = { [key: string]: unknown; id: string; name?: string; type?: string; source_property_ids?: string[]; source_contexts?: EntityMention[]; source_properties?: EntitySource[]; definition?: string }

const hiddenFields = new Set(['id', 'name', 'description', 'embedding', 'source_property_ids', 'source_contexts', 'source_properties'])

function displayValue(value: unknown) {
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'object' && value !== null) return JSON.stringify(value, null, 2)
  return String(value)
}

type EntityOverlayProps = {
  entity: Entity | null
  relations?: GraphRelationDetail[]
  onRelationSelect?: (relation: GraphRelationDetail) => void
  onClose: () => void
}

export default function EntityOverlay({ entity, relations = [], onRelationSelect, onClose }: EntityOverlayProps) {
  const { t } = useDocSeekTranslation()
  const details = entity ? Object.entries(entity).filter(([key, value]) => !hiddenFields.has(key) && value !== null && value !== undefined && value !== '') : []
  return <FloatingWindow open={Boolean(entity)} as="aside" className="property-overlay entity-overlay" role="dialog" aria-label={t('Entity preview')}>
    {entity && <><div className="overlay-header"><div><span className="eyebrow">{t('ENTITY PREVIEW')}</span><h2>{entity.name || entity.id}</h2></div><button className="icon-button" onClick={onClose} title={t('Close entity preview')} aria-label={t('Close entity preview')}><X size={16} /></button></div>
      <div className="attribute-block"><span className="eyebrow">{t('ENTITY DETAILS')}</span><dl><div><dt>{t('Identifier')}</dt><dd>{entity.id}</dd></div>{details.map(([key, value]) => <div key={key}><dt>{t(key.split('_').join(' '))}</dt><dd className={typeof value === 'object' ? 'entity-value entity-value-object' : 'entity-value'}>{displayValue(value)}</dd></div>)}</dl></div>
      <RelationList relations={relations} onSelect={onRelationSelect} />
      {entity.source_contexts?.length ? <div className="attribute-block"><span className="eyebrow">{t('MENTION CONTEXT')}</span><ul className="entity-contexts">{entity.source_contexts.map((context, index) => <li key={`${context.property_id || 'source'}-${index}`}><small>{context.property_id || t('Source property')}</small><p>{context.text || t('No stored context.')}</p></li>)}</ul></div> : null}
      <div className="attribute-block"><span className="eyebrow">{t('SOURCE PROPERTIES')}</span>{entity.source_properties?.length ? <ul className="entity-sources">{entity.source_properties.map(source => <li key={source.id}><div><strong>{source.filename || source.id}</strong><small>{source.property_type || source.id}</small></div>{source.definition && <p>{source.definition}</p>}</li>)}</ul> : entity.source_property_ids?.length ? <ul className="entity-sources">{entity.source_property_ids.map(sourceId => <li key={sourceId}>{sourceId}</li>)}</ul> : <p className="muted">{t('No source property references.')}</p>}</div></>}
  </FloatingWindow>
}
