import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import { en, type MessageKey } from './en'
import { zh } from './zh'

export type { MessageKey }

export type Language = 'en' | 'zh'

const STORAGE_KEY = 'docseek-language'

const messages: Record<Language, Record<MessageKey, string>> = { en, zh }

function initialLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'en' || stored === 'zh') return stored
  } catch {
    /* storage unavailable */
  }
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('zh')
    ? 'zh'
    : 'en'
}

// Module-level language so plain modules (e.g. processing-status) can translate
// without a React context. Components that consume it must also subscribe to
// the provider, which re-renders them whenever the language changes.
let currentLanguage: Language = initialLanguage()

export function translate(key: MessageKey, params?: Record<string, string | number>): string {
  const template = messages[currentLanguage][key] ?? en[key] ?? key
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  )
}

type LanguageContextValue = {
  language: Language
  setLanguage: (next: Language) => void
}

const LanguageContext = createContext<LanguageContextValue | null>(null)

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(currentLanguage)
  const value = useMemo<LanguageContextValue>(
    () => ({
      language,
      setLanguage(next) {
        currentLanguage = next
        try {
          localStorage.setItem(STORAGE_KEY, next)
        } catch {
          /* storage unavailable */
        }
        setLanguageState(next)
      },
    }),
    [language],
  )
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>
}

export function useLanguage() {
  const context = useContext(LanguageContext)
  const language = context?.language ?? currentLanguage
  const setLanguage =
    context?.setLanguage ??
    ((next: Language) => {
      currentLanguage = next
    })
  return { language, setLanguage, t: translate }
}
