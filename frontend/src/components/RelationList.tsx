import { ArrowDownLeft, ArrowUpRight } from 'lucide-react'
import type { GraphRelationDetail } from '../graph-relations'
import { useLanguage } from '../i18n'

type RelationListProps = {
  relations: GraphRelationDetail[]
  onSelect?: (relation: GraphRelationDetail) => void
}

export default function RelationList({ relations, onSelect }: RelationListProps) {
  const { t } = useLanguage()
  return <div className="attribute-block relation-section">
    <div className="relation-list-heading">
      <span className="eyebrow">{t('RELATIONS')}</span>
      <small>{relations.length} {relations.length === 1 ? t('relation') : t('relations')}</small>
    </div>
    {relations.length ? <ul className="node-relations">
      {relations.map((relation, index) => {
        const direction = relation.direction || 'outgoing'
        const relatedLabel = relation.relatedLabel || (direction === 'outgoing' ? relation.targetLabel : relation.sourceLabel)
        return <li key={`${relation.source}-${relation.target}-${relation.type}-${index}`}>
          <button
            type="button"
            aria-label={`${relation.type} ${direction} ${relatedLabel}`}
            disabled={!onSelect}
            onClick={() => onSelect?.(relation)}
          >
            <span className={`relation-direction ${direction}`}>
              {direction === 'outgoing' ? <ArrowUpRight size={13} /> : <ArrowDownLeft size={13} />}
              {direction}
            </span>
            <strong>{relation.type}</strong>
            <small>{relatedLabel}</small>
          </button>
        </li>
      })}
    </ul> : <p className="muted">{t('No graph relations.')}</p>}
  </div>
}
