import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@wenjin/api-client';
import { ErrorNotice, Loading, Success } from '@wenjin/ui';

export function ModelRuntimePanel() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['model-runtime'], queryFn: ({ signal }) => api.modelRuntime(signal), refetchInterval: 5000 });
  const [vendor, setVendor] = useState<Parameters<typeof api.publishModel>[0]['vendor']>('deepseek');
  const [model,setModel]=useState('');
  const [promptVersion, setPromptVersion] = useState<'probability.followup.v1' | 'probability.followup.v2' | 'probability.followup.v3'>('probability.followup.v1');
  const [accountLimit, setAccountLimit] = useState(0), [siteLimit, setSiteLimit] = useState(0);
  const [tokens, setTokens] = useState(512), [acknowledged, setAcknowledged] = useState(false);
  const [notice, setNotice] = useState('');
  const mutation = useMutation({ mutationFn: (action: 'publish' | 'pause') => action === 'pause'
    ? api.pauseModel(query.data!.revision)
    : api.publishModel({ vendor, ...(model.trim()?{model:model.trim()}:{}), prompt_version: promptVersion, expected_revision: query.data!.revision, account_daily_calls: accountLimit,
      site_daily_calls: siteLimit, max_output_tokens: tokens, acknowledge_development_only: true }),
    onSuccess: (data, action) => { client.setQueryData(['model-runtime'], data); setNotice(action === 'pause' ? '模型追问已停用，旧任务结果将被拦截。' : '开发模型配置已发布，按已开放用途运行；新服务商需独立验收。'); },
    onError: () => { void query.refetch(); } });
  if (query.isPending) return <Loading text="正在读取模型配置…"/>;
  if (query.isError) return <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/>;
  const state = query.data, active = state.active;
  const mediaProvider = state.provider_catalog?.find(p => p.vendor === 'qwen');
  const status: Record<string, string> = { waiting: '等待教学步骤', pending: '等待调用', running: '模型处理中', succeeded: '已保存建议', failed: '失败，保留预设提示', cancelled: '已取消', discarded: '过期结果已拦截', unknown: '结果未知，不自动重试' };
  return <section className="card section-gap"><div className="card-head"><h2>模型与运行配置</h2><span className="badge">{active ? `${active.vendor === 'fake' ? '模拟' : 'DeepSeek'} · 已开放开发测试` : '已停用'}</span></div>
    <p className="body-copy">允许模拟/成人测试账号；原概率路线继续使用，新增通用课程与互动用途在“课程生成与资源”单独开启。诊断与独立检测不调用模型。单轮发送题目与本轮思路；多轮最多增加同题、同空间、同配置下 3 轮已完成交流，含本轮最多 4 轮。我教 AI 仅发送当前题目、数值表达与本次讲解，由模型选择讨论方向，以安全模板展示问题。所有模型追问均不作为能力评价。</p>
    <p className="card-note">当前追问模式：{active ? active.prompt_version === 'probability.followup.v3' ? '同题最多 4 轮 · v3 候选版' : active.prompt_version === 'probability.followup.v2' ? '同题最多 4 轮 · v2 多轮版' : '单轮开发版' : '未开放'}。v2 已有真实评测及未通过的历史对照；v3 为待审核候选，须结合评测报告决定是否发布。发布新配置会重新开始收集历史。</p>
    <h3>按用途核对外发范围与额度</h3><p className="card-note">陪学、互动、规划和课程生成共用账号/全站日额度；检索和 OpenMAIC 使用独立预算。0 表示不限次数；不代表免费或无限 tokens。</p><div className="table-scroll"><table><thead><tr><th>用途</th><th>状态与数据范围</th><th>展示与输出限制</th><th>预算</th></tr></thead><tbody>{(state.purpose_scopes ?? []).map(scope => <tr key={scope.purpose}><td>{scope.purpose}</td><td>{scope.enabled ? '已发布开发配置' : '未开放'} · {scope.data_scope}</td><td>{scope.output_scope}</td><td>{scope.max_output_tokens} tokens/次；账号 {scope.account_daily_calls ?? '—'} / 全站 {scope.site_daily_calls ?? '—'} 次/日；{scope.budget_group === 'shared_development_calls' ? '共用开发次数' : scope.budget_group === 'search_credits' ? '独立搜索额度' : '独立资源预算'}</td></tr>)}</tbody></table></div><p className="card-note">{state.verification_boundary}</p>
    <label className="field section-gap">追问模式<select value={promptVersion} disabled={mutation.isPending} onChange={e => setPromptVersion(e.target.value as typeof promptVersion)}><option value="probability.followup.v1">单轮开发版</option><option value="probability.followup.v2">同题最多 4 轮 · v2 多轮版（历史对照未通过）</option><option value="probability.followup.v3">同题最多 4 轮 · v3 候选版（需审核后发布）</option></select></label>
    <dl className="definition-list"><div><dt>当前配置版本</dt><dd>{active?.configuration_id ?? '尚未发布'}</dd></div><div><dt>当前型号</dt><dd>{active?.model ?? '—'}</dd></div><div><dt>DeepSeek 本机密钥</dt><dd>{state.deepseek_key_configured ? '已配置（不返回原值）' : '未配置或服务无读取权限；请检查本机密钥配置'}</dd></div><div><dt>当前调用额度</dt><dd>{active ? `每账号 ${active.account_daily_calls === 0 ? "不限次数" : `${active.account_daily_calls} 次/日`}，全站 ${active.site_daily_calls === 0 ? "不限次数" : `${active.site_daily_calls} 次/日`}，输出上限 ${active.max_output_tokens} tokens` : '未开放'}</dd></div></dl>
    <div className="notice"><div>今日（北京时间）{state.budget_day}：已预约 {state.reserved_calls} 次；已知输入 {state.input_tokens_known} / 输出 {state.output_tokens_known} tokens。{state.missing_usage_calls} 次用量不完整，{state.billing_unknown_calls} 次费用状态未知。每日零点重置，预约后不返还次数，不自动重试；此处不是人民币账单。</div></div>
    <form className="form-stack section-gap" onSubmit={event => { event.preventDefault(); if (acknowledged) mutation.mutate('publish'); }}>
      <div className="grid two"><label className="field">开发服务<select value={vendor} disabled={mutation.isPending} onChange={e => setVendor(e.target.value as typeof vendor)}><option value="deepseek">DeepSeek · 真实开发模型</option>{(state.provider_catalog??[]).filter(p=>p.vendor!=='deepseek').map(p=><option key={String(p.vendor)} value={String(p.vendor)}>{String(p.name)} · {p.key_configured?'密钥已配置':'未配置密钥'} · 真实验收未运行</option>)}<option value="fake">Fake · 模拟测试（不出网）</option></select></label><label className="field">模型ID（新服务商必填）<input maxLength={160} value={model} onChange={e=>setModel(e.target.value)} placeholder="请使用服务商实际可用的模型ID"/></label><label className="field">每次输出 tokens<input type="number" min={64} max={512} required value={tokens} onChange={e => setTokens(Number(e.target.value))}/></label><label className="field">每账号每日次数（0 为不限）<input type="number" min={0} max={30} required value={accountLimit} onChange={e => setAccountLimit(Number(e.target.value))}/></label><label className="field">全站每日次数（0 为不限）<input type="number" min={0} max={100} required value={siteLimit} onChange={e => setSiteLimit(Number(e.target.value))}/></label></div>
      <label className="inline"><input type="checkbox" checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)}/>确认仅用于开发测试，并已核对外发范围与额度</label>
      <div className="actions"><button className="button primary" disabled={mutation.isPending || !acknowledged || (vendor === 'deepseek' && !state.deepseek_key_configured)}>发布开发模型配置</button><button type="button" className="button" disabled={mutation.isPending || !active} onClick={() => mutation.mutate('pause')}>停用模型追问</button></div>
    </form>
    <ErrorNotice error={mutation.error}/>{notice && <Success>{notice}</Success>}
    <section className="soft-box section-gap" aria-label="百炼媒体 API 配置"><h3>百炼媒体 API 配置</h3><p>用于课堂图片、配音和视频；教学对话继续使用上方当前模型。</p><dl className="definition-list"><div><dt>服务端密钥</dt><dd>{mediaProvider?.key_configured ? '已配置（不返回原值）' : '未配置'}</dd></div><div><dt>兼容接口地址</dt><dd style={{overflowWrap:'anywhere'}}>{String(mediaProvider?.endpoint ?? '未读取')}</dd></div></dl><p>媒体使用相同域名的 DashScope 接口。密钥已配置不代表生成验收已通过；真实调用还需要课堂和独立预算。</p><p>图片：qwen-image-2.0-2026-03-03；配音：qwen3-tts-flash；视频：wan2.2-t2v-plus。</p><a className="button small" href="/admin/generation">查看课堂与预算</a></section>
    <h3 className="section-gap">最近 20 次模型任务</h3><p className="card-note">保留配置与调用状态；不在管理概览展示学生原文。发布新配置不改写旧任务；停用会阻止旧结果生效。</p>
    {state.recent.length ? <div className="table-scroll"><table><thead><tr><th>任务</th><th>服务 / 型号</th><th>状态</th><th>已知 token</th><th>耗时</th></tr></thead><tbody>{state.recent.map(op => <tr key={op.operation_id}><td>{op.task_id}</td><td>{op.simulated ? '模拟 · ' : ''}{op.model}</td><td>{status[op.status]}{op.error_code ? `（${op.error_code}）` : ''}</td><td>{op.usage?.input_tokens ?? '未知'} / {op.usage?.output_tokens ?? '未知'}</td><td>{op.elapsed_ms === null ? '—' : `${op.elapsed_ms} ms`}</td></tr>)}</tbody></table></div> : <p className="card-note">尚无模型任务。</p>}
  </section>;
}
