import { Check, Languages } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { request } from '../api'
import { changeDisplayLanguage, type DisplayLanguage, useDocSeekTranslation } from '../i18n'
import FloatingWindow from './FloatingWindow'

type ProfileResponse = {
  preferences?: Record<string, unknown>
}

const choices: Array<{ code: DisplayLanguage; label: string }> = [
  { code: 'en', label: 'English' },
  { code: 'zh', label: '中文' },
]

function hasAuthToken(): boolean {
  return typeof localStorage?.getItem === 'function'
    && Boolean(localStorage.getItem('docseek-token'))
}

export default function LanguageSwitcher() {
  const { t, i18n } = useDocSeekTranslation()
  const [open, setOpen] = useState(false)
  const [preferences, setPreferences] = useState<Record<string, unknown>>({})
  const rootRef = useRef<HTMLDivElement>(null)
  const language = i18n.language.toLowerCase().startsWith('zh') ? 'zh' : 'en'

  useEffect(() => {
    if (!hasAuthToken()) return
    let disposed = false
    request<ProfileResponse>('/profile')
      .then((profile) => {
        if (disposed) return
        const nextPreferences = profile.preferences || {}
        setPreferences(nextPreferences)
        const saved = nextPreferences.display_language
        if (saved === 'zh' || saved === 'en') {
          void changeDisplayLanguage(saved, { persistProfile: false })
        }
      })
      .catch(() => undefined)
    return () => { disposed = true }
  }, [])

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  async function selectLanguage(nextLanguage: DisplayLanguage) {
    setOpen(false)
    await changeDisplayLanguage(nextLanguage)
    if (!hasAuthToken()) return
    const nextPreferences = { ...preferences, display_language: nextLanguage }
    setPreferences(nextPreferences)
    await request('/profile/preferences', {
      method: 'PATCH',
      body: JSON.stringify(nextPreferences),
    }).catch(() => undefined)
  }

  return (
    <div className="language-switcher" ref={rootRef}>
      <button
        type="button"
        className="icon-button"
        aria-label={t('Language')}
        title={t('Language')}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Languages size={17} />
      </button>
      <FloatingWindow open={open} className="language-menu" role="menu" aria-label={t('Language')}>
          <header className="compact-floating-header">
            <span className="eyebrow">{t('Language')}</span>
            <h2>{t('Language')}</h2>
          </header>
          {choices.map((choice) => (
            <button
              type="button"
              role="menuitem"
              key={choice.code}
              onClick={() => void selectLanguage(choice.code)}
            >
              <span>{choice.label}</span>
              {language === choice.code && <Check size={14} />}
            </button>
          ))}
      </FloatingWindow>
    </div>
  )
}
