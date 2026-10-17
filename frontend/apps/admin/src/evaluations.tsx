import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@wenjin/api-client';
import { ErrorNotice, Heading, Loading } from '@wenjin/ui';
import { ReviewForm } from './development-review';

export function EvaluationsPage() {
  const client = useQueryClient(), [selected, setSelected] = useState('single-baseline'), [failuresOnly, setFailuresOnly] = useState(false);
  const list = useQuery({ queryKey: ['evaluation-reports'], queryFn: ({ signal }) => api.evaluationReports(signal) });
  const detail = useQuery({ queryKey: ['evaluation-report', selected], queryFn: ({ signal }) => api.evaluationReport(selected, signal) });
  return <><Heading kicker="EVIDENCE BEFORE RELEASE" title="评测工作台" description="查看冻结的开发运行，逐条复核，不用模拟结果替代真实模型表现。"/>
    <div className="notice">此处不发起模型调用。复核绑定报告哈希和样本，不自动修改提示或模型配置；真实多轮质量仍待复测。</div>
    {list.isPending ? <Loading/> : list.isError ? <ErrorNotice error={list.error}/> : <><p className="card-note">{list.data.notice}</p><label className="field section-gap">评测运行<select value={selected} onChange={e => { setSelected(e.target.value); setFailuresOnly(false); }}>{list.data.items.map(report => <option key={report.report_id} value={report.report_id}>{report.title} · {report.mode === 'fake' ? '模拟' : '真实开发模型'} · 已复核 {report.reviewed_cases}/{report.cases}</option>)}</select></label></>}
    {detail.isPending ? <Loading/> : detail.isError ? <ErrorNotice error={detail.error} retry={() => { void detail.refetch(); }}/> : <>
      <section className="card section-gap"><div className="card-head"><h2>{detail.data.title}</h2><span className="badge">{detail.data.mode === 'fake' ? 'Fake · 仅工程验证' : '真实模型 · 自编开发样本'}</span></div>
        <p>提示版本：{detail.data.prompt_version}；样本 {detail.data.cases.length} 条。未经教师金标审核。</p><p className="card-note" style={{ overflowWrap: 'anywhere' }}>报告 SHA-256：{detail.data.report_sha256}</p>
        <label className="inline"><input type="checkbox" checked={failuresOnly} onChange={e => setFailuresOnly(e.target.checked)}/>仅显示不符合开发参考的条目</label>
      </section>
      {detail.data.cases.filter(row => !failuresOnly || !row.case.direction_match).map(row => <section key={selected + ':' + row.target_id} className="card section-gap">
        <div className="card-head"><h3>{row.target_id}</h3><span className="badge neutral">{row.case.direction_match ? '符合开发参考' : '待复核'} · {row.case.status}</span></div>
        <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}><strong>自编输入：</strong>{row.case.text}</p>
        <p>开发参考：{row.case.expected_focus.join(' / ')}；实际方向：{row.case.selected_focus ?? '无结果'}</p><p className="card-note">开发依据：{row.case.rationale}</p>
        <blockquote style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>引用：{row.case.quote || '未返回有效引用'}</blockquote><p>追问：{row.case.question ?? '未返回有效追问'}</p>
        <p className="card-note">结构 {row.case.contract_pass ? '通过' : '未通过'} · 引用 {row.case.citation_pass ? '通过' : '未通过'} · 方向权限 {row.case.policy_pass ? '通过' : '未通过'} · {row.case.elapsed_ms ?? '未知'} ms{row.case.error_code ? ` · ${row.case.error_code}` : ''}</p>
        <ReviewForm kind="evaluation" target={row.target_id} version={detail.data.report_sha256} revision={row.revision} reviews={row.reviews}
          saved={() => { void client.invalidateQueries({ queryKey: ['evaluation-report', selected] }); void client.invalidateQueries({ queryKey: ['evaluation-reports'] }); }}/>
      </section>)}
    </>}
  </>;
}
