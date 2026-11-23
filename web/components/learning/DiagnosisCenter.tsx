'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { scopedUrl } from '@/lib/workspace-scope'
import GoalContractPanel from './GoalContractPanel'
import PlanVersions from './PlanVersions'
import PersonalTraining from './PersonalTraining'
import { LearningShell } from './LearningShell'
import { projectEndpoint, projectRequest, learningProjectRoute } from '@/lib/learning-project-api'

type Row = {
  classroom_origin?: { classroom_id: string; scene_id: string }
  observation_id: string
  objective_name: string
  ability: string
  answer: unknown
  result: string
  assistance: string
  validity: string
  occurred_at: number
  eligibility: string
  exclusion_reasons: string[]
  session_id: string
  turn_id: string
  question_id: string
  feedback: string
  context: null | {
    task_prompt: string
    task_version: string
    grading_method: string
    grading_version: string
    rubric_fingerprint: string
    scaffold_level: number
  }
  events: { id: number; type: string; at: number; payload: unknown }[]
}
type Quality = {
  total: number
  eligible: number
  excluded: number
  sequences: number
  results: Record<string, number>
  assistance: Record<string, number>
  exclusions: Record<string, number>
  skills: Record<string, number>
}
type Catalogue = {
  teaching_activity?: {
    id: string
    objective_name: string
    state: string
    assisted: boolean
    human_turn_count: number
    updated_at: number
  }[]
  name: string
  source_revision: number
  rows: Row[]
  quality: Quality
  limitations: string[]
  self_reports: unknown[]
  support_activity?: {
    id: number
    type: string
    session_id: string
    turn_id: string
    kind?: string
    question_id?: string
    level?: number
  }[]
}
type Dataset = {
  id: string
  created_at: number
  source_revision: number
  digest: string
  quality: Quality
  rule_version: string
  status: string
  rows?: unknown[]
  provenance?: Row[]
}
type Brief = {
  objective?: { name: string } | null
  decision?: {
    reason: string
    evidence?: { achieved: string[]; missing: string[] }
    diagnostic_estimate?: { n_eligible: number; parameter_origin: string; summary: string }
  } | null
}
type Source = {
  title: string
  located: boolean
  messages: { id: string | number; role: string; content: string; matched: boolean }[]
}
const labels: Record<string, string> = {
  correct: '正确',
  incorrect: '错误',
  partial: '部分正确',
  independent: '独立',
  assisted: '有辅助',
  unknown: '未知',
  eligible: '可纳入',
  excluded: '排除／待核验',
  recognition: '识别',
  explanation: '解释',
  application: '应用',
  transfer: '迁移',
  deterministic: '确定性判分',
  semantic: '模型判分',
}
const label = (s: string) => labels[s] || s
const button = 'rounded-lg border border-border px-3 py-2 text-sm disabled:opacity-50'
function Bars({ values }: { values: Record<string, number> }) {
  const max = Math.max(1, ...Object.values(values))
  return (
    <ul className="space-y-2">
      {Object.entries(values).map(([key, n]) => (
        <li key={key} className="text-xs">
          <div className="flex justify-between gap-3">
            <span>{label(key)}</span>
            <span>{n}</span>
          </div>
          <div className="mt-1 h-1.5 rounded bg-muted">
            <div className="h-full rounded bg-primary" style={{ width: `${(n / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  )
}
export default function DiagnosisCenterPage() {
  const { pathId } = useParams<{ pathId: string }>()
  return <DiagnosisCenter key={pathId} pathId={pathId} />
}
function DiagnosisCenter({ pathId }: { pathId: string }) {
  const endpoint = projectEndpoint(pathId) + '/diagnosis'
  const [brief, setBrief] = useState<Brief | null>(null)
  const [tab, setTab] = useState<'overview' | 'evidence' | 'data' | 'training' | 'plans'>(
    'overview'
  )
  const [data, setData] = useState<Catalogue | null>(null)
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Row | null>(null)
  const [source, setSource] = useState<Source | null>(null)
  const [sourceError, setSourceError] = useState('')
  const [excluded, setExcluded] = useState<string[]>([])
  const [synthetic, setSynthetic] = useState(false)
  const [snapshot, setSnapshot] = useState<Dataset | null>(null)
  const generation = useRef(0)
  const matchRef = useRef<HTMLDivElement>(null)
  const loadVersion = useRef(0)
  const load = useCallback(async () => {
    const version = ++loadVersion.current
    setBusy(true)
    setError('')
    try {
      const [a, b, c] = await Promise.all([
        projectRequest<Catalogue>(endpoint + '/evidence'),
        projectRequest<Dataset[]>(endpoint + '/datasets'),
        projectRequest<Brief>(projectEndpoint(pathId) + '/wenjin'),
      ])
      if (version !== loadVersion.current) return
      setData(a)
      setDatasets(b)
      setBrief(c)
      setExcluded(old =>
        old.filter(id => a.rows.some(r => r.observation_id === id && r.eligibility === 'eligible'))
      )
    } catch (e) {
      if (version === loadVersion.current) setError(String(e))
    } finally {
      if (version === loadVersion.current) setBusy(false)
    }
  }, [endpoint, pathId])
  useEffect(() => {
    void load()
    return () => {
      generation.current++
      loadVersion.current++
    }
  }, [load])
  useEffect(() => {
    matchRef.current?.scrollIntoView({ block: 'center' })
  }, [source])
  async function inspect(row: Row) {
    const token = ++generation.current
    setSelected(row)
    setSource(null)
    setSourceError('')
    if (row.classroom_origin) return
    if (!row.session_id) {
      setSourceError('旧记录未保留会话来源，不能定位。')
      return
    }
    try {
      const s = await projectRequest<Source>(
        endpoint + '/source?observation_id=' + encodeURIComponent(row.observation_id)
      )
      if (token === generation.current) setSource(s)
    } catch (e) {
      if (token === generation.current) setSourceError(String(e))
    }
  }
  async function create() {
    if (!data) return
    setBusy(true)
    setError('')
    try {
      const s = await projectRequest<Dataset>(endpoint + '/datasets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_revision: data.source_revision,
          excluded_ids: excluded,
          synthetic,
        }),
      })
      await load()
      setSnapshot(s)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  async function view(id: string) {
    setBusy(true)
    setError('')
    try {
      setSnapshot(await projectRequest<Dataset>(endpoint + '/datasets/' + id))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  function download(s: Dataset, withSources: boolean) {
    const payload = withSources
      ? s
      : {
          schema_version: 1,
          id: s.id,
          source_revision: s.source_revision,
          rule_version: s.rule_version,
          status: s.status,
          rows: s.rows,
        }
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    )
    const a = document.createElement('a')
    a.href = url
    a.download = `diagnosis-${s.id}${withSources ? '-with-sources' : ''}.json`
    a.click()
    URL.revokeObjectURL(url)
  }
  const rows = (data?.rows || []).filter(
    r =>
      (!filter || r.eligibility === filter) &&
      (!query || `${r.objective_name} ${r.question_id} ${r.session_id}`.includes(query))
  )
  const observedSkills = (data?.rows || [])
    .filter(row => row.validity === 'recorded')
    .reduce<Record<string, number>>((result, row) => {
      result[row.objective_name] = (result[row.objective_name] || 0) + 1
      return result
    }, {})
  const count =
    data?.rows.filter(r => r.eligibility === 'eligible' && !excluded.includes(r.observation_id))
      .length || 0
  return (
    <LearningShell
      title="学习诊断中心"
      subtitle={data?.name || '查看学习证据与训练数据'}
      action={
        <Link className={button} href={learningProjectRoute(pathId)}>
          返回学习项目
        </Link>
      }
    >
      <nav className="mb-6 flex flex-wrap gap-2" aria-label="诊断页面">
        <Link
          href={scopedUrl('/learning/projects/' + encodeURIComponent(pathId) + '/support')}
          className={button}
        >
          画像与支持
        </Link>
        {(['overview', 'evidence', 'data', 'training', 'plans'] as const).map(v => (
          <button
            key={v}
            aria-pressed={tab === v}
            className={`${button} ${tab === v ? 'bg-primary text-primary-foreground' : ''}`}
            onClick={() => setTab(v)}
          >
            {
              {
                overview: '学习概况',
                evidence: '证据溯源',
                data: '数据质量',
                training: '个人模型训练',
                plans: '计划变化',
              }[v]
            }
          </button>
        ))}
        <button className={button} disabled={busy} onClick={() => void load()}>
          刷新
        </button>
      </nav>
      {error && (
        <p role="alert" className="mb-4 text-destructive">
          {error}
        </p>
      )}
      {!data && <p>{busy ? '正在读取…' : '暂无数据'}</p>}
      {data && (
        <>
          {tab !== 'overview' && (
            <div className="mb-6 grid grid-cols-3 gap-3">
              {[
                ['学习记录', data.quality.total],
                ['训练可用', data.quality.eligible],
                ['训练待核验', data.quality.excluded],
              ].map(([name, n]) => (
                <div key={name} className="rounded-xl border border-border p-4">
                  <p className="text-xs text-muted-foreground">{name}</p>
                  <p className="mt-2 text-2xl">{n}</p>
                </div>
              ))}
            </div>
          )}
          {tab === 'overview' ? (
            <>
              <GoalContractPanel
                key={pathId + ':goal'}
                pathId={pathId}
                onUpdated={() => void load()}
              />
              <section className="grid gap-5 lg:grid-cols-2">
                <div className="rounded-2xl border border-primary/20 bg-primary/5 p-5 lg:col-span-2">
                  <p className="text-xs text-muted-foreground">当前核查知识点</p>
                  <h2 className="mt-2 text-lg font-semibold">
                    {brief?.objective?.name || '先确认学习目标'}
                  </h2>
                  <p className="mt-3 text-sm">
                    {brief?.decision?.reason ||
                      '目前证据不足，可以先交流或尝试一个小任务；这不表示你不会。'}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    {!!brief?.decision?.evidence?.achieved.length && (
                      <span className="rounded-full bg-primary/10 px-3 py-1">
                        已记录的表现：{brief.decision.evidence.achieved.map(label).join('、')}
                      </span>
                    )}
                    {!!brief?.decision?.evidence?.missing.length && (
                      <span className="rounded-full border border-border px-3 py-1">
                        接下来核查：{brief.decision.evidence.missing.map(label).join('、')}
                      </span>
                    )}
                  </div>
                  <details className="mt-3 text-xs text-muted-foreground">
                    <summary className="cursor-pointer">诊断依据与适用范围</summary>
                    <p className="mt-2">{brief?.decision?.diagnostic_estimate?.summary}</p>
                    <p className="mt-2">
                      训练资格不等于学习成绩；辅助完成、自述和待核验评分不会直接当成独立掌握证据。
                    </p>
                  </details>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Link
                      href={learningProjectRoute(pathId)}
                      className="rounded-full bg-primary px-4 py-2 text-sm text-primary-foreground"
                    >
                      继续学习
                    </Link>
                    <button className={button} onClick={() => setTab('evidence')}>
                      查看判断依据
                    </button>
                  </div>
                </div>
                <div className="rounded-2xl border border-border bg-card p-5">
                  <h2 className="text-lg font-semibold">学习证据分布</h2>
                  <p className="my-3 text-sm text-muted-foreground">
                    独立作答与辅助表现分别查看；记录数量不等于掌握程度。
                  </p>
                  <Bars values={data.quality.assistance} />
                </div>
                <div className="rounded-2xl border border-border bg-card p-5">
                  <h2 className="text-lg font-semibold">已有学习记录的知识点</h2>
                  <div className="mt-4">
                    <Bars values={observedSkills} />
                  </div>
                  {!Object.keys(observedSkills).length && (
                    <p className="mt-4 text-sm text-muted-foreground">
                      还没有保存学习记录。完成任务后，这里会显示记录数量；它不直接表示掌握程度。
                    </p>
                  )}
                </div>
                <div className="rounded-2xl border border-border p-5 lg:col-span-2">
                  <h2 className="font-semibold">最近学习表现</h2>
                  {data.rows
                    .slice(-5)
                    .reverse()
                    .map(row => (
                      <button
                        key={row.observation_id}
                        onClick={() => void inspect(row)}
                        className="mt-2 flex w-full flex-wrap justify-between gap-2 rounded-xl bg-muted/50 p-3 text-left text-sm"
                      >
                        <span>{row.objective_name}</span>
                        <span>
                          {label(row.result)} · {label(row.assistance)} · 查看来源
                        </span>
                      </button>
                    ))}
                </div>
              </section>
            </>
          ) : tab === 'plans' ? (
            <PlanVersions
              key={pathId}
              pathId={pathId}
              revision={data.source_revision}
              onUpdated={() => void load()}
            />
          ) : tab === 'training' ? (
            <PersonalTraining
              key={pathId}
              pathId={pathId}
              revision={data.source_revision}
              eligible={data.quality.eligible}
              onUpdated={() => void load()}
            />
          ) : tab === 'evidence' ? (
            <>
              <div className="mb-4 flex flex-wrap gap-3">
                <input
                  aria-label="搜索证据"
                  placeholder="搜索知识点、题目或会话编号"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  className="rounded-lg border border-border bg-background p-2"
                />
                <select
                  aria-label="证据状态"
                  value={filter}
                  onChange={e => setFilter(e.target.value)}
                  className={button}
                >
                  <option value="">全部记录</option>
                  <option value="eligible">可纳入</option>
                  <option value="excluded">排除／待核验</option>
                </select>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr>
                      {['时间／知识点', '回答与结果', '辅助情况', '训练资格', '来源'].map(x => (
                        <th key={x} className="p-3">
                          {x}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(row => (
                      <tr key={row.observation_id} className="border-t border-border">
                        <td className="p-3">
                          {row.objective_name}
                          <p className="text-xs text-muted-foreground">
                            {new Date(row.occurred_at * 1000).toLocaleString()} ·{' '}
                            {label(row.ability)}
                          </p>
                        </td>
                        <td className="max-w-xs p-3">
                          <p className="line-clamp-2">
                            {typeof row.answer === 'string'
                              ? row.answer
                              : JSON.stringify(row.answer)}
                          </p>
                          {label(row.result)}
                        </td>
                        <td className="p-3">{label(row.assistance)}</td>
                        <td className="p-3">{label(row.eligibility)}</td>
                        <td className="p-3">
                          <button
                            className="text-primary underline"
                            onClick={() => void inspect(row)}
                          >
                            查看证据与原对话
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!rows.length && (
                <p className="py-8 text-muted-foreground">
                  暂无符合条件的记录。完成陪学任务后会自动出现；普通聊天不直接计分。
                </p>
              )}
              <details className="mt-5">
                <summary>学习帮助记录（请求与实际提示分开）</summary>
                <p className="mt-2 text-xs text-muted-foreground">
                  请求帮助不等于已经得到提示；实际提示需有交付记录。曾求助的题目需另用独立任务核验。
                </p>
                {data.support_activity?.map(e => (
                  <div key={e.id} className="mt-2 rounded-lg border border-border p-3 text-xs">
                    <p>
                      {e.type === 'scaffold.requested' ? '请求帮助' : '已记录实际提示'} ·{' '}
                      {e.kind || '提示层级 ' + e.level}
                    </p>
                    <p className="mt-1 text-muted-foreground">
                      题目 {e.question_id || '尚未绑定'} · 对话 {e.session_id || '新对话'} ·
                      来源轮次 {e.turn_id || '请求阶段'}
                    </p>
                  </div>
                ))}
              </details>
              <details className="mt-5">
                <summary>我教 AI 讲解记录（不计入训练标签）</summary>
                <p className="mt-2 text-xs text-muted-foreground">
                  这里保留学生讲解、模拟追问及模型反馈的过程来源。模型报告不自动改变掌握度；请通过独立核查验证应用。
                </p>
                {data.teaching_activity?.map(s => (
                  <Link
                    key={s.id}
                    href={scopedUrl(
                      '/learning/projects/' +
                        encodeURIComponent(pathId) +
                        '/teach-ai?teaching_session=' +
                        encodeURIComponent(s.id)
                    )}
                    className="mt-2 block rounded-lg border border-border p-3 text-sm"
                  >
                    {s.objective_name} · 讲解 {s.human_turn_count} 次 ·{' '}
                    {s.assisted ? '有学习帮助' : '未记录帮助'}
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {s.state === 'finished' ? '查看报告与原话' : '继续讲解与查看原话'} ·{' '}
                      {new Date(s.updated_at * 1000).toLocaleString()}
                    </span>
                  </Link>
                ))}
              </details>
              <details className="mt-5">
                <summary>自述与候选信息（不作为正误标签）</summary>
                <pre className="whitespace-pre-wrap break-words text-xs">
                  {JSON.stringify(data.self_reports, null, 2)}
                </pre>
              </details>
            </>
          ) : (
            <>
              <div className="grid gap-5 md:grid-cols-2">
                {[
                  ['排除原因', data.quality.exclusions],
                  ['作答结果', data.quality.results],
                  ['辅助情况', data.quality.assistance],
                  ['有效知识覆盖', data.quality.skills],
                ].map(([title, values]) => (
                  <section key={String(title)} className="rounded-xl border border-border p-4">
                    <h2 className="mb-3 font-medium">{String(title)}</h2>
                    <Bars values={values as Record<string, number>} />
                  </section>
                ))}
              </div>
              <section className="mt-6 rounded-xl border border-border p-4">
                <h2 className="font-medium">创建训练数据集</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  仅包含本人当前项目。快照固定规则与来源，不自动训练或划分测试集。可纳入 {count}{' '}
                  条。
                </p>
                <details className="mt-3">
                  <summary>选择需手动排除的样本</summary>
                  {data.rows
                    .filter(r => r.eligibility === 'eligible')
                    .map(r => (
                      <label key={r.observation_id} className="mt-2 block text-sm">
                        <input
                          type="checkbox"
                          checked={excluded.includes(r.observation_id)}
                          onChange={e =>
                            setExcluded(old =>
                              e.target.checked
                                ? [...old, r.observation_id]
                                : old.filter(id => id !== r.observation_id)
                            )
                          }
                        />{' '}
                        排除 {r.objective_name} · {r.question_id}
                      </label>
                    ))}
                </details>
                <label className="my-3 block text-sm">
                  <input
                    type="checkbox"
                    checked={synthetic}
                    onChange={e => setSynthetic(e.target.checked)}
                  />{' '}
                  本项目为模拟／验收数据（禁止纳入训练）
                </label>
                <button
                  className={button}
                  disabled={busy || !count || synthetic}
                  onClick={() => void create()}
                >
                  保存不可变快照
                </button>
              </section>
              <section className="mt-6">
                <h2 className="font-medium">数据集版本</h2>
                {!datasets.length && (
                  <p className="mt-3 text-sm text-muted-foreground">尚未创建数据集。</p>
                )}
                {datasets.map(s => (
                  <button
                    key={s.id}
                    className="mt-3 block w-full rounded-xl border border-border p-4 text-left"
                    onClick={() => void view(s.id)}
                  >
                    {new Date(s.created_at * 1000).toLocaleString()} · {s.quality.eligible} 条 ·
                    源版本 {s.source_revision}
                    <p className="text-xs text-muted-foreground">{s.id} · 未验证</p>
                  </button>
                ))}
              </section>
              {snapshot && (
                <section className="mt-5 rounded-xl border border-border p-4">
                  <h3>数据集 {snapshot.id}</h3>
                  <p className="break-all text-xs">内容校验：{snapshot.digest}</p>
                  <div className="my-3 flex flex-wrap gap-3">
                    <button className={button} onClick={() => download(snapshot, false)}>
                      导出训练字段
                    </button>
                    <button className={button} onClick={() => download(snapshot, true)}>
                      导出含来源的完整快照
                    </button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    完整快照包含回答和来源，请仅在授权范围使用。当前未执行模型训练。
                  </p>
                  <details className="mt-3">
                    <summary>快照样本及排除原因</summary>
                    <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs">
                      {JSON.stringify(snapshot.provenance, null, 2)}
                    </pre>
                  </details>
                </section>
              )}
            </>
          )}
          <ul className="mt-6 space-y-1 text-xs text-muted-foreground">
            {data.limitations.map(x => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </>
      )}
      {selected && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/40"
          onClick={() => {
            generation.current++
            setSelected(null)
          }}
        >
          <aside
            role="dialog"
            aria-modal="true"
            aria-label="证据与来源"
            className="h-full w-full max-w-2xl overflow-y-auto bg-background p-6 shadow-xl"
            onClick={e => e.stopPropagation()}
            onKeyDown={e => {
              if (e.key === 'Escape') {
                generation.current++
                setSelected(null)
              }
              if (e.key === 'Tab') {
                const els = Array.from(
                  e.currentTarget.querySelectorAll<HTMLElement>(
                    'button, a, input, select, summary, [tabindex="0"]'
                  )
                )
                const first = els[0],
                  last = els[els.length - 1]
                if (e.shiftKey && document.activeElement === first) {
                  e.preventDefault()
                  last?.focus()
                } else if (!e.shiftKey && document.activeElement === last) {
                  e.preventDefault()
                  first?.focus()
                }
              }
            }}
          >
            <button
              autoFocus
              className={button}
              onClick={() => {
                generation.current++
                setSelected(null)
              }}
            >
              关闭详情
            </button>
            <h2 className="mt-4 text-lg font-semibold">{selected.objective_name}</h2>
            <p className="mt-2">{selected.context?.task_prompt || '旧记录缺少题目快照'}</p>
            <p className="my-3 whitespace-pre-wrap">
              学生回答：
              {typeof selected.answer === 'string'
                ? selected.answer
                : JSON.stringify(selected.answer)}
            </p>
            <p>
              {label(selected.result)} · {label(selected.assistance)} ·{' '}
              {label(selected.context?.grading_method || '未知')}
            </p>
            <p className="mt-2">{selected.feedback || '未保留评分解释'}</p>
            <ul className="my-3 text-sm">
              {selected.exclusion_reasons.map(x => (
                <li key={x}>{x}</li>
              ))}
            </ul>
            <details>
              <summary>任务版本、评分与事件记录</summary>
              <pre className="whitespace-pre-wrap break-words text-xs">
                {JSON.stringify(
                  {
                    context: selected.context,
                    events: selected.events,
                    session_id: selected.session_id,
                    turn_id: selected.turn_id,
                  },
                  null,
                  2
                )}
              </pre>
            </details>
            {selected.classroom_origin && (
              <Link
                className="my-4 block text-primary underline"
                href={scopedUrl(
                  '/learning/projects/' +
                    encodeURIComponent(pathId) +
                    '/classroom?classroom=' +
                    encodeURIComponent(selected.classroom_origin.classroom_id) +
                    '&scene=' +
                    encodeURIComponent(selected.classroom_origin.scene_id)
                )}
              >
                回到作答课堂与原页
              </Link>
            )}
            <h3 className="mt-6 font-semibold">
              {selected.classroom_origin ? '课堂页是本条证据的原始来源' : '原对话'}
            </h3>
            {sourceError && <p role="alert">{sourceError}</p>}
            {source && (
              <>
                <p>{source.title}</p>
                {!source.located && (
                  <p className="my-2 text-sm text-muted-foreground">
                    旧消息缺少精确轮次关联，以下展示可访问的原会话，不推测匹配。
                  </p>
                )}
                {source.messages.map((m, index) => (
                  <div
                    key={m.id ?? index}
                    ref={m.matched ? matchRef : undefined}
                    className={`my-3 rounded-lg border p-3 ${m.matched ? 'border-primary bg-primary/10' : 'border-border'}`}
                  >
                    <p className="text-xs">
                      {m.role === 'user' ? '学生' : '导师'} · 消息 {m.id}
                      {m.matched ? ' · 来源轮次' : ''}
                    </p>
                    <p className="mt-2 whitespace-pre-wrap break-words text-sm">{m.content}</p>
                  </div>
                ))}
              </>
            )}
          </aside>
        </div>
      )}
    </LearningShell>
  )
}
