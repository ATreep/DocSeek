import { useEffect, useRef, type FormEvent } from 'react'
import { Bot, CircleDot, FileText, Send, Trash2, UserRound } from 'lucide-react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

const markdownComponents: Components = {
  table: ({ node: _node, ...props }) => <div className="chat-table-scroll"><table {...props} /></div>,
}

export type ChatCitation = {
  kind?: string
  id?: string
  label?: string
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
  const transcriptRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const transcript = transcriptRef.current
    if (transcript) transcript.scrollTop = transcript.scrollHeight
  }, [messages])

  return <section className="query-panel chat-panel" aria-label="AI Query chat">
    <div className="chat-header">
      <div className="chat-title">
        <span className="eyebrow">AI QUERY</span>
        <h1>Ask across both graphs.</h1>
      </div>
      <button
        type="button"
        className="icon-button chat-clear-button"
        aria-label="Clear chat history"
        title="Clear chat history"
        disabled={busy || messages.length === 0 || !onClear}
        onClick={onClear}
      >
        <Trash2 size={17} />
      </button>
    </div>
    <div className="chat-transcript" aria-live="polite" ref={transcriptRef}>
      {!messages.length && <div className="chat-empty"><Bot size={24} /><strong>Start a grounded conversation</strong><span>Ask about the active project and its source context.</span></div>}
      {messages.map((message, index) => <article className={`chat-message ${message.role}`} key={`${message.role}-${index}`}>
        <div className="chat-avatar" aria-hidden="true">{message.role === 'assistant' ? <Bot size={15} /> : <UserRound size={15} />}</div>
        <div className="chat-message-body">
          <span className="chat-role">{message.role === 'assistant' ? 'DocSeek' : 'You'}</span>
          <div className="chat-bubble">
            {message.role === 'assistant' ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{message.content}</ReactMarkdown> : <p>{message.content}</p>}
            {message.streaming ? <span className="chat-streaming-caret" role="status" aria-label="DocSeek is responding" /> : null}
          </div>
          {message.citations?.length ? <div className="citation-row">{message.citations.map((citation, citationIndex) => {
            const label = citation.label || citation.id || citation.kind || 'Source'
            return <button
              type="button"
              className={`citation citation-${citation.kind || 'source'}`}
              key={`${citation.id || citation.label || 'citation'}-${citationIndex}`}
              aria-label={`Open ${citation.kind || 'source'} ${label}`}
              disabled={!onCitationSelect}
              onClick={() => onCitationSelect?.(citation)}
            >{citation.kind === 'entity' ? <CircleDot size={13} /> : <FileText size={13} />}{label}</button>
          })}</div> : null}
        </div>
      </article>)}
    </div>
    <form className="chat-composer" onSubmit={onSubmit}>
      <textarea
        aria-label="Ask AI Query"
        value={question}
        onChange={event => onQuestionChange(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            event.currentTarget.form?.requestSubmit()
          }
        }}
        placeholder="Ask a question about this project"
        rows={1}
      />
      <button type="submit" className="primary-button" aria-label="Send query" disabled={busy || !question.trim()}>
        <Send size={15} /> {busy ? 'Thinking...' : 'Send'}
      </button>
    </form>
  </section>
}
