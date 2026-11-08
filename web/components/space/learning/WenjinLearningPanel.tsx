'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { masteryTopicRoute } from '@/lib/learning-routes'
import { useTranslation } from 'react-i18next'
import { apiFetch, apiUrl } from '@/lib/api'

type Ability = 'recognition' | 'explanation' | 'application' | 'transfer'
type Evidence = {
  id: string
  name: string
  required: Ability[]
  achieved: Ability[]
  missing: Ability[]
  attempts: number
  independent_successes: number
  assisted_successes: number
}
type TutorState = {
  enabled: boolean
  complete: boolean
  claimed_complete?: boolean
  objective: { id: string; name: string } | null
  decision: { reason: string; evidence_kind: Ability; evidence: Evidence } | null
  support_level: number
  objectives: Evidence[]
  reflections: { text: string; timestamp: number }[]
  reviews: { knowledge_point_id: string; due_at: number }[]
  prior_learning?: { path_id: string; objective: string; updated_at: number }[]
  sources: { id: string; title: string; url: string; excerpt: string }[]
  strategy: {
    consent?: boolean
    status?: string
    active?: { version: string }
    previous?: { version: string }
    candidate?: { version: string; status: string }
    evaluation?: {
      baseline: { independent_n: number; delayed_n: number }
      candidate: { independent_n: number; delayed_n: number }
    }
    history?: { event: string; at: number; version?: string }[]
  }
}

export function WenjinLearningPanel({
  pathId, revision, streaming, onSend,
}: {
  pathId: string
  revision: number
  streaming: boolean
  onSend: (message: string) => unknown
}) {
  const { t } = useTranslation()
  const [state, setState] = useState<TutorState | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const endpoint = '/api/mastery-paths/topics/' + encodeURIComponent(pathId) + '/wenjin'
  const label = (ability: Ability) => t('wenjinTutor.' + ability)

  useEffect(() => {
    if (streaming) return
    const controller = new AbortController()
    setError('')
    async function read() {
      try {
        let response = await apiFetch(apiUrl(endpoint), { signal: controller.signal })
        if (!response.ok) throw new Error(t('wenjinTutor.loadError'))
        let data: TutorState = await response.json()
        if (!data.enabled) {
          response = await apiFetch(apiUrl(endpoint + '/enable'), { method: 'POST', signal: controller.signal })
          if (!response.ok) throw new Error(t('wenjinTutor.loadError'))
          data = await response.json()
        }
        if (!controller.signal.aborted) setState(data)
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : t('wenjinTutor.loadError'))
      }
    }
    void read()
    return () => controller.abort()
  }, [endpoint, revision, streaming, reload, t])

  async function change(path: string, method: string, body?: { enabled: boolean }) {
    const previous = state
    if (path === '/consent' && body) {
      setState(current => current ? {
        ...current, strategy: { ...current.strategy, consent: body.enabled },
      } : current)
    }
    setBusy(true)
    setError('')
    try {
      const response = await apiFetch(apiUrl(endpoint + path), {
        method,
        headers: { 'Content-Type': 'application/json' },
        ...(body ? { body: JSON.stringify(body) } : {}),
      })
      if (!response.ok) throw new Error(t('wenjinTutor.saveError'))
      setState(await response.json())
    } catch (reason) {
      setState(previous)
      setError(reason instanceof Error ? reason.message : t('wenjinTutor.saveError'))
    } finally { setBusy(false) }
  }

  return (
    <details className="shrink-0 border-y border-border bg-card/60 text-sm">
      <summary className="cursor-pointer px-4 py-2.5 text-muted-foreground">
        {t('wenjinTutor.details')}
        {state?.objective && <span className="ml-3 text-foreground">{state.objective.name}</span>}
      </summary>
      <div className="max-h-[45dvh] space-y-4 overflow-y-auto px-4 pb-4">
        {error && <p role="alert" className="text-destructive">{error} <button type="button" className="underline" onClick={() => setReload(value => value + 1)}>{t('Retry')}</button></p>}
        {!state && !error && <p>{t('Loading…')}</p>}
        {state && <>
          <p>{state.decision?.reason || t(state.complete ? 'wenjinTutor.completed' : state.claimed_complete ? 'wenjinTutor.claimedComplete' : 'wenjinTutor.noEvidence')}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={streaming || busy} onClick={() => onSend(t('wenjinTutor.hintRequest'))} className="rounded-lg border border-border px-3 py-2 disabled:opacity-50">{t('wenjinTutor.hint')}</button>
            <button type="button" disabled={streaming || busy} onClick={() => onSend(t('wenjinTutor.explainRequest'))} className="rounded-lg border border-border px-3 py-2 disabled:opacity-50">{t('wenjinTutor.explain')}</button>
            <button type="button" disabled={streaming || busy} onClick={() => onSend(t('wenjinTutor.reflectionRequest'))} className="rounded-lg border border-border px-3 py-2 disabled:opacity-50">{t('wenjinTutor.reflect')}</button>
            <Link href={masteryTopicRoute(pathId)} aria-disabled={streaming}
              onClick={event => { if (streaming) event.preventDefault() }}
              className="rounded-lg border border-border px-3 py-2 aria-disabled:opacity-50">{t('wenjinTutor.pause')}</Link>
          </div>
          <p className="text-xs text-muted-foreground">{t('wenjinTutor.support')} {state.support_level} / 3</p>
          <details>
            <summary className="cursor-pointer font-medium">{t('wenjinTutor.evidence')}</summary>
            <ul className="mt-2 space-y-3">
              {state.objectives.map(objective => <li key={objective.id} className="rounded-lg border border-border p-3">
                <p className="font-medium">{objective.name}</p>
                <p className="mt-1 text-xs text-muted-foreground">{t('wenjinTutor.verified')} {objective.achieved.length ? objective.achieved.map(label).join(' · ') : t('wenjinTutor.none')}</p>
                <p className="mt-1 text-xs">{objective.missing.length ? <>{t('wenjinTutor.toCheck')} {objective.missing.map(label).join(' · ')}</> : t('wenjinTutor.confirmed')}</p>
                <p className="mt-1 text-xs text-muted-foreground">{t('wenjinTutor.independentCount', { count: objective.independent_successes })} · {t('wenjinTutor.assistedCount', { count: objective.assisted_successes })}</p>
              </li>)}
            </ul>
          </details>
          <details>
            <summary className="cursor-pointer font-medium">{t('wenjinTutor.sources')}</summary>
            {state.sources.length ? <ul className="mt-2 space-y-2">{state.sources.map(source => <li key={source.id}>
              <a href={source.url} target="_blank" rel="noopener noreferrer" className="text-primary underline">{source.title}</a>
              <p className="mt-1 text-xs text-muted-foreground">{source.excerpt.slice(0, 240)}</p>
            </li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">{t('wenjinTutor.optionalSources')}</p>}
          </details>
          <details>
            <summary className="cursor-pointer font-medium">{t('wenjinTutor.reflections')}</summary>
            {state.reflections.length ? <ul className="mt-2 space-y-2">{state.reflections.map(item => <li key={item.timestamp}>{item.text}</li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">{t('wenjinTutor.noReflection')}</p>}
            <p className="mt-2 text-xs text-muted-foreground">{t('wenjinTutor.reviewCount', { count: state.reviews.filter(item => item.due_at * 1000 <= Date.now()).length })}</p>
          </details>
          {!!state.prior_learning?.length && <details>
            <summary className="cursor-pointer font-medium">{t('wenjinTutor.prior')}</summary>
            <p className="mt-2 text-xs text-muted-foreground">{t('wenjinTutor.priorNote')}</p>
            <ul className="mt-2 space-y-1">{state.prior_learning.map(item => <li key={item.path_id + item.objective}>{item.objective}</li>)}</ul>
          </details>}
          <details>
            <summary className="cursor-pointer font-medium">{t('wenjinTutor.strategy')}</summary>
            <p className="mt-2 text-xs text-muted-foreground">{t('wenjinTutor.consentNote')}</p>
            <label className="mt-3 flex items-start gap-2">
              <input type="checkbox" checked={Boolean(state.strategy.consent)} disabled={busy || streaming} onChange={event => void change('/consent', 'PUT', { enabled: event.target.checked })} />
              <span>{t('wenjinTutor.consent')}</span>
            </label>
            <p className="mt-2 text-xs">{t('wenjinTutor.currentVersion')} {state.strategy.active?.version || 'baseline'}</p>
            <p className="mt-1 text-xs">{t('wenjinTutor.status')} {t('wenjinTutor.status.' + (state.strategy.status || 'paused'))}</p>
            {state.strategy.candidate && <p className="mt-1 text-xs">{t('wenjinTutor.candidate')} {state.strategy.candidate.version}</p>}
            {state.strategy.evaluation && <p className="mt-1 text-xs text-muted-foreground">{t('wenjinTutor.trials', { baseline: state.strategy.evaluation.baseline.independent_n, candidate: state.strategy.evaluation.candidate.independent_n })} · {t('wenjinTutor.delayedTrials', { baseline: state.strategy.evaluation.baseline.delayed_n, candidate: state.strategy.evaluation.candidate.delayed_n })}</p>}
            {state.strategy.previous && <button type="button" disabled={busy || streaming} onClick={() => void change('/rollback', 'POST')} className="mt-2 rounded-lg border border-border px-3 py-2">{t('wenjinTutor.rollback')}</button>}
            {!!state.strategy.history?.length && <ul className="mt-2 space-y-1 text-xs text-muted-foreground">{state.strategy.history.slice(-5).map((item, index) => <li key={index}>{t('wenjinTutor.event.' + item.event)} {item.version || ''}</li>)}</ul>}
          </details>
        </>}
      </div>
    </details>
  )
}
