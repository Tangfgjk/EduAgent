'use client'
import { useEffect, useRef, useState } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { LearningShell } from './LearningShell'
import SlideCanvas, { type Element } from './SlideCanvas'
import MarkdownRenderer from '@/components/common/MarkdownRenderer'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { projectEndpoint, projectRequest } from '@/lib/learning-project-api'
import { scopedUrl } from '@/lib/workspace-scope'

type Visual = {
  title: string
  content: string
  layout: string
  panels: { heading: string; text: string }[]
  formula: string
  notes: string
  spoken_script: string
  lines: number[][]
}
type Page = {
  id: string
  title: string
  content: string
  narration: string
  steps: string[]
  kind: string
  visual?: Visual
  canvas?: Element[]
}
type Draft = {
  id: string
  revision: number
  parent_id: string
  parent_version: number
  state: string
  page_order_changed?: boolean
  selected_page_id?: string
  pages: Page[]
  changed_page_ids: string[]
}
type Form = {
  title: string
  content: string
  narration: string
  steps: string
  layout: string
  panels: { heading: string; text: string }[]
  formula: string
  notes: string
  spoken_script: string
  lines: string
}
function values(p: Page): Form {
  return {
    title: p.title,
    content: p.content,
    narration: p.narration || '',
    steps: (p.steps || []).join('\n'),
    layout: p.visual?.layout || 'concept',
    panels: structuredClone(p.visual?.panels || []),
    formula: p.visual?.formula || '',
    notes: p.visual?.notes || '',
    spoken_script: p.visual?.spoken_script || '',
    lines: (p.visual?.lines || []).map(l => l.join(', ')).join('\n'),
  }
}
const button =
  'rounded-xl border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-40'
const input = 'mt-1 w-full rounded-xl border border-border bg-background p-3 text-sm'
export default function ClassroomEditor() {
  const { pathId, cid } = useParams<{ pathId: string; cid: string }>(),
    router = useRouter(),
    search = useSearchParams()
  const requested = search.get('scene')
  const base = projectEndpoint(pathId) + '/classrooms/' + encodeURIComponent(cid)
  const [draft, setDraft] = useState<Draft | null>(null),
    [selected, setSelected] = useState(''),
    [form, setForm] = useState<Form | null>(null)
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [preview, setPreview] = useState(false),
    [reason, setReason] = useState('调整课件讲解内容'),
    [discard, setDiscard] = useState(false)
  const inflight = useRef(false)
  const page = draft?.pages.find(p => p.id === selected)
  const dirty = !!page && !!form && JSON.stringify(form) !== JSON.stringify(values(page))
  useEffect(() => {
    let live = true
    void projectRequest<Draft>(base + '/editor', { method: 'POST' })
      .then(d => {
        if (!live) return
        const p =
          d.pages.find(p => p.id === requested && p.kind !== 'quiz') ||
          d.pages.find(p => p.kind !== 'quiz')
        setDraft(d)
        setSelected(p?.id || '')
        setForm(p ? values(p) : null)
      })
      .catch(e => {
        if (live) setError(e instanceof Error ? e.message : '草稿加载失败')
      })
    return () => {
      live = false
    }
  }, [base, requested])
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  function change<K extends keyof Form>(key: K, value: Form[K]) {
    setForm(old => (old ? { ...old, [key]: value } : old))
    setNotice('')
    setPreview(false)
  }
  async function run(fn: () => Promise<void>) {
    if (inflight.current) return
    inflight.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作未完成，请重试')
    } finally {
      inflight.current = false
      setBusy(false)
    }
  }
  function back() {
    if (dirty) {
      setError('本页修改尚未保存，请先保存草稿，或放弃本页修改。')
      return
    }
    router.push(
      scopedUrl(
        '/learning/projects/' +
          encodeURIComponent(pathId) +
          '/classroom?classroom=' +
          encodeURIComponent(cid) +
          '&scene=' +
          encodeURIComponent(selected)
      )
    )
  }
  async function save() {
    if (!draft || !page || !form) throw Error('草稿尚未加载')
    const original = values(page),
      keys: (keyof Form)[] = page.visual
        ? ['title', 'content', 'layout', 'panels', 'formula', 'notes', 'spoken_script', 'lines']
        : [
            'title',
            'content',
            'narration',
            ...(page.kind === 'whiteboard' ? ['steps' as const] : []),
          ]
    const changes: Record<string, unknown> = {}
    for (const key of keys)
      if (JSON.stringify(form[key]) !== JSON.stringify(original[key])) {
        if (key === 'lines') {
          const lines = form.lines.trim()
            ? form.lines
                .trim()
                .split('\n')
                .map(row => row.split(/[,，]/).map(x => (x.trim() ? Number(x.trim()) : NaN)))
            : []
          if (lines.some(line => line.length !== 3 || line.some(n => !Number.isFinite(n))))
            throw Error('每行填写三个有限数 a,b,c，例如 1,1,2。')
          changes.lines = lines
        } else if (key === 'steps')
          changes.steps = form.steps
            .split('\n')
            .map(x => x.trim())
            .filter(Boolean)
        else changes[key] = form[key]
      }
    if (!Object.keys(changes).length) return draft
    const d = await projectRequest<Draft>(base + '/editor/' + draft.id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: draft.revision, page_id: selected, changes }),
    })
    setDraft(d)
    setForm(values(d.pages.find(p => p.id === selected)!))
    return d
  }
  async function arrange(action: 'duplicate' | 'move' | 'delete', position?: number) {
    if (!draft || dirty) throw Error('请先保存或放弃本页修改。')
    const d = await projectRequest<Draft>(base + '/editor/' + draft.id + '/pages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: draft.revision, action, page_id: selected, position }),
    })
    const p = d.pages.find(p => p.id === d.selected_page_id)!
    setDraft(d)
    setSelected(p.id)
    setForm(values(p))
    setPreview(false)
    setNotice(
      action === 'duplicate'
        ? '已复制为新页，可将较长内容拆开讲解。'
        : action === 'delete'
          ? '已从草稿移除此页，原课堂保留。'
          : '已保存页面顺序。'
    )
  }
  async function publish() {
    const d = dirty ? await save() : draft
    if (!d) return
    if (!reason.trim()) throw Error('请简短说明这次修改。')
    const course = await projectRequest<{ id: string }>(base + '/editor/' + d.id + '/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: d.revision, reason: reason.trim() }),
    })
    router.push(
      scopedUrl(
        '/learning/projects/' +
          encodeURIComponent(pathId) +
          '/classroom?classroom=' +
          encodeURIComponent(course.id) +
          '&scene=' +
          encodeURIComponent(selected)
      )
    )
  }
  return (
    <LearningShell title="编辑学习课件" subtitle="先保存草稿，再发布自己的新版本" back={false}>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <button className={button} disabled={busy} onClick={back}>
          ← 返回课堂
        </button>
        <span role="status" className="text-sm text-muted-foreground">
          {busy
            ? '正在保存…'
            : dirty
              ? '本页有未保存修改'
              : draft
                ? '草稿已保存 · 刷新可继续编辑'
                : '正在读取草稿…'}
        </span>
      </div>
      {error && (
        <p role="alert" className="mb-4 rounded-xl bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mb-4 rounded-xl bg-primary/5 p-3 text-sm">
          {notice}
        </p>
      )}
      {draft && form && page && (
        <>
          <p className="mb-5 text-sm text-muted-foreground">
            基于第 {draft.parent_version}{' '}
            版。发布会建立新的课件版本；旧课堂的作答、学习位置和媒体保留。修改的内容标为待核查，不直接改变学习诊断。
          </p>
          <div className="grid items-start gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
            <details open className="rounded-2xl border border-border bg-card p-4">
              <summary className="cursor-pointer font-medium">
                课件页面 · {draft.pages.length}
              </summary>
              <div className="mt-3 max-h-[65vh] space-y-2 overflow-auto">
                {draft.pages.map((p, i) => (
                  <button
                    key={p.id}
                    disabled={busy || dirty || p.kind === 'quiz'}
                    aria-current={p.id === selected ? 'page' : undefined}
                    className={
                      button +
                      ' w-full text-left ' +
                      (p.id === selected ? 'bg-primary text-primary-foreground' : '')
                    }
                    onClick={() => {
                      setSelected(p.id)
                      setForm(values(p))
                      setPreview(false)
                      setError('')
                      setNotice('')
                    }}
                  >
                    {i + 1}. {p.title}
                    {p.kind === 'quiz'
                      ? ' · 测验只读'
                      : draft.changed_page_ids.includes(p.id)
                        ? ' · 已修改'
                        : ''}
                  </button>
                ))}
              </div>
              {dirty && (
                <p className="mt-3 text-xs text-muted-foreground">
                  保存或放弃本页修改后，可切换页面。
                </p>
              )}
            </details>
            <section className="min-w-0 rounded-2xl border border-border bg-card p-5">
              <div className="mb-4 flex flex-wrap gap-2">
                <button
                  className={button}
                  disabled={busy || dirty}
                  onClick={() => void run(() => arrange('duplicate'))}
                >
                  复制为新页
                </button>
                <button
                  className={button}
                  disabled={busy || dirty || draft.pages[0].id === selected}
                  onClick={() =>
                    void run(() =>
                      arrange('move', draft.pages.findIndex(p => p.id === selected) - 1)
                    )
                  }
                >
                  上移
                </button>
                <button
                  className={button}
                  disabled={busy || dirty || draft.pages.at(-1)?.id === selected}
                  onClick={() =>
                    void run(() =>
                      arrange('move', draft.pages.findIndex(p => p.id === selected) + 1)
                    )
                  }
                >
                  下移
                </button>
                <button
                  className={button}
                  disabled={busy || dirty || draft.pages.filter(p => p.kind !== 'quiz').length <= 1}
                  onClick={() => void run(() => arrange('delete'))}
                >
                  从草稿移除此页
                </button>
              </div>
              <div className="mb-5 flex flex-wrap items-center gap-3">
                <button
                  className={button}
                  disabled={busy}
                  aria-pressed={!preview}
                  onClick={() => setPreview(false)}
                >
                  编辑内容
                </button>
                <button
                  className={button}
                  disabled={busy || dirty}
                  aria-pressed={preview}
                  onClick={() => setPreview(true)}
                >
                  预览已保存草稿
                </button>
              </div>
              {preview ? (
                <>
                  <h2 className="mb-4 text-xl font-semibold">{page.title}</h2>
                  {page.canvas ? (
                    <div className="[&_svg]:min-w-0">
                      <SlideCanvas elements={page.canvas} title={page.title} />
                    </div>
                  ) : (
                    <MarkdownRenderer
                      content={page.content}
                  allowHtml={false}
                      className="prose dark:prose-invert max-w-none"
                    />
                  )}
                </>
              ) : (
                <fieldset disabled={busy} className="space-y-4">
                  <label className="block text-sm">
                    页面标题
                    <input
                      className={input}
                      value={form.title}
                      maxLength={page.visual ? 30 : 120}
                      onChange={e => change('title', e.target.value)}
                    />
                  </label>
                  <label className="block text-sm">
                    正文说明
                    <textarea
                      className={input}
                      rows={page.visual ? 3 : 8}
                      value={form.content}
                      maxLength={page.visual ? 180 : 12000}
                      onChange={e => change('content', e.target.value)}
                    />
                  </label>
                  {page.visual ? (
                    <>
                      <label className="block text-sm">
                        页面版式
                        <select
                          className={input}
                          value={form.layout}
                          onChange={e => change('layout', e.target.value)}
                        >
                          {Object.entries({
                            concept: '概念讲解',
                            comparison: '对照辨析',
                            process: '步骤流程',
                            example: '例题讲解',
                            coordinate: '坐标图',
                          }).map(([id, name]) => (
                            <option value={id} key={id}>
                              {name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div>
                        <h2 className="text-sm font-medium">讲解区</h2>
                        {form.panels.map((panel, i) => (
                          <div className="my-3 rounded-xl border border-border p-3" key={i}>
                            <label className="block text-sm">
                              第{i + 1}区标题
                              <input
                                className={input}
                                maxLength={18}
                                value={panel.heading}
                                onChange={e =>
                                  change(
                                    'panels',
                                    form.panels.map((p, n) =>
                                      n === i ? { ...p, heading: e.target.value } : p
                                    )
                                  )
                                }
                              />
                            </label>
                            <label className="mt-2 block text-sm">
                              第{i + 1}区说明
                              <textarea
                                className={input}
                                rows={3}
                                maxLength={140}
                                value={panel.text}
                                onChange={e =>
                                  change(
                                    'panels',
                                    form.panels.map((p, n) =>
                                      n === i ? { ...p, text: e.target.value } : p
                                    )
                                  )
                                }
                              />
                            </label>
                            <button
                              type="button"
                              className={button + ' mt-2'}
                              disabled={form.panels.length <= 1}
                              onClick={() =>
                                change(
                                  'panels',
                                  form.panels.filter((_, n) => i !== n)
                                )
                              }
                            >
                              移除第{i + 1}区
                            </button>
                          </div>
                        ))}
                        <button
                          type="button"
                          className={button}
                          disabled={form.panels.length >= (form.layout === 'coordinate' ? 2 : 4)}
                          onClick={() =>
                            change('panels', [
                              ...form.panels,
                              { heading: '新的重点', text: '请填写说明' },
                            ])
                          }
                        >
                          添加讲解区
                        </button>
                      </div>
                      <label className="block text-sm">
                        公式（LaTeX，可留空）
                        <textarea
                          className={input + ' font-mono'}
                          rows={2}
                          maxLength={250}
                          value={form.formula}
                          onChange={e => change('formula', e.target.value)}
                        />
                      </label>
                      {form.layout === 'coordinate' && (
                        <label className="block text-sm">
                          坐标直线（每行 a,b,c 表示 ax+by=c）
                          <textarea
                            className={input}
                            rows={3}
                            value={form.lines}
                            onChange={e => change('lines', e.target.value)}
                          />
                        </label>
                      )}
                    </>
                  ) : (
                    page.kind === 'whiteboard' && (
                      <label className="block text-sm">
                        板书步骤（每行一步）
                        <textarea
                          className={input}
                          rows={6}
                          value={form.steps}
                          onChange={e => change('steps', e.target.value)}
                        />
                      </label>
                    )
                  )}
                  <details className="rounded-xl border border-border p-4">
                    <summary className="cursor-pointer text-sm font-medium">配音讲稿与补充</summary>
                    <p className="my-3 text-xs text-muted-foreground">
                      修改正文、公式或讲解区后，未同步修改的旧讲稿会清空；留空时按新版页面内容朗读。发布后需为新版本生成配音和视频。
                    </p>
                    <label className="block text-sm">
                      配音讲稿
                      <textarea
                        className={input}
                        rows={7}
                        maxLength={page.visual ? 1800 : 12000}
                        value={page.visual ? form.spoken_script : form.narration}
                        onChange={e =>
                          change(page.visual ? 'spoken_script' : 'narration', e.target.value)
                        }
                      />
                    </label>
                    {page.visual && (
                      <label className="mt-3 block text-sm">
                        补充备注
                        <textarea
                          className={input}
                          rows={4}
                          maxLength={3000}
                          value={form.notes}
                          onChange={e => change('notes', e.target.value)}
                        />
                      </label>
                    )}
                  </details>
                </fieldset>
              )}
              <div className="mt-5 flex flex-wrap gap-3">
                <button
                  className={button + ' bg-primary text-primary-foreground'}
                  disabled={busy || !dirty}
                  onClick={() =>
                    void run(async () => {
                      await save()
                      setPreview(true)
                      setNotice('本页草稿已保存。预览与后续导出将使用保存的内容。')
                    })
                  }
                >
                  保存并预览
                </button>
                <button
                  className={button}
                  disabled={busy || !dirty}
                  onClick={() => {
                    setForm(values(page))
                    setError('')
                    setNotice('已放弃本页尚未保存的修改。')
                  }}
                >
                  放弃本页修改
                </button>
              </div>
            </section>
          </div>
          <section className="mt-5 rounded-2xl border border-border bg-card p-5">
            <h2 className="font-semibold">发布新的课件版本</h2>
            <p className="my-2 text-sm text-muted-foreground">
              已保存修改或增删 {draft.changed_page_ids.length} 页。
              {draft.page_order_changed ? ' 页面顺序已调整。' : ''}
              仅建立本项目的新版本；修改后的内容仍需自行核查，旧版本可随时打开。
            </p>
            <label className="block text-sm">
              修改说明
              <input
                className={input}
                maxLength={500}
                value={reason}
                onChange={e => setReason(e.target.value)}
              />
            </label>
            <div className="mt-4 flex flex-wrap gap-3">
              <button
                className={button + ' bg-primary text-primary-foreground'}
                disabled={
                  busy || (!dirty && !draft.changed_page_ids.length && !draft.page_order_changed)
                }
                onClick={() => void run(publish)}
              >
                发布新版本并打开
              </button>
              <button className={button} disabled={busy} onClick={() => setDiscard(true)}>
                放弃整个草稿
              </button>
            </div>
          </section>
          <ConfirmDialog
            open={discard}
            title="放弃整个课件草稿？"
            confirmLabel="放弃草稿"
            cancelLabel="继续编辑"
            tone="danger"
            busy={busy}
            onCancel={() => setDiscard(false)}
            onConfirm={() =>
              void run(async () => {
                await projectRequest(base + '/editor/' + draft.id + '/discard', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ revision: draft.revision }),
                })
                router.push(
                  scopedUrl(
                    '/learning/projects/' +
                      encodeURIComponent(pathId) +
                      '/classroom?classroom=' +
                      encodeURIComponent(cid)
                  )
                )
              })
            }
          >
            <p>将结束本次草稿编辑，原课件和学习记录保留。</p>
          </ConfirmDialog>
        </>
      )}
    </LearningShell>
  )
}
