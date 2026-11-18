'use client'
import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import SlideCanvas, { type Element } from './SlideCanvas'
import { setPendingPrompt } from '@/lib/pending-prompt'
import { masterySessionRoute as newStudyRoute, MASTERY_OPENING_SCOPE } from '@/lib/mastery-mode'
import { masterySessionRoute } from '@/lib/learning-routes'
import Link from 'next/link'
import ClassroomMedia from './ClassroomMedia'
import MarkdownRenderer from '@/components/common/MarkdownRenderer'
import { scopedUrl } from '@/lib/workspace-scope'
import { LearningShell } from './LearningShell'
import { projectRequest, projectEndpoint, learningProjectRoute } from '@/lib/learning-project-api'

type Section = {
  title: string
  objective_id: string
  kind: 'slide' | 'whiteboard' | 'quiz'
  goal: string
}
type Page = Section & {
  canvas?: Element[]
  module_id?: string
  id: string
  content: string
  steps: string[]
  narration: string
  question: string
  options: string[]
  source_ids: string[]
}
type Course = {
  mode?: string
  curriculum?: { module_id: string; module_name: string; objective_id: string; title: string }[]
  objective_cursor?: number
  sources?: { id: string; label: string; excerpt: string }[]
  id: string
  title: string
  state: string
  outline: Section[]
  pages: Page[]
  position: number
  error?: string
  completed_at?: number
  ended_at?: number
  whiteboard_steps?: Record<string, number>
  summary?: {
    viewed: number
    total: number
    answered: number
    skipped: number
    pending: number
    complete: boolean
  }
  answers: Record<
    string,
    { choice: number | null; correct: boolean | null; skipped?: boolean; feedback: string }
  >
  conversation: { id: string; scene_id: string; question: string; response: string }[]
}
const button = 'rounded-xl border border-border px-4 py-2 text-sm disabled:opacity-40'
const states: Record<string, string> = {
  queued_outline: '等待准备大纲',
  generating: '正在准备课堂',
  awaiting_confirmation: '请确认课堂大纲',
  queued_pages: '正在准备下一页',
  ready: '课堂已准备好',
  failed: '该步骤未完成',
  cancelled: '已取消生成',
}
export default function Classroom() {
  const { pathId } = useParams<{ pathId: string }>()
  const router = useRouter()
  const [moduleId, setModuleId] = useState(''),
    [modules, setModules] = useState<
      { id: string; name: string; knowledge_points?: { id: string; name: string }[] }[]
    >([])
  const [objectiveId, setObjectiveId] = useState('')
  useEffect(() => {
    setModuleId(new URLSearchParams(window.location.search).get('module') || '')
    setObjectiveId(new URLSearchParams(window.location.search).get('objective') || '')
    void projectRequest<{ topic: { map: { modules: { id: string; name: string }[] } } }>(
      projectEndpoint(pathId) + '/project'
    )
      .then(p => setModules(p?.topic?.map?.modules || []))
      .catch(() => {})
  }, [pathId])
  const base = projectEndpoint(pathId) + '/classrooms'
  const [courses, setCourses] = useState<Course[]>([]),
    [course, setCourse] = useState<Course | null>(null),
    [draft, setDraft] = useState<Section[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [minutes, setMinutes] = useState(15),
    [question, setQuestion] = useState(''),
    [chat, setChat] = useState(false),
    [step, setStep] = useState(0),
    [choice, setChoice] = useState<number | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [notice, setNotice] = useState('')
  const deletedIds = useRef(new Set<string>())
  const active = course && ['queued_outline', 'queued_pages', 'generating'].includes(course.state)
  const load = useCallback(async () => {
    const q = new URLSearchParams(window.location.search)
    const requested = q.get('classroom')
    const target = requested && !deletedIds.current.has(requested) ? requested : null
    const values = (await projectRequest<Course[]>(base)).filter(c => !deletedIds.current.has(c.id))
    setCourses(values)
    const exact = target
      ? await projectRequest<Course>(base + '/' + encodeURIComponent(target)).catch(e => {
          if (deletedIds.current.has(target)) return null
          throw e
        })
      : null
    if (target && deletedIds.current.has(target)) return
    const scene = q.get('scene')
    if (exact && scene && !exact.pages.some(p => p.id === scene))
      throw new Error('指定课堂页不存在或尚未准备好')
    setCourse(old => {
      if (old && deletedIds.current.has(old.id)) return null
      if (old) {
        const fresh = values.find(c => c.id === old.id) || (exact?.id === old.id ? exact : old)
        return { ...fresh, position: old.position }
      }
      const chapter=q.get('module'),objective=q.get('objective')
      const selected = exact || values.find(c=>(!chapter||c.curriculum?.every(k=>k.module_id===chapter))&&(!objective||c.curriculum?.every(k=>k.objective_id===objective)))
      if (!selected) return null
      const index = selected.pages.findIndex(p => p.id === scene)
      return index >= 0 ? { ...selected, position: index } : selected
    })
  }, [base])
  useEffect(() => {
    void load().catch(e => setError(String(e)))
  }, [load])
  useEffect(() => {
    if (!active) return
    const timer = setInterval(
      () => void load().catch(() => setError('连接中断，生成会在后台继续，请刷新')),
      3000
    )
    return () => clearInterval(timer)
  }, [active, load])
  useEffect(() => {
    setDraft(course?.outline || [])
  }, [course?.id, course?.outline])
  useEffect(() => {
    setStep(course?.whiteboard_steps?.[course.pages[course.position]?.id] || 0)
    setChoice(null)
  }, [course?.id, course?.position])
  async function act(path: string, body: unknown) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const d = await projectRequest<Course>(base + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      setCourse(d)
      return d
    } catch (e) {
      setError(String(e))
      return null
    } finally {
      setBusy(false)
    }
  }
  async function removeCourse() {
    if (!course) return
    const id = course.id
    setBusy(true)
    setError('')
    try {
      await projectRequest(base + '/' + encodeURIComponent(id), { method: 'DELETE' })
      deletedIds.current.add(id)
      const location = new URL(window.location.href)
      location.searchParams.delete('classroom')
      location.searchParams.delete('scene')
      window.history.replaceState(window.history.state, '', location.pathname + location.search + location.hash)
      setCourses(old => old.filter(c => c.id !== id))
      setCourse(null)
      setDraft([])
      setQuestion('')
      setChat(false)
      setConfirmDelete(false)
      setNotice('课堂已删除。选择生成范围后，点击“按已有目录生成详解PPT”即可重新开始。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败，请重试')
    } finally {
      setBusy(false)
    }
  }
  const page = course?.pages[course.position]
  const answer = page && course?.answers[page.id]
  useEffect(() => {
    if (!course?.pages[course.position]) return
    void projectRequest(base + '/' + course.id + '/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'view', position: course.position }),
    }).catch(e => setError(String(e)))
  }, [base, course?.id, course?.position, course?.pages.length])
  async function move(position: number) {
    if (course) await act('/' + course.id + '/control', { action: 'position', position })
  }
  async function handoff(intent: 'explain' | 'check', index = course?.position || 0) {
    const selectedPage = course?.pages[index]
    if (!course || !selectedPage) return
    setBusy(true)
    setError('')
    try {
      const result = await projectRequest<{ session_id: string }>(
        base + '/' + course.id + '/tutor',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ index, intent }),
        }
      )
      const prompt = `我刚阅读了PPT「${selectedPage.title}」。${intent === 'check' ? '请用没有在PPT展示答案的新任务检查我的理解；不要把看完当成掌握。' : '请围绕这一页继续解释，先了解我哪里没理解。'}如果已有未完成任务，请先说明接续方式，不要覆盖它。`
      setPendingPrompt(prompt, result.session_id ? 'mastery_path' : MASTERY_OPENING_SCOPE)
      router.push(
        result.session_id
          ? masterySessionRoute(pathId, result.session_id)
          : newStudyRoute(pathId, 'study')
      )
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <LearningShell title="互动课堂" subtitle="在当前项目中讲解、提问和练习" back={false}>
      <nav className="mb-5 flex flex-wrap gap-3">
        <Link className={button} href={learningProjectRoute(pathId)}>
          返回项目
        </Link>
        <Link
          className={button}
          href={scopedUrl('/learning/projects/' + encodeURIComponent(pathId) + '/diagnosis')}
        >
          学习诊断
        </Link>
        {!!course?.pages.length && <a className={button} href="#classroom-resources">配音与视频</a>}
      </nav>
      <ConfirmDialog open={confirmDelete} title="删除这堂互动课堂？"
        confirmLabel="确认删除" cancelLabel="取消" tone="danger" busy={busy} busyLabel="正在删除…"
        onCancel={() => setConfirmDelete(false)} onConfirm={() => void removeCourse()}>
        <p>将移除当前课堂及课件、配音、视频等资源入口，并停止继续生成。项目的章节目录、资料、陪学对话和诊断证据会保留。删除后可按已有目录重新生成。</p>
      </ConfirmDialog>
      {notice && <p role="status" className="my-4 text-sm text-primary">{notice}</p>}
      {error && (
        <p role="alert" className="my-4 text-destructive">
          {error}
          <button className={button} onClick={() => void load()}>
            重新连接
          </button>
        </p>
      )}
      <details open={!course} className="mb-5 rounded-2xl border border-border p-4">
        <summary className="cursor-pointer">准备一堂新课 / 选择历史课堂</summary>
        <p className="my-3 text-sm text-muted-foreground">
          直接沿用“章节与学习”的目录，不另起课堂大纲。按知识点分批生成详尽课件，无固定总页数；可随时暂停，已准备页面可以先学。
        </p>
        <div className="flex flex-wrap gap-3">
          <label>
            生成范围{' '}
            <select
              value={moduleId}
              onChange={e => {
                setModuleId(e.target.value)
                setObjectiveId('')
              }}
              className={button}
            >
              <option value="">全部已有章节</option>
              {modules.map(m => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            知识点{' '}
            <select
              className={button}
              value={objectiveId}
              onChange={e => setObjectiveId(e.target.value)}
            >
              <option value="">所选范围的全部知识点</option>
              {modules
                .filter(m => !moduleId || m.id === moduleId)
                .flatMap(m =>
                  (m.knowledge_points || []).map(k => (
                    <option key={k.id} value={k.id}>
                      {m.name} · {k.name}
                    </option>
                  ))
                )}
            </select>
          </label>
          <button
            disabled={busy}
            className={button}
            onClick={() =>
              void act('', {
                mode: 'curriculum_ppt',
                module_id: moduleId,
                objective_ids: objectiveId ? [objectiveId] : [],
              })
            }
          >
            按已有目录生成详解PPT
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {courses.map(c => (
            <button className={button} key={c.id} onClick={() => setCourse(c)}>
              {c.title} · {states[c.state]}
            </button>
          ))}
        </div>
      </details>
      {course && (
        <>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <p role="status">
              {states[course.state]} · 已准备 {course.pages.length} 页
              {!!course.curriculum?.length && <> · 知识点课件 {course.objective_cursor || 0}/{course.curriculum.length}</>}
            </p>
            <button className={button + ' text-destructive'} disabled={busy}
              onClick={() => setConfirmDelete(true)}>删除课堂</button>
            {active && (
              <button
                className={button}
                disabled={busy}
                onClick={() => void act('/' + course.id + '/control', { action: 'cancel' })}
              >
                取消生成
              </button>
            )}
            {(course.state === 'failed' || course.state === 'cancelled') && (
              <button
                className={button}
                disabled={busy}
                onClick={() =>
                  void act('/' + course.id + '/control', {
                    action: course.state === 'cancelled' ? 'resume' : 'retry',
                  })
                }
              >
                {course.state === 'cancelled' ? '恢复生成' : '重试未完成步骤'}
              </button>
            )}
          </div>
          {course.error && <p className="my-3 text-destructive">{course.error}</p>}
          {course.state === 'awaiting_confirmation' ? (
            <section className="rounded-2xl border border-border p-6">
              <h2 className="text-xl font-semibold">确认这堂课怎么学</h2>
              <p className="my-3 text-sm text-muted-foreground">
                可以修改各页标题与学习目标；确认后逐页准备。
              </p>
              {draft.map((s, i) => (
                <div className="my-4 rounded-xl bg-muted/40 p-4" key={i}>
                  <p className="text-xs text-muted-foreground">
                    第{i + 1}页 · {{ slide: '课件', whiteboard: '白板', quiz: '随堂练习' }[s.kind]}
                  </p>
                  <input
                    aria-label={'第' + (i + 1) + '页标题'}
                    className="my-2 w-full rounded-lg border border-border bg-background p-2"
                    value={s.title}
                    onChange={e =>
                      setDraft(old =>
                        old.map((x, n) => (n === i ? { ...x, title: e.target.value } : x))
                      )
                    }
                  />
                  <input
                    aria-label={'第' + (i + 1) + '页目标'}
                    className="w-full rounded-lg border border-border bg-background p-2"
                    value={s.goal}
                    onChange={e =>
                      setDraft(old =>
                        old.map((x, n) => (n === i ? { ...x, goal: e.target.value } : x))
                      )
                    }
                  />
                </div>
              ))}
              <button
                disabled={busy}
                className="rounded-xl bg-primary px-5 py-3 text-primary-foreground"
                onClick={() => void act('/' + course.id + '/confirm', { sections: draft })}
              >
                确认大纲并生成
              </button>
            </section>
          ) : page ? (
            <div className="grid min-w-0 gap-4 lg:grid-cols-[200px_minmax(0,1fr)]">
              <details open className="rounded-xl border border-border p-3">
                <summary>
                  {course.mode === 'curriculum_ppt' ? '项目学习目录' : '旧课堂目录'}
                </summary>
                {course.mode === 'curriculum_ppt' &&
                  course.curriculum?.map((item, i) => (
                    <details key={item.objective_id} open>
                      <summary className="my-2 cursor-pointer text-sm">
                        {item.module_name} · {item.title}
                      </summary>
                      {course.pages.map((p, n) =>
                        p.objective_id === item.objective_id ? (
                          <button
                            key={p.id}
                            className={
                              button +
                              ' my-1 block w-full text-left ' +
                              (n === course.position ? 'bg-primary text-primary-foreground' : '')
                            }
                            onClick={() => void move(n)}
                          >
                            {n + 1}. {p.title}
                          </button>
                        ) : null
                      )}
                      {!course.pages.some(p => p.objective_id === item.objective_id) && (
                        <p className="text-xs text-muted-foreground">待准备</p>
                      )}
                    </details>
                  ))}
                {course.mode !== 'curriculum_ppt' &&
                  course.outline.map((s, i) => (
                    <button
                      className={
                        'my-2 block w-full rounded-lg p-2 text-left text-sm ' +
                        (i === course.position
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-muted/40')
                      }
                      disabled={i >= course.pages.length || busy}
                      key={i}
                      onClick={() => void move(i)}
                    >
                      {i + 1}. {s.title}
                      {i >= course.pages.length ? ' · 准备中' : ''}
                    </button>
                  ))}
              </details>
              <section className="min-w-0 rounded-2xl border border-border bg-card p-5 md:p-8">
                <p className="text-sm text-muted-foreground">
                  第 {course.position + 1} 页 · {page.goal}
                </p>
                <h2 className="my-4 text-2xl font-semibold">{page.title}</h2>
                {page.canvas ? (
                  <SlideCanvas elements={page.canvas} title={page.title} />
                ) : (
                  <MarkdownRenderer
                    content={page.content}
                    enableMath
                    enableImages={false}
                    allowHtml={false}
                    className="break-words leading-8"
                  />
                )}
                <div className="my-4 flex flex-wrap gap-2">
                  {(['explain', 'check'] as const).map(intent => (
                    <button
                      className={button}
                      disabled={busy}
                      key={intent}
                      onClick={() => void handoff(intent)}
                    >
                      {intent === 'explain' ? '这页没看懂，向津津提问' : '学完了，让津津检查理解'}
                    </button>
                  ))}
                </div>
                {page.kind === 'whiteboard' && (
                  <div className="my-5 rounded-xl border border-border bg-muted/30 p-4">
                    <ol className="space-y-3">
                      {page.steps.slice(0, step + 1).map((s, i) => (
                        <li key={i}>
                          <MarkdownRenderer
                            content={`${i + 1}. ${s}`}
                            enableMath
                            enableImages={false}
                            allowHtml={false}
                          />
                        </li>
                      ))}
                    </ol>
                    <button
                      className={button}
                      disabled={busy || step >= page.steps.length - 1}
                      onClick={() =>
                        void act('/' + course.id + '/control', {
                          action: 'step',
                          position: step + 1,
                        }).then(d => {
                          if (d) setStep(d.whiteboard_steps?.[page.id] || 0)
                        })
                      }
                    >
                      下一步板书
                    </button>
                  </div>
                )}
                {page.kind === 'quiz' && (
                  <fieldset
                    disabled={busy || !!answer}
                    className="my-5 space-y-3 rounded-xl border border-border p-4"
                  >
                    <legend>随堂练习 · 不计为独立掌握</legend>
                    <p>{page.question}</p>
                    {page.options.map((o, i) => (
                      <label className="block rounded-lg bg-muted/40 p-3" key={i}>
                        <input
                          name={page.id}
                          type="radio"
                          checked={choice === i}
                          onChange={() => setChoice(i)}
                        />{' '}
                        {o}
                      </label>
                    ))}
                    <button
                      disabled={choice === null || !!answer || busy}
                      className={button}
                      onClick={() =>
                        void act('/' + course.id + '/answer', { index: course.position, choice })
                      }
                    >
                      提交回答
                    </button>
                  </fieldset>
                )}
                {answer && (
                  <div role="status" className="my-4 rounded-xl bg-muted p-4">
                    <p>
                      {answer.skipped
                        ? '本题已跳过'
                        : answer.correct
                          ? '本题回答正确'
                          : '本题还需要巩固'}
                    </p>
                    <p className="mt-2">{answer.feedback}</p>
                    <p className="mt-2 text-xs">
                      {answer.skipped
                        ? '跳过不计分。'
                        : '本次为课堂练习，已作为受助表现保存到项目，不直接认定独立掌握。'}
                    </p>
                  </div>
                )}
                <details className="my-4">
                  <summary>讲解文字与引用</summary>
                  <p className="whitespace-pre-wrap">{page.narration}</p>
                  <p className="mt-2 text-xs">
                    {page.source_ids.length
                      ? '资料片段编号：' +
                        page.source_ids
                          .map(id => course.sources?.find(s => s.id === id)?.label || id)
                          .join('、')
                      : '模型生成内容，无资料引用；重要结论请核查。'}
                  </p>
                  {course.sources
                    ?.filter(s => page.source_ids.includes(s.id))
                    .map(s => (
                      <blockquote
                        key={s.id}
                        className="mt-3 border-l-2 border-primary pl-3 text-sm"
                      >
                        {s.excerpt}
                      </blockquote>
                    ))}
                </details>
                <div className="mt-6 flex flex-wrap gap-2">
                  <button
                    disabled={busy || course.position === 0}
                    className={button}
                    onClick={() => void move(course.position - 1)}
                  >
                    上一页
                  </button>
                  <button
                    disabled={
                      busy ||
                      course.position >= course.pages.length - 1 ||
                      (page.kind === 'quiz' && !answer)
                    }
                    className={button}
                    onClick={() => void move(course.position + 1)}
                  >
                    下一页
                  </button>
                  {page.kind === 'quiz' && !answer && (
                    <button
                      className={button}
                      disabled={busy}
                      onClick={() => void act('/' + course.id + '/control', { action: 'skip' })}
                    >
                      跳过本题（不计分）
                    </button>
                  )}
                  <button className={button} onClick={() => setChat(!chat)}>
                    问津津 / 给我提示
                  </button>
                  {course.state === 'ready' && (
                    <button
                      disabled={busy}
                      className={button}
                      onClick={() => void act('/' + course.id + '/control', { action: 'finish' })}
                    >
                      结束并回顾
                    </button>
                  )}
                </div>
                {course.ended_at && (
                  <div className="mt-5 rounded-xl bg-muted p-4">
                    <h3>课堂回顾</h3>
                    <p>
                      {course.summary?.complete
                        ? '本课流程已完成'
                        : '本次学习已结束，可稍后继续未完成内容'}
                      。已浏览 {course.summary?.viewed || 0}/
                      {course.summary?.total || course.outline.length} 页；已作答{' '}
                      {course.summary?.answered || 0} 题，跳过 {course.summary?.skipped || 0}{' '}
                      题，待作答 {course.summary?.pending || 0}{' '}
                      题。看完课件不代表掌握，建议返回陪学独立核查。
                    </p>
                    <Link className="underline" href={learningProjectRoute(pathId)}>
                      返回项目继续陪学
                    </Link>
                  </div>
                )}
                <ClassroomMedia
                  key={course.id}
                  pathId={pathId}
                  cid={course.id}
                  index={course.position}
                  ready={course.state === 'ready'}
                  navigate={index => void move(index)}
                  tutor={index => void handoff('explain',index)}
                />
                {chat && (
                  <aside className="mt-6 rounded-xl border border-border p-4">
                    <h3>围绕当前页提问</h3>
                    {course.conversation
                      .filter(x => x.scene_id === page.id)
                      .map(x => (
                        <div className="my-3 whitespace-pre-wrap text-sm" key={x.id}>
                          <p className="font-medium">你：{x.question}</p>
                          <p className="mt-2">津津：{x.response}</p>
                        </div>
                      ))}
                    <form
                      onSubmit={e => {
                        e.preventDefault()
                        if (question.trim())
                          void act('/' + course.id + '/ask', {
                            index: course.position,
                            text: question,
                            request_id: crypto.randomUUID(),
                          }).then(d => {
                            if (d) setQuestion('')
                          })
                      }}
                    >
                      <textarea
                        aria-label="课堂提问"
                        required
                        maxLength={4000}
                        className="my-3 w-full rounded-lg border border-border bg-background p-3"
                        value={question}
                        onChange={e => setQuestion(e.target.value)}
                      />
                      <button className={button} disabled={busy}>
                        发送问题
                      </button>
                    </form>
                  </aside>
                )}
              </section>
            </div>
          ) : (
            <p className="py-10 text-muted-foreground">
              {course.state === 'cancelled'
                ? '生成已取消，已准备内容保留。点击“恢复生成”继续。'
                : course.state === 'failed'
                  ? '生成未完成，请点击重试。'
                  : '正在准备，请稍候。离开页面也会继续生成，可从项目课堂入口回来。'}
            </p>
          )}
        </>
      )}
    </LearningShell>
  )
}
