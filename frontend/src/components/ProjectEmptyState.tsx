import { FolderPlus } from 'lucide-react'
import { FormEvent, useState } from 'react'
import { useDocSeekTranslation } from '../i18n'

export default function ProjectEmptyState({ busy, onCreate }: { busy: boolean; onCreate: (name: string) => Promise<boolean> }) {
  const { t } = useDocSeekTranslation()
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    const projectName = name.trim()
    if (!projectName) return
    setSubmitting(true)
    try {
      if (await onCreate(projectName)) setName('')
    } finally {
      setSubmitting(false)
    }
  }

  return <section className="empty-project-view" aria-labelledby="create-project-title"><div className="empty-project-heading"><FolderPlus size={30} /><span className="eyebrow">{t('PROJECTS')}</span><h1 id="create-project-title">{t('Create your first project')}</h1></div><form className="empty-project-form" onSubmit={submit}><label>{t('Project name')}<input aria-label={t('Project name')} value={name} onChange={event => setName(event.target.value)} placeholder={t('Project name')} autoFocus required /></label><button className="primary-button" disabled={busy || submitting || !name.trim()}><FolderPlus size={15} /> {t('Create project')}</button></form></section>
}
