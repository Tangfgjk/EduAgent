'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { LearningShell } from './LearningShell'
import MarkdownRenderer from '@/components/common/MarkdownRenderer'
import {
  projectRequest,
  projectEndpoint,
  learningProjectRoute,
  type LearningProject,
} from '@/lib/learning-project-api'
import { scopedUrl } from '@/lib/workspace-scope'
import { masterySessionRoute, MASTERY_OPENING_SCOPE } from '@/lib/mastery-mode'
import { setPendingPrompt } from '@/lib/pending-prompt'

type Message = {
  id: string
  role: 'human' | 'ai_student' | 'tutor' | 'help_request'
  text: string
  at: number
  assisted?: boolean
}
type Claim = {
  dimension: 'concept' | 'reasoning' | 'example' | 'boundary'
  status: 'supported' | 'needs_clarification' | 'uncertain'
  observation: string
  human_turn_ids: string[]
  source_ids: string[]
  source_names?: string[]
}
type Session = {
  id: string
  objective_id: string
  objective_name: string
  persona: string
  student: { name: string; description: string }
  state: 'active' | 'paused' | 'finished'
  help_delivered: boolean
  messages: Message[]
  stale?: boolean
  latest_request?: {
    id: string
    kind: string
    text: string
    state: string
    retryable: boolean
    stage?: string
    error?: string
  }
  report: null | {
    summary: string
    claims: Claim[]
    dimensions?: { name: string; observed: boolean }[]
    human_turn_count: number
    assisted: boolean
    next: string
    models: string[]
  }
}
type Summary = Pick<Session, 'id' | 'objective_id' | 'objective_name' | 'persona' | 'state'>
const button =
  'rounded-xl border border-border px-3 py-2 text-sm disabled:opacity-40 hover:bg-muted'
const dimensions = {
  concept: '概念',
  reasoning: '理由与步骤',
  example: '举例',
  boundary: '条件与边界',
}
const statuses = {
  supported: '本次表达有依据',
  needs_clarification: '还需澄清',
  uncertain: '暂不能确认',
}

export default function TeachAI() {
  const { pathId } = useParams<{ pathId: string }>(),
    router = useRouter(),
    search = useSearchParams()
  const selected = search.get('teaching_session') || search.get('session') || ''
  const [project, setProject] = useState<LearningProject | null>(null),
    [history, setHistory] = useState<Summary[]>([]),
    [session, setSession] = useState<Session | null>(null)
  const [objective, setObjective] = useState(search.get('objective') || ''),
    [persona, setPersona] = useState('curious'),
    [text, setText] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  const alive = useRef(true),
    request = useRef(0),
    bottom = useRef<HTMLDivElement>(null)
  const base = projectEndpoint(pathId) + '/teach-ai'
  const route = (sid: string) =>
    scopedUrl(
      '/learning/projects/' +
        encodeURIComponent(pathId) +
        '/teach-ai?teaching_session=' +
        encodeURIComponent(sid)
    )
  const read = useCallback(async () => {
    const seq = ++request.current
    try {
      const [p, h, s] = await Promise.all([
        projectRequest<LearningProject>(projectEndpoint(pathId) + '/project'),
        projectRequest<Summary[]>(base),
        selected
          ? projectRequest<Session>(base + '/' + encodeURIComponent(selected))
          : Promise.resolve(null),
      ])
      if (alive.current && seq === request.current) {
        setProject(p)
        setHistory(h)
        setSession(s)
        setObjective(old => old || p.topic.map.modules[0]?.knowledge_points[0]?.id || '')
      }
    } catch (e) {
      if (alive.current && seq === request.current)
        setError(e instanceof Error ? e.message : '读取失败，请重试')
    }
  }, [pathId, base, selected])
  useEffect(() => {
    alive.current = true
    void read()
    return () => {
      alive.current = false
      request.current++
    }
  }, [read])
  const viewKey = useRef('')
  viewKey.current = pathId + ':' + selected
  const running = session?.latest_request?.state === 'running' && session.state === 'active'
  useEffect(() => {
    if (!running && !busy) return
    const timer = setInterval(() => void read(), 4000)
    return () => clearInterval(timer)
  }, [read, running, busy])
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [session?.messages.length])
  const working = busy || running
  async function create() {
    setBusy(true)
    setError('')
    try {
      const s = await projectRequest<Session>(base, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ objective_id: objective, persona, request_id: crypto.randomUUID() }),
      })
      setSession(s)
      router.push(route(s.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建失败')
    } finally {
      setBusy(false)
    }
  }
  async function send(kind: string, supplied?: { id: string; text: string }) {
    if (!session) return
    const sentFrom = viewKey.current
    const body =
      supplied?.text ||
      text.trim() ||
      (kind === 'example'
        ? '请给一个不同例子，帮助我重新组织讲解。'
        : '我不确定怎样解释，请给一小步提示。')
    if (kind === 'explain' && !text.trim() && !supplied) return
    setBusy(true)
    setError('')
    try {
      const s = await projectRequest<Session>(base + '/' + session.id + '/turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: supplied?.id || crypto.randomUUID(), kind, text: body }),
      })
      if (alive.current && viewKey.current === sentFrom) {
        request.current++
        setSession(s)
        setText('')
      }
    } catch (e) {
      if (!alive.current || viewKey.current !== sentFrom) return
      setError(e instanceof Error ? e.message : '生成未完成，讲解原话已保存')
      await read()
    } finally {
      setBusy(false)
    }
  }
  async function control(action: string) {
    if (!session) return
    setError('')
    try {
      setSession(
        await projectRequest<Session>(base + '/' + session.id + '/control', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action }),
        })
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    }
  }
  function tutor() {
    if (!session) return
    setPendingPrompt(
      '我刚在‘我教 AI’中讲解了「' +
        session.objective_name +
        '」。请根据项目内最新讲解记录和反馈，接着陪我学习，不要把AI学生的发言当成我的能力。',
      MASTERY_OPENING_SCOPE
    )
    router.push(masterySessionRoute(pathId, 'study'))
  }
  const points = project?.topic.map.modules.flatMap(m => m.knowledge_points) || []
  const humanOrder = (id: string) =>
    (session?.messages.filter(m => m.role === 'human').findIndex(m => m.id === id) ?? -1) + 1
  return (
    <LearningShell title="我教 AI" subtitle="把知识讲给一个会追问的 AI 学生" back={false}>
      <Link href={learningProjectRoute(pathId)} className="mb-4 inline-block text-sm text-primary">
        ← 返回项目
      </Link>
      {error && (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-destructive/40 p-3 text-sm text-destructive"
        >
          {error}
          <button className={button + ' ml-3'} onClick={() => void read()}>
            刷新记录
          </button>
        </div>
      )}
      {!project && !error && <p role="status">正在读取项目…</p>}
      {project && !session && (
        <div className="mx-auto max-w-2xl space-y-6">
          <section className="rounded-2xl border border-border bg-card p-6 space-y-4">
            <h2 className="text-xl font-semibold">试着把知识讲清楚</h2>
            <p className="text-sm text-muted-foreground">
              选择一个已有知识点，用自己的话讲解；AI 学生会针对你的解释追问。资料可选，AI
              的反馈不等于掌握结论。
            </p>
            {points.length ? (
              <>
                <label className="block text-sm">
                  我要教的知识点
                  <select
                    aria-label="我要教的知识点"
                    value={objective}
                    onChange={e => setObjective(e.target.value)}
                    className="mt-2 block w-full rounded-xl border border-border bg-background p-3"
                  >
                    {points.map(p => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-sm">
                  AI 学生
                  <select
                    aria-label="AI 学生"
                    value={persona}
                    onChange={e => setPersona(e.target.value)}
                    className="mt-2 block w-full rounded-xl border border-border bg-background p-3"
                  >
                    <option value="curious">小津 · 爱追问理由的初学者</option>
                    <option value="confused">小桂 · 容易混淆条件的同学</option>
                  </select>
                </label>
                <button
                  className={button + ' bg-primary text-primary-foreground'}
                  disabled={busy || !objective}
                  onClick={() => void create()}
                >
                  {busy ? '正在准备…' : '开始讲解'}
                </button>
              </>
            ) : (
              <p>
                请先在项目中和津津确认学习大纲。
                <Link
                  href={masterySessionRoute(pathId, 'outline')}
                  className="text-primary underline"
                >
                  确认大纲
                </Link>
              </p>
            )}
          </section>
          {!!history.length && (
            <section>
              <h2 className="font-semibold">继续上次讲解</h2>
              <ul className="mt-3 space-y-2">
                {history.map(s => (
                  <li key={s.id}>
                    <Link
                      href={route(s.id)}
                      className="block rounded-xl border border-border bg-card p-4"
                    >
                      {s.objective_name}
                      <span className="ml-3 text-sm text-muted-foreground">
                        {s.state === 'finished'
                          ? '查看回顾'
                          : s.state === 'paused'
                            ? '已暂停'
                            : '继续讲解'}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
      {session && (
        <div className="mx-auto max-w-3xl space-y-4">
          <header className="rounded-2xl border border-border bg-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="font-semibold">
                  正在教{session.student.name} · {session.objective_name}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {session.state === 'finished'
                    ? '本轮已结束'
                    : session.state === 'paused'
                      ? '已暂停，可回来继续'
                      : '模拟学生，不是真实学生账号'}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {session.state === 'active' && (
                  <button className={button} onClick={() => void control('pause')}>
                    暂停，稍后继续
                  </button>
                )}
                {session.state === 'paused' && (
                  <button disabled={busy} className={button} onClick={() => void control('resume')}>
                    继续讲解
                  </button>
                )}
                {session.state !== 'finished' && (
                  <button
                    disabled={!!working}
                    className={button}
                    onClick={() => void control('finish')}
                  >
                    结束并回顾
                  </button>
                )}
                <Link
                  className={button}
                  href={scopedUrl('/learning/projects/' + encodeURIComponent(pathId) + '/teach-ai')}
                >
                  选择其他知识点
                </Link>
              </div>
            </div>
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer text-muted-foreground">学习目标与学生卡</summary>
              <p className="mt-2">{session.student.description} 角色仅用于模拟追问。</p>
              <p className="mt-2">
                评价会引用你的原话，并保留模型判断的不确定性；AI
                学生的回答不计入你的掌握或训练数据。
              </p>
            </details>
            {session.help_delivered && (
              <p className="mt-3 text-xs text-primary">
                本轮已使用津津帮助，后续通过新任务核查独立应用。
              </p>
            )}
          </header>
          {session.stale && (
            <p role="alert" className="rounded-xl bg-muted p-3 text-sm">
              知识点或资料已变化，本记录保留供回顾。请返回选择页开始新一轮，不沿用旧资料继续判断。
            </p>
          )}
          <section aria-label="师生讲解对话" className="space-y-4">
            {session.messages.map(m => (
              <article
                id={'teach-turn-' + m.id}
                key={m.id}
                className={
                  'scroll-mt-6 rounded-2xl p-4 ' +
                  (m.role === 'human' || m.role === 'help_request'
                    ? 'ml-6 border border-border bg-muted/40'
                    : 'mr-6 border border-border bg-card')
                }
              >
                <p className="mb-2 text-xs font-medium text-muted-foreground">
                  {m.role === 'human'
                    ? '我 · 讲解'
                    : m.role === 'help_request'
                      ? '我 · 请求帮助'
                      : m.role === 'tutor'
                        ? '津津 · 学习帮助'
                        : session.student.name + ' · AI 学生'}
                  {m.assisted ? ' · 使用帮助后' : ''}
                </p>
                <MarkdownRenderer content={m.text} />
              </article>
            ))}
            <div ref={bottom} />
          </section>
          {working && (
            <p role="status" className="text-sm text-muted-foreground">
              {session.latest_request?.stage === 'follow_up'
                ? '正在准备 AI 学生的追问'
                : session.latest_request?.stage === 'help'
                  ? '津津正在准备一小步帮助'
                  : '正在理解并检查你的讲解'}
              ，原话已保存；可暂停后稍后继续。
            </p>
          )}
          {session.latest_request?.retryable && session.state === 'active' && !busy && (
            <div className="rounded-xl border border-border p-3 text-sm">
              <p>{session.latest_request.error || '上次生成中断，可接着完成'}</p>
              <button
                className={button + ' mt-2'}
                onClick={() =>
                  void send(session.latest_request!.kind, {
                    id: session.latest_request!.id,
                    text: session.latest_request!.text,
                  })
                }
              >
                重试上次生成
              </button>
            </div>
          )}
          {session.state === 'active' && !session.stale && (
            <form
              onSubmit={e => {
                e.preventDefault()
                void send('explain')
              }}
              className="sticky bottom-0 rounded-2xl border border-border bg-background p-3 shadow-sm"
            >
              <label className="sr-only" htmlFor="teach-explanation">
                我的讲解
              </label>
              <textarea
                id="teach-explanation"
                maxLength={6000}
                rows={3}
                placeholder="用自己的话讲给 AI 学生听，也可以回答它刚才的问题…"
                value={text}
                disabled={!!working || session.latest_request?.retryable}
                onChange={e => setText(e.target.value)}
                className="block w-full resize-y bg-transparent p-2 outline-none"
              />
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={!!working || session.latest_request?.retryable}
                  className={button}
                  onClick={() => void send('hint')}
                >
                  给个提示
                </button>
                <button
                  type="button"
                  disabled={!!working || session.latest_request?.retryable}
                  className={button}
                  onClick={() => void send('example')}
                >
                  换个例子
                </button>
                <button
                  type="submit"
                  disabled={!!working || !text.trim() || session.latest_request?.retryable}
                  className={button + ' ml-auto bg-primary text-primary-foreground'}
                >
                  发送讲解
                </button>
              </div>
            </form>
          )}
          {session.report && (
            <section
              aria-label="讲解回顾"
              className="rounded-2xl border border-border bg-card p-5 space-y-4"
            >
              <h2 className="text-xl font-semibold">这次讲解的回顾</h2>
              <p className="text-sm text-muted-foreground">{session.report.summary}</p>
              <p className="text-sm">
                讲解 {session.report.human_turn_count} 次 ·{' '}
                {session.report.assisted ? '有学习帮助' : '未记录学习帮助'}
              </p>
              <div className="flex flex-wrap gap-2">
                {session.report.dimensions?.map(d => (
                  <span
                    key={d.name}
                    className="rounded-full border border-border px-3 py-1 text-xs"
                  >
                    {d.name} · {d.observed ? '已有反馈' : '尚未观察'}
                  </span>
                ))}
              </div>
              <div className="space-y-3">
                {session.report.claims.map((c, i) => (
                  <div key={i} className="rounded-xl border border-border p-3 text-sm">
                    <p className="font-medium">
                      第{humanOrder(c.human_turn_ids[c.human_turn_ids.length - 1])}次讲解 ·{' '}
                      {dimensions[c.dimension]} · {statuses[c.status]}
                    </p>
                    <p className="mt-2">{c.observation}</p>
                    <div className="mt-2 flex flex-wrap gap-3">
                      {c.human_turn_ids.map(id => (
                        <a key={id} href={'#teach-turn-' + id} className="text-primary underline">
                          查看讲解原话 {humanOrder(id)}
                        </a>
                      ))}
                    </div>
                    {!!c.source_ids.length && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        资料依据：{(c.source_names || c.source_ids).join('、')}
                      </p>
                    )}
                  </div>
                ))}
              </div>
              <p className="text-sm">{session.report.next}</p>
              <div className="flex flex-wrap gap-3">
                <Link
                  href={scopedUrl(
                    '/learning/projects/' +
                      encodeURIComponent(pathId) +
                      '/checks?objective=' +
                      encodeURIComponent(session.objective_id)
                  )}
                  className={button + ' bg-primary text-primary-foreground'}
                >
                  用新任务独立核查
                </Link>
                <button className={button} onClick={tutor}>
                  和津津继续学习
                </button>
              </div>
            </section>
          )}
        </div>
      )}
    </LearningShell>
  )
}
