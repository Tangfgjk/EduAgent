'use client'

import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import MarkdownRenderer from '@/components/common/MarkdownRenderer'
import { LearningShell } from './LearningShell'
import GoalContractPanel from './GoalContractPanel'
import DiagnosticFactsPanel from './DiagnosticFactsPanel'
import { scopedUrl } from '@/lib/workspace-scope'
import ExternalDiagnosisPanel from './ExternalDiagnosisPanel'
import DiagnosticImpact from './DiagnosticImpact'
import {
  projectRequest,
  projectEndpoint,
  learningProjectRoute,
  uploadProjectFile,
  type LearningProject,
} from '@/lib/learning-project-api'
import { fetchMasteryTopicSessions, type MapModule, type TopicSession } from '@/lib/learning-api'
import { masterySessionRoute as newSessionRoute } from '@/lib/mastery-mode'
import { masterySessionRoute, masteryTopicRoute } from '@/lib/learning-routes'
import ProjectPractice from './ProjectPractice'
import { setPendingPrompt } from '@/lib/pending-prompt'

type ChapterContent = { chapter_id: string; content: string; created_at: number }

export default function LearningProjectPage() {
  const params = useParams<{ pathId: string; chapterId?: string }>()
  return <LearningProject key={params.pathId} pathId={params.pathId} chapterId={params.chapterId} />
}
function LearningProject({ pathId, chapterId }: { pathId: string; chapterId?: string }) {
  const router = useRouter()
  const { i18n } = useTranslation()
  const tr = (zh: string, en: string) => (i18n.language.startsWith('zh') ? zh : en)
  const [tab, setTab] = useState<'overview' | 'chats' | 'materials' | 'practice'>('chats')
  const [project, setProject] = useState<LearningProject | null>(null)
  const [sessions, setSessions] = useState<TopicSession[]>([])
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [directoryOpen, setDirectoryOpen] = useState(true)
  const fileInput = useRef<HTMLInputElement>(null)
  const endpoint = projectEndpoint(pathId)
  const projectLoad = useRef(0)
  const sessionsLoad = useRef(0)
  const [sessionError, setSessionError] = useState('')
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const loadProject = useCallback(async () => {
    const version = ++projectLoad.current
    try {
      const data = await projectRequest<LearningProject>(endpoint + '/project')
      if (version !== projectLoad.current) return
      setProject(data)
      setError('')
    } catch (reason) {
      if (version === projectLoad.current)
        setError(reason instanceof Error ? reason.message : 'Could not load project')
    }
  }, [endpoint])
  const loadSessions = useCallback(async () => {
    const version = ++sessionsLoad.current
    setSessionsLoading(true)
    setSessionError('')
    try {
      const threads = await fetchMasteryTopicSessions(pathId)
      if (version !== sessionsLoad.current) return
      setSessions(
        threads.filter(session => !session.archived).sort((a, b) => b.updated_at - a.updated_at)
      )
    } catch (reason) {
      if (version === sessionsLoad.current)
        setSessionError(reason instanceof Error ? reason.message : 'Could not load conversations')
    } finally {
      if (version === sessionsLoad.current) setSessionsLoading(false)
    }
  }, [pathId])
  const load = useCallback(async () => {
    await Promise.all([loadProject(), loadSessions()])
  }, [loadProject, loadSessions])
  useEffect(() => {
    void load()
    return () => {
      projectLoad.current++
      sessionsLoad.current++
    }
  }, [load])

  async function attach(file: File | undefined) {
    if (!file) return
    setUploading(true)
    setError('')
    try {
      const source = await uploadProjectFile(file)
      await projectRequest(endpoint + '/project-materials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_id: source.source_id }),
      })
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : tr('添加失败', 'Could not add material'))
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  async function discuss(chapter?: MapModule, passage?: string) {
    if (chapter) {
      setPendingPrompt(
        tr('我正在学习章节「', 'I am studying chapter “') +
          chapter.name +
          tr('」。请围绕这一章陪我学习。', '”. Please tutor me within this chapter.') +
          (passage
            ? '\n' + tr('我不理解这段内容：', 'I need help with this passage: ') + passage
            : ''),
        'mastery_path'
      )
    }
    router.push(
      sessions[0]
        ? masterySessionRoute(pathId, sessions[0].session_id)
        : newSessionRoute(pathId, 'study')
    )
  }

  if (!project)
    return (
      <LearningShell title={tr('学习项目', 'Learning project')} subtitle="" back={false}>
        <p role={error ? 'alert' : 'status'}>{error || tr('正在读取项目…', 'Loading project…')}</p>
        {error && (
          <button onClick={() => void load().catch(reason => setError(reason.message))}>
            {tr('重试', 'Retry')}
          </button>
        )}
      </LearningShell>
    )
  const topic = project.topic
  const modules = topic.map.modules
  const chapter = modules.find(module => module.id === chapterId)
  const current =
    modules.find(module => module.id === project.location.chapter_id) ||
    modules.find(module =>
      module.knowledge_points.some(point => point.id === topic.next.knowledge_point_id)
    ) ||
    modules[0]
  const resume =
    sessions[0] && (project.location.surface === 'tutoring' || !project.location.chapter_id)
      ? masterySessionRoute(pathId, sessions[0].session_id)
      : current
        ? learningProjectRoute(pathId, current.id)
        : newSessionRoute(pathId, 'outline')
  const sourceInput = (
    <input
      ref={fileInput}
      type="file"
      accept=".txt,.md,.pdf,.docx"
      className="sr-only"
      aria-label={tr('添加项目资料', 'Add project materials')}
      onChange={event => void attach(event.target.files?.[0])}
    />
  )
  const sources = (
    <details className="rounded-xl border border-border bg-card p-4">
      <summary className="cursor-pointer font-medium">
        {tr('项目资料', 'Project materials')} ·{' '}
        {topic.sources.filter(source => source.kind !== 'goal').length}
      </summary>
      <ul className="mt-3 space-y-2">
        {topic.sources
          .filter(source => source.kind !== 'goal')
          .map(source => (
            <li key={source.id} className="text-sm">
              {source.label}{' '}
              <span className="text-xs text-muted-foreground">
                {source.available ? '' : tr('暂不可用', 'Unavailable')}
              </span>
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-primary">
                  {tr('查看资料片段', 'View excerpt')}
                </summary>
                <p className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">
                  {source.excerpt}
                </p>
              </details>
            </li>
          ))}
      </ul>
      {!topic.sources.some(source => source.kind !== 'goal') && (
        <p className="mt-2 text-sm text-muted-foreground">
          {tr(
            '可以直接按主题学习，也可以随时添加自己的资料。',
            'Start from the topic directly or add your own materials at any time.'
          )}
        </p>
      )}
      <button
        disabled={uploading}
        onClick={() => fileInput.current?.click()}
        className="mt-3 rounded-lg border border-border px-3 py-2 text-sm"
      >
        {uploading ? tr('正在解析…', 'Parsing…') : tr('添加资料', 'Add materials')}
      </button>
    </details>
  )

  if (chapterId && !chapter)
    return (
      <LearningShell
        title={topic.name}
        subtitle={tr(
          '此章节已调整，请返回目录选择。',
          'This chapter changed. Choose another from the outline.'
        )}
        back={false}
      >
        <Link href={learningProjectRoute(pathId)}>{tr('返回项目', 'Back to project')}</Link>
      </LearningShell>
    )
  if (chapter)
    return (
      <div className="flex h-full min-h-0 flex-col bg-background">
        {sourceInput}
        <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
          <Link href={learningProjectRoute(pathId)} className="text-sm text-primary">
            ← {topic.name}
          </Link>
          <span className="text-sm font-medium">{chapter.name}</span>
          <button
            onClick={() => setDirectoryOpen(value => !value)}
            className="ml-auto rounded-lg border border-border px-3 py-2 text-sm"
          >
            {tr(
              directoryOpen ? '收起目录' : '展开目录',
              directoryOpen ? 'Hide chapters' : 'Show chapters'
            )}
          </button>
          <button
            onClick={() => document.getElementById('chapter-ask')?.click()}
            className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
          >
            {tr('津津陪学', 'Ask Jinjin')}
          </button>
        </header>
        {error && (
          <p role="alert" className="p-3 text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="flex min-h-0 flex-1">
          {directoryOpen && (
            <aside className="w-44 shrink-0 overflow-auto border-r border-border p-3 sm:w-56">
              <h2 className="text-sm font-medium">{tr('章节目录', 'Chapters')}</h2>
              <nav className="mt-3 space-y-2">
                {modules.map(module => (
                  <Link
                    aria-current={module.id === chapter.id ? 'page' : undefined}
                    key={module.id}
                    href={learningProjectRoute(pathId, module.id)}
                    className={
                      'block rounded-lg p-2 text-sm ' +
                      (module.id === chapter.id
                        ? 'bg-accent text-primary'
                        : 'text-muted-foreground')
                    }
                  >
                    {module.name}
                    <span className="mt-1 block text-xs">
                      {module.mastered}/{module.total}
                    </span>
                  </Link>
                ))}
              </nav>
              <div className="mt-5">{sources}</div>
            </aside>
          )}
          <ChapterReader
            key={chapter.id + topic.sources.map(source => source.id).join(',')}
            pathId={pathId}
            chapter={chapter}
            initialPosition={
              project.location.chapter_id === chapter.id ? project.location.position || 0 : 0
            }
            onAsk={passage => discuss(chapter, passage)}
            tr={tr}
          />
        </div>
      </div>
    )

  return (
    <LearningShell title={topic.name} subtitle={topic.metadata.goal} back={false}>
      {sourceInput}
      {error && (
        <p role="alert" className="mb-4 text-destructive">
          {error}
        </p>
      )}
      <GoalContractPanel key={pathId + ':goal'} pathId={pathId} onUpdated={() => void load()} />
      <nav aria-label={tr('项目工作区', 'Project workspace')} className="mb-6 flex flex-wrap gap-2">
        {(
          [
            ['chats', '对话', 'Conversations'],
            ['overview', '章节与学习', 'Chapters'],
            ['materials', '项目资料', 'Materials'],
            ['practice', '核查与复习', 'Checks and review'],
          ] as const
        ).map(([key, zh, en]) => (
          <button
            key={key}
            type="button"
            aria-pressed={tab === key}
            onClick={() => setTab(key)}
            className={
              'rounded-lg px-4 py-2 text-sm ' +
              (tab === key ? 'bg-primary text-primary-foreground' : 'border border-border')
            }
          >
            {tr(zh, en)}
          </button>
        ))}
        <Link
          className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted"
          href={scopedUrl('/learning/projects/' + encodeURIComponent(pathId) + '/teach-ai')}
        >
          我教 AI
        </Link>
        <Link
          className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted"
          href={scopedUrl('/learning/projects/' + encodeURIComponent(pathId) + '/classroom')}
        >
          互动课堂
        </Link>
        <Link
          className="rounded-lg border border-border px-4 py-2 text-sm"
          href={scopedUrl('/learning/projects/' + encodeURIComponent(pathId) + '/diagnosis')}
        >
          {tr('学习诊断', 'Diagnosis')}
        </Link>
      </nav>
      {tab === 'chats' && (
        <section>
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">{tr('项目对话', 'Project conversations')}</h2>
            <Link
              href={newSessionRoute(pathId, modules.length ? 'study' : 'outline')}
              className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground"
            >
              {tr('新建对话', 'New conversation')}
            </Link>
          </div>
          <p className="my-3 text-sm text-muted-foreground">
            {tr(
              '这里的对话属于当前项目，共用本项目的学习目标、资料与学习记录。',
              'These conversations belong to this project and use its goal, materials and learning records.'
            )}
          </p>
          <ul className="space-y-2">
            {sessions.map(session => (
              <li key={session.session_id}>
                <Link
                  href={masterySessionRoute(pathId, session.session_id)}
                  className="block rounded-xl border border-border bg-card p-4"
                >
                  {session.title}
                  <p className="mt-1 text-xs text-muted-foreground">
                    {new Date(session.updated_at * 1000).toLocaleString(i18n.language)}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
          {sessionsLoading && (
            <p role="status" className="my-3 text-sm text-muted-foreground">
              {tr('正在读取对话…', 'Loading conversations…')}
            </p>
          )}
          {sessionError && (
            <div role="alert" className="my-3 text-sm text-destructive">
              {sessionError}
              <button
                className="ml-3 rounded-lg border border-border px-3 py-1"
                onClick={() => void loadSessions()}
              >
                {tr('重试读取对话', 'Retry conversations')}
              </button>
            </div>
          )}
          {!sessionsLoading && !sessionError && !sessions.length && (
            <p className="py-8 text-muted-foreground">
              {tr('还没有项目对话，从上方新建对话开始。', 'No conversations yet. Start one above.')}
            </p>
          )}
        </section>
      )}
      {tab === 'materials' && sources}
      {tab === 'practice' && <ProjectPractice key={pathId} pathId={pathId} />}
      <div hidden={tab !== 'overview'}>
        <div className="rounded-2xl border border-border bg-card p-6">
          <p className="text-sm text-muted-foreground">
            {modules.length ? tr('当前学习', 'Current learning') : tr('下一步', 'Next step')}
          </p>
          <h2 className="mt-2 text-xl font-semibold">
            {current?.name ||
              tr('先和津津确认目标与大纲', 'Agree on your goal and outline with Jinjin')}
          </h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {modules.length
              ? tr(
                  '阅读位置与学习证据分别保存，随时回来继续。',
                  'Your reading position and ability evidence are saved separately.'
                )
              : tr(
                  '津津会了解你的起点，提出大纲。你确认后再按章学习。',
                  'Jinjin will discuss your starting point and propose an outline for you to confirm.'
                )}
          </p>
          <Link
            href={resume}
            className="mt-5 inline-flex rounded-lg bg-primary px-4 py-2.5 text-primary-foreground"
          >
            {modules.length
              ? tr('继续学习', 'Continue learning')
              : tr('和津津确认目标与大纲', 'Plan with Jinjin')}
          </Link>
        </div>
        <DiagnosticImpact key={pathId + ':impact'} pathId={pathId} />
        <Link
          className="mt-4 inline-block text-sm text-primary underline"
          href={scopedUrl('/learning/projects/' + encodeURIComponent(pathId) + '/support')}
        >
          查看我的学习画像与支持
        </Link>
        {!!modules.length && (
          <section className="mt-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">{tr('学习章节', 'Chapters')}</h2>
              <Link href={newSessionRoute(pathId, 'outline')} className="text-sm text-primary">
                {tr('调整大纲', 'Adjust outline')}
              </Link>
            </div>
            <ol className="mt-3 space-y-3">
              {modules.map((module, index) => (
                <li key={module.id}>
                  <Link
                    href={learningProjectRoute(pathId, module.id)}
                    className="flex items-start gap-4 rounded-xl border border-border bg-card p-4 hover:border-primary"
                  >
                    <span className="text-primary">{String(index + 1).padStart(2, '0')}</span>
                    <div className="min-w-0 flex-1">
                      <h3 className="font-medium">{module.name}</h3>
                      <p className="mt-1 text-sm text-muted-foreground">{module.objective}</p>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {tr('独立能力已确认', 'Abilities confirmed')} {module.mastered}/
                        {module.total} ·{' '}
                        {project.chapters[module.id]
                          ? tr('章节内容已保存', 'Chapter content saved')
                          : tr('打开后准备章节内容', 'Open to prepare chapter content')}
                      </p>
                    </div>
                    <span>→</span>
                  </Link>
                  <Link
                    className="mt-2 inline-block text-sm text-primary underline"
                    href={scopedUrl(
                      '/learning/projects/' +
                        encodeURIComponent(pathId) +
                        '/classroom?module=' +
                        encodeURIComponent(module.id)
                    )}
                  >
                    学习本章PPT
                  </Link>
                  <Link
                    className="ml-4 mt-2 inline-block text-sm text-primary underline"
                    href={scopedUrl(
                      '/learning/projects/' +
                        encodeURIComponent(pathId) +
                        '/checks?module=' +
                        encodeURIComponent(module.id)
                    )}
                  >
                    学完后独立核查
                  </Link>
                </li>
              ))}
            </ol>
          </section>
        )}
      </div>
      <div hidden={tab !== 'overview'}>
        <DiagnosticFactsPanel key={pathId} pathId={pathId} />
        <ExternalDiagnosisPanel
          key={pathId + ':external'}
          pathId={pathId}
          objectives={modules.flatMap(module =>
            module.knowledge_points.map(point => ({ id: point.id, name: point.name }))
          )}
        />
        <details className="mt-6 text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            {tr('项目设置', 'Project settings')}
          </summary>
          <ul className="mt-3 space-y-2">
            {sessions.map(session => (
              <li key={session.session_id}>
                <Link
                  href={masterySessionRoute(pathId, session.session_id)}
                  className="text-primary"
                >
                  {session.title}
                </Link>
              </li>
            ))}
          </ul>
          <Link href={masteryTopicRoute(pathId)} className="mt-4 inline-block underline">
            {tr('打开原项目管理', 'Advanced project management')}
          </Link>
        </details>
      </div>
    </LearningShell>
  )
}

function ChapterReader({
  pathId,
  chapter,
  initialPosition,
  onAsk,
  tr,
}: {
  pathId: string
  chapter: MapModule
  initialPosition: number
  onAsk: (text?: string) => void | Promise<void>
  tr: (zh: string, en: string) => string
}) {
  const [material, setMaterial] = useState<ChapterContent | null>(null)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  const [selection, setSelection] = useState('')
  const [saveError, setSaveError] = useState(false)
  const leaving = useRef(false)
  const writes = useRef<Promise<unknown>>(Promise.resolve())
  const scroller = useRef<HTMLDivElement>(null)
  const position = useRef(initialPosition)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const endpoint = projectEndpoint(pathId)
  useEffect(() => {
    let active = true
    projectRequest<ChapterContent>(endpoint + '/chapters/' + encodeURIComponent(chapter.id), {
      method: 'POST',
    })
      .then(data => {
        if (active) setMaterial(data)
      })
      .catch(reason => {
        if (active) setError(reason.message)
      })
    return () => {
      active = false
    }
  }, [endpoint, chapter.id, nonce])
  useEffect(() => {
    if (!material || !scroller.current) return
    const node = scroller.current
    node.scrollTop = (node.scrollHeight - node.clientHeight) * initialPosition
  }, [material, initialPosition])
  const persist = useCallback(
    (keepalive = false, surface = 'reading') => {
      const body = JSON.stringify({ chapter_id: chapter.id, position: position.current, surface })
      writes.current = writes.current
        .catch(() => {})
        .then(() =>
          projectRequest(endpoint + '/project/location', {
            method: 'PUT',
            keepalive,
            headers: { 'Content-Type': 'application/json' },
            body,
          })
        )
      void writes.current.then(
        () => setSaveError(false),
        () => setSaveError(true)
      )
      return writes.current
    },
    [endpoint, chapter.id]
  )
  useEffect(() => {
    if (!material) return
    void persist()
    return () => {
      if (timer.current) clearTimeout(timer.current)
      if (!leaving.current) void persist(true)
    }
  }, [material, persist])
  async function ask(text?: string) {
    if (timer.current) clearTimeout(timer.current)
    leaving.current = true
    try {
      await persist(false, 'tutoring')
      await onAsk(text)
    } catch {
      leaving.current = false
      setSaveError(true)
    }
  }
  function scroll() {
    const node = scroller.current
    if (!node || !material) return
    position.current =
      node.scrollHeight > node.clientHeight
        ? node.scrollTop / (node.scrollHeight - node.clientHeight)
        : 0
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => persist(), 800)
  }
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2 text-xs text-muted-foreground">
        <span>{tr('章节阅读', 'Chapter reading')}</span>
        <button id="chapter-ask" onClick={() => void ask()} className="text-primary">
          {tr('围绕本章提问', 'Ask about this chapter')}
        </button>
        <details>
          <summary className="cursor-pointer">{tr('更多学习方式', 'More learning modes')}</summary>
          <div className="absolute z-20 mt-2 rounded-lg border border-border bg-card p-3 shadow">
            <p>
              <Link
                href={scopedUrl(
                  '/learning/projects/' +
                    encodeURIComponent(pathId) +
                    '/teach-ai?objective=' +
                    encodeURIComponent(chapter.knowledge_points[0]?.id || '')
                )}
              >
                {tr('我教 AI', 'Teach AI')}
              </Link>
            </p>
            <p className="mt-2">
              <Link
                href={scopedUrl('/learning/projects/' + encodeURIComponent(pathId) + '/classroom')}
              >
                互动课堂
              </Link>
            </p>
          </div>
        </details>
      </div>
      {saveError && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          {tr(
            '位置未保存，请检查连接后重试。',
            'Position not saved. Check your connection and retry.'
          )}{' '}
          <button onClick={() => void persist()} className="underline">
            {tr('重试', 'Retry')}
          </button>
        </p>
      )}
      {selection && (
        <button
          onClick={() => void ask(selection)}
          className="bg-accent px-4 py-2 text-left text-sm text-primary"
        >
          {tr('向津津询问选中的内容', 'Ask Jinjin about the selected passage')} →
        </button>
      )}
      <div
        ref={scroller}
        onScroll={scroll}
        onMouseUp={() =>
          setSelection(window.getSelection()?.toString().trim().slice(0, 1500) || '')
        }
        className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-10"
      >
        <div className="mx-auto max-w-3xl">
          {!material && !error && (
            <p role="status">
              {tr(
                '正在准备章节内容，可能需要一两分钟。你也可以先进入陪学。',
                'Preparing the chapter. This may take a minute or two. You can enter tutoring meanwhile.'
              )}
            </p>
          )}
          {error && (
            <p role="alert">
              {error}{' '}
              <button
                className="text-primary underline"
                onClick={() => {
                  setError('')
                  setNonce(value => value + 1)
                }}
              >
                {tr('重试', 'Retry')}
              </button>
            </p>
          )}
          {material && (
            <>
              <MarkdownRenderer content={material.content} enableImages={false} allowHtml={false} />
              <p className="mt-8 text-xs text-muted-foreground">
                {tr(
                  '这是生成的学习说明。阅读进度不等于掌握，理解通过陪学和独立尝试验证。',
                  'This is generated learning content. Reading progress is separate from mastery, which requires independent evidence.'
                )}
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
