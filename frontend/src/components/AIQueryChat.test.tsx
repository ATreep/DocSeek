import type { FormEvent } from 'react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AIQueryChat from './AIQueryChat'

afterEach(cleanup)

function KeyboardHarness({ onSubmit }: { onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  const [question, setQuestion] = useState('First line')
  return <AIQueryChat question={question} messages={[]} busy={false} onQuestionChange={setQuestion} onSubmit={onSubmit} />
}

describe('AIQueryChat', () => {
  it('starts the query textarea at its single-row minimum height', () => {
    render(<AIQueryChat question="" messages={[]} busy={false} onQuestionChange={() => undefined} onSubmit={() => undefined} />)

    expect(screen.getByRole('textbox', { name: 'Ask AI Query' }).getAttribute('rows')).toBe('1')
  })

  it('renders assistant answers as markdown', () => {
    render(<AIQueryChat question="" messages={[{ role: 'assistant', content: '**Ready**\n\n- grounded result' }]} busy={false} onQuestionChange={() => undefined} onSubmit={() => undefined} />)
    expect(screen.getByText('Ready').tagName).toBe('STRONG')
    expect(screen.getByRole('listitem').textContent).toContain('grounded result')
  })

  it('renders GitHub-flavored Markdown tables', () => {
    render(<AIQueryChat
      question=""
      messages={[{
        role: 'assistant',
        content: '| Property | Group |\n| --- | --- |\n| guide.md | Product |',
      }]}
      busy={false}
      onQuestionChange={() => undefined}
      onSubmit={() => undefined}
    />)

    expect(screen.getByRole('table')).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: 'Property' })).toBeTruthy()
    expect(screen.getByRole('cell', { name: 'guide.md' })).toBeTruthy()
  })

  it('clears completed chat history from the header', async () => {
    const user = userEvent.setup()
    const onClear = vi.fn()
    render(<AIQueryChat
      question=""
      messages={[
        { role: 'user', content: 'What is Atlas?' },
        { role: 'assistant', content: 'Atlas is a release product.' },
      ]}
      busy={false}
      onQuestionChange={() => undefined}
      onSubmit={() => undefined}
      onClear={onClear}
    />)

    await user.click(screen.getByRole('button', { name: 'Clear chat history' }))

    expect(onClear).toHaveBeenCalledOnce()
  })

  it('submits the chat composer question', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn((event: FormEvent) => event.preventDefault())
    render(<AIQueryChat question="What is ready?" messages={[]} busy={false} onQuestionChange={() => undefined} onSubmit={onSubmit} />)
    await user.click(screen.getByRole('button', { name: 'Send query' }))
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('submits with Enter and inserts a new line with Shift+Enter', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn((event: FormEvent) => event.preventDefault())
    render(<KeyboardHarness onSubmit={onSubmit} />)
    const textbox = screen.getByRole('textbox', { name: 'Ask AI Query' })

    await user.click(textbox)
    await user.keyboard('{Shift>}{Enter}{/Shift}Second line')
    expect((textbox as HTMLTextAreaElement).value).toBe('First line\nSecond line')
    expect(onSubmit).not.toHaveBeenCalled()

    await user.keyboard('{Enter}')
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('selects property and entity citations from assistant answers', async () => {
    const user = userEvent.setup()
    const onCitationSelect = vi.fn()
    render(<AIQueryChat
      question=""
      messages={[{
        role: 'assistant',
        content: 'Grounded answer',
        citations: [
          { kind: 'property', id: 'p1', label: 'plan.md' },
          { kind: 'entity', id: 'e1', label: 'Neo4j' },
        ],
      }]}
      busy={false}
      onQuestionChange={() => undefined}
      onSubmit={() => undefined}
      onCitationSelect={onCitationSelect}
    />)

    await user.click(screen.getByRole('button', { name: 'Open property plan.md' }))
    await user.click(screen.getByRole('button', { name: 'Open entity Neo4j' }))

    expect(screen.getByRole('button', { name: 'Open property plan.md' }).classList.contains('citation-property')).toBe(true)
    expect(screen.getByRole('button', { name: 'Open entity Neo4j' }).classList.contains('citation-entity')).toBe(true)

    expect(onCitationSelect).toHaveBeenNthCalledWith(1, {
      kind: 'property', id: 'p1', label: 'plan.md',
    })
    expect(onCitationSelect).toHaveBeenNthCalledWith(2, {
      kind: 'entity', id: 'e1', label: 'Neo4j',
    })
  })

  it('keeps citation details in a tooltip instead of expanding the chip', () => {
    render(<AIQueryChat
      question=""
      messages={[{
        role: 'assistant',
        content: 'Atlas uses Neo4j.',
        citations: [{
          kind: 'entity',
          id: 'neo4j',
          label: 'Neo4j',
          reason: 'Related through USES',
          path: ['Atlas', 'USES', 'Neo4j'],
        }],
      }]}
      busy={false}
      onQuestionChange={() => undefined}
      onSubmit={() => undefined}
    />)

    const citation = screen.getByRole('button', {
      name: 'Open entity Neo4j',
    })

    expect(citation.textContent).toBe('Neo4j')
    expect(citation.getAttribute('title')).toBe(
      'Related through USES · Atlas -> USES -> Neo4j',
    )
    expect(citation.querySelector('small')).toBeNull()
  })

  it('shows when the assistant answer is still streaming', () => {
    render(<AIQueryChat question="" messages={[{ role: 'assistant', content: 'Partial', streaming: true }]} busy={true} onQuestionChange={() => undefined} onSubmit={() => undefined} />)
    expect(screen.getByLabelText('DocSeek is responding')).toBeTruthy()
  })
})
