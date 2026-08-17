import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { LanguageProvider, translate, useLanguage } from './i18n'
import { en, type MessageKey } from './i18n/en'
import { zh } from './i18n/zh'
import { processingStageLabel } from './processing-status'

function Probe() {
  const { language, setLanguage, t } = useLanguage()
  return (
    <>
      <span data-testid="lang">{language}</span>
      <span data-testid="greeting">{t('Sign in')}</span>
      <span data-testid="stage">{processingStageLabel('queued')}</span>
      <button type="button" onClick={() => setLanguage('zh')}>set-zh</button>
      <button type="button" onClick={() => setLanguage('en')}>set-en</button>
    </>
  )
}

describe('i18n catalogs', () => {
  it('covers every English key with a Chinese translation', () => {
    const englishKeys = Object.keys(en) as MessageKey[]
    const chineseKeys = new Set(Object.keys(zh))
    const missing = englishKeys.filter(key => !chineseKeys.has(key))
    expect(missing).toEqual([])
  })
})

describe('translate', () => {
  it('returns the English source string by default', () => {
    expect(translate('Sign in')).toBe('Sign in')
    expect(translate('Settings')).toBe('Settings')
  })

  it('interpolates named parameters', () => {
    expect(translate('Delete {name}? This permanently removes its files and graph.', { name: 'Atlas' }))
      .toBe('Delete Atlas? This permanently removes its files and graph.')
    expect(translate('Remove provider {name}?', { name: 'OpenAI' })).toBe('Remove provider OpenAI?')
  })
})

describe('LanguageProvider', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    cleanup()
  })

  it('starts in English and re-renders consumers after switching', async () => {
    const user = userEvent.setup()
    render(<LanguageProvider><Probe /></LanguageProvider>)
    expect(screen.getByTestId('lang').textContent).toBe('en')
    expect(screen.getByTestId('greeting').textContent).toBe('Sign in')

    await user.click(screen.getByRole('button', { name: 'set-zh' }))
    expect(screen.getByTestId('lang').textContent).toBe('zh')
    expect(screen.getByTestId('greeting').textContent).toBe('登录')
    expect(screen.getByTestId('stage').textContent).toBe('等待处理')
    expect(localStorage.getItem('docseek-language')).toBe('zh')

    await user.click(screen.getByRole('button', { name: 'set-en' }))
    expect(screen.getByTestId('lang').textContent).toBe('en')
    expect(screen.getByTestId('greeting').textContent).toBe('Sign in')
    expect(screen.getByTestId('stage').textContent).toBe('Queued for processing')
  })

  it('persists the selected language and restores it on a fresh provider', async () => {
    const user = userEvent.setup()
    const first = render(<LanguageProvider><Probe /></LanguageProvider>)
    await user.click(first.getByRole('button', { name: 'set-zh' }))
    first.unmount()

    render(<LanguageProvider><Probe /></LanguageProvider>)
    expect(screen.getByTestId('lang').textContent).toBe('zh')
    expect(screen.getByTestId('greeting').textContent).toBe('登录')
  })
})
