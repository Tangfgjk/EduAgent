'use client'

import dynamic from "next/dynamic"
import type { ImportImpact } from "@/lib/diagnostic-impact"
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { scopedUrl } from '@/lib/workspace-scope'
import Link from 'next/link'
import { masteryTopicRoute, masterySessionRoute } from '@/lib/learning-routes'
import { useTranslation } from 'react-i18next'
import { apiFetch, apiUrl } from '@/lib/api'

function DetailLoading(){const {t}=useTranslation();return <p role="status">{t('Loading…')}</p>}
const DiagnosticImpact=dynamic(()=>import('@/components/learning/DiagnosticImpact'),{ssr:false,loading:DetailLoading});

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
  resume_session_id?: string | null
  import_impact?: ImportImpact | null
  enabled: boolean
  complete: boolean
  claimed_complete?: boolean
  objective: { id: string; name: string } | null
  decision: { diagnostic_estimate?: {measurement_status: string; n_eligible: number; summary: string}; external_references?: {source_system:string;knowledge_name:string;observed_on:string;probability:number|null;relation:string;active_reference:boolean}[]; reason: string; reason_code?: string; policy_version?: string; preference?: string; source_revision?: number; evidence_kind: Ability; evidence: Evidence; prerequisite_suggestions?: {id:string;name:string;reason:string}[] } | null
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
  pathId, revision, streaming, onSend, helpHost, sessionId, showResume,
}: {
  pathId: string
  sessionId?: string | null
  showResume?: boolean
  helpHost?: HTMLElement | null
  revision: number
  streaming: boolean
  onSend: (message: string) => unknown
}) {
  const { t, i18n } = useTranslation()
  const zh=i18n.language.startsWith("zh")
  const [state, setState] = useState<TutorState | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const [detailsOpen,setDetailsOpen]=useState(false)
  const drawer = useRef<HTMLDialogElement>(null)
  const helpMenu = useRef<HTMLDetailsElement>(null)
  useEffect(() => { drawer.current?.close(); helpMenu.current?.removeAttribute('open') }, [pathId])
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

  async function choose(strategy: 'teach_first' | 'probe_first') {
    setBusy(true);setError('')
    try {
      const response=await apiFetch(apiUrl(endpoint+'/preference'),{
        method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({strategy}),
      })
      if(!response.ok)throw new Error(t('wenjinTutor.saveError'))
      setState(await response.json())
      onSend(strategy==='teach_first'
        ? (zh?'请先讲解当前目标，暂不做起点核查；之后再确认我的理解。若有待答题，请先和我确认如何处理。':'Please teach the current objective first and defer the initial probe. If a question is pending, confirm how to handle it with me.')
        : (zh?'我愿意先尝试一个针对当前缺口的小任务，请说明核查什么。若有待答题请先继续该题。':'I would like one small probe for the current evidence gap. Explain what it checks; continue any pending task first.'))
    } catch(reason) { setError(reason instanceof Error?reason.message:t('wenjinTutor.saveError')) }
    finally {setBusy(false)}
  }

  async function requestHelp(kind:string,message:string) {
    if(helpMenu.current)helpMenu.current.open=false
    setBusy(true);setError('')
    try {
      const response=await apiFetch(apiUrl(endpoint+'/help-requests'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,session_id:sessionId||'',request_id:crypto.randomUUID()})})
      if(!response.ok)throw new Error(zh?'帮助请求未保存，请重试。':'Could not save help request.')
      onSend(message)
    } catch(reason){setError(String(reason))}finally{setBusy(false)}
  }
  const helpToolbar = (
    <section aria-label={zh?'学习帮助':'Learning support'} className="shrink-0 border-b border-border bg-card px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-xs font-medium text-muted-foreground">{zh?'学习帮助':'Learning support'}</span>
        {[
          ['hint',zh?'给我提示':'Hint', zh?'请只给当前任务一个方向提示，先不要透露答案；明确告诉我用了什么帮助。':'Give one directional hint without revealing the answer.'],
          ['explain',zh?'换个讲法':'Explain differently',t('wenjinTutor.explainRequest')],

        ].map(([kind,name,message])=><button key={name} type="button" disabled={streaming||busy} onClick={()=>void requestHelp(kind,message)} className="rounded-full border border-border px-3 py-1.5 text-sm hover:bg-primary/10 disabled:opacity-50">{name}</button>)}
        <button type="button" disabled={streaming||busy} onClick={()=>void choose('probe_first')} className="rounded-full border border-border px-3 py-1.5 text-sm disabled:opacity-50">{zh?'独立试试':'Try independently'}</button>
        <Link className="px-2 py-1 text-xs text-primary underline" href={scopedUrl('/learning/projects/'+encodeURIComponent(pathId)+'/support')}>为什么这样帮助我</Link>
        <details ref={helpMenu} className="relative" onKeyDown={event => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus() } }}><summary className="cursor-pointer rounded-full border border-border px-3 py-1.5 text-sm">{zh?'更多帮助':'More help'}</summary><div className="absolute right-0 bottom-full z-30 mb-2 w-[min(18rem,85vw)] max-h-[50dvh] overflow-y-auto rounded-xl border border-border bg-background p-3 shadow-lg">
          <button type="button" disabled={streaming||busy} onClick={()=>{ if(helpMenu.current) helpMenu.current.open=false; void choose('teach_first') }} className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-muted disabled:opacity-50">{zh?'先听讲，稍后核查':'Teach first'}</button>
          {[
            ['example','理解支持 · 举个例子','请用一个不同的具体例子解释当前概念，再让我说明它与当前任务的联系。'],
            ['steps','解题支持 · 拆成小步','请把当前任务拆成小步骤，只引导第一步，等我完成后再继续；记录这次使用了步骤支持。'],
            ['worked_example','示范支持 · 类似例题','请示范一道类似但不同的例题，并说明方法。示范后让我独立完成原任务，不把看完示范当作已掌握。'],
            ['checklist','自我检查 · 检查清单','请给我当前任务的检查清单，让我自己检查，暂时不要直接改出答案。'],
            ['reflection','反思支持 · 我来讲解','请让我讲解当前知识点，你再根据我的解释追问，不要先替我总结。'],
          ].map(([kind,name,message])=><button key={name} type="button" disabled={streaming||busy} onClick={()=>void requestHelp(kind,message)} className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-muted disabled:opacity-50">{name}</button>)}
        </div></details>
      </div>

    </section>
  )
  return (
    <>
      <section className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border bg-background px-4 py-2 text-sm" aria-label={zh?'当前学习目标':'Current objective'}>
        <button type="button" onClick={()=>{setDetailsOpen(true);drawer.current?.showModal()}} className="min-w-0 flex-1 truncate text-left font-medium hover:text-primary">{state?.objective?.name || (zh?'查看学习目标与资料':'Learning context')} <span className="ml-1 text-xs text-muted-foreground">{zh?'详情':'Details'}</span></button>
        <Link className="rounded-lg px-2 py-1 text-xs text-primary hover:bg-muted" href={scopedUrl('/learning/projects/'+encodeURIComponent(pathId)+'/checks')}>{zh?'核查与复习':'Checks and review'}</Link>
        <Link className="rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted" href={scopedUrl('/learning/projects/'+encodeURIComponent(pathId)+'/diagnosis')}>{zh?'学习诊断':'Diagnosis'}</Link>
        <Link href={masteryTopicRoute(pathId)} aria-disabled={streaming} onClick={event=>{if(streaming)event.preventDefault()}} className="rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted aria-disabled:opacity-50">{zh?'返回项目':'Back to project'}</Link>
      </section>
      {error&&<p role="alert" className="px-4 py-2 text-xs text-destructive">{error} <button type="button" onClick={()=>setReload(v=>v+1)} className="underline">{t('Retry')}</button></p>}
      {showResume&&state?.objective&&<div className="flex flex-wrap items-center gap-2 border-b border-border bg-primary/5 px-4 py-2 text-xs"><span>{zh?'接着本项目学习：':'Continue project: '}{state.objective.name}</span>{state.resume_session_id && state.resume_session_id!==sessionId ? <Link className="rounded-full border border-border px-3 py-1" href={masterySessionRoute(pathId,state.resume_session_id)}>{zh?'继续上次':'Resume'}</Link> : <button type="button" disabled={streaming||busy} onClick={()=>onSend(zh?'请接着本项目上次的学习继续，先说明已记录的进度与未完成任务；有待答题时继续原题，不直接给答案。':'Resume using recorded project progress and pending task.')} className="rounded-full border border-border px-3 py-1 disabled:opacity-50">{zh?'继续上次':'Resume'}</button>}<button type="button" disabled={streaming||busy} onClick={()=>onSend(zh?'我想换个问题，请先问我想讨论什么，保留当前学习进度。':'Ask what I would like to discuss; keep my current progress.')} className="px-2 py-1">{zh?'换个问题':'Another question'}</button></div>}
      {helpHost ? createPortal(helpToolbar, helpHost) : null}
    <dialog ref={drawer} onClose={()=>setDetailsOpen(false)} aria-labelledby="wenjin-details-title" onClick={event=>{if(event.target===event.currentTarget)event.currentTarget.close()}} className="fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-full max-w-none overflow-y-auto border-l border-border bg-background p-0 text-sm text-foreground shadow-2xl backdrop:bg-black/35 sm:max-w-md">
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-background px-5 py-4"><h2 id="wenjin-details-title" className="font-semibold">{zh?'学习目标与资料':'Learning context'}</h2><button type="button" onClick={()=>drawer.current?.close()} className="rounded-lg border border-border px-3 py-1.5">{zh?'关闭':'Close'}</button></header>
      <div className="space-y-5 p-5">
    {detailsOpen&&<DiagnosticImpact pathId={pathId} impact={state?.import_impact??null} onSend={onSend} compact disabled={streaming||busy} />}

        {error && <p role="alert" className="text-destructive">{error} <button type="button" className="underline" onClick={() => setReload(value => value + 1)}>{t('Retry')}</button></p>}
        {!state && !error && <p>{t('Loading…')}</p>}
        {state && <>
          <p>{state.decision?.reason || t(state.complete ? 'wenjinTutor.completed' : state.claimed_complete ? 'wenjinTutor.claimedComplete' : 'wenjinTutor.noEvidence')}</p>
          {state.decision?.diagnostic_estimate && <div className="rounded-lg border border-border p-3 text-xs" role="status">
            <p className="font-medium">{zh ? '持续诊断 · 实验跟踪' : 'Continuous diagnosis · Experimental tracking'}</p>
            <p className="mt-1 text-muted-foreground">{zh ? state.decision.diagnostic_estimate.summary : 'The tutor uses actual performance, goals and preferences to choose the next step. BKT parameters are not calibrated.'}</p>
            <p className="mt-1 text-muted-foreground">{zh ? '本目标有效独立作答：' : 'Eligible independent responses for this objective: '}{state.decision.diagnostic_estimate.n_eligible}{zh ? '。诊断提供依据，导师决定如何讲解、核查或调整帮助；你可以随时要求恢复帮助。' : '. Diagnosis informs the tutor; you can request more support at any time.'}</p>
          </div>}
          <p className="text-xs text-muted-foreground">{zh?'核查可稍后进行，不要求上传资料；选择先讲解不会被记为已掌握。':'Probes can wait. Uploads are optional; choosing teaching grants no mastery credit.'}</p>
          {!!state.decision?.external_references?.length&&<details><summary className="cursor-pointer text-xs">{zh?'外部诊断参考（本人上传，未核实）':'External references (self-uploaded, unverified)'}</summary><ul className="mt-2 space-y-2 text-xs">{state.decision.external_references.map((reference,index)=><li key={index}>{reference.knowledge_name} · {reference.source_system} · {reference.observed_on} · {reference.active_reference?(zh?'估计值 ':'Estimate ')+reference.probability:(zh?'仅作历史或未测记录':'Historical/unmeasured only')} · {reference.relation==='partial'?(zh?'部分对应':'Partial scope'):(zh?'映射声明一致':'Declared equivalent')}</li>)}</ul><p className="mt-2 text-xs text-muted-foreground">{zh?'外部概率不等于本地能力证据，需要用实际任务核查，且不会覆盖新的独立表现。':'External estimates grant no mastery credit and do not override new independent evidence.'}</p></details>}
          <p className="text-xs text-muted-foreground">{state.support_level > 0 ? (zh?'本题已使用学习帮助；后续独立任务将分别记录。':'Help has been used on this task.') : (zh?'本题尚未记录提示使用。':'No hints recorded for this task.')}</p>
          <details>
            <summary className="cursor-pointer font-medium">{t('wenjinTutor.evidence')}</summary>
            {state.decision?.policy_version&&<p className="mt-2 text-xs text-muted-foreground">{zh?'诊断决策版本':'Diagnostic policy'}：{state.decision.policy_version} · {zh?'依据记录版本':'Source revision'} {state.decision.source_revision}</p>}
            {!!state.decision?.prerequisite_suggestions?.length&&<p className="mt-2 text-xs">{zh?'可选核查先修知识（尚未认定为原因）：':'Optional prerequisite checks, not confirmed causes: '}{state.decision.prerequisite_suggestions.map(p=>p.name).join(' · ')}</p>}
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
    </dialog>
    </>
  )
}
