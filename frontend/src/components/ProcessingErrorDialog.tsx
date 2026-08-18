import { useEffect, useState } from 'react'
import { Bug, Copy, X } from 'lucide-react'
import { useDocSeekTranslation } from '../i18n'
import FloatingWindow from './FloatingWindow'

type ProcessingErrorDialogProps = {
  open: boolean
  summary?: string | null
  detail?: string | null
  llmResponse?: string | null
  onClose: () => void
}

export default function ProcessingErrorDialog({ open, summary, detail, llmResponse, onClose }: ProcessingErrorDialogProps) {
  const { t } = useDocSeekTranslation()
  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied' | 'failed'>('idle')
  const stack = detail?.trim() || summary?.trim() || t('No error detail was recorded.')
  const response = llmResponse?.trim() || ''
  const report = response
    ? `${stack}\n\n${t('Original LLM response:')}\n${response}`
    : stack

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
      <div className="processing-error-body">
        <section className="processing-error-section">
          <h3>{t('Developer stack trace')}</h3>
          <pre className="processing-error-stack" tabIndex={0}>{stack}</pre>
        </section>
        {response ? <section className="processing-error-section">
          <h3>{t('Original LLM response')}</h3>
          <pre className="processing-error-stack processing-error-response" tabIndex={0}>{response}</pre>
        </section> : null}
      </div>
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
