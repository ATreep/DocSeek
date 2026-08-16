import { FormEvent, useEffect, useRef, useState } from 'react'
import { FolderTree, X } from 'lucide-react'
import FloatingWindow from './FloatingWindow'

type RegroupPropertiesDialogProps = {
  open: boolean
  busy: boolean
  onClose: () => void
  onSubmit: (revisionPrompt: string) => void | Promise<void>
}

export default function RegroupPropertiesDialog({ open, busy, onClose, onSubmit }: RegroupPropertiesDialogProps) {
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
    <form className="upload-modal regroup-modal" role="dialog" aria-modal="true" aria-label="Re-group properties dialog" onSubmit={submit}>
      <div className="overlay-header">
        <div><span className="eyebrow">RE-GROUP PROPERTIES</span><h2>Revise property tree</h2></div>
        <button type="button" className="icon-button" aria-label="Close re-group window" disabled={busy} onClick={onClose}><X size={16} /></button>
      </div>
      <label className="regroup-field">
        Arrangement revision
        <textarea ref={textareaRef} value={revisionPrompt} onChange={(event) => setRevisionPrompt(event.target.value)} required />
      </label>
      <div className="modal-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="primary-button" disabled={busy || !revisionPrompt.trim()}><FolderTree size={15} /> Apply re-grouping</button>
      </div>
    </form>
  </FloatingWindow>
}
