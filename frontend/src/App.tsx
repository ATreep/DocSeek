import { useEffect, useState } from 'react'
import { useDocSeekTranslation } from './i18n'
import { request } from './api'
import Login from './components/Login'
import ProjectShell from './components/ProjectShell'

type AuthenticationState = 'checking' | 'authenticated' | 'anonymous'

export default function App() {
  const { t } = useDocSeekTranslation()
  const [authentication, setAuthentication] = useState<AuthenticationState>(
    localStorage.getItem('docseek-token') ? 'checking' : 'anonymous',
  )

  useEffect(() => {
    if (authentication !== 'checking') return
    let disposed = false
    request('/auth/me')
      .then(() => {
        if (!disposed) setAuthentication('authenticated')
      })
      .catch(() => {
        localStorage.removeItem('docseek-token')
        if (!disposed) setAuthentication('anonymous')
      })
    return () => {
      disposed = true
    }
  }, [authentication])

  if (authentication === 'checking') {
    return (
      <main className="login-page">
        <div className="login-form">
          <div className="brand-mark"><span>DOCSEEK</span></div>
          <p className="muted">{t('Checking session...')}</p>
        </div>
      </main>
    )
  }
  return authentication === 'authenticated'
    ? <ProjectShell onLogout={() => setAuthentication('anonymous')} />
    : <Login onLogin={() => setAuthentication('authenticated')} />
}
