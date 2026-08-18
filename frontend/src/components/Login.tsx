import { FormEvent, useState } from 'react'
import { KeyRound, LogIn } from 'lucide-react'
import { useDocSeekTranslation } from '../i18n'
import { login } from '../api'

export default function Login({ onLogin }: { onLogin: () => void }) {
  const { t } = useDocSeekTranslation()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('admin')
  const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    try { await login(username, password); onLogin() } catch (err) { setError(err instanceof Error ? err.message : t('Sign in failed')) }
  }
  return <main className="login-page"><form className="login-form" onSubmit={submit}>
    <div className="brand-mark"><KeyRound size={18} /><span>DOCSEEK</span></div>
    <h1>{t('Knowledge, with provenance.')}</h1><p className="muted">{t('Sign in to your private graph workspace.')}</p>
    <label>{t('Username')}<input value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" /></label>
    <label>{t('Password')}<input value={password} onChange={event => setPassword(event.target.value)} type="password" autoComplete="current-password" /></label>
    {error && <div className="error-text">{error}</div>}
    <button className="primary-button" type="submit"><LogIn size={16} /> {t('Sign in')}</button>
    <p className="hint">{t('Local development account: admin / admin')}</p>
  </form></main>
}
