import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@wenjin/api-client';
import { ErrorNotice, Loading, Success } from '@wenjin/ui';

export function ModelRuntimePanel() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['model-runtime'], queryFn: ({ signal }) => api.modelRuntime(signal), refetchInterval: 5000 });
  const [vendor, setVendor] = useState<'deepseek' | 'fake'>('deepseek');
  const [promptVersion, setPromptVersion] = useState<'probability.followup.v1' | 'probability.followup.v2' | 'probability.followup.v3'>('probability.followup.v1');
  const [accountLimit, setAccountLimit] = useState(0), [siteLimit, setSiteLimit] = useState(0);
  const [tokens, setTokens] = useState(512), [acknowledged, setAcknowledged] = useState(false);
  const [notice, setNotice] = useState('');
  const mutation = useMutation({ mutationFn: (action: 'publish' | 'pause') => action === 'pause'
    ? api.pauseModel(query.data!.revision)
    : api.publishModel({ vendor, prompt_version: promptVersion, expected_revision: query.data!.revision, account_daily_calls: accountLimit,
      site_daily_calls: siteLimit, max_output_tokens: tokens, acknowledge_development_only: true }),
    onSuccess: (data, action) => { client.setQueryData(['model-runtime'], data); setNotice(action === 'pause' ? '模型追问已停用，旧任务结果将被拦截。' : '开发模型配置已发布，用于概率陪学与我教 AI 开发追问。'); },
    onError: () => { void query.refetch(); } });
  if (query.isPending) return <Loading text="正在读取模型配置…"/>;
  if (query.isError) return <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/>;
  const state = query.data, active = state.active;
  const status: Record<string, string> = { waiting: '等待教学步骤', pending: '等待调用', running: '模型处理中', succeeded: '已保存建议', failed: '失败，保留预设提示', cancelled: '已取消', discarded: '过期结果已拦截', unknown: '结果未知，不自动重试' };
  return <section className="card section-gap"><div className="card-head"><h2>模型与运行配置</h2><span className="badge">{active ? `${active.vendor === 'fake' ? '模拟' : 'DeepSeek'} · 已开放开发测试` : '已停用'}</span></div>
    <p className="body-copy">仅自编概率陪学与概率我教 AI，允许模拟/成人测试账号。诊断与独立检测不调用模型。单轮发送题目与本轮思路；多轮最多增加同题、同空间、同配置下 3 轮已完成交流，含本轮最多 4 轮。我教 AI 仅发送当前题目、数值表达与本次讲解，由模型生成一个待核验问题。所有模型追问均不作为能力评价。</p>
    <p className="card-note">当前追问模式：{active ? active.prompt_version === 'probability.followup.v3' ? '同题最多 4 轮 · v3 候选版' : active.prompt_version === 'probability.followup.v2' ? '同题最多 4 轮 · v2 多轮版' : '单轮开发版' : '未开放'}。v2 已有真实评测及未通过的历史对照；v3 为待审核候选，须结合评测报告决定是否发布。发布新配置会重新开始收集历史。</p>
    <label className="field section-gap">追问模式<select value={promptVersion} disabled={mutation.isPending} onChange={e => setPromptVersion(e.target.value as typeof promptVersion)}><option value="probability.followup.v1">单轮开发版</option><option value="probability.followup.v2">同题最多 4 轮 · v2 多轮版（历史对照未通过）</option><option value="probability.followup.v3">同题最多 4 轮 · v3 候选版（需审核后发布）</option></select></label>
    <dl className="definition-list"><div><dt>当前配置版本</dt><dd>{active?.configuration_id ?? '尚未发布'}</dd></div><div><dt>当前型号</dt><dd>{active?.model ?? '—'}</dd></div><div><dt>DeepSeek 本机密钥</dt><dd>{state.deepseek_key_configured ? '已配置（不返回原值）' : '未配置或服务无读取权限；请检查本机密钥配置'}</dd></div><div><dt>当前调用额度</dt><dd>{active ? `每账号 ${active.account_daily_calls === 0 ? "不限次数" : `${active.account_daily_calls} 次/日`}，全站 ${active.site_daily_calls === 0 ? "不限次数" : `${active.site_daily_calls} 次/日`}，输出上限 ${active.max_output_tokens} tokens` : '未开放'}</dd></div></dl>
    <div className="notice"><div>今日（北京时间）{state.budget_day}：已预约 {state.reserved_calls} 次；已知输入 {state.input_tokens_known} / 输出 {state.output_tokens_known} tokens。{state.missing_usage_calls} 次用量不完整，{state.billing_unknown_calls} 次费用状态未知。每日零点重置，预约后不返还次数，不自动重试；此处不是人民币账单。</div></div>
    <form className="form-stack section-gap" onSubmit={event => { event.preventDefault(); if (acknowledged) mutation.mutate('publish'); }}>
      <div className="grid two"><label className="field">开发服务<select value={vendor} disabled={mutation.isPending} onChange={e => setVendor(e.target.value as 'deepseek' | 'fake')}><option value="deepseek">DeepSeek · 真实开发模型</option><option value="fake">Fake · 模拟测试（不出网）</option></select></label><label className="field">每次输出 tokens<input type="number" min={64} max={512} required value={tokens} onChange={e => setTokens(Number(e.target.value))}/></label><label className="field">每账号每日次数（0 为不限）<input type="number" min={0} max={30} required value={accountLimit} onChange={e => setAccountLimit(Number(e.target.value))}/></label><label className="field">全站每日次数（0 为不限）<input type="number" min={0} max={100} required value={siteLimit} onChange={e => setSiteLimit(Number(e.target.value))}/></label></div>
      <label className="inline"><input type="checkbox" checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)}/>确认仅用于开发测试，并已核对外发范围与额度</label>
      <div className="actions"><button className="button primary" disabled={mutation.isPending || !acknowledged || (vendor === 'deepseek' && !state.deepseek_key_configured)}>发布开发模型配置</button><button type="button" className="button" disabled={mutation.isPending || !active} onClick={() => mutation.mutate('pause')}>停用模型追问</button></div>
    </form>
    <ErrorNotice error={mutation.error}/>{notice && <Success>{notice}</Success>}
    <h3 className="section-gap">最近 20 次模型任务</h3><p className="card-note">保留配置与调用状态；不在管理概览展示学生原文。发布新配置不改写旧任务；停用会阻止旧结果生效。</p>
    {state.recent.length ? <div className="table-scroll"><table><thead><tr><th>任务</th><th>服务 / 型号</th><th>状态</th><th>已知 token</th><th>耗时</th></tr></thead><tbody>{state.recent.map(op => <tr key={op.operation_id}><td>{op.task_id}</td><td>{op.simulated ? '模拟 · ' : ''}{op.model}</td><td>{status[op.status]}{op.error_code ? `（${op.error_code}）` : ''}</td><td>{op.usage?.input_tokens ?? '未知'} / {op.usage?.output_tokens ?? '未知'}</td><td>{op.elapsed_ms === null ? '—' : `${op.elapsed_ms} ms`}</td></tr>)}</tbody></table></div> : <p className="card-note">尚无模型任务。</p>}
  </section>;
}
