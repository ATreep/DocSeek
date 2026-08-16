import { FormEvent, useEffect, useRef, useState } from 'react'
import { Check, X } from 'lucide-react'
import FloatingWindow from './FloatingWindow'

type PropertyRenameDialogProps = {
  open: boolean
  currentFilename: string
  defaultFilename: string
  busy: boolean
  onClose: () => void
  onSubmit: (filename: string) => void | Promise<void>
}

export default function PropertyRenameDialog({ open, currentFilename, defaultFilename, busy, onClose, onSubmit }: PropertyRenameDialogProps) {
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
    void onSubmit(nextFilename)
  }

  return <FloatingWindow open={open} className="modal-backdrop">
    <form className="upload-modal rename-modal" role="dialog" aria-modal="true" aria-label="Rename property dialog" onSubmit={submit}>
      <div className="overlay-header">
        <div><span className="eyebrow">RENAME PROPERTY</span><h2>{currentFilename}</h2></div>
        <button type="button" className="icon-button" aria-label="Close rename window" onClick={onClose}><X size={16} /></button>
      </div>
      <label className="rename-field">
        Property filename
        <input ref={inputRef} value={filename} onChange={(event) => setFilename(event.target.value)} required />
      </label>
      <div className="modal-actions">
        <button type="button" className="secondary-button" onClick={onClose}>Cancel</button>
        <button type="submit" className="primary-button" disabled={busy || !filename.trim()}><Check size={15} /> Rename property</button>
      </div>
    </form>
  </FloatingWindow>
}
