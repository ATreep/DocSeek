import { Languages } from 'lucide-react'
import { useLanguage } from '../i18n'

export default function LanguageSwitcher() {
  const { language, setLanguage, t } = useLanguage()
  const next = language === 'zh' ? 'en' : 'zh'
  return (
    <button
      type="button"
      className="text-button language-switcher"
      title={language === 'zh' ? t('Switch to English') : t('Switch to Chinese')}
      aria-label={language === 'zh' ? t('Switch to English') : t('Switch to Chinese')}
      onClick={() => setLanguage(next)}
    >
      <Languages size={15} /> {language === 'zh' ? 'EN' : '中文'}
    </button>
  )
}
