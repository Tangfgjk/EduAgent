import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type EvaluationIndex } from '@wenjin/api-client';
import { ErrorNotice, Heading, Loading } from '@wenjin/ui';
import { ReviewForm } from './development-review';

const gateNames = { simulation_only: '仅模拟 · 不用于模型准入', blocked: '评测检查未通过', needs_review: '待逐条人工复核', development_candidate: '可作开发候选 · 非正式教学发布' };
const metrics = [['primary_cases', '主样本数'], ['total_attempts', '全部尝试数'], ['contract_pass', '主样本结构通过'],
  ['direction_match', '主样本方向匹配'], ['input_tokens_known', '已知输入 tokens'], ['output_tokens_known', '已知输出 tokens'],
  ['missing_usage_attempts', '用量不完整条目'], ['latency_median_ms', '耗时中位数 ms'], ['latency_max_ms', '最大耗时 ms'],
  ['repeat_pairs', '预定重复对数'], ['repeat_same_focus', '重复方向相同'], ['repeat_both_direction_match', '重复两次均匹配参考']];
const metric = (data: Record<string, unknown>, key: string) => typeof data[key] === 'number' ? String(data[key]) : '—';

export function EvaluationsPage() {
  const client = useQueryClient(), [selected, setSelected] = useState('teach-live'), [failuresOnly, setFailuresOnly] = useState(false);
  const list = useQuery({ queryKey: ['evaluation-reports'], queryFn: ({ signal }) => api.evaluationReports(signal) });
  const detail = useQuery({ queryKey: ['evaluation-report', selected], queryFn: ({ signal }) => api.evaluationReport(selected, signal) });
  return <><Heading kicker="EVIDENCE BEFORE RELEASE" title="评测工作台" description="查看冻结的开发运行，逐条复核，不用模拟结果替代真实模型表现。"/>
    <div className="notice">此处只读冻结报告，不发起模型调用。复核绑定报告哈希和样本；自动检查、参考方向匹配与问题语义复核分别记录，不自动发布或修改当前模型开关。</div>
    {list.isPending ? <Loading/> : list.isError ? <ErrorNotice error={list.error}/> : <><p className="card-note">{list.data.notice}</p><label className="field section-gap">评测运行<select aria-label="评测运行" value={selected} onChange={e => { setSelected(e.target.value); setFailuresOnly(false); }}>{list.data.items.map(report => <option key={report.report_id} value={report.report_id}>{report.title} · {report.mode === 'fake' ? '模拟' : '真实开发模型'} · 已复核 {report.reviewed_cases}/{report.cases}</option>)}</select></label></>}
    {list.data && <Comparison selected={selected} reports={list.data.items}/>}
    {detail.isPending ? <Loading/> : detail.isError ? <ErrorNotice error={detail.error} retry={() => { void detail.refetch(); }}/> : <>
      <section className="card section-gap"><div className="card-head"><h2>{detail.data.title}</h2><span className="badge">{detail.data.mode === 'fake' ? 'Fake · 仅工程验证' : '真实模型 · 自编开发样本'}</span></div>
        <p>提示版本：{detail.data.prompt_version}；样本 {detail.data.cases.length} 条。未经教师金标审核。</p><p className="card-note" style={{ overflowWrap: 'anywhere' }}>报告 SHA-256：{detail.data.report_sha256}</p>
        <h3>{gateNames[detail.data.assessment.state]}</h3><p>工程失败 {detail.data.assessment.engineering_failures} · 参考不匹配 {detail.data.assessment.reference_mismatches} · 待复核 {detail.data.assessment.pending_reviews} · 复核问题 {detail.data.assessment.review_issues}</p><p className="card-note">{detail.data.assessment.notice}</p>
        {detail.data.report_kind === 'planning' && <p className="notice">规划报告的 source_contract 仅表示字段与引用规则；指标中的匹配数不是建议质量或教学效果评分。</p>}<p>全部尝试中返回可用结果：{detail.data.cases.filter(row => row.case.status === 'succeeded').length} / {detail.data.cases.length}。失败及重复均保留。</p>
        <details><summary>查看运行指标与重复稳定性</summary><table><tbody>{metrics.map(([key, label]) => <tr key={key}><th>{label}</th><td>{metric(detail.data.summary, key)}</td></tr>)}</tbody></table><p className="card-note">方向相同不等于问题语义相同；已知 token 数不是金额账单。</p></details>
        {['teach', 'roundtable', 'planning'].includes(detail.data.report_kind) && <><h3 className="section-gap">{detail.data.report_kind === 'planning' ? '规划 · 引用与建议语义复核' : detail.data.report_kind === 'roundtable' ? '圆桌 · 三角色分工与语义复核' : '我教 AI · 逐条语义复核维度'}</h3><ul>{detail.data.human_review_rubric.map(item => <li key={item}>{item}</li>)}</ul><p className="card-note">即使自动方向匹配，生成的问题仍需逐条阅读并记录复核依据；当前均为开发复核。</p></>}
        <label className="inline"><input type="checkbox" checked={failuresOnly} onChange={e => setFailuresOnly(e.target.checked)}/>仅显示不符合开发参考的条目</label>
      </section>
      {detail.data.cases.filter(row => !failuresOnly || !row.case.direction_match).map(row => <section key={selected + ':' + row.target_id} className="card section-gap">
        <div className="card-head"><h3>{row.target_id}</h3><span className="badge neutral">{row.case.direction_match ? detail.data.report_kind === 'planning' ? '字段与引用规则通过' : '符合开发参考' : '待复核'} · {row.case.status}</span></div>
        <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}><strong>自编输入：</strong>{row.case.text}</p>
        {row.case.answer != null && <p>自编候选数值：{row.case.answer}（不是模型评分）</p>}{row.case.repeat_of && <p className="card-note">预定重复：{row.case.repeat_of}。独立保留本次输出。</p>}
        {detail.data.report_kind === 'planning' ? <p>仅检查字段与引用约束，不评价规划建议是否合适。</p> : <p>开发参考：{row.case.expected_focus.join(' / ')}；实际方向：{row.case.selected_focus ?? '无结果'}</p>}<p className="card-note">开发依据：{row.case.rationale}</p>
        <blockquote style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>引用：{row.case.quote || '未返回有效引用'}</blockquote>{(row.case.voices ?? []).length ? <ol>{row.case.voices?.map(voice => <li key={voice.role}><strong>{{ proposer: '思路提出者', critic: '质疑者', organizer: '整理者' }[voice.role]}</strong><blockquote>{voice.quote}</blockquote><p>{voice.text}</p></li>)}</ol> : <p>追问：{row.case.question ?? '未返回有效追问'}</p>}
        <p className="card-note">结构 {row.case.contract_pass ? '通过' : '未通过'} · 引用 {row.case.citation_pass ? '通过' : '未通过'} · 方向权限 {row.case.policy_pass ? '通过' : '未通过'} · {row.case.elapsed_ms ?? '未知'} ms{row.case.error_code ? ` · ${row.case.error_code}` : ''}</p>
        {row.case.policy && <p>候选：每日 {row.case.policy.daily_minutes} 分钟；反思重点 {row.case.policy.reflection_focus}</p>}{(row.case.citations ?? []).map(citation => <article key={citation.source_id}><strong>{citation.source_id}</strong><blockquote>{citation.quote}</blockquote><p>{citation.reason}</p><details><summary>核对模型读取片段</summary><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.case.source_excerpts?.[citation.source_id]}</p></details></article>)}
        <ReviewForm kind="evaluation" target={row.target_id} version={detail.data.report_sha256} revision={row.revision} reviews={row.reviews}
          saved={() => { void client.invalidateQueries({ queryKey: ['evaluation-report', selected] }); void client.invalidateQueries({ queryKey: ['evaluation-reports'] }); }}/>
      </section>)}
    </>}
  </>;
}

function Comparison({ selected, reports }: { selected: string; reports: EvaluationIndex['items'] }) {
  const [choice, setChoice] = useState(''), [changesOnly, setChangesOnly] = useState(true);
  const left = reports.find(report => report.report_id === selected);
  const compatible = reports.filter(report => report.report_id !== selected && report.corpus_sha256 === left?.corpus_sha256
    && report.mode === left?.mode && report.model === left?.model && report.report_kind === left?.report_kind);
  const right = compatible.some(item => item.report_id === choice) ? choice : compatible[0]?.report_id ?? '';
  const query = useQuery({ queryKey: ['evaluation-comparison', selected, right], enabled: !!right,
    queryFn: ({ signal }) => api.evaluationComparison(selected, right, signal) });
  return <section className="card section-gap"><h2>同语料版本对比</h2><p className="card-note">以当前选定报告为左侧，只提供同语料、型号、模式与路线的对照。服务端进一步核对内容版本、样本集合、输入与参考。</p>
    {!right ? <p>此报告暂无同条件对照。不能把 Fake 与真实模型或不同语料直接比较。</p> : <><label className="field">右侧对照报告<select aria-label="右侧对照报告" value={right} onChange={e => setChoice(e.target.value)}>{compatible.map(report => <option key={report.report_id} value={report.report_id}>{report.title}</option>)}</select></label>
      {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error}/> : <><p>左：{query.data.left_prompt} → 右：{query.data.right_prompt}</p><strong>改善 {query.data.improved} · 退步 {query.data.regressed} · 未变化 {query.data.unchanged}</strong><p className="card-note">{query.data.notice}</p>
        <details><summary>查看左右运行指标</summary><div className="table-scroll"><table><thead><tr><th>指标</th><th>左侧</th><th>右侧</th></tr></thead><tbody>{metrics.map(([key, label]) => <tr key={key}><th>{label}</th><td>{metric(query.data.left_summary, key)}</td><td>{metric(query.data.right_summary, key)}</td></tr>)}</tbody></table></div></details>
        <label className="inline"><input type="checkbox" checked={changesOnly} onChange={e => setChangesOnly(e.target.checked)}/>只看方向匹配状态变化</label>
        {query.data.cases.filter(row => !changesOnly || row.left_match !== row.right_match).map(row => <article key={row.case_id} className="section-gap"><h3>{row.case_id}</h3><p>左：{row.left_focus ?? '无结果'} · {row.left_status} · {row.left_match ? '匹配参考' : '未匹配'} → 右：{row.right_focus ?? '无结果'} · {row.right_status} · {row.right_match ? '匹配参考' : '未匹配'}</p><p>左侧追问：{row.left_question ?? '无可用结果'}</p><p>右侧追问：{row.right_question ?? '无可用结果'}</p></article>)}
      </>}
    </>}
  </section>;
}
