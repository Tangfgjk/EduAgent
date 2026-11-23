'use client'
import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { LearningShell } from './LearningShell'
import { projectRequest, projectEndpoint, learningProjectRoute } from '@/lib/learning-project-api'
import { masterySessionRoute as sessionRoute } from '@/lib/learning-routes'
import { masterySessionRoute as openingRoute, MASTERY_OPENING_SCOPE } from '@/lib/mastery-mode'
import { setPendingPrompt } from '@/lib/pending-prompt'
import { scopedUrl } from '@/lib/workspace-scope'
type Evidence = {
  question_id: string
  phase: 'initial' | 'review' | 'transfer'
  result: 'correct' | 'partial' | 'incorrect'
  independent: boolean
  session_id: string
  feedback: string
  model_feedback: boolean
  at: number
}
type Row = {
  objective_id: string
  objective_name: string
  module_id: string
  phase: string
  phase_label: string
  status: string
  due_at: number | null
  completed_cycles: number
  feedback: string
  scope_changed: boolean
  evidence_invalidated: boolean
  evidence: Evidence[]
}
type Data = { objectives: Row[]; due_count: number; rules: string[] }
const button = 'rounded-xl border border-border px-4 py-2 text-sm disabled:opacity-40'
export function CheckCycles({ pathId, compact = false }: { pathId: string; compact?: boolean }) {
  const router = useRouter(),
    search = useSearchParams()
  const [data, setData] = useState<Data | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState('')
  const [filter, setFilter] = useState(search.get('objective') || '__next__')
  const load = useCallback(async () => {
    try {
      setData(await projectRequest<Data>(projectEndpoint(pathId) + '/checks'))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败，请重试')
    }
  }, [pathId])
  useEffect(() => {
    void load()
    const refresh = () => void load()
    window.addEventListener('focus', refresh)
    const timer = setInterval(refresh, 20000)
    return () => {
      window.removeEventListener('focus', refresh)
      clearInterval(timer)
    }
  }, [load])
  async function start(row: Row) {
    setBusy(row.objective_id)
    setError('')
    try {
      const r = await projectRequest<{
        session_id: string
        prompt?: string
        pending: boolean
        message?: string
      }>(projectEndpoint(pathId) + '/checks/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ objective_id: row.objective_id }),
      })
      if (r.pending) {
        if (r.session_id) router.push(sessionRoute(pathId, r.session_id))
        else throw new Error(r.message || '请在原对话继续未完成题目')
        return
      }
      setPendingPrompt(r.prompt || '', r.session_id ? 'mastery_path' : MASTERY_OPENING_SCOPE)
      router.push(
        r.session_id
          ? sessionRoute(pathId, r.session_id)
          : openingRoute(pathId, row.phase === 'initial' ? 'study' : 'review')
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : '启动失败，请重试')
    } finally {
      setBusy('')
    }
  }
  const available = (data?.objectives || []).filter(
    r => !search.get('module') || r.module_id === search.get('module')
  )
  const recommended =
    available.find(r => r.status === 'in_progress') ||
    available.find(r => r.phase === 'review' && r.status === 'ready') ||
    available.find(r => r.status === 'ready') ||
    available[0]
  const rows = available.filter(r =>
    filter === '__next__'
      ? r.objective_id === recommended?.objective_id
      : !filter || r.objective_id === filter
  )
  return (
    <section className="space-y-5" aria-label="独立核查与复习">
      <div className="rounded-2xl border border-border bg-card p-5">
        <h2 className="text-xl font-semibold">独立核查与复习</h2>
        <p className="mt-3 text-sm text-muted-foreground">
          学完一章后，用新任务检查理解；过一段时间再复习，最后换个情境验证能否应用。题目和反馈在津津陪学中完成，未完成题目会接着做。
        </p>
        <p className="mt-3 text-sm">独立核查 → 延迟复习 → 迁移验证 → 下次复习</p>
        {data && (
          <p className="mt-3 text-sm text-primary">当前到期复习：{data.due_count} 个知识点</p>
        )}
      </div>
      {error && (
        <p role="alert" className="rounded-xl border border-destructive/40 p-3 text-destructive">
          {error}
          <button className={button} onClick={() => void load()}>
            刷新状态
          </button>
        </p>
      )}
      {!data && !error && <p role="status">正在读取学习证据…</p>}
      {data && (
        <label className="block text-sm">
          知识点{' '}
          <select
            aria-label="筛选核查知识点"
            value={filter}
            onChange={e => setFilter(e.target.value)}
            className="ml-2 max-w-full rounded-lg border border-border bg-background p-2"
          >
            <option value="__next__">推荐下一步</option>
            <option value="">全部知识点</option>
            {data.objectives.map(r => (
              <option key={r.objective_id} value={r.objective_id}>
                {r.objective_name}
              </option>
            ))}
          </select>
        </label>
      )}
      {rows.map(row => (
        <article key={row.objective_id} className="rounded-2xl border border-border bg-card p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="font-semibold">{row.objective_name}</h3>
              <p className="mt-2 text-sm">
                下一步：{row.phase_label}
                {row.status === 'waiting'
                  ? ' · 尚未到期'
                  : row.status === 'in_progress'
                    ? ' · 进行中'
                    : ''}
              </p>
            </div>
            <button
              disabled={!!busy || row.status === 'waiting'}
              className={button + ' bg-primary text-primary-foreground'}
              onClick={() => void start(row)}
            >
              {busy === row.objective_id
                ? '正在接续…'
                : row.status === 'in_progress'
                  ? '继续当前任务'
                  : row.status === 'waiting'
                    ? '等待复习到期'
                    : '开始' + row.phase_label}
            </button>
          </div>
          {row.due_at && (
            <p className="mt-3 text-sm text-muted-foreground">
              复习时间：{new Date(row.due_at * 1000).toLocaleString('zh-CN')}
              。提前学习不替代本次延迟复习。
            </p>
          )}
          {!!row.completed_cycles && (
            <p className="mt-2 text-sm">
              已有 {row.completed_cycles} 轮迁移核查记录，后续仍会安排复习。
            </p>
          )}
          {row.feedback && (
            <p className="mt-3 rounded-xl bg-muted/40 p-3 text-sm">{row.feedback}</p>
          )}
          {(row.scope_changed || row.evidence_invalidated) && (
            <p className="mt-3 text-sm text-amber-700">
              目标或证据发生变化，已重新核对；旧记录保留，但不直接用于通过本轮。
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-4 text-sm">
            <Link
              className="text-primary underline"
              href={learningProjectRoute(pathId, row.module_id)}
            >
              回顾本章
            </Link>
            <Link
              className="text-primary underline"
              href={scopedUrl('/learning/projects/' + encodeURIComponent(pathId) + '/diagnosis')}
            >
              查看诊断依据
            </Link>
          </div>
          {!!row.evidence.length && (
            <details className="mt-4 text-sm">
              <summary className="cursor-pointer">核查记录与反馈（{row.evidence.length}）</summary>
              {row.evidence.map(e => (
                <div key={e.question_id} className="mt-3 rounded-lg border border-border p-3">
                  <p>
                    {{ initial: '独立核查', review: '延迟复习', transfer: '迁移验证' }[e.phase] ||
                      e.phase}{' '}
                    ·{' '}
                    {
                      { correct: '本次反馈通过', partial: '还需补充', incorrect: '需要巩固' }[
                        e.result
                      ]
                    }{' '}
                    · {e.independent ? '未记录帮助' : '使用过帮助'}
                  </p>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {new Date(e.at * 1000).toLocaleString('zh-CN')}
                  </p>
                  <p className="mt-2">{e.feedback}</p>
                  {e.model_feedback && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      模型评价，保留其不确定性，不等于已确认掌握或合格训练标签。
                    </p>
                  )}
                  {e.session_id && (
                    <Link
                      className="mt-2 inline-block text-primary underline"
                      href={sessionRoute(pathId, e.session_id)}
                    >
                      查看原题与对话
                    </Link>
                  )}
                </div>
              ))}
            </details>
          )}
        </article>
      ))}
      {data && !rows.length && (
        <p className="text-sm text-muted-foreground">
          暂无匹配知识点。请先确认项目大纲，或切换筛选。
        </p>
      )}
      {!compact && data && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">如何理解核查结果</summary>
          {data.rules.map(rule => (
            <p key={rule} className="mt-2">
              {rule}
            </p>
          ))}
        </details>
      )}
    </section>
  )
}
export default function IndependentChecks() {
  const { pathId } = useParams<{ pathId: string }>()
  return (
    <LearningShell title="核查与复习" subtitle="用真实作答接续学习" back={false}>
      <Link className="mb-5 inline-block text-primary" href={learningProjectRoute(pathId)}>
        ← 返回项目
      </Link>
      <CheckCycles pathId={pathId} />
    </LearningShell>
  )
}
