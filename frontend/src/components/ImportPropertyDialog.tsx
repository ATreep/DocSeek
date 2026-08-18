import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Check, FileText, X } from 'lucide-react'
import FloatingWindow from './FloatingWindow'
import { useDocSeekTranslation } from '../i18n'

export type ImportPropertyDialogItem = {
  import_id: string
  original_filename: string
  suggested_filename: string
  definition: string
  word_count?: number
  character_count?: number
  oversized?: boolean
  reasons?: Array<'word_count' | 'character_count'>
}

export type ConfirmedPropertyImport = {
  import_id: string
  filename: string
}

type ImportPropertyDialogProps = {
  open: boolean
  items: ImportPropertyDialogItem[]
  busy: boolean
  onCancel: () => void | Promise<void>
  onConfirm: (items: ConfirmedPropertyImport[]) => void | Promise<void>
}

export default function ImportPropertyDialog({ open, items, busy, onCancel, onConfirm }: ImportPropertyDialogProps) {
  const { t } = useDocSeekTranslation()
  const [filenames, setFilenames] = useState<Record<string, string>>({})
  const firstInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setFilenames(Object.fromEntries(items.map((item) => [item.import_id, item.suggested_filename])))
    const selectionTimer = window.setTimeout(() => firstInputRef.current?.select(), 0)
    return () => window.clearTimeout(selectionTimer)
  }, [items, open])

  const confirmedItems = useMemo(
    () => items.map((item) => ({ import_id: item.import_id, filename: (filenames[item.import_id] || '').trim() })),
    [filenames, items],
  )
  const hasEmptyFilename = confirmedItems.some((item) => !item.filename)
  const normalizedFilenames = confirmedItems.map((item) => item.filename.toLocaleLowerCase())
  const hasDuplicateFilename = new Set(normalizedFilenames).size !== normalizedFilenames.length
  const invalid = hasEmptyFilename || hasDuplicateFilename || confirmedItems.length === 0

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (invalid || busy) return
    void onConfirm(confirmedItems)
  }

  const importLabel = `${t('Import')} ${items.length} ${t(items.length === 1 ? 'property' : 'properties')}`

  return <FloatingWindow open={open} className="modal-backdrop">
    <form className="upload-modal import-batch-modal" role="dialog" aria-modal="true" aria-label={t('Import property dialog')} onSubmit={submit}>
      <div className="overlay-header">
        <div><span className="eyebrow">{t('IMPORT PROPERTIES')}</span><h2>{t('Review')} {items.length} {t(items.length === 1 ? 'property' : 'properties')}</h2></div>
        <button type="button" className="icon-button" aria-label={t('Close import window')} disabled={busy} onClick={() => void onCancel()}><X size={16} /></button>
      </div>
      <div className="import-property-list">
        {items.map((item, index) => <section className="import-property-row" key={item.import_id}>
          <div className="import-property-source">
            <FileText size={16} />
            <span>
              <strong>{item.original_filename}</strong>
              <small>{item.definition}</small>
              {item.oversized && <span className="import-size-warning" role="alert">
                <AlertTriangle size={14} aria-hidden="true" />
                <span>{t('This property is very large ({{words}} words, {{characters}} characters). Processing may take longer.', {
                  words: (item.word_count || 0).toLocaleString(),
                  characters: (item.character_count || 0).toLocaleString(),
                })}</span>
              </span>}
            </span>
          </div>
          <label className="rename-field">
            {t('Property filename')}
            <input
              ref={index === 0 ? firstInputRef : undefined}
              aria-label={t('Property filename for {{filename}}', { filename: item.original_filename })}
              value={filenames[item.import_id] || ''}
              onChange={(event) => setFilenames((current) => ({ ...current, [item.import_id]: event.target.value }))}
              required
            />
          </label>
        </section>)}
      </div>
      {hasDuplicateFilename ? <p className="import-validation" role="alert">{t('Each property needs a unique filename.')}</p> : null}
      <div className="modal-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void onCancel()}>{t('Cancel')}</button>
        <button type="submit" className="primary-button" disabled={busy || invalid}><Check size={15} /> {importLabel}</button>
      </div>
    </form>
  </FloatingWindow>
}
