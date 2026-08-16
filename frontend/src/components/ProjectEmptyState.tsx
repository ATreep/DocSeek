import { FolderPlus } from 'lucide-react'
import { FormEvent, useState } from 'react'

export default function ProjectEmptyState({ busy, onCreate }: { busy: boolean; onCreate: (name: string) => Promise<boolean> }) {
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

  return <section className="empty-project-view" aria-labelledby="create-project-title"><div className="empty-project-heading"><FolderPlus size={30} /><span className="eyebrow">PROJECTS</span><h1 id="create-project-title">Create your first project</h1></div><form className="empty-project-form" onSubmit={submit}><label>Project name<input aria-label="Project name" value={name} onChange={event => setName(event.target.value)} placeholder="Project name" autoFocus required /></label><button className="primary-button" disabled={busy || submitting || !name.trim()}><FolderPlus size={15} /> Create project</button></form></section>
}
