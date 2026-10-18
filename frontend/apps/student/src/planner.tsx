import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ActiveStrategy, type StrategyStart, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading, StrategyDetails, strategyPhases } from '@wenjin/ui';

export function PlannerPage({ workspace }: { workspace: Workspace }) {
  const client = useQueryClient(), [offset, setOffset] = useState(0), [materialOffset, setMaterialOffset] = useState(0), [selected, setSelected] = useState<string>();
  const [body, setBody] = useState<StrategyStart>({ daily_minutes: 15, reflection_focus: 'basis', learning_experience: '', material_ids: [], request_model: false });
  const intent = useRef({ signature: '', key: '' });
  const query = useQuery({ queryKey: ['strategies', workspace.workspace_id, offset], queryFn: ({ signal }) => api.strategies(workspace.workspace_id, offset, signal), staleTime: 0, refetchInterval: 2000 });
  const materials = useQuery({ queryKey: ['planning-materials', materialOffset], queryFn: ({ signal }) => api.planningMaterials(false, materialOffset, signal), staleTime: 0 });
  const active = useQuery({ queryKey: ['active-strategy', workspace.workspace_id], queryFn: ({ signal }) => api.activeStrategy(workspace.workspace_id, signal), staleTime: 0, refetchInterval: 2000 });
  const runs = query.data?.items ?? [], pending = runs.find(run => ['queued', 'awaiting_input'].includes(run.status));
  const rid = selected ?? pending?.run_id ?? runs[0]?.run_id;
  const detail = useQuery({ queryKey: ['strategy', workspace.workspace_id, rid], queryFn: ({ signal }) => api.strategy(rid!, workspace.workspace_id, signal), enabled: !!rid, staleTime: 0, refetchInterval: 2000 });
  function refresh() { void client.invalidateQueries({ queryKey: ['strategies'] }); void client.invalidateQueries({ queryKey: ['strategy'] }); void client.invalidateQueries({ queryKey: ['active-strategy'] }); }
  const start = useMutation({ mutationFn: () => {
    const signature = JSON.stringify(body); if (intent.current.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    return api.startStrategy(workspace.workspace_id, body, intent.current.key);
  }, onSuccess: run => { setOffset(0); setSelected(run.run_id); intent.current = { signature: '', key: '' }; refresh(); } });
  function submit(event: FormEvent) { event.preventDefault(); if (!start.isPending) start.mutate(); }
  const locked = workspace.status !== 'active' || !!pending || start.isPending;
  return <><Heading kicker="ADJUST WITH EVIDENCE" title="规划与策略" description="先核对依据，再调整软策略；经审核和你的确认后才生效。"/><div className="notice">开发规划 · 默认按你的选择生成固定提案；可明确选择由管理员开放的模型生成有来源候选。模型只读有界片段，不能判定教学效果。资料只作参考数据，教学硬规则不可被提案、资料或 Skill.md 改写。</div>
    {workspace.kind === 'temporary' ? <section className="card section-gap"><Empty title="策略规划从正式项目开始">临时空间的过程保留在原处，不自动作为正式项目的策略输入。</Empty></section> : <>
      {active.isPending ? <Loading/> : active.isError ? <ErrorNotice error={active.error}/> : <ActivePanel key={active.data.revision} active={active.data} archived={workspace.status !== 'active'} refresh={refresh}/>}
      {!pending && <section className="card section-gap"><h2>提出一次有依据的调整</h2><form className="form-stack" onSubmit={submit}><label className="field">我的学习体验<textarea value={body.learning_experience} onChange={e => setBody({ ...body, learning_experience: e.target.value })} minLength={2} maxLength={1000} required disabled={locked}/><small>作为学生自述引用，不当作客观诊断或已掌握证明。</small></label><div className="grid two"><label className="field">每日建议时长<input type="number" min={5} max={60} value={body.daily_minutes} onChange={e => setBody({ ...body, daily_minutes: Number(e.target.value) })} disabled={locked}/></label><label className="field">反思引导重点<select aria-label="反思引导重点" value={body.reflection_focus} disabled={locked} onChange={e => setBody({ ...body, reflection_focus: e.target.value as StrategyStart['reflection_focus'] })}><option value="basis">依据与方法回望</option><option value="verification">代入核验与检查</option></select></label></div><h3>选择已发布的课程参考资料（1–5 份）</h3>
        {materials.isPending ? <Loading/> : materials.isError ? <ErrorNotice error={materials.error}/> : materials.data.items.length ? <div>{materials.data.items.map(material => <label className="strategy-material" key={material.material_id}><input type="checkbox" checked={body.material_ids.includes(material.material_id)} disabled={locked || (!body.material_ids.includes(material.material_id) && body.material_ids.length >= 5)} onChange={e => setBody({ ...body, material_ids: e.target.checked ? [...body.material_ids, material.material_id] : body.material_ids.filter(id => id !== material.material_id) })}/><span><strong>{material.title}</strong><small>{material.edition} · {material.source_ref} · 未经专业审核</small></span></label>)}</div> : <Empty title="还没有已发布的规划资料">请管理员在“策略评审 → 课程资料”录入并发布有来源和版本的内容。</Empty>}
        <div className="pagination"><button type="button" className="button small" disabled={materialOffset === 0} onClick={() => setMaterialOffset(Math.max(0, materialOffset - 20))}>上一页资料</button><span>已选 {body.material_ids.length} 份</span><button type="button" className="button small" disabled={(materials.data?.items.length ?? 0) < 20} onClick={() => setMaterialOffset(materialOffset + 20)}>下一页资料</button></div><label className="inline"><input type="checkbox" checked={body.request_model ?? false} disabled={locked} onChange={e => setBody({ ...body, request_model: e.target.checked })}/>使用已开放模型生成有来源的候选</label>{body.request_model && <p className="card-note">仅模拟/成人开发用途：发送本次体验、本项目最近至多6条脱敏记录、3条自述和所选资料每份前800字；其他正式项目仅计数，排除临时会话。不自动重试，仍需管理员审核与你的确认。</p>}<button className="button primary" disabled={locked || !body.material_ids.length || body.learning_experience.trim().length < 2}>生成开发策略提案</button><ErrorNotice error={start.error}/></form></section>}
      {query.isError && <ErrorNotice error={query.error}/>} {rid && (detail.isPending ? <Loading/> : detail.isError ? <ErrorNotice error={detail.error}/> : <StrategyDetails key={detail.data.run_id} run={detail.data} refresh={refresh}/>)}
      <section className="card section-gap"><h2>项目策略版本</h2><div className="plan-history">{runs.map(run => <button className="button" key={run.run_id} onClick={() => setSelected(run.run_id)}>{new Date(run.created_at * 1000).toLocaleString()} · {run.status === 'queued' ? '正在处理' : run.status === 'failed' ? '已失效' : run.status === 'cancelled' ? '已取消' : strategyPhases[run.view?.phase ?? '']}</button>)}</div><div className="pagination"><button className="button" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - 20)); setSelected(undefined); }}>上一页</button><button className="button" disabled={runs.length < 20} onClick={() => { setOffset(offset + 20); setSelected(undefined); }}>下一页</button></div></section>
    </>}
  </>;
}

function ActivePanel({ active, archived, refresh }: { active: ActiveStrategy; archived: boolean; refresh: () => void }) {
  const [target, setTarget] = useState(''), [note, setNote] = useState('');
  const intent = useRef({ signature: '', key: '' });
  const rollback = useMutation({ mutationFn: () => {
    const signature = JSON.stringify([active.revision, target, note.trim()]); if (intent.current.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    return api.rollbackStrategy(active, target || null, note.trim(), intent.current.key);
  }, onSuccess: refresh, onError: refresh });
  const choices = [...new Set(active.history.map(row => row.to_version).filter((id): id is string => typeof id === 'string' && id !== active.version_id))];
  return <section className="card section-gap"><div className="card-head"><h2>当前学习软策略</h2><span className="badge neutral">{active.effective ? '已激活' : '平台默认'}</span></div><p>每日建议 {active.policy.daily_minutes} 分钟 · {active.policy.reflection_focus === 'verification' ? '反思侧重代入核验' : '反思侧重依据与方法'}</p><p className="card-note">当前指针：{active.version_id ?? '默认基线'} · 第 {active.revision} 次变更</p>{active.unavailable_reason && <div className="notice">原策略暂不可用（{active.unavailable_reason}），后续学习使用平台默认；历史指针仍保留。</div>}
    {active.history.length > 0 && <details><summary>查看激活历史与回滚</summary><p className="card-note">显示最近 50 次变更；回滚只影响后续学习。</p><ul className="detail-list">{active.history.map((row, index) => <li key={index}>{String(row.kind)} · {String(row.note)} · {row.to_version ? String(row.to_version) : '默认基线'}</li>)}</ul><label className="field">回滚目标<select aria-label="回滚目标" value={target} onChange={e => setTarget(e.target.value)} disabled={archived || rollback.isPending}><option value="">平台默认基线</option>{choices.map(id => <option key={id} value={id}>{id}</option>)}</select></label><label className="field">回滚理由<textarea value={note} onChange={e => setNote(e.target.value)} minLength={2} maxLength={1000} disabled={archived || rollback.isPending}/></label><button className="button" disabled={archived || rollback.isPending || note.trim().length < 2 || (target || null) === active.version_id} onClick={() => rollback.mutate()}>确认回滚</button><ErrorNotice error={rollback.error}/></details>}
  </section>;
}
