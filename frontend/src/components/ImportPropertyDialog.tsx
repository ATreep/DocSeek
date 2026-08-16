import { FormEvent, useEffect, useRef, useState } from 'react'
import { Check, X } from 'lucide-react'
import FloatingWindow from './FloatingWindow'

type ImportPropertyDialogProps = {
  open: boolean
  originalFilename: string
  defaultFilename: string
  busy: boolean
  onCancel: () => void | Promise<void>
  onConfirm: (filename: string) => void | Promise<void>
}

export default function ImportPropertyDialog({ open, originalFilename, defaultFilename, busy, onCancel, onConfirm }: ImportPropertyDialogProps) {
  const [filename, setFilename] = useState(defaultFilename)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setFilename(defaultFilename)
    const selectionTimer = window.setTimeout(() => inputRef.current?.select(), 0)
    return () => window.clearTimeout(selectionTimer)
  }, [defaultFilename, open])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const nextFilename = filename.trim()
    if (!nextFilename || busy) return
    void onConfirm(nextFilename)
  }

  return <FloatingWindow open={open} className="modal-backdrop">
    <form className="upload-modal rename-modal" role="dialog" aria-modal="true" aria-label="Import property dialog" onSubmit={submit}>
      <div className="overlay-header">
        <div><span className="eyebrow">IMPORT PROPERTY</span><h2>{originalFilename}</h2></div>
        <button type="button" className="icon-button" aria-label="Close import window" disabled={busy} onClick={() => void onCancel()}><X size={16} /></button>
      </div>
      <label className="rename-field">
        Property filename
        <input ref={inputRef} value={filename} onChange={(event) => setFilename(event.target.value)} required />
      </label>
      <div className="modal-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void onCancel()}>Cancel</button>
        <button type="submit" className="primary-button" disabled={busy || !filename.trim()}><Check size={15} /> Import property</button>
      </div>
    </form>
  </FloatingWindow>
}
