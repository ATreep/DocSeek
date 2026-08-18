import { CSSProperties, FormEvent, useEffect, useMemo, useState } from 'react'
import { ArrowRight, Check, FileText, FolderTree, X } from 'lucide-react'
import type { RegroupConfirmationItem, RegroupProposal } from '../api'
import FloatingWindow from './FloatingWindow'
import './RegroupConfirmationDialog.css'
import { useDocSeekTranslation } from '../i18n'

type RegroupConfirmationDialogProps = {
  open: boolean
  proposal: RegroupProposal | null
  busy: boolean
  onClose: () => void
  onConfirm: (items: RegroupConfirmationItem[]) => void | Promise<void>
}

export const REGROUP_CONFIRMATION_WIDTH = '980px'

export default function RegroupConfirmationDialog({ open, proposal, busy, onClose, onConfirm }: RegroupConfirmationDialogProps) {
  const { t } = useDocSeekTranslation()
  const [filenames, setFilenames] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!open || !proposal) return
    setFilenames(Object.fromEntries(proposal.changes.map(change => [change.property_id, change.proposed_filename])))
  }, [open, proposal])

  const items = useMemo<RegroupConfirmationItem[]>(() => (proposal?.changes || []).map(change => ({
    property_id: change.property_id,
    directory: change.proposed_directory,
    filename: (filenames[change.property_id] || '').trim(),
  })), [filenames, proposal])
  const normalizedNames = items.map(item => `${item.directory}/${item.filename}`.toLocaleLowerCase())
  const invalid = items.some(item => !item.filename) || new Set(normalizedNames).size !== normalizedNames.length
  const changedCount = proposal?.changes.filter(change => change.changed).length || 0

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || invalid || !proposal) return
    void onConfirm(items)
  }

  return <FloatingWindow open={open} className="modal-backdrop">
    <form
      className="upload-modal regroup-confirm-modal"
      style={{ '--regroup-confirm-width': REGROUP_CONFIRMATION_WIDTH } as CSSProperties}
      role="dialog"
      aria-modal="true"
      aria-label={t('Confirm property tree changes')}
      onSubmit={submit}
    >
      <div className="overlay-header">
        <div><span className="eyebrow">{t('CONFIRM PROPERTY TREE')}</span><h2>{t('Review proposed changes')}</h2></div>
        <button type="button" className="icon-button" aria-label={t('Close re-group confirmation')} disabled={busy} onClick={onClose}><X size={16} /></button>
      </div>
      <p className="regroup-confirm-summary">{changedCount ? t(changedCount === 1 ? '{{count}} property has proposed changes.' : '{{count}} properties have proposed changes.', { count: changedCount }) : t('The Group Arrangement Agent did not propose any tree or filename changes.')} {t('Edit filenames below before applying.')}</p>
      <div className="regroup-diff-list">
        {(proposal?.changes || []).map(change => <section className={`regroup-diff-row${change.changed ? ' changed' : ''}`} key={change.property_id}>
          <div className="regroup-diff-title"><FileText size={15} /><span><strong>{change.current_filename}</strong>{change.definition && <small>{change.definition}</small>}</span></div>
          <div className="regroup-directory-diff">
            <span><FolderTree size={13} />{change.current_directory || t('Root')}</span>
            <ArrowRight size={14} aria-hidden="true" />
            <span><FolderTree size={13} />{change.proposed_directory || t('Root')}</span>
          </div>
          <label className="rename-field">
            {t('Proposed filename')}
            <input
              aria-label={t('Proposed filename for {{filename}}', { filename: change.current_filename })}
              value={filenames[change.property_id] || ''}
              onChange={event => setFilenames(current => ({ ...current, [change.property_id]: event.target.value }))}
              required
            />
          </label>
        </section>)}
      </div>
      {invalid && <p className="import-validation" role="alert">{t('Every target group needs unique, non-empty property filenames.')}</p>}
      <div className="modal-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={onClose}>{t('Cancel')}</button>
        <button type="submit" className="primary-button" disabled={busy || invalid || !proposal}>
          {busy ? <span className="spinner" aria-hidden="true" /> : <Check size={15} />}
          {t(busy ? 'Applying changes' : 'Apply confirmed changes')}
        </button>
      </div>
    </form>
  </FloatingWindow>
}
