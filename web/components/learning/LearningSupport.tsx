'use client'
import { useCallback, useEffect, useState, useRef } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { LearningShell } from './LearningShell'
import GoalContractPanel from './GoalContractPanel'
import { projectRequest, projectEndpoint, learningProjectRoute } from '@/lib/learning-project-api'
import { masterySessionRoute as sourceRoute } from '@/lib/learning-routes'
import { masterySessionRoute, MASTERY_OPENING_SCOPE } from '@/lib/mastery-mode'
import { setPendingPrompt } from '@/lib/pending-prompt'
import { scopedUrl } from '@/lib/workspace-scope'

type Evidence = {
  id: string
  question_id: string
  session_id: string
  turn_id: string
  at: number
  ability: string
  result: string
  assistance: string
  model_feedback: boolean
}
type Change = {
  id: string
  before: number
  after: number
  reason: string
  authority: string
  observation_ids: string[]
  basis?: { observation_id: string; session_id: string; question_id: string }[]
  session_id: string
  at: number
}
type Objective = {
  id: string
  name: string
  observed: boolean
  independent_n: number
  assisted_n: number
  latest_result: string | null
  support: {
    level: number
    label: string
    mode: string
    reason: string
    actual_level: number | null
    history: Change[]
    rules: string
  }
  evidence: Evidence[]
  check: { phase_label: string; status: string; due_at: number | null; completed_cycles: number }
}
type Metric = {
  delivered_n?: number
  independent_n: number
  independent_successes: number
  independent_interval: number[]
  delayed_n: number
  delayed_successes: number
  delayed_interval: number[]
  exploratory_n?: number
  independent_unmatched_n?: number
  sources?: {
    episode_id: string
    session_id: string
    turn_id: string
    outcomes: {
      observation_id: string
      session_id: string
      question_id: string
      phase: string
      result: string
    }[]
  }[]
}
type Strategy = {
  consent?: boolean
  protocol?: string
  status?: string
  active?: { version: string; template: string }
  candidate?: { version: string; template: string; hypothesis: string; status: string }
  previous?: unknown
  delivery_count: number
  catalogue: { id: string; name: string }[]
  evaluation?: { baseline: Metric; candidate: Metric; limitations?: string[] }
  history?: { event: string; version?: string; at: number }[]
  limitations: string[]
}
type Portrait = {
  name: string
  source_revision: number
  goal: string
  self_report: Record<string, string>
  objective_id: string | null
  objectives: Objective[]
  strategy: Strategy
  limits: string[]
}
const fields = {
  prior_knowledge: '我目前会什么',
  preferences: '我偏好的学习方式',
}
const label: Record<string, string> = {
  correct: '本次反馈通过',
  partial: '还需补充',
  incorrect: '需要巩固',
  independent: '未记录帮助',
  assisted: '有学习帮助',
  unknown: '帮助情况未知',
  application: '应用',
  transfer: '迁移',
  explanation: '解释',
  recognition: '识别',
}
const states: Record<string, string> = {
  paused: '尚未试用／已暂停',
  collecting_evidence: '正在积累表现记录',
  awaiting_agent_proposal: '等待津津判断是否提出改进',
  trial: '正在小范围试用',
  trial_insufficient_evidence: '对比证据不足，保留当前策略',
  monitoring: '候选已启用，继续观察',
  rejected: '候选未采用，保留原方式',
  rolled_back: '已恢复原策略',
}
const button =
  'rounded-xl border border-border px-3 py-2 text-sm disabled:opacity-40 hover:bg-muted'
export default function LearningSupportPage() {
  const { pathId } = useParams<{ pathId: string }>()
  return <LearningSupport key={pathId} pathId={pathId} />
}
function LearningSupport({ pathId }: { pathId: string }) {
  const router = useRouter()
  const [data, setData] = useState<Portrait | null>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false),
    [tab, setTab] = useState('portrait'),
    [objective, setObjective] = useState(''),
    [editing, setEditing] = useState(false),
    [draft, setDraft] = useState<Record<string, string>>({})
  const base = projectEndpoint(pathId)
  const loadVersion = useRef(0)
  const load = useCallback(async () => {
    const version = ++loadVersion.current
    try {
      const p = await projectRequest<Portrait>(base + '/learning-support')
      if (version !== loadVersion.current) return
      setData(p)
      setDraft(Object.fromEntries(Object.keys(fields).map(k => [k, p.self_report[k] || ''])))
      setObjective(old =>
        p.objectives.some(o => o.id === old) ? old : p.objective_id || p.objectives[0]?.id || ''
      )
      setError('')
    } catch (e) {
      if (version !== loadVersion.current) return
      setError(e instanceof Error ? e.message : '读取失败，请重试')
    }
  }, [base])
  useEffect(() => {
    void load()
    return () => {
      loadVersion.current++
    }
  }, [load])
  const selected = data?.objectives.find(o => o.id === objective)
  async function choose(action: string) {
    if (!selected) return
    setBusy(true)
    setError('')
    try {
      await projectRequest(base + '/learning-support/choice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ objective_id: selected.id, action }),
      })
      await load()
      setNotice('后续任务的支持选择已保存。当前题实际使用过的帮助与作答记录保持可追溯。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }
  async function save() {
    if (!data) return
    setBusy(true)
    setError('')
    try {
      const p = await projectRequest<Portrait>(base + '/learning-support/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_revision: data.source_revision, fields: draft }),
      })
      setData(p)
      setEditing(false)
      setNotice('学习信息已保存。你的自述与实际任务表现会分别保留。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败，请刷新后重试')
    } finally {
      setBusy(false)
    }
  }
  async function experiment(enabled: boolean) {
    setBusy(true)
    setError('')
    try {
      await projectRequest(base + '/wenjin/consent', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      await load()
      setNotice(
        enabled
          ? '已开启本人当前项目的教学方式试用，可随时停止。'
          : '已停止新的策略试用，照常学习，历史依据保留。'
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }
  async function rollback() {
    setBusy(true)
    setError('')
    try {
      await projectRequest(base + '/wenjin/rollback', { method: 'POST' })
      await load()
      setNotice('已恢复原教学策略，历史试用记录保留。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '恢复失败')
    } finally {
      setBusy(false)
    }
  }
  function discuss() {
    setPendingPrompt(
      '请结合我的学习目标、已有表现和帮助选择，说明下一步怎样学、为什么这样安排。如果已开启教学方式试用，请自主判断保持原方式或提出适合我的教学改进，并说明依据；证据不足时不要猜测我的掌握程度。',
      MASTERY_OPENING_SCOPE
    )
    router.push(masterySessionRoute(pathId, 'study'))
  }
  const strategy = data?.strategy
  const strategyName = (id?: string) =>
    strategy?.catalogue.find(x => x.id === id)?.name || '先说明理由'
  return (
    <LearningShell title="学习画像与支持" subtitle="知道依据，也能决定需要多少帮助" back={false}>
      <Link className="mb-4 inline-block text-sm text-primary" href={learningProjectRoute(pathId)}>
        ← 返回项目
      </Link>
      <GoalContractPanel key={pathId + ':goal'} pathId={pathId} onUpdated={() => void load()} />
      <nav className="mb-5 flex flex-wrap gap-2" aria-label="画像与教学方式">
        <button
          className={button + (tab === 'portrait' ? ' bg-primary text-primary-foreground' : '')}
          aria-pressed={tab === 'portrait'}
          onClick={() => setTab('portrait')}
        >
          我的学习画像
        </button>
        <button
          className={button + (tab === 'strategy' ? ' bg-primary text-primary-foreground' : '')}
          aria-pressed={tab === 'strategy'}
          onClick={() => setTab('strategy')}
        >
          教学方式试用
        </button>
        <button disabled={busy} className={button} onClick={() => void load()}>
          刷新
        </button>
      </nav>
      {error && (
        <p
          role="alert"
          className="mb-4 rounded-xl border border-destructive/40 p-3 text-sm text-destructive"
        >
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mb-4 rounded-xl bg-primary/5 p-3 text-sm">
          {notice}
        </p>
      )}
      {!data && !error && <p role="status">正在核对学习记录…</p>}
      {data && tab === 'portrait' && (
        <div className="mx-auto max-w-3xl space-y-5">
          <section className="rounded-2xl border border-border bg-card p-5">
            <div className="flex justify-between gap-3">
              <h2 className="text-lg font-semibold">我的学习起点与偏好</h2>
              <button className={button} disabled={busy} onClick={() => setEditing(v => !v)}>
                {editing ? '收起编辑' : '补充或修改'}
              </button>
            </div>

            <p className="mt-2 text-sm text-muted-foreground">
              下面的信息来自你的自述；任务表现单独记录。没填或没有记录，都不代表不会。
            </p>
            {editing ? (
              <form
                onSubmit={e => {
                  e.preventDefault()
                  void save()
                }}
                className="mt-4 space-y-4"
              >
                {Object.entries(fields).map(([k, name]) => (
                  <label key={k} className="block text-sm">
                    {name}
                    <textarea
                      aria-label={name}
                      maxLength={2000}
                      value={draft[k] || ''}
                      onChange={e => setDraft({ ...draft, [k]: e.target.value })}
                      rows={2}
                      className="mt-2 block w-full rounded-xl border border-border bg-background p-3"
                    />
                  </label>
                ))}
                <p className="text-xs text-muted-foreground">
                  清空后保存会移除对应自述，其他项目与学习证据保留。
                </p>
                <button disabled={busy} className={button + ' bg-primary text-primary-foreground'}>
                  保存我的学习信息
                </button>
              </form>
            ) : (
              <dl className="mt-4 grid gap-4 sm:grid-cols-2">
                {Object.entries(fields).map(([k, name]) => (
                  <div key={k}>
                    <dt className="text-xs text-muted-foreground">{name}</dt>
                    <dd className="mt-1 whitespace-pre-wrap text-sm">
                      {data.self_report[k] || '尚未说明'}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
          {!!data.objectives.length && (
            <label className="block text-sm">
              查看知识点
              <select
                aria-label="查看画像知识点"
                value={objective}
                onChange={e => setObjective(e.target.value)}
                className="ml-3 max-w-full rounded-xl border border-border bg-background p-2"
              >
                {data.objectives.map(o => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {selected && (
            <>
              <section className="rounded-2xl border border-border bg-card p-5">
                <h2 className="font-semibold">已有表现与待确认的部分</h2>
                <p className="mt-3 text-sm">
                  {selected.observed
                    ? `已记录未受助表现 ${selected.independent_n} 次、有辅助表现 ${selected.assisted_n} 次。次数不等于掌握程度。`
                    : '当前还没有足够的有效任务记录，这是未知，不代表不会。'}
                </p>
                <p className="mt-2 text-sm">
                  下一步核查：{selected.check.phase_label}
                  {selected.check.status === 'waiting' ? ' · 等待到期' : ''}
                </p>
                <Link
                  href={scopedUrl(
                    '/learning/projects/' +
                      encodeURIComponent(pathId) +
                      '/checks?objective=' +
                      encodeURIComponent(selected.id)
                  )}
                  className="mt-3 inline-block text-sm text-primary underline"
                >
                  用新任务确认理解
                </Link>
                <details className="mt-4 text-sm">
                  <summary className="cursor-pointer">查看表现依据与原对话</summary>
                  {selected.evidence.map(e => (
                    <div key={e.id} className="mt-3 rounded-xl border border-border p-3">
                      <p>
                        {label[e.ability]} · {label[e.result]} · {label[e.assistance]}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {new Date(e.at * 1000).toLocaleString('zh-CN')}
                        {e.model_feedback ? ' · 模型反馈，仍保留不确定性' : ''}
                      </p>
                      {e.session_id && (
                        <Link
                          className="mt-2 inline-block text-primary underline"
                          href={sourceRoute(pathId, e.session_id)}
                        >
                          查看原题与对话
                        </Link>
                      )}
                    </div>
                  ))}
                </details>
              </section>
              <section className="rounded-2xl border border-border bg-card p-5">
                <h2 className="font-semibold">现在为什么这样帮助我</h2>
                <p className="mt-3 text-sm">{selected.support.reason}</p>
                <p className="mt-3 text-sm">后续任务计划：{selected.support.label}</p>
                <p className="mt-2 text-xs text-muted-foreground">
                  当前题实际帮助：
                  {selected.support.actual_level === null
                    ? '暂无待答题'
                    : selected.support.actual_level + ' / 3'}
                  。{selected.support.rules}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    disabled={busy}
                    className={button}
                    onClick={() => void choose('restore_support')}
                  >
                    多给些支持
                  </button>
                  <button
                    disabled={busy}
                    className={button}
                    onClick={() => void choose('independent')}
                  >
                    先让我独立试试
                  </button>
                  <button
                    disabled={busy}
                    className={button}
                    onClick={() => void choose('adaptive')}
                  >
                    由津津按表现建议
                  </button>
                </div>
                <button
                  className="mt-4 text-sm text-primary underline"
                  disabled={busy}
                  onClick={discuss}
                >
                  让津津解释并继续学习
                </button>
                <details className="mt-4 text-sm">
                  <summary className="cursor-pointer">帮助调整记录与理由</summary>
                  {!selected.support.history.length && (
                    <p className="mt-2 text-muted-foreground">尚无调整记录。暂不减少支持。</p>
                  )}
                  {selected.support.history
                    .slice()
                    .reverse()
                    .map(h => (
                      <div key={h.id} className="mt-3 rounded-xl border border-border p-3">
                        <p>
                          {h.authority === 'learner' ? '我的选择' : '津津的建议'} · 计划 {h.before}{' '}
                          → {h.after}
                        </p>
                        <p className="mt-2">{h.reason}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {new Date(h.at * 1000).toLocaleString('zh-CN')}
                        </p>
                        {!!h.basis?.length && (
                          <div className="mt-2 flex flex-wrap gap-3">
                            {h.basis.map((b, index) =>
                              b.session_id ? (
                                <Link
                                  key={b.observation_id}
                                  className="text-primary underline"
                                  href={sourceRoute(pathId, b.session_id)}
                                >
                                  查看依据 {index + 1}
                                </Link>
                              ) : (
                                <span
                                  key={b.observation_id}
                                  className="text-xs text-muted-foreground"
                                >
                                  旧记录未保留会话定位
                                </span>
                              )
                            )}
                          </div>
                        )}
                        {h.session_id && (
                          <Link
                            className="mt-2 inline-block text-primary underline"
                            href={sourceRoute(pathId, h.session_id)}
                          >
                            查看决策对话
                          </Link>
                        )}
                      </div>
                    ))}
                </details>
              </section>
            </>
          )}
          {!data.objectives.length && (
            <p className="text-sm text-muted-foreground">
              先和津津确认项目大纲，再按知识点积累表现。
            </p>
          )}
        </div>
      )}
      {data && tab === 'strategy' && strategy && (
        <div className="mx-auto max-w-3xl space-y-5">
          <section className="rounded-2xl border border-border bg-card p-5">
            <h2 className="text-lg font-semibold">让教学方式随你的表现改进</h2>
            <p className="mt-3 text-sm text-muted-foreground">
              只比较本人当前项目的教学动作，不训练陪学大模型权重。津津可以提议新的教学方式；只有真实交付和可比较的后测才用于效果评估。你可随时停止或恢复原策略。
            </p>
            <p className="mt-4">当前方式：{strategyName(strategy.active?.template)}</p>
            <p className="mt-2 text-sm">
              {states[strategy.status || 'paused'] || '历史试用记录，效果尚待核查'}
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <button
                disabled={busy}
                onClick={() => void experiment(!strategy.consent)}
                className={
                  button + (!strategy.consent ? ' bg-primary text-primary-foreground' : '')
                }
              >
                {strategy.consent ? '停止策略试用' : '开启本人项目的策略试用'}
              </button>
              {strategy.consent && strategy.protocol !== 'observed-delivery-v2' && (
                <button disabled={busy} className={button} onClick={() => void experiment(true)}>
                  使用新版交付核查规则
                </button>
              )}
              {!!strategy.previous && (
                <button disabled={busy} className={button} onClick={() => void rollback()}>
                  恢复原教学策略
                </button>
              )}
            </div>
            <button
              className="mt-4 text-sm text-primary underline"
              disabled={busy}
              onClick={discuss}
            >
              请津津评估下一步教学方式
            </button>
          </section>
          {strategy.candidate && (
            <section className="rounded-2xl border border-border bg-card p-5">
              <h2 className="font-semibold">正在核验的改进想法</h2>
              <p className="mt-3">{strategyName(strategy.candidate.template)}</p>
              <p className="mt-2 text-sm">{strategy.candidate.hypothesis}</p>
              <p className="mt-2 text-xs text-muted-foreground">
                结构检查通过不等于效果已证实；试用阶段仅分配少量任务。数据不足时保留原方式。
              </p>
            </section>
          )}
          <section className="rounded-2xl border border-border bg-card p-5">
            <h2 className="font-semibold">目前能说明什么</h2>
            <p className="mt-3 text-sm">
              已记录实际交付 {strategy.delivery_count} 次。未记录交付的策略分配不算教学干预。
            </p>
            {!strategy.evaluation && (
              <p className="mt-2 text-sm text-muted-foreground">
                暂没有足够的可比后测。继续正常学习即可，不会凭空生成“有效率”。
              </p>
            )}
            {strategy.evaluation && (
              <>
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr>
                        {['教学方式', '可比独立后测', '延迟保持后测'].map(x => (
                          <th key={x} className="p-2">
                            {x}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(
                        [
                          ['baseline', '原有方式'],
                          ['candidate', '改进方式'],
                        ] as const
                      ).map(([key, name]) => {
                        const m = strategy.evaluation![key]
                        return (
                          <tr key={key} className="border-t border-border">
                            <td className="p-2">{name}</td>
                            <td className="p-2">
                              {m.independent_n
                                ? `${m.independent_successes} / ${m.independent_n}`
                                : '待积累'}
                            </td>
                            <td className="p-2">
                              {m.delayed_n
                                ? `${m.delayed_successes} / ${m.delayed_n}`
                                : '至少间隔一天，待积累'}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
                <details className="mt-4 text-sm">
                  <summary className="cursor-pointer">可信区间、排除项与原始来源</summary>
                  {(['baseline', 'candidate'] as const).map(key => {
                    const m = strategy.evaluation![key]
                    return (
                      <div key={key} className="mt-3 space-y-2 rounded-xl border border-border p-3">
                        <p>
                          {key === 'baseline' ? '原有方式' : '改进方式'}：独立后测区间{' '}
                          {m.independent_interval.map(v => Math.round(v * 100) + '%').join('–')}
                          ；延迟区间{' '}
                          {m.delayed_interval.map(v => Math.round(v * 100) + '%').join('–')}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          区间宽表示不确定，不是个人掌握概率。模型反馈探索记录{' '}
                          {m.exploratory_n || 0}，缺少可比条件的独立记录{' '}
                          {m.independent_unmatched_n || 0}，不用于自动启用。
                        </p>
                        {m.sources?.map(e => (
                          <div key={e.episode_id} className="mt-2 text-xs">
                            {e.session_id && (
                              <Link
                                href={sourceRoute(pathId, e.session_id)}
                                className="text-primary underline"
                              >
                                查看实际教学交付
                              </Link>
                            )}
                            {e.outcomes.map(o => (
                              <p key={o.observation_id}>
                                {o.phase === 'delayed' ? '延迟' : '新任务'} · {label[o.result]}{' '}
                                {o.session_id && (
                                  <Link
                                    className="text-primary underline"
                                    href={sourceRoute(pathId, o.session_id)}
                                  >
                                    查看后测原话
                                  </Link>
                                )}
                              </p>
                            ))}
                          </div>
                        ))}
                      </div>
                    )
                  })}
                </details>
              </>
            )}
            <details className="mt-4 text-xs text-muted-foreground">
              <summary className="cursor-pointer">评估边界与策略版本记录</summary>
              {strategy.limitations.map(x => (
                <p key={x} className="mt-2">
                  {x}
                </p>
              ))}
              <p className="mt-2">
                每种方式至少需要12条可比独立后测及12条延迟保持记录，还需满足改进与保持条件；确定性判分也依赖题目与答案质量。策略版本：
                {strategy.active?.version || 'baseline'}
                。交付动作类型由智能体声明，公开原话可回查；小样本比较不证明因果效果。
              </p>
              {strategy.history?.slice(-8).map((h, i) => (
                <p key={i} className="mt-2">
                  {new Date(h.at * 1000).toLocaleString('zh-CN')} ·{' '}
                  {(
                    {
                      candidate_created: '提出候选',
                      promoted: '启用候选',
                      candidate_rejected: '保留原方式',
                      constraint_revalidation_failed: '策略约束已变化，停止采用候选',
                      monitoring_completed: '保持观察完成，继续积累改进依据',
                      protocol_upgraded: '历史比较归档，启用交付核查规则',
                      automatic_rollback: '观察到退化，恢复原方式',
                      learner_rollback: '学生恢复原方式',
                      evidence_revalidation_rollback: '依据变化，恢复原方式',
                    } as Record<string, string>
                  )[h.event] || '状态更新'}{' '}
                  {h.version}
                </p>
              ))}
            </details>
          </section>
        </div>
      )}
    </LearningShell>
  )
}
