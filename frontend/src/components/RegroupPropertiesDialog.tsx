import { FormEvent, useEffect, useRef, useState } from 'react'
import { FolderTree, X } from 'lucide-react'
import { useDocSeekTranslation } from '../i18n'
import FloatingWindow from './FloatingWindow'

type RegroupPropertiesDialogProps = {
  open: boolean
  projectName: string
  busy: boolean
  onClose: () => void
  onSubmit: (revisionPrompt: string) => void | Promise<void>
}

export default function RegroupPropertiesDialog({ open, projectName, busy, onClose, onSubmit }: RegroupPropertiesDialogProps) {
  const { t } = useDocSeekTranslation()
  const [revisionPrompt, setRevisionPrompt] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!open) return
    setRevisionPrompt('')
    const focusTimer = window.setTimeout(() => textareaRef.current?.focus(), 0)
    return () => window.clearTimeout(focusTimer)
  }, [open])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const prompt = revisionPrompt.trim()
    if (!prompt || busy) return
    void onSubmit(prompt)
  }

  return <FloatingWindow open={open} className="modal-backdrop">
    <form className="upload-modal regroup-modal" role="dialog" aria-modal="true" aria-label={t('Rearrange {{projectName}}', { projectName })} onSubmit={submit}>
      <div className="overlay-header">
        <div><span className="eyebrow">{t('REVISE PROJECT GROUPING')}</span><h2>{t('Rearrange {{projectName}}', { projectName })}</h2></div>
        <button type="button" className="icon-button" aria-label={t('Close property organization window')} disabled={busy} onClick={onClose}><X size={16} /></button>
      </div>
      <label className="regroup-field">
        {t('Arrangement revision')}
        <textarea ref={textareaRef} value={revisionPrompt} onChange={(event) => setRevisionPrompt(event.target.value)} required />
      </label>
      <p className="regroup-hint">{t('Provide revision instructions or additional context to help the Group Arrangement Agent update the property grouping tree and property names to match your intended result.')}</p>
      <div className="modal-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={onClose}>{t('Cancel')}</button>
        <button type="submit" className="primary-button" disabled={busy || !revisionPrompt.trim()}>
          {busy ? <span className="spinner" aria-hidden="true" /> : <FolderTree size={15} />}
          {t(busy ? 'Generating proposal' : 'Review re-grouping')}
        </button>
      </div>
    </form>
  </FloatingWindow>
}
