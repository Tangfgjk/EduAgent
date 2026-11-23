'use client'
import { useEffect, useRef, useState } from 'react'
import { projectRequest, projectEndpoint } from '@/lib/learning-project-api'
type Metrics = { log_loss: number | null; brier: number | null; n: number }
type Run = {
  id: string
  status: string
  created_at: number
  reason: string
  n: number
  sequences: number
  validation_metrics?: Metrics
  baseline_metrics?: Metrics
  parameters: Record<string, number>
  activated: boolean
}
type Job = {
  id: string
  status: string
  error?: string
  planning?: { action: string; reason: string }
  source_revision: number
}
const statuses: Record<string, string> = {
  insufficient_data: '数据不足',
  validated_candidate: '候选参数通过验证',
  rejected: '保留原参数',
  failed: '训练未收敛',
}
type Props = { pathId: string; revision: number; eligible: number; onUpdated?: () => void }
export default function PersonalTraining(props: Props) {
  return <TrainingPanel key={props.pathId} {...props} />
}
function TrainingPanel({ pathId, revision, eligible, onUpdated }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]),
    [runs, setRuns] = useState<Run[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  const [ready, setReady] = useState(false),
    [loadError, setLoadError] = useState(''),
    [refresh, setRefresh] = useState(0)
  const loadVersion = useRef(0)
  const endpoint = projectEndpoint(pathId) + '/diagnosis/training'
  const lastCompleted = useRef<string | null>(null)
  useEffect(() => {
    const done = jobs.find(j => j.status === 'completed')
    if (done && lastCompleted.current !== done.id) {
      lastCompleted.current = done.id
      onUpdated?.()
    }
  }, [jobs, onUpdated])
  const pending = jobs.some(j => j.status === 'queued' || j.status === 'running')
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const version = ++loadVersion.current
    async function poll() {
      try {
        const [models, tasks] = await Promise.all([
          projectRequest<Run[]>(endpoint),
          projectRequest<Job[]>(endpoint + '/jobs'),
        ])
        if (alive && version === loadVersion.current) {
          setRuns(models)
          setJobs(tasks)
          setReady(true)
          setLoadError('')
        }
      } catch (e) {
        if (alive && version === loadVersion.current)
          setLoadError(e instanceof Error ? e.message : String(e))
      } finally {
        if (alive && version === loadVersion.current && pending)
          timer = setTimeout(() => void poll(), 3000)
      }
    }
    void poll()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [endpoint, pending, refresh])
  async function train() {
    loadVersion.current++
    setBusy(true)
    setError('')
    try {
      const job = await projectRequest<Job>(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_revision: revision }),
      })
      setJobs(old => [job, ...old.filter(j => j.id !== job.id)])
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
      setRefresh(n => n + 1)
    }
  }
  async function retry(id: string) {
    loadVersion.current++
    setBusy(true)
    setError('')
    try {
      const job = await projectRequest<Job>(endpoint + '/jobs/' + id + '/retry', { method: 'POST' })
      setJobs(old => old.map(j => (j.id === id ? job : j)))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
      setRefresh(n => n + 1)
    }
  }
  async function rollback(id: string) {
    loadVersion.current++
    setBusy(true)
    setError('')
    try {
      await projectRequest(endpoint + '/rollback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: id }),
      })
      setRuns(await projectRequest<Run[]>(endpoint))
      onUpdated?.()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
      setRefresh(n => n + 1)
    }
  }
  return (
    <section className="rounded-2xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">个人诊断模型</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            只使用本人当前项目的独立作答，按时间顺序验证。当前可纳入 {eligible} 条。
          </p>
        </div>
        <button
          disabled={busy || pending || !ready || !!loadError}
          onClick={() => void train()}
          className="rounded-full bg-primary px-5 py-2 text-sm text-primary-foreground disabled:opacity-50"
        >
          {!ready ? '正在读取训练状态…' : busy || pending ? '训练与规划进行中…' : '训练并更新诊断'}
        </button>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        训练不会改变已记录的学习事实。候选参数的验证结果与当前生效状态分别展示。
      </p>
      {eligible < 50 && (
        <div role="note" className="mt-4 rounded-xl bg-muted/50 p-4 text-sm">
          <p className="font-medium">目前优先继续学习，暂不适合更新个人参数</p>
          <p className="mt-2">
            当前 {eligible} 条合格记录；训练至少需要 50 条、两个有效序列且每个序列至少 10
            次作答，并包含正误。数据不足不代表你没有掌握。
          </p>
          <p className="mt-2">
            仍可点击按钮检查：系统保留原参数，并由津津判断是否需要调整学习计划。
          </p>
        </div>
      )}
      {loadError && (
        <div role="alert" className="mt-3 text-sm text-destructive">
          {loadError}
          <button
            className="ml-3 rounded-lg border border-border px-3 py-1"
            onClick={() => setRefresh(n => n + 1)}
          >
            重新读取训练状态
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {error}
        </p>
      )}
      <div aria-live="polite">
        {jobs.map(job => (
          <div key={job.id} className="mt-3 rounded-xl bg-muted/50 p-3 text-sm">
            <p>
              {(
                {
                  queued: '任务已排队',
                  running: '正在训练或调整计划',
                  completed: '训练与规划已完成',
                  failed: '任务未完成，请查看原因',
                } as Record<string, string>
              )[job.status] || job.status}
            </p>
            {job.planning && (
              <p className="mt-1">
                {job.planning.action === 'keep' ? '保留当前计划' : '已调整学习顺序'}：
                {job.planning.reason}
              </p>
            )}
            {job.error && <p className="mt-1 text-destructive">{job.error}</p>}
            {job.status === 'failed' && (
              <button
                disabled={busy}
                className="mt-2 rounded-full border border-border px-3 py-1"
                onClick={() => void retry(job.id)}
              >
                重试任务
              </button>
            )}
          </div>
        ))}
        {ready && !loadError && !runs.length && (
          <p className="py-6 text-sm text-muted-foreground">暂无训练记录，诊断仍采用默认参数。</p>
        )}
        {runs.map(run => (
          <article key={run.id} className="mt-4 rounded-xl border border-border p-4">
            <div className="flex flex-wrap justify-between gap-2">
              <strong>{statuses[run.status] || run.status}</strong>
              <span className="text-xs text-muted-foreground">
                {new Date(run.created_at * 1000).toLocaleString()}
              </span>
            </div>
            <p className="mt-2 text-sm">{run.reason}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              {run.n} 条记录 · {run.sequences} 个序列 ·{' '}
              {run.activated ? '已启用' : '未启用候选参数'}
            </p>
            {run.validation_metrics && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                {(
                  [
                    ['Log loss', 'log_loss'],
                    ['Brier', 'brier'],
                  ] as const
                ).map(([name, key]) => (
                  <div key={key} className="rounded-lg bg-muted p-3">
                    <p className="text-xs">{name}（越低越好）</p>
                    <p className="mt-1 font-medium">
                      {run.baseline_metrics?.[key]?.toFixed(4)} →{' '}
                      {run.validation_metrics?.[key]?.toFixed(4)}
                    </p>
                  </div>
                ))}
              </div>
            )}
            {run.activated && (
              <button
                disabled={busy || pending}
                onClick={() => void rollback(run.id)}
                className="mt-3 rounded-full border border-border px-4 py-2 text-sm disabled:opacity-50"
              >
                回退到上一模型
              </button>
            )}
            <details className="mt-3 text-xs">
              <summary className="cursor-pointer">参数与版本</summary>
              <pre className="mt-2 whitespace-pre-wrap">
                {JSON.stringify({ id: run.id, parameters: run.parameters }, null, 2)}
              </pre>
            </details>
          </article>
        ))}
      </div>
    </section>
  )
}
