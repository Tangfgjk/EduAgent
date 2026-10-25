import { GoalPanel } from './goal-panel';
import { EvidenceCalendar } from './evidence-visuals';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChartNoAxesCombined, FileSearch } from 'lucide-react';
import { api, type EvidenceReport, type EvidenceCategory, type EvidenceSource, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading } from '@wenjin/ui';
import { LearningAnalysis } from './learning-insights';
import { SourceDialog, type Trace } from './review';
import './evidence.css';

const sources: Record<string, string> = { synthetic: '模拟数据', adult_test: '成人测试', real_student: '真实学生' };
const kinds: Record<string, string> = { answer: '作答与复做', checkpoint: '首次曝光检测', hint: '提示', reflection: '反思', activity: '互动贡献', self_report: '学生自述', human_judgment: '人工判断', process: '过程' };
const statuses: Record<string, string> = { passed: '数值通过', failed: '数值未通过', unverifiable: '不可验证', skipped: '未完成', correct: '人工判定数值正确', incorrect: '人工判定数值错误', unresolved: '人工仍无法判断' };
const stages: Record<string, string> = { diagnostic: '初始诊断', practice: '练习', checkpoint: '检测提交（含重复/曝光未知）', review: '原题复做', roundtable: '圆桌讨论', teach: '我教 AI' };

export function EvidencePage({ workspace }: { workspace?: Workspace }) {
  const [scope, setScope] = useState<'global' | 'workspace'>('global'), [days, setDays] = useState<14 | 30 | 90>(30);
  const [source, setSource] = useState<EvidenceSource>('all'), [category, setCategory] = useState<EvidenceCategory>('all'), [offset, setOffset] = useState(0);
  const [assistance,setAssistance]=useState('all');
  const [trace, setTrace] = useState<Trace>(), [includeArchived,setIncludeArchived] = useState(true);
  const query = useQuery({ queryKey: ['evidence', scope, workspace?.workspace_id, days, source, category, offset, includeArchived,assistance], staleTime: 0, refetchInterval: 5000,
    queryFn: ({ signal }) => api.evidence(scope === 'workspace' ? workspace?.workspace_id : undefined, days, source, category, offset, signal,includeArchived,assistance) });
  const insights = useQuery({ queryKey: ['learning-insights',scope,workspace?.workspace_id,days,source,includeArchived,assistance], staleTime: 0, refetchInterval: 5000, queryFn: ({signal}) => api.learningInsights(scope==='workspace'?workspace?.workspace_id:undefined,days,source,includeArchived,signal,assistance) });
  function changeScope(value: 'global' | 'workspace') { setScope(value); setOffset(0); }
  return <>{workspace && <GoalPanel workspace={workspace}/>} <div className="actions section-gap"><Link className="button" to={"/planner?workspace=" + (workspace?.workspace_id ?? "")}>学习建议与策略</Link><Link className="button" to={"/skill-evolution?workspace=" + (workspace?.workspace_id ?? "")}>Skill 改进与评测</Link></div><Heading kicker="GROWTH, WITH ITS SOURCES" title="成长证据" description="把过程看清，把依据留下；每个统计都能回到原始记录。"/>
    <div className="tabs" role="tablist" aria-label="证据范围"><button role="tab" aria-selected={scope === 'global'} onClick={() => changeScope('global')}>全局宏观</button><button role="tab" aria-selected={scope === 'workspace'} disabled={!workspace} onClick={() => changeScope('workspace')}>当前空间局部</button></div>
    <div className="notice"><ChartNoAxesCombined size={20}/><div>{scope === 'global' ? `汇集你的正式项目，${includeArchived?'包含':'排除'}归档项目，排除临时探索。` : workspace?.kind === 'temporary' ? '临时空间只展示局部过程，不并入全局正式项目。' : `仅查看「${workspace?.title}」的记录。`}<br/>模拟、成人测试、真实学生来源分别统计；数值核验、学生自述和人工判断分开保留。</div></div>
    <div className="evidence-filters section-gap"><label className="field">时间范围<select value={days} onChange={e => { setDays(Number(e.target.value) as typeof days); setOffset(0); }}><option value={14}>最近 14 天</option><option value={30}>最近 30 天</option><option value={90}>最近 90 天</option></select></label><label className="field">数据来源<select value={source} onChange={e => { setSource(e.target.value as EvidenceSource); setOffset(0); }}><option value="all">全部来源（分别展示）</option>{Object.entries(sources).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label><label className="field">辅助条件<select value={assistance} onChange={e=>{setAssistance(e.target.value);setOffset(0);}}><option value="all">全部条件（分类展示）</option><option value="unassisted_practice">本轮未用提示的练习</option><option value="assisted">辅助后与互动表达</option><option value="independent">首次曝光独立检测</option></select></label><button className="button" disabled={query.isFetching} onClick={() => { void query.refetch(); void insights.refetch(); }}>刷新证据</button></div>
    {query.isPending ? <Loading text="正在核对这个范围的学习记录…"/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : <>
      <p className="page-note">{query.data.workspace_count} 个空间 · 日期按 UTC+08:00 自然日 · 截至 {new Date(query.data.as_of * 1000).toLocaleString()}。来源列表分页不影响上方完整范围的统计。</p>
      {scope==='global'&&<label className="inline section-gap"><input type="checkbox" checked={includeArchived} onChange={e=>{setIncludeArchived(e.target.checked);setOffset(0);}}/>全局包含归档项目</label>}
      {insights.isPending?<Loading text="正在整理知识点与支架来源…"/>:insights.isError?<ErrorNotice error={insights.error}/>:<LearningAnalysis key={scope+workspace?.workspace_id+days+source+includeArchived+assistance} report={insights.data} trace={setTrace} workspaceId={scope==='workspace'&&workspace?.kind==='project'?workspace.workspace_id:undefined}/>}
      {query.data.groups.length ? <>
        <div className="grid section-gap">{query.data.groups.map(group => <section className="card" key={group.source_kind}><div className="card-head"><h2>{sources[group.source_kind] ?? group.source_kind} · 过程概览</h2><span className="badge neutral">来源独立统计</span></div><div className="evidence-kpis">{[
          ['学习事实', group.facts], ['作答与原题复做', group.answers], ['陪学首次曝光检测数值通过', `${group.checkpoint_passed} / ${group.checkpoint_answers}`], ['提示请求', group.hints], ['反思', group.reflections], ['互动贡献', group.activity_contributions], ['学生自述', group.self_reports], ['人工判断', group.human_judgments],
        ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><p className="card-note">统计单位为提交/记录次数。同题多次尝试分别保留；通过数不等于知识点覆盖、掌握度或素养。</p></section>)}</div>
        <section className="card section-gap"><h2>数值核验的来源与阶段</h2><div className="evidence-table-wrap"><table className="evidence-table"><caption>只统计作答和原题复做；互动后的核验不混入独立检测。</caption><thead><tr><th>来源</th><th>阶段</th><th>通过</th><th>未通过</th><th>不可验证</th></tr></thead><tbody>{query.data.stages.map(row => <tr key={row.source_kind + row.stage}><td>{sources[row.source_kind] ?? row.source_kind}</td><td>{stages[row.stage] ?? row.stage}</td><td>{row.passed}</td><td>{row.failed}</td><td>{row.unverifiable}</td></tr>)}</tbody></table></div>{!query.data.stages.length && <p className="card-note">当前只有过程材料，尚无这些阶段的数值提交。</p>}</section>
        <EvidenceCalendar report={query.data}/><Trend key={scope + workspace?.workspace_id + days + source + includeArchived} report={query.data}/>
      </> : <section className="card section-gap"><Empty title="当前范围还没有过程证据">调整时间或来源筛选，或在陪学、复习、圆桌和我教 AI 中留下自己的尝试。没有记录不会显示为能力零分。</Empty></section>}
      <section className="card section-gap"><div className="card-head"><h2>证据来源记录</h2><FileSearch size={20}/></div><label className="field evidence-record-filter">记录类型<select value={category} onChange={e => { setCategory(e.target.value as EvidenceCategory); setOffset(0); }}><option value="all">全部记录</option>{Object.entries(kinds).filter(([key]) => key !== 'process').map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><p className="card-note">当前筛选 {query.data.record_count} 条。自述反映学生体验，人工判断只代表所写的复核范围，均不覆盖原始自动核验。</p>
        {query.data.records.length ? <ol className="evidence-records">{query.data.records.map(record => <li key={record.record_kind + record.record_id}><div className="card-head"><strong>{record.workspace_title}{record.archived ? ' · 已归档' : ''}</strong><span className="badge neutral">{sources[record.source_kind] ?? record.source_kind} · {kinds[record.record_kind] ?? '过程'}</span></div><p className="card-note">{record.task_id} · {stages[record.stage] ?? record.stage} · {new Date(record.created_at * 1000).toLocaleString()}</p><p className="evidence-excerpt">{record.excerpt}</p><div className="card-head"><span>{record.status ? statuses[record.status] ?? record.status : '过程记录 · 不作能力判断'}</span><button className="button small" onClick={() => setTrace({ wid: record.workspace_id, fid: record.fact_id, archived: record.archived })}>查看原始来源</button></div></li>)}</ol> : <Empty title="此类型暂时没有记录">其他类型或时间范围可能有记录；这不代表对应能力为零。</Empty>}
        <div className="pagination section-gap"><button className="button" disabled={offset === 0 || query.isFetching} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><span>第 {offset / 20 + 1} 页</span><button className="button" disabled={offset + 20 >= query.data.record_count || query.isFetching} onClick={() => setOffset(offset + 20)}>下一页</button></div>
      </section>
    </>}
    {trace && <SourceDialog key={trace.wid + trace.fid} trace={trace} close={() => {setTrace(undefined);void query.refetch();void insights.refetch();}}/>}
  </>;
}

function Trend({ report }: { report: EvidenceReport }) {
  const [source, setSource] = useState(report.groups[0].source_kind), [metric, setMetric] = useState<'answers' | 'hints' | 'checkpoint_answers'>('answers'), [selected, setSelected] = useState(report.days - 1);
  const metrics = { answers: '作答与原题复做', hints: '提示请求', checkpoint_answers: '首次曝光检测提交' };
  const rows = Array.from({ length: report.days }, (_, index) => {
    const day = new Date((report.starts_at + index * 86400 + 8 * 3600) * 1000).toISOString().slice(0, 10);
    return { day, value: report.daily.find(row => row.day === day && row.source_kind === source)?.[metric] ?? 0 };
  });
  const max = Math.max(1, ...rows.map(row => row.value));
  const x = (index: number) => 35 + index / Math.max(1, rows.length - 1) * 630, y = (value: number) => 180 - value / max * 140;
  return <section className="card section-gap"><h2>过程趋势 · 按天查看</h2><div className="evidence-filters"><label className="field">趋势来源<select value={source} onChange={e => setSource(e.target.value)}>{report.groups.map(group => <option key={group.source_kind} value={group.source_kind}>{sources[group.source_kind] ?? group.source_kind}</option>)}</select></label><label className="field">趋势指标<select value={metric} onChange={e => setMetric(e.target.value as typeof metric)}>{Object.entries(metrics).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label></div>
    <p className="card-note">这是记录次数的走势，不是能力变化。0 表示当日没有此类记录，不代表退步；不同题目和尝试不能直接作为学习增益比较。</p>
    <svg className="evidence-trend" viewBox="0 0 700 220" role="group" aria-label="每日过程次数趋势，选择圆点查看当天数量"><line x1="35" y1="180" x2="665" y2="180" stroke="#cbd3c8"/><text x="5" y="45" fontSize="12" fill="currentColor">{max} 次</text><text x="12" y="185" fontSize="12" fill="currentColor">0</text><polyline points={rows.map((row, index) => `${x(index)},${y(row.value)}`).join(' ')} fill="none" stroke="#4d725e" strokeWidth="2"/>{rows.map((row, index) => <circle key={row.day} cx={x(index)} cy={y(row.value)} r={selected === index ? 6 : 4} fill={selected === index ? '#a47c30' : '#4d725e'} tabIndex={0} role="button" aria-label={`${row.day} ${metrics[metric]} ${row.value} 次`} onClick={() => setSelected(index)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelected(index); } }}><title>{row.day}：{row.value} 次</title></circle>)}<text x="35" y="210" fontSize="12" fill="currentColor">{rows[0].day.slice(5)}</text><text x="665" y="210" textAnchor="end" fontSize="12" fill="currentColor">{rows.at(-1)!.day.slice(5)}</text></svg>
    <p role="status">{rows[selected].day} · {sources[source] ?? source} · {metrics[metric]} {rows[selected].value} 次</p><details><summary>查看趋势数据表</summary><div className="evidence-table-wrap"><table className="evidence-table"><caption>{sources[source] ?? source} · {metrics[metric]}（次数）</caption><thead><tr><th>日期</th><th>次数</th></tr></thead><tbody>{rows.map(row => <tr key={row.day}><td>{row.day}</td><td>{row.value}</td></tr>)}</tbody></table></div></details>
  </section>;
}
