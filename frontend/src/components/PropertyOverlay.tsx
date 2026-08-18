import { useEffect, useState } from 'react'
import { X, FileText, Image as ImageIcon, Pencil, Trash2 } from 'lucide-react'
import { Property } from '../api'
import type { GraphRelationDetail } from '../graph-relations'
import FloatingWindow from './FloatingWindow'
import type { Entity } from './EntityOverlay'
import RelationList from './RelationList'
import { useDocSeekTranslation } from '../i18n'

type PropertyOverlayProps = {
  property: Property | null
  onClose: () => void
  locked?: boolean
  onRename?: () => void
  onRemove?: () => void
  entities?: Entity[]
  onEntitySelect?: (entity: Entity) => void
  relations?: GraphRelationDetail[]
  onRelationSelect?: (relation: GraphRelationDetail) => void
}

export default function PropertyOverlay({ property, onClose, locked = false, onRename, onRemove, entities = [], onEntitySelect, relations = [], onRelationSelect }: PropertyOverlayProps) {
  const { t } = useDocSeekTranslation()
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [textPreview, setTextPreview] = useState('')
  useEffect(() => {
    let objectUrl: string | null = null
    setPreviewUrl(null)
    setTextPreview('')
    if (!property?.project_id) return () => undefined
    const token = typeof localStorage?.getItem === 'function' ? localStorage.getItem('docseek-token') : null
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
    fetch(`/api/projects/${property.project_id}/properties/${property.id}/content`, { headers })
      .then(async response => setTextPreview((await response.text()).slice(0, 12000)))
      .catch(() => undefined)
    if (property.property_type === 'image') {
      fetch(`/api/projects/${property.project_id}/properties/${property.id}/raw`, { headers })
        .then(async response => {
          objectUrl = URL.createObjectURL(await response.blob())
          setPreviewUrl(objectUrl)
        })
        .catch(() => undefined)
    }
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [property])

  return <FloatingWindow open={Boolean(property)} as="aside" className="property-overlay">
    {property && <><div className="overlay-header"><div><span className="eyebrow">{t('PROPERTY PREVIEW')}</span><h2>{property.filename}</h2></div><button className="icon-button" onClick={onClose} title={t('Close preview')}><X size={16} /></button></div>
      {(onRename || onRemove) && <div className="overlay-actions"><button type="button" className="icon-button" aria-label={t('Rename property')} title={t('Rename property')} disabled={locked} onClick={onRename}><Pencil size={15} /></button><button type="button" className="icon-button danger" aria-label={t('Remove property')} title={t('Remove property')} disabled={locked} onClick={onRemove}><Trash2 size={15} /></button></div>}
      {property.property_type === 'image' && <div className="preview-frame">{previewUrl ? <img className="preview-image" src={previewUrl} alt={property.filename} /> : <><ImageIcon size={34} /><span>{t('Loading image preview')}</span></>}</div>}
      <div className={`preview-frame ${property.property_type === 'image' ? 'preview-content' : ''}`}>{textPreview ? <pre className="preview-text">{textPreview}</pre> : <><FileText size={34} /><span>{t('Loading extracted content')}</span></>}</div>
      <div className="attribute-block"><span className="eyebrow">{t('ATTRIBUTE')}</span><h3>{t('Definition')}</h3><p>{property.definition || t('Definition is being generated.')}</p><dl><div><dt>{t('Type')}</dt><dd>{property.property_type}</dd></div><div><dt>{t('Status')}</dt><dd className="status-pill">{t(property.status)}</dd></div></dl></div>
      <RelationList relations={relations} onSelect={onRelationSelect} />
      <div className="attribute-block"><div className="property-entity-heading"><span className="eyebrow">{t('OWNED ENTITIES')}</span><small>{entities.length} {t(entities.length === 1 ? 'entity' : 'entities')}</small></div>{entities.length ? <ul className="property-entities">{entities.map(entity => <li key={entity.id}><button type="button" aria-label={`${entity.name || entity.id} ${entity.type || t('Entity')}`} onClick={() => onEntitySelect?.(entity)}><strong>{entity.name || entity.id}</strong><small>{entity.type || t('Entity')}</small>{entity.definition ? <p>{entity.definition}</p> : null}</button></li>)}</ul> : <p className="muted">{t('No entities extracted from this property.')}</p>}</div></>}
  </FloatingWindow>
}
