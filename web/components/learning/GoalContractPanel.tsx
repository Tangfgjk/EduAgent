'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { projectEndpoint, projectRequest } from '@/lib/learning-project-api'

type Level = 'understand' | 'apply' | 'transfer'
type Contract = {
  version: number
  assessment_version: number
  level: Level
  summary: string
  time_budget: string
  confirmed_at: number
}
type Goal = {
  source_revision: number
  status: 'confirmed' | 'needs_confirmation'
  current: Contract | null
  suggested_summary: string
  suggested_time_budget?: string
  proposal: { level: Level; summary: string; reason: string } | null
  levels: {
    level: Level
    abilities: string[]
    minimum_distinct_tasks: number
    delayed_transfer_required: boolean
  }[]
  history: Contract[]
  progress: { total: number; demonstrated: number; model_feedback_used: boolean }
}
const button = 'rounded-lg border border-border px-3 py-2 text-sm disabled:opacity-50'
type Props = { pathId: string; onUpdated?: () => void }
export default function GoalContractPanel(props: Props) {
  return <GoalContractEditor key={props.pathId} {...props} />
}
function GoalContractEditor({ pathId, onUpdated }: Props) {
  const { t } = useTranslation()
  const [data, setData] = useState<Goal | null>(null)
  const [editing, setEditing] = useState(false)
  const [level, setLevel] = useState<Level | ''>('')
  const [summary, setSummary] = useState('')
  const [timeBudget, setTimeBudget] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  const request = useRef<{ payload: string; id: string } | null>(null)
  const endpoint = projectEndpoint(pathId) + '/goal-contract'
  const refresh = useCallback(async () => {
    const token = ++generation.current
    try {
      const value = await projectRequest<Goal>(endpoint)
      if (token !== generation.current) return
      setBusy(false)
      setData(value)
      setLevel(value.proposal?.level || value.current?.level || '')
      setSummary(value.suggested_summary ?? value.current?.summary ?? '')
      setTimeBudget(value.current?.time_budget ?? value.suggested_time_budget ?? '')
      setError('')
      setEditing(value.status !== 'confirmed')
    } catch (e) {
      if (token === generation.current)
        setError(e instanceof Error ? e.message : 'learningGoal.loadError')
    }
  }, [endpoint])
  useEffect(() => {
    void refresh()
    return () => {
      generation.current += 1
    }
  }, [refresh])
  const selected = data?.levels.find(item => item.level === level)
  async function confirm() {
    if (!data || !level || !summary.trim()) return
    const token = generation.current
    const payload = {
      source_revision: data.source_revision,
      level,
      summary: summary.trim(),
      time_budget: timeBudget.trim(),
    }
    const serialized = JSON.stringify(payload)
    if (request.current?.payload !== serialized)
      request.current = {
        payload: serialized,
        id:
          globalThis.crypto?.randomUUID?.() ||
          'goal-' + Date.now() + '-' + Math.random().toString(36).slice(2),
      }
    setBusy(true)
    setError('')
    try {
      const saved = await projectRequest<Goal>(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, request_id: request.current.id }),
      })
      if (token !== generation.current) return
      setData(saved)
      setEditing(false)
      request.current = null
      onUpdated?.()
    } catch (e) {
      if (token === generation.current)
        setError(e instanceof Error ? e.message : t('learningGoal.saveError'))
    } finally {
      if (token === generation.current) setBusy(false)
    }
  }
  return (
    <section
      aria-label={t('learningGoal.title')}
      className="my-4 rounded-2xl border border-primary/20 bg-primary/5 p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">{t('learningGoal.title')}</h2>
        {data?.status === 'confirmed' && !editing && (
          <button className={button} onClick={() => setEditing(true)}>
            {t('learningGoal.edit')}
          </button>
        )}
      </div>
      {error && (
        <div role="alert" className="my-3 text-sm text-destructive">
          {t(error)}
          <button className={button + ' ml-3'} disabled={busy} onClick={() => void refresh()}>
            {t('learningGoal.refresh')}
          </button>
        </div>
      )}
      {!data ? (
        <p className="mt-2 text-sm text-muted-foreground">{t('learningGoal.loading')}</p>
      ) : (
        <>
          <p className="mt-2 text-sm">
            {t(data.status === 'confirmed' ? 'learningGoal.confirmed' : 'learningGoal.pending')}
          </p>
          {data.proposal && (
            <p className="mt-2 text-sm text-muted-foreground">
              {t('learningGoal.proposal')} {data.proposal.summary} · {data.proposal.reason}
            </p>
          )}
          {editing ? (
            <form
              className="mt-4 space-y-4"
              onSubmit={e => {
                e.preventDefault()
                void confirm()
              }}
            >
              <fieldset disabled={busy}>
                <legend className="mb-2 text-sm font-medium">{t('learningGoal.level')}</legend>
                <div className="grid gap-2 sm:grid-cols-3">
                  {data.levels.map(item => (
                    <label
                      key={item.level}
                      className={
                        'cursor-pointer rounded-xl border p-3 text-sm ' +
                        (level === item.level ? 'border-primary bg-background' : 'border-border')
                      }
                    >
                      <input
                        type="radio"
                        aria-label={t('learningGoal.levels.' + item.level)}
                        name={'goal-level-' + pathId}
                        value={item.level}
                        checked={level === item.level}
                        onChange={() => setLevel(item.level)}
                      />
                      <span className="ml-2 font-medium">
                        {t('learningGoal.levels.' + item.level)}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {t('learningGoal.descriptions.' + item.level)}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <label className="block text-sm">
                {t('learningGoal.summary')}
                <textarea
                  maxLength={1500}
                  rows={2}
                  disabled={busy}
                  value={summary}
                  onChange={e => setSummary(e.target.value)}
                  className="mt-1 block w-full rounded-lg border border-border bg-background p-2"
                />
              </label>
              <label className="block text-sm">
                {t('learningGoal.time')}
                <input
                  maxLength={2000}
                  disabled={busy}
                  value={timeBudget}
                  onChange={e => setTimeBudget(e.target.value)}
                  className="mt-1 block w-full rounded-lg border border-border bg-background p-2"
                />
              </label>
              {selected && (
                <div className="rounded-xl bg-background p-3 text-sm">
                  <p className="font-medium">{t('learningGoal.criteria')}</p>
                  <p className="mt-1">
                    {selected.abilities.map(a => t('learningGoal.abilities.' + a)).join('、')} ·{' '}
                    {t('learningGoal.tasks', { count: selected.minimum_distinct_tasks })}
                  </p>
                  {selected.delayed_transfer_required && (
                    <p className="mt-1">{t('learningGoal.delayed')}</p>
                  )}
                  <p className="mt-1 text-muted-foreground">{t('learningGoal.independence')}</p>
                </div>
              )}
              <p className="text-xs text-muted-foreground">{t('learningGoal.changeRule')}</p>
              <div className="flex flex-wrap gap-2">
                <button
                  disabled={busy || !level || !summary.trim()}
                  className={button + ' bg-primary text-primary-foreground'}
                  type="submit"
                >
                  {t(busy ? 'learningGoal.saving' : 'learningGoal.confirm')}
                </button>
                {data.status === 'confirmed' && (
                  <button
                    type="button"
                    disabled={busy}
                    className={button}
                    onClick={() => {
                      setLevel(data.current!.level)
                      setSummary(data.current!.summary)
                      setTimeBudget(data.current!.time_budget)
                      setEditing(false)
                    }}
                  >
                    {t('learningGoal.cancel')}
                  </button>
                )}
              </div>
            </form>
          ) : (
            data.current && (
              <div className="mt-3 text-sm">
                <p className="font-medium">
                  {data.current.summary} · {t('learningGoal.levels.' + data.current.level)}
                </p>
                <p className="mt-2">
                  {t('learningGoal.progress', {
                    done: data.progress.demonstrated,
                    total: data.progress.total,
                  })}
                </p>
                {data.progress.model_feedback_used && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('learningGoal.modelFeedback')}
                  </p>
                )}
                <p className="mt-1 text-xs text-muted-foreground">
                  {t('learningGoal.supportRule')}
                </p>
              </div>
            )
          )}
          {!!data.history.length && (
            <details className="mt-3 text-xs text-muted-foreground">
              <summary className="cursor-pointer">{t('learningGoal.history')}</summary>
              <ol className="mt-2 space-y-2">
                {data.history
                  .slice(-5)
                  .reverse()
                  .map(item => (
                    <li key={item.version}>
                      v{item.version} · {item.summary} · {t('learningGoal.levels.' + item.level)}{' '}
                      <button
                        disabled={busy}
                        className="ml-2 underline"
                        onClick={() => {
                          setLevel(item.level)
                          setSummary(item.summary)
                          setTimeBudget(item.time_budget)
                          setEditing(true)
                        }}
                      >
                        {t('learningGoal.reuse')}
                      </button>
                    </li>
                  ))}
              </ol>
            </details>
          )}
        </>
      )}
    </section>
  )
}
