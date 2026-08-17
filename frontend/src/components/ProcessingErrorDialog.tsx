import { useEffect, useState } from 'react'
import { Bug, Copy, X } from 'lucide-react'
import { useLanguage } from '../i18n'
import FloatingWindow from './FloatingWindow'

type ProcessingErrorDialogProps = {
  open: boolean
  summary?: string | null
  detail?: string | null
  onClose: () => void
}

export default function ProcessingErrorDialog({ open, summary, detail, onClose }: ProcessingErrorDialogProps) {
  const { t } = useLanguage()
  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied' | 'failed'>('idle')
  const report = detail?.trim() || summary?.trim() || t('No error detail was recorded.')

  useEffect(() => {
    if (open) setCopyStatus('idle')
  }, [open, report])

  async function copyReport() {
    try {
      await navigator.clipboard.writeText(report)
      setCopyStatus('copied')
    } catch {
      setCopyStatus('failed')
    }
  }

  return <FloatingWindow open={open} className="modal-backdrop" role="presentation">
    <section className="upload-modal processing-error-modal" role="dialog" aria-modal="true" aria-label={t('Processing error detail')}>
      <header className="processing-error-header">
        <div>
          <span className="eyebrow">{t('PROCESSING ERROR')}</span>
          <h2><Bug size={18} /> {t('Candidate build failed')}</h2>
        </div>
        <button type="button" className="icon-button" aria-label={t('Close error detail')} title={t('Close')} onClick={onClose}>
          <X size={16} />
        </button>
      </header>
      {summary ? <p className="processing-error-summary">{summary}</p> : null}
      <pre className="processing-error-stack" tabIndex={0}>{report}</pre>
      <footer className="modal-actions processing-error-actions">
        <span role="status">
          {copyStatus === 'copied' ? t('Copied to clipboard') : copyStatus === 'failed' ? t('Copy failed') : ''}
        </span>
        <button type="button" className="secondary-button" onClick={onClose}>{t('Close')}</button>
        <button type="button" className="primary-button" aria-label={t('Copy error detail')} onClick={copyReport}>
          <Copy size={15} /> {t('Copy error detail')}
        </button>
      </footer>
    </section>
  </FloatingWindow>
}
