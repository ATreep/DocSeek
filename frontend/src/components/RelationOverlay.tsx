import { ArrowRight, Share2, X } from 'lucide-react'
import type { GraphRelationDetail } from '../graph-relations'
import FloatingWindow from './FloatingWindow'

export default function RelationOverlay({ relation, onClose }: { relation: GraphRelationDetail | null; onClose: () => void }) {
  return <FloatingWindow open={Boolean(relation)} as="aside" className="property-overlay relation-overlay" role="dialog" aria-label="Relation detail">
    {relation ? <>
      <div className="overlay-header">
        <div>
          <span className="eyebrow">{relation.kind.toUpperCase()} RELATION</span>
          <h2><Share2 size={17} /> {relation.type}</h2>
        </div>
        <button type="button" className="icon-button" aria-label="Close relation detail" title="Close relation detail" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <div className={`relation-path ${relation.kind}`}>
        <div>
          <small>Source</small>
          <strong>{relation.sourceLabel}</strong>
          <span>{relation.source}</span>
        </div>
        <ArrowRight size={20} aria-hidden="true" />
        <div>
          <small>Target</small>
          <strong>{relation.targetLabel}</strong>
          <span>{relation.target}</span>
        </div>
      </div>
      <div className="attribute-block relation-details">
        <span className="eyebrow">RELATION DETAILS</span>
        <dl>
          <div><dt>Graph</dt><dd>{relation.kind === 'entity' ? 'Entity graph' : 'Property graph'}</dd></div>
          <div><dt>Relation type</dt><dd>{relation.type}</dd></div>
          <div><dt>Source identifier</dt><dd>{relation.source}</dd></div>
          <div><dt>Target identifier</dt><dd>{relation.target}</dd></div>
        </dl>
      </div>
    </> : null}
  </FloatingWindow>
}
