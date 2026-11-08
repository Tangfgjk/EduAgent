'use client'

import { useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useTranslation } from 'react-i18next'
import { ArrowRight, Paperclip, Plus, Loader2 } from 'lucide-react'
import { LearningShell } from './LearningShell'
import { useLearningIndex } from '@/hooks/useLearningIndex'
import { useAuthStatus } from '@/hooks/useAuthStatus'
import { allLearningActivity } from '@/lib/learning-dashboard'
import { createMasteryTopic, type TopicSourceInput } from '@/lib/learning-api'
import { learningProjectRoute, uploadProjectFile } from '@/lib/learning-project-api'
import { scopedUrl } from '@/lib/workspace-scope'

export default function UnifiedLearningHome() {
  const { t, i18n } = useTranslation()
  const tr = (zh: string, en: string) => i18n.language.startsWith('zh') ? zh : en
  const router = useRouter()
  const index = useLearningIndex()
  const auth = useAuthStatus()
  const input = useRef<HTMLInputElement>(null)
  const [goal, setGoal] = useState('')
  const [files, setFiles] = useState<TopicSourceInput[]>([])
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const canPlan = !auth.learningPolicy || auth.learningPolicy.allowedSurfaces.includes('chat')
  async function upload(selected: FileList | null) {
    if (!selected) return
    if (selected.length + files.length > 16) {
      setError(tr('每个项目最多添加 16 份资料，请减少选择。', 'Choose up to 16 materials per project.'))
      return
    }
    setUploading(true)
    setError('')
    try {
      for (const file of Array.from(selected)) {
        const source = await uploadProjectFile(file)
        setFiles(current => current.some(item => item.source_id === source.source_id) ? current : [...current, source])
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : tr('上传失败', 'Upload failed')) }
    finally { setUploading(false); if (input.current) input.current.value = '' }
  }
  async function create() {
    if (busy || uploading || (!goal.trim() && !files.length)) return
    setBusy(true)
    setError('')
    try {
      const objective = goal.trim() || tr('根据上传资料制定适合我的学习计划', 'Help me learn from the uploaded materials')
      const topic = await createMasteryTopic({ goal: objective, sources: files, modules: [], emoji: '🧭' })
      router.push(learningProjectRoute(topic.path_id))
    } catch (reason) { setError(reason instanceof Error ? reason.message : tr('创建失败', 'Could not create project')); setBusy(false) }
  }
  const topics = index.sources.mastery.filter(topic => topic.metadata.status === 'active')
  const other = allLearningActivity(index.sources).filter(item => item.kind !== 'mastery').slice(0, 8)
  return (
    <LearningShell title={tr('我的学习', 'My learning')} subtitle={tr('从一个目标开始，把资料、章节和陪学放在一起。', 'Keep your goal, chapters, materials and tutor together.')} back={false}>
      <form onSubmit={event => { event.preventDefault(); void create() }} className="rounded-2xl border border-border bg-card p-5 sm:p-7">
        <label htmlFor="project-goal" className="block text-xl font-semibold">{tr('你想学习什么？', 'What would you like to learn?')}</label>
        <textarea id="project-goal" value={goal} onChange={event => setGoal(event.target.value)} maxLength={2000} rows={3}
          placeholder={tr('例如：我想学会用二元一次方程组解决实际问题', 'For example, solve practical problems using simultaneous equations')}
          className="mt-4 w-full resize-y rounded-xl border border-input bg-background p-3 text-sm outline-none focus:border-ring" />
        <input ref={input} type="file" accept=".txt,.md,.pdf,.docx" multiple className="sr-only" aria-label={tr('上传学习资料', 'Upload learning materials')} onChange={event => void upload(event.target.files)} />
        {files.length > 0 && <ul className="mt-3 flex flex-wrap gap-2">{files.map(source => <li key={source.source_id} className="flex items-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm">
          <span className="max-w-56 truncate">{source.label}</span>
          <button type="button" disabled={busy || uploading} aria-label={tr('移除 ', 'Remove ') + source.label} onClick={() => setFiles(current => current.filter(item => item.source_id !== source.source_id))}>×</button>
        </li>)}</ul>}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <button type="button" disabled={busy || uploading || files.length >= 16} onClick={() => input.current?.click()} className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm disabled:opacity-50">
            {uploading ? <Loader2 size={16} className="animate-spin" /> : <Paperclip size={16} />}{tr('添加资料（可选）', 'Add materials (optional)')}
          </button>
          <button type="submit" disabled={busy || uploading || !canPlan || (!goal.trim() && !files.length)} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-primary-foreground disabled:opacity-50">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}{tr('创建学习项目', 'Create learning project')}
          </button>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">{tr('直接输入主题即可开始。资料支持 TXT、Markdown、PDF、DOCX，单文件最多 16 MB。', 'A topic is enough to begin. Optional TXT, Markdown, PDF or DOCX files, up to 16 MB each.')}</p>
        {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
      </form>
      <section className="mt-8">
        <h2 className="text-lg font-semibold">{tr('继续学习', 'Continue learning')}</h2>
        {index.loading && <p className="mt-4 text-sm text-muted-foreground">{t('Loading…')}</p>}
        {!!index.failed.length && <p role="alert" className="mt-3 text-sm">{tr('部分记录暂时无法读取。', 'Some records could not be loaded.')} <button className="underline" onClick={index.retry}>{t('Retry')}</button></p>}
        {!index.loading && !topics.length && <p className="mt-4 text-sm text-muted-foreground">{tr('还没有学习项目，在上方写下第一个想学的主题。', 'Start your first project with a topic above.')}</p>}
        <div className="mt-4 grid gap-4 sm:grid-cols-2">{topics.map(topic => <Link key={topic.path_id} href={learningProjectRoute(topic.path_id, undefined, topic.content_workspace_id)} className="rounded-xl border border-border bg-card p-5 transition hover:border-primary">
          <h3 className="font-semibold">{topic.name || topic.metadata.goal}</h3>
          <p className="mt-2 text-sm text-muted-foreground">{topic.next.knowledge_point_name || (topic.map.counts.total ? tr('可以复习或继续探索', 'Review or explore further') : tr('先和津津确认目标与大纲', 'Agree on your goal and outline'))}</p>
          <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground"><span>{tr('独立能力已确认', 'Abilities confirmed')} {topic.map.counts.mastered}/{topic.map.counts.total}</span><ArrowRight size={16} /></div>
        </Link>)}</div>
      </section>
      <details className="mt-8 text-sm"><summary className="cursor-pointer text-muted-foreground">{tr('其他学习记录与工具', 'Other learning records and tools')}</summary>
        <div className="mt-3 flex flex-wrap gap-4">
          <Link href={scopedUrl('/learning/mastery')}>{tr('原项目管理', 'Advanced project management')}</Link>
          <Link href={scopedUrl('/learning/reading')}>{tr('资料阅读', 'Material reader')}</Link>
          <Link href={scopedUrl('/learning/practice')}>{tr('独立练习', 'Practice')}</Link>
        </div>
        <ul className="mt-3 space-y-2">{other.map(item => <li key={item.key}><Link className="text-primary underline" href={item.href}>{item.title}</Link></li>)}</ul>
      </details>
    </LearningShell>
  )
}
