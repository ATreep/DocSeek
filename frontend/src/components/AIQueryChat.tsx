import { useEffect, useRef, type FormEvent } from 'react'
import { Bot, CircleDot, FileText, Send, Trash2, UserRound } from 'lucide-react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useDocSeekTranslation } from '../i18n'

const markdownComponents: Components = {
  table: ({ node: _node, ...props }) => <div className="chat-table-scroll"><table {...props} /></div>,
}

export type ChatCitation = {
  kind?: string
  id?: string
  label?: string
  reason?: string
  path?: string[]
}

export type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
  citations?: ChatCitation[]
  streaming?: boolean
}

type AIQueryChatProps = {
  question: string
  messages: ChatMessage[]
  busy: boolean
  onQuestionChange: (value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onClear?: () => void
  onCitationSelect?: (citation: ChatCitation) => void
}

export default function AIQueryChat({ question, messages, busy, onQuestionChange, onSubmit, onClear, onCitationSelect }: AIQueryChatProps) {
  const { t } = useDocSeekTranslation()
  const transcriptRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const transcript = transcriptRef.current
    if (transcript) transcript.scrollTop = transcript.scrollHeight
  }, [messages])

  return <section className="query-panel chat-panel" aria-label={t('AI Query chat')}>
    <div className="chat-header">
      <div className="chat-title">
        <span className="eyebrow">{t('AI QUERY')}</span>
        <h1>{t('Ask across both graphs.')}</h1>
      </div>
      <button
        type="button"
        className="icon-button chat-clear-button"
        aria-label={t('Clear chat history')}
        title={t('Clear chat history')}
        disabled={busy || messages.length === 0 || !onClear}
        onClick={onClear}
      >
        <Trash2 size={17} />
      </button>
    </div>
    <div className="chat-transcript" aria-live="polite" ref={transcriptRef}>
      {!messages.length && <div className="chat-empty"><Bot size={24} /><strong>{t('Start a grounded conversation')}</strong><span>{t('Ask about the active project and its source context.')}</span></div>}
      {messages.map((message, index) => <article className={`chat-message ${message.role}`} key={`${message.role}-${index}`}>
        <div className="chat-avatar" aria-hidden="true">{message.role === 'assistant' ? <Bot size={15} /> : <UserRound size={15} />}</div>
        <div className="chat-message-body">
          <span className="chat-role">{message.role === 'assistant' ? 'DocSeek' : t('You')}</span>
          <div className="chat-bubble">
            {message.role === 'assistant' ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{message.content}</ReactMarkdown> : <p>{message.content}</p>}
            {message.streaming ? <span className="chat-streaming-caret" role="status" aria-label={t('DocSeek is responding')} /> : null}
          </div>
          {message.citations?.length ? <div className="citation-row">{message.citations.map((citation, citationIndex) => {
            const label = citation.label || citation.id || citation.kind || t('Source')
            const path = citation.path?.filter(Boolean).join(' -> ')
            const details = [
              citation.reason,
              path && path !== label ? path : '',
            ].filter(Boolean).join(' · ')
            return <button
              type="button"
              className={`citation citation-${citation.kind || 'source'}`}
              key={`${citation.id || citation.label || 'citation'}-${citationIndex}`}
              aria-label={`Open ${citation.kind || 'source'} ${label}`}
              title={details || label}
              disabled={!onCitationSelect}
              onClick={() => onCitationSelect?.(citation)}
            >
              {citation.kind === 'entity' ? <CircleDot size={10} /> : <FileText size={10} />}
              <span className="citation-label">{label}</span>
            </button>
          })}</div> : null}
        </div>
      </article>)}
    </div>
    <form className="chat-composer" onSubmit={onSubmit}>
      <textarea
        aria-label={t('Ask AI Query')}
        value={question}
        onChange={event => onQuestionChange(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            event.currentTarget.form?.requestSubmit()
          }
        }}
        placeholder={t('Ask a question about this project')}
        rows={1}
      />
      <button type="submit" className="primary-button" aria-label={t('Send query')} disabled={busy || !question.trim()}>
        <Send size={15} /> {t(busy ? 'Thinking...' : 'Send')}
      </button>
    </form>
  </section>
}
