import { ArrowRight, Share2, X } from 'lucide-react'
import type { GraphRelationDetail } from '../graph-relations'
import { useLanguage } from '../i18n'
import FloatingWindow from './FloatingWindow'

export default function RelationOverlay({ relation, onClose }: { relation: GraphRelationDetail | null; onClose: () => void }) {
  const { t } = useLanguage()
  return <FloatingWindow open={Boolean(relation)} as="aside" className="property-overlay relation-overlay" role="dialog" aria-label={t('Relation detail')}>
    {relation ? <>
      <div className="overlay-header">
        <div>
          <span className="eyebrow">{relation.kind === 'entity' ? t('ENTITY RELATION') : t('PROPERTY RELATION')}</span>
          <h2><Share2 size={17} /> {relation.type}</h2>
        </div>
        <button type="button" className="icon-button" aria-label={t('Close relation detail')} title={t('Close relation detail')} onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <div className={`relation-path ${relation.kind}`}>
        <div>
          <small>{t('Source')}</small>
          <strong>{relation.sourceLabel}</strong>
          <span>{relation.source}</span>
        </div>
        <ArrowRight size={20} aria-hidden="true" />
        <div>
          <small>{t('Target')}</small>
          <strong>{relation.targetLabel}</strong>
          <span>{relation.target}</span>
        </div>
      </div>
      <div className="attribute-block relation-details">
        <span className="eyebrow">{t('RELATION DETAILS')}</span>
        <dl>
          <div><dt>{t('Graph')}</dt><dd>{relation.kind === 'entity' ? t('Entity graph') : t('Property graph')}</dd></div>
          <div><dt>{t('Relation type')}</dt><dd>{relation.type}</dd></div>
          <div><dt>{t('Source identifier')}</dt><dd>{relation.source}</dd></div>
          <div><dt>{t('Target identifier')}</dt><dd>{relation.target}</dd></div>
        </dl>
      </div>
    </> : null}
  </FloatingWindow>
}
